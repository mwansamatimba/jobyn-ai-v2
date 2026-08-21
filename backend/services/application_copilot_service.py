"""
AI Application Copilot service layer.

Orchestrates the cover-letter/application-generation pipeline:

1. Load the requested Job via JobRepository.
2. Load the authenticated user's latest Resume via ResumeRepository.
3. Verify Resume ownership.
4. Optionally load the latest CareerInsight.
5. Verify CareerInsight ownership.
6. Assemble context and call ApplicationCopilotService.
7. Normalize the AI result into the API response schema.
8. Return the structured application package.
9. No persistence is performed by this service.

Security invariant:
    user_id ALWAYS comes from the authenticated JWT subject.
    User-supplied IDs are never trusted for ownership decisions.

Database invariant:
    No direct SQLAlchemy queries exist in this file.
    All database access goes through repositories.

AI invariant:
    No AI calls exist in the API route.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from backend.ai.application_copilot import (
    ApplicationCopilotError,
    ApplicationCopilotService,
)
from backend.models.career import CareerInsight
from backend.models.job import Job
from backend.models.resume import Resume
from backend.repositories.career import CareerInsightRepository
from backend.repositories.job import JobRepository
from backend.repositories.resume import ResumeRepository
from backend.schemas.application_copilot import (
    AddressedSkillGap,
    ApplicationCopilotResponse,
    MatchedRequirement,
)
from backend.services.base import BaseService


logger = logging.getLogger(__name__)


# ============================================================================
# Exceptions
# ============================================================================


class CopilotServiceError(Exception):
    """Raised when the application copilot pipeline fails."""


class JobNotFoundError(CopilotServiceError):
    """Raised when the requested job does not exist or is inactive."""


class NoResumeError(CopilotServiceError):
    """Raised when the authenticated user has no parsed resume."""


# ============================================================================
# Generic helpers
# ============================================================================


def _as_uuid(value: Any) -> uuid.UUID | None:
    """Safely convert a value to UUID."""
    if isinstance(value, uuid.UUID):
        return value

    if value is None:
        return None

    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _as_string(value: Any, default: str = "") -> str:
    """Convert a value to a clean string."""
    if value is None:
        return default

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def _safe_list(value: Any) -> list[Any]:
    """
    Normalize arbitrary AI output into a list.

    Supports:
        list
        tuple
        JSON encoded list
        single string
        single object
    """

    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return []

        try:
            parsed = json.loads(value)

            if isinstance(parsed, list):
                return parsed

        except (json.JSONDecodeError, TypeError):
            pass

        return [value]

    return [value]


def _ensure_dict(value: Any) -> dict[str, Any]:
    """
    Normalize an arbitrary value into a dictionary.

    Supports:
        dict
        JSON string
        Pydantic model
        objects exposing dict()
    """

    if isinstance(value, dict):
        return dict(value)

    if value is None:
        return {}

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return {}

        try:
            parsed = json.loads(value)

            if isinstance(parsed, dict):
                return parsed

        except (json.JSONDecodeError, TypeError):
            return {}

    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()

            if isinstance(dumped, dict):
                return dumped

        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            dumped = value.dict()

            if isinstance(dumped, dict):
                return dumped

        except Exception:
            pass

    return {}


# ============================================================================
# Job conversion
# ============================================================================


def _job_to_dict(job: Job) -> dict[str, Any]:
    """Convert a Job ORM instance to an AI-friendly dictionary."""

    return {
        "job_id": str(job.id),
        "title": _as_string(job.title),
        "company": _as_string(job.company_name),
        "description": _as_string(job.description),
        "location": _as_string(job.location),
        "location_type": (
            str(job.location_type)
            if job.location_type is not None
            else ""
        ),
        "employment_type": (
            str(job.employment_type)
            if job.employment_type is not None
            else ""
        ),
        "experience_level": (
            str(job.experience_level)
            if job.experience_level is not None
            else ""
        ),
    }


# ============================================================================
# Resume conversion
# ============================================================================


def _resume_content_to_dict(resume: Resume) -> dict[str, Any]:
    """Normalize Resume.content into a dictionary."""

    content = resume.content

    if isinstance(content, dict):
        return dict(content)

    if isinstance(content, str):
        try:
            parsed = json.loads(content)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            logger.warning(
                "Resume %s contains non-JSON content.",
                resume.id,
            )

    return {
        "resume_content": _as_string(content),
    }


# ============================================================================
# String list normalization
# ============================================================================


def _normalise_string_list(value: Any) -> list[str]:
    """
    Convert arbitrary AI-generated values into list[str].

    Handles both:

        ["Strong Python experience", "API design"]

    and:

        [
            {"text": "Strong Python experience"},
            {"point": "API design"}
        ]
    """

    items = _safe_list(value)

    result: list[str] = []

    for item in items:

        if isinstance(item, str):
            text = item.strip()

            if text:
                result.append(text)

            continue

        if isinstance(item, dict):

            candidate = (
                item.get("text")
                or item.get("point")
                or item.get("tip")
                or item.get("value")
                or item.get("description")
                or item.get("recommendation")
                or item.get("reason")
            )

            if candidate is not None:
                text = _as_string(candidate)

                if text:
                    result.append(text)

                continue

            parts: list[str] = []

            for key, value_item in item.items():

                if value_item is None:
                    continue

                text = _as_string(value_item)

                if text:
                    parts.append(f"{key}: {text}")

            if parts:
                result.append("; ".join(parts))

            continue

        text = _as_string(item)

        if text:
            result.append(text)

    return result


# ============================================================================
# Matched requirements
# ============================================================================


def _normalise_matched_requirement(
    item: Any,
) -> dict[str, Any]:
    """
    Convert one AI matched requirement into the schema expected by
    MatchedRequirement.

    Supported examples:

        {
            "requirement": "Python",
            "evidence": "4 years experience"
        }

    or:

        "Python — present in profile with 4 years experience"
    """

    if isinstance(item, MatchedRequirement):
        return item.model_dump()

    if isinstance(item, dict):

        data = dict(item)

        requirement = (
            data.get("requirement")
            or data.get("skill")
            or data.get("name")
            or data.get("title")
            or ""
        )

        evidence = (
            data.get("evidence")
            or data.get("match")
            or data.get("reason")
            or data.get("description")
            or ""
        )

        return {
            "requirement": _as_string(requirement),
            "evidence": _as_string(evidence),
        }

    text = _as_string(item)

    if not text:
        return {
            "requirement": "",
            "evidence": "",
        }

    separators = (
        " — ",
        " – ",
        " - ",
        ": ",
    )

    for separator in separators:

        if separator in text:

            requirement, evidence = text.split(
                separator,
                1,
            )

            return {
                "requirement": requirement.strip(),
                "evidence": evidence.strip(),
            }

    return {
        "requirement": text,
        "evidence": "",
    }


def _normalise_matched_requirements(
    value: Any,
) -> list[MatchedRequirement]:
    """Normalize matched requirements into Pydantic models."""

    items = _safe_list(value)

    result: list[MatchedRequirement] = []

    for item in items:

        data = _normalise_matched_requirement(item)

        try:

            result.append(
                MatchedRequirement.model_validate(data)
            )

        except Exception as exc:

            logger.warning(
                "Skipping invalid matched requirement: %r; error=%s",
                item,
                exc,
            )

    return result


# ============================================================================
# Addressed skill gaps
# ============================================================================


def _normalise_addressed_skill_gap(
    item: Any,
) -> dict[str, Any]:
    """
    Convert one AI addressed-skill-gap item into the schema expected by
    AddressedSkillGap.

    Supported examples:

        {
            "gap": "Kubernetes",
            "response": "Currently completing CKA certification"
        }

    or:

        "Kubernetes — currently completing CKA certification"
    """

    if isinstance(item, AddressedSkillGap):
        return item.model_dump()

    if isinstance(item, dict):

        data = dict(item)

        gap = (
            data.get("gap")
            or data.get("skill_gap")
            or data.get("skill")
            or data.get("name")
            or data.get("requirement")
            or ""
        )

        response = (
            data.get("response")
            or data.get("plan")
            or data.get("recommendation")
            or data.get("evidence")
            or data.get("description")
            or ""
        )

        return {
            "gap": _as_string(gap),
            "response": _as_string(response),
        }

    text = _as_string(item)

    if not text:
        return {
            "gap": "",
            "response": "",
        }

    separators = (
        " — ",
        " – ",
        " - ",
        ": ",
    )

    for separator in separators:

        if separator in text:

            gap, response = text.split(
                separator,
                1,
            )

            return {
                "gap": gap.strip(),
                "response": response.strip(),
            }

    return {
        "gap": text,
        "response": "",
    }


def _normalise_addressed_skill_gaps(
    value: Any,
) -> list[AddressedSkillGap]:
    """Normalize addressed skill gaps into Pydantic models."""

    items = _safe_list(value)

    result: list[AddressedSkillGap] = []

    for item in items:

        data = _normalise_addressed_skill_gap(item)

        try:

            result.append(
                AddressedSkillGap.model_validate(data)
            )

        except Exception as exc:

            logger.warning(
                "Skipping invalid addressed skill gap: %r; error=%s",
                item,
                exc,
            )

    return result


# ============================================================================
# Application Copilot Orchestrator
# ============================================================================


class ApplicationCopilotOrchestrator(
    BaseService[JobRepository]
):
    """
    Orchestrates context loading and AI generation for job applications.

    Stateless service.

    No application data is persisted here.
    """

    def __init__(
        self,
        job_repository: JobRepository,
        resume_repository: ResumeRepository,
        insight_repository: CareerInsightRepository,
        copilot: ApplicationCopilotService | None = None,
    ) -> None:

        super().__init__(job_repository)

        self.job_repository = job_repository
        self.resume_repository = resume_repository
        self.insight_repository = insight_repository

        self._copilot = (
            copilot
            if copilot is not None
            else ApplicationCopilotService()
        )

    # ========================================================================
    # Main pipeline
    # ========================================================================

    async def generate_for_user(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        additional_context: str | None = None,
    ) -> ApplicationCopilotResponse:
        """
        Generate an AI-powered application package.

        user_id MUST originate from the authenticated JWT.
        """

        # ====================================================================
        # 1. Validate authenticated user
        # ====================================================================

        authenticated_user_id = _as_uuid(user_id)

        if authenticated_user_id is None:
            raise CopilotServiceError(
                "Invalid authenticated user ID."
            )

        # ====================================================================
        # 2. Load requested job
        # ====================================================================

        job = await self.job_repository.get_by_id(job_id)

        if job is None:

            raise JobNotFoundError(
                f"Job {job_id} not found or is no longer active."
            )

        # ====================================================================
        # 3. Load authenticated user's latest resume
        # ====================================================================

        resume = await self.resume_repository.get_latest_for_user(
            authenticated_user_id
        )

        if resume is None:

            raise NoResumeError(
                "No parsed resume found. "
                "Upload and parse a resume before generating an application."
            )

        # ====================================================================
        # 4. Verify resume ownership
        # ====================================================================

        resume_owner_id = _as_uuid(resume.user_id)

        if resume_owner_id != authenticated_user_id:

            logger.warning(
                "Resume ownership violation: "
                "resume=%s owner=%s authenticated_user=%s",
                resume.id,
                resume_owner_id,
                authenticated_user_id,
            )

            raise NoResumeError(
                "Resume does not belong to the authenticated user."
            )

        if not resume.content:

            raise NoResumeError(
                "The latest resume has no parsed content. "
                "Upload and parse a resume before generating an application."
            )

        # ====================================================================
        # 5. Load latest career insight
        # ====================================================================

        career_context: dict[str, Any] | None = None

        insight: CareerInsight | None = (
            await self.insight_repository.get_latest_for_user(
                authenticated_user_id
            )
        )

        if insight is not None:

            insight_owner_id = _as_uuid(
                insight.user_id
            )

            if insight_owner_id == authenticated_user_id:

                career_context = _ensure_dict(
                    insight.analysis
                )

            else:

                logger.warning(
                    "Career insight ownership violation: "
                    "insight=%s owner=%s authenticated_user=%s",
                    insight.id,
                    insight_owner_id,
                    authenticated_user_id,
                )

        # ====================================================================
        # 6. Assemble AI context
        # ====================================================================

        candidate_profile = _resume_content_to_dict(
            resume
        )

        job_details = _job_to_dict(
            job
        )

        clean_additional_context: str | None = None

        if additional_context is not None:

            clean_additional_context = (
                additional_context.strip()
            )

            if not clean_additional_context:
                clean_additional_context = None

        logger.info(
            "Generating application copilot response "
            "for user=%s job=%s resume=%s career_insight=%s",
            authenticated_user_id,
            job.id,
            resume.id,
            insight.id if insight else None,
        )

        # ====================================================================
        # 7. Call AI layer
        # ====================================================================

        try:

            ai_result = await self._copilot.generate(
                candidate_profile=candidate_profile,
                job_details=job_details,
                career_context=career_context,
                additional_context=clean_additional_context,
            )

        except ApplicationCopilotError as exc:

            logger.exception(
                "Application Copilot AI failure "
                "for user=%s job=%s",
                authenticated_user_id,
                job.id,
            )

            raise CopilotServiceError(
                f"AI application generation failed: {exc}"
            ) from exc

        except Exception as exc:

            logger.exception(
                "Unexpected Application Copilot failure "
                "for user=%s job=%s",
                authenticated_user_id,
                job.id,
            )

            raise CopilotServiceError(
                "AI application generation failed unexpectedly."
            ) from exc

        # ====================================================================
        # 8. Normalize AI result
        # ====================================================================

        if ai_result is None:

            raise CopilotServiceError(
                "AI returned an empty application package."
            )

        if not isinstance(ai_result, dict):

            try:

                if hasattr(ai_result, "model_dump"):

                    ai_result = ai_result.model_dump()

                elif hasattr(ai_result, "dict"):

                    ai_result = ai_result.dict()

                else:

                    raise TypeError(
                        "AI result is not a dictionary."
                    )

            except Exception as exc:

                logger.exception(
                    "Unable to normalize AI result "
                    "for user=%s job=%s",
                    authenticated_user_id,
                    job.id,
                )

                raise CopilotServiceError(
                    "AI returned an invalid application package."
                ) from exc

        # ====================================================================
        # 9. Normalize response fields
        # ====================================================================

        cover_letter = _as_string(
            ai_result.get("cover_letter")
        )

        key_selling_points = _normalise_string_list(
            ai_result.get("key_selling_points")
        )

        matched_requirements = (
            _normalise_matched_requirements(
                ai_result.get("matched_requirements")
            )
        )

        addressed_skill_gaps = (
            _normalise_addressed_skill_gaps(
                ai_result.get("addressed_skill_gaps")
            )
        )

        application_tips = _normalise_string_list(
            ai_result.get("application_tips")
        )

        # ====================================================================
        # 10. Build final Pydantic response
        # ====================================================================

        try:

            response = ApplicationCopilotResponse(
                job_id=job.id,
                job_title=job.title,
                company=job.company_name,
                cover_letter=cover_letter,
                key_selling_points=key_selling_points,
                matched_requirements=matched_requirements,
                addressed_skill_gaps=addressed_skill_gaps,
                application_tips=application_tips,
            )

        except Exception as exc:

            logger.exception(
                "Failed to construct ApplicationCopilotResponse "
                "for user=%s job=%s",
                authenticated_user_id,
                job.id,
            )

            raise CopilotServiceError(
                "AI returned an invalid application package."
            ) from exc

        # ====================================================================
        # 11. Return response — no persistence
        # ====================================================================

        return response


# ============================================================================
# Public exports
# ============================================================================


__all__ = [
    "ApplicationCopilotOrchestrator",
    "CopilotServiceError",
    "JobNotFoundError",
    "NoResumeError",
]