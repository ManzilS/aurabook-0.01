"""Lightweight idempotency stores for retried Cloud Brain writes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PersistedIdempotencyEntry(BaseModel):
    """One persisted idempotent response payload."""

    key: str
    expires_at: float
    payload: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class PersistedIdempotencyState(BaseModel):
    """On-disk idempotency state."""

    entries: list[PersistedIdempotencyEntry] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MemoryIdempotencyStore:
    """In-memory idempotency store for one process lifetime."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        """Return a cached response payload when it is still valid."""
        now = time.time()
        with self._lock:
            cached = self._entries.get(key)
            if cached is None:
                return None
            expires_at, payload = cached
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return dict(payload)

    def put(self, key: str, payload: dict[str, Any], *, ttl_s: int) -> None:
        """Store a response payload for future retries."""
        with self._lock:
            self._entries[key] = (time.time() + ttl_s, dict(payload))


class FileBackedIdempotencyStore(MemoryIdempotencyStore):
    """JSON-backed idempotency store used in demo mode."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._load()

    def get(self, key: str) -> dict[str, Any] | None:
        payload = super().get(key)
        if payload is not None:
            self._persist()
        return payload

    def put(self, key: str, payload: dict[str, Any], *, ttl_s: int) -> None:
        super().put(key, payload, ttl_s=ttl_s)
        self._persist()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            state = PersistedIdempotencyState.model_validate(raw)
        except (OSError, ValueError):
            return
        now = time.time()
        for entry in state.entries:
            if entry.expires_at > now:
                self._entries[entry.key] = (entry.expires_at, entry.payload)

    def _persist(self) -> None:
        state = PersistedIdempotencyState(
            entries=[
                PersistedIdempotencyEntry(
                    key=key,
                    expires_at=expires_at,
                    payload=payload,
                )
                for key, (expires_at, payload) in self._entries.items()
                if expires_at > time.time()
            ]
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
