"""Tests for cloud provider adapters."""

from __future__ import annotations

import httpx
import pytest

from config import Settings
from services.model_providers import (
    AnthropicTextProvider,
    GeminiEmbeddingProvider,
    OpenAICompatibleTextProvider,
)


@pytest.mark.asyncio
async def test_openai_compatible_text_provider_parses_chat_completions() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "fixed"}}],
                "usage": {"total_tokens": 21},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleTextProvider(
        provider_name="openai",
        base_url="https://example.test/v1",
        api_key="secret",
        model_name="gpt-test",
        timeout=30.0,
        client=client,
    )

    result = await provider.generate_text(
        system_prompt="system prompt",
        user_prompt="user prompt",
    )

    assert captured["path"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    assert result.text == "fixed"
    assert result.total_tokens == 21
    await provider.close()


@pytest.mark.asyncio
async def test_anthropic_text_provider_parses_messages_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "anthropic fix"}],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicTextProvider(
        base_url="https://api.anthropic.com",
        api_key="anthropic-key",
        api_version="2023-06-01",
        model_name="claude-sonnet-test",
        timeout=30.0,
        max_output_tokens=512,
        client=client,
    )

    result = await provider.generate_text(
        system_prompt="system prompt",
        user_prompt="user prompt",
    )

    assert result.text == "anthropic fix"
    assert result.total_tokens == 14
    await provider.close()


@pytest.mark.asyncio
async def test_gemini_embedding_provider_parses_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "gemini-key"
        return httpx.Response(
            200,
            json={"embedding": {"values": [0.1, 0.2, 0.3]}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiEmbeddingProvider(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-key",
        model_name="gemini-embedding-001",
        timeout=30.0,
        client=client,
    )

    embedding = await provider.embed_text("hello world")

    assert embedding == [0.1, 0.2, 0.3]
    await provider.close()


def test_settings_normalize_provider_aliases() -> None:
    settings = Settings(generation_provider="anthropic", embedding_provider="llmstudio")

    assert settings.normalized_generation_provider() == "claude"
    assert settings.normalized_embedding_provider() == "lmstudio"
