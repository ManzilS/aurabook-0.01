"""Pluggable evaluation pipeline for scoring and ranking search results.

The pipeline is composed of ordered *layers*, each contributing a weighted
score component.  Layers can be added, removed, reweighted, or reordered
per-experiment variant without touching the core retrieval code.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from models.experiment import LayerType, ScoringLayer, ScoringPipeline
from models.schema import NoteSearchResult
from services.fingerprints import normalize_text

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


# ---------------------------------------------------------------------------
# Individual scoring layer implementations
# ---------------------------------------------------------------------------


def _score_vector_similarity(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Raw vector cosine similarity from the embedding search."""
    return float(result.similarity)


def _score_lexical_overlap(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Token-level Jaccard overlap between query and result text."""
    left_tokens = set(_TOKEN_PATTERN.findall(normalize_text(query_text)))
    right_text = "\n\n".join(
        part
        for part in (
            result.note_text,
            result.original_prompt or "",
            result.final_fix or "",
            result.ai_reflection or "",
        )
        if part.strip()
    )
    right_tokens = set(_TOKEN_PATTERN.findall(normalize_text(right_text)))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return max(intersection / union, intersection / min(len(left_tokens), len(right_tokens)))


def _score_quality_prior(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Feedback-based quality score using tanh normalization."""
    helpful = float(result.helpful_count or 0)
    unhelpful = float(result.unhelpful_count or 0)
    suppress = float(result.suppress_count or 0)
    matches = float((result.metadata or {}).get("match_count", 0))

    # Tunable parameters
    helpful_weight = params.get("helpful_weight", 1.0)
    match_weight = params.get("match_weight", 0.25)
    unhelpful_weight = params.get("unhelpful_weight", 1.0)
    suppress_weight = params.get("suppress_weight", 1.5)
    denominator = params.get("denominator", 5.0)

    raw = (
        helpful_weight * helpful
        + match_weight * matches
        - unhelpful_weight * unhelpful
        - suppress_weight * suppress
    )
    return max(-1.0, min(1.0, math.tanh(raw / denominator)))


def _score_recency_boost(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Exponential decay boost favoring recently created notes.

    params:
        decay_hours: half-life in hours (default 168 = 1 week)
    """
    decay_hours = params.get("decay_hours", 168.0)
    now = datetime.now(timezone.utc)
    age_hours = (now - result.created_at).total_seconds() / 3600.0
    return math.exp(-0.693 * age_hours / max(decay_hours, 1.0))


def _score_source_model_affinity(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Boost results from preferred source models.

    params:
        preferred_models: list of model name substrings to boost
        boost: score when model matches (default 1.0)
        penalty: score when model doesn't match (default 0.0)
    """
    preferred = params.get("preferred_models", [])
    boost = params.get("boost", 1.0)
    penalty = params.get("penalty", 0.0)
    model = result.source_model.lower()
    for pref in preferred:
        if pref.lower() in model:
            return boost
    return penalty


def _score_feedback_momentum(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Velocity-based score: recent feedback trend matters more than absolute count.

    Uses a simplified momentum: (helpful - unhelpful) / total_feedback.
    Results with no feedback get a neutral 0.5 score.
    """
    helpful = float(result.helpful_count or 0)
    unhelpful = float(result.unhelpful_count or 0)
    suppress = float(result.suppress_count or 0)
    total = helpful + unhelpful + suppress
    if total == 0:
        return 0.5  # neutral
    return (helpful - unhelpful - suppress) / total * 0.5 + 0.5


def _score_diversity_penalty(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Penalize results that are too similar to higher-ranked ones.

    This is computed relative to position in the current result set.
    Results at index 0 get no penalty, later results are penalized if
    they share the same trajectory or very similar text.

    params:
        same_trajectory_penalty: penalty for same trajectory (default 0.3)
    """
    same_traj_penalty = params.get("same_trajectory_penalty", 0.3)
    idx = next(
        (i for i, r in enumerate(all_results) if r.note_id == result.note_id),
        0,
    )
    if idx == 0:
        return 1.0

    # Check if any earlier result shares the same trajectory
    traj_id = result.trajectory_id
    for earlier in all_results[:idx]:
        if earlier.trajectory_id == traj_id:
            return 1.0 - same_traj_penalty

    return 1.0


def _score_context_length_penalty(
    result: NoteSearchResult,
    query_text: str,
    all_results: list[NoteSearchResult],
    params: dict[str, Any],
) -> float:
    """Prefer concise results — long notes may overwhelm the context window.

    params:
        ideal_length: ideal character count (default 500)
        max_length: beyond this, score drops to floor (default 3000)
        floor: minimum score (default 0.3)
    """
    ideal = params.get("ideal_length", 500)
    max_len = params.get("max_length", 3000)
    floor = params.get("floor", 0.3)
    length = len(result.note_text)
    if length <= ideal:
        return 1.0
    if length >= max_len:
        return floor
    # Linear decay between ideal and max
    return 1.0 - (1.0 - floor) * (length - ideal) / (max_len - ideal)


# ---------------------------------------------------------------------------
# Layer registry
# ---------------------------------------------------------------------------

_LAYER_FUNCTIONS = {
    LayerType.VECTOR_SIMILARITY: _score_vector_similarity,
    LayerType.LEXICAL_OVERLAP: _score_lexical_overlap,
    LayerType.QUALITY_PRIOR: _score_quality_prior,
    LayerType.RECENCY_BOOST: _score_recency_boost,
    LayerType.SOURCE_MODEL_AFFINITY: _score_source_model_affinity,
    LayerType.FEEDBACK_MOMENTUM: _score_feedback_momentum,
    LayerType.DIVERSITY_PENALTY: _score_diversity_penalty,
    LayerType.CONTEXT_LENGTH_PENALTY: _score_context_length_penalty,
}


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------


def evaluate_pipeline(
    results: list[NoteSearchResult],
    pipeline: ScoringPipeline,
    query_text: str,
) -> list[tuple[NoteSearchResult, dict[str, float]]]:
    """Run the full scoring pipeline and return ranked results with per-layer scores.

    Returns:
        List of (result, scores_dict) tuples sorted by combined score descending.
        scores_dict keys are layer_type names, plus "combined".
    """
    active_layers = [layer for layer in pipeline.layers if layer.enabled]
    if not active_layers:
        # No layers → return in original order
        return [(r, {"combined": float(r.similarity)}) for r in results]

    # Normalize weights if requested
    total_weight = sum(layer.weight for layer in active_layers)
    if pipeline.normalize_weights and total_weight > 0:
        weight_scale = 1.0 / total_weight
    else:
        weight_scale = 1.0

    scored: list[tuple[float, NoteSearchResult, dict[str, float]]] = []

    for result in results:
        layer_scores: dict[str, float] = {}
        combined = 0.0

        for layer in active_layers:
            scorer = _LAYER_FUNCTIONS.get(layer.layer_type)
            if scorer is None:
                continue
            raw_score = scorer(result, query_text, results, layer.params)
            normalized_weight = layer.weight * weight_scale
            layer_scores[layer.layer_type.value] = round(raw_score, 4)
            combined += normalized_weight * raw_score

        layer_scores["combined"] = round(combined, 4)
        scored.append((combined, result, layer_scores))

    scored.sort(key=lambda item: item[0], reverse=True)

    # Apply minimum threshold
    if pipeline.min_score_threshold > 0:
        scored = [s for s in scored if s[0] >= pipeline.min_score_threshold]

    return [(result, scores) for _, result, scores in scored]


def enrich_results(
    ranked: list[tuple[NoteSearchResult, dict[str, float]]],
    experiment_id: str = "",
    variant_id: str = "",
) -> list[NoteSearchResult]:
    """Attach scoring metadata and experiment info to results."""
    enriched = []
    for result, scores in ranked:
        updated_metadata = {
            **result.metadata,
            **{f"score_{k}": v for k, v in scores.items()},
        }
        if experiment_id:
            updated_metadata["experiment_id"] = experiment_id
            updated_metadata["variant_id"] = variant_id
        enriched.append(result.model_copy(update={"metadata": updated_metadata}))
    return enriched


# ---------------------------------------------------------------------------
# Default pipeline (matches current production behavior)
# ---------------------------------------------------------------------------


def default_pipeline(
    vector_weight: float = 0.65,
    lexical_weight: float = 0.25,
    quality_weight: float = 0.10,
) -> ScoringPipeline:
    """Return the default pipeline matching current production scoring."""
    return ScoringPipeline(
        layers=[
            ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=vector_weight),
            ScoringLayer(layer_type=LayerType.LEXICAL_OVERLAP, weight=lexical_weight),
            ScoringLayer(layer_type=LayerType.QUALITY_PRIOR, weight=quality_weight),
        ],
        normalize_weights=False,  # Weights already sum to 1.0
    )
