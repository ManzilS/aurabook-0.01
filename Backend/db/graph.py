"""Relational graph storage built on top of standard Supabase SQL tables."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.base import GraphStore
from db.supabase_client import SupabaseClientProvider
from models.schema import (
    GraphEdgeRecord,
    GraphNodeRecord,
    IngestResponse,
    PrefetchMatch,
    TrajectoryIngestRequest,
    StoredTrajectory,
)
from postgrest.exceptions import APIError
from services.fingerprints import trajectory_fingerprint

logger = logging.getLogger(__name__)


def _response_data(response):
    """Return Supabase response data, tolerating `maybe_single().execute()` -> None."""
    if response is None:
        return None
    return response.data


def _api_error_code(exc: APIError) -> str | None:
    """Extract a stable PostgREST error code from an APIError."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    raw_error = getattr(exc, "_raw_error", None)
    if isinstance(raw_error, dict):
        raw_code = raw_error.get("code")
        if isinstance(raw_code, str) and raw_code:
            return raw_code
    return None


def _merge_unique_strings(*groups: list[str] | tuple[str, ...]) -> list[str]:
    """Return unique non-empty strings while preserving first-seen order."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for value in group:
            cleaned = value.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            merged.append(cleaned)
    return merged


def _history_identity(turn) -> tuple[Any, ...]:
    """Return a stable dedupe key for one recorded attempt turn."""
    return (
        turn.model,
        turn.prompt,
        turn.response,
        turn.thought,
        turn.tried,
        turn.outcome,
        tuple(turn.commands),
        tuple(turn.changed_files),
        turn.token_count,
        turn.duration_ms,
    )


def _merge_history(existing: list, incoming: list) -> list:
    """Merge trajectory attempt history while preserving order."""
    seen: set[tuple[Any, ...]] = set()
    merged = []
    for turn in [*existing, *incoming]:
        identity = _history_identity(turn)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(turn)
    merged.sort(key=lambda item: item.timestamp)
    return merged


def _is_resolved_final_fix(final_fix: str) -> bool:
    """Return True when the final fix represents a resolved outcome."""
    return not final_fix.strip().upper().startswith("UNRESOLVED")


def _metadata_dict(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize arbitrary metadata into a mutable dictionary."""
    return dict(payload or {})


class SupabaseGraphStore(GraphStore):
    """Stores trajectories and prompt-attempt-error-fix relationships."""

    def __init__(self, provider: SupabaseClientProvider) -> None:
        self._provider = provider

    async def insert_trajectory(self, trajectory: TrajectoryIngestRequest) -> IngestResponse:
        """Insert a trajectory and its graph edges."""
        fingerprint = trajectory_fingerprint(
            original_prompt=trajectory.original_prompt,
            error_log=trajectory.error_log,
            final_fix=trajectory.final_fix,
        )
        existing_by_id = await self.get_trajectory_by_id(trajectory.trajectory_id)
        if existing_by_id is not None:
            return await self._merge_existing_trajectory(
                existing_by_id,
                trajectory,
                fingerprint=fingerprint,
            )
        existing_prefetch = None
        if trajectory.cdc_hash:
            existing_prefetch = await self.get_prefetch_match(trajectory.cdc_hash)
        existing_trajectory = None
        if existing_prefetch is None:
            existing_trajectory = await self.get_trajectory_by_fingerprint(fingerprint)
        if existing_prefetch is not None:
            existing_by_hash = await self.get_trajectory_by_id(existing_prefetch.trajectory_id)
            if existing_by_hash is None:
                return IngestResponse(
                    trajectory_id=existing_prefetch.trajectory_id,
                    created=False,
                    stored_nodes=0,
                    stored_edges=0,
                    queued_for_notes=existing_prefetch.note_text is None,
                )
            return await self._merge_existing_trajectory(
                existing_by_hash,
                trajectory,
                fingerprint=fingerprint,
            )
        if existing_trajectory is not None:
            return await self._merge_existing_trajectory(
                existing_trajectory,
                trajectory,
                fingerprint=fingerprint,
            )

        client = await self._provider.get_client()
        trajectory_payload = {
            "id": trajectory.trajectory_id,
            "source": trajectory.source,
            "client_id": trajectory.client_id,
            "cdc_hash": trajectory.cdc_hash,
            "original_goal": trajectory.original_goal,
            "original_prompt": trajectory.original_prompt,
            "broken_code": trajectory.broken_code,
            "error_log": trajectory.error_log,
            "ai_reflection": trajectory.ai_reflection,
            "final_fix": trajectory.final_fix,
            "trajectory_fingerprint": fingerprint,
            "history": [turn.model_dump(mode="json") for turn in trajectory.history],
            "metadata": trajectory.metadata,
        }
        try:
            trajectory_response = await client.table("trajectories").insert(
                trajectory_payload
            ).execute()
        except APIError as exc:
            if _api_error_code(exc) != "23505":
                raise
            logger.info(
                "Trajectory %s already exists; treating ingest as idempotent replay.",
                trajectory.trajectory_id,
            )
            existing_by_id = await self.get_trajectory_by_id(trajectory.trajectory_id)
            if existing_by_id is None:
                return IngestResponse(
                    trajectory_id=trajectory.trajectory_id,
                    created=False,
                    stored_nodes=0,
                    stored_edges=0,
                    queued_for_notes=False,
                )
            return await self._merge_existing_trajectory(
                existing_by_id,
                trajectory,
                fingerprint=fingerprint,
            )
        stored_payload = _response_data(trajectory_response) or []
        stored_trajectory = stored_payload[0]

        nodes = [
            GraphNodeRecord(
                trajectory_id=stored_trajectory["id"],
                node_type="prompt",
                content=trajectory.original_prompt,
            ),
            GraphNodeRecord(
                trajectory_id=stored_trajectory["id"],
                node_type="attempt",
                content=trajectory.broken_code or "",
            ),
            GraphNodeRecord(
                trajectory_id=stored_trajectory["id"],
                node_type="error",
                content=trajectory.error_log,
            ),
        ]
        if trajectory.ai_reflection:
            nodes.append(
                GraphNodeRecord(
                    trajectory_id=stored_trajectory["id"],
                    node_type="reflection",
                    content=trajectory.ai_reflection,
                )
            )
        nodes.append(
            GraphNodeRecord(
                trajectory_id=stored_trajectory["id"],
                node_type="fix",
                content=trajectory.final_fix,
            )
        )
        node_response = await client.table("trajectory_nodes").insert(
            [node.model_dump(by_alias=True, mode="json") for node in nodes]
        ).execute()
        stored_nodes = _response_data(node_response) or []
        node_map = {node["node_type"]: node["id"] for node in stored_nodes}

        edges = [
            GraphEdgeRecord(
                trajectory_id=stored_trajectory["id"],
                source_node_id=node_map["prompt"],
                target_node_id=node_map["attempt"],
                edge_type="prompt_to_attempt",
            ),
            GraphEdgeRecord(
                trajectory_id=stored_trajectory["id"],
                source_node_id=node_map["attempt"],
                target_node_id=node_map["error"],
                edge_type="attempt_to_error",
            ),
        ]
        if "reflection" in node_map:
            edges.append(
                GraphEdgeRecord(
                    trajectory_id=stored_trajectory["id"],
                    source_node_id=node_map["error"],
                    target_node_id=node_map["reflection"],
                    edge_type="error_to_reflection",
                )
            )
            edges.append(
                GraphEdgeRecord(
                    trajectory_id=stored_trajectory["id"],
                    source_node_id=node_map["reflection"],
                    target_node_id=node_map["fix"],
                    edge_type="reflection_to_fix",
                )
            )
        else:
            edges.append(
                GraphEdgeRecord(
                    trajectory_id=stored_trajectory["id"],
                    source_node_id=node_map["error"],
                    target_node_id=node_map["fix"],
                    edge_type="error_to_fix",
                )
            )
        await client.table("trajectory_edges").insert(
            [edge.model_dump(by_alias=True, mode="json") for edge in edges]
        ).execute()

        logger.info("Stored trajectory %s", stored_trajectory["id"])
        return IngestResponse(
            trajectory_id=stored_trajectory["id"],
            created=True,
            stored_nodes=len(nodes),
            stored_edges=len(edges),
            queued_for_notes=True,
        )

    async def _merge_existing_trajectory(
        self,
        existing: StoredTrajectory,
        incoming: TrajectoryIngestRequest,
        *,
        fingerprint: str,
    ) -> IngestResponse:
        """Merge a replayed observation into an existing trajectory row."""
        merged_history = _merge_history(existing.history, incoming.history)
        merged_metadata = self._merge_metadata(existing, incoming, merged_history=merged_history)
        resolved_incoming = _is_resolved_final_fix(incoming.final_fix)
        resolved_existing = _is_resolved_final_fix(existing.final_fix)
        final_fix = incoming.final_fix if resolved_incoming or not resolved_existing else existing.final_fix
        ai_reflection = incoming.ai_reflection or existing.ai_reflection
        broken_code = incoming.broken_code or existing.broken_code
        error_log = (
            incoming.error_log
            if len(incoming.error_log.strip()) > len(existing.error_log.strip())
            else existing.error_log
        )
        original_goal = incoming.original_goal or existing.original_goal
        original_prompt = (
            incoming.original_prompt
            if len(incoming.original_prompt.strip()) > len(existing.original_prompt.strip())
            else existing.original_prompt
        )
        cdc_hash = incoming.cdc_hash or existing.cdc_hash
        client_id = incoming.client_id or existing.client_id

        incoming_history_dump = [turn.model_dump(mode="json") for turn in merged_history]
        existing_history_dump = [turn.model_dump(mode="json") for turn in existing.history]
        history_changed = incoming_history_dump != existing_history_dump
        metadata_changed = merged_metadata != _metadata_dict(existing.metadata)
        content_changed = any(
            [
                final_fix != existing.final_fix,
                ai_reflection != existing.ai_reflection,
                broken_code != existing.broken_code,
                error_log != existing.error_log,
                original_goal != existing.original_goal,
                original_prompt != existing.original_prompt,
                cdc_hash != existing.cdc_hash,
                client_id != existing.client_id,
                history_changed,
                metadata_changed,
            ]
        )
        should_requeue_notes = bool(
            not existing.processed
            or final_fix != existing.final_fix
            or ai_reflection != existing.ai_reflection
            or broken_code != existing.broken_code
            or error_log != existing.error_log
            or original_prompt != existing.original_prompt
        )

        if content_changed:
            client = await self._provider.get_client()
            await client.table("trajectories").update(
                {
                    "source": existing.source,
                    "client_id": client_id,
                    "cdc_hash": cdc_hash,
                    "original_goal": original_goal,
                    "original_prompt": original_prompt,
                    "broken_code": broken_code,
                    "error_log": error_log,
                    "ai_reflection": ai_reflection,
                    "final_fix": final_fix,
                    "trajectory_fingerprint": fingerprint,
                    "history": incoming_history_dump,
                    "metadata": merged_metadata,
                    "processed": False if should_requeue_notes else existing.processed,
                    "note_error": None if should_requeue_notes else existing.note_error,
                }
            ).eq("id", existing.trajectory_id).execute()
            logger.info(
                "Merged replayed trajectory %s (requeue=%s)",
                existing.trajectory_id,
                should_requeue_notes,
            )

        return IngestResponse(
            trajectory_id=existing.trajectory_id,
            created=False,
            stored_nodes=0,
            stored_edges=0,
            queued_for_notes=should_requeue_notes,
        )

    def _merge_metadata(
        self,
        existing: StoredTrajectory,
        incoming: TrajectoryIngestRequest,
        *,
        merged_history: list,
    ) -> dict[str, Any]:
        """Merge high-value observation metadata for analytics and replay safety."""
        existing_metadata = _metadata_dict(existing.metadata)
        incoming_metadata = _metadata_dict(incoming.metadata)
        merged = {**existing_metadata, **incoming_metadata}

        resolved_existing = _is_resolved_final_fix(existing.final_fix)
        resolved_incoming = _is_resolved_final_fix(incoming.final_fix)

        sources_seen = _merge_unique_strings(
            [str(item) for item in existing_metadata.get("sources_seen", []) if isinstance(item, str)],
            [str(item) for item in incoming_metadata.get("sources_seen", []) if isinstance(item, str)],
            [existing.source, incoming.source],
        )
        client_ids = _merge_unique_strings(
            [str(item) for item in existing_metadata.get("client_ids", []) if isinstance(item, str)],
            [str(item) for item in incoming_metadata.get("client_ids", []) if isinstance(item, str)],
            [value for value in (existing.client_id, incoming.client_id) if value],
        )
        attempted_models = _merge_unique_strings(
            [str(item) for item in existing_metadata.get("attempted_models", []) if isinstance(item, str)],
            [str(item) for item in incoming_metadata.get("attempted_models", []) if isinstance(item, str)],
            [turn.model for turn in merged_history if turn.model],
        )
        repositories_seen = _merge_unique_strings(
            [str(item) for item in existing_metadata.get("repositories_seen", []) if isinstance(item, str)],
            [str(item) for item in incoming_metadata.get("repositories_seen", []) if isinstance(item, str)],
            [
                str(item)
                for item in (
                    existing_metadata.get("repository"),
                    incoming_metadata.get("repository"),
                )
                if isinstance(item, str)
            ],
        )

        existing_observation_count = int(existing_metadata.get("observation_count", 1) or 1)
        existing_resolved_count = int(
            existing_metadata.get(
                "resolved_observation_count",
                1 if resolved_existing else 0,
            )
            or 0
        )
        existing_unresolved_count = int(
            existing_metadata.get(
                "unresolved_observation_count",
                0 if resolved_existing else 1,
            )
            or 0
        )
        model_attempt_counts: dict[str, int] = {}
        for turn in merged_history:
            if not turn.model:
                continue
            model_attempt_counts[turn.model] = model_attempt_counts.get(turn.model, 0) + 1

        merged["attempt_count"] = len(merged_history)
        merged["attempted_models"] = attempted_models
        merged["failed_model_count"] = len(attempted_models)
        merged["observation_count"] = existing_observation_count + 1
        merged["resolved_observation_count"] = existing_resolved_count + (1 if resolved_incoming else 0)
        merged["unresolved_observation_count"] = existing_unresolved_count + (0 if resolved_incoming else 1)
        merged["sources_seen"] = sources_seen
        merged["client_ids"] = client_ids
        merged["repositories_seen"] = repositories_seen
        merged["model_attempt_counts"] = model_attempt_counts
        merged["last_ingested_at"] = datetime.now(UTC).isoformat()
        merged.setdefault("first_ingested_at", existing.created_at.isoformat())
        return merged

    async def get_prefetch_match(self, cdc_hash: str) -> PrefetchMatch | None:
        """Return an exact hash match when one exists."""
        client = await self._provider.get_client()
        trajectory_response = await client.table("trajectories").select(
            "id, cdc_hash, original_goal, ai_reflection, final_fix"
        ).eq("cdc_hash", cdc_hash).maybe_single().execute()
        trajectory = _response_data(trajectory_response)
        if trajectory is None:
            return None

        note_response = await client.table("trajectory_notes").select(
            "id, note_text, source_model"
        ).eq("trajectory_id", trajectory["id"]).maybe_single().execute()
        note = _response_data(note_response)

        return PrefetchMatch(
            trajectory_id=trajectory["id"],
            cdc_hash=trajectory["cdc_hash"],
            note_id=None if note is None else note["id"],
            original_goal=trajectory.get("original_goal"),
            final_fix=trajectory["final_fix"],
            note_text=None if note is None else note["note_text"],
            source_model=None if note is None else note["source_model"],
            ai_reflection=trajectory.get("ai_reflection"),
        )

    async def list_unprocessed(self, *, limit: int = 10) -> list[StoredTrajectory]:
        """Return the oldest unprocessed trajectories."""
        client = await self._provider.get_client()
        response = await client.table("trajectories").select("*").eq(
            "processed",
            False,
        ).order("created_at").range(0, max(limit - 1, 0)).execute()
        rows = _response_data(response) or []
        return [StoredTrajectory.model_validate(row) for row in rows]

    async def list_recent_trajectories(self, *, limit: int = 50) -> list[StoredTrajectory]:
        """Return recent trajectories for dashboard rendering."""
        client = await self._provider.get_client()
        response = await client.table("trajectories").select("*").order(
            "created_at",
            desc=True,
        ).range(0, max(limit - 1, 0)).execute()
        rows = _response_data(response) or []
        return [StoredTrajectory.model_validate(row) for row in rows]

    async def get_trajectory_by_fingerprint(self, fingerprint: str) -> StoredTrajectory | None:
        """Return an already ingested trajectory by normalized fingerprint."""
        client = await self._provider.get_client()
        response = await client.table("trajectories").select("*").eq(
            "trajectory_fingerprint",
            fingerprint,
        ).maybe_single().execute()
        row = _response_data(response)
        if row is None:
            return None
        return StoredTrajectory.model_validate(row)

    async def get_trajectory_by_id(self, trajectory_id: str) -> StoredTrajectory | None:
        """Return a trajectory by its primary key when it already exists."""
        client = await self._provider.get_client()
        response = await client.table("trajectories").select("*").eq(
            "id",
            trajectory_id,
        ).maybe_single().execute()
        row = _response_data(response)
        if row is None:
            return None
        return StoredTrajectory.model_validate(row)

    async def mark_processed(self, trajectory_id: str, *, note_model: str) -> None:
        """Mark a trajectory as processed after note creation."""
        client = await self._provider.get_client()
        await client.table("trajectories").update(
            {
                "processed": True,
                "note_model": note_model,
                "note_error": None,
                "processed_at": datetime.now(UTC).isoformat(),
            }
        ).eq("id", trajectory_id).execute()

    async def mark_processing_error(self, trajectory_id: str, *, error_message: str) -> None:
        """Store the last note-generation error for a trajectory."""
        client = await self._provider.get_client()
        await client.table("trajectories").update(
            {"note_error": error_message[:500]}
        ).eq("id", trajectory_id).execute()

    async def delete_older_than(self, *, retention_days: int) -> int:
        """Delete processed trajectories older than the retention threshold."""
        client = await self._provider.get_client()
        cutoff = (datetime.now(UTC).timestamp() - retention_days * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        response = await client.table("trajectories").select("id").eq(
            "processed",
            True,
        ).lt("created_at", cutoff_iso).execute()
        rows = _response_data(response) or []
        if not rows:
            return 0
        ids = [row["id"] for row in rows]
        for trajectory_id in ids:
            await client.table("trajectories").delete().eq("id", trajectory_id).execute()
        return len(ids)
