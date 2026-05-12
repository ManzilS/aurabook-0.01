"""Anthropic Messages API adapter."""

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


class AnthropicAdapter(AdapterBase):
    """Adapter that translates OpenAI-style chat requests into Anthropic Messages API calls."""

    name = "claude"
    supports_streaming = False

    def __init__(
        self,
        settings: Settings,
        *,
        adapter_name: str = "claude",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self.name = adapter_name
        self._base_url = settings.provider_base_url(adapter_name).rstrip("/")
        self._api_key = settings.provider_api_key(adapter_name)
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._owns_client = client is None

    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Convert an OpenAI-style request into an Anthropic payload."""
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            text = message.text_content().strip()
            if not text:
                continue
            if message.role == Role.SYSTEM:
                system_parts.append(text)
                continue
            role = "assistant" if message.role == Role.ASSISTANT else "user"
            messages.append({"role": role, "content": text})
        if not messages:
            messages.append({"role": "user", "content": request.messages[-1].text_content()})
        return {
            "model": request.model,
            "max_tokens": request.max_tokens or 2048,
            "system": "\n\n".join(system_parts),
            "messages": messages,
        }

    def translate_to_universal(self, raw: dict[str, Any]) -> ChatCompletionResponse:
        """Convert an Anthropic response into the shared response shape."""
        parts = raw.get("content", [])
        text_segments: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        text_segments.append(text.strip())
        usage = raw.get("usage", {})
        return ChatCompletionResponse(
            model=str(raw.get("model", "")),
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content="\n".join(text_segments)),
                    finish_reason=str(raw.get("stop_reason", "stop") or "stop"),
                )
            ],
            usage=Usage(
                prompt_tokens=int(usage.get("input_tokens", 0) or 0),
                completion_tokens=int(usage.get("output_tokens", 0) or 0),
                total_tokens=int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("output_tokens", 0) or 0),
            ),
        )

    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a non-streaming request to Anthropic."""
        payload = self.translate_to_ai(request)
        headers = self._build_headers(request)
        try:
            response = await self._client.post(
                self._messages_url(),
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                "Upstream Anthropic request timed out",
                adapter=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._classify_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"Failed to contact Anthropic API: {exc}",
                adapter=self.name,
            ) from exc
        return self.translate_to_universal(response.json())

    async def close(self) -> None:
        """Close the underlying HTTP client when owned."""
        if self._owns_client:
            await self._client.aclose()

    def _build_headers(self, request: ChatCompletionRequest) -> dict[str, str]:
        """Build outbound headers for Anthropic."""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self._settings.anthropic_version,
        }
        api_key = self._api_key or self._forwarded_api_key(request)
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def _messages_url(self) -> str:
        """Return the configured Messages API URL."""
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/messages"
        return f"{self._base_url}/v1/messages"

    def _forwarded_api_key(self, request: ChatCompletionRequest) -> str:
        """Extract a forwarded bearer token and use it as the Anthropic key."""
        forwarded_auth = request.metadata.get("upstream_authorization")
        if not isinstance(forwarded_auth, str) or not forwarded_auth:
            return ""
        if forwarded_auth.lower().startswith("bearer "):
            return forwarded_auth[7:].strip()
        return forwarded_auth.strip()

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> AdapterError:
        """Map HTTP status codes into structured adapter errors."""
        status_code = exc.response.status_code
        message = exc.response.text or "Anthropic API returned an error"
        if status_code in (401, 403):
            return AdapterAuthError(message, adapter=self.name)
        if status_code == 429:
            return AdapterRateLimitError(message, adapter=self.name)
        return AdapterError(message, adapter=self.name)
