"""Tests for community context middleware and services."""

from __future__ import annotations

import pytest

from aurabook.core.models import (
    ChatCompletionRequest,
    CommunityPrefetchMatch,
    CommunitySearchResponse,
    CommunitySearchResult,
    Message,
    ResponseGradient,
    Role,
)
from aurabook.core.state import PipelineState
from aurabook.middleware.community import CommunityContextMiddleware
from aurabook.services.community import CommunityContextService
from aurabook.services.memory import CommunityMemoryStore
from aurabook.utils.config import Settings


class FakeCloudClient:
    """Fake cloud client for community context tests."""

    def __init__(self) -> None:
        self.search_response = CommunitySearchResponse()
        self.search_requests: list[dict[str, object]] = []
        self.prefetch_requests: list[str] = []

    async def prefetch(self, cdc_hash: str):
        self.prefetch_requests.append(cdc_hash)
        return {"found": False, "match": None}

    async def search(self, *, prompt: str, error_log: str | None = None, limit: int = 5, match_threshold=None):
        self.search_requests.append(
            {
                "prompt": prompt,
                "error_log": error_log,
                "limit": limit,
            }
        )
        return self.search_response


def make_state(prompt: str) -> PipelineState:
    return PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content=prompt)]),
    )


@pytest.mark.asyncio
async def test_community_middleware_early_exits_on_exact_match(tmp_path) -> None:
    settings = Settings(
        watch_root=str(tmp_path),
        state_dir=str(tmp_path / ".aurabook"),
        cloud_enabled=False,
        max_gradient="full",
    )
    memory = CommunityMemoryStore()
    memory.store_prefetch_result(
        path=tmp_path / "app.py",
        cdc_hash="hash123",
        match=CommunityPrefetchMatch(
            trajectory_id="traj-1",
            cdc_hash="hash123",
            final_fix="print('cached fix')",
            note_text="System Note: reuse the cached fix.",
        ),
    )
    service = CommunityContextService(settings, cloud_client=None, memory_store=memory)
    middleware = CommunityContextMiddleware(service)

    state = await middleware.process(make_state("please fix this"))

    assert state.early_exit is True
    assert state.response is not None
    assert state.response.text == "print('cached fix')"
    assert state.extras["community_gradient"] == ResponseGradient.FULL.value


@pytest.mark.asyncio
async def test_community_middleware_injects_notes_for_semantic_match(tmp_path) -> None:
    settings = Settings(
        watch_root=str(tmp_path),
        state_dir=str(tmp_path / ".aurabook"),
        max_gradient="notes",
    )
    cloud_client = FakeCloudClient()
    cloud_client.search_response = CommunitySearchResponse(
        matches=[
            CommunitySearchResult(
                note_id="note-1",
                trajectory_id="traj-1",
                note_text="System Note: Do not use requests for async work.",
                source_model="gpt-5.4-mini",
                similarity=0.82,
                final_fix="Use httpx.AsyncClient.",
            )
        ]
    )
    service = CommunityContextService(
        settings,
        cloud_client=cloud_client,
        memory_store=CommunityMemoryStore(),
    )
    middleware = CommunityContextMiddleware(service)

    state = await middleware.process(make_state("My async request is blocking"))

    assert state.early_exit is False
    assert state.request.messages[0].role == Role.SYSTEM
    assert "Do not use requests for async work" in state.request.messages[0].text_content()
    assert state.extras["community_gradient"] == ResponseGradient.NOTES.value
    assert cloud_client.search_requests


@pytest.mark.asyncio
async def test_community_service_suppresses_note_immediately(tmp_path) -> None:
    settings = Settings(
        watch_root=str(tmp_path),
        state_dir=str(tmp_path / ".aurabook"),
        max_gradient="notes",
    )
    cloud_client = FakeCloudClient()
    cloud_client.search_response = CommunitySearchResponse(
        matches=[
            CommunitySearchResult(
                note_id="note-1",
                trajectory_id="traj-1",
                note_text="System Note: Avoid requests in async code.",
                source_model="gpt-5.4-mini",
                similarity=0.9,
            )
        ]
    )
    service = CommunityContextService(
        settings,
        cloud_client=cloud_client,
        memory_store=CommunityMemoryStore(),
    )

    first = await service.resolve_context("async request blocks")
    service.suppress_note("note-1")
    second = await service.resolve_context("async request blocks")

    assert first.gradient == ResponseGradient.NOTES
    assert second.gradient == ResponseGradient.PASS_THROUGH
