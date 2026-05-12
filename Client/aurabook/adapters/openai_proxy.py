"""OpenAI-compatible upstream proxy adapter."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aurabook.adapters.base import AdapterBase
from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse
from aurabook.utils.config import Settings
from aurabook.utils.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterRateLimitError,
    AdapterTimeoutError,
)

logger = logging.getLogger(__name__)


class OpenAIProxyAdapter(AdapterBase):
    """Forwards requests to a named OpenAI-compatible endpoint."""

    name = "openai_proxy"
    supports_streaming = True

    def __init__(
        self,
        settings: Settings,
        *,
        adapter_name: str = "openai_proxy",
        base_url: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self.name = adapter_name
        self._base_url = (base_url or settings.provider_base_url(adapter_name)).rstrip("/")
        self._api_key = settings.provider_api_key(adapter_name) if api_key is None else api_key
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._owns_client = client is None

    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Convert the universal request into an OpenAI-compatible payload."""
        return request.model_dump(
            exclude={"id", "target_adapter", "metadata", "created_at"},
            exclude_none=True,
        )

    def translate_to_universal(self, raw: dict[str, Any]) -> ChatCompletionResponse:
        """Validate an upstream OpenAI-compatible response."""
        return ChatCompletionResponse.model_validate(raw)

    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a non-streaming request to the upstream API."""
        payload = self.translate_to_ai(request)
        headers = self._build_headers(request)

        try:
            response = await self._client.post(
                self._chat_completions_url(),
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                "Upstream request timed out",
                adapter=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._classify_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"Failed to contact upstream API: {exc}",
                adapter=self.name,
            ) from exc

        return self.translate_to_universal(response.json())

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Stream SSE chunks from the upstream API."""
        payload = self.translate_to_ai(request)
        headers = self._build_headers(request)

        try:
            async with self._client.stream(
                "POST",
                self._chat_completions_url(),
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        yield f"{line}\n\n"
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                "Upstream streaming request timed out",
                adapter=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._classify_http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"Failed to stream from upstream API: {exc}",
                adapter=self.name,
            ) from exc

    async def close(self) -> None:
        """Close the underlying HTTP client when the adapter owns it."""
        if self._owns_client:
            await self._client.aclose()

    def _build_headers(self, request: ChatCompletionRequest) -> dict[str, str]:
        """Build the outbound headers for the upstream request."""
        headers = {"Content-Type": "application/json"}
        authorization = self._api_key
        if authorization:
            headers["Authorization"] = f"Bearer {authorization}"
            return headers

        forwarded_auth = request.metadata.get("upstream_authorization")
        if isinstance(forwarded_auth, str) and forwarded_auth:
            headers["Authorization"] = forwarded_auth
        return headers

    def _chat_completions_url(self) -> str:
        """Return the configured upstream chat completions URL."""
        return f"{self._base_url}/chat/completions"

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> AdapterError:
        """Map HTTP status codes to structured adapter errors."""
        status_code = exc.response.status_code
        message = exc.response.text or "Upstream API returned an error"
        if status_code in (401, 403):
            return AdapterAuthError(message, adapter=self.name)
        if status_code == 429:
            return AdapterRateLimitError(message, adapter=self.name)
        return AdapterError(message, adapter=self.name)
