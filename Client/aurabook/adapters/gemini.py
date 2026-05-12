"""Gemini generateContent adapter."""

from __future__ import annotations

from typing import Any

import httpx

from aurabook.adapters.base import AdapterBase
from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, Message, Role, Usage
from aurabook.utils.config import Settings
from aurabook.utils.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterRateLimitError,
    AdapterTimeoutError,
)


class GeminiAdapter(AdapterBase):
    """Adapter that translates OpenAI-style chat requests into Gemini generateContent calls."""

    name = "gemini"
    supports_streaming = False

    def __init__(
        self,
        settings: Settings,
        *,
        adapter_name: str = "gemini",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self.name = adapter_name
        self._base_url = settings.provider_base_url(adapter_name).rstrip("/")
        self._api_key = settings.provider_api_key(adapter_name)
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._owns_client = client is None

    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Convert an OpenAI-style request into a Gemini payload."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in request.messages:
            text = message.text_content().strip()
            if not text:
                continue
            if message.role == Role.SYSTEM:
                system_parts.append(text)
                continue
            role = "model" if message.role == Role.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": text}]})
        if not contents:
            contents.append(
                {"role": "user", "parts": [{"text": request.messages[-1].text_content()}]}
            )
        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return payload

    def translate_to_universal(self, raw: dict[str, Any]) -> ChatCompletionResponse:
        """Convert a Gemini response into the shared response shape."""
        texts: list[str] = []
        candidates = raw.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content", {})
            if isinstance(content, dict):
                parts = content.get("parts", [])
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict):
                            text = part.get("text")
                            if isinstance(text, str) and text.strip():
                                texts.append(text.strip())
        usage = raw.get("usageMetadata", {})
        prompt_tokens = int(usage.get("promptTokenCount", 0) or 0)
        completion_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        total_tokens = int(usage.get("totalTokenCount", 0) or 0) or (
            prompt_tokens + completion_tokens
        )
        return ChatCompletionResponse(
            model=str(raw.get("modelVersion", "")),
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content="\n".join(texts)),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        )

    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a non-streaming request to Gemini."""
        payload = self.translate_to_ai(request)
        try:
            response = await self._client.post(
                self._generate_url(request.model),
                params={"key": self._api_key or self._forwarded_api_key(request)},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                "Upstream Gemini request timed out",
                adapter=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._classify_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"Failed to contact Gemini API: {exc}",
                adapter=self.name,
            ) from exc
        return self.translate_to_universal(response.json())

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()

    def _generate_url(self, model: str) -> str:
        """Return the configured generateContent URL for a model."""
        return f"{self._base_url}/models/{model}:generateContent"

    def _forwarded_api_key(self, request: ChatCompletionRequest) -> str:
        """Extract a forwarded bearer token and use it as the Gemini key."""
        forwarded_auth = request.metadata.get("upstream_authorization")
        if not isinstance(forwarded_auth, str) or not forwarded_auth:
            return ""
        if forwarded_auth.lower().startswith("bearer "):
            return forwarded_auth[7:].strip()
        return forwarded_auth.strip()

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> AdapterError:
        """Map HTTP status codes into structured adapter errors."""
        status_code = exc.response.status_code
        message = exc.response.text or "Gemini API returned an error"
        if status_code in (401, 403):
            return AdapterAuthError(message, adapter=self.name)
        if status_code == 429:
            return AdapterRateLimitError(message, adapter=self.name)
        return AdapterError(message, adapter=self.name)
