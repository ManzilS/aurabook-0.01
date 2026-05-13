"""Shared fixtures for AuraBook Cloud Brain tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from api.main import CloudBrainServices
from config import Settings
from models.schema import IngestResponse, NoteRecord, NoteSearchResult, PrefetchMatch, PrefetchResponse, StoredTrajectory, TrajectoryTurn
from services.idempotency import MemoryIdempotencyStore


class FakeGraphStore:
    """Simple fake graph store for API and worker tests."""

    def __init__(self) -> None:
        self.ingested_payloads = []
        self.prefetch_match: PrefetchMatch | None = None
        self.unprocessed: list[StoredTrajectory] = []
        self.recent_trajectories: list[StoredTrajectory] = []
        self.processed_ids: list[str] = []
        self.error_marks: list[tuple[str, str]] = []
        self.deleted_count = 0

    async def insert_trajectory(self, payload):
        self.ingested_payloads.append(payload)
        return IngestResponse(
            trajectory_id=payload.trajectory_id,
            created=True,
            stored_nodes=5 if payload.ai_reflection else 4,
            stored_edges=4 if payload.ai_reflection else 3,
            queued_for_notes=True,
        )

    async def get_prefetch_match(self, cdc_hash: str):
        return self.prefetch_match

    async def list_unprocessed(self, *, limit: int = 10):
        return self.unprocessed[:limit]

    async def list_recent_trajectories(self, *, limit: int = 50):
        return self.recent_trajectories[:limit]

    async def mark_processed(self, trajectory_id: str, *, note_model: str) -> None:
        self.processed_ids.append(trajectory_id)

    async def mark_processing_error(self, trajectory_id: str, *, error_message: str) -> None:
        self.error_marks.append((trajectory_id, error_message))

    async def delete_older_than(self, *, retention_days: int) -> int:
        self.deleted_count = retention_days
        return retention_days


class FakeVectorStore:
    """Simple fake vector store."""

    def __init__(self) -> None:
        self.search_results: list[NoteSearchResult] = []
        self.recent_notes: list[NoteRecord] = []
        self.upserted_notes = []
        self.recorded_matches: list[str] = []
        self.feedback_payloads = []

    async def search(
        self,
        query_embedding,
        *,
        query_text: str = "",
        settings=None,
        limit: int = 10,
        match_threshold: float = 0.75,
    ):
        return self.search_results[:limit]

    async def upsert_embeddings(self, notes):
        self.upserted_notes.extend(notes)
        return len(notes)

    async def record_matches(self, note_ids: list[str]) -> None:
        self.recorded_matches.extend(note_ids)

    async def submit_feedback(self, payload) -> None:
        self.feedback_payloads.append(payload)

    async def count(self) -> int:
        return len(self.recent_notes)

    async def list_recent_notes(self, *, limit: int = 50):
        return self.recent_notes[:limit]


class FakeEmbeddingService:
    """Fake embedding service that records input text."""

    def __init__(self, embedding: list[float] | None = None) -> None:
        self.embedding = embedding or [0.1, 0.2, 0.3]
        self.requests: list[str] = []

    async def embed_text(self, text: str) -> list[float]:
        self.requests.append(text)
        return self.embedding

    async def close(self) -> None:
        return None


class FakeSummarizer:
    """Fake summarizer for janitor tests."""

    def __init__(self, note_text: str = "System Note: Prefer safe updates.") -> None:
        self.note_text = note_text
        self.trajectories = []
        self.raise_error: Exception | None = None

    async def summarize(self, trajectory):
        self.trajectories.append(trajectory)
        if self.raise_error is not None:
            raise self.raise_error
        return self.note_text


@pytest.fixture
def settings() -> Settings:
    """Return deterministic settings for tests."""
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        openai_api_key="test-openai-key",
        cloud_api_base_url="http://test",
        api_shared_secret="",
    )


@pytest.fixture
def services(settings: Settings) -> CloudBrainServices:
    """Return a fake service container for API tests."""
    return CloudBrainServices(
        settings=settings,
        graph_store=FakeGraphStore(),
        vector_store=FakeVectorStore(),
        embedding_service=FakeEmbeddingService(),
        idempotency_store=MemoryIdempotencyStore(),
    )


@pytest.fixture
def stored_trajectory() -> StoredTrajectory:
    """Return a representative stored trajectory for worker tests."""
    return StoredTrajectory(
        id="traj-123",
        source="local_client",
        client_id="client-123",
        cdc_hash="abc123hash",
        trajectory_fingerprint="fp-123",
        original_goal="Fix async HTTP client usage.",
        original_prompt="Why is this async request hanging?",
        broken_code="response = requests.get(url)",
        error_log="RuntimeError: Cannot call blocking request inside async function",
        ai_reflection="The failure came from using a blocking HTTP client inside async code.",
        final_fix="Use httpx.AsyncClient inside an async context manager.",
        history=[
            TrajectoryTurn(
                prompt="Attempt 1",
                response="Summary: Replace requests with httpx.",
                model="gpt-5.4-mini",
                thought="The error means a blocking client is running inside async code.",
                tried="Swapped requests for httpx.AsyncClient and reran tests.",
                outcome="success",
                commands=["python -m pytest -q"],
                changed_files=["app.py"],
                token_count=480,
                duration_ms=1900,
            )
        ],
        metadata={"attempted_models": ["gpt-5.4-mini"], "attempt_count": 1},
        processed=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
