"""Tests for trajectory capture middleware."""

from __future__ import annotations

import pytest

from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, Message, Role
from aurabook.core.state import PipelineState
from aurabook.middleware.trajectory import TrajectoryCaptureMiddleware
from aurabook.services.ingest_queue import FailedTrajectoryQueue
from aurabook.services.status import RuntimeStatusStore


class FakeCommunityService:
    def latest_cdc_hash(self) -> str:
        return "hash-123"


class FakeCloudClient:
    def __init__(self) -> None:
        self.payloads = []
        self.raise_error = False

    async def ingest(self, payload):
        if self.raise_error:
            raise RuntimeError("network error")
        self.payloads.append(payload)
        return {"trajectory_id": "traj-123"}


def make_state(prompt: str, response: str) -> PipelineState:
    return PipelineState(
        request=ChatCompletionRequest(
            messages=[Message(role=Role.USER, content=prompt)],
            model="gpt-local-test",
        ),
        response=ChatCompletionResponse(
            model="gpt-test",
            choices=[Choice(message=Message(role=Role.ASSISTANT, content=response))],
        ),
    )


@pytest.mark.asyncio
async def test_history_rolls_to_last_three_entries() -> None:
    middleware = TrajectoryCaptureMiddleware(emitter=lambda _: None, history_size=3)

    for index in range(4):
        await middleware.process(make_state(f"prompt {index}", f"response {index}"))

    history = middleware.history_snapshot()

    assert len(history) == 3
    assert history[0].prompt == "prompt 1"
    assert history[-1].prompt == "prompt 3"


@pytest.mark.asyncio
async def test_success_prompt_emits_mock_upload() -> None:
    emitted: list[str] = []
    middleware = TrajectoryCaptureMiddleware(emitter=emitted.append, history_size=3)

    await middleware.process(
        make_state(
            "Traceback: ValueError exploded",
            "Try this:\n```python\nprint('fixed')\n```",
        )
    )
    await middleware.process(make_state("I reran the test", "Looks cleaner now"))
    await middleware.process(make_state("That fixed it, thanks", "Glad it works"))

    assert emitted
    assert emitted[0].startswith("[Trajectory Capture] Success identified.")
    assert "Traceback" in emitted[0]
    assert '"final_fix"' in emitted[0]


@pytest.mark.asyncio
async def test_success_prompt_uploads_to_cloud_when_client_is_present() -> None:
    emitted: list[str] = []
    cloud_client = FakeCloudClient()
    middleware = TrajectoryCaptureMiddleware(
        emitter=emitted.append,
        history_size=3,
        cloud_client=cloud_client,
        community_service=FakeCommunityService(),
        client_id="client-123",
    )

    await middleware.process(
        make_state(
            "Traceback: RuntimeError exploded\n```python\nbroken()\n```",
            "I think the failure comes from a blocking call.\n```python\nfixed()\n```",
        )
    )
    await middleware.process(make_state("I reran the test", "Looks cleaner now"))
    await middleware.process(make_state("That fixed it", "Glad it works"))

    assert cloud_client.payloads
    payload = cloud_client.payloads[0]
    assert payload.client_id == "client-123"
    assert payload.cdc_hash == "hash-123"
    assert payload.error_log.startswith("Traceback")
    assert payload.final_fix == "fixed()"
    assert payload.history[0].model == "gpt-local-test"


@pytest.mark.asyncio
async def test_failed_upload_is_queued_and_recorded_in_status(tmp_path) -> None:
    emitted: list[str] = []
    cloud_client = FakeCloudClient()
    cloud_client.raise_error = True
    status_store = RuntimeStatusStore(tmp_path / "status.json")
    queue = FailedTrajectoryQueue(tmp_path / "queue", status_store=status_store)
    middleware = TrajectoryCaptureMiddleware(
        emitter=emitted.append,
        cloud_client=cloud_client,
        community_service=FakeCommunityService(),
        client_id="client-123",
        failed_upload_queue=queue,
        status_store=status_store,
    )

    await middleware.process(
        make_state(
            "Traceback: RuntimeError exploded\n```python\nbroken()\n```",
            "Use this fix.\n```python\nfixed()\n```",
        )
    )
    await middleware.process(make_state("That fixed it", "Glad it works"))

    snapshot = status_store.snapshot()
    assert queue.depth() == 1
    assert snapshot.last_upload is not None
    assert snapshot.last_upload.queued is True
    assert snapshot.last_upload.success is False
