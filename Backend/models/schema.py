"""Strict Pydantic schemas for trajectories, notes, and API responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator


def _new_id() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrajectoryTurn(BaseModel):
    """Single prompt-response pair captured during local troubleshooting."""

    prompt: StrictStr
    response: StrictStr
    model: StrictStr | None = None
    thought: StrictStr | None = None
    tried: StrictStr | None = None
    outcome: StrictStr | None = None
    commands: list[StrictStr] = Field(default_factory=list)
    changed_files: list[StrictStr] = Field(default_factory=list)
    token_count: StrictInt | None = None
    duration_ms: StrictInt | None = None
    timestamp: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid")


class TrajectoryIngestRequest(BaseModel):
    """Inbound trajectory payload accepted by the Cloud Brain."""

    trajectory_id: StrictStr = Field(
        default_factory=_new_id,
        validation_alias=AliasChoices("trajectory_id", "id"),
        serialization_alias="trajectory_id",
    )
    source: Literal["local_client", "swarm", "manual", "worker"] = "local_client"
    client_id: StrictStr | None = None
    cdc_hash: StrictStr | None = None
    original_goal: StrictStr | None = None
    original_prompt: StrictStr = Field(
        validation_alias=AliasChoices("original_prompt", "trigger_prompt")
    )
    broken_code: StrictStr | None = Field(
        default=None,
        validation_alias=AliasChoices("broken_code", "attempt_code", "attempt"),
    )
    error_log: StrictStr = Field(
        validation_alias=AliasChoices("error_log", "previous_error_log")
    )
    ai_reflection: StrictStr | None = None
    final_fix: StrictStr = Field(
        validation_alias=AliasChoices("final_fix", "final_code", "fix")
    )
    history: list[TrajectoryTurn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def searchable_text(self) -> str:
        """Return the combined text used for embeddings and search."""
        sections = [
            self.original_goal or "",
            self.original_prompt,
            self.broken_code or "",
            self.error_log,
            self.ai_reflection or "",
            self.final_fix,
        ]
        return "\n\n".join(section for section in sections if section.strip())


class StoredTrajectory(BaseModel):
    """Trajectory row persisted in Supabase."""

    trajectory_id: StrictStr = Field(alias="id")
    source: StrictStr
    client_id: StrictStr | None = None
    cdc_hash: StrictStr | None = None
    trajectory_fingerprint: StrictStr | None = None
    original_goal: StrictStr | None = None
    original_prompt: StrictStr
    broken_code: StrictStr | None = None
    error_log: StrictStr
    ai_reflection: StrictStr | None = None
    final_fix: StrictStr
    history: list[TrajectoryTurn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    processed: StrictBool = False
    note_model: StrictStr | None = None
    note_error: StrictStr | None = None
    processed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def searchable_text(self) -> str:
        """Return the combined text used when generating embeddings."""
        payload = TrajectoryIngestRequest(
            trajectory_id=self.trajectory_id,
            source=self.source,
            cdc_hash=self.cdc_hash,
            original_goal=self.original_goal,
            original_prompt=self.original_prompt,
            broken_code=self.broken_code,
            error_log=self.error_log,
            ai_reflection=self.ai_reflection,
            final_fix=self.final_fix,
            history=self.history,
            metadata=self.metadata,
        )
        return payload.searchable_text()


class GraphNodeRecord(BaseModel):
    """Single trajectory graph node."""

    node_id: StrictStr = Field(default_factory=_new_id, alias="id")
    trajectory_id: StrictStr
    node_type: Literal["prompt", "attempt", "error", "reflection", "fix"]
    content: StrictStr
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GraphEdgeRecord(BaseModel):
    """Single trajectory graph edge."""

    edge_id: StrictStr = Field(default_factory=_new_id, alias="id")
    trajectory_id: StrictStr
    source_node_id: StrictStr
    target_node_id: StrictStr
    edge_type: StrictStr

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IngestResponse(BaseModel):
    """Response returned from the ingest endpoint."""

    trajectory_id: StrictStr
    created: StrictBool
    stored_nodes: StrictInt
    stored_edges: StrictInt
    queued_for_notes: StrictBool

    model_config = ConfigDict(extra="forbid")


class PrefetchRequest(BaseModel):
    """Exact-hash lookup request."""

    cdc_hash: StrictStr = Field(min_length=8)

    model_config = ConfigDict(extra="forbid")


class PrefetchMatch(BaseModel):
    """Exact prefetch lookup hit."""

    trajectory_id: StrictStr
    cdc_hash: StrictStr
    note_id: StrictStr | None = None
    original_goal: StrictStr | None = None
    final_fix: StrictStr
    note_text: StrictStr | None = None
    source_model: StrictStr | None = None
    ai_reflection: StrictStr | None = None

    model_config = ConfigDict(extra="forbid")


class PrefetchResponse(BaseModel):
    """Response returned from the prefetch endpoint."""

    found: StrictBool
    match: PrefetchMatch | None = None

    model_config = ConfigDict(extra="forbid")


class SearchRequest(BaseModel):
    """Semantic search request."""

    prompt: StrictStr | None = None
    error_log: StrictStr | None = None
    limit: StrictInt = 5
    match_threshold: StrictFloat | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_query(self) -> "SearchRequest":
        if not (self.prompt or self.error_log):
            raise ValueError("prompt or error_log is required")
        return self

    def searchable_text(self) -> str:
        """Return the combined query string used for embedding search."""
        sections = [self.prompt or "", self.error_log or ""]
        return "\n\n".join(section for section in sections if section.strip())


class NoteRecord(BaseModel):
    """Embedded note row stored in the vector table."""

    note_id: StrictStr = Field(default_factory=_new_id, alias="id")
    trajectory_id: StrictStr
    note_text: StrictStr
    source_model: StrictStr
    embedding: list[float]
    version: StrictInt = 1
    helpful_count: StrictInt = 0
    unhelpful_count: StrictInt = 0
    suppress_count: StrictInt = 0
    match_count: StrictInt = 0
    last_matched_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NoteSearchResult(BaseModel):
    """Semantic note search result."""

    note_id: StrictStr
    trajectory_id: StrictStr
    note_text: StrictStr
    source_model: StrictStr
    similarity: StrictFloat
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    original_prompt: StrictStr | None = None
    final_fix: StrictStr | None = None
    ai_reflection: StrictStr | None = None
    version: StrictInt | None = None
    helpful_count: StrictInt | None = None
    unhelpful_count: StrictInt | None = None
    suppress_count: StrictInt | None = None

    model_config = ConfigDict(extra="forbid")


class SearchResponse(BaseModel):
    """Response returned from the semantic search endpoint."""

    matches: list[NoteSearchResult] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class FeedbackRequest(BaseModel):
    """Feedback payload submitted by the local client."""

    note_id: StrictStr | None = None
    trajectory_id: StrictStr | None = None
    client_id: StrictStr
    feedback: Literal["helpful", "wrong", "too_aggressive"]
    repository: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_target(self) -> "FeedbackRequest":
        if not (self.note_id or self.trajectory_id):
            raise ValueError("note_id or trajectory_id is required")
        return self


class FeedbackResponse(BaseModel):
    """Feedback acknowledgement returned by the cloud API."""

    accepted: StrictBool = True
    note_id: StrictStr | None = None
    trajectory_id: StrictStr | None = None
    feedback: Literal["helpful", "wrong", "too_aggressive"]

    model_config = ConfigDict(extra="forbid")


class WorkerBatchResult(BaseModel):
    """Summary of one janitor batch run."""

    requested: StrictInt
    processed: StrictInt
    failed: StrictInt
    llm_tokens_used: StrictInt = 0

    model_config = ConfigDict(extra="forbid")


class DashboardRatingPoint(BaseModel):
    """Single derived rating-history point for a dashboard note."""

    timestamp: datetime
    score: StrictInt

    model_config = ConfigDict(extra="forbid")


class DashboardComment(BaseModel):
    """Dashboard comment rendered in the note detail panel."""

    comment_id: StrictStr = Field(alias="id")
    author: StrictStr
    text: StrictStr
    created_at: datetime

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DashboardAttempt(BaseModel):
    """Structured model-attempt record shown in the dashboard detail panel."""

    timestamp: datetime
    model: StrictStr | None = None
    thought: StrictStr | None = None
    tried: StrictStr | None = None
    outcome: StrictStr | None = None
    commands: list[StrictStr] = Field(default_factory=list)
    changed_files: list[StrictStr] = Field(default_factory=list)
    token_count: StrictInt | None = None
    duration_ms: StrictInt | None = None

    model_config = ConfigDict(extra="forbid")


class DashboardErrorStep(BaseModel):
    """One linked step in an error-resolution chain."""

    step_id: StrictStr = Field(alias="id")
    parent_step_id: StrictStr | None = None
    depth: StrictInt = 0
    kind: Literal["root_error", "attempt", "derived_error", "solution"]
    title: StrictStr
    timestamp: datetime
    trajectory_id: StrictStr | None = None
    model: StrictStr | None = None
    diagnosis: StrictStr | None = None
    action: StrictStr | None = None
    outcome: StrictStr | None = None
    error_excerpt: StrictStr | None = None
    error_detail: StrictStr | None = None
    commands: list[StrictStr] = Field(default_factory=list)
    changed_files: list[StrictStr] = Field(default_factory=list)
    token_count: StrictInt | None = None
    duration_ms: StrictInt | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DashboardErrorCluster(BaseModel):
    """Dashboard-ready grouped error cluster with linked follow-on failures."""

    cluster_id: StrictStr = Field(alias="id")
    trajectory_id: StrictStr | None = None
    note_id: StrictStr | None = None
    problem_tag: StrictStr
    summary: StrictStr
    diagnosis_summary: StrictStr
    full_diagnosis: StrictStr
    root_error_excerpt: StrictStr
    root_error_detail: StrictStr
    latest_error_excerpt: StrictStr | None = None
    unresolved: StrictBool
    created_at: datetime
    updated_at: datetime
    language: Literal["Python", "JavaScript", "TypeScript", "Rust"]
    attempted_models: list[StrictStr] = Field(default_factory=list)
    failed_model_count: StrictInt = 0
    attempt_count: StrictInt = 0
    failure_count: StrictInt = 0
    cascade_error_count: StrictInt = 0
    related_trajectory_ids: list[StrictStr] = Field(default_factory=list)
    steps: list[DashboardErrorStep] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DashboardEnvironment(BaseModel):
    """Environment summary shown in the dashboard detail panel."""

    os: StrictStr
    language_version: StrictStr
    libraries: list[StrictStr] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class DashboardNote(BaseModel):
    """Dashboard-ready note card."""

    note_id: StrictStr = Field(alias="id")
    error_cluster_id: StrictStr | None = None
    problem_tag: StrictStr
    summary: StrictStr
    diagnosis_summary: StrictStr
    full_diagnosis: StrictStr
    model: StrictStr
    created_at: datetime
    velocity: Literal["rising", "stable", "declining"]
    status: Literal["Rising", "Stable", "Declining"]
    score: StrictInt
    unsolved: StrictBool
    language: Literal["Python", "JavaScript", "TypeScript", "Rust"]
    problem_description: StrictStr
    solution_text: StrictStr
    environment: DashboardEnvironment
    usage_count: StrictInt
    attempted_models: list[StrictStr] = Field(default_factory=list)
    unsolved_attempt_count: StrictInt = 0
    failed_model_count: StrictInt = 0
    attempt_count: StrictInt = 0
    attempts: list[DashboardAttempt] = Field(default_factory=list)
    rating_history: list[DashboardRatingPoint] = Field(default_factory=list)
    comments: list[DashboardComment] = Field(default_factory=list)
    community_context: StrictStr
    trajectory_id: StrictStr | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DashboardStats(BaseModel):
    """Top-line stats shown in the dashboard sidebar."""

    total_notes: StrictInt
    active_agents: StrictInt
    problems_solved_today: StrictInt

    model_config = ConfigDict(extra="forbid")


class DashboardActivity(BaseModel):
    """Realtime activity item for the dashboard ticker."""

    activity_id: StrictStr = Field(alias="id")
    text: StrictStr
    created_at: datetime
    tone: Literal["success", "warning", "neutral"]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DashboardSnapshot(BaseModel):
    """Complete dashboard payload returned to the frontend."""

    notes: list[DashboardNote] = Field(default_factory=list)
    error_clusters: list[DashboardErrorCluster] = Field(default_factory=list)
    stats: DashboardStats
    activities: list[DashboardActivity] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
