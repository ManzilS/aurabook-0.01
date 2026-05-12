"""Abstract base class for upstream LLM adapters."""

from __future__ import annotations

import abc
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse


class AdapterBase(abc.ABC):
    """Contract implemented by every upstream adapter."""

    name: str = "base"
    supports_streaming: bool = False

    @abc.abstractmethod
    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Convert a universal request into an upstream payload."""

    @abc.abstractmethod
    def translate_to_universal(self, raw: dict[str, Any]) -> ChatCompletionResponse:
        """Convert an upstream payload into a universal response."""

    @abc.abstractmethod
    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a non-streaming request upstream."""

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield SSE-formatted chunks for a streaming response."""
        response = await self.send(request)
        payload = {
            "id": response.id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": response.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": response.text,
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
