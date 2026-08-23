"""
AI Application Copilot.

This module contains the AI-layer implementation responsible for generating
structured job-application content.

The service layer is responsible for:
    - loading the job
    - loading the user's resume
    - loading career insights
    - enforcing ownership

This module is responsible only for AI generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ApplicationCopilotError(Exception):
    """Raised when application copilot generation fails."""

def normalize_application_analysis(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize AI-generated application analysis into frontend-safe strings.

    The LLM may return either strings or structured dictionaries for
    list-based fields. This helper converts both forms into consistent
    string lists.
    """

    list_fields = [
        "key_selling_points",
        "matched_requirements",
        "addressed_skill_gaps",
        "application_tips",
    ]

    normalized = dict(analysis)

    field_text_keys = {
        "key_selling_points": ("point", "evidence"),
        "matched_requirements": ("requirement", "evidence"),
        "addressed_skill_gaps": ("gap", "recommendation"),
        "application_tips": ("tip", "reason"),
    }

    for field in list_fields:
        value = normalized.get(field, [])

        if not isinstance(value, list):
            value = [value]

        result: list[str] = []

        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)

            elif isinstance(item, dict):
                keys = field_text_keys[field]

                parts = []

                for key in keys:
                    value_part = item.get(key)

                    if value_part is not None:
                        text_part = str(value_part).strip()

                        if text_part:
                            parts.append(text_part)

                if parts:
                    result.append(" — ".join(parts))

            elif item is not None:
                result.append(str(item).strip())

        normalized[field] = result

    return normalized
class ApplicationCopilotService:
    """
    AI service for generating a complete job application package.

    This class deliberately has no database/repository dependencies.
    """

    async def generate(
        self,
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None = None,
        additional_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate the structured application package.

        Replace the AI call in this method with your actual NVIDIA NIM /
        LLM implementation if it is already available elsewhere.
        """

        try:
            return await self._generate_with_llm(
                candidate_profile=candidate_profile,
                job_details=job_details,
                career_context=career_context,
                additional_context=additional_context,
            )

        except ApplicationCopilotError:
            raise

        except Exception as exc:
            logger.exception("Application copilot generation failed")
            raise ApplicationCopilotError(
                f"Application copilot generation failed: {exc}"
            ) from exc

    async def _generate_with_llm(
        self,
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None,
        additional_context: str | None,
    ) -> dict[str, Any]:
        """
        Generate application content.

        IMPORTANT:
        Keep the actual LLM integration here.

        The returned dictionary must contain these fields:

            cover_letter
            key_selling_points
            matched_requirements
            addressed_skill_gaps
            application_tips
        """

        # ---------------------------------------------------------
        # TEMPORARY SAFE FALLBACK
        # ---------------------------------------------------------
        #
        # If your existing NVIDIA/LLM implementation already exists,
        # replace ONLY this fallback with that implementation.
        #
        # The rest of the application pipeline expects this shape.
        # ---------------------------------------------------------

        candidate_name = (
            candidate_profile.get("name")
            or candidate_profile.get("full_name")
            or "the candidate"
        )

        job_title = job_details.get("title") or "the position"
        company = job_details.get("company") or "your organisation"

        skills = candidate_profile.get("skills") or []
        technical_skills = candidate_profile.get("technical_skills") or []

        all_skills: list[str] = []

        for skill in [*skills, *technical_skills]:
            if isinstance(skill, str) and skill.strip():
                if skill.strip() not in all_skills:
                    all_skills.append(skill.strip())

        skill_text = ", ".join(all_skills[:8])

        return {
            "cover_letter": (
                f"Dear Hiring Manager,\n\n"
                f"I am writing to express my interest in the {job_title} "
                f"position at {company}. My background and experience align "
                f"well with the requirements of the role. "
                f"I would welcome the opportunity to contribute my skills "
                f"and experience to your organisation.\n\n"
                f"My technical background includes {skill_text or 'relevant professional skills'}. "
                f"I am confident that my experience, problem-solving ability, "
                f"and commitment to delivering high-quality work would enable "
                f"me to make a meaningful contribution.\n\n"
                f"Thank you for considering my application. I would welcome "
                f"the opportunity to discuss my suitability for the position.\n\n"
                f"Yours sincerely,\n"
                f"{candidate_name}"
            ),
            "key_selling_points": [
                f"Relevant experience for the {job_title} position",
                *all_skills[:3],
            ],
            "matched_requirements": [
                {
                    "requirement": skill,
                    "evidence": (
                        f"{skill} is listed in the candidate's professional "
                        f"profile."
                    ),
                }
                for skill in all_skills[:3]
            ],
            "addressed_skill_gaps": [],
            "application_tips": [
                "Tailor the cover letter to the specific job description.",
                "Highlight measurable achievements from your experience.",
                "Prepare examples that demonstrate your strongest technical skills.",
            ],
        }


__all__ = [
    "ApplicationCopilotError",
    "ApplicationCopilotService",
    "normalize_application_analysis",

]