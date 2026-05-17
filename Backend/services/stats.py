"""Event collection and statistical analysis for experiments.

Collects events (searches served, clicks, feedback) and computes
aggregate metrics per variant to determine winners.
"""

from __future__ import annotations

import math
import uuid
import logging
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from models.experiment import (
    EventType,
    ExperimentEvent,
    ExperimentReport,
    ExperimentStatus,
    VariantStats,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------


class StatsCollector:
    """Collects experiment events and computes aggregate statistics.

    In production, events would stream to a time-series DB or analytics
    warehouse. This in-memory implementation supports persistence to a
    JSON-lines file for durability across restarts.
    """

    def __init__(self, persist_path: str | None = None, max_events: int = 100_000) -> None:
        self._events: list[ExperimentEvent] = []
        self._lock = Lock()
        self._persist_path = persist_path
        self._max_events = max_events
        self._file_handle = None
        if persist_path:
            self._load_from_disk()

    # -- Event recording ----------------------------------------------------

    def record(self, event: ExperimentEvent) -> None:
        """Record a single experiment event."""
        if not event.event_id:
            event.event_id = str(uuid.uuid4())
        with self._lock:
            self._events.append(event)
            # Ring buffer: drop oldest events when at capacity
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]
            self._append_to_disk(event)

    def record_search(
        self,
        *,
        experiment_id: str,
        variant_id: str,
        client_id: str = "",
        user_id: str = "",
        org_slug: str = "",
        query_text: str = "",
        result_count: int = 0,
        latency_ms: float = 0.0,
        scores: dict[str, float] | None = None,
    ) -> str:
        """Convenience: record a search-served event. Returns event_id."""
        event = ExperimentEvent(
            experiment_id=experiment_id,
            variant_id=variant_id,
            event_type=EventType.SEARCH_SERVED,
            client_id=client_id,
            user_id=user_id,
            org_slug=org_slug,
            query_text=query_text,
            result_count=result_count,
            latency_ms=latency_ms,
            scores=scores or {},
        )
        self.record(event)
        return event.event_id

    def record_click(
        self,
        *,
        experiment_id: str,
        variant_id: str,
        client_id: str = "",
        user_id: str = "",
        note_id: str = "",
        position: int = 0,
    ) -> None:
        """Convenience: record a result-clicked event."""
        self.record(ExperimentEvent(
            experiment_id=experiment_id,
            variant_id=variant_id,
            event_type=EventType.RESULT_CLICKED,
            client_id=client_id,
            user_id=user_id,
            note_id=note_id,
            position=position,
        ))

    def record_feedback(
        self,
        *,
        experiment_id: str,
        variant_id: str,
        feedback_type: str,
        client_id: str = "",
        user_id: str = "",
        note_id: str = "",
    ) -> None:
        """Convenience: record a feedback event."""
        type_map = {
            "helpful": EventType.FEEDBACK_HELPFUL,
            "wrong": EventType.FEEDBACK_WRONG,
            "too_aggressive": EventType.FEEDBACK_SUPPRESSED,
        }
        event_type = type_map.get(feedback_type, EventType.FEEDBACK_WRONG)
        self.record(ExperimentEvent(
            experiment_id=experiment_id,
            variant_id=variant_id,
            event_type=event_type,
            client_id=client_id,
            user_id=user_id,
            note_id=note_id,
        ))

    # -- Querying -----------------------------------------------------------

    def get_events(
        self,
        experiment_id: str,
        *,
        variant_id: str = "",
        event_type: EventType | None = None,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[ExperimentEvent]:
        """Query events with optional filters."""
        results = []
        for event in reversed(self._events):
            if event.experiment_id != experiment_id:
                continue
            if variant_id and event.variant_id != variant_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if since is not None and event.timestamp < since:
                break
            results.append(event)
            if len(results) >= limit:
                break
        return results

    def event_count(self, experiment_id: str) -> int:
        """Total events for an experiment."""
        return sum(1 for e in self._events if e.experiment_id == experiment_id)

    # -- Aggregation --------------------------------------------------------

    def compute_variant_stats(
        self,
        experiment_id: str,
        variant_id: str,
        variant_name: str = "",
    ) -> VariantStats:
        """Compute aggregate stats for a single variant."""
        events = [
            e for e in self._events
            if e.experiment_id == experiment_id and e.variant_id == variant_id
        ]

        searches = [e for e in events if e.event_type == EventType.SEARCH_SERVED]
        clicks = [e for e in events if e.event_type == EventType.RESULT_CLICKED]
        helpful = [e for e in events if e.event_type == EventType.FEEDBACK_HELPFUL]
        wrong = [e for e in events if e.event_type == EventType.FEEDBACK_WRONG]
        suppressed = [e for e in events if e.event_type == EventType.FEEDBACK_SUPPRESSED]
        ignored = [e for e in events if e.event_type == EventType.RESULT_IGNORED]

        total_searches = len(searches)
        total_clicks = len(clicks)
        total_helpful = len(helpful)
        total_wrong = len(wrong)
        total_suppressed = len(suppressed)

        # Click-through rate
        ctr = total_clicks / total_searches if total_searches > 0 else 0.0

        # Precision@1: fraction of searches where position 0 was clicked
        search_ids_with_top_click = set()
        for click in clicks:
            if click.position == 0:
                search_ids_with_top_click.add(click.client_id + click.query_text)
        p_at_1 = len(search_ids_with_top_click) / total_searches if total_searches > 0 else 0.0

        # Mean Reciprocal Rank
        reciprocal_ranks = []
        for click in clicks:
            if click.position is not None:
                reciprocal_ranks.append(1.0 / (click.position + 1))
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0

        # NDCG@5 (simplified: treat clicks as relevant, position gives discount)
        ndcg = self._compute_ndcg(clicks, k=5) if clicks else 0.0

        # Average latency
        latencies = [e.latency_ms for e in searches if e.latency_ms and e.latency_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        # Helpful rate
        total_feedback = total_helpful + total_wrong + total_suppressed
        helpful_rate = total_helpful / total_feedback if total_feedback > 0 else 0.0

        # 95% confidence interval for CTR (Wilson score)
        ci = self._wilson_ci(total_clicks, total_searches)

        return VariantStats(
            variant_id=variant_id,
            variant_name=variant_name,
            total_searches=total_searches,
            total_clicks=total_clicks,
            total_helpful=total_helpful,
            total_wrong=total_wrong,
            total_suppressed=total_suppressed,
            total_ignored=len(ignored),
            click_through_rate=round(ctr, 4),
            precision_at_1=round(p_at_1, 4),
            mean_reciprocal_rank=round(mrr, 4),
            ndcg_at_5=round(ndcg, 4),
            avg_latency_ms=round(avg_latency, 2),
            helpful_rate=round(helpful_rate, 4),
            sample_size=total_searches,
            confidence_interval=(round(ci[0], 4), round(ci[1], 4)),
        )

    def generate_report(self, experiment: Any) -> ExperimentReport:
        """Generate a full comparison report for an experiment."""
        variant_stats = []
        for variant in experiment.variants:
            stats = self.compute_variant_stats(
                experiment.id,
                variant.id,
                variant.name,
            )
            variant_stats.append(stats)

        # Determine winner (highest CTR with statistical significance)
        winner = None
        confidence = 0.0
        recommendation = ""

        if len(variant_stats) >= 2:
            sorted_stats = sorted(variant_stats, key=lambda s: s.click_through_rate, reverse=True)
            best = sorted_stats[0]
            runner_up = sorted_stats[1]

            if best.sample_size >= 30 and runner_up.sample_size >= 30:
                # Z-test for two proportions
                z, p_value = self._z_test_proportions(
                    best.total_clicks, best.total_searches,
                    runner_up.total_clicks, runner_up.total_searches,
                )
                confidence = 1.0 - p_value
                if confidence >= 0.95:
                    winner = best.variant_id
                    lift = (
                        (best.click_through_rate - runner_up.click_through_rate)
                        / runner_up.click_through_rate * 100
                        if runner_up.click_through_rate > 0 else 0
                    )
                    recommendation = (
                        f"Variant '{best.variant_name}' wins with {confidence:.1%} confidence. "
                        f"CTR lift: +{lift:.1f}%. Recommend promoting to 100% traffic."
                    )
                else:
                    recommendation = (
                        f"No significant winner yet ({confidence:.1%} confidence). "
                        f"Need more data — current sample: {best.sample_size} vs {runner_up.sample_size}."
                    )
            else:
                min_needed = 30
                recommendation = (
                    f"Insufficient data. Need at least {min_needed} searches per variant. "
                    f"Current: {best.sample_size} vs {runner_up.sample_size}."
                )

        # Duration
        duration_hours = 0.0
        if experiment.started_at:
            end = experiment.ended_at or datetime.now(timezone.utc)
            duration_hours = (end - experiment.started_at).total_seconds() / 3600.0

        return ExperimentReport(
            experiment_id=experiment.id,
            experiment_name=experiment.name,
            status=experiment.status,
            duration_hours=round(duration_hours, 1),
            variants=variant_stats,
            winner=winner,
            confidence=round(confidence, 4),
            recommendation=recommendation,
        )

    # -- Statistical helpers ------------------------------------------------

    @staticmethod
    def _wilson_ci(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
        """Wilson score confidence interval for a proportion."""
        if trials == 0:
            return (0.0, 0.0)
        # Clamp: can't have more successes than trials for a proportion
        p = min(successes, trials) / trials
        denominator = 1 + z * z / trials
        center = p + z * z / (2 * trials)
        spread = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
        lower = (center - spread) / denominator
        upper = (center + spread) / denominator
        return (max(0.0, lower), min(1.0, upper))

    @staticmethod
    def _z_test_proportions(
        successes_a: int, trials_a: int,
        successes_b: int, trials_b: int,
    ) -> tuple[float, float]:
        """Two-proportion z-test. Returns (z_stat, p_value)."""
        if trials_a == 0 or trials_b == 0:
            return (0.0, 1.0)
        p_a = successes_a / trials_a
        p_b = successes_b / trials_b
        p_pool = (successes_a + successes_b) / (trials_a + trials_b)
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / trials_a + 1 / trials_b))
        if se == 0:
            return (0.0, 1.0)
        z = (p_a - p_b) / se
        # Approximate p-value using normal CDF
        p_value = 0.5 * math.erfc(abs(z) / math.sqrt(2))
        return (z, p_value)

    @staticmethod
    def _compute_ndcg(clicks: list[ExperimentEvent], k: int = 5) -> float:
        """Compute a simplified NDCG@k from click events."""
        if not clicks:
            return 0.0
        # DCG: sum 1/log2(position+2) for each click within top-k
        dcg = 0.0
        count = 0
        for click in clicks:
            if click.position is not None and click.position < k:
                dcg += 1.0 / math.log2(click.position + 2)
                count += 1
        if count == 0:
            return 0.0
        # Ideal DCG: if all clicks were at position 0, 1, 2, ...
        idcg = sum(1.0 / math.log2(i + 2) for i in range(min(count, k)))
        return dcg / idcg if idcg > 0 else 0.0

    # -- Persistence --------------------------------------------------------

    def _append_to_disk(self, event: ExperimentEvent) -> None:
        if not self._persist_path:
            return
        try:
            path = Path(self._persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(event.model_dump(mode="json"), default=str) + "\n")
        except Exception as exc:
            logger.warning("Failed to persist event: %s", exc)

    def _load_from_disk(self) -> None:
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        if not path.exists():
            return
        try:
            count = 0
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._events.append(ExperimentEvent.model_validate_json(line))
                        count += 1
            # Apply ring buffer limit
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]
            logger.info("Loaded %d experiment events from disk", count)
        except Exception as exc:
            logger.warning("Failed to load events from disk: %s", exc)
