"""AI job matching service.

Compares a candidate profile against available jobs using Gemini and returns
intelligent, ranked job recommendations. This module contains only AI analysis
orchestration and stays independent of frameworks, databases and API layers.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ai.llm import LLMError, generate_json

_OUTPUT_SCHEMA = """{
  "top_matches": [
    {
      "job_title": "",
      "company": "",
      "match_score": 0,
      "matching_skills": [],
      "missing_skills": [],
      "reason": ""
    }
  ],
  "overall_match_summary": "",
  "recommended_next_actions": []
}"""

_MIN_MATCH_SCORE = 0
_MAX_MATCH_SCORE = 100


class JobMatcherError(Exception):
    """Raised when job matching cannot be performed."""


class JobMatcherService:
    """Recommend jobs for a candidate profile using Gemini."""

    async def match_jobs(
        self,
        candidate_profile: dict[str, Any],
        available_jobs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Match a candidate profile against available jobs.

        Args:
            candidate_profile: A generated candidate profile dictionary.
            available_jobs: A list of job dictionaries with fields such as
                ``job_title``, ``company`` and ``requirements``.

        Returns:
            A dictionary describing the match results with keys for
            ``top_matches``, ``overall_match_summary`` and
            ``recommended_next_actions``.

        Raises:
            JobMatcherError: If the input is empty or Gemini fails to produce a
                parseable response.
        """
        if not candidate_profile or not isinstance(candidate_profile, dict):
            raise JobMatcherError("Candidate profile must be a non-empty dictionary.")
        if not available_jobs or not isinstance(available_jobs, list):
            raise JobMatcherError("Available jobs must be a non-empty list.")

        prompt = self._build_prompt(candidate_profile, available_jobs)

        try:
            result = await generate_json(prompt)
        except LLMError as exc:
            raise JobMatcherError("Failed to generate job matches.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(
        candidate_profile: dict[str, Any],
        available_jobs: list[dict[str, Any]],
    ) -> str:
        """Build the prompt that asks Gemini for job recommendations.

        Args:
            candidate_profile: The structured candidate profile.
            available_jobs: The list of available jobs.

        Returns:
            A complete prompt requesting a strictly-JSON match result.
        """
        serialized_profile = json.dumps(candidate_profile, ensure_ascii=False, indent=2)
        serialized_jobs = json.dumps(available_jobs, ensure_ascii=False, indent=2)
        return (
            "You are an expert AI recruitment specialist. Compare the candidate "
            "profile below against the available jobs and produce ranked job "
            "recommendations for a hiring product demo.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- `top_matches`: the best-fitting jobs ranked by `match_score` "
            "from highest to lowest.\n"
            "- `match_score`: an integer from 0 to 100 representing the fit "
            "between the candidate and the job.\n"
            "- `matching_skills`: skills the candidate has that the job needs.\n"
            "- `missing_skills`: job requirements the candidate does not meet.\n"
            "- `reason`: a concise explanation of why the job is a match.\n"
            "- `overall_match_summary`: a short summary of the candidate's "
            "overall fit with the available opportunities.\n"
            "- `recommended_next_actions`: concrete, actionable steps to "
            "improve the candidate's chances.\n"
            "- Base every match strictly on the provided profile and job data.\n"
            "- NEVER invent companies, job requirements or candidate "
            "experience that are not present in the input.\n"
            "- Use empty arrays and empty strings when information is "
            "unavailable.\n\n"
            "Candidate profile:\n"
            f"{serialized_profile}\n\n"
            "Available jobs:\n"
            f"{serialized_jobs}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """Normalize the Gemini response so every expected key is present.

        Args:
            result: The raw match result returned by Gemini.

        Returns:
            The result with all expected keys filled, using an empty string for
            text fields, an empty list for collection fields, and match scores
            clamped to the 0-100 range.
        """
        normalized = dict(result)

        overall_summary = normalized.get("overall_match_summary")
        if overall_summary is None:
            normalized["overall_match_summary"] = ""

        next_actions = normalized.get("recommended_next_actions")
        if not isinstance(next_actions, list):
            normalized["recommended_next_actions"] = []

        top_matches = normalized.get("top_matches")
        if not isinstance(top_matches, list):
            normalized["top_matches"] = []
        else:
            normalized["top_matches"] = [
                JobMatcherService._normalize_match(match) for match in top_matches
            ]

        return normalized

    @staticmethod
    def _normalize_match(match: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single match entry.

        Args:
            match: A raw match entry returned by Gemini.

        Returns:
            The match entry with all expected fields present and the match
            score clamped to the 0-100 range.
        """
        normalized = dict(match)

        for field in ("job_title", "company", "reason"):
            value = normalized.get(field)
            if value is None:
                normalized[field] = ""

        for field in ("matching_skills", "missing_skills"):
            value = normalized.get(field)
            if not isinstance(value, list):
                normalized[field] = []

        score = normalized.get("match_score")
        if not isinstance(score, int):
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = 0
        normalized["match_score"] = max(_MIN_MATCH_SCORE, min(_MAX_MATCH_SCORE, score))

        return normalized


__all__ = ["JobMatcherService", "JobMatcherError"]
