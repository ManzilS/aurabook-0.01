"""Tests for hybrid retrieval and note quality reranking."""

from __future__ import annotations

from config import Settings
from models.schema import NoteSearchResult, StoredTrajectory, TrajectoryTurn
from services.retrieval import build_retrieval_text, quality_score, rerank_results


def test_quality_score_penalizes_suppressed_notes() -> None:
    strong = NoteSearchResult(
        note_id="note-good",
        trajectory_id="traj-good",
        note_text="System Note: Use httpx.AsyncClient in async code.",
        source_model="gpt-5.4-mini",
        similarity=0.82,
        metadata={"match_count": 10},
        created_at="2026-04-22T00:00:00+00:00",
        helpful_count=5,
        unhelpful_count=0,
        suppress_count=0,
    )
    weak = NoteSearchResult(
        note_id="note-bad",
        trajectory_id="traj-bad",
        note_text="System Note: Keep using requests in async code.",
        source_model="gpt-5.4-mini",
        similarity=0.82,
        metadata={"match_count": 1},
        created_at="2026-04-22T00:00:00+00:00",
        helpful_count=0,
        unhelpful_count=2,
        suppress_count=2,
    )

    assert quality_score(strong) > quality_score(weak)


def test_rerank_results_uses_lexical_and_quality_signals() -> None:
    settings = Settings()
    generic = NoteSearchResult(
        note_id="note-generic",
        trajectory_id="traj-generic",
        note_text="System Note: Review the previous failure carefully.",
        source_model="gpt-5.4-mini",
        similarity=0.91,
        metadata={"match_count": 0},
        created_at="2026-04-22T00:00:00+00:00",
        helpful_count=0,
        unhelpful_count=0,
        suppress_count=0,
    )
    targeted = NoteSearchResult(
        note_id="note-targeted",
        trajectory_id="traj-targeted",
        note_text="System Note: Import CORSMiddleware from fastapi.middleware.cors before adding middleware.",
        source_model="gpt-5.4-mini",
        similarity=0.88,
        metadata={"match_count": 8},
        created_at="2026-04-22T00:00:00+00:00",
        helpful_count=4,
        unhelpful_count=0,
        suppress_count=0,
        final_fix="from fastapi.middleware.cors import CORSMiddleware",
    )

    ranked = rerank_results(
        [generic, targeted],
        query_text="FastAPI CORS middleware import is missing",
        settings=settings,
    )

    assert ranked[0].note_id == "note-targeted"
    assert ranked[0].metadata["hybrid_score"] >= ranked[1].metadata["hybrid_score"]


def test_build_retrieval_text_includes_attempt_history_and_models() -> None:
    trajectory = StoredTrajectory(
        id="traj-123",
        source="swarm",
        original_goal="Fix async HTTP usage.",
        original_prompt="My async request blocks the event loop.",
        broken_code="requests.get(url)",
        error_log="RuntimeError: blocking call detected",
        ai_reflection="The code used a blocking client in async code.",
        final_fix="Use httpx.AsyncClient.",
        history=[
            TrajectoryTurn(
                prompt="Attempt 1",
                response="Summary: Replace requests with httpx.",
                model="gpt-5.4-mini",
                thought="The bug is a blocking HTTP client.",
                tried="Swapped to httpx.AsyncClient.",
                outcome="success",
            )
        ],
        metadata={"attempted_models": ["gpt-5.4-mini"], "model_attempt_counts": {"gpt-5.4-mini": 1}},
    )

    text = build_retrieval_text(
        note_text="System Note: Use httpx.AsyncClient in async code.",
        trajectory=trajectory,
    )

    assert "Attempted models:" in text
    assert "gpt-5.4-mini" in text
    assert "Attempt history:" in text
    assert "Swapped to httpx.AsyncClient." in text
