"""Tests for the OpenAI proxy adapter."""

from __future__ import annotations

import httpx
import pytest

from aurabook.adapters.openai_proxy import OpenAIProxyAdapter
from aurabook.core.models import ChatCompletionRequest, Message, Role
from aurabook.utils.errors import AdapterRateLimitError


@pytest.mark.asyncio
async def test_send_forwards_openai_payload(settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        captured["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_123",
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIProxyAdapter(settings=settings, client=client)

    response = await adapter.send(
        ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")], model="gpt-test")
    )

    assert '"model":"gpt-test"' in str(captured["body"])
    assert captured["authorization"] == "Bearer test-upstream-key"
    assert response.text == "hello"

    await client.aclose()


@pytest.mark.asyncio
async def test_send_classifies_rate_limit_errors(settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many requests")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIProxyAdapter(settings=settings, client=client)

    with pytest.raises(AdapterRateLimitError):
        await adapter.send(
            ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")], model="gpt-test")
        )

    await client.aclose()


@pytest.mark.asyncio
async def test_stream_yields_sse_chunks(settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIProxyAdapter(settings=settings, client=client)
    request = ChatCompletionRequest(
        messages=[Message(role=Role.USER, content="hi")],
        model="gpt-test",
        stream=True,
    )

    chunks = [chunk async for chunk in adapter.stream(request)]

    assert chunks[0].startswith("data:")
    assert chunks[-1] == "data: [DONE]\n\n"

    await client.aclose()
