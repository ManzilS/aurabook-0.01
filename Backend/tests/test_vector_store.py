"""Tests for Supabase vector-store behavior."""

from __future__ import annotations

import pytest

from db.vector import SupabaseVectorStore


class FakeResponse:
    """Minimal Supabase-style response wrapper."""

    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Minimal query builder used by the vector-store tests."""

    def __init__(self, rows):
        self._rows = rows
        self._in_filter: tuple[str, set[str]] | None = None

    def select(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def in_(self, column: str, values: list[str]):
        self._in_filter = (column, set(values))
        return self

    async def execute(self):
        rows = self._rows
        if self._in_filter is not None:
            column, values = self._in_filter
            rows = [row for row in rows if row[column] in values]
        return FakeResponse(rows)


class FakeRPCQuery:
    """Minimal RPC builder that returns a fixed payload."""

    def __init__(self, payload):
        self._payload = payload

    async def execute(self):
        return FakeResponse(self._payload)


class FakeClient:
    """Minimal Supabase client surface used by the vector-store tests."""

    def __init__(self) -> None:
        self.last_rpc_args = None
        self.tables = {
            "trajectory_notes": [
                {
                    "id": "note-1",
                    "trajectory_id": "traj-1",
                    "note_text": "System Note: Use httpx.AsyncClient in async code.",
                    "source_model": "gpt-5.4-mini",
                    "embedding": "[1.0, 0.0]",
                    "metadata": {},
                    "created_at": "2026-04-22T00:00:00+00:00",
                    "version": 1,
                    "helpful_count": 0,
                    "unhelpful_count": 0,
                    "suppress_count": 0,
                    "match_count": 0,
                }
            ],
            "trajectories": [
                {
                    "id": "traj-1",
                    "original_prompt": "My async request blocks the event loop.",
                    "final_fix": "Use httpx.AsyncClient.",
                    "ai_reflection": "Blocking network calls stall the loop.",
                }
            ],
        }

    def rpc(self, *_args, **_kwargs):
        self.last_rpc_args = (_args, _kwargs)
        return FakeRPCQuery([])

    def table(self, name: str):
        return FakeQuery(self.tables[name])


class FakeProvider:
    """Minimal provider returning the fake client."""

    def __init__(self) -> None:
        self.client = FakeClient()

    async def get_client(self):
        return self.client


@pytest.mark.asyncio
async def test_search_falls_back_to_client_side_similarity() -> None:
    provider = FakeProvider()
    store = SupabaseVectorStore(provider)

    results = await store.search(
        [1.0, 0.0],
        query_text="My async request blocks the event loop.",
        settings=None,
        limit=5,
        match_threshold=0.0,
    )

    assert len(results) == 1
    assert results[0].note_id == "note-1"
    assert results[0].similarity == pytest.approx(1.0)
    assert results[0].original_prompt == "My async request blocks the event loop."
    assert provider.client.last_rpc_args is not None
    assert provider.client.last_rpc_args[0][1]["query_embedding"] == "[1.0,0.0]"


@pytest.mark.asyncio
async def test_list_recent_notes_parses_string_embeddings() -> None:
    provider = FakeProvider()
    store = SupabaseVectorStore(provider)

    notes = await store.list_recent_notes(limit=5)

    assert len(notes) == 1
    assert notes[0].embedding == [1.0, 0.0]
