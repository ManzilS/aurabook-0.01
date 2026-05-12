"""Supabase pgvector storage with a LanceEngine-like interface."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Any

from db.supabase_client import SupabaseClientProvider
from models.schema import FeedbackRequest, NoteRecord, NoteSearchResult
from services.retrieval import rerank_results

logger = logging.getLogger(__name__)


def _response_data(response):
    """Return Supabase response data, tolerating `maybe_single().execute()` -> None."""
    if response is None:
        return None
    return response.data


def _parse_embedding(raw_embedding: Any) -> list[float]:
    """Parse a stored embedding payload into floats."""
    if isinstance(raw_embedding, list):
        return [float(value) for value in raw_embedding]
    if isinstance(raw_embedding, str):
        return [float(value) for value in json.loads(raw_embedding)]
    raise TypeError(f"Unsupported embedding payload type: {type(raw_embedding)!r}")


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity between two same-length vectors."""
    if len(left) != len(right):
        raise ValueError("Embedding vectors must share the same dimensionality")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(left_value * right_value for left_value, right_value in zip(left, right))
    return dot / (left_norm * right_norm)


def _vector_literal(values: list[float]) -> str:
    """Return a pgvector-compatible literal string."""
    return json.dumps([float(value) for value in values], separators=(",", ":"))


class SupabaseVectorStore:
    """Async wrapper for storing and querying embedded notes."""

    def __init__(
        self,
        provider: SupabaseClientProvider,
        *,
        table_name: str = "trajectory_notes",
        match_rpc: str = "match_notes",
    ) -> None:
        self._provider = provider
        self._table_name = table_name
        self._match_rpc = match_rpc

    async def upsert_embeddings(self, notes: list[NoteRecord]) -> int:
        """Insert or update embedded notes."""
        if not notes:
            return 0

        client = await self._provider.get_client()
        payload: list[dict[str, Any]] = []
        for note in notes:
            existing = await client.table(self._table_name).select(
                "id, version, helpful_count, unhelpful_count, suppress_count, match_count"
            ).eq("trajectory_id", note.trajectory_id).maybe_single().execute()
            existing_note = _response_data(existing)
            payload.append(
                note.model_dump(
                    by_alias=True,
                    mode="json",
                )
                | {
                    "id": note.note_id if existing_note is None else existing_note["id"],
                    "version": 1 if existing_note is None else int(existing_note["version"]) + 1,
                    "helpful_count": 0 if existing_note is None else int(existing_note["helpful_count"]),
                    "unhelpful_count": 0 if existing_note is None else int(existing_note["unhelpful_count"]),
                    "suppress_count": 0 if existing_note is None else int(existing_note["suppress_count"]),
                    "match_count": 0 if existing_note is None else int(existing_note["match_count"]),
                    "last_matched_at": None,
                }
            )
        await client.table(self._table_name).upsert(
            payload,
            on_conflict="trajectory_id",
        ).execute()
        logger.info("Upserted %d note embedding(s)", len(payload))
        return len(payload)

    async def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        settings: Any | None = None,
        limit: int = 10,
        match_threshold: float = 0.75,
    ) -> list[NoteSearchResult]:
        """Return semantic note matches for the given embedding."""
        client = await self._provider.get_client()
        response = await client.rpc(
            self._match_rpc,
            {
                "query_embedding": _vector_literal(query_embedding),
                "match_threshold": match_threshold,
                "result_limit": limit,
            },
        ).execute()

        raw_results = _response_data(response) or []
        results = [
            NoteSearchResult(
                note_id=row["note_id"],
                trajectory_id=row["trajectory_id"],
                note_text=row["note_text"],
                source_model=row["source_model"],
                metadata=(row.get("metadata") or {}) | {"match_count": row.get("match_count", 0)},
                similarity=float(row["similarity"]),
                created_at=row["created_at"],
                original_prompt=row.get("original_prompt"),
                final_fix=row.get("final_fix"),
                ai_reflection=row.get("ai_reflection"),
                version=row.get("version"),
                helpful_count=row.get("helpful_count"),
                unhelpful_count=row.get("unhelpful_count"),
                suppress_count=row.get("suppress_count"),
            )
            for row in raw_results
        ]
        if not results:
            results = await self._fallback_search(
                client=client,
                query_embedding=query_embedding,
                limit=limit,
                match_threshold=match_threshold,
            )
        if settings is not None and query_text.strip():
            results = rerank_results(results, query_text=query_text, settings=settings)
        return results

    async def _fallback_search(
        self,
        *,
        client: Any,
        query_embedding: list[float],
        limit: int,
        match_threshold: float,
    ) -> list[NoteSearchResult]:
        """Search notes client-side when the RPC path returns no rows."""
        logger.warning("match_notes RPC returned no rows; falling back to client-side vector search")
        candidate_limit = max(limit * 10, 50)
        note_response = await client.table(self._table_name).select(
            "id, trajectory_id, note_text, source_model, embedding, metadata, created_at, "
            "version, helpful_count, unhelpful_count, suppress_count, match_count"
        ).order("created_at", desc=True).limit(candidate_limit).execute()
        note_rows = _response_data(note_response) or []
        if not note_rows:
            return []

        trajectory_ids = sorted({row["trajectory_id"] for row in note_rows})
        trajectory_map: dict[str, dict[str, Any]] = {}
        if trajectory_ids:
            trajectory_response = await client.table("trajectories").select(
                "id, original_prompt, final_fix, ai_reflection"
            ).in_("id", trajectory_ids).execute()
            trajectory_rows = _response_data(trajectory_response) or []
            trajectory_map = {row["id"]: row for row in trajectory_rows}

        results: list[NoteSearchResult] = []
        for row in note_rows:
            similarity = _cosine_similarity(query_embedding, _parse_embedding(row["embedding"]))
            if similarity < match_threshold:
                continue
            trajectory = trajectory_map.get(row["trajectory_id"], {})
            results.append(
                NoteSearchResult(
                    note_id=row["id"],
                    trajectory_id=row["trajectory_id"],
                    note_text=row["note_text"],
                    source_model=row["source_model"],
                    similarity=float(similarity),
                    metadata=(row.get("metadata") or {}) | {"match_count": row.get("match_count", 0)},
                    created_at=row["created_at"],
                    original_prompt=trajectory.get("original_prompt"),
                    final_fix=trajectory.get("final_fix"),
                    ai_reflection=trajectory.get("ai_reflection"),
                    version=row.get("version"),
                    helpful_count=row.get("helpful_count"),
                    unhelpful_count=row.get("unhelpful_count"),
                    suppress_count=row.get("suppress_count"),
                )
            )

        results.sort(key=lambda item: item.similarity, reverse=True)
        return results[:limit]

    async def record_matches(self, note_ids: list[str]) -> None:
        """Increment usage counters for returned note matches."""
        if not note_ids:
            return
        client = await self._provider.get_client()
        now = datetime.now(UTC).isoformat()
        for note_id in note_ids:
            response = await client.table(self._table_name).select("match_count").eq(
                "id",
                note_id,
            ).maybe_single().execute()
            row = _response_data(response)
            if row is None:
                continue
            await client.table(self._table_name).update(
                {
                    "match_count": int(row.get("match_count", 0)) + 1,
                    "last_matched_at": now,
                }
            ).eq("id", note_id).execute()

    async def submit_feedback(self, payload: FeedbackRequest) -> None:
        """Apply one user feedback event to a note."""
        if payload.note_id is None:
            return
        counter_field = {
            "helpful": "helpful_count",
            "wrong": "unhelpful_count",
            "too_aggressive": "suppress_count",
        }[payload.feedback]
        client = await self._provider.get_client()
        response = await client.table(self._table_name).select(
            counter_field
        ).eq("id", payload.note_id).maybe_single().execute()
        row = _response_data(response)
        if row is None:
            return
        await client.table(self._table_name).update(
            {counter_field: int(row.get(counter_field, 0)) + 1}
        ).eq("id", payload.note_id).execute()

    async def count(self) -> int:
        """Return the number of stored notes."""
        client = await self._provider.get_client()
        response = await client.table(self._table_name).select(
            "id",
            count="exact",
        ).limit(0).execute()
        return int(response.count or 0)

    async def list_recent_notes(self, *, limit: int = 50) -> list[NoteRecord]:
        """Return recent note rows for dashboard rendering."""
        client = await self._provider.get_client()
        response = await client.table(self._table_name).select(
            "id, trajectory_id, note_text, source_model, embedding, version, "
            "helpful_count, unhelpful_count, suppress_count, match_count, "
            "last_matched_at, metadata, created_at"
        ).order("created_at", desc=True).range(0, max(limit - 1, 0)).execute()
        rows = _response_data(response) or []
        normalized_rows = []
        for row in rows:
            payload = dict(row)
            payload["embedding"] = _parse_embedding(row["embedding"])
            normalized_rows.append(payload)
        return [NoteRecord.model_validate(row) for row in normalized_rows]
