"""Tests for non-OpenAI local provider adapters."""

from __future__ import annotations

import httpx
import pytest

from aurabook.adapters.anthropic import AnthropicAdapter
from aurabook.adapters.gemini import GeminiAdapter
from aurabook.core.models import ChatCompletionRequest, Message, Role


@pytest.mark.asyncio
async def test_anthropic_adapter_translates_messages(settings) -> None:
    configured = settings.model_copy(
        update={"anthropic_api_key": "anthropic-key", "anthropic_base_url": "https://api.anthropic.com"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == configured.anthropic_api_key
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-test",
                "content": [{"type": "text", "text": "claude reply"}],
                "usage": {"input_tokens": 8, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AnthropicAdapter(configured, client=client)

    response = await adapter.send(
        ChatCompletionRequest(
            model="claude-sonnet-test",
            messages=[
                Message(role=Role.SYSTEM, content="You are helpful."),
                Message(role=Role.USER, content="hello"),
            ],
        )
    )

    assert response.text == "claude reply"
    assert response.usage.total_tokens == 11
    await adapter.close()


@pytest.mark.asyncio
async def test_gemini_adapter_translates_messages(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "gemini-key"
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-test",
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "gemini reply"}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 7,
                },
            },
        )

    configured = settings.model_copy(
        update={
            "gemini_api_key": "gemini-key",
            "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GeminiAdapter(configured, client=client)

    response = await adapter.send(
        ChatCompletionRequest(
            model="gemini-test",
            messages=[
                Message(role=Role.SYSTEM, content="You are helpful."),
                Message(role=Role.USER, content="hello"),
            ],
        )
    )

    assert response.text == "gemini reply"
    assert response.usage.total_tokens == 7
    await adapter.close()
