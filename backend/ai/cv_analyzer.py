"""AI CV analyzer service.

Analyzes extracted resume text with Gemini and produces a structured candidate
profile. This module contains only AI analysis orchestration; PDF extraction,
persistence and API concerns live outside of it.
"""

from __future__ import annotations

from typing import Any

from backend.ai.llm import LLMError, generate_json

_OUTPUT_SCHEMA = """{
  "name": "",
  "career_level": "",
  "years_experience": "",
  "skills": [],
  "technical_skills": [],
  "soft_skills": [],
  "industries": [],
  "strengths": [],
  "skill_gaps": [],
  "recommended_roles": []
}"""

_REQUIRED_FIELDS = (
    "name",
    "career_level",
    "years_experience",
    "skills",
    "technical_skills",
    "soft_skills",
    "industries",
    "strengths",
    "skill_gaps",
    "recommended_roles",
)


class CVAnalysisError(Exception):
    """Raised when a CV cannot be analyzed."""


class CVAnalyzerService:
    """Convert raw resume text into a structured candidate profile."""

    async def analyze_cv(self, resume_text: str) -> dict[str, Any]:
        """Analyze resume text and return a structured candidate profile.

        Args:
            resume_text: The plain-text content extracted from the resume.

        Returns:
            A dictionary describing the candidate profile with keys for
            ``name``, ``career_level``, ``years_experience``, ``skills``,
            ``technical_skills``, ``soft_skills``, ``industries``,
            ``strengths``, ``skill_gaps`` and ``recommended_roles``.

        Raises:
            CVAnalysisError: If the resume text is empty or Gemini fails to
                produce a parseable profile.
        """
        if not resume_text or not resume_text.strip():
            raise CVAnalysisError("Resume text must not be empty.")

        prompt = self._build_prompt(resume_text)

        try:
            profile = await generate_json(prompt)
        except LLMError as exc:
            raise CVAnalysisError("Failed to analyze the CV.") from exc

        return self._normalize_profile(profile)

    @staticmethod
    def _build_prompt(resume_text: str) -> str:
        """Build the prompt that asks Gemini for a structured profile.

        Args:
            resume_text: The plain-text resume content.

        Returns:
            A complete prompt requesting a strictly-JSON candidate profile.
        """
        return (
            "You are an expert career analyst and resume reviewer. Analyze the "
            "candidate's resume text below and extract a structured candidate "
            "profile.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- `name`: the candidate's full name if present, otherwise an empty string.\n"
            "- `career_level`: the seniority implied by the resume, e.g. "
            "'Entry', 'Mid-level', 'Senior', 'Lead', 'Principal'.\n"
            "- `years_experience`: a concise summary such as '5 years' or '3-4 years'.\n"
            "- `skills`: the overall set of skills mentioned.\n"
            "- `technical_skills`: programming languages, tools and frameworks.\n"
            "- `soft_skills`: communication, leadership and collaboration traits.\n"
            "- `industries`: the industries the candidate has worked in.\n"
            "- `strengths`: the candidate's most notable strengths.\n"
            "- `skill_gaps`: missing or underdeveloped skills relevant to their level.\n"
            "- `recommended_roles`: roles the candidate is well suited for.\n"
            "- Use empty strings and empty lists when information is not available.\n\n"
            "Resume text:\n"
            f"{resume_text.strip()}"
        )

    @staticmethod
    def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """Normalize the Gemini response so every expected key is present.

        Args:
            profile: The raw profile returned by Gemini.

        Returns:
            The profile with all expected keys filled, using an empty string
            for text fields and an empty list for collection fields.
        """
        normalized = dict(profile)
        for field in _REQUIRED_FIELDS:
            if field not in normalized or normalized[field] is None:
                normalized[field] = [] if field in _LIST_FIELDS else ""
        return normalized


_LIST_FIELDS = frozenset(
    {
        "skills",
        "technical_skills",
        "soft_skills",
        "industries",
        "strengths",
        "skill_gaps",
        "recommended_roles",
    }
)


__all__ = ["CVAnalyzerService", "CVAnalysisError"]
