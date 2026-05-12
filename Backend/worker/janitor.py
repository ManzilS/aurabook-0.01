"""Background job that turns raw trajectories into searchable notes."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, get_settings
from db.demo_store import DemoGraphStore, DemoVectorStore, DeterministicEmbeddingService
from db.graph import SupabaseGraphStore
from db.supabase_client import SupabaseClientProvider
from db.vector import SupabaseVectorStore
from models.schema import NoteRecord, StoredTrajectory, WorkerBatchResult
from services.embeddings import EmbeddingService
from services.openai_failures import OpenAIHaltError
from services.retrieval import build_retrieval_text
from worker.summarizer import build_summarizer

logger = logging.getLogger(__name__)


class NotesJanitorJob:
    """Fetches unprocessed trajectories and emits summarized notes."""

    def __init__(
        self,
        *,
        graph_store: Any,
        vector_store: Any,
        summarizer: Any,
        embedding_service: Any,
        summary_model: str,
        batch_size: int = 10,
    ) -> None:
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._summarizer = summarizer
        self._embedding_service = embedding_service
        self._summary_model = summary_model
        self._batch_size = batch_size

    async def run_once(self) -> WorkerBatchResult:
        """Run one worker batch."""
        trajectories: list[StoredTrajectory] = await self._graph_store.list_unprocessed(
            limit=self._batch_size
        )
        processed = 0
        failed = 0

        for trajectory in trajectories:
            try:
                note_text = await self._summarizer.summarize(trajectory)
                embedding = await self._embedding_service.embed_text(
                    build_retrieval_text(note_text=note_text, trajectory=trajectory)
                )
                note = NoteRecord(
                    trajectory_id=trajectory.trajectory_id,
                    note_text=note_text,
                    source_model=self._summary_model,
                    embedding=embedding,
                    metadata={
                        "source": trajectory.source,
                        "cdc_hash": trajectory.cdc_hash,
                    },
                )
                await self._vector_store.upsert_embeddings([note])
                await self._graph_store.mark_processed(
                    trajectory.trajectory_id,
                    note_model=self._summary_model,
                )
                processed += 1
            except OpenAIHaltError:
                logger.error(
                    "Stopping janitor because the configured LLM provider returned a fatal token/quota error for trajectory %s",
                    trajectory.trajectory_id,
                )
                raise
            except Exception as exc:
                failed += 1
                logger.exception(
                    "Failed to process trajectory %s: %s",
                    trajectory.trajectory_id,
                    exc,
                )
                await self._graph_store.mark_processing_error(
                    trajectory.trajectory_id,
                    error_message=str(exc),
                )

        return WorkerBatchResult(
            requested=len(trajectories),
            processed=processed,
            failed=failed,
            llm_tokens_used=int(getattr(self._summarizer, "total_tokens_used", 0)),
        )


async def _run_job(settings: Settings) -> WorkerBatchResult:
    use_demo_mode = settings.demo_mode or not (
        settings.supabase_url
        and settings.supabase_service_role_key
        and settings.embedding_provider_ready()
    )
    provider = None if use_demo_mode else SupabaseClientProvider(settings)
    graph_store = (
        DemoGraphStore(Path(settings.demo_store_path).expanduser().resolve())
        if use_demo_mode
        else SupabaseGraphStore(provider)
    )
    vector_store = (
        DemoVectorStore(Path(settings.demo_store_path).expanduser().resolve())
        if use_demo_mode
        else SupabaseVectorStore(provider)
    )
    embedding_service = DeterministicEmbeddingService() if use_demo_mode else EmbeddingService(settings)
    summarizer = build_summarizer(settings)
    job = NotesJanitorJob(
        graph_store=graph_store,
        vector_store=vector_store,
        summarizer=summarizer,
        embedding_service=embedding_service,
        summary_model=settings.resolve_summary_model(),
        batch_size=settings.worker_batch_size,
    )

    try:
        return await job.run_once()
    finally:
        await summarizer.close()
        await embedding_service.close()
        if provider is not None:
            await provider.close()


def main() -> None:
    """CLI entry point for the janitor worker."""
    parser = argparse.ArgumentParser(description="Run one AuraBook notes janitor batch.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override the configured worker batch size.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.batch_size is not None:
        settings.worker_batch_size = args.batch_size

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    result = asyncio.run(_run_job(settings))
    logger.info("Worker batch finished: %s", result.model_dump())
