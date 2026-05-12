"""Tests for persisted runtime status tracking."""

from __future__ import annotations

from pathlib import Path

from aurabook.services.status import RuntimeStatusStore


def test_status_store_persists_runtime_and_upload_state(tmp_path: Path) -> None:
    store = RuntimeStatusStore(tmp_path / "status.json")
    store.initialize(
        client_id="client-123",
        cloud_enabled=True,
        cloud_demo_mode=False,
        max_gradient="full",
    )
    store.record_queue_depth(2)
    store.record_upload(
        success=False,
        queued=True,
        trajectory_id=None,
        error_message="network down",
    )

    snapshot = store.snapshot()

    assert snapshot.client_id == "client-123"
    assert snapshot.queue_depth == 2
    assert snapshot.last_upload is not None
    assert snapshot.last_upload.queued is True
    assert snapshot.last_upload.error_message == "network down"
