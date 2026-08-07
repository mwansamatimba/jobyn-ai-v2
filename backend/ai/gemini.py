"""Gemini AI integration layer.

Single wrapper around the official Google Gemini Python SDK. Business services
depend on :class:`GeminiClient` (or the module-level helpers) instead of the
SDK directly, keeping model-call details isolated behind a small async
interface.

Environment variables:
    GEMINI_API_KEY: Google Cloud API key used to authenticate.
    GEMINI_MODEL: Model identifier (defaults to "gemini-2.0-flash").
    GEMINI_TIMEOUT_SECONDS: Per-request timeout in seconds (defaults to 60).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from backend.core.config import get_settings
from google import genai

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.0-flash"
_DEFAULT_TIMEOUT_SECONDS = 60.0


class GeminiError(Exception):
    """Base class for all Gemini integration errors."""


class GeminiConfigurationError(GeminiError):
    """Raised when the client cannot be configured, e.g. a missing API key."""


class GeminiGenerationError(GeminiError):
    """Raised when the model fails to produce a response."""


class GeminiResponseParsingError(GeminiError):
    """Raised when a structured response cannot be parsed into JSON."""


class GeminiClient:
    """Async client encapsulating all interactions with the Gemini API.

    Args:
        api_key: Google API key. Falls back to the ``GEMINI_API_KEY``
            environment variable.
        model: Model identifier. Falls back to the ``GEMINI_MODEL``
            environment variable.
        timeout_seconds: Per-request timeout in seconds. Falls back to the
            ``GEMINI_TIMEOUT_SECONDS`` environment variable.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (
            api_key
            or settings.GEMINI_API_KEY
            or os.getenv("GEMINI_API_KEY")
        )
        if not self._api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        self._model = (
            model
            or settings.GEMINI_MODEL
            or os.getenv("GEMINI_MODEL")
            or _DEFAULT_MODEL
        )
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(
                settings.GEMINI_TIMEOUT_SECONDS
                if settings.GEMINI_TIMEOUT_SECONDS is not None
                else os.getenv("GEMINI_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
            )
        )
        self._client = genai.Client(api_key=self._api_key)

    @property
    def model(self) -> str:
        """Return the model identifier used for requests."""
        return self._model

    async def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
    ) -> str:
        """Send a prompt to Gemini and return the generated text.

        Args:
            prompt: The user-facing prompt sent to the model.
            system_instruction: Optional system-level guidance for the model.
            model: Optional per-call model identifier override.

        Returns:
            The generated text response.

        Raises:
            GeminiGenerationError: If the model call fails or returns no text.
        """
        response = await self._generate(
            prompt,
            system_instruction=system_instruction,
            model=model,
        )
        text = self._extract_response_text(response)
        if text is None:
            raise GeminiGenerationError("Gemini returned an empty response.")
        return text

    async def generate_json(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Send a prompt to Gemini and return a parsed JSON object.

        The model is instructed to emit JSON and the response is parsed
        defensively, tolerating markdown code fences.

        Args:
            prompt: The user-facing prompt sent to the model.
            system_instruction: Optional system-level guidance for the model.
            model: Optional per-call model identifier override.

        Returns:
            The parsed JSON payload as a dictionary.

        Raises:
            GeminiGenerationError: If the model call fails or returns no text.
            GeminiResponseParsingError: If the response cannot be parsed as JSON.
        """
        response = await self._generate(
            prompt,
            system_instruction=system_instruction,
            model=model,
            response_mime_type="application/json",
        )
        text = self._extract_response_text(response)
        if text is None:
            raise GeminiGenerationError("Gemini returned an empty response.")

        try:
            payload = json.loads(_strip_code_fences(text))
        except json.JSONDecodeError as exc:
            logger.exception("Gemini returned invalid JSON: %s", exc)
            raise GeminiResponseParsingError(
                "Gemini returned a response that is not valid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise GeminiResponseParsingError(
                "Gemini returned a JSON response that is not an object."
            )
        return payload

    async def _generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None,
        model: str | None,
        response_mime_type: str | None = None,
    ) -> Any:
        """Run a single model request and wrap any SDK failure."""
        request_payload: dict[str, Any] = {
            "model": model or self._model,
            "input": prompt,
            "timeout": self._timeout_seconds,
        }
        if system_instruction is not None:
            request_payload["instructions"] = system_instruction

        try:
            responses_api = getattr(self._client.aio, "responses", None)
            if responses_api is not None and hasattr(responses_api, "create"):
                return await responses_api.create(**request_payload)

            models_api = getattr(self._client.aio, "models", None)
            if models_api is not None and hasattr(models_api, "generate_content"):
                from google.genai import types as genai_types

                config: dict[str, Any] = {}
                if system_instruction is not None:
                    config["system_instruction"] = system_instruction
                if response_mime_type is not None:
                    config["response_mime_type"] = response_mime_type

                return await models_api.generate_content(
                    model=model or self._model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(**config),
                    timeout=self._timeout_seconds,
                )

            raise GeminiGenerationError(
                "Unsupported Google Gemini SDK interface: async generation method not found."
            )
        except Exception as exc:
            logger.exception("Gemini API failed: %s", exc)
            raise GeminiGenerationError(str(exc)) from exc

    @staticmethod
    def _extract_response_text(response: Any) -> str | None:
        if response is None:
            return None

        if hasattr(response, "output_text") and isinstance(response.output_text, str):
            return response.output_text

        if hasattr(response, "text") and isinstance(response.text, str):
            return response.text

        output = getattr(response, "output", None)
        if isinstance(output, list) and output:
            first_item = output[0]
            if isinstance(first_item, dict):
                content = first_item.get("content")
            else:
                content = getattr(first_item, "content", None)

            if isinstance(content, list) and content:
                first_content = content[0]
                if isinstance(first_content, dict):
                    return first_content.get("text")
                return getattr(first_content, "text", None)

        return None


_default_client: GeminiClient | None = None


def get_gemini_client() -> GeminiClient:
    """Return the process-wide default :class:`GeminiClient`, creating it lazily.

    Returns:
        A configured :class:`GeminiClient` instance.

    Raises:
        GeminiConfigurationError: If no API key is configured.
    """
    global _default_client
    if _default_client is None:
        _default_client = GeminiClient()
    return _default_client


async def generate_text(
    prompt: str,
    *,
    system_instruction: str | None = None,
) -> str:
    """Generate text through the default Gemini client.

    Args:
        prompt: The user-facing prompt sent to the model.
        system_instruction: Optional system-level guidance for the model.

    Returns:
        The generated text response.
    """
    return await get_gemini_client().generate_text(
        prompt,
        system_instruction=system_instruction,
    )


async def generate_json(prompt: str) -> dict[str, Any]:
    """Generate and parse a JSON object through the default Gemini client.

    Args:
        prompt: The user-facing prompt sent to the model.

    Returns:
        The parsed JSON payload as a dictionary.
    """
    return await get_gemini_client().generate_json(prompt)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences surrounding a JSON payload.

    Args:
        text: The raw text returned by the model.

    Returns:
        The text with an optional leading/trailing ``` fence removed.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


__all__ = [
    "GeminiClient",
    "GeminiError",
    "GeminiConfigurationError",
    "GeminiGenerationError",
    "GeminiResponseParsingError",
    "get_gemini_client",
    "generate_text",
    "generate_json",
]
