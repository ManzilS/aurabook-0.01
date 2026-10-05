"""Endpoint tests for the AuraBook Cloud Brain API."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import build_services, create_app
from config import Settings
from models.schema import NoteRecord, NoteSearchResult, PrefetchMatch, StoredTrajectory, TrajectoryTurn


@pytest.mark.asyncio
async def test_ingest_endpoint_stores_trajectory(services) -> None:
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ingest",
            json={
                "source": "swarm",
                "original_goal": "Enable CORS",
                "original_prompt": "Fix this FastAPI app",
                "broken_code": "app.add_middleware(CORSMiddleware)",
                "error_log": "NameError: CORSMiddleware is not defined",
                "ai_reflection": "The import is missing, so middleware registration fails immediately.",
                "final_fix": "from fastapi.middleware.cors import CORSMiddleware",
            },
        )

    assert response.status_code == 200
    assert response.json()["stored_nodes"] == 5
    assert services.graph_store.ingested_payloads[0].source == "swarm"


@pytest.mark.asyncio
async def test_ingest_endpoint_sanitizes_absolute_paths(services) -> None:
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ingest",
            json={
                "source": "swarm",
                "original_prompt": r"Fix E:\Users\manzi\project\repo\app.py",
                "error_log": r'Traceback (most recent call last): File "E:\Users\manzi\AppData\Local\Temp\tmp123\repo\tests\test_sample.py", line 7',
                "final_fix": r"Use C:\Users\manzi\repo\app.py instead.",
                "metadata": {"repository": r"E:\Users\manzi\project\repo"},
            },
        )

    assert response.status_code == 200
    stored = services.graph_store.ingested_payloads[0]
    assert "<path>/" in stored.original_prompt
    assert "<repo>/" in stored.error_log
    assert "Users\\manzi" not in stored.error_log
    assert stored.metadata["repository"] == "<repo>"


@pytest.mark.asyncio
async def test_prefetch_endpoint_returns_exact_match(services) -> None:
    services.graph_store.prefetch_match = PrefetchMatch(
        trajectory_id="traj-1",
        cdc_hash="hash-123",
        original_goal="Fix pandas assignment",
        final_fix="Use .loc to assign into the filtered frame.",
        note_text="System Note: Use .loc for pandas slice assignment.",
        source_model="gpt-5.4-mini",
        ai_reflection="The original write targeted a view instead of the owning frame.",
    )
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/prefetch", json={"cdc_hash": "hash-123"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["match"]["trajectory_id"] == "traj-1"


@pytest.mark.asyncio
async def test_search_endpoint_embeds_query_and_returns_matches(services) -> None:
    services.vector_store.search_results = [
        NoteSearchResult(
            note_id="note-1",
            trajectory_id="traj-1",
            note_text="System Note: Do not use requests in async code; use httpx.AsyncClient instead.",
            source_model="gpt-5.4-mini",
            similarity=0.91,
            metadata={},
            created_at="2026-04-22T00:00:00+00:00",
            original_prompt="My async request is blocking the event loop",
            final_fix="Use httpx.AsyncClient inside an async function.",
            ai_reflection="Blocking network calls stall the event loop and hide backpressure bugs.",
        )
    ]
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/search",
            json={
                "prompt": "My async request is blocking the event loop",
                "error_log": "RuntimeError: blocking call detected",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["matches"]) == 1
    assert services.embedding_service.requests
    assert "blocking call detected" in services.embedding_service.requests[0]
    assert services.vector_store.recorded_matches == ["note-1"]


@pytest.mark.asyncio
async def test_search_endpoint_validates_query(services) -> None:
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/search", json={"limit": 3})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_demo_compare_endpoint_uses_matching_note(monkeypatch, services) -> None:
    services.vector_store.search_results = [
        NoteSearchResult(
            note_id="note-1",
            trajectory_id="traj-1",
            note_text="System Note: Prefer httpx.AsyncClient in async code.",
            source_model="gpt-5.4-mini",
            similarity=0.91,
            metadata={},
            created_at="2026-04-22T00:00:00+00:00",
            original_prompt="My async request is blocking the event loop",
            final_fix="Use httpx.AsyncClient inside an async function.",
            ai_reflection="Blocking network calls stall the event loop.",
        )
    ]
    captured: dict[str, str | None] = {}

    async def fake_run_live_compare(*, prompt, api_key, injected_note, model_name, provider=None):
        captured.update(
            {
                "prompt": prompt,
                "api_key": api_key,
                "injected_note": injected_note,
                "model_name": model_name,
            }
        )
        return SimpleNamespace(
            injected_note=injected_note,
            left_events=[
                SimpleNamespace(
                    at=100,
                    kind="success",
                    text="Baseline live response",
                    token_count=10,
                    status_label="Complete",
                )
            ],
            right_events=[
                SimpleNamespace(
                    at=100,
                    kind="success",
                    text="AuraBook live response",
                    token_count=12,
                    status_label="Complete",
                )
            ],
            baseline_tokens=10,
            aurabook_tokens=12,
            total_tokens=22,
            provider_used="openai",
            model_used="gpt-4o-mini",
            provider_call_count=2,
        )

    async def fake_embed_live_demo_prompt(prompt, api_key):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr("api.main._embed_live_demo_prompt", fake_embed_live_demo_prompt)
    monkeypatch.setattr("api.main.run_live_compare", fake_run_live_compare)
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo/compare",
            json={"prompt": "Fix my async HTTP call", "api_key": "sk-test", "model": "gpt-test"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert captured == {
        "prompt": "Fix my async HTTP call",
        "api_key": "sk-test",
        "injected_note": "System Note: Prefer httpx.AsyncClient in async code.",
        "model_name": "gpt-test",
    }
    assert payload["injected_note"] == "System Note: Prefer httpx.AsyncClient in async code."
    assert payload["left_events"][0]["text"] == "Baseline live response"
    assert payload["right_events"][0]["text"] == "AuraBook live response"
    assert payload["baseline_tokens"] == 10
    assert payload["aurabook_tokens"] == 12
    assert payload["total_tokens"] == 22
    assert payload["retrieval_provider_call_count"] == 1
    assert payload["provider_call_count"] == 3


@pytest.mark.asyncio
async def test_demo_models_endpoint_validates_key(monkeypatch, services) -> None:
    async def fake_list_models_for_key(api_key, *, provider=None):
        assert api_key == "sk-test"
        assert provider is None
        return SimpleNamespace(
            provider="openai",
            valid=True,
            models=["gpt-test", "gpt-other"],
            default_model="gpt-test",
            cache_status="live",
            error=None,
        )

    monkeypatch.setattr("api.main.list_models_for_key", fake_list_models_for_key)
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/demo/models", json={"api_key": "sk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["models"] == ["gpt-test", "gpt-other"]
    assert payload["default_model"] == "gpt-test"


@pytest.mark.asyncio
async def test_dashboard_snapshot_endpoint_returns_live_rows(services) -> None:
    services.graph_store.recent_trajectories = [
        StoredTrajectory(
            id="traj-1",
            source="local_client",
            client_id="client-1",
            cdc_hash="hash-1",
            trajectory_fingerprint="fp-1",
            original_goal="Fix a blocking async HTTP call.",
            original_prompt="My async request hangs when I use requests.",
            broken_code="requests.get(url)",
            error_log="BlockingIOError: event loop blocked by synchronous HTTP client",
            ai_reflection="The code used a blocking HTTP client in an async function.",
            final_fix="Use httpx.AsyncClient inside the coroutine.",
            history=[
                TrajectoryTurn(
                    prompt="Attempt 1",
                    response="Summary: Replace requests with httpx.",
                    model="gpt-5.4-mini",
                    thought="The issue is a blocking HTTP client inside async code.",
                    tried="Swapped the client implementation.",
                    outcome="success",
                    commands=["python -m pytest -q"],
                    changed_files=["app.py"],
                    token_count=620,
                    duration_ms=2100,
                    timestamp="2026-04-24T10:03:00+00:00",
                )
            ],
            metadata={"os": "Windows 11", "language_version": "Python 3.13"},
            processed=True,
            created_at="2026-04-24T10:00:00+00:00",
            updated_at="2026-04-24T10:05:00+00:00",
            processed_at="2026-04-24T10:05:00+00:00",
        ),
        StoredTrajectory(
            id="traj-2",
            source="swarm",
            client_id="client-2",
            cdc_hash="hash-2",
            trajectory_fingerprint="fp-1",
            original_goal="Fix a blocking async HTTP call.",
            original_prompt="The same async request still hangs.",
            broken_code="requests.get(url)",
            error_log="RuntimeError: blocking request inside async function",
            ai_reflection="The model kept trying sync HTTP clients.",
            final_fix="UNRESOLVED after 2 attempts.",
            history=[
                TrajectoryTurn(
                    prompt="Attempt 1",
                    response="Summary: Tried another blocking client.",
                    model="codex-fix-alpha",
                    thought="The error looked like a timeout bug.",
                    tried="Retried with a different sync client.",
                    outcome="failure: RuntimeError",
                    commands=["python -m pytest -q"],
                    changed_files=["worker.py"],
                    token_count=410,
                    duration_ms=1800,
                    timestamp="2026-04-24T10:02:00+00:00",
                )
            ],
            metadata={"resolved": False},
            processed=False,
            note_error="still unresolved",
            created_at="2026-04-24T10:02:00+00:00",
            updated_at="2026-04-24T10:04:00+00:00",
        )
    ]
    services.vector_store.recent_notes = [
        NoteRecord(
            id="note-1",
            trajectory_id="traj-1",
            note_text="System Note: Use httpx.AsyncClient instead of requests in async code.",
            source_model="gpt-5.4-mini",
            embedding=[0.1, 0.2, 0.3],
            helpful_count=2,
            match_count=4,
            metadata={},
            created_at="2026-04-24T10:05:00+00:00",
        )
    ]
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/dashboard/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["total_notes"] == 1
    assert payload["notes"][0]["problem_tag"] == "Async HTTP client blocking"
    assert payload["notes"][0]["solution_text"] == "Use httpx.AsyncClient inside the coroutine."
    assert payload["notes"][0]["attempted_models"] == ["gpt-5.4-mini", "codex-fix-alpha"]
    assert payload["notes"][0]["unsolved_attempt_count"] == 1
    assert payload["notes"][0]["failed_model_count"] == 1
    assert payload["notes"][0]["attempt_count"] == 2
    assert payload["notes"][0]["attempts"][0]["model"] == "codex-fix-alpha"
    assert payload["notes"][0]["diagnosis_summary"] == "The issue is a blocking HTTP client inside async code."
    assert payload["notes"][0]["full_diagnosis"].startswith("The code used a blocking HTTP client in an async function.")
    assert payload["notes"][0]["error_cluster_id"] == "cluster-fp-1"
    assert len(payload["error_clusters"]) == 1
    cluster = payload["error_clusters"][0]
    assert cluster["id"] == "cluster-fp-1"
    assert cluster["root_error_detail"] == "BlockingIOError: event loop blocked by synchronous HTTP client"
    assert cluster["failure_count"] == 1
    assert cluster["cascade_error_count"] == 1
    assert cluster["attempted_models"] == ["gpt-5.4-mini", "codex-fix-alpha"]
    assert cluster["steps"][0]["kind"] == "root_error"
    assert cluster["steps"][1]["kind"] == "attempt"
    assert cluster["steps"][2]["kind"] == "derived_error"


def test_build_services_uses_demo_mode_without_live_credentials(tmp_path) -> None:
    services = build_services(
        Settings(
            demo_mode=True,
            demo_store_path=str(tmp_path / "cloud.json"),
        )
    )

    assert services.supabase_provider is None


@pytest.mark.asyncio
async def test_feedback_endpoint_records_feedback(services) -> None:
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/feedback",
            json={
                "note_id": "note-1",
                "trajectory_id": "traj-1",
                "client_id": "client-123",
                "feedback": "wrong",
                "repository": "E:/repo",
            },
        )

    assert response.status_code == 200
    assert services.vector_store.feedback_payloads[0].note_id == "note-1"
    assert response.json()["feedback"] == "wrong"


@pytest.mark.asyncio
async def test_idempotent_ingest_reuses_cached_response(services) -> None:
    app = create_app(settings=services.settings, services=services)
    body = {
        "source": "swarm",
        "original_goal": "Enable CORS",
        "original_prompt": "Fix this FastAPI app",
        "broken_code": "app.add_middleware(CORSMiddleware)",
        "error_log": "NameError: CORSMiddleware is not defined",
        "final_fix": "from fastapi.middleware.cors import CORSMiddleware",
    }
    headers = {"X-AuraBook-Idempotency-Key": "ingest-123"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/ingest", json=body, headers=headers)
        second = await client.post("/v1/ingest", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(services.graph_store.ingested_payloads) == 1


@pytest.mark.asyncio
async def test_admin_prune_endpoint_uses_graph_retention(services) -> None:
    app = create_app(settings=services.settings, services=services)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/admin/prune", json={})

    assert response.status_code == 200
    assert response.json()["deleted"] == services.settings.retention_days


@pytest.mark.asyncio
async def test_signed_requests_are_required_when_secret_is_configured(tmp_path) -> None:
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        openai_api_key="test-openai-key",
        api_shared_secret="top-secret",
        demo_mode=True,
        demo_store_path=str(tmp_path / "cloud.json"),
    )
    services = build_services(settings)
    app = create_app(settings=settings, services=services)
    body = {
        "source": "swarm",
        "original_prompt": "Fix async client",
        "error_log": "RuntimeError",
        "final_fix": "Use httpx.AsyncClient",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unsigned_response = await client.post("/v1/ingest", json=body)
        signed_response = await client.post(
            "/v1/ingest",
            content=json.dumps(body, separators=(",", ":"), sort_keys=True),
            headers=_signed_headers(settings.api_shared_secret, body),
        )

    assert unsigned_response.status_code == 401
    assert signed_response.status_code == 200


def _signed_headers(secret: str, payload: dict[str, object]) -> dict[str, str]:
    timestamp = str(int(time.time()))
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-AuraBook-Timestamp": timestamp,
        "X-AuraBook-Signature": signature,
    }


@pytest.mark.asyncio
async def test_stale_signed_requests_are_rejected(tmp_path) -> None:
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        openai_api_key="test-openai-key",
        api_shared_secret="top-secret",
        api_signature_max_age_s=1,
        demo_mode=True,
        demo_store_path=str(tmp_path / "cloud.json"),
    )
    services = build_services(settings)
    app = create_app(settings=settings, services=services)
    body = {
        "source": "swarm",
        "original_prompt": "Fix async client",
        "error_log": "RuntimeError",
        "final_fix": "Use httpx.AsyncClient",
    }
    timestamp = str(int(time.time()) - 60)
    payload_body = json.dumps(body, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        settings.api_shared_secret.encode("utf-8"),
        f"{timestamp}.{payload_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/ingest",
            content=payload_body,
            headers={
                "Content-Type": "application/json",
                "X-AuraBook-Timestamp": timestamp,
                "X-AuraBook-Signature": signature,
            },
        )

    assert response.status_code == 401
