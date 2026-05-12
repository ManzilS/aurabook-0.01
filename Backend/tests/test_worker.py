"""Tests for the janitor worker."""

from __future__ import annotations

import pytest

from services.openai_failures import OpenAIHaltError
from worker.janitor import NotesJanitorJob


@pytest.mark.asyncio
async def test_janitor_processes_unprocessed_trajectories(
    services,
    stored_trajectory,
) -> None:
    services.graph_store.unprocessed = [stored_trajectory]

    class FakeSummarizer:
        async def summarize(self, trajectory):
            return "System Note: Use httpx.AsyncClient in async code."

    job = NotesJanitorJob(
        graph_store=services.graph_store,
        vector_store=services.vector_store,
        summarizer=FakeSummarizer(),
        embedding_service=services.embedding_service,
        summary_model="gpt-5.4-mini",
        batch_size=10,
    )

    result = await job.run_once()

    assert result.processed == 1
    assert result.failed == 0
    assert result.llm_tokens_used == 0
    assert services.vector_store.upserted_notes[0].trajectory_id == stored_trajectory.trajectory_id
    assert services.embedding_service.requests
    assert "System Note: Use httpx.AsyncClient in async code." in services.embedding_service.requests[0]
    assert stored_trajectory.original_prompt in services.embedding_service.requests[0]
    assert stored_trajectory.error_log in services.embedding_service.requests[0]
    assert "gpt-5.4-mini" in services.embedding_service.requests[0]
    assert "Swapped requests for httpx.AsyncClient and reran tests." in services.embedding_service.requests[0]
    assert services.graph_store.processed_ids == [stored_trajectory.trajectory_id]


@pytest.mark.asyncio
async def test_janitor_marks_errors_and_continues(services, stored_trajectory) -> None:
    services.graph_store.unprocessed = [stored_trajectory]

    class ExplodingSummarizer:
        async def summarize(self, trajectory):
            raise ValueError("bad summary")

    job = NotesJanitorJob(
        graph_store=services.graph_store,
        vector_store=services.vector_store,
        summarizer=ExplodingSummarizer(),
        embedding_service=services.embedding_service,
        summary_model="gpt-5.4-mini",
        batch_size=10,
    )

    result = await job.run_once()

    assert result.processed == 0
    assert result.failed == 1
    assert result.llm_tokens_used == 0
    assert services.graph_store.error_marks[0][0] == stored_trajectory.trajectory_id


@pytest.mark.asyncio
async def test_janitor_halts_on_fatal_openai_errors(services, stored_trajectory) -> None:
    services.graph_store.unprocessed = [stored_trajectory]

    class FatalSummarizer:
        async def summarize(self, trajectory):
            raise OpenAIHaltError("insufficient_quota")

    job = NotesJanitorJob(
        graph_store=services.graph_store,
        vector_store=services.vector_store,
        summarizer=FatalSummarizer(),
        embedding_service=services.embedding_service,
        summary_model="gpt-5.4-mini",
        batch_size=10,
    )

    with pytest.raises(OpenAIHaltError):
        await job.run_once()

    assert services.graph_store.error_marks == []
    assert services.graph_store.processed_ids == []
