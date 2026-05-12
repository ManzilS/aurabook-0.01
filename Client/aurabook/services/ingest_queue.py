"""Disk-backed retry queue for failed trajectory uploads."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from aurabook.core.models import TrajectoryUploadPayload
from aurabook.services.status import RuntimeStatusStore

logger = logging.getLogger(__name__)


class IngestClient(Protocol):
    """Protocol used by the retry worker."""

    async def ingest(self, payload: TrajectoryUploadPayload) -> dict[str, Any]:
        """Upload one payload."""


class QueuedUploadRecord(BaseModel):
    """One persisted failed upload entry."""

    queue_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    attempts: int = 0
    last_error: str | None = None
    payload: TrajectoryUploadPayload

    model_config = ConfigDict(extra="forbid")


class FailedTrajectoryQueue:
    """Stores failed trajectory uploads as individual JSON files."""

    def __init__(
        self,
        queue_dir: Path,
        *,
        status_store: RuntimeStatusStore | None = None,
    ) -> None:
        self._queue_dir = queue_dir
        self._status_store = status_store
        self._lock = Lock()

    def enqueue(self, payload: TrajectoryUploadPayload, *, error_message: str | None = None) -> str:
        """Persist one failed upload to disk."""
        record = QueuedUploadRecord(payload=payload, last_error=error_message)
        with self._lock:
            self._queue_dir.mkdir(parents=True, exist_ok=True)
            path = self._record_path(record.queue_id)
            path.write_text(
                json.dumps(record.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
            self._update_depth_locked()
        return record.queue_id

    def list_batch(self, limit: int) -> list[QueuedUploadRecord]:
        """Return the oldest queued uploads up to the requested limit."""
        with self._lock:
            paths = sorted(self._queue_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
            records: list[QueuedUploadRecord] = []
            for path in paths[:limit]:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    records.append(QueuedUploadRecord.model_validate(raw))
                except (OSError, ValueError) as exc:
                    logger.warning("Failed to read queued upload %s: %s", path, exc)
            return records

    def mark_done(self, queue_id: str) -> None:
        """Remove a successfully replayed upload."""
        with self._lock:
            path = self._record_path(queue_id)
            if path.exists():
                path.unlink()
            self._update_depth_locked()

    def mark_failed(self, queue_id: str, *, error_message: str) -> None:
        """Increment the failure count for a queued upload."""
        with self._lock:
            path = self._record_path(queue_id)
            if not path.exists():
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
            record = QueuedUploadRecord.model_validate(raw)
            record.attempts += 1
            record.last_error = error_message[:500]
            path.write_text(
                json.dumps(record.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
            self._update_depth_locked()

    def depth(self) -> int:
        """Return the current queued upload count."""
        with self._lock:
            if not self._queue_dir.exists():
                return 0
            return len(list(self._queue_dir.glob("*.json")))

    def _record_path(self, queue_id: str) -> Path:
        return self._queue_dir / f"{queue_id}.json"

    def _update_depth_locked(self) -> None:
        if self._status_store is not None:
            self._status_store.record_queue_depth(len(list(self._queue_dir.glob("*.json"))))


class QueuedUploadWorker:
    """Background worker that retries failed trajectory uploads."""

    def __init__(
        self,
        *,
        queue: FailedTrajectoryQueue,
        client: IngestClient | None,
        interval_s: float,
        batch_size: int,
        status_store: RuntimeStatusStore | None = None,
    ) -> None:
        self._queue = queue
        self._client = client
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._status_store = status_store
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the retry loop."""
        if self._client is None:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="aurabook-upload-worker")

    async def stop(self) -> None:
        """Stop the retry loop."""
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def flush_once(self) -> None:
        """Replay one queue batch immediately."""
        if self._client is None:
            return

        for record in self._queue.list_batch(self._batch_size):
            try:
                await self._client.ingest(record.payload)
            except Exception as exc:
                logger.warning(
                    "Queued trajectory upload %s failed again: %s",
                    record.queue_id,
                    exc,
                )
                self._queue.mark_failed(record.queue_id, error_message=str(exc))
                if self._status_store is not None:
                    self._status_store.record_upload(
                        success=False,
                        queued=True,
                        trajectory_id=record.payload.metadata.get("trajectory_id"),
                        error_message=str(exc),
                    )
                continue

            self._queue.mark_done(record.queue_id)
            if self._status_store is not None:
                self._status_store.record_upload(
                    success=True,
                    queued=False,
                    trajectory_id=record.payload.metadata.get("trajectory_id"),
                )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.flush_once()
            except Exception as exc:  # pragma: no cover - last-resort isolation for task loop
                logger.exception("Upload retry worker failed: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue
