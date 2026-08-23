"""
Provider-independent LLM client for Jobyn AI.

Current provider:
    NVIDIA NIM using its OpenAI-compatible API.

Primary model:
    nvidia/nemotron-3.5-lightning-30b-a3b

The rest of Jobyn AI should depend on the public functions:

    generate_text()
    generate_json()

rather than depending directly on the NVIDIA SDK.

This keeps the AI layer provider-independent and makes it possible to
switch providers/models later without changing application services.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)


# ============================================================================
# Environment
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load project .env explicitly.
load_dotenv(PROJECT_ROOT / ".env")


# ============================================================================
# Logging
# ============================================================================

logger = logging.getLogger(__name__)


# ============================================================================
# Provider configuration
# ============================================================================

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL",
    "https://integrate.api.nvidia.com/v1",
)

# IMPORTANT:
# GLM-5.2 is retired and must not be used as the fallback.
NVIDIA_MODEL = os.getenv(
    "NVIDIA_MODEL",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)

# Provider identifier used in structured logs.
_PROVIDER = "nvidia_nim"

# Logging protection.
_MAX_BODY_LOG_CHARS = 2000

# Default Nemotron behavior for Jobyn.
#
# For resume parsing, candidate profiling, cover letters and JSON extraction,
# we generally want direct instruction-following rather than visible reasoning.
DEFAULT_ENABLE_THINKING = (
    os.getenv("NVIDIA_ENABLE_THINKING", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# Optional reasoning budget when thinking mode is enabled.
#
# NVIDIA documents reasoning_budget for Nemotron's thinking mode.
DEFAULT_REASONING_BUDGET = int(
    os.getenv("NVIDIA_REASONING_BUDGET", "8192")
)

# Optional top-p.
DEFAULT_TOP_P = float(
    os.getenv("NVIDIA_TOP_P", "0.95")
)


# ============================================================================
# Exceptions
# ============================================================================


class LLMError(Exception):
    """Raised when an LLM provider request fails."""


# ============================================================================
# Shared client
# ============================================================================

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """
    Return the shared NVIDIA NIM OpenAI-compatible client.

    The client is created lazily so importing backend.ai.llm does not
    immediately fail if the API key is missing.
    """

    global _client

    if _client is None:
        if not NVIDIA_API_KEY:
            raise LLMError(
                "NVIDIA_API_KEY is not configured. "
                "Add NVIDIA_API_KEY to the project's .env file."
            )

        _client = AsyncOpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
        )

    return _client


# ============================================================================
# Logging / sanitization helpers
# ============================================================================


def _sanitize_text(text: str | None) -> str:
    """
    Truncate and redact secrets from text before logging.

    Never log:
        - NVIDIA API keys
        - bearer tokens
        - authorization headers
    """

    if not text:
        return ""

    sanitized = str(text)

    # ------------------------------------------------------------------------
    # Direct API-key redaction
    # ------------------------------------------------------------------------

    if NVIDIA_API_KEY:
        sanitized = sanitized.replace(
            NVIDIA_API_KEY,
            "[REDACTED_API_KEY]",
        )

    # ------------------------------------------------------------------------
    # Generic bearer-token redaction
    # ------------------------------------------------------------------------

    sanitized = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED_TOKEN]",
        sanitized,
    )

    # ------------------------------------------------------------------------
    # Generic API-key patterns
    # ------------------------------------------------------------------------

    sanitized = re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)['\"]?[^'\"\s,}]+",
        r"\1[REDACTED_API_KEY]",
        sanitized,
    )

    # ------------------------------------------------------------------------
    # Truncate very large error bodies
    # ------------------------------------------------------------------------

    if len(sanitized) > _MAX_BODY_LOG_CHARS:
        sanitized = (
            sanitized[:_MAX_BODY_LOG_CHARS]
            + "...[truncated]"
        )

    return sanitized


def _classify_nim_exception(
    exc: BaseException,
) -> str:
    """
    Return a stable category label for NIM failures.
    """

    if isinstance(exc, APITimeoutError):
        return "timeout"

    if isinstance(exc, APIConnectionError):
        return "connection_network_error"

    if isinstance(exc, RateLimitError):
        return "http_api_error_rate_limit"

    if isinstance(exc, APIError):
        return "http_api_error"

    if isinstance(exc, json.JSONDecodeError):
        return "malformed_json"

    return "unexpected_python_exception"


def _extract_http_details(
    exc: BaseException,
) -> dict[str, Any]:
    """
    Extract useful HTTP information from OpenAI-compatible SDK exceptions.
    """

    details: dict[str, Any] = {
        "http_status": None,
        "response_body": None,
        "timeout": None,
    }

    # ------------------------------------------------------------------------
    # HTTP status
    # ------------------------------------------------------------------------

    status = getattr(
        exc,
        "status_code",
        None,
    )

    if status is not None:
        details["http_status"] = status

    # ------------------------------------------------------------------------
    # Exception body
    # ------------------------------------------------------------------------

    body = getattr(
        exc,
        "body",
        None,
    )

    if body is not None:
        try:
            if isinstance(body, str):
                body_text = body
            else:
                body_text = json.dumps(
                    body,
                    default=str,
                )
        except Exception:
            body_text = str(body)

        details["response_body"] = _sanitize_text(
            body_text
        )

    # ------------------------------------------------------------------------
    # HTTP response object
    # ------------------------------------------------------------------------

    response = getattr(
        exc,
        "response",
        None,
    )

    if response is not None:

        if details["http_status"] is None:
            details["http_status"] = getattr(
                response,
                "status_code",
                None,
            )

        if details["response_body"] is None:
            try:
                text = getattr(
                    response,
                    "text",
                    None,
                )

                if callable(text):
                    text = text()

                if text:
                    details["response_body"] = (
                        _sanitize_text(str(text))
                    )

            except Exception:
                pass

    # ------------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------------

    if isinstance(exc, APITimeoutError):
        details["timeout"] = True

        details["timeout_info"] = _sanitize_text(
            str(exc)
        )

    # ------------------------------------------------------------------------
    # Request object
    # ------------------------------------------------------------------------

    request = getattr(
        exc,
        "request",
        None,
    )

    if request is not None:
        details["has_request_object"] = True

    return details


def _log_nim_failure(
    *,
    operation: str,
    exc: BaseException,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Log structured NVIDIA NIM failure information.

    Never logs:
        - prompts
        - resumes
        - candidate profiles
        - job descriptions
        - API keys
    """

    category = _classify_nim_exception(
        exc
    )

    http_details = _extract_http_details(
        exc
    )

    payload: dict[str, Any] = {
        "provider": _PROVIDER,
        "model": NVIDIA_MODEL,
        "operation": operation,
        "error_category": category,
        "exception_type": type(exc).__name__,
        "exception_message": _sanitize_text(
            str(exc)
        ),
        **http_details,
    }

    if extra:
        payload.update(extra)

    logger.exception(
        "NVIDIA NIM request failed: %s",
        payload,
    )


# ============================================================================
# Model request helpers
# ============================================================================


def _build_extra_body(
    *,
    enable_thinking: bool,
    reasoning_budget: int | None,
) -> dict[str, Any]:
    """
    Build NVIDIA-specific request parameters.

    Nemotron supports chat_template_kwargs for controlling thinking mode.
    """

    extra_body: dict[str, Any] = {
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
        }
    }

    if enable_thinking and reasoning_budget is not None:
        if reasoning_budget < 1:
            raise LLMError(
                "reasoning_budget must be greater than zero."
            )

        extra_body["reasoning_budget"] = reasoning_budget

    return extra_body


def _extract_message_content(
    response: Any,
) -> str:
    """
    Safely extract final textual content from an OpenAI-compatible response.

    We intentionally use message.content rather than reasoning_content.
    Jobyn's public services should receive the model's answer, not its
    internal reasoning trace.
    """

    if not response.choices:
        raise LLMError(
            "NVIDIA NIM returned no choices."
        )

    message = response.choices[0].message

    content = getattr(
        message,
        "content",
        None,
    )

    if content is None:
        raise LLMError(
            "NVIDIA NIM returned no message content."
        )

    if not isinstance(content, str):
        content = str(content)

    content = content.strip()

    if not content:
        raise LLMError(
            "NVIDIA NIM returned an empty response."
        )

    return content


# ============================================================================
# Text generation
# ============================================================================


async def generate_text(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    top_p: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
) -> str:
    """
    Generate text using NVIDIA NIM.

    Args:
        prompt:
            User prompt.

        system_prompt:
            Optional system instruction.

        temperature:
            Sampling temperature. Jobyn defaults to 0.2.

        max_tokens:
            Maximum generated tokens.

        top_p:
            Optional nucleus sampling value.

        enable_thinking:
            Whether Nemotron reasoning/thinking mode is enabled.

        reasoning_budget:
            Reasoning token budget when thinking is enabled.

    Returns:
        Generated text.

    Raises:
        LLMError:
            If validation or provider generation fails.
    """

    # ------------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------------

    if not isinstance(prompt, str):
        raise LLMError(
            "Prompt must be a string."
        )

    if not prompt.strip():
        raise LLMError(
            "Prompt must be a non-empty string."
        )

    if not isinstance(temperature, (int, float)):
        raise LLMError(
            "Temperature must be a number."
        )

    if not 0 <= temperature <= 1:
        raise LLMError(
            "Temperature must be between 0 and 1."
        )

    if not isinstance(max_tokens, int):
        raise LLMError(
            "max_tokens must be an integer."
        )

    if max_tokens < 1:
        raise LLMError(
            "max_tokens must be greater than zero."
        )

    if top_p is None:
        top_p = DEFAULT_TOP_P

    if not 0 < top_p <= 1:
        raise LLMError(
            "top_p must be greater than 0 and less than or equal to 1."
        )

    if enable_thinking is None:
        enable_thinking = DEFAULT_ENABLE_THINKING

    if reasoning_budget is None:
        reasoning_budget = DEFAULT_REASONING_BUDGET

    # ------------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------------

    client = _get_client()

    # ------------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------------

    messages: list[dict[str, str]] = []

    if system_prompt:
        clean_system_prompt = system_prompt.strip()

        if clean_system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": clean_system_prompt,
                }
            )

    messages.append(
        {
            "role": "user",
            "content": prompt.strip(),
        }
    )

    # ------------------------------------------------------------------------
    # NVIDIA-specific options
    # ------------------------------------------------------------------------

    extra_body = _build_extra_body(
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )

    # ------------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------------

    logger.info(
        (
            "NVIDIA NIM request starting "
            "provider=%s model=%s operation=%s "
            "temperature=%s max_tokens=%s top_p=%s "
            "thinking=%s"
        ),
        _PROVIDER,
        NVIDIA_MODEL,
        "generate_text",
        temperature,
        max_tokens,
        top_p,
        enable_thinking,
    )

    # ------------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------------

    try:
        response = await client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body=extra_body,
            stream=False,
        )

    except Exception as exc:
        _log_nim_failure(
            operation="generate_text",
            exc=exc,
        )

        raise LLMError(
            f"NVIDIA NIM request failed: {_sanitize_text(str(exc))}"
        ) from exc

    # ------------------------------------------------------------------------
    # Extract content
    # ------------------------------------------------------------------------

    try:
        content = _extract_message_content(
            response
        )

    except LLMError as exc:
        logger.error(
            (
                "NVIDIA NIM invalid response "
                "provider=%s model=%s operation=%s "
                "error_category=invalid_empty_nim_response"
            ),
            _PROVIDER,
            NVIDIA_MODEL,
            "generate_text",
        )

        raise exc

    # ------------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------------

    logger.info(
        (
            "NVIDIA NIM request completed "
            "provider=%s model=%s operation=%s"
        ),
        _PROVIDER,
        NVIDIA_MODEL,
        "generate_text",
    )

    return content


# ============================================================================
# JSON cleaning helpers
# ============================================================================


def _remove_markdown_json_fence(
    content: str,
) -> str:
    """
    Remove common Markdown JSON code fences.
    """

    cleaned = content.strip()

    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()

    # Remove opening fence.
    if lines:
        first = lines[0].strip().lower()

        if first in {
            "```",
            "```json",
            "```javascript",
            "```js",
        }:
            lines = lines[1:]

    # Remove closing fence.
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def _extract_json_object(
    content: str,
) -> str:
    """
    Extract a JSON object from slightly noisy model output.

    First attempts the entire response.

    If that fails, finds the first '{' and last '}' and attempts to parse
    that section.

    This is intentionally conservative because resume/profile JSON can
    contain braces inside strings.
    """

    cleaned = _remove_markdown_json_fence(
        content
    )

    # ------------------------------------------------------------------------
    # First attempt: exact response
    # ------------------------------------------------------------------------

    try:
        parsed = json.loads(cleaned)

        if isinstance(parsed, dict):
            return cleaned

    except json.JSONDecodeError:
        pass

    # ------------------------------------------------------------------------
    # Second attempt: extract outer object
    # ------------------------------------------------------------------------

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if (
        first_brace != -1
        and last_brace != -1
        and last_brace > first_brace
    ):
        candidate = cleaned[
            first_brace:last_brace + 1
        ].strip()

        try:
            parsed = json.loads(candidate)

            if isinstance(parsed, dict):
                return candidate

        except json.JSONDecodeError:
            pass

    # ------------------------------------------------------------------------
    # Nothing worked.
    # ------------------------------------------------------------------------

    return cleaned


# ============================================================================
# JSON generation
# ============================================================================


async def generate_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    top_p: float | None = None,
    enable_thinking: bool = False,
    reasoning_budget: int | None = None,
) -> dict[str, Any]:
    """
    Generate and parse a JSON object using NVIDIA NIM.

    This function is designed for Jobyn AI's structured tasks:

        - resume parsing
        - candidate profiles
        - career insights
        - job matching
        - application copilot
        - skill-gap analysis
        - structured career coaching

    Thinking is disabled by default because structured JSON generation is
    more reliable when the model is instructed to return only the final
    object.
    """

    # ------------------------------------------------------------------------
    # Strong JSON instruction
    # ------------------------------------------------------------------------

    json_instruction = """
Return ONLY one valid JSON object.

Requirements:
- Do not use Markdown.
- Do not use code fences.
- Do not include explanations.
- Do not include commentary before the JSON.
- Do not include commentary after the JSON.
- Use double quotes for JSON keys and string values.
- Do not use trailing commas.
- Do not return a JSON array.
- The top-level response MUST be a JSON object.
""".strip()

    # ------------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------------

    if system_prompt:
        combined_system_prompt = (
            f"{system_prompt.strip()}\n\n"
            f"{json_instruction}"
        )
    else:
        combined_system_prompt = json_instruction

    # ------------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------------

    content = await generate_text(
        prompt,
        system_prompt=combined_system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )

    # ------------------------------------------------------------------------
    # Clean response
    # ------------------------------------------------------------------------

    cleaned = _extract_json_object(
        content
    )

    # ------------------------------------------------------------------------
    # Parse JSON
    # ------------------------------------------------------------------------

    try:
        result = json.loads(cleaned)

    except json.JSONDecodeError as exc:

        logger.error(
            (
                "NVIDIA NIM malformed JSON "
                "provider=%s model=%s operation=%s "
                "error_category=malformed_json "
                "exception_type=%s "
                "exception_message=%s "
                "response_preview=%s"
            ),
            _PROVIDER,
            NVIDIA_MODEL,
            "generate_json",
            type(exc).__name__,
            _sanitize_text(str(exc)),
            _sanitize_text(cleaned),
        )

        raise LLMError(
            (
                "NVIDIA NIM returned invalid JSON. "
                f"Response preview: {_sanitize_text(cleaned[:500])}"
            )
        ) from exc

    # ------------------------------------------------------------------------
    # Validate top-level object
    # ------------------------------------------------------------------------

    if not isinstance(result, dict):

        logger.error(
            (
                "NVIDIA NIM schema validation failure "
                "provider=%s model=%s operation=%s "
                "error_category=model_output_schema_validation_failure "
                "got_type=%s"
            ),
            _PROVIDER,
            NVIDIA_MODEL,
            "generate_json",
            type(result).__name__,
        )

        raise LLMError(
            "NVIDIA NIM JSON response must be a JSON object."
        )

    # ------------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------------

    logger.info(
        (
            "NVIDIA NIM JSON parse succeeded "
            "provider=%s model=%s operation=%s"
        ),
        _PROVIDER,
        NVIDIA_MODEL,
        "generate_json",
    )

    return result


# ============================================================================
# Health / configuration helpers
# ============================================================================


def get_llm_config() -> dict[str, Any]:
    """
    Return safe LLM configuration information.

    Never returns the API key.

    Useful for:
        /health
        /debug
        startup diagnostics
        tests
    """

    return {
        "provider": _PROVIDER,
        "model": NVIDIA_MODEL,
        "base_url": NVIDIA_BASE_URL,
        "api_key_configured": bool(NVIDIA_API_KEY),
        "default_enable_thinking": DEFAULT_ENABLE_THINKING,
        "default_reasoning_budget": DEFAULT_REASONING_BUDGET,
        "default_top_p": DEFAULT_TOP_P,
    }


def reset_client() -> None:
    """
    Reset the shared client.

    Primarily useful for tests.

    The next call to generate_text() or generate_json() will recreate it.
    """

    global _client

    _client = None


# ============================================================================
# Public exports
# ============================================================================


__all__ = [
    "LLMError",
    "generate_text",
    "generate_json",
    "get_llm_config",
    "reset_client",
]