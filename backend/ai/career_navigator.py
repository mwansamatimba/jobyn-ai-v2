"""AI career navigator service.

Transforms a candidate profile into personalized career guidance using Gemini.
This module contains only AI analysis orchestration and stays independent of
frameworks, databases and API layers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.llm import LLMError, generate_json

logger = logging.getLogger(__name__)

_OUTPUT_SCHEMA = """{
  "career_direction": "",
  "recommended_roles": [],
  "career_path": [
    {
      "stage": "",
      "timeline": "",
      "actions": []
    }
  ],
  "skill_priorities": [],
  "certification_recommendations": [],
  "job_search_strategy": [],
  "career_advice": ""
}"""

_REQUIRED_FIELDS = (
    "career_direction",
    "recommended_roles",
    "career_path",
    "skill_priorities",
    "certification_recommendations",
    "job_search_strategy",
    "career_advice",
)

_LIST_FIELDS = frozenset(
    {
        "recommended_roles",
        "career_path",
        "skill_priorities",
        "certification_recommendations",
        "job_search_strategy",
    }
)


class CareerNavigatorError(Exception):
    """Raised when career guidance cannot be generated."""


class CareerNavigatorService:
    """Generate personalized career guidance from a candidate profile."""

    async def navigate(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Generate career guidance for a candidate profile.

        Args:
            profile: A generated candidate profile dictionary, e.g. the output
                of :class:`CandidateProfileService`.

        Returns:
            A dictionary describing the career navigation with keys for
            ``career_direction``, ``recommended_roles``, ``career_path``,
            ``skill_priorities``, ``certification_recommendations``,
            ``job_search_strategy`` and ``career_advice``.

        Raises:
            CareerNavigatorError: If the input is empty or Gemini fails to
                produce a parseable response.
        """
        if not profile or not isinstance(profile, dict):
            raise CareerNavigatorError("Candidate profile input must be a non-empty dictionary.")

        prompt = self._build_prompt(profile)

        try:
            result = await generate_json(prompt)
        except LLMError as exc:
            # Safe external message; underlying NIM details are logged in llm.py.
            logger.exception(
                "CareerNavigatorService.navigate failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise CareerNavigatorError("Failed to generate career guidance.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(profile: dict[str, Any]) -> str:
        """Build the prompt that asks Gemini for career guidance.

        Args:
            profile: The structured candidate profile.

        Returns:
            A complete prompt requesting a strictly-JSON career plan.
        """
        serialized = json.dumps(profile, ensure_ascii=False, indent=2)
        return (
            "You are an expert AI career navigator. Using the candidate profile "
            "below, create a personalized, actionable career plan for a hiring "
            "product demo.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- `career_direction`: a concise statement of the recommended "
            "career direction.\n"
            "- `recommended_roles`: realistic roles aligned with the "
            "candidate's level and skills.\n"
            "- `career_path`: an ordered list of stages, each with a "
            "`stage`, a `timeline`, and concrete `actions`.\n"
            "- `skill_priorities`: the most important skills to develop next, "
            "ordered by impact.\n"
            "- `certification_recommendations`: relevant certifications if any "
            "apply; empty list otherwise.\n"
            "- `job_search_strategy`: practical steps for the job search.\n"
            "- `career_advice`: 2-3 sentences of honest, grounded advice.\n"
            "- Base every recommendation strictly on the provided profile.\n"
            "- NEVER invent experience, employers or credentials that are not "
            "present in the profile.\n"
            "- Use empty arrays and empty strings when information is "
            "unavailable.\n\n"
            "Candidate profile:\n"
            f"{serialized}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """Normalize the Gemini response so every expected key is present.

        Args:
            result: The raw navigation response returned by Gemini.

        Returns:
            The response with all expected keys filled, using an empty string
            for text fields and an empty list for collection fields.
        """
        normalized = dict(result)
        for field in _REQUIRED_FIELDS:
            value = normalized.get(field)
            if value is None:
                normalized[field] = [] if field in _LIST_FIELDS else ""
        if not isinstance(normalized.get("career_path"), list):
            normalized["career_path"] = []
        return normalized


__all__ = ["CareerNavigatorService", "CareerNavigatorError"]
