"""
Claude cover-letter generation through the TaBiAI OpenAI-compatible gateway.

TaBiAI exposes Claude through:
    POST /v1/chat/completions

The implementation intentionally uses the OpenAI Python client because
TaBiAI provides an OpenAI-compatible API.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

from backend.core.config import settings

logger = logging.getLogger(__name__)


class ClaudeCoverLetterClient:
    """Generate cover letters using Claude through TaBiAI."""

    def __init__(self) -> None:
        if not settings.TABITOKEN_API_KEY:
            raise RuntimeError(
                "TABITOKEN_API_KEY is not configured."
            )

        self.client = AsyncOpenAI(
            api_key=settings.TABITOKEN_API_KEY,
            base_url=settings.TABITOKEN_BASE_URL,
        )

        self.model = settings.TABITOKEN_COVER_LETTER_MODEL

    async def generate_cover_letter(
        self,
        *,
        candidate_profile: str,
        job_title: str,
        company: str,
        job_description: str,
        matched_requirements: list[str] | None = None,
        addressed_skill_gaps: list[str] | None = None,
        additional_context: str | None = None,
    ) -> str:
        """
        Generate a tailored professional cover letter.

        The model is instructed to use only information supplied by the
        candidate and job data. It must not invent qualifications,
        employers, achievements, dates, or credentials.
        """

        matched_requirements = matched_requirements or []
        addressed_skill_gaps = addressed_skill_gaps or []

        matched_text = "\n".join(
            f"- {item}" for item in matched_requirements
        )

        gaps_text = "\n".join(
            f"- {item}" for item in addressed_skill_gaps
        )

        prompt = f"""
Create a professional, highly tailored cover letter for the candidate
below.

CANDIDATE PROFILE
-----------------
{candidate_profile}

TARGET POSITION
---------------
{job_title}

COMPANY
-------
{company}

JOB DESCRIPTION
---------------
{job_description}

MATCHED REQUIREMENTS
--------------------
{matched_text}

SKILL GAPS / AREAS TO ADDRESS
-----------------------------
{gaps_text}

ADDITIONAL CANDIDATE CONTEXT
----------------------------
{additional_context or "None provided."}

INSTRUCTIONS
------------
1. Write a polished professional cover letter.
2. Tailor it specifically to the position and company.
3. Emphasise the candidate's strongest relevant experience and skills.
4. Connect the candidate's experience directly to the employer's needs.
5. Address genuine skill gaps positively where appropriate.
6. Never invent qualifications, employment history, achievements,
   certifications, employers, dates, metrics, or responsibilities.
7. Do not claim the candidate has a skill unless it appears in the
   supplied candidate information.
8. Do not mention that AI was used.
9. Avoid generic phrases and unnecessary repetition.
10. Keep the letter concise and persuasive.
11. Use normal professional business-letter structure.
12. Do not include placeholders such as [Name], [Date], or [Company].
13. Do not add a subject line unless it improves the professional format.
14. Return ONLY the finished cover letter.

TONE
----
Confident, professional, specific, authentic and human.
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert executive career writer "
                            "specialising in tailored job applications."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.7,
            )

            content = response.choices[0].message.content

            if not content:
                raise RuntimeError(
                    "Claude returned an empty cover letter."
                )

            return content.strip()

        except Exception:
            logger.exception(
                "Claude/TaBiAI cover-letter generation failed."
            )


from functools import lru_cache


@lru_cache
def get_claude_cover_letter_client() -> ClaudeCoverLetterClient:
    return ClaudeCoverLetterClient()