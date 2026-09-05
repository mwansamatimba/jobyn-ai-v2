"""
Provider-independent LLM client for Jobyn AI.

Supported providers (selected via LLM_PROVIDER env var):

    freellmapi  — FreeLLMAPI self-hosted router (EXPERIMENTAL / demo only).
                  Routes requests across configured free-tier providers via a
                  single OpenAI-compatible /v1 endpoint.

                  IMPORTANT — data privacy:
                  FreeLLMAPI forwards prompts to upstream free-tier providers
                  (Groq, Cerebras, Google AI Studio, NVIDIA, etc.).  Request
                  payloads including CV text leave your infrastructure and are
                  governed by each upstream provider's own data-retention
                  policy.  Do NOT use with real user data in production.
                  See https://github.com/tashfeenahmed/freellmapi#disclaimer

    nvidia      — NVIDIA NIM (existing behaviour, default).
                  Uses the OpenAI-compatible NVIDIA NIM endpoint with
                  Nemotron-specific extra_body parameters.

Public interface (unchanged — all AI services call these):

    generate_text()
    generate_json()

Provider selection is transparent to callers.  Switching providers requires
only a change to LLM_PROVIDER (and, for freellmapi, FREELLMAPI_BASE_URL and
FREELLMAPI_API_KEY).  No AI service file needs to change.
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

from backend.core.config import get_settings


# ============================================================================
# Environment
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# ============================================================================
# Logging
# ============================================================================

logger = logging.getLogger(__name__)

# ============================================================================
# NVIDIA NIM provider configuration
# ============================================================================
# Read directly from environment (historical behaviour retained).

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL",
    "https://integrate.api.nvidia.com/v1",
).rstrip("/")

# IMPORTANT: Do not use the retired GLM model as a fallback.
NVIDIA_MODEL = os.getenv(
    "NVIDIA_MODEL",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)

DEFAULT_ENABLE_THINKING = (
    os.getenv("NVIDIA_ENABLE_THINKING", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)


def _get_reasoning_budget() -> int:
    raw = os.getenv("NVIDIA_REASONING_BUDGET", "8192")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid NVIDIA_REASONING_BUDGET=%r, using 8192.", raw)
        return 8192
    if value < 1:
        logger.warning("NVIDIA_REASONING_BUDGET must be > 0, using 8192.")
        return 8192
    return value


DEFAULT_REASONING_BUDGET = _get_reasoning_budget()


def _get_top_p() -> float:
    raw = os.getenv("NVIDIA_TOP_P", "0.95")
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid NVIDIA_TOP_P=%r, using 0.95.", raw)
        return 0.95
    if not 0 < value <= 1:
        logger.warning("NVIDIA_TOP_P must be in (0, 1], using 0.95.")
        return 0.95
    return value


DEFAULT_TOP_P = _get_top_p()

# ============================================================================
# Logging limits
# ============================================================================

_MAX_BODY_LOG_CHARS = 2000

# ============================================================================
# Exceptions
# ============================================================================


class LLMError(Exception):
    """Raised when an LLM provider request fails."""


# ============================================================================
# Shared client singletons (one per provider)
# ============================================================================

_nvidia_client: AsyncOpenAI | None = None
_freellmapi_client: AsyncOpenAI | None = None


def _get_nvidia_client() -> AsyncOpenAI:
    """Return the shared NVIDIA NIM AsyncOpenAI client (created lazily)."""
    global _nvidia_client
    if _nvidia_client is None:
        api_key = NVIDIA_API_KEY
        if not api_key:
            raise LLMError(
                "NVIDIA_API_KEY is not configured. "
                "Add NVIDIA_API_KEY to the project's .env file."
            )
        _nvidia_client = AsyncOpenAI(
            api_key=api_key,
            base_url=NVIDIA_BASE_URL,
        )
    return _nvidia_client


def _get_freellmapi_client() -> AsyncOpenAI:
    """Return the shared FreeLLMAPI AsyncOpenAI client (created lazily).

    Raises LLMError if FreeLLMAPI is not configured.
    """
    global _freellmapi_client
    if _freellmapi_client is None:
        settings = get_settings()
        base_url = settings.FREELLMAPI_BASE_URL
        api_key = settings.FREELLMAPI_API_KEY
        if not base_url or not api_key:
            raise LLMError(
                "FreeLLMAPI is not configured. "
                "Set FREELLMAPI_BASE_URL and FREELLMAPI_API_KEY in .env, "
                "or switch LLM_PROVIDER=nvidia to use NVIDIA NIM."
            )
        _freellmapi_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
        )
    return _freellmapi_client


def _active_provider() -> str:
    """Return the configured provider name ('freellmapi' or 'nvidia')."""
    return get_settings().LLM_PROVIDER


# ============================================================================
# Logging / sanitisation helpers (provider-aware)
# ============================================================================


def _sanitize_text(text: str | None, *, provider: str = "unknown") -> str:
    """Truncate and redact secrets before logging.

    Never intentionally logs:
        - API keys (NVIDIA or FreeLLMAPI)
        - Bearer tokens
        - Authorization credentials
        - Prompt or CV content
    """
    if not text:
        return ""

    sanitized = str(text)

    # Redact NVIDIA API key if present.
    if NVIDIA_API_KEY:
        sanitized = sanitized.replace(NVIDIA_API_KEY, "[REDACTED_API_KEY]")

    # Redact FreeLLMAPI key if present.
    fk = get_settings().FREELLMAPI_API_KEY
    if fk:
        sanitized = sanitized.replace(fk, "[REDACTED_API_KEY]")

    # Generic Bearer token redaction.
    sanitized = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED_TOKEN]",
        sanitized,
    )

    # Generic API key pattern redaction.
    sanitized = re.sub(
        r"""(?i)(api[_-]?key\s*[:=]\s*)['"]?[^'"\s,}]+""",
        r"\1[REDACTED_API_KEY]",
        sanitized,
    )

    if len(sanitized) > _MAX_BODY_LOG_CHARS:
        sanitized = sanitized[:_MAX_BODY_LOG_CHARS] + "...[truncated]"

    return sanitized


def _classify_exception(exc: BaseException) -> str:
    """Return a stable category label for provider API failures."""
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


def _extract_http_details(exc: BaseException) -> dict[str, Any]:
    """Extract HTTP status and body from OpenAI-SDK exceptions."""
    details: dict[str, Any] = {
        "http_status": None,
        "response_body": None,
        "timeout": None,
    }

    status = getattr(exc, "status_code", None)
    if status is not None:
        details["http_status"] = status

    body = getattr(exc, "body", None)
    if body is not None:
        try:
            body_text = body if isinstance(body, str) else json.dumps(body, default=str)
        except Exception:
            body_text = str(body)
        details["response_body"] = _sanitize_text(body_text)

    response = getattr(exc, "response", None)
    if response is not None:
        if details["http_status"] is None:
            details["http_status"] = getattr(response, "status_code", None)
        if details["response_body"] is None:
            try:
                text = getattr(response, "text", None)
                if callable(text):
                    text = text()
                if text:
                    details["response_body"] = _sanitize_text(str(text))
            except Exception:
                pass

    if isinstance(exc, APITimeoutError):
        details["timeout"] = True
        details["timeout_info"] = _sanitize_text(str(exc))

    if getattr(exc, "request", None) is not None:
        details["has_request_object"] = True

    return details


def _log_provider_failure(
    *,
    provider: str,
    model: str,
    operation: str,
    exc: BaseException,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a structured provider failure.

    Never logs prompts, resume text, CV contents, or API keys.
    """
    category = _classify_exception(exc)
    http_details = _extract_http_details(exc)

    payload: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "operation": operation,
        "error_category": category,
        "exception_type": type(exc).__name__,
        "exception_message": _sanitize_text(str(exc), provider=provider),
        **http_details,
    }
    if extra:
        payload.update(extra)

    logger.exception("LLM request failed: %s", payload)


# ============================================================================
# NVIDIA NIM — request helpers
# ============================================================================


def _build_nvidia_extra_body(
    *,
    enable_thinking: bool,
    reasoning_budget: int | None,
) -> dict[str, Any]:
    """Build NVIDIA Nemotron-specific request parameters.

    These parameters MUST NOT be sent to FreeLLMAPI or any other provider.
    """
    extra_body: dict[str, Any] = {
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
        },
    }
    if enable_thinking and reasoning_budget is not None:
        if reasoning_budget < 1:
            raise LLMError("reasoning_budget must be greater than zero.")
        extra_body["reasoning_budget"] = reasoning_budget
    return extra_body


def _extract_message_content(response: Any) -> str:
    """Safely extract final textual content from an OpenAI-compatible response."""
    choices = getattr(response, "choices", None)
    if not choices:
        raise LLMError("Provider returned no choices.")

    message = choices[0].message
    content = getattr(message, "content", None)
    if content is None:
        raise LLMError("Provider returned no message content.")
    if not isinstance(content, str):
        content = str(content)
    content = content.strip()
    if not content:
        raise LLMError("Provider returned an empty response.")
    return content


# ============================================================================
# NVIDIA NIM — core generation
# ============================================================================


async def _generate_with_nvidia(
    prompt: str,
    *,
    system_prompt: str | None,
    temperature: float,
    max_tokens: int,
    top_p: float,
    enable_thinking: bool,
    reasoning_budget: int | None,
) -> str:
    """Generate text via NVIDIA NIM with Nemotron-specific parameters."""
    client = _get_nvidia_client()

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt.strip()})

    extra_body = _build_nvidia_extra_body(
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )

    logger.info(
        "LLM request starting provider=nvidia_nim model=%s operation=generate_text "
        "temperature=%s max_tokens=%s top_p=%s thinking=%s",
        NVIDIA_MODEL,
        temperature,
        max_tokens,
        top_p,
        enable_thinking,
    )

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
    except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
        _log_provider_failure(
            provider="nvidia_nim",
            model=NVIDIA_MODEL,
            operation="generate_text",
            exc=exc,
        )
        raise LLMError(
            f"NVIDIA NIM request failed ({_classify_exception(exc)})."
        ) from exc
    except Exception as exc:
        _log_provider_failure(
            provider="nvidia_nim",
            model=NVIDIA_MODEL,
            operation="generate_text",
            exc=exc,
        )
        raise LLMError("Unexpected NVIDIA NIM error.") from exc

    try:
        content = _extract_message_content(response)
    except LLMError:
        logger.error(
            "LLM invalid response provider=nvidia_nim model=%s operation=generate_text "
            "error_category=invalid_empty_response",
            NVIDIA_MODEL,
        )
        raise

    logger.info(
        "LLM request completed provider=nvidia_nim model=%s operation=generate_text",
        NVIDIA_MODEL,
    )
    return content


# ============================================================================
# FreeLLMAPI — core generation
# ============================================================================


async def _generate_with_freellmapi(
    prompt: str,
    *,
    system_prompt: str | None,
    temperature: float,
    max_tokens: int,
) -> str:
    """Generate text via FreeLLMAPI.

    Uses the standard OpenAI chat.completions.create interface with no
    provider-specific extra_body.  The "model" field carries the FreeLLMAPI
    routing strategy (e.g. "auto", "auto:fast", "auto:smart", or a specific
    model id from the catalog).

    NVIDIA Nemotron parameters (chat_template_kwargs, reasoning_budget,
    enable_thinking) are intentionally NOT sent here.
    """
    settings = get_settings()
    model = settings.FREELLMAPI_MODEL
    client = _get_freellmapi_client()

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt.strip()})

    logger.info(
        "LLM request starting provider=freellmapi model=%s operation=generate_text "
        "temperature=%s max_tokens=%s",
        model,
        temperature,
        max_tokens,
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            # top_p intentionally omitted: not universally supported across
            # all providers behind FreeLLMAPI's router.
            # response_format intentionally omitted: JSON reliability across
            # 34 providers varies; application-side extraction handles it.
        )
    except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
        _log_provider_failure(
            provider="freellmapi",
            model=model,
            operation="generate_text",
            exc=exc,
        )
        raise LLMError(
            f"FreeLLMAPI request failed ({_classify_exception(exc)})."
        ) from exc
    except Exception as exc:
        _log_provider_failure(
            provider="freellmapi",
            model=model,
            operation="generate_text",
            exc=exc,
        )
        raise LLMError("Unexpected FreeLLMAPI error.") from exc

    routed_via = getattr(response, "headers", {})
    if hasattr(routed_via, "get"):
        routed_via = routed_via.get("x-routed-via", "unknown")
    else:
        routed_via = "unknown"

    try:
        content = _extract_message_content(response)
    except LLMError:
        logger.error(
            "LLM invalid response provider=freellmapi model=%s operation=generate_text "
            "error_category=invalid_empty_response routed_via=%s",
            model,
            routed_via,
        )
        raise

    logger.info(
        "LLM request completed provider=freellmapi model=%s operation=generate_text "
        "routed_via=%s",
        model,
        routed_via,
    )
    return content


# ============================================================================
# JSON cleaning helpers (provider-independent)
# ============================================================================


def _remove_markdown_json_fence(content: str) -> str:
    """Remove common Markdown JSON code fences from model output."""
    cleaned = content.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines:
        first = lines[0].strip().lower()
        if first in {"```", "```json", "```javascript", "```js"}:
            lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(content: str) -> str:
    """Extract a JSON object from slightly noisy model output.

    First attempts the entire response.  If that fails, scans for a
    balanced JSON object using a brace scanner (safer than rfind).
    """
    cleaned = _remove_markdown_json_fence(content)

    # First attempt: exact response.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return cleaned
    except json.JSONDecodeError:
        pass

    # Second attempt: scan for a balanced JSON object.
    start_index = cleaned.find("{")
    if start_index == -1:
        return cleaned

    depth = 0
    in_string = False
    escaped = False

    for index in range(start_index, len(cleaned)):
        char = cleaned[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start_index : index + 1].strip()
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return candidate
                except json.JSONDecodeError:
                    return cleaned

    return cleaned


# ============================================================================
# Public API — generate_text
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
    """Generate text using the configured LLM provider.

    The active provider is selected by LLM_PROVIDER:

        LLM_PROVIDER=freellmapi  — routes via FreeLLMAPI (experimental).
        LLM_PROVIDER=nvidia      — calls NVIDIA NIM directly (default).

    Args:
        prompt:
            User prompt.
        system_prompt:
            Optional system instruction.
        temperature:
            Sampling temperature between 0 and 1.
        max_tokens:
            Maximum generated tokens.
        top_p:
            Nucleus sampling (NVIDIA path only; ignored for FreeLLMAPI).
        enable_thinking:
            Whether Nemotron thinking mode is enabled (NVIDIA path only).
        reasoning_budget:
            Reasoning token budget when thinking is enabled (NVIDIA only).

    Returns:
        Generated text string.

    Raises:
        LLMError: If validation or provider generation fails.
    """
    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    if not isinstance(prompt, str):
        raise LLMError("Prompt must be a string.")
    if not prompt.strip():
        raise LLMError("Prompt must be a non-empty string.")
    if not isinstance(temperature, (int, float)):
        raise LLMError("Temperature must be a number.")
    if not 0 <= temperature <= 1:
        raise LLMError("Temperature must be between 0 and 1.")
    if not isinstance(max_tokens, int):
        raise LLMError("max_tokens must be an integer.")
    if max_tokens < 1:
        raise LLMError("max_tokens must be greater than zero.")

    provider = _active_provider()

    # ------------------------------------------------------------------
    # FreeLLMAPI path — no NVIDIA-specific parameters
    # ------------------------------------------------------------------
    if provider == "freellmapi":
        # top_p, enable_thinking and reasoning_budget are NVIDIA-specific;
        # they are intentionally NOT forwarded to FreeLLMAPI.
        return await _generate_with_freellmapi(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # NVIDIA NIM path — full Nemotron parameter support
    # ------------------------------------------------------------------
    if top_p is None:
        top_p = DEFAULT_TOP_P
    if not isinstance(top_p, (int, float)):
        raise LLMError("top_p must be a number.")
    if not 0 < top_p <= 1:
        raise LLMError("top_p must be greater than 0 and <= 1.")

    if enable_thinking is None:
        enable_thinking = DEFAULT_ENABLE_THINKING
    if reasoning_budget is None:
        reasoning_budget = DEFAULT_REASONING_BUDGET

    return await _generate_with_nvidia(
        prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )


# ============================================================================
# Public API — generate_json
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
    """Generate and parse a JSON object using the configured LLM provider.

    Designed for all structured-output workloads:
        - CV analysis
        - candidate profiles
        - career navigation
        - job matching
        - interview coaching

    JSON extraction uses application-side parsing rather than
    provider-specific structured-output APIs, ensuring compatibility
    across both NVIDIA NIM and any model routed by FreeLLMAPI.

    Note on FreeLLMAPI:
        response_format={"type":"json_object"} is NOT sent because JSON
        mode support varies across the 34+ providers FreeLLMAPI routes
        through.  The JSON instruction in the system prompt and the
        application-side extractor handle model output reliably.
    """
    provider = _active_provider()

    # ------------------------------------------------------------------
    # Strong JSON instruction (provider-independent)
    # ------------------------------------------------------------------
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

    combined_system_prompt = (
        f"{system_prompt.strip()}\n\n{json_instruction}"
        if system_prompt and system_prompt.strip()
        else json_instruction
    )

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------
    content = await generate_text(
        prompt,
        system_prompt=combined_system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )

    # ------------------------------------------------------------------
    # Extract and parse JSON
    # ------------------------------------------------------------------
    cleaned = _extract_json_object(content)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "LLM malformed JSON provider=%s operation=generate_json "
            "error_category=malformed_json "
            "exception_type=%s exception_message=%s response_preview=%s",
            provider,
            type(exc).__name__,
            _sanitize_text(str(exc)),
            _sanitize_text(cleaned[:500]),
        )
        raise LLMError(f"LLM provider ({provider}) returned invalid JSON.") from exc

    if not isinstance(result, dict):
        logger.error(
            "LLM schema validation failure provider=%s operation=generate_json "
            "error_category=model_output_schema_validation_failure got_type=%s",
            provider,
            type(result).__name__,
        )
        raise LLMError(
            f"LLM provider ({provider}) JSON response must be a JSON object."
        )

    logger.info(
        "LLM JSON parse succeeded provider=%s operation=generate_json",
        provider,
    )
    return result


# ============================================================================
# Health / configuration helpers
# ============================================================================


def get_llm_config() -> dict[str, Any]:
    """Return safe LLM configuration information.

    No API keys are ever returned.
    """
    provider = _active_provider()
    settings = get_settings()

    base: dict[str, Any] = {
        "active_provider": provider,
    }

    if provider == "freellmapi":
        base.update(
            {
                "freellmapi_base_url": settings.FREELLMAPI_BASE_URL,
                "freellmapi_model": settings.FREELLMAPI_MODEL,
                "freellmapi_api_key_configured": bool(settings.FREELLMAPI_API_KEY),
            }
        )
    else:
        base.update(
            {
                "provider": "nvidia_nim",
                "model": NVIDIA_MODEL,
                "base_url": NVIDIA_BASE_URL,
                "api_key_configured": bool(NVIDIA_API_KEY),
                "default_enable_thinking": DEFAULT_ENABLE_THINKING,
                "default_reasoning_budget": DEFAULT_REASONING_BUDGET,
                "default_top_p": DEFAULT_TOP_P,
            }
        )

    return base


# ============================================================================
# Client lifecycle helpers
# ============================================================================


async def close_client() -> None:
    """Close both provider clients.

    Useful during application shutdown and tests.
    """
    global _nvidia_client, _freellmapi_client

    for client, name in [
        (_nvidia_client, "nvidia"),
        (_freellmapi_client, "freellmapi"),
    ]:
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.warning("Failed to close %s client.", name, exc_info=True)

    _nvidia_client = None
    _freellmapi_client = None


def reset_client() -> None:
    """Reset both client references without awaiting close.

    Primarily useful for tests.  Use close_client() during shutdown.
    """
    global _nvidia_client, _freellmapi_client
    _nvidia_client = None
    _freellmapi_client = None


# ============================================================================
# Public exports
# ============================================================================

__all__ = [
    "LLMError",
    "generate_text",
    "generate_json",
    "get_llm_config",
    "close_client",
    "reset_client",
    # Exposed for tests and health endpoints:
    "NVIDIA_API_KEY",
    "NVIDIA_MODEL",
    "NVIDIA_BASE_URL",
]
