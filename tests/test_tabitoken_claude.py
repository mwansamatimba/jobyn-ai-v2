import pytest

from backend.ai.tabitoken_client import TaBiTokenClient


class FakeMessage:
    content = "Dear Hiring Manager,\n\nThis is a test cover letter."


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletions:
    async def create(self, **kwargs):
        return FakeResponse()


class FakeChat:
    completions = FakeCompletions()


class FakeClient:
    chat = FakeChat()


@pytest.mark.asyncio
async def test_tabitoken_cover_letter_generation(monkeypatch):
    monkeypatch.setenv(
        "TABITOKEN_API_KEY",
        "test-key",
    )

    client = object.__new__(TaBiTokenClient)

    client.client = FakeClient()
    client.model = "claude-opus-5"

    result = await client.generate_cover_letter(
        resume="Python developer with FastAPI experience.",
        job_title="Backend Engineer",
        company="TestCo",
        job_description="Build APIs.",
        key_selling_points=[
            "Python",
            "FastAPI",
        ],
        matched_requirements=[
            "Backend API development",
        ],
        addressed_skill_gaps=[
            "Kubernetes",
        ],
        additional_context=None,
    )

    assert isinstance(result, str)
    assert "Dear Hiring Manager" in result