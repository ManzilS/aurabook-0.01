"""Integration tests for the AuraBook Cloud Brain API.

These tests exercise full request flows through the app — auth, RBAC,
error handling, rate limiting, and the ingest→search pipeline — using
the ASGI test client against a real app instance with fake stores.
"""

from __future__ import annotations

import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import CloudBrainServices, create_app
from config import Settings
from models.schema import NoteSearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    defaults = dict(
        demo_mode=True,
        demo_store_path=".demo/test_integration.json",
        api_shared_secret="",
        session_secret="test-session-secret-32chars-long!",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_app(services=None, **settings_overrides):
    settings = _make_settings(**settings_overrides)
    return create_app(settings=settings, services=services)


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_404_returns_structured_json(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.get("/v1/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
        assert body["status_code"] == 404

    @pytest.mark.asyncio
    async def test_422_returns_field_level_errors(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.post("/v1/search", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "Validation error"
        assert "details" in body
        assert len(body["details"]) > 0

    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_500(self, monkeypatch):
        app = _make_app()

        async def _explode(*args, **kwargs):
            raise RuntimeError("Unexpected boom")

        monkeypatch.setattr(
            "api.main.DashboardService.build_snapshot", _explode
        )
        async with await _client(app) as client:
            resp = await client.get("/v1/dashboard/snapshot")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "Internal server error"
        assert "boom" not in json.dumps(body).lower()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:

    @pytest.mark.asyncio
    async def test_rate_limit_returns_429(self):
        app = _make_app(rate_limit_rpm=1.0, rate_limit_burst=1.0)
        async with await _client(app) as client:
            resp1 = await client.post("/v1/search", json={"prompt": "test", "error_log": "e"})
            resp2 = await client.post("/v1/search", json={"prompt": "test", "error_log": "e"})

        assert resp1.status_code == 200
        assert resp2.status_code == 429
        assert "Retry-After" in resp2.headers

    @pytest.mark.asyncio
    async def test_rate_limit_keyed_by_client_id(self):
        app = _make_app(rate_limit_rpm=1.0, rate_limit_burst=1.0)
        async with await _client(app) as client:
            resp1 = await client.post(
                "/v1/search",
                json={"prompt": "test", "error_log": "e"},
                headers={"X-AuraBook-Client-ID": "client-a"},
            )
            resp2 = await client.post(
                "/v1/search",
                json={"prompt": "test", "error_log": "e"},
                headers={"X-AuraBook-Client-ID": "client-b"},
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Authentication flow
# ---------------------------------------------------------------------------

class TestAuthentication:

    @pytest.mark.asyncio
    async def test_api_key_required_rejects_unauthenticated(self):
        app = _make_app(require_api_key=True)
        async with await _client(app) as client:
            resp = await client.post(
                "/v1/search",
                json={"prompt": "test", "error_log": "error"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_hmac_auth_accepts_valid_signature(self):
        app = _make_app(api_shared_secret="test-secret")
        body = {
            "source": "swarm",
            "original_prompt": "Fix bug",
            "error_log": "Error",
            "final_fix": "Fixed",
        }
        import hashlib, hmac as hmac_mod
        timestamp = str(int(time.time()))
        body_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
        sig = hmac_mod.new(
            b"test-secret",
            f"{timestamp}.{body_str}".encode(),
            hashlib.sha256,
        ).hexdigest()

        async with await _client(app) as client:
            resp = await client.post(
                "/v1/ingest",
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "X-AuraBook-Timestamp": timestamp,
                    "X-AuraBook-Signature": sig,
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_oauth_me_rejects_without_session(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.get("/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_oauth_github_redirect_requires_config(self):
        app = _make_app(github_client_id="", github_client_secret="")
        async with await _client(app) as client:
            resp = await client.get("/auth/github", follow_redirects=False)
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_oauth_github_redirects_when_configured(self):
        app = _make_app(
            github_client_id="test-id",
            github_client_secret="test-secret",
            github_redirect_uri="http://localhost:8000/auth/github/callback",
        )
        async with await _client(app) as client:
            resp = await client.get("/auth/github", follow_redirects=False)
        assert resp.status_code == 307
        assert "github.com/login/oauth" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_oauth_callback_rejects_bad_state(self):
        app = _make_app(
            github_client_id="test-id",
            github_client_secret="test-secret",
        )
        async with await _client(app) as client:
            resp = await client.get(
                "/auth/github/callback?code=abc&state=bad",
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged_out"


# ---------------------------------------------------------------------------
# RBAC on org endpoints
# ---------------------------------------------------------------------------

class TestRBAC:

    @pytest.mark.asyncio
    async def test_create_org_requires_auth(self):
        """Org creation should fail without any auth context when required."""
        app = _make_app(require_api_key=True)
        async with await _client(app) as client:
            resp = await client.post(
                "/v1/orgs",
                json={"name": "Test Org", "slug": "test-org"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_org_requires_auth(self):
        """Org endpoints require authentication — unauthenticated requests get 401."""
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.get("/v1/orgs/nonexistent")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_org_slug_validation(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.post(
                "/v1/orgs",
                json={"name": "Bad Slug", "slug": "UPPER-CASE"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ingest → Search pipeline
# ---------------------------------------------------------------------------

class TestIngestSearchPipeline:

    @pytest.mark.asyncio
    async def test_ingest_then_search_roundtrip(self, services):
        """Ingest a trajectory and verify search works against it."""
        services.vector_store.search_results = [
            NoteSearchResult(
                note_id="note-1",
                trajectory_id="traj-1",
                note_text="System Note: Use httpx in async code.",
                source_model="gpt-5.4-mini",
                similarity=0.85,
                metadata={},
                created_at="2026-05-15T00:00:00+00:00",
                original_prompt="Async HTTP blocking",
                final_fix="Use httpx.AsyncClient",
                ai_reflection="Blocking HTTP client in async context.",
            )
        ]
        app = create_app(settings=services.settings, services=services)

        async with await _client(app) as client:
            ingest_resp = await client.post(
                "/v1/ingest",
                json={
                    "source": "swarm",
                    "original_goal": "Fix async HTTP",
                    "original_prompt": "Async request is blocking",
                    "broken_code": "requests.get(url)",
                    "error_log": "RuntimeError: blocking call",
                    "ai_reflection": "Used blocking HTTP in async.",
                    "final_fix": "Use httpx.AsyncClient",
                },
            )
            assert ingest_resp.status_code == 200
            assert ingest_resp.json()["created"] is True

            search_resp = await client.post(
                "/v1/search",
                json={
                    "prompt": "Async HTTP call is blocking",
                    "error_log": "RuntimeError",
                },
            )
            assert search_resp.status_code == 200
            matches = search_resp.json()["matches"]
            assert len(matches) == 1
            assert "httpx" in matches[0]["note_text"]

    @pytest.mark.asyncio
    async def test_feedback_after_search(self, services):
        """Submit feedback on a search result."""
        app = create_app(settings=services.settings, services=services)

        async with await _client(app) as client:
            resp = await client.post(
                "/v1/feedback",
                json={
                    "note_id": "note-1",
                    "trajectory_id": "traj-1",
                    "client_id": "client-1",
                    "feedback": "helpful",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True
        assert resp.json()["feedback"] == "helpful"

    @pytest.mark.asyncio
    async def test_prefetch_miss_then_hit(self, services):
        """Prefetch returns miss, then we set a match and it returns hit."""
        from models.schema import PrefetchMatch
        app = create_app(settings=services.settings, services=services)

        async with await _client(app) as client:
            miss = await client.post("/v1/prefetch", json={"cdc_hash": "unknown-hash-value"})
            assert miss.status_code == 200
            assert miss.json()["found"] is False

            services.graph_store.prefetch_match = PrefetchMatch(
                trajectory_id="traj-1",
                cdc_hash="known-hash",
                original_goal="Fix bug",
                final_fix="Fixed it",
                note_text="A note",
                source_model="gpt-5.4-mini",
            )

            hit = await client.post("/v1/prefetch", json={"cdc_hash": "known-hash"})
            assert hit.status_code == 200
            assert hit.json()["found"] is True


# ---------------------------------------------------------------------------
# Health & observability
# ---------------------------------------------------------------------------

class TestObservability:

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_endpoint(self):
        app = _make_app()
        async with await _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 200
        assert "checks" in resp.json()

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        app = _make_app()
        async with await _client(app) as client:
            await client.get("/health")
            resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "aurabook_requests_total" in resp.text
