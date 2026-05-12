"""Integration tests for the FastAPI server."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from aurabook.core.models import CommunityPrefetchMatch, ResponseGradient
from aurabook.core.runtime import build_runtime
from aurabook.server.main import create_app
from aurabook.utils.config import Settings
from tests.conftest import FakeAdapter, StubWatcher


@pytest.mark.asyncio
async def test_app_lifespan_starts_and_stops_watcher(settings) -> None:
    watcher = StubWatcher()
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=watcher)
    app = create_app(runtime)

    async with app.router.lifespan_context(app):
        assert watcher.started is True

    assert watcher.stopped is True


@pytest.mark.asyncio
async def test_health_endpoint_returns_runtime_details(settings) -> None:
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["default_adapter"] == "openai_proxy"
    assert response.json()["runtime_status"]["client_id"]


@pytest.mark.asyncio
async def test_chat_completions_sanitizes_before_dispatch(settings) -> None:
    adapter = FakeAdapter(reply="proxy ok")
    runtime = build_runtime(settings=settings, adapter=adapter, watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-test",
                "messages": [
                    {
                        "role": "user",
                        "content": "Email me at dev@example.com",
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert adapter.last_request is not None
    assert adapter.last_request.messages[0].text_content() == "Email me at [REDACTED_EMAIL]"
    assert response.json()["choices"][0]["message"]["content"] == "proxy ok"
    assert response.headers["X-AuraBook-Gradient"] == "pass_through"
    assert response.headers["X-AuraBook-PII-Redactions"] == "1"


@pytest.mark.asyncio
async def test_chat_completions_routes_to_named_provider_adapter(settings) -> None:
    default_adapter = FakeAdapter(reply="default")
    claude_adapter = FakeAdapter(reply="claude ok")
    runtime = build_runtime(settings=settings, adapter=default_adapter, watcher=StubWatcher())
    runtime.pipeline.adapters["claude"] = claude_adapter
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "claude/claude-sonnet-test",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "claude ok"
    assert claude_adapter.last_request is not None
    assert claude_adapter.last_request.model == "claude-sonnet-test"
    assert claude_adapter.last_request.target_adapter == "claude"


@pytest.mark.asyncio
async def test_auth_middleware_blocks_invalid_keys(tmp_path) -> None:
    settings = Settings(
        api_key="local-secret",
        upstream_base_url="https://example.test/v1",
        upstream_api_key="upstream-secret",
        watch_root=str(tmp_path),
    )
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_body_limit_middleware_rejects_large_requests(settings) -> None:
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Content-Length": "99999999"},
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_invalid_json_returns_400(settings) -> None:
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_streaming_chat_completions_returns_sse(settings) -> None:
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-test",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert response.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_exact_match_can_intercept_without_upstream_call(settings) -> None:
    adapter = FakeAdapter(reply="should not run")
    runtime = build_runtime(settings=settings, adapter=adapter, watcher=StubWatcher())
    runtime.memory_store.store_prefetch_result(
        path=settings.resolve_watch_root() / "app.py",
        cdc_hash="hash123",
        match=CommunityPrefetchMatch(
            trajectory_id="traj-1",
            cdc_hash="hash123",
            final_fix="print('exact community fix')",
            note_text="System Note: exact fix available.",
        ),
    )
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "fix this failure"}]},
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "print('exact community fix')"
    assert adapter.last_request is None
    assert response.headers["X-AuraBook-Gradient"] == "full"
    assert response.headers["X-AuraBook-Cache"] == "hit"
    assert response.headers["X-AuraBook-Trajectory"] == "traj-1"


@pytest.mark.asyncio
async def test_dashboard_and_interventions_endpoints_render_runtime_state(settings) -> None:
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    runtime.community_service.record_intervention(
        gradient="notes",
        source="search_remote",
        note_id="note-1",
        trajectory_id="traj-1",
    )
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dashboard_response = await client.get("/dashboard")
        interventions_response = await client.get("/v1/interventions")

    assert dashboard_response.status_code == 200
    assert "AuraBook Dashboard" in dashboard_response.text
    assert interventions_response.status_code == 200
    assert interventions_response.json()["interventions"][0]["note_id"] == "note-1"


@pytest.mark.asyncio
async def test_feedback_endpoint_suppresses_note_in_active_runtime(tmp_path) -> None:
    settings = Settings(
        watch_root=str(tmp_path),
        state_dir=str(tmp_path / ".aurabook"),
        demo_mode=True,
        cloud_enabled=True,
        cloud_demo_mode=True,
        max_gradient="notes",
    )
    runtime = build_runtime(settings=settings, adapter=FakeAdapter(), watcher=StubWatcher())
    app = create_app(runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        feedback_response = await client.post(
            "/v1/feedback",
            json={
                "note_id": "note-demo-async-httpx",
                "trajectory_id": "demo-async-httpx",
                "client_id": runtime.identity_store.get_client_id(),
                "feedback": "wrong",
                "repository": str(settings.resolve_watch_root()),
            },
        )

    policy = runtime.preferences_store.get_repo_policy(settings.resolve_watch_root())
    resolved = await runtime.community_service.resolve_context("My async request is blocking the event loop")

    assert feedback_response.status_code == 200
    assert policy is not None
    assert "note-demo-async-httpx" in policy.suppressed_note_ids
    assert resolved.gradient == ResponseGradient.PASS_THROUGH
