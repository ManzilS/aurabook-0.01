"""API endpoints for experiment management, stats, and event recording.

Admin endpoints for creating/managing experiments and viewing reports.
Client endpoints for recording clicks and feedback events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from models.experiment import (
    EventType,
    Experiment,
    ExperimentEvent,
    ExperimentReport,
    ExperimentStatus,
    ScoringPipeline,
    Variant,
    VariantStats,
)

router = APIRouter(prefix="/v1/experiments", tags=["experiments"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class CreateExperimentRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    variants: list[Variant]
    rollout_strategy: str = "percentage"
    target_orgs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateExperimentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    variants: list[Variant] | None = None
    rollout_strategy: str | None = None
    target_orgs: list[str] | None = None
    metadata: dict[str, Any] | None = None


class RecordClickRequest(BaseModel):
    experiment_id: str
    variant_id: str
    note_id: str
    position: int = 0
    client_id: str = ""


class RecordFeedbackRequest(BaseModel):
    experiment_id: str
    variant_id: str
    note_id: str
    feedback_type: str  # "helpful" | "wrong" | "too_aggressive"
    client_id: str = ""


class ExperimentListResponse(BaseModel):
    experiments: list[Experiment]
    total: int


class MyExperimentsResponse(BaseModel):
    active: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=Experiment, status_code=201)
async def create_experiment(payload: CreateExperimentRequest, request: Request) -> Experiment:
    """Create a new experiment (starts in DRAFT status)."""
    engine = request.app.state.experiment_engine
    # Validate traffic percentages sum to 100
    total_traffic = sum(v.traffic_percentage for v in payload.variants)
    if abs(total_traffic - 100.0) > 0.01:
        raise HTTPException(
            status_code=422,
            detail=f"Variant traffic percentages must sum to 100 (got {total_traffic})",
        )
    experiment = Experiment(
        id=payload.id,
        name=payload.name,
        description=payload.description,
        variants=payload.variants,
        rollout_strategy=payload.rollout_strategy,
        target_orgs=payload.target_orgs,
        metadata=payload.metadata,
    )
    try:
        return engine.store.create(experiment)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=ExperimentListResponse)
async def list_experiments(request: Request, status: str | None = None) -> ExperimentListResponse:
    """List all experiments, optionally filtered by status."""
    engine = request.app.state.experiment_engine
    filter_status = ExperimentStatus(status) if status else None
    exps = engine.store.list_all(status=filter_status)
    return ExperimentListResponse(experiments=exps, total=len(exps))


@router.get("/{experiment_id}", response_model=Experiment)
async def get_experiment(experiment_id: str, request: Request) -> Experiment:
    """Get a single experiment by ID."""
    engine = request.app.state.experiment_engine
    exp = engine.store.get(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return exp


@router.patch("/{experiment_id}", response_model=Experiment)
async def update_experiment(
    experiment_id: str,
    payload: UpdateExperimentRequest,
    request: Request,
) -> Experiment:
    """Update experiment fields (only while in DRAFT or PAUSED)."""
    engine = request.app.state.experiment_engine
    exp = engine.store.get(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if exp.status == ExperimentStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot modify a running experiment. Pause it first.",
        )
    if payload.name is not None:
        exp.name = payload.name
    if payload.description is not None:
        exp.description = payload.description
    if payload.variants is not None:
        total_traffic = sum(v.traffic_percentage for v in payload.variants)
        if abs(total_traffic - 100.0) > 0.01:
            raise HTTPException(
                status_code=422,
                detail=f"Variant traffic percentages must sum to 100 (got {total_traffic})",
            )
        exp.variants = payload.variants
    if payload.rollout_strategy is not None:
        exp.rollout_strategy = payload.rollout_strategy
    if payload.target_orgs is not None:
        exp.target_orgs = payload.target_orgs
    if payload.metadata is not None:
        exp.metadata = payload.metadata
    return engine.store.update(exp)


@router.post("/{experiment_id}/start", response_model=Experiment)
async def start_experiment(experiment_id: str, request: Request) -> Experiment:
    """Start an experiment (moves from DRAFT/PAUSED → RUNNING)."""
    engine = request.app.state.experiment_engine
    try:
        return engine.store.start(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{experiment_id}/pause", response_model=Experiment)
async def pause_experiment(experiment_id: str, request: Request) -> Experiment:
    """Pause a running experiment."""
    engine = request.app.state.experiment_engine
    try:
        return engine.store.pause(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{experiment_id}/stop", response_model=Experiment)
async def stop_experiment(experiment_id: str, request: Request) -> Experiment:
    """Stop an experiment (marks as COMPLETED)."""
    engine = request.app.state.experiment_engine
    try:
        return engine.store.stop(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{experiment_id}", status_code=204)
async def delete_experiment(experiment_id: str, request: Request) -> None:
    """Delete an experiment (only if DRAFT or COMPLETED)."""
    engine = request.app.state.experiment_engine
    exp = engine.store.get(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if exp.status == ExperimentStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Cannot delete a running experiment")
    engine.store.delete(experiment_id)


# ---------------------------------------------------------------------------
# Stats and reporting
# ---------------------------------------------------------------------------


@router.get("/{experiment_id}/report", response_model=ExperimentReport)
async def get_report(experiment_id: str, request: Request) -> ExperimentReport:
    """Get the full statistical report for an experiment."""
    engine = request.app.state.experiment_engine
    stats = request.app.state.stats_collector
    exp = engine.store.get(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return stats.generate_report(exp)


@router.get("/{experiment_id}/variants/{variant_id}/stats", response_model=VariantStats)
async def get_variant_stats(
    experiment_id: str,
    variant_id: str,
    request: Request,
) -> VariantStats:
    """Get stats for a specific variant."""
    engine = request.app.state.experiment_engine
    stats = request.app.state.stats_collector
    exp = engine.store.get(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    variant = next((v for v in exp.variants if v.id == variant_id), None)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return stats.compute_variant_stats(experiment_id, variant_id, variant.name)


@router.get("/{experiment_id}/events")
async def get_events(
    experiment_id: str,
    request: Request,
    variant_id: str = "",
    event_type: str = "",
    limit: int = 100,
) -> list[ExperimentEvent]:
    """Query raw events for an experiment."""
    stats = request.app.state.stats_collector
    et = EventType(event_type) if event_type else None
    return stats.get_events(experiment_id, variant_id=variant_id, event_type=et, limit=limit)


# ---------------------------------------------------------------------------
# Client-facing: record events
# ---------------------------------------------------------------------------


@router.post("/events/click", status_code=201)
async def record_click(payload: RecordClickRequest, request: Request) -> dict[str, str]:
    """Record that a user clicked/used a search result."""
    stats = request.app.state.stats_collector
    stats.record_click(
        experiment_id=payload.experiment_id,
        variant_id=payload.variant_id,
        client_id=payload.client_id,
        note_id=payload.note_id,
        position=payload.position,
    )
    return {"status": "recorded"}


@router.post("/events/feedback", status_code=201)
async def record_feedback(payload: RecordFeedbackRequest, request: Request) -> dict[str, str]:
    """Record user feedback (helpful/wrong/too_aggressive) for a result."""
    stats = request.app.state.stats_collector
    stats.record_feedback(
        experiment_id=payload.experiment_id,
        variant_id=payload.variant_id,
        feedback_type=payload.feedback_type,
        client_id=payload.client_id,
        note_id=payload.note_id,
    )
    return {"status": "recorded"}


# ---------------------------------------------------------------------------
# User-facing: what experiments am I in?
# ---------------------------------------------------------------------------


@router.get("/me/active", response_model=MyExperimentsResponse)
async def my_experiments(
    request: Request,
    client_id: str = "",
    user_id: str = "",
    org_slug: str = "",
) -> MyExperimentsResponse:
    """See which experiments/variants apply to the current user."""
    engine = request.app.state.experiment_engine
    active = engine.get_active_experiments_for_user(
        client_id=client_id,
        user_id=user_id,
        org_slug=org_slug,
    )
    return MyExperimentsResponse(active=active)
