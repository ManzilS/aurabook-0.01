"""Hybrid retrieval and reranking helpers for AuraBook Cloud Brain."""

from __future__ import annotations

import math
import re

from config import Settings
from models.schema import NoteSearchResult, StoredTrajectory
from services.fingerprints import normalize_text

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


def build_retrieval_text(*, note_text: str, trajectory: StoredTrajectory) -> str:
    """Build the text embedded for note retrieval.

    The retrieval document keeps the dense system note while preserving the
    original prompt, failure mode, and verified fix so prompt-style semantic
    searches can still land on the right note.
    """
    metadata = trajectory.metadata if isinstance(trajectory.metadata, dict) else {}
    attempted_models = metadata.get("attempted_models", [])
    model_attempt_counts = metadata.get("model_attempt_counts", {})
    history_lines: list[str] = []
    for index, turn in enumerate(trajectory.history, start=1):
        history_lines.append(
            "\n".join(
                part
                for part in (
                    f"Attempt {index}",
                    f"Model: {turn.model}" if turn.model else "",
                    f"Thought: {turn.thought}" if turn.thought else "",
                    f"Tried: {turn.tried}" if turn.tried else "",
                    f"Outcome: {turn.outcome}" if turn.outcome else "",
                )
                if part
            )
        )
    sections = [
        f"System note:\n{note_text}",
        f"Original goal:\n{trajectory.original_goal or ''}",
        f"Original prompt:\n{trajectory.original_prompt}",
        f"Broken code:\n{trajectory.broken_code or ''}",
        f"Error log:\n{trajectory.error_log}",
        f"Reflection:\n{trajectory.ai_reflection or ''}",
        f"Attempted models:\n{attempted_models}",
        f"Model attempt counts:\n{model_attempt_counts}",
        "Attempt history:\n{}".format('\n\n'.join(history_lines) if history_lines else ''),
        f"Final fix:\n{trajectory.final_fix}",
    ]
    return "\n\n".join(section for section in sections if section.strip())


def lexical_overlap(left: str, right: str) -> float:
    """Return a simple lexical overlap score between two texts."""
    left_tokens = set(_TOKEN_PATTERN.findall(normalize_text(left)))
    right_tokens = set(_TOKEN_PATTERN.findall(normalize_text(right)))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return max(intersection / union, intersection / min(len(left_tokens), len(right_tokens)))


def quality_score(result: NoteSearchResult) -> float:
    """Return a small quality prior derived from note usage and feedback."""
    helpful = float(result.helpful_count or 0)
    unhelpful = float(result.unhelpful_count or 0)
    suppress = float(result.suppress_count or 0)
    matches = float((result.metadata or {}).get("match_count", 0))
    raw = helpful + 0.25 * matches - unhelpful - 1.5 * suppress
    return max(-1.0, min(1.0, math.tanh(raw / 5.0)))


def rerank_results(
    results: list[NoteSearchResult],
    *,
    query_text: str,
    settings: Settings,
) -> list[NoteSearchResult]:
    """Rerank note results using hybrid lexical, vector, and quality signals."""
    enriched: list[tuple[float, NoteSearchResult]] = []
    for result in results:
        lexical = lexical_overlap(
            query_text,
            "\n\n".join(
                part
                for part in (
                    result.note_text,
                    result.original_prompt or "",
                    result.final_fix or "",
                    result.ai_reflection or "",
                )
                if part.strip()
            ),
        )
        quality = quality_score(result)
        combined = (
            settings.retrieval_vector_weight * float(result.similarity)
            + settings.retrieval_lexical_weight * lexical
            + settings.retrieval_quality_weight * quality
        )
        updated = result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "vector_similarity": float(result.similarity),
                    "lexical_similarity": round(lexical, 4),
                    "quality_score": round(quality, 4),
                    "hybrid_score": round(combined, 4),
                    "match_count": result.metadata.get("match_count", 0),
                }
            }
        )
        enriched.append((combined, updated))
    enriched.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in enriched]
