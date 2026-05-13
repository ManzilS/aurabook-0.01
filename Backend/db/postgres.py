"""Direct asyncpg-backed storage — no Supabase SDK required.

Connect to any Postgres instance with pgvector installed.  Works on
DigitalOcean Managed Databases, Railway, Neon, self-hosted, etc.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

from db.base import GraphStore, VectorStore
from models.schema import (
    FeedbackRequest,
    IngestResponse,
    NoteRecord,
    NoteSearchResult,
    PrefetchMatch,
    StoredTrajectory,
    TrajectoryIngestRequest,
)
from services.fingerprints import trajectory_fingerprint
from services.retrieval import rerank_results

logger = logging.getLogger(__name__)

SCHEMA_SQL = """\
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS trajectories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL DEFAULT 'local_client',
  client_id TEXT,
  cdc_hash TEXT UNIQUE,
  trajectory_fingerprint TEXT NOT NULL,
  original_goal TEXT,
  original_prompt TEXT NOT NULL,
  broken_code TEXT,
  error_log TEXT NOT NULL,
  ai_reflection TEXT,
  final_fix TEXT NOT NULL,
  history JSONB NOT NULL DEFAULT '[]'::JSONB,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  processed BOOLEAN NOT NULL DEFAULT FALSE,
  note_model TEXT,
  note_error TEXT,
  processed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS trajectory_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trajectory_id UUID NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
  node_type TEXT NOT NULL CHECK (node_type IN ('prompt','attempt','error','reflection','fix')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS trajectory_edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trajectory_id UUID NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
  source_node_id UUID NOT NULL REFERENCES trajectory_nodes(id) ON DELETE CASCADE,
  target_node_id UUID NOT NULL REFERENCES trajectory_nodes(id) ON DELETE CASCADE,
  edge_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS trajectory_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trajectory_id UUID NOT NULL UNIQUE REFERENCES trajectories(id) ON DELETE CASCADE,
  note_text TEXT NOT NULL,
  source_model TEXT NOT NULL,
  embedding VECTOR(1536) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  helpful_count INTEGER NOT NULL DEFAULT 0,
  unhelpful_count INTEGER NOT NULL DEFAULT 0,
  suppress_count INTEGER NOT NULL DEFAULT 0,
  match_count INTEGER NOT NULL DEFAULT 0,
  last_matched_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS trajectories_processed_created_idx
  ON trajectories (processed, created_at);
CREATE INDEX IF NOT EXISTS trajectories_cdc_hash_idx
  ON trajectories (cdc_hash);
CREATE INDEX IF NOT EXISTS trajectories_fingerprint_idx
  ON trajectories (trajectory_fingerprint);
CREATE INDEX IF NOT EXISTS trajectory_nodes_trajectory_idx
  ON trajectory_nodes (trajectory_id, node_type);
CREATE INDEX IF NOT EXISTS trajectory_edges_trajectory_idx
  ON trajectory_edges (trajectory_id);
CREATE INDEX IF NOT EXISTS trajectory_notes_embedding_hnsw_idx
  ON trajectory_notes USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW() AT TIME ZONE 'utc';
  RETURN NEW;
END;
$$;

DO $$ BEGIN
  CREATE TRIGGER trajectories_set_updated_at
    BEFORE UPDATE ON trajectories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TRIGGER trajectory_notes_set_updated_at
    BEFORE UPDATE ON trajectory_notes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""


def _new_id() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _row_to_stored(row: asyncpg.Record) -> StoredTrajectory:
    return StoredTrajectory.model_validate(
        {
            "id": str(row["id"]),
            "source": row["source"],
            "client_id": row["client_id"],
            "cdc_hash": row["cdc_hash"],
            "trajectory_fingerprint": row["trajectory_fingerprint"],
            "original_goal": row["original_goal"],
            "original_prompt": row["original_prompt"],
            "broken_code": row["broken_code"],
            "error_log": row["error_log"],
            "ai_reflection": row["ai_reflection"],
            "final_fix": row["final_fix"],
            "history": json.loads(row["history"]) if isinstance(row["history"], str) else row["history"],
            "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"],
            "processed": row["processed"],
            "note_model": row["note_model"],
            "note_error": row["note_error"],
            "processed_at": row["processed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    ln = math.sqrt(sum(v * v for v in left))
    rn = math.sqrt(sum(v * v for v in right))
    if ln == 0.0 or rn == 0.0:
        return 0.0
    return dot / (ln * rn)


def _is_resolved(final_fix: str) -> bool:
    return not final_fix.strip().upper().startswith("UNRESOLVED")


class PostgresConnectionProvider:
    """Manages a connection pool to a plain Postgres instance."""

    def __init__(self, database_url: str) -> None:
        if asyncpg is None:
            raise RuntimeError("asyncpg is not installed. Run: pip install asyncpg")
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=10)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        logger.info("Postgres schema ensured")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class PostgresGraphStore(GraphStore):
    """Direct asyncpg-backed trajectory storage."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    async def insert_trajectory(self, trajectory: TrajectoryIngestRequest) -> IngestResponse:
        fingerprint = trajectory_fingerprint(
            original_prompt=trajectory.original_prompt,
            error_log=trajectory.error_log,
            final_fix=trajectory.final_fix,
        )
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            existing = await self._find_existing(conn, trajectory, fingerprint)
            if existing is not None:
                return await self._merge_existing(conn, existing, trajectory, fingerprint)
            return await self._insert_new(conn, trajectory, fingerprint)

    async def _find_existing(
        self,
        conn: asyncpg.Connection,
        trajectory: TrajectoryIngestRequest,
        fingerprint: str,
    ) -> StoredTrajectory | None:
        row = await conn.fetchrow(
            "SELECT * FROM trajectories WHERE id = $1", trajectory.trajectory_id
        )
        if row is not None:
            return _row_to_stored(row)
        if trajectory.cdc_hash:
            row = await conn.fetchrow(
                "SELECT * FROM trajectories WHERE cdc_hash = $1", trajectory.cdc_hash
            )
            if row is not None:
                return _row_to_stored(row)
        row = await conn.fetchrow(
            "SELECT * FROM trajectories WHERE trajectory_fingerprint = $1", fingerprint
        )
        if row is not None:
            return _row_to_stored(row)
        return None

    async def _insert_new(
        self,
        conn: asyncpg.Connection,
        trajectory: TrajectoryIngestRequest,
        fingerprint: str,
    ) -> IngestResponse:
        tid = trajectory.trajectory_id
        history_json = json.dumps([t.model_dump(mode="json") for t in trajectory.history])
        metadata_json = json.dumps(trajectory.metadata)

        await conn.execute(
            """INSERT INTO trajectories
               (id, source, client_id, cdc_hash, trajectory_fingerprint,
                original_goal, original_prompt, broken_code, error_log,
                ai_reflection, final_fix, history, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb)
               ON CONFLICT (id) DO NOTHING""",
            tid, trajectory.source, trajectory.client_id, trajectory.cdc_hash,
            fingerprint, trajectory.original_goal, trajectory.original_prompt,
            trajectory.broken_code, trajectory.error_log, trajectory.ai_reflection,
            trajectory.final_fix, history_json, metadata_json,
        )

        nodes = [
            (_new_id(), tid, "prompt", trajectory.original_prompt),
            (_new_id(), tid, "attempt", trajectory.broken_code or ""),
            (_new_id(), tid, "error", trajectory.error_log),
        ]
        if trajectory.ai_reflection:
            nodes.append((_new_id(), tid, "reflection", trajectory.ai_reflection))
        nodes.append((_new_id(), tid, "fix", trajectory.final_fix))

        await conn.executemany(
            """INSERT INTO trajectory_nodes (id, trajectory_id, node_type, content)
               VALUES ($1, $2, $3, $4)""",
            nodes,
        )
        node_map = {n[2]: n[0] for n in nodes}

        edges = [
            (_new_id(), tid, node_map["prompt"], node_map["attempt"], "prompt_to_attempt"),
            (_new_id(), tid, node_map["attempt"], node_map["error"], "attempt_to_error"),
        ]
        if "reflection" in node_map:
            edges.append((_new_id(), tid, node_map["error"], node_map["reflection"], "error_to_reflection"))
            edges.append((_new_id(), tid, node_map["reflection"], node_map["fix"], "reflection_to_fix"))
        else:
            edges.append((_new_id(), tid, node_map["error"], node_map["fix"], "error_to_fix"))

        await conn.executemany(
            """INSERT INTO trajectory_edges
               (id, trajectory_id, source_node_id, target_node_id, edge_type)
               VALUES ($1,$2,$3,$4,$5)""",
            edges,
        )

        return IngestResponse(
            trajectory_id=tid,
            created=True,
            stored_nodes=len(nodes),
            stored_edges=len(edges),
            queued_for_notes=True,
        )

    async def _merge_existing(
        self,
        conn: asyncpg.Connection,
        existing: StoredTrajectory,
        incoming: TrajectoryIngestRequest,
        fingerprint: str,
    ) -> IngestResponse:
        final_fix = incoming.final_fix if _is_resolved(incoming.final_fix) or not _is_resolved(existing.final_fix) else existing.final_fix
        ai_reflection = incoming.ai_reflection or existing.ai_reflection
        error_log = incoming.error_log if len(incoming.error_log.strip()) > len(existing.error_log.strip()) else existing.error_log
        original_prompt = incoming.original_prompt if len(incoming.original_prompt.strip()) > len(existing.original_prompt.strip()) else existing.original_prompt

        should_requeue = bool(
            not existing.processed
            or final_fix != existing.final_fix
            or ai_reflection != existing.ai_reflection
            or error_log != existing.error_log
            or original_prompt != existing.original_prompt
        )

        await conn.execute(
            """UPDATE trajectories SET
                 original_prompt=$1, error_log=$2, final_fix=$3,
                 ai_reflection=$4, trajectory_fingerprint=$5,
                 cdc_hash=COALESCE($6, cdc_hash),
                 client_id=COALESCE($7, client_id),
                 original_goal=COALESCE($8, original_goal),
                 broken_code=COALESCE($9, broken_code),
                 processed = CASE WHEN $10 THEN FALSE ELSE processed END,
                 note_error = CASE WHEN $10 THEN NULL ELSE note_error END
               WHERE id=$11""",
            original_prompt, error_log, final_fix, ai_reflection,
            fingerprint, incoming.cdc_hash, incoming.client_id,
            incoming.original_goal, incoming.broken_code,
            should_requeue, existing.trajectory_id,
        )

        return IngestResponse(
            trajectory_id=existing.trajectory_id,
            created=False,
            stored_nodes=0,
            stored_edges=0,
            queued_for_notes=should_requeue,
        )

    async def get_prefetch_match(self, cdc_hash: str) -> PrefetchMatch | None:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT t.id, t.cdc_hash, t.original_goal, t.ai_reflection, t.final_fix,
                          n.id AS note_id, n.note_text, n.source_model
                   FROM trajectories t
                   LEFT JOIN trajectory_notes n ON n.trajectory_id = t.id
                   WHERE t.cdc_hash = $1
                   LIMIT 1""",
                cdc_hash,
            )
        if row is None:
            return None
        return PrefetchMatch(
            trajectory_id=str(row["id"]),
            cdc_hash=row["cdc_hash"],
            note_id=str(row["note_id"]) if row["note_id"] else None,
            original_goal=row["original_goal"],
            final_fix=row["final_fix"],
            note_text=row["note_text"],
            source_model=row["source_model"],
            ai_reflection=row["ai_reflection"],
        )

    async def list_unprocessed(self, *, limit: int = 10) -> list[StoredTrajectory]:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM trajectories WHERE processed = FALSE ORDER BY created_at LIMIT $1",
                limit,
            )
        return [_row_to_stored(r) for r in rows]

    async def list_recent_trajectories(self, *, limit: int = 50) -> list[StoredTrajectory]:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM trajectories ORDER BY created_at DESC LIMIT $1", limit
            )
        return [_row_to_stored(r) for r in rows]

    async def mark_processed(self, trajectory_id: str, *, note_model: str) -> None:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE trajectories
                   SET processed=TRUE, note_model=$1, note_error=NULL, processed_at=$2
                   WHERE id=$3""",
                note_model, _utcnow(), trajectory_id,
            )

    async def mark_processing_error(self, trajectory_id: str, *, error_message: str) -> None:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE trajectories SET note_error=$1 WHERE id=$2",
                error_message[:500], trajectory_id,
            )

    async def delete_older_than(self, *, retention_days: int) -> int:
        cutoff = datetime.fromtimestamp(
            _utcnow().timestamp() - retention_days * 86400, tz=UTC
        )
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM trajectories WHERE processed = TRUE AND created_at < $1",
                cutoff,
            )
        count = int(result.split()[-1]) if result else 0
        return count


class PostgresVectorStore(VectorStore):
    """Direct asyncpg-backed vector search with pgvector."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    async def upsert_embeddings(self, notes: list[NoteRecord]) -> int:
        if not notes:
            return 0
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            for note in notes:
                embedding_str = "[" + ",".join(str(v) for v in note.embedding) + "]"
                metadata_json = json.dumps(note.metadata)
                await conn.execute(
                    """INSERT INTO trajectory_notes
                         (id, trajectory_id, note_text, source_model, embedding, metadata)
                       VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb)
                       ON CONFLICT (trajectory_id) DO UPDATE SET
                         note_text = EXCLUDED.note_text,
                         source_model = EXCLUDED.source_model,
                         embedding = EXCLUDED.embedding,
                         metadata = EXCLUDED.metadata,
                         version = trajectory_notes.version + 1""",
                    note.note_id, note.trajectory_id, note.note_text,
                    note.source_model, embedding_str, metadata_json,
                )
        return len(notes)

    async def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        settings: Any | None = None,
        limit: int = 10,
        match_threshold: float = 0.75,
    ) -> list[NoteSearchResult]:
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT
                     n.id AS note_id, n.trajectory_id, n.note_text, n.source_model,
                     n.metadata, n.created_at, n.version,
                     n.helpful_count, n.unhelpful_count, n.suppress_count, n.match_count,
                     t.original_prompt, t.final_fix, t.ai_reflection,
                     1 - (n.embedding <=> $1::vector) AS similarity
                   FROM trajectory_notes n
                   JOIN trajectories t ON t.id = n.trajectory_id
                   WHERE 1 - (n.embedding <=> $1::vector) >= $2
                   ORDER BY n.embedding <=> $1::vector
                   LIMIT $3""",
                embedding_str, match_threshold, limit,
            )

        results = [
            NoteSearchResult(
                note_id=str(row["note_id"]),
                trajectory_id=str(row["trajectory_id"]),
                note_text=row["note_text"],
                source_model=row["source_model"],
                similarity=float(row["similarity"]),
                metadata=(json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"] or {})
                | {"match_count": row["match_count"]},
                created_at=row["created_at"],
                original_prompt=row["original_prompt"],
                final_fix=row["final_fix"],
                ai_reflection=row["ai_reflection"],
                version=row["version"],
                helpful_count=row["helpful_count"],
                unhelpful_count=row["unhelpful_count"],
                suppress_count=row["suppress_count"],
            )
            for row in rows
        ]

        if not results:
            results = await self._fallback_search(query_embedding, limit, match_threshold)

        if settings is not None and query_text.strip():
            results = rerank_results(results, query_text=query_text, settings=settings)
        return results

    async def _fallback_search(
        self,
        query_embedding: list[float],
        limit: int,
        match_threshold: float,
    ) -> list[NoteSearchResult]:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT n.id AS note_id, n.trajectory_id, n.note_text, n.source_model,
                          n.embedding, n.metadata, n.created_at, n.version,
                          n.helpful_count, n.unhelpful_count, n.suppress_count, n.match_count,
                          t.original_prompt, t.final_fix, t.ai_reflection
                   FROM trajectory_notes n
                   JOIN trajectories t ON t.id = n.trajectory_id
                   ORDER BY n.created_at DESC LIMIT $1""",
                max(limit * 10, 50),
            )

        results: list[NoteSearchResult] = []
        for row in rows:
            raw_emb = row["embedding"]
            if isinstance(raw_emb, str):
                stored_emb = [float(v) for v in json.loads(raw_emb)]
            elif isinstance(raw_emb, (list, tuple)):
                stored_emb = [float(v) for v in raw_emb]
            else:
                continue
            sim = _cosine_similarity(query_embedding, stored_emb)
            if sim < match_threshold:
                continue
            results.append(
                NoteSearchResult(
                    note_id=str(row["note_id"]),
                    trajectory_id=str(row["trajectory_id"]),
                    note_text=row["note_text"],
                    source_model=row["source_model"],
                    similarity=round(sim, 4),
                    metadata=(json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"] or {})
                    | {"match_count": row["match_count"]},
                    created_at=row["created_at"],
                    original_prompt=row["original_prompt"],
                    final_fix=row["final_fix"],
                    ai_reflection=row["ai_reflection"],
                    version=row["version"],
                    helpful_count=row["helpful_count"],
                    unhelpful_count=row["unhelpful_count"],
                    suppress_count=row["suppress_count"],
                )
            )
        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:limit]

    async def record_matches(self, note_ids: list[str]) -> None:
        if not note_ids:
            return
        now = _utcnow()
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """UPDATE trajectory_notes
                   SET match_count = match_count + 1, last_matched_at = $1
                   WHERE id = $2""",
                [(now, nid) for nid in note_ids],
            )

    async def submit_feedback(self, payload: FeedbackRequest) -> None:
        if payload.note_id is None:
            return
        counter = {
            "helpful": "helpful_count",
            "wrong": "unhelpful_count",
            "too_aggressive": "suppress_count",
        }[payload.feedback]
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE trajectory_notes SET {counter} = {counter} + 1 WHERE id = $1",
                payload.note_id,
            )

    async def count(self) -> int:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM trajectory_notes")
        return int(row["cnt"]) if row else 0

    async def list_recent_notes(self, *, limit: int = 50) -> list[NoteRecord]:
        pool = await self._provider.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, trajectory_id, note_text, source_model, embedding,
                          version, helpful_count, unhelpful_count, suppress_count,
                          match_count, last_matched_at, metadata, created_at
                   FROM trajectory_notes
                   ORDER BY created_at DESC LIMIT $1""",
                limit,
            )
        results: list[NoteRecord] = []
        for row in rows:
            raw_emb = row["embedding"]
            if isinstance(raw_emb, str):
                embedding = [float(v) for v in json.loads(raw_emb)]
            elif isinstance(raw_emb, (list, tuple)):
                embedding = [float(v) for v in raw_emb]
            else:
                embedding = []
            results.append(
                NoteRecord.model_validate(
                    {
                        "id": str(row["id"]),
                        "trajectory_id": str(row["trajectory_id"]),
                        "note_text": row["note_text"],
                        "source_model": row["source_model"],
                        "embedding": embedding,
                        "version": row["version"],
                        "helpful_count": row["helpful_count"],
                        "unhelpful_count": row["unhelpful_count"],
                        "suppress_count": row["suppress_count"],
                        "match_count": row["match_count"],
                        "last_matched_at": row["last_matched_at"],
                        "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"] or {},
                        "created_at": row["created_at"],
                    }
                )
            )
        return results
