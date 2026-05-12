"""Sanitize stored trajectories and note embeddings in Supabase."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from db.supabase_client import SupabaseClientProvider
from db.vector import SupabaseVectorStore
from models.schema import NoteRecord, StoredTrajectory
from services.embeddings import EmbeddingService
from services.fingerprints import trajectory_fingerprint
from services.privacy import sanitize_machine_text, sanitize_stored_trajectory
from services.retrieval import build_retrieval_text

logger = logging.getLogger(__name__)


async def _fetch_trajectories(client, *, batch_size: int, limit: int | None) -> list[StoredTrajectory]:
    trajectories: list[StoredTrajectory] = []
    start = 0
    while True:
        end = start + batch_size - 1
        response = await client.table("trajectories").select("*").order(
            "created_at"
        ).range(start, end).execute()
        rows = response.data or []
        if not rows:
            break
        for row in rows:
            trajectories.append(StoredTrajectory.model_validate(row))
            if limit is not None and len(trajectories) >= limit:
                return trajectories
        if len(rows) < batch_size:
            break
        start += len(rows)
    return trajectories


async def _run_async(*, batch_size: int, limit: int | None, dry_run: bool) -> None:
    settings = get_settings()
    if settings.demo_mode or not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Live Supabase credentials are required for trajectory sanitization.")

    provider = SupabaseClientProvider(settings)
    vector_store = SupabaseVectorStore(provider)
    embedding_service = EmbeddingService(settings)
    client = await provider.get_client()

    try:
        trajectories = await _fetch_trajectories(client, batch_size=batch_size, limit=limit)
        updated_trajectories = 0
        updated_notes = 0

        for trajectory in trajectories:
            sanitized = sanitize_stored_trajectory(trajectory)
            new_fingerprint = trajectory_fingerprint(
                original_prompt=sanitized.original_prompt,
                error_log=sanitized.error_log,
                final_fix=sanitized.final_fix,
            )
            updates: dict[str, object] = {}

            for field_name in (
                "original_goal",
                "original_prompt",
                "broken_code",
                "error_log",
                "ai_reflection",
                "final_fix",
                "history",
                "metadata",
            ):
                original_value = getattr(trajectory, field_name)
                updated_value = getattr(sanitized, field_name)
                if original_value != updated_value:
                    updates[field_name] = (
                        [turn.model_dump(mode="json") for turn in updated_value]
                        if field_name == "history"
                        else updated_value
                    )
            if trajectory.trajectory_fingerprint != new_fingerprint:
                updates["trajectory_fingerprint"] = new_fingerprint

            note_response = await client.table("trajectory_notes").select("*").eq(
                "trajectory_id",
                trajectory.trajectory_id,
            ).maybe_single().execute()
            note_row = None if note_response is None else note_response.data
            note_changed = False

            if note_row is not None:
                note_text = sanitize_machine_text(note_row["note_text"])
                note_metadata = note_row.get("metadata") or {}
                if note_text != note_row["note_text"]:
                    note_changed = True
                if updates or note_changed:
                    embedding = await embedding_service.embed_text(
                        build_retrieval_text(note_text=note_text, trajectory=sanitized)
                    )
                    updated_note = NoteRecord(
                        id=note_row["id"],
                        trajectory_id=note_row["trajectory_id"],
                        note_text=note_text,
                        source_model=note_row["source_model"],
                        embedding=embedding,
                        metadata=note_metadata,
                        created_at=note_row["created_at"],
                        version=int(note_row.get("version") or 1),
                        helpful_count=int(note_row.get("helpful_count") or 0),
                        unhelpful_count=int(note_row.get("unhelpful_count") or 0),
                        suppress_count=int(note_row.get("suppress_count") or 0),
                        match_count=int(note_row.get("match_count") or 0),
                        last_matched_at=note_row.get("last_matched_at"),
                    )
                    if not dry_run:
                        await vector_store.upsert_embeddings([updated_note])
                    updated_notes += 1

            if updates:
                if not dry_run:
                    await client.table("trajectories").update(updates).eq(
                        "id",
                        trajectory.trajectory_id,
                    ).execute()
                updated_trajectories += 1

        logger.info(
            "Trajectory sanitization finished: trajectories=%d notes=%d dry_run=%s scanned=%d",
            updated_trajectories,
            updated_notes,
            dry_run,
            len(trajectories),
        )
    finally:
        await embedding_service.close()
        await provider.close()


def main() -> None:
    """Run the live trajectory sanitization pass."""
    parser = argparse.ArgumentParser(description="Sanitize stored machine paths in live trajectories and notes.")
    parser.add_argument("--batch-size", type=int, default=100, help="Supabase page size.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of trajectories to scan.")
    parser.add_argument("--dry-run", action="store_true", help="Compute changes without writing them.")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    asyncio.run(_run_async(batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
