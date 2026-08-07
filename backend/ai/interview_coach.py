"""AI interview coach service.

Prepares candidates for job interviews by generating tailored questions, answer
guidance, readiness assessment and improvement advice using Gemini. This module
contains only AI analysis orchestration and stays independent of frameworks,
databases and API layers.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ai.gemini import GeminiError, generate_json

_OUTPUT_SCHEMA = """{
  "target_role": "",
  "readiness_score": 0,
  "interview_questions": [
    {
      "question": "",
      "category": "",
      "difficulty": "",
      "ideal_answer_points": []
    }
  ],
  "strength_areas": [],
  "improvement_areas": [],
  "preparation_plan": [],
  "final_advice": ""
}"""

_MIN_READINESS_SCORE = 0
_MAX_READINESS_SCORE = 100


class InterviewCoachError(Exception):
    """Raised when an interview plan cannot be generated."""


class InterviewCoachService:
    """Generate a tailored interview preparation plan for a candidate."""

    async def generate_interview_plan(
        self,
        candidate_profile: dict[str, Any],
        target_role: str,
    ) -> dict[str, Any]:
        """Generate an interview preparation plan for a target role.

        Args:
            candidate_profile: A generated candidate profile dictionary.
            target_role: The role the candidate is preparing to interview for.

        Returns:
            A dictionary describing the interview plan with keys for
            ``target_role``, ``readiness_score``, ``interview_questions``,
            ``strength_areas``, ``improvement_areas``, ``preparation_plan``
            and ``final_advice``.

        Raises:
            InterviewCoachError: If the input is invalid or Gemini fails to
                produce a parseable response.
        """
        if not candidate_profile or not isinstance(candidate_profile, dict):
            raise InterviewCoachError("Candidate profile must be a non-empty dictionary.")
        if not target_role or not isinstance(target_role, str) or not target_role.strip():
            raise InterviewCoachError("Target role must be a non-empty string.")

        prompt = self._build_prompt(candidate_profile, target_role)

        try:
            result = await generate_json(prompt)
        except GeminiError as exc:
            raise InterviewCoachError("Failed to generate the interview plan.") from exc

        return self._normalize_response(result, target_role)

    @staticmethod
    def _build_prompt(candidate_profile: dict[str, Any], target_role: str) -> str:
        """Build the prompt that asks Gemini for an interview plan.

        Args:
            candidate_profile: The structured candidate profile.
            target_role: The target interview role.

        Returns:
            A complete prompt requesting a strictly-JSON interview plan.
        """
        serialized_profile = json.dumps(candidate_profile, ensure_ascii=False, indent=2)
        return (
            "You are an expert technical recruiter and interview coach. Using "
            "the candidate profile below, prepare the candidate for an "
            "interview for the given target role.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- `target_role`: the target role as provided.\n"
            "- `readiness_score`: an integer from 0 to 100 estimating the "
            "candidate's readiness for the role.\n"
            "- `interview_questions`: a realistic set of questions for the "
            "role, each with a `category`, a `difficulty` and "
            "`ideal_answer_points`.\n"
            "- `strength_areas`: areas where the candidate is well prepared.\n"
            "- `improvement_areas`: likely interview challenges and gaps.\n"
            "- `preparation_plan`: concrete, ordered preparation steps.\n"
            "- `final_advice`: 2-3 sentences of honest, actionable advice.\n"
            "- Base every question and judgment strictly on the supplied "
            "candidate information.\n"
            "- NEVER invent experience, employers or certifications that are "
            "not present in the profile.\n"
            "- Use empty arrays and empty strings when information is "
            "unavailable.\n\n"
            "Target role:\n"
            f"{target_role}\n\n"
            "Candidate profile:\n"
            f"{serialized_profile}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any], target_role: str) -> dict[str, Any]:
        """Normalize the Gemini response so every expected key is present.

        Args:
            result: The raw interview plan returned by Gemini.
            target_role: The requested target role, used as a safe default.

        Returns:
            The plan with all expected keys filled, using an empty string for
            text fields, an empty list for collection fields, and a readiness
            score clamped to the 0-100 range.
        """
        normalized = dict(result)

        normalized["target_role"] = target_role

        final_advice = normalized.get("final_advice")
        if final_advice is None:
            normalized["final_advice"] = ""

        for field in ("strength_areas", "improvement_areas", "preparation_plan"):
            value = normalized.get(field)
            if not isinstance(value, list):
                normalized[field] = []

        questions = normalized.get("interview_questions")
        if not isinstance(questions, list):
            normalized["interview_questions"] = []
        else:
            normalized["interview_questions"] = [
                InterviewCoachService._normalize_question(question)
                for question in questions
                if isinstance(question, dict)
            ]

        score = normalized.get("readiness_score")
        if not isinstance(score, int):
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = 0
        normalized["readiness_score"] = max(
            _MIN_READINESS_SCORE, min(_MAX_READINESS_SCORE, score)
        )

        return normalized

    @staticmethod
    def _normalize_question(question: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single interview question entry.

        Args:
            question: A raw question entry returned by Gemini.

        Returns:
            The question entry with all expected fields present.
        """
        normalized = dict(question)

        for field in ("question", "category", "difficulty"):
            value = normalized.get(field)
            if value is None:
                normalized[field] = ""

        answer_points = normalized.get("ideal_answer_points")
        if not isinstance(answer_points, list):
            normalized["ideal_answer_points"] = []

        return normalized


__all__ = ["InterviewCoachService", "InterviewCoachError"]
