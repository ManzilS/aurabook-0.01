"""Async Supabase client provider."""

from __future__ import annotations

import asyncio
from typing import Any

from config import Settings

try:
    from supabase import AsyncClient, acreate_client
except ImportError:  # pragma: no cover - exercised implicitly in local test env
    AsyncClient = Any  # type: ignore[assignment,misc]
    acreate_client = None  # type: ignore[assignment]


class SupabaseClientProvider:
    """Creates and memoizes the async Supabase client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get_client(self) -> AsyncClient:
        """Return a memoized async client."""
        if acreate_client is None:
            raise RuntimeError(
                "supabase-py is not installed. Install the project dependencies first."
            )

        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is None:
                self._client = await acreate_client(
                    self._settings.supabase_url,
                    self._settings.supabase_service_role_key,
                )
        return self._client

    async def close(self) -> None:
        """Close the underlying Supabase client when supported."""
        if self._client is None:
            return

        close = getattr(self._client, "aclose", None)
        if callable(close):
            await close()
        self._client = None
