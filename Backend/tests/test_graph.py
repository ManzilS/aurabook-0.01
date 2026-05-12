"""Tests for the Supabase graph store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from postgrest.exceptions import APIError

from db.graph import SupabaseGraphStore
from models.schema import StoredTrajectory, TrajectoryIngestRequest


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name: str, existing_row: dict[str, object]) -> None:
        self._name = name
        self._existing_row = existing_row
        self._mode = ""
        self._filters: dict[str, object] = {}
        self.updated_payload: dict[str, object] | None = None

    def select(self, *_columns: str):
        self._mode = "select"
        return self

    def eq(self, key: str, value: object):
        self._filters[key] = value
        return self

    def maybe_single(self):
        return self

    def insert(self, _payload):
        self._mode = "insert"
        return self

    def update(self, payload):
        self._mode = "update"
        self.updated_payload = payload
        return self

    async def execute(self):
        if self._name == "trajectories" and self._mode == "insert":
            raise APIError(
                {
                    "message": 'duplicate key value violates unique constraint "trajectories_pkey"',
                    "code": "23505",
                    "hint": None,
                    "details": 'Key (id)=(traj-123) already exists.',
                }
            )

        if self._name == "trajectories" and self._mode == "select":
            if self._filters.get("trajectory_fingerprint") == "fp-miss":
                return _FakeResponse(None)
            if self._filters.get("id") == "traj-123":
                return _FakeResponse(self._existing_row)
        if self._name == "trajectories" and self._mode == "update":
            assert self.updated_payload is not None
            self._existing_row.update(self.updated_payload)
            return _FakeResponse([self._existing_row])

        return _FakeResponse(None)


class _FakeClient:
    def __init__(self, existing_row: dict[str, object]) -> None:
        self._existing_row = existing_row
        self._tables: dict[str, _FakeTable] = {}

    def table(self, name: str) -> _FakeTable:
        if name not in self._tables:
            self._tables[name] = _FakeTable(name, self._existing_row)
        return self._tables[name]


class _FakeProvider:
    def __init__(self, existing_row: dict[str, object]) -> None:
        self._client = _FakeClient(existing_row)

    async def get_client(self):
        return self._client


@pytest.mark.asyncio
async def test_insert_trajectory_treats_duplicate_primary_key_as_idempotent_replay(monkeypatch) -> None:
    existing = StoredTrajectory(
        id="traj-123",
        source="swarm",
        client_id="repo-build-swarm",
        cdc_hash=None,
        trajectory_fingerprint="fp-existing",
        original_goal="Repair the project.",
        original_prompt="Build the repo.",
        broken_code="print(missing_name)",
        error_log="NameError: missing_name is not defined",
        ai_reflection="The build failed because the variable is undefined.",
        final_fix="UNRESOLVED after 3 attempts.",
        history=[
            {
                "prompt": "Attempt 1",
                "response": "Summary: define the missing variable.",
                "model": "gpt-5.4-mini",
                "thought": "The symbol is undefined.",
                "tried": "Added the missing symbol.",
                "outcome": "failure: NameError",
            }
        ],
        metadata={"resolved": False, "attempted_models": ["gpt-5.4-mini"], "attempt_count": 1},
        processed=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    ).model_dump(by_alias=True, mode="json")
    store = SupabaseGraphStore(_FakeProvider(existing))
    monkeypatch.setattr(
        "db.graph.trajectory_fingerprint",
        lambda **_kwargs: "fp-miss",
    )

    response = await store.insert_trajectory(
        TrajectoryIngestRequest(
            trajectory_id="traj-123",
            source="swarm",
            original_prompt="Build the repo.",
            error_log="NameError: missing_name is not defined",
            final_fix="UNRESOLVED after 3 attempts.",
            history=[
                {
                    "prompt": "Attempt 1",
                    "response": "Summary: define the missing variable.",
                    "model": "gpt-5.4-mini",
                    "thought": "The symbol is undefined.",
                    "tried": "Added the missing symbol.",
                    "outcome": "failure: NameError",
                },
                {
                    "prompt": "Attempt 2",
                    "response": "Summary: inline the literal value.",
                    "model": "gpt-5.4-nano",
                    "thought": "The second fix removes the undefined symbol entirely.",
                    "tried": "Rewrote the expression to use a literal value.",
                    "outcome": "success",
                },
            ],
            metadata={"resolved": True, "attempted_models": ["gpt-5.4-mini", "gpt-5.4-nano"]},
        )
    )

    assert response.trajectory_id == "traj-123"
    assert response.created is False
    assert response.stored_nodes == 0
    assert response.stored_edges == 0
    assert response.queued_for_notes is True
    stored = await store.get_trajectory_by_id("traj-123")
    assert stored is not None
    assert len(stored.history) == 2
    assert stored.metadata["attempt_count"] == 2
    assert stored.metadata["attempted_models"] == ["gpt-5.4-mini", "gpt-5.4-nano"]
    assert stored.metadata["observation_count"] == 2
