"""Data models for the experimentation and evaluation framework."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Scoring layer definitions
# ---------------------------------------------------------------------------


class LayerType(str, Enum):
    """Types of scoring layers available in the evaluation pipeline."""

    VECTOR_SIMILARITY = "vector_similarity"
    LEXICAL_OVERLAP = "lexical_overlap"
    QUALITY_PRIOR = "quality_prior"
    RECENCY_BOOST = "recency_boost"
    SOURCE_MODEL_AFFINITY = "source_model_affinity"
    FEEDBACK_MOMENTUM = "feedback_momentum"
    DIVERSITY_PENALTY = "diversity_penalty"
    CONTEXT_LENGTH_PENALTY = "context_length_penalty"
    CUSTOM = "custom"


class ScoringLayer(BaseModel):
    """A single scoring layer in the evaluation pipeline.

    Each layer contributes a weighted score component. Layers can be
    enabled/disabled, reordered, and have their weights tuned per experiment.
    """

    layer_type: LayerType
    weight: float = 1.0
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    """Layer-specific parameters (e.g. decay_hours for recency_boost)."""

    model_config = ConfigDict(extra="forbid")


class ScoringPipeline(BaseModel):
    """An ordered list of scoring layers defining how results are ranked."""

    layers: list[ScoringLayer] = Field(default_factory=list)
    normalize_weights: bool = True
    """If True, layer weights are normalized to sum to 1.0 at evaluation time."""
    min_score_threshold: float = 0.0
    """Results below this combined score are dropped."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class RolloutStrategy(str, Enum):
    """How users are assigned to experiment variants."""

    PERCENTAGE = "percentage"
    """Hash-based percentage allocation."""
    ALLOWLIST = "allowlist"
    """Explicit user/client IDs."""
    ORG = "org"
    """All members of specified orgs."""


class Variant(BaseModel):
    """A single variant (arm) in an experiment."""

    id: str
    name: str
    description: str = ""
    pipeline: ScoringPipeline
    """The scoring pipeline this variant uses."""
    traffic_percentage: float = 0.0
    """Percentage of traffic allocated (0-100). Must sum to 100 across variants."""
    allowlist: list[str] = Field(default_factory=list)
    """Explicit client_ids or user_ids assigned to this variant."""

    model_config = ConfigDict(extra="forbid")


class Experiment(BaseModel):
    """An A/B test comparing multiple scoring configurations."""

    id: str
    name: str
    description: str = ""
    status: ExperimentStatus = ExperimentStatus.DRAFT
    variants: list[Variant] = Field(default_factory=list)
    rollout_strategy: RolloutStrategy = RolloutStrategy.PERCENTAGE
    target_orgs: list[str] = Field(default_factory=list)
    """For ORG strategy: org slugs that participate."""
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Events and stats
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Types of events recorded for experiment analysis."""

    SEARCH_SERVED = "search_served"
    """A search result set was returned to the user."""
    RESULT_CLICKED = "result_clicked"
    """User applied/used a specific result."""
    FEEDBACK_HELPFUL = "feedback_helpful"
    FEEDBACK_WRONG = "feedback_wrong"
    FEEDBACK_SUPPRESSED = "feedback_suppressed"
    RESULT_IGNORED = "result_ignored"
    """Results were returned but none were used."""
    LATENCY_RECORDED = "latency_recorded"


class ExperimentEvent(BaseModel):
    """A single event in the experiment analytics stream."""

    event_id: str = ""
    experiment_id: str
    variant_id: str
    event_type: EventType
    client_id: str = ""
    user_id: str = ""
    org_slug: str = ""
    note_id: str | None = None
    query_text: str = ""
    result_count: int = 0
    position: int | None = None
    """Position of the clicked result (0-indexed). Enables NDCG calculation."""
    latency_ms: float | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    """Per-layer scores for this event (for debugging)."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class VariantStats(BaseModel):
    """Aggregated statistics for a single variant."""

    variant_id: str
    variant_name: str = ""
    total_searches: int = 0
    total_clicks: int = 0
    total_helpful: int = 0
    total_wrong: int = 0
    total_suppressed: int = 0
    total_ignored: int = 0
    click_through_rate: float = 0.0
    """clicks / searches"""
    precision_at_1: float = 0.0
    """Fraction of searches where first result was clicked."""
    mean_reciprocal_rank: float = 0.0
    """Average 1/(position+1) for clicked results."""
    ndcg_at_5: float = 0.0
    """Normalized discounted cumulative gain at k=5."""
    avg_latency_ms: float = 0.0
    helpful_rate: float = 0.0
    """helpful / (helpful + wrong + suppressed)"""
    sample_size: int = 0
    confidence_interval: tuple[float, float] = (0.0, 0.0)
    """95% CI for click_through_rate."""

    model_config = ConfigDict(extra="forbid")


class ExperimentReport(BaseModel):
    """Full report for an experiment comparing all variants."""

    experiment_id: str
    experiment_name: str = ""
    status: ExperimentStatus = ExperimentStatus.RUNNING
    duration_hours: float = 0.0
    variants: list[VariantStats] = Field(default_factory=list)
    winner: str | None = None
    """variant_id of statistically significant winner, if any."""
    confidence: float = 0.0
    """Statistical confidence that winner is better (0-1)."""
    recommendation: str = ""
    """Human-readable recommendation."""

    model_config = ConfigDict(extra="forbid")
