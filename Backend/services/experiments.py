"""Experiment management: variant assignment, feature flags, and lifecycle.

Handles the full experiment lifecycle:
- Define experiments with multiple variants (scoring pipelines)
- Assign users/clients to variants deterministically (hash-based)
- Support allowlists, org-based targeting, and percentage rollouts
- Graceful fallback to default pipeline when no experiment matches
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from models.experiment import (
    Experiment,
    ExperimentStatus,
    RolloutStrategy,
    ScoringPipeline,
    Variant,
)
from services.evaluation import default_pipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory store (swap for DB-backed store in production)
# ---------------------------------------------------------------------------


class ExperimentStore:
    """Thread-safe in-memory experiment store with optional file persistence.

    In production this would be backed by Postgres, but the in-memory version
    lets us iterate fast and still persist state across restarts via JSON file.
    """

    def __init__(self, persist_path: str | None = None) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._lock = Lock()
        self._persist_path = persist_path
        if persist_path:
            self._load_from_disk()

    # -- CRUD ---------------------------------------------------------------

    def create(self, experiment: Experiment) -> Experiment:
        with self._lock:
            if experiment.id in self._experiments:
                raise ValueError(f"Experiment '{experiment.id}' already exists")
            self._experiments[experiment.id] = experiment
            self._persist()
            return experiment

    def get(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def list_all(self, status: ExperimentStatus | None = None) -> list[Experiment]:
        exps = list(self._experiments.values())
        if status is not None:
            exps = [e for e in exps if e.status == status]
        return sorted(exps, key=lambda e: e.created_at, reverse=True)

    def update(self, experiment: Experiment) -> Experiment:
        with self._lock:
            if experiment.id not in self._experiments:
                raise ValueError(f"Experiment '{experiment.id}' not found")
            self._experiments[experiment.id] = experiment
            self._persist()
            return experiment

    def delete(self, experiment_id: str) -> bool:
        with self._lock:
            if experiment_id in self._experiments:
                del self._experiments[experiment_id]
                self._persist()
                return True
            return False

    def start(self, experiment_id: str) -> Experiment:
        exp = self.get(experiment_id)
        if exp is None:
            raise ValueError(f"Experiment '{experiment_id}' not found")
        exp.status = ExperimentStatus.RUNNING
        exp.started_at = datetime.now(timezone.utc)
        return self.update(exp)

    def stop(self, experiment_id: str) -> Experiment:
        exp = self.get(experiment_id)
        if exp is None:
            raise ValueError(f"Experiment '{experiment_id}' not found")
        exp.status = ExperimentStatus.COMPLETED
        exp.ended_at = datetime.now(timezone.utc)
        return self.update(exp)

    def pause(self, experiment_id: str) -> Experiment:
        exp = self.get(experiment_id)
        if exp is None:
            raise ValueError(f"Experiment '{experiment_id}' not found")
        exp.status = ExperimentStatus.PAUSED
        return self.update(exp)

    # -- Persistence --------------------------------------------------------

    def _persist(self) -> None:
        if not self._persist_path:
            return
        try:
            path = Path(self._persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {eid: exp.model_dump(mode="json") for eid, exp in self._experiments.items()}
            path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.warning("Failed to persist experiments: %s", exc)

    def _load_from_disk(self) -> None:
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for eid, raw in data.items():
                self._experiments[eid] = Experiment.model_validate(raw)
            logger.info("Loaded %d experiments from disk", len(self._experiments))
        except Exception as exc:
            logger.warning("Failed to load experiments from disk: %s", exc)


# ---------------------------------------------------------------------------
# Variant assignment
# ---------------------------------------------------------------------------


def _hash_to_bucket(key: str, experiment_id: str) -> float:
    """Deterministically map a key to [0, 100) for percentage-based assignment."""
    digest = hashlib.sha256(f"{experiment_id}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % 10000 / 100.0


def assign_variant(
    experiment: Experiment,
    *,
    client_id: str = "",
    user_id: str = "",
    org_slug: str = "",
) -> Variant | None:
    """Determine which variant a request should see.

    Assignment logic by strategy:
    - ALLOWLIST: check if client_id or user_id is in a variant's allowlist
    - ORG: check if org_slug matches experiment.target_orgs
    - PERCENTAGE: hash-based deterministic bucket assignment

    Returns None if the experiment isn't running or user doesn't qualify.
    """
    if experiment.status != ExperimentStatus.RUNNING:
        return None

    strategy = experiment.rollout_strategy

    # Allowlist: check explicit inclusion first
    if strategy == RolloutStrategy.ALLOWLIST:
        for variant in experiment.variants:
            if client_id and client_id in variant.allowlist:
                return variant
            if user_id and user_id in variant.allowlist:
                return variant
        return None

    # Org-based: only applies if caller is in a target org
    if strategy == RolloutStrategy.ORG:
        if org_slug not in experiment.target_orgs:
            return None
        # Fall through to percentage assignment within org

    # Percentage-based (also used as sub-allocation for ORG strategy)
    key = client_id or user_id or ""
    if not key:
        return None

    bucket = _hash_to_bucket(key, experiment.id)

    cumulative = 0.0
    for variant in experiment.variants:
        cumulative += variant.traffic_percentage
        if bucket < cumulative:
            return variant

    # Shouldn't happen if percentages sum to 100, but fallback to control
    return experiment.variants[0] if experiment.variants else None


# ---------------------------------------------------------------------------
# Pipeline resolution
# ---------------------------------------------------------------------------


class ExperimentEngine:
    """Main engine that resolves the correct pipeline for a given request.

    Usage in the search endpoint:
        engine = request.app.state.experiment_engine
        pipeline, exp_id, var_id = engine.resolve_pipeline(client_id=..., ...)
        # Use pipeline to rank results
        # Record events with exp_id and var_id
    """

    def __init__(self, store: ExperimentStore) -> None:
        self.store = store

    def resolve_pipeline(
        self,
        *,
        client_id: str = "",
        user_id: str = "",
        org_slug: str = "",
        default_vector_weight: float = 0.65,
        default_lexical_weight: float = 0.25,
        default_quality_weight: float = 0.10,
    ) -> tuple[ScoringPipeline, str, str]:
        """Resolve which scoring pipeline to use for a request.

        Checks all running experiments and returns:
            (pipeline, experiment_id, variant_id)

        Falls back to the default pipeline with ("", "").
        """
        running = self.store.list_all(status=ExperimentStatus.RUNNING)

        for experiment in running:
            variant = assign_variant(
                experiment,
                client_id=client_id,
                user_id=user_id,
                org_slug=org_slug,
            )
            if variant is not None:
                return (variant.pipeline, experiment.id, variant.id)

        # No experiment applies → default pipeline
        return (
            default_pipeline(default_vector_weight, default_lexical_weight, default_quality_weight),
            "",
            "",
        )

    def get_active_experiments_for_user(
        self,
        *,
        client_id: str = "",
        user_id: str = "",
        org_slug: str = "",
    ) -> list[dict[str, str]]:
        """Return all experiments+variants a user is currently in."""
        result = []
        running = self.store.list_all(status=ExperimentStatus.RUNNING)
        for experiment in running:
            variant = assign_variant(
                experiment,
                client_id=client_id,
                user_id=user_id,
                org_slug=org_slug,
            )
            if variant is not None:
                result.append({
                    "experiment_id": experiment.id,
                    "experiment_name": experiment.name,
                    "variant_id": variant.id,
                    "variant_name": variant.name,
                })
        return result
