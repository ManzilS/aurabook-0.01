"""Shared test fixtures for the AuraBook test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aurabook.adapters.base import AdapterBase
from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, Message, Role
from aurabook.utils.config import Settings


class FakeAdapter(AdapterBase):
    """Small adapter double used by the application tests."""

    name = "openai_proxy"
    supports_streaming = True

    def __init__(self, reply: str = "OK") -> None:
        self.reply = reply
        self.last_request: ChatCompletionRequest | None = None

    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, object]:
        return request.model_dump()

    def translate_to_universal(self, raw: dict[str, object]) -> ChatCompletionResponse:
        return ChatCompletionResponse.model_validate(raw)

    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        self.last_request = request
        return ChatCompletionResponse(
            model=request.model,
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content=self.reply),
                    finish_reason="stop",
                )
            ],
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        self.last_request = request
        yield 'data: {"choices":[{"delta":{"content":"stream"}}]}\n\n'
        yield "data: [DONE]\n\n"


class StubWatcher:
    """Watcher double that records lifecycle calls."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Return a deterministic settings object for tests."""
    return Settings(
        upstream_base_url="https://example.test/v1",
        upstream_api_key="test-upstream-key",
        watch_root=str(tmp_path),
        state_dir=str(tmp_path / ".aurabook"),
        default_adapter="openai_proxy",
        cloud_enabled=False,
    )
