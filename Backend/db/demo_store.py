"""JSON-backed demo stores for the AuraBook Cloud Brain."""

from __future__ import annotations

import json
import math
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from models.schema import (
    FeedbackRequest,
    IngestResponse,
    NoteRecord,
    NoteSearchResult,
    PrefetchMatch,
    StoredTrajectory,
    TrajectoryIngestRequest,
)
from services.fingerprints import normalize_text, trajectory_fingerprint
from services.retrieval import rerank_results


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _seed_trajectories() -> list[StoredTrajectory]:
    return [
        StoredTrajectory(
            id="demo-async-httpx",
            source="swarm",
            client_id="demo-client",
            cdc_hash="cdca413a92b68195",
            original_goal="Make async HTTP requests without blocking the event loop.",
            original_prompt="My async request is blocking the event loop.",
            broken_code="response = requests.get(url)",
            error_log="RuntimeError: blocking call detected inside async function",
            ai_reflection=(
                "The failure came from using a blocking HTTP client inside async code, "
                "which stalled the event loop."
            ),
            final_fix="Use httpx.AsyncClient inside an async function.",
            history=[],
            metadata={"demo": True},
            processed=True,
            note_model="aurabook-demo",
            processed_at=_utcnow(),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        ),
        StoredTrajectory(
            id="demo-fastapi-cors",
            source="swarm",
            client_id="demo-client",
            cdc_hash="c2cb43f5baaf1f8a",
            original_goal="Enable localhost CORS in a FastAPI app.",
            original_prompt="Fix this FastAPI CORS middleware setup.",
            broken_code="app.add_middleware(CORSMiddleware)",
            error_log="NameError: name 'CORSMiddleware' is not defined",
            ai_reflection="The middleware registration failed because CORSMiddleware was never imported.",
            final_fix="Import CORSMiddleware from fastapi.middleware.cors before adding the middleware.",
            history=[],
            metadata={"demo": True},
            processed=True,
            note_model="aurabook-demo",
            processed_at=_utcnow(),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        ),
    ]


def _seed_notes() -> list[NoteRecord]:
    return [
        NoteRecord(
            id="note-demo-async-httpx",
            trajectory_id="demo-async-httpx",
            note_text=(
                "System Note: Do not use the requests library inside async code; "
                "use httpx.AsyncClient so the event loop stays non-blocking."
            ),
            source_model="aurabook-demo",
            embedding=DeterministicEmbeddingService()._embed_for_store(
                "Do not use requests inside async code; use httpx.AsyncClient instead."
            ),
            metadata={"demo": True},
            helpful_count=4,
            match_count=10,
            created_at=_utcnow(),
        ),
        NoteRecord(
            id="note-demo-fastapi-cors",
            trajectory_id="demo-fastapi-cors",
            note_text=(
                "System Note: Import CORSMiddleware from fastapi.middleware.cors "
                "before registering it on the app."
            ),
            source_model="aurabook-demo",
            embedding=DeterministicEmbeddingService()._embed_for_store(
                "Import CORSMiddleware from fastapi.middleware.cors before adding middleware."
            ),
            metadata={"demo": True},
            helpful_count=3,
            match_count=6,
            created_at=_utcnow(),
        ),
    ]


class DemoGraphStore:
    """JSON-backed graph store used by the cloud API in demo mode."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._lock = Lock()
        self._ensure_store()

    async def insert_trajectory(self, payload: TrajectoryIngestRequest) -> IngestResponse:
        with self._lock:
            trajectories, notes = self._load_locked()
            payload_fingerprint = trajectory_fingerprint(
                original_prompt=payload.original_prompt,
                error_log=payload.error_log,
                final_fix=payload.final_fix,
            )
            for trajectory in trajectories:
                current_fingerprint = trajectory_fingerprint(
                    original_prompt=trajectory.original_prompt,
                    error_log=trajectory.error_log,
                    final_fix=trajectory.final_fix,
                )
                if payload.cdc_hash and payload.cdc_hash == trajectory.cdc_hash:
                    return self._existing_response(trajectory, notes)
                if payload_fingerprint == current_fingerprint:
                    return self._existing_response(trajectory, notes)

            stored = StoredTrajectory(
                id=payload.trajectory_id,
                source=payload.source,
                client_id=payload.client_id,
                cdc_hash=payload.cdc_hash,
                original_goal=payload.original_goal,
                original_prompt=payload.original_prompt,
                broken_code=payload.broken_code,
                error_log=payload.error_log,
                ai_reflection=payload.ai_reflection,
                final_fix=payload.final_fix,
                history=payload.history,
                metadata=payload.metadata,
                processed=False,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            trajectories.append(stored)
            self._write_locked(trajectories, notes)
            return IngestResponse(
                trajectory_id=stored.trajectory_id,
                created=True,
                stored_nodes=5 if stored.ai_reflection else 4,
                stored_edges=4 if stored.ai_reflection else 3,
                queued_for_notes=True,
            )

    async def get_prefetch_match(self, cdc_hash: str) -> PrefetchMatch | None:
        with self._lock:
            trajectories, notes = self._load_locked()
            for trajectory in trajectories:
                if trajectory.cdc_hash != cdc_hash:
                    continue
                note = next((candidate for candidate in notes if candidate.trajectory_id == trajectory.trajectory_id), None)
                return PrefetchMatch(
                    trajectory_id=trajectory.trajectory_id,
                    cdc_hash=cdc_hash,
                    note_id=None if note is None else note.note_id,
                    original_goal=trajectory.original_goal,
                    final_fix=trajectory.final_fix,
                    note_text=None if note is None else note.note_text,
                    source_model=None if note is None else note.source_model,
                    ai_reflection=trajectory.ai_reflection,
                )
        return None

    async def list_unprocessed(self, *, limit: int = 10) -> list[StoredTrajectory]:
        with self._lock:
            trajectories, _ = self._load_locked()
            pending = [trajectory for trajectory in trajectories if not trajectory.processed]
            pending.sort(key=lambda item: item.created_at)
            return pending[:limit]

    async def list_recent_trajectories(self, *, limit: int = 50) -> list[StoredTrajectory]:
        """Return recent demo trajectories for dashboard rendering."""
        with self._lock:
            trajectories, _ = self._load_locked()
            ordered = sorted(trajectories, key=lambda item: item.created_at, reverse=True)
            return ordered[:limit]

    async def mark_processed(self, trajectory_id: str, *, note_model: str) -> None:
        with self._lock:
            trajectories, notes = self._load_locked()
            for trajectory in trajectories:
                if trajectory.trajectory_id != trajectory_id:
                    continue
                trajectory.processed = True
                trajectory.note_model = note_model
                trajectory.note_error = None
                trajectory.processed_at = _utcnow()
                trajectory.updated_at = _utcnow()
                break
            self._write_locked(trajectories, notes)

    async def mark_processing_error(self, trajectory_id: str, *, error_message: str) -> None:
        with self._lock:
            trajectories, notes = self._load_locked()
            for trajectory in trajectories:
                if trajectory.trajectory_id != trajectory_id:
                    continue
                trajectory.note_error = error_message[:500]
                trajectory.updated_at = _utcnow()
                break
            self._write_locked(trajectories, notes)

    def _ensure_store(self) -> None:
        with self._lock:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            if self._store_path.exists():
                return
            self._write_locked(_seed_trajectories(), _seed_notes())

    def _load_locked(self) -> tuple[list[StoredTrajectory], list[NoteRecord]]:
        raw = json.loads(self._store_path.read_text(encoding="utf-8"))
        trajectories = [StoredTrajectory.model_validate(item) for item in raw.get("trajectories", [])]
        notes = [NoteRecord.model_validate(item) for item in raw.get("notes", [])]
        return trajectories, notes

    def _write_locked(self, trajectories: list[StoredTrajectory], notes: list[NoteRecord]) -> None:
        payload = {
            "trajectories": [item.model_dump(by_alias=True, mode="json") for item in trajectories],
            "notes": [item.model_dump(by_alias=True, mode="json") for item in notes],
        }
        self._store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _existing_response(
        self,
        trajectory: StoredTrajectory,
        notes: list[NoteRecord],
    ) -> IngestResponse:
        note = next((candidate for candidate in notes if candidate.trajectory_id == trajectory.trajectory_id), None)
        return IngestResponse(
            trajectory_id=trajectory.trajectory_id,
            created=False,
            stored_nodes=0,
            stored_edges=0,
            queued_for_notes=note is None,
        )

    async def delete_older_than(self, *, retention_days: int) -> int:
        """Delete processed demo trajectories older than the retention threshold."""
        cutoff = _utcnow().timestamp() - retention_days * 86400
        with self._lock:
            trajectories, notes = self._load_locked()
            kept_trajectories = [
                trajectory
                for trajectory in trajectories
                if not (
                    trajectory.processed
                    and trajectory.created_at.timestamp() < cutoff
                )
            ]
            kept_ids = {trajectory.trajectory_id for trajectory in kept_trajectories}
            kept_notes = [note for note in notes if note.trajectory_id in kept_ids]
            deleted = len(trajectories) - len(kept_trajectories)
            if deleted:
                self._write_locked(kept_trajectories, kept_notes)
            return deleted


class DemoVectorStore:
    """JSON-backed vector store used in demo mode."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._lock = Lock()
        self._graph_store = DemoGraphStore(store_path)

    async def upsert_embeddings(self, notes: list[NoteRecord]) -> int:
        if not notes:
            return 0
        with self._lock:
            trajectories, existing_notes = self._graph_store._load_locked()
            by_trajectory = {note.trajectory_id: note for note in existing_notes}
            for note in notes:
                existing = by_trajectory.get(note.trajectory_id)
                if existing is None:
                    by_trajectory[note.trajectory_id] = note
                    continue
                by_trajectory[note.trajectory_id] = note.model_copy(
                    update={
                        "note_id": existing.note_id,
                        "version": existing.version + 1,
                        "helpful_count": existing.helpful_count,
                        "unhelpful_count": existing.unhelpful_count,
                        "suppress_count": existing.suppress_count,
                        "match_count": existing.match_count,
                        "last_matched_at": existing.last_matched_at,
                        "created_at": existing.created_at,
                    }
                )
            self._graph_store._write_locked(trajectories, list(by_trajectory.values()))
        return len(notes)

    async def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        settings: Any | None = None,
        limit: int = 10,
        match_threshold: float = 0.30,
    ) -> list[NoteSearchResult]:
        with self._lock:
            trajectories, notes = self._graph_store._load_locked()
            trajectory_map = {trajectory.trajectory_id: trajectory for trajectory in trajectories}
            scored: list[tuple[float, NoteRecord]] = []
            for note in notes:
                similarity = cosine_similarity(query_embedding, note.embedding)
                if similarity >= match_threshold:
                    scored.append((similarity, note))
            scored.sort(key=lambda item: item[0], reverse=True)

        results: list[NoteSearchResult] = []
        for similarity, note in scored[:limit]:
            trajectory = trajectory_map[note.trajectory_id]
            results.append(
                NoteSearchResult(
                    note_id=note.note_id,
                    trajectory_id=note.trajectory_id,
                    note_text=note.note_text,
                    source_model=note.source_model,
                    similarity=round(similarity, 4),
                    metadata={**note.metadata, "match_count": note.match_count},
                    created_at=note.created_at,
                    original_prompt=trajectory.original_prompt,
                    final_fix=trajectory.final_fix,
                    ai_reflection=trajectory.ai_reflection,
                    version=note.version,
                    helpful_count=note.helpful_count,
                    unhelpful_count=note.unhelpful_count,
                    suppress_count=note.suppress_count,
                )
            )
        if settings is not None and query_text.strip():
            results = rerank_results(results, query_text=query_text, settings=settings)
        return results

    async def record_matches(self, note_ids: list[str]) -> None:
        """Increment match counters for returned notes."""
        if not note_ids:
            return
        with self._lock:
            trajectories, notes = self._graph_store._load_locked()
            for note in notes:
                if note.note_id in note_ids:
                    note.match_count += 1
                    note.last_matched_at = _utcnow()
            self._graph_store._write_locked(trajectories, notes)

    async def submit_feedback(self, payload: FeedbackRequest) -> None:
        """Apply one feedback event to the referenced note."""
        if payload.note_id is None:
            return
        with self._lock:
            trajectories, notes = self._graph_store._load_locked()
            for note in notes:
                if note.note_id != payload.note_id:
                    continue
                if payload.feedback == "helpful":
                    note.helpful_count += 1
                elif payload.feedback == "wrong":
                    note.unhelpful_count += 1
                else:
                    note.suppress_count += 1
                break
            self._graph_store._write_locked(trajectories, notes)

    async def count(self) -> int:
        with self._lock:
            _, notes = self._graph_store._load_locked()
            return len(notes)

    async def list_recent_notes(self, *, limit: int = 50) -> list[NoteRecord]:
        """Return recent demo notes for dashboard rendering."""
        with self._lock:
            _, notes = self._graph_store._load_locked()
            ordered = sorted(notes, key=lambda item: item.created_at, reverse=True)
            return ordered[:limit]


class DeterministicEmbeddingService:
    """Stable, dependency-free embedding approximation used in demo mode.

    Uses 256 dimensions with both word-level and character 4-gram hashing so
    that morphologically related terms (e.g. "asyncio" / "async", "sqlalchemy"
    / "sql", "setInterval" / "interval") share enough signal to produce
    cosine-similarity scores in a useful range without a real embedding model.
    """

    _NGRAM_SIZE = 4
    _DIMENSIONS = 256
    # Three independent hash offsets spread the signal across dimensions.
    _HASH_SALTS = (b"a1", b"b2", b"c3")

    def __init__(self, dimensions: int = _DIMENSIONS) -> None:
        self._dimensions = dimensions

    async def embed_text(self, text: str) -> list[float]:
        return self._embed_for_store(text)

    async def close(self) -> None:
        return None

    def _embed_for_store(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        tokens = normalize_text(text).split()

        for token in tokens:
            encoded = token.encode("utf-8")
            # Word-level signal (three hash salts → three buckets per token)
            for salt in self._HASH_SALTS:
                digest = hashlib.sha256(salt + encoded).hexdigest()
                idx = int(digest[:8], 16) % self._dimensions
                vector[idx] += 1.0

            # Character n-gram signal captures morphological overlap
            for start in range(len(token) - self._NGRAM_SIZE + 1):
                gram = token[start : start + self._NGRAM_SIZE].encode("utf-8")
                digest = hashlib.sha256(gram).hexdigest()
                idx = int(digest[:8], 16) % self._dimensions
                vector[idx] += 0.5

        return vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity between two vectors."""
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
