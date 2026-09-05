"""Tests for the FreeLLMAPI / NVIDIA provider selection layer in llm.py.

Validation scope — SOURCE-LEVEL (no real provider credentials required):
    All tests use monkeypatching / mock objects.  They verify the routing
    logic, parameter isolation, error handling, and JSON parsing behaviour
    without making real network calls.

Tests NOT covered here (require a running FreeLLMAPI instance):
    - Real FreeLLMAPI endpoint connectivity
    - Actual upstream provider routing and failover
    - End-to-end CV analysis through FreeLLMAPI

Manual integration test procedure is documented at the bottom of this file.
"""

from __future__ import annotations

import json
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.ai.llm as llm_module
import backend.core.config as config_module
from backend.ai.llm import (
    LLMError,
    _active_provider,
    _extract_json_object,
    _remove_markdown_json_fence,
    generate_json,
    generate_text,
    get_llm_config,
    reset_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: str) -> MagicMock:
    """Build a minimal OpenAI-compatible chat completion response mock."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.headers = {}
    return response


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> None:
    """Patch Settings fields and clear the lru_cache so changes take effect."""
    config_module.get_settings.cache_clear()
    for key, value in kwargs.items():
        monkeypatch.setattr(
            config_module.get_settings(),
            key,
            value,
            raising=False,
        )


def _configure_provider(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    """Set LLM_PROVIDER via monkeypatch and force cache invalidation."""
    config_module.get_settings.cache_clear()

    fake_settings = config_module.Settings.model_construct(
        LLM_PROVIDER=provider,
        FREELLMAPI_BASE_URL="http://localhost:3001/v1" if provider == "freellmapi" else None,
        FREELLMAPI_API_KEY="freellmapi-test-key" if provider == "freellmapi" else None,
        FREELLMAPI_MODEL="auto",
        ENVIRONMENT="development",
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_module, "get_settings", lambda: fake_settings)
    reset_client()


# ---------------------------------------------------------------------------
# Test 1: FreeLLMAPI configured → FreeLLMAPI is selected as primary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freellmapi_configured_is_selected_as_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LLM_PROVIDER=freellmapi, _active_provider() returns 'freellmapi'
    and generate_text() routes through the FreeLLMAPI client."""
    _configure_provider(monkeypatch, "freellmapi")

    freellmapi_called = []
    nvidia_called = []

    async def mock_freellmapi(prompt, *, system_prompt, temperature, max_tokens):
        freellmapi_called.append(True)
        return "FreeLLMAPI response"

    async def mock_nvidia(*args, **kwargs):
        nvidia_called.append(True)
        return "NVIDIA response"

    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)
    monkeypatch.setattr(llm_module, "_generate_with_nvidia", mock_nvidia)

    result = await generate_text("test prompt")

    assert result == "FreeLLMAPI response"
    assert len(freellmapi_called) == 1, "FreeLLMAPI must be called exactly once"
    assert len(nvidia_called) == 0, "NVIDIA must NOT be called when provider=freellmapi"


# ---------------------------------------------------------------------------
# Test 2: FreeLLMAPI not configured + NVIDIA configured → NVIDIA usable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nvidia_usable_when_freellmapi_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LLM_PROVIDER=nvidia, _active_provider() returns 'nvidia' and
    generate_text() routes through the NVIDIA client."""
    _configure_provider(monkeypatch, "nvidia")

    freellmapi_called = []
    nvidia_called = []

    async def mock_nvidia(
        prompt, *, system_prompt, temperature, max_tokens, top_p,
        enable_thinking, reasoning_budget
    ):
        nvidia_called.append(True)
        return "NVIDIA response"

    async def mock_freellmapi(*args, **kwargs):
        freellmapi_called.append(True)
        return "FreeLLMAPI response"

    monkeypatch.setattr(llm_module, "_generate_with_nvidia", mock_nvidia)
    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)

    result = await generate_text("test prompt")

    assert result == "NVIDIA response"
    assert len(nvidia_called) == 1, "NVIDIA must be called exactly once"
    assert len(freellmapi_called) == 0, "FreeLLMAPI must NOT be called when provider=nvidia"


# ---------------------------------------------------------------------------
# Test 3: LLM_PROVIDER=freellmapi → NVIDIA is not called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nvidia_not_called_when_provider_is_freellmapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_PROVIDER=freellmapi must never invoke _generate_with_nvidia."""
    _configure_provider(monkeypatch, "freellmapi")

    nvidia_was_invoked = []

    async def mock_nvidia(*args, **kwargs):
        nvidia_was_invoked.append(True)
        pytest.fail("_generate_with_nvidia must not be called when provider=freellmapi")

    async def mock_freellmapi(prompt, *, system_prompt, temperature, max_tokens):
        return "ok"

    monkeypatch.setattr(llm_module, "_generate_with_nvidia", mock_nvidia)
    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)

    await generate_text("hello")
    assert not nvidia_was_invoked


# ---------------------------------------------------------------------------
# Test 4: LLM_PROVIDER=nvidia → FreeLLMAPI is not called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freellmapi_not_called_when_provider_is_nvidia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_PROVIDER=nvidia must never invoke _generate_with_freellmapi."""
    _configure_provider(monkeypatch, "nvidia")

    freellmapi_was_invoked = []

    async def mock_freellmapi(*args, **kwargs):
        freellmapi_was_invoked.append(True)
        pytest.fail("_generate_with_freellmapi must not be called when provider=nvidia")

    async def mock_nvidia(
        prompt, *, system_prompt, temperature, max_tokens, top_p,
        enable_thinking, reasoning_budget
    ):
        return "ok"

    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)
    monkeypatch.setattr(llm_module, "_generate_with_nvidia", mock_nvidia)

    await generate_text("hello")
    assert not freellmapi_was_invoked


# ---------------------------------------------------------------------------
# Test 5: FreeLLMAPI request does NOT include NVIDIA-specific extra_body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freellmapi_request_has_no_nvidia_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_with_freellmapi must never pass NVIDIA-specific parameters
    (chat_template_kwargs, reasoning_budget, enable_thinking, top_p)."""
    _configure_provider(monkeypatch, "freellmapi")

    captured_kwargs: dict[str, Any] = {}

    async def mock_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return _make_response('{"result": "ok"}')

    fake_completions = MagicMock()
    fake_completions.create = mock_create
    fake_chat = MagicMock()
    fake_chat.completions = fake_completions
    fake_client = MagicMock()
    fake_client.chat = fake_chat

    monkeypatch.setattr(llm_module, "_freellmapi_client", fake_client)

    await generate_text("test prompt")

    # These NVIDIA-specific params must be absent.
    assert "extra_body" not in captured_kwargs, (
        "extra_body (NVIDIA chat_template_kwargs) must NOT be sent to FreeLLMAPI"
    )
    assert "top_p" not in captured_kwargs, (
        "top_p must NOT be sent to FreeLLMAPI (not universally supported)"
    )

    # These standard params must be present.
    assert "model" in captured_kwargs
    assert "messages" in captured_kwargs
    assert "temperature" in captured_kwargs
    assert "max_tokens" in captured_kwargs


# ---------------------------------------------------------------------------
# Test 6: NVIDIA request retains NVIDIA-specific extra_body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nvidia_request_retains_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_with_nvidia must include chat_template_kwargs in extra_body."""
    _configure_provider(monkeypatch, "nvidia")

    captured_kwargs: dict[str, Any] = {}

    async def mock_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return _make_response("NVIDIA text response")

    fake_completions = MagicMock()
    fake_completions.create = mock_create
    fake_chat = MagicMock()
    fake_chat.completions = fake_completions
    fake_client = MagicMock()
    fake_client.chat = fake_chat

    monkeypatch.setattr(llm_module, "_nvidia_client", fake_client)

    await generate_text("test prompt")

    assert "extra_body" in captured_kwargs, (
        "NVIDIA request must include extra_body with Nemotron parameters"
    )
    extra = captured_kwargs["extra_body"]
    assert "chat_template_kwargs" in extra, (
        "extra_body must contain chat_template_kwargs for Nemotron"
    )
    assert "enable_thinking" in extra["chat_template_kwargs"], (
        "chat_template_kwargs must contain enable_thinking"
    )
    assert "top_p" in captured_kwargs, "NVIDIA request must include top_p"


# ---------------------------------------------------------------------------
# Test 7: FreeLLMAPI failure produces LLMError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freellmapi_failure_produces_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When FreeLLMAPI raises an API error, generate_text must raise LLMError."""
    from openai import APIConnectionError

    _configure_provider(monkeypatch, "freellmapi")

    async def mock_create(**kwargs: Any) -> None:
        raise APIConnectionError(request=MagicMock())

    fake_completions = MagicMock()
    fake_completions.create = mock_create
    fake_chat = MagicMock()
    fake_chat.completions = fake_completions
    fake_client = MagicMock()
    fake_client.chat = fake_chat

    monkeypatch.setattr(llm_module, "_freellmapi_client", fake_client)

    with pytest.raises(LLMError) as exc_info:
        await generate_text("test prompt")

    assert "freellmapi" in str(exc_info.value).lower(), (
        "LLMError message should identify the freellmapi provider"
    )


# ---------------------------------------------------------------------------
# Test 8: generate_json() correctly parses valid JSON from provider response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_json_parses_valid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_json() must return a parsed dict when the model returns valid JSON."""
    _configure_provider(monkeypatch, "freellmapi")

    expected = {"name": "Alice", "skills": ["Python", "FastAPI"], "score": 85}

    async def mock_freellmapi(prompt, *, system_prompt, temperature, max_tokens):
        return json.dumps(expected)

    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)

    result = await generate_json("Analyse this CV.")

    assert result == expected
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 9: Malformed JSON from provider raises LLMError (not a crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_json_raises_llm_error_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_json() must raise LLMError when the provider returns unparseable
    output, not a bare JSONDecodeError or a silent wrong return value."""
    _configure_provider(monkeypatch, "freellmapi")

    async def mock_freellmapi(prompt, *, system_prompt, temperature, max_tokens):
        return "This is definitely not JSON at all!!!"

    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)

    with pytest.raises(LLMError) as exc_info:
        await generate_json("Analyse this CV.")

    assert "invalid JSON" in str(exc_info.value) or "json" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_generate_json_parses_json_with_markdown_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_json() must strip markdown code fences from model output."""
    _configure_provider(monkeypatch, "freellmapi")

    payload = {"career_level": "Senior", "skills": ["Go", "Kubernetes"]}

    async def mock_freellmapi(prompt, *, system_prompt, temperature, max_tokens):
        return f"```json\n{json.dumps(payload)}\n```"

    monkeypatch.setattr(llm_module, "_generate_with_freellmapi", mock_freellmapi)

    result = await generate_json("Analyse this CV.")
    assert result == payload


# ---------------------------------------------------------------------------
# Test 10: No provider API credentials are exposed in logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_api_credentials_in_log_output(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider API keys must never appear in log output, even during errors."""
    import logging

    fake_nvidia_key = "nvapi-SUPERSECRET1234567890"
    fake_freellmapi_key = "freellmapi-TOPSECRETKEY9876543210"

    # Patch environment values directly on the module.
    monkeypatch.setattr(llm_module, "NVIDIA_API_KEY", fake_nvidia_key)

    config_module.get_settings.cache_clear()
    fake_settings = config_module.Settings.model_construct(
        LLM_PROVIDER="freellmapi",
        FREELLMAPI_BASE_URL="http://localhost:3001/v1",
        FREELLMAPI_API_KEY=fake_freellmapi_key,
        FREELLMAPI_MODEL="auto",
        ENVIRONMENT="development",
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_module, "get_settings", lambda: fake_settings)
    reset_client()

    from openai import APIConnectionError

    async def mock_create(**kwargs: Any) -> None:
        raise APIConnectionError(request=MagicMock())

    fake_completions = MagicMock()
    fake_completions.create = mock_create
    fake_chat = MagicMock()
    fake_chat.completions = fake_completions
    fake_client = MagicMock()
    fake_client.chat = fake_chat

    monkeypatch.setattr(llm_module, "_freellmapi_client", fake_client)

    with caplog.at_level(logging.DEBUG, logger="backend.ai.llm"):
        with pytest.raises(LLMError):
            await generate_text("test prompt")

    all_log_output = "\n".join(caplog.messages)

    assert fake_nvidia_key not in all_log_output, (
        f"NVIDIA API key must not appear in logs. Found in: {all_log_output!r}"
    )
    assert fake_freellmapi_key not in all_log_output, (
        f"FreeLLMAPI API key must not appear in logs. Found in: {all_log_output!r}"
    )


# ---------------------------------------------------------------------------
# JSON extraction unit tests (provider-independent)
# ---------------------------------------------------------------------------

class TestExtractJsonObject:
    def test_clean_json_returned_as_is(self) -> None:
        data = {"key": "value", "num": 42}
        assert _extract_json_object(json.dumps(data)) == json.dumps(data)

    def test_json_with_leading_text_extracted(self) -> None:
        content = 'Here is the result:\n{"key": "value"}'
        result = _extract_json_object(content)
        assert json.loads(result) == {"key": "value"}

    def test_markdown_fence_stripped(self) -> None:
        content = '```json\n{"a": 1}\n```'
        result = _extract_json_object(content)
        assert json.loads(result) == {"a": 1}

    def test_nested_braces_handled_correctly(self) -> None:
        data = {"outer": {"inner": "value"}}
        result = _extract_json_object(json.dumps(data))
        assert json.loads(result) == data

    def test_garbage_input_returned_unchanged(self) -> None:
        garbage = "this is not json at all"
        result = _extract_json_object(garbage)
        # Should not raise; returns something (caller handles parse error)
        assert isinstance(result, str)


class TestRemoveMarkdownJsonFence:
    def test_json_fence_stripped(self) -> None:
        assert _remove_markdown_json_fence("```json\n{}\n```") == "{}"

    def test_plain_fence_stripped(self) -> None:
        assert _remove_markdown_json_fence("```\n{}\n```") == "{}"

    def test_no_fence_unchanged(self) -> None:
        assert _remove_markdown_json_fence('{"a": 1}') == '{"a": 1}'

    def test_whitespace_trimmed(self) -> None:
        assert _remove_markdown_json_fence('  {"a": 1}  ') == '{"a": 1}'


# ---------------------------------------------------------------------------
# get_llm_config returns provider-specific safe info
# ---------------------------------------------------------------------------

def test_get_llm_config_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_provider(monkeypatch, "nvidia")
    cfg = get_llm_config()
    assert cfg["active_provider"] == "nvidia"
    assert "freellmapi_api_key_configured" not in cfg
    assert "api_key_configured" in cfg


def test_get_llm_config_freellmapi(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_provider(monkeypatch, "freellmapi")
    cfg = get_llm_config()
    assert cfg["active_provider"] == "freellmapi"
    assert cfg["freellmapi_model"] == "auto"
    assert cfg["freellmapi_api_key_configured"] is True
    # Must not expose the actual key value.
    assert "freellmapi_api_key" not in cfg


# ---------------------------------------------------------------------------
# MANUAL INTEGRATION TEST PROCEDURE
# ---------------------------------------------------------------------------
#
# The following steps require a running FreeLLMAPI instance.
# Do NOT automate these — they require real provider API keys.
#
# 1. Install FreeLLMAPI locally:
#      curl -fsSL https://freellmapi.co/install.sh | bash
#    Or use the Windows .exe from Releases.
#
# 2. Open the FreeLLMAPI dashboard at http://localhost:3001
#    and add at least one provider API key (e.g. Groq, Cerebras,
#    or Google AI Studio — all offer free tiers with no credit card).
#
# 3. Copy the unified API key from the dashboard header.
#
# 4. Set in .env:
#      LLM_PROVIDER=freellmapi
#      FREELLMAPI_BASE_URL=http://localhost:3001/v1
#      FREELLMAPI_API_KEY=freellmapi-<your-unified-key>
#      FREELLMAPI_MODEL=auto
#
# 5. Start the Jobyn backend:
#      uvicorn backend.main:app --reload
#
# 6. Verify provider selection:
#      curl http://localhost:8000/api/v1/health/llm
#      Expected: {"active_provider": "freellmapi", ...}
#
# 7. Run a simple text generation test:
#      python -c "
#      import asyncio
#      from backend.ai.llm import generate_text
#      result = asyncio.run(generate_text('Say hello in one sentence.'))
#      print(result)
#      "
#
# 8. Run CV analysis with a SYNTHETIC test CV (do NOT use real user data):
#      python -c "
#      import asyncio
#      from backend.ai.cv_analyzer import CVAnalyzerService
#      svc = CVAnalyzerService()
#      result = asyncio.run(svc.analyze_cv(
#          'Jane Doe. Senior Python Developer. 7 years experience. '
#          'Skills: Python, FastAPI, PostgreSQL, Docker, AWS.'
#      ))
#      import json; print(json.dumps(result, indent=2))
#      "
#    Verify: result is a dict with all required schema fields.
#
# 9. Verify candidate profile generation:
#      (use output from step 8 as input to CandidateProfileService)
#
# 10. Verify job matching:
#       (pass the candidate profile and a list of synthetic jobs to
#        JobMatcherService.match_jobs())
#
# 11. Roll back to NVIDIA NIM:
#       Set LLM_PROVIDER=nvidia in .env and restart.
#       No code changes required.
# ---------------------------------------------------------------------------
