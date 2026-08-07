"""AI candidate profile generator service.

Transforms the structured output of the CV analyzer into a polished,
Gemini-generated candidate profile. This module contains only AI analysis
orchestration and stays independent of frameworks, databases and API layers.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ai.gemini import GeminiError, generate_json

_OUTPUT_SCHEMA = """{
  "candidate_summary": "",
  "professional_identity": "",
  "career_level": "",
  "core_skills": [],
  "technical_strengths": [],
  "career_interests": [],
  "target_roles": [],
  "recommended_learning_path": [],
  "career_readiness_score": 0,
  "growth_opportunities": []
}"""

_REQUIRED_FIELDS = (
    "candidate_summary",
    "professional_identity",
    "career_level",
    "core_skills",
    "technical_strengths",
    "career_interests",
    "target_roles",
    "recommended_learning_path",
    "career_readiness_score",
    "growth_opportunities",
)

_LIST_FIELDS = frozenset(
    {
        "core_skills",
        "technical_strengths",
        "career_interests",
        "target_roles",
        "recommended_learning_path",
        "growth_opportunities",
    }
)

_MIN_READINESS_SCORE = 0
_MAX_READINESS_SCORE = 100


class CandidateProfileError(Exception):
    """Raised when a candidate profile cannot be generated."""


class CandidateProfileService:
    """Generate a polished candidate profile from CV analysis output."""

    async def generate_profile(self, cv_analysis: dict[str, Any]) -> dict[str, Any]:
        """Generate a professional candidate profile from CV analysis.

        Args:
            cv_analysis: The structured profile produced by
                :class:`CVAnalyzerService` (e.g. skills, career level, gaps).

        Returns:
            A dictionary describing the candidate profile with keys for
            ``candidate_summary``, ``professional_identity``, ``career_level``,
            ``core_skills``, ``technical_strengths``, ``career_interests``,
            ``target_roles``, ``recommended_learning_path``,
            ``career_readiness_score`` and ``growth_opportunities``.

        Raises:
            CandidateProfileError: If the input is empty or Gemini fails to
                produce a parseable profile.
        """
        if not cv_analysis or not isinstance(cv_analysis, dict):
            raise CandidateProfileError("CV analysis input must be a non-empty dictionary.")

        prompt = self._build_prompt(cv_analysis)

        try:
            profile = await generate_json(prompt)
        except GeminiError as exc:
            raise CandidateProfileError("Failed to generate the candidate profile.") from exc

        return self._normalize_profile(profile)

    @staticmethod
    def _build_prompt(cv_analysis: dict[str, Any]) -> str:
        """Build the prompt that asks Gemini for a polished profile.

        Args:
            cv_analysis: The structured CV analysis output.

        Returns:
            A complete prompt requesting a strictly-JSON candidate profile.
        """
        serialized = json.dumps(cv_analysis, ensure_ascii=False, indent=2)
        return (
            "You are an expert AI career strategist. Transform the raw CV "
            "analysis below into a polished, professional candidate profile "
            "suitable for a hiring product demo.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- Base every field strictly on the provided analysis.\n"
            "- `candidate_summary`: a compelling 2-3 sentence professional summary.\n"
            "- `professional_identity`: a short title describing the candidate, "
            "e.g. 'Senior Backend Engineer'.\n"
            "- `career_level`: reuse the candidate's stated career level.\n"
            "- `core_skills`: the candidate's most relevant overall skills.\n"
            "- `technical_strengths`: notable technical skills and expertise.\n"
            "- `career_interests`: inferred interests grounded in the analysis.\n"
            "- `target_roles`: realistic roles matching the candidate's level.\n"
            "- `recommended_learning_path`: concrete, ordered upskilling steps.\n"
            "- `career_readiness_score`: an integer from 0 to 100 measuring how "
            "ready the candidate is for their target roles.\n"
            "- `growth_opportunities`: specific, actionable growth areas.\n"
            "- NEVER invent experience, employers or credentials that are not "
            "present in the analysis.\n"
            "- Use empty arrays and empty strings when information is unavailable.\n\n"
            "Raw CV analysis:\n"
            f"{serialized}"
        )

    @staticmethod
    def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """Normalize the Gemini response so every expected key is present.

        Args:
            profile: The raw profile returned by Gemini.

        Returns:
            The profile with all expected keys filled, using an empty string
            for text fields, an empty list for collection fields, and a score
            clamped to the 0-100 range.
        """
        normalized = dict(profile)
        for field in _REQUIRED_FIELDS:
            value = normalized.get(field)
            if value is None:
                normalized[field] = [] if field in _LIST_FIELDS else ""
        if isinstance(normalized.get("career_readiness_score"), int) is False:
            normalized["career_readiness_score"] = 0
        if not isinstance(normalized["career_readiness_score"], int):
            normalized["career_readiness_score"] = 0
        score = normalized["career_readiness_score"]
        normalized["career_readiness_score"] = max(
            _MIN_READINESS_SCORE, min(_MAX_READINESS_SCORE, int(score))
        )
        return normalized


__all__ = ["CandidateProfileService", "CandidateProfileError"]
