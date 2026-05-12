"""Provider abstractions for generation and embedding backends."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from config import Settings
from services.fingerprints import normalize_text
from services.openai_failures import is_openai_halt_error

_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class GeneratedText:
    """Normalized text-generation result."""

    text: str
    total_tokens: int = 0


class ProviderRequestError(RuntimeError):
    """Structured provider error with retry and halt hints."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int = 0,
        retryable: bool = False,
        halt: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        self.halt = halt


class TextGenerationProvider(Protocol):
    """Interface implemented by generation backends."""

    @property
    def model_name(self) -> str:
        """Return the configured default model name."""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> GeneratedText:
        """Generate text for a system/user prompt pair."""

    async def close(self) -> None:
        """Release any underlying resources."""


class EmbeddingProvider(Protocol):
    """Interface implemented by embedding backends."""

    @property
    def model_name(self) -> str:
        """Return the configured default embedding model."""

    async def embed_text(self, text: str) -> list[float]:
        """Embed one text value."""

    async def close(self) -> None:
        """Release any underlying resources."""


def build_text_generation_provider(
    settings: Settings,
    *,
    purpose: str,
) -> TextGenerationProvider:
    """Construct the configured generation provider."""
    provider = settings.normalized_generation_provider()
    model = settings.resolve_summary_model() if purpose == "summary" else settings.resolve_fix_model()
    timeout = settings.provider_request_timeout

    if provider in {"openai", "ollama", "lmstudio", "qwen"}:
        return OpenAICompatibleTextProvider(
            provider_name=provider,
            base_url=settings.provider_base_url(provider),
            api_key=settings.provider_api_key(provider),
            model_name=model,
            timeout=timeout,
        )
    if provider == "claude":
        return AnthropicTextProvider(
            base_url=settings.provider_base_url(provider),
            api_key=settings.provider_api_key(provider),
            api_version=settings.anthropic_version,
            model_name=model,
            timeout=timeout,
            max_output_tokens=settings.provider_max_output_tokens,
        )
    if provider == "gemini":
        return GeminiTextProvider(
            base_url=settings.provider_base_url(provider),
            api_key=settings.provider_api_key(provider),
            model_name=model,
            timeout=timeout,
        )
    raise ValueError(f"Unsupported generation provider '{provider}'")


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Construct the configured embedding provider."""
    provider = settings.normalized_embedding_provider()
    model = settings.resolve_embedding_model()
    timeout = settings.provider_request_timeout

    if provider in {"openai", "ollama", "lmstudio", "qwen"}:
        return OpenAICompatibleEmbeddingProvider(
            provider_name=provider,
            base_url=settings.provider_base_url(provider),
            api_key=settings.provider_api_key(provider),
            model_name=model,
            timeout=timeout,
        )
    if provider == "gemini":
        return GeminiEmbeddingProvider(
            base_url=settings.provider_base_url(provider),
            api_key=settings.provider_api_key(provider),
            model_name=model,
            timeout=timeout,
        )
    raise ValueError(f"Unsupported embedding provider '{provider}'")


class OpenAICompatibleTextProvider:
    """Generation provider for OpenAI-compatible chat-completions APIs."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> GeneratedText:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json={
                "model": self._model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            headers=_openai_compatible_headers(self._api_key),
        )
        payload = _parse_json_response(response, self._provider_name)
        choices = payload.get("choices", [])
        if not choices:
            raise ProviderRequestError(
                f"{self._provider_name} returned no choices",
                provider=self._provider_name,
            )
        message = choices[0].get("message", {})
        text = _coerce_message_text(message.get("content"))
        if not text:
            raise ProviderRequestError(
                f"{self._provider_name} returned an empty message",
                provider=self._provider_name,
            )
        usage = payload.get("usage", {})
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        return GeneratedText(text=text, total_tokens=total_tokens)

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()


class AnthropicTextProvider:
    """Generation provider for Anthropic Messages API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_version: str,
        model_name: str,
        timeout: float,
        max_output_tokens: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_version = api_version
        self._model_name = model_name
        self._max_output_tokens = max_output_tokens
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> GeneratedText:
        response = await self._client.post(
            _versioned_endpoint(self._base_url, "/messages"),
            json={
                "model": self._model_name,
                "max_tokens": self._max_output_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": self._api_version,
            },
        )
        payload = _parse_json_response(response, "claude")
        text = _coerce_anthropic_text(payload.get("content", []))
        if not text:
            raise ProviderRequestError(
                "claude returned an empty message",
                provider="claude",
            )
        usage = payload.get("usage", {})
        total_tokens = int(usage.get("input_tokens", 0) or 0) + int(
            usage.get("output_tokens", 0) or 0
        )
        return GeneratedText(text=text, total_tokens=total_tokens)

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()


class GeminiTextProvider:
    """Generation provider for Gemini generateContent."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> GeneratedText:
        response = await self._client.post(
            f"{self._base_url}/models/{self._model_name}:generateContent",
            params={"key": self._api_key},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            },
            headers={"Content-Type": "application/json"},
        )
        payload = _parse_json_response(response, "gemini")
        text = _coerce_gemini_text(payload)
        if not text:
            raise ProviderRequestError(
                "gemini returned an empty message",
                provider="gemini",
            )
        usage = payload.get("usageMetadata", {})
        total_tokens = int(usage.get("totalTokenCount", 0) or 0)
        if total_tokens == 0:
            total_tokens = int(usage.get("promptTokenCount", 0) or 0) + int(
                usage.get("candidatesTokenCount", 0) or 0
            )
        return GeneratedText(text=text, total_tokens=total_tokens)

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()


class OpenAICompatibleEmbeddingProvider:
    """Embedding provider for OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    async def embed_text(self, text: str) -> list[float]:
        normalized = normalize_text(text)
        if not normalized.strip():
            raise ValueError("Cannot embed empty text")
        response = await self._client.post(
            f"{self._base_url}/embeddings",
            json={"model": self._model_name, "input": normalized},
            headers=_openai_compatible_headers(self._api_key),
        )
        payload = _parse_json_response(response, self._provider_name)
        data = payload.get("data", [])
        if not data:
            raise ProviderRequestError(
                f"{self._provider_name} returned no embeddings",
                provider=self._provider_name,
            )
        embedding = data[0].get("embedding")
        if not isinstance(embedding, list):
            raise ProviderRequestError(
                f"{self._provider_name} returned an invalid embedding payload",
                provider=self._provider_name,
            )
        return [float(value) for value in embedding]

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()


class GeminiEmbeddingProvider:
    """Embedding provider for Gemini embedContent."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    async def embed_text(self, text: str) -> list[float]:
        normalized = normalize_text(text)
        if not normalized.strip():
            raise ValueError("Cannot embed empty text")
        response = await self._client.post(
            f"{self._base_url}/models/{self._model_name}:embedContent",
            params={"key": self._api_key},
            json={"content": {"parts": [{"text": normalized}]}},
            headers={"Content-Type": "application/json"},
        )
        payload = _parse_json_response(response, "gemini")
        values = payload.get("embedding", {}).get("values")
        if not isinstance(values, list):
            raise ProviderRequestError(
                "gemini returned an invalid embedding payload",
                provider="gemini",
            )
        return [float(value) for value in values]

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()


def _openai_compatible_headers(api_key: str) -> dict[str, str]:
    """Build standard headers for OpenAI-compatible endpoints."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _versioned_endpoint(base_url: str, suffix: str) -> str:
    """Append a suffix to a provider base URL with one `/v1` segment."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}{suffix}"
    return f"{normalized}/v1{suffix}"


def _parse_json_response(response: httpx.Response, provider: str) -> dict[str, Any]:
    """Validate a JSON HTTP response or raise a structured provider error."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        message = _extract_error_message(exc.response)
        raise ProviderRequestError(
            message,
            provider=provider,
            status_code=exc.response.status_code,
            retryable=exc.response.status_code in _RETRYABLE_STATUS_CODES,
            halt=is_openai_halt_error(RuntimeError(message)),
        ) from exc
    except httpx.TimeoutException as exc:
        raise ProviderRequestError(
            f"{provider} request timed out",
            provider=provider,
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderRequestError(
            f"{provider} request failed: {exc}",
            provider=provider,
            retryable=True,
        ) from exc

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderRequestError(
            f"{provider} returned invalid JSON",
            provider=provider,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderRequestError(
            f"{provider} returned an unexpected payload type",
            provider=provider,
        )
    return payload


def _extract_error_message(response: httpx.Response) -> str:
    """Extract the most useful error message from a provider response."""
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("details")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return response.text or f"HTTP {response.status_code}"


def _coerce_message_text(content: Any) -> str:
    """Coerce an OpenAI-compatible message content payload into text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                    continue
                nested = item.get("content")
                if isinstance(nested, str) and nested.strip():
                    parts.append(nested.strip())
        return "\n".join(parts)
    return ""


def _coerce_anthropic_text(content: Any) -> str:
    """Coerce Anthropic content blocks into plain text."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _coerce_gemini_text(payload: dict[str, Any]) -> str:
    """Coerce Gemini candidate content into plain text."""
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content", {})
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n".join(texts)
