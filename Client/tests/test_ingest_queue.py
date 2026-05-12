"""Tests for failed trajectory upload persistence and replay."""

from __future__ import annotations

import pytest

from aurabook.core.models import TrajectoryUploadPayload
from aurabook.services.ingest_queue import FailedTrajectoryQueue, QueuedUploadWorker
from aurabook.services.status import RuntimeStatusStore


class FakeClient:
    """Small ingest client double for queue tests."""

    def __init__(self) -> None:
        self.payloads = []

    async def ingest(self, payload: TrajectoryUploadPayload):
        self.payloads.append(payload)
        return {"trajectory_id": "traj-123"}


def make_payload() -> TrajectoryUploadPayload:
    return TrajectoryUploadPayload(
        client_id="client-123",
        original_prompt="Fix this blocking sleep",
        error_log="RuntimeWarning: blocking sleep",
        final_fix="await asyncio.sleep(2)",
        history=[],
    )


@pytest.mark.asyncio
async def test_failed_upload_queue_round_trips_and_replays(tmp_path) -> None:
    status_store = RuntimeStatusStore(tmp_path / "status.json")
    queue = FailedTrajectoryQueue(tmp_path / "queue", status_store=status_store)
    client = FakeClient()
    worker = QueuedUploadWorker(
        queue=queue,
        client=client,
        interval_s=0.01,
        batch_size=10,
        status_store=status_store,
    )

    queue.enqueue(make_payload(), error_message="timeout")
    assert queue.depth() == 1

    await worker.flush_once()

    assert queue.depth() == 0
    assert len(client.payloads) == 1
    assert status_store.snapshot().queue_depth == 0
