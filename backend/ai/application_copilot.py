"""AI Application Copilot service.

Generates a tailored job application package — cover letter, key selling
points, requirement alignment and application tips — using the candidate's
own profile data and the specific job details.

Data integrity rules enforced by the prompt:
- The AI is forbidden from inventing employment history.
- The AI is forbidden from claiming qualifications not in the profile.
- Every claim in the cover letter must be traceable to the supplied profile.
- The prompt explicitly states the candidate's actual skills, level and
  background so the model has no reason to hallucinate.

This module is stateless: it accepts plain Python dicts and returns a plain
Python dict. No database access, no HTTP concerns.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ai.gemini import GeminiError, generate_json

_OUTPUT_SCHEMA = """{
  "cover_letter": "",
  "key_selling_points": [],
  "matched_requirements": [],
  "addressed_skill_gaps": [],
  "application_tips": []
}"""

_REQUIRED_FIELDS = (
    "cover_letter",
    "key_selling_points",
    "matched_requirements",
    "addressed_skill_gaps",
    "application_tips",
)

_LIST_FIELDS = frozenset(
    {
        "key_selling_points",
        "matched_requirements",
        "addressed_skill_gaps",
        "application_tips",
    }
)


class ApplicationCopilotError(Exception):
    """Raised when the application package cannot be generated."""


class ApplicationCopilotService:
    """Generate a tailored application package for a specific job."""

    async def generate(
        self,
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None = None,
        additional_context: str | None = None,
    ) -> dict[str, Any]:
        """Generate a tailored cover letter and application guidance.

        Args:
            candidate_profile: Structured profile from ``Resume.content``
                (keys: name, career_level, years_experience, skills,
                technical_skills, soft_skills, strengths, industries, etc.).
            job_details: Plain dict describing the target job (keys: title,
                company, description, location, experience_level, etc.).
            career_context: Optional analysis dict from the latest
                ``CareerInsight.analysis`` — used to tailor career narrative.
            additional_context: Optional free-text notes from the candidate
                (e.g. "I worked on a related open-source project").

        Returns:
            A dict with keys ``cover_letter``, ``key_selling_points``,
            ``matched_requirements``, ``addressed_skill_gaps`` and
            ``application_tips``.

        Raises:
            ApplicationCopilotError: If inputs are invalid or the AI fails.
        """
        if not candidate_profile or not isinstance(candidate_profile, dict):
            raise ApplicationCopilotError(
                "Candidate profile must be a non-empty dictionary."
            )
        if not job_details or not isinstance(job_details, dict):
            raise ApplicationCopilotError(
                "Job details must be a non-empty dictionary."
            )

        prompt = self._build_prompt(
            candidate_profile=candidate_profile,
            job_details=job_details,
            career_context=career_context,
            additional_context=additional_context,
        )

        try:
            result = await generate_json(prompt)
        except GeminiError as exc:
            raise ApplicationCopilotError(
                "Failed to generate the application package."
            ) from exc

        return self._normalize(result)

    @staticmethod
    def _build_prompt(
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None,
        additional_context: str | None,
    ) -> str:
        """Build the Gemini prompt with strict data-integrity instructions.

        The prompt is designed to prevent hallucination by:
        1. Providing the full candidate profile explicitly.
        2. Providing the full job details explicitly.
        3. Explicitly forbidding invention of history or qualifications.
        4. Instructing the model to cite only facts from the supplied data.
        """
        profile_json = json.dumps(candidate_profile, ensure_ascii=False, indent=2)
        job_json = json.dumps(job_details, ensure_ascii=False, indent=2)

        career_section = ""
        if career_context:
            career_json = json.dumps(career_context, ensure_ascii=False, indent=2)
            career_section = (
                "\n\nCareer navigator context (use to strengthen the narrative):\n"
                f"{career_json}"
            )

        additional_section = ""
        if additional_context and additional_context.strip():
            additional_section = (
                "\n\nAdditional context provided by the candidate "
                "(incorporate only if factual and relevant):\n"
                f"{additional_context.strip()}"
            )

        return (
            "You are an expert AI career coach and professional cover letter writer. "
            "Your task is to generate a highly tailored job application package.\n\n"
            "CRITICAL DATA INTEGRITY RULES — you MUST follow these without exception:\n"
            "1. NEVER invent employment history, companies, job titles or dates "
            "that are not explicitly present in the candidate profile below.\n"
            "2. NEVER claim qualifications, certifications or degrees that are "
            "not present in the candidate profile.\n"
            "3. NEVER inflate years of experience beyond what the profile states.\n"
            "4. Every statement in the cover letter MUST be traceable to the "
            "candidate profile or job description provided.\n"
            "5. If the candidate lacks a requirement, acknowledge it honestly in "
            "`addressed_skill_gaps` and suggest how they plan to address it — "
            "do NOT pretend the skill exists.\n"
            "6. Write in first person from the candidate's perspective.\n"
            "7. Avoid generic filler phrases like 'I am passionate about...' "
            "unless grounded in the actual profile.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Field guidelines:\n"
            "- `cover_letter`: A professional, specific 3-4 paragraph cover letter "
            "tailored to this exact job and company. Opening paragraph introduces "
            "the candidate and the role. Second paragraph highlights relevant "
            "experience and skills directly matching the job. Third paragraph "
            "addresses any skill gaps positively. Closing paragraph is a call to "
            "action.\n"
            "- `key_selling_points`: 3-5 concise bullet points of the candidate's "
            "strongest advantages for this specific role.\n"
            "- `matched_requirements`: Job requirements from the description that "
            "the candidate clearly meets, with a brief reason for each.\n"
            "- `addressed_skill_gaps`: Skills the job requires that the candidate "
            "is still developing, with honest, constructive framing.\n"
            "- `application_tips`: 2-4 actionable tips specific to this application "
            "(e.g. keywords to use, interview prep, portfolio items to highlight).\n"
            "- Use empty arrays when a section has no relevant content.\n\n"
            "Candidate profile:\n"
            f"{profile_json}"
            f"{career_section}"
            "\n\nTarget job:\n"
            f"{job_json}"
            f"{additional_section}"
        )

    @staticmethod
    def _normalize(result: dict[str, Any]) -> dict[str, Any]:
        """Ensure every required key is present with a safe default.

        Args:
            result: Raw dict returned by Gemini.

        Returns:
            Normalised dict with all expected keys filled.
        """
        normalized = dict(result)
        for field in _REQUIRED_FIELDS:
            value = normalized.get(field)
            if value is None:
                normalized[field] = [] if field in _LIST_FIELDS else ""
        return normalized


__all__ = ["ApplicationCopilotService", "ApplicationCopilotError"]
