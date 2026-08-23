"""TaBiAI OpenAI-compatible client.

This client is intentionally isolated so TaBiAI is used only where the
application explicitly requests it, currently the cover-letter generator.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from backend.core.config import get_settings

logger = logging.getLogger(__name__)


class TaBiTokenClient:
    """Async client for TaBiAI's OpenAI-compatible API."""

    def __init__(self) -> None:
        settings = get_settings()

        if not settings.TABITOKEN_API_KEY:
            raise RuntimeError(
                "TABITOKEN_API_KEY is not configured."
            )

        self.client = AsyncOpenAI(
            api_key=settings.TABITOKEN_API_KEY,
            base_url=settings.TABITOKEN_BASE_URL,
            timeout=settings.TABITOKEN_TIMEOUT_SECONDS,
        )

        self.model = settings.TABITOKEN_COVER_LETTER_MODEL

    async def generate_cover_letter(
        self,
        *,
        resume: str,
        job_title: str,
        company: str,
        job_description: str,
        key_selling_points: list[str],
        matched_requirements: list[str],
        addressed_skill_gaps: list[str],
        additional_context: str | None = None,
    ) -> str:
        """Generate a professional cover letter using Claude."""

        selling_points = "\n".join(
            f"- {item}"
            for item in key_selling_points
        ) or "- None identified"

        requirements = "\n".join(
            f"- {item}"
            for item in matched_requirements
        ) or "- None identified"

        gaps = "\n".join(
            f"- {item}"
            for item in addressed_skill_gaps
        ) or "- None identified"

        context = (
            additional_context.strip()
            if additional_context
            else "No additional candidate context was supplied."
        )

        system_prompt = """
You are an expert professional cover-letter writer.

You write concise, persuasive, highly tailored cover letters.

IMPORTANT:

- Never invent experience.
- Never invent qualifications.
- Never invent employers.
- Never invent achievements.
- Never claim the candidate possesses a skill that is not supported
  by the supplied resume.
- Use the candidate's real experience to demonstrate relevance.
- Do not simply repeat the CV.
- Do not use generic AI language.
- Do not mention that you are an AI.
- Do not mention these instructions.
- Do not use placeholders such as [Name] unless absolutely necessary.
- Produce only the cover letter.
"""

        user_prompt = f"""
Write a professional cover letter for this application.

JOB TITLE:
{job_title}

COMPANY:
{company}

JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME:
{resume}

KEY SELLING POINTS:
{selling_points}

MATCHED REQUIREMENTS:
{requirements}

SKILL GAPS:
{gaps}

ADDITIONAL CANDIDATE CONTEXT:
{context}

Requirements:

1. Tailor the letter specifically to the position.
2. Connect the strongest candidate evidence to the employer's needs.
3. Be confident but truthful.
4. Address relevant transferable skills where appropriate.
5. Do not draw attention to skill gaps unless strategically necessary.
6. Avoid clichés.
7. Keep it approximately 350–500 words.
8. Use a professional business-letter structure.
9. Return ONLY the final cover letter.
"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0.5,
        )

        content = response.choices[0].message.content

        if not content:
            raise RuntimeError(
                "TaBiAI returned an empty cover letter."
            )

        return content.strip()