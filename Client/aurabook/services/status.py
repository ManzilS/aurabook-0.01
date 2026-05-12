"""Persistent runtime status tracking for the AuraBook local client."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


class CacheMetricsSnapshot(BaseModel):
    """Summary counters for in-memory cache behavior."""

    prefetch_entries: int = 0
    search_entries: int = 0
    prefetch_evictions: int = 0
    search_evictions: int = 0
    exact_match_hits: int = 0
    search_cache_hits: int = 0
    search_cache_misses: int = 0

    model_config = ConfigDict(extra="forbid")


class PrefetchStatusEntry(BaseModel):
    """One recent Ghost Pre-Fetch observation."""

    path: str
    cdc_hash: str
    exact_match: bool
    observed_at: str = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class UploadStatus(BaseModel):
    """Last trajectory upload outcome."""

    success: bool
    queued: bool
    trajectory_id: str | None = None
    error_message: str | None = None
    updated_at: str = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class SearchStatus(BaseModel):
    """Last semantic lookup outcome."""

    source: str = "none"
    gradient: str = "pass_through"
    similarity: float | None = None
    updated_at: str = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class InterventionStatusEntry(BaseModel):
    """One recent intervention decision made by AuraBook."""

    gradient: str
    source: str
    note_id: str | None = None
    trajectory_id: str | None = None
    repository: str | None = None
    feedback: str | None = None
    created_at: str = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class RuntimeStatusSnapshot(BaseModel):
    """Snapshot rendered by the local status CLI and health surfaces."""

    client_id: str = ""
    cloud_enabled: bool = False
    cloud_demo_mode: bool = False
    max_gradient: str = "full"
    queue_depth: int = 0
    recent_hashes: list[PrefetchStatusEntry] = Field(default_factory=list)
    recent_interventions: list[InterventionStatusEntry] = Field(default_factory=list)
    cache_metrics: CacheMetricsSnapshot = Field(default_factory=CacheMetricsSnapshot)
    last_upload: UploadStatus | None = None
    last_search: SearchStatus = Field(default_factory=SearchStatus)

    model_config = ConfigDict(extra="forbid")


class RuntimeStatusStore:
    """Persist small runtime health snapshots to disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def initialize(
        self,
        *,
        client_id: str,
        cloud_enabled: bool,
        cloud_demo_mode: bool,
        max_gradient: str,
    ) -> None:
        """Seed the persisted runtime status with stable configuration."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.client_id = client_id
            snapshot.cloud_enabled = cloud_enabled
            snapshot.cloud_demo_mode = cloud_demo_mode
            snapshot.max_gradient = max_gradient
            self._write_locked(snapshot)

    def snapshot(self) -> RuntimeStatusSnapshot:
        """Return the current persisted status snapshot."""
        with self._lock:
            return self._read_locked()

    def record_prefetch(self, *, path: Path, cdc_hash: str, exact_match: bool) -> None:
        """Persist one recent prefetch observation."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.recent_hashes.append(
                PrefetchStatusEntry(
                    path=str(path),
                    cdc_hash=cdc_hash,
                    exact_match=exact_match,
                )
            )
            snapshot.recent_hashes = snapshot.recent_hashes[-5:]
            self._write_locked(snapshot)

    def update_cache_metrics(self, metrics: dict[str, int]) -> None:
        """Persist the latest cache metrics."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.cache_metrics = CacheMetricsSnapshot.model_validate(metrics)
            self._write_locked(snapshot)

    def record_search(
        self,
        *,
        source: str,
        gradient: str,
        similarity: float | None,
    ) -> None:
        """Persist the latest semantic search result."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.last_search = SearchStatus(
                source=source,
                gradient=gradient,
                similarity=similarity,
            )
            self._write_locked(snapshot)

    def record_upload(
        self,
        *,
        success: bool,
        queued: bool,
        trajectory_id: str | None,
        error_message: str | None = None,
    ) -> None:
        """Persist the latest trajectory upload outcome."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.last_upload = UploadStatus(
                success=success,
                queued=queued,
                trajectory_id=trajectory_id,
                error_message=error_message,
            )
            self._write_locked(snapshot)

    def record_queue_depth(self, depth: int) -> None:
        """Persist the current failed-upload queue depth."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.queue_depth = depth
            self._write_locked(snapshot)

    def record_intervention(
        self,
        *,
        gradient: str,
        source: str,
        note_id: str | None = None,
        trajectory_id: str | None = None,
        repository: str | None = None,
        feedback: str | None = None,
    ) -> None:
        """Persist one recent intervention or feedback event."""
        with self._lock:
            snapshot = self._read_locked()
            snapshot.recent_interventions.append(
                InterventionStatusEntry(
                    gradient=gradient,
                    source=source,
                    note_id=note_id,
                    trajectory_id=trajectory_id,
                    repository=repository,
                    feedback=feedback,
                )
            )
            snapshot.recent_interventions = snapshot.recent_interventions[-10:]
            self._write_locked(snapshot)

    def _read_locked(self) -> RuntimeStatusSnapshot:
        if not self._path.exists():
            return RuntimeStatusSnapshot()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RuntimeStatusSnapshot()
        return RuntimeStatusSnapshot.model_validate(raw)

    def _write_locked(self, snapshot: RuntimeStatusSnapshot) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(snapshot.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
