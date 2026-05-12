"""Embedding service used by search, notes, and synthetic generation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from config import Settings
from services.model_providers import (
    ProviderRequestError,
    build_embedding_provider,
)

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Thin async wrapper around the configured embedding provider."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: Any | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retryable_exceptions: Sequence[type[Exception]] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or build_embedding_provider(settings)
        self._sleep = sleep_func
        self._retryable_exceptions = tuple(
            retryable_exceptions or (ProviderRequestError,)
        )

    async def embed_text(self, text: str) -> list[float]:
        """Embed one text input using the configured provider model."""
        for attempt in range(1, self._settings.openai_retry_attempts + 1):
            try:
                return await self._provider.embed_text(text)
            except self._retryable_exceptions as exc:
                retryable = getattr(exc, "retryable", True)
                halt = getattr(exc, "halt", False)
                if halt or not retryable:
                    raise
                if attempt >= self._settings.openai_retry_attempts:
                    logger.error("Embedding failed after %d attempts", attempt)
                    raise
                delay = self._settings.openai_retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Embedding attempt %d failed with %s; retrying in %.1fs",
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await self._sleep(delay)

        raise RuntimeError("Embedding retry loop exited unexpectedly")

    async def close(self) -> None:
        """Close the underlying provider when supported."""
        close = getattr(self._provider, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result
