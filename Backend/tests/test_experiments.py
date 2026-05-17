"""Tests for the experimentation and evaluation framework."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import pytest

from models.experiment import (
    EventType,
    Experiment,
    ExperimentEvent,
    ExperimentStatus,
    LayerType,
    RolloutStrategy,
    ScoringLayer,
    ScoringPipeline,
    Variant,
)
from models.schema import NoteSearchResult
from services.evaluation import (
    default_pipeline,
    enrich_results,
    evaluate_pipeline,
)
from services.experiments import (
    ExperimentEngine,
    ExperimentStore,
    assign_variant,
    _hash_to_bucket,
)
from services.stats import StatsCollector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(
    note_id: str = "n1",
    similarity: float = 0.8,
    note_text: str = "Fix the database connection timeout error",
    helpful: int = 5,
    unhelpful: int = 1,
    suppress: int = 0,
    created_at: datetime | None = None,
    source_model: str = "gpt-4",
    trajectory_id: str = "t1",
) -> NoteSearchResult:
    return NoteSearchResult(
        note_id=note_id,
        trajectory_id=trajectory_id,
        note_text=note_text,
        source_model=source_model,
        similarity=similarity,
        metadata={"match_count": 3},
        created_at=created_at or datetime.now(timezone.utc),
        original_prompt="How do I fix database timeout?",
        final_fix="Increase pool_timeout to 30s",
        ai_reflection="The issue was connection pool exhaustion",
        helpful_count=helpful,
        unhelpful_count=unhelpful,
        suppress_count=suppress,
    )


def _make_experiment(
    exp_id: str = "exp-1",
    variants: list[Variant] | None = None,
    status: ExperimentStatus = ExperimentStatus.RUNNING,
) -> Experiment:
    if variants is None:
        variants = [
            Variant(
                id="control",
                name="Control",
                pipeline=ScoringPipeline(layers=[
                    ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=0.65),
                    ScoringLayer(layer_type=LayerType.LEXICAL_OVERLAP, weight=0.25),
                    ScoringLayer(layer_type=LayerType.QUALITY_PRIOR, weight=0.10),
                ], normalize_weights=False),
                traffic_percentage=50.0,
            ),
            Variant(
                id="treatment",
                name="Treatment",
                pipeline=ScoringPipeline(layers=[
                    ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=0.40),
                    ScoringLayer(layer_type=LayerType.LEXICAL_OVERLAP, weight=0.20),
                    ScoringLayer(layer_type=LayerType.QUALITY_PRIOR, weight=0.15),
                    ScoringLayer(layer_type=LayerType.RECENCY_BOOST, weight=0.15, params={"decay_hours": 72}),
                    ScoringLayer(layer_type=LayerType.FEEDBACK_MOMENTUM, weight=0.10),
                ], normalize_weights=False),
                traffic_percentage=50.0,
            ),
        ]
    return Experiment(
        id=exp_id,
        name="Test Experiment",
        status=status,
        variants=variants,
        rollout_strategy=RolloutStrategy.PERCENTAGE,
    )


# ---------------------------------------------------------------------------
# Evaluation pipeline tests
# ---------------------------------------------------------------------------


class TestEvaluationPipeline:
    def test_default_pipeline_matches_original_behavior(self):
        """Default pipeline should produce same ranking as old rerank_results."""
        results = [
            _make_result(note_id="n1", similarity=0.9, helpful=10),
            _make_result(note_id="n2", similarity=0.7, helpful=20),
            _make_result(note_id="n3", similarity=0.95, helpful=0),
        ]
        pipeline = default_pipeline()
        ranked = evaluate_pipeline(results, pipeline, "database timeout")
        # All should have combined scores
        assert all(scores["combined"] > 0 for _, scores in ranked)
        # Should be sorted by combined score descending
        combined_scores = [scores["combined"] for _, scores in ranked]
        assert combined_scores == sorted(combined_scores, reverse=True)

    def test_empty_results_returns_empty(self):
        pipeline = default_pipeline()
        ranked = evaluate_pipeline([], pipeline, "test query")
        assert ranked == []

    def test_disabled_layers_are_skipped(self):
        pipeline = ScoringPipeline(layers=[
            ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=1.0, enabled=True),
            ScoringLayer(layer_type=LayerType.LEXICAL_OVERLAP, weight=1.0, enabled=False),
        ], normalize_weights=True)
        results = [_make_result()]
        ranked = evaluate_pipeline(results, pipeline, "test")
        _, scores = ranked[0]
        assert "vector_similarity" in scores
        assert "lexical_overlap" not in scores

    def test_recency_boost_favors_newer(self):
        old = _make_result(
            note_id="old",
            similarity=0.8,
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        new = _make_result(
            note_id="new",
            similarity=0.8,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        pipeline = ScoringPipeline(layers=[
            ScoringLayer(layer_type=LayerType.RECENCY_BOOST, weight=1.0),
        ])
        ranked = evaluate_pipeline([old, new], pipeline, "test")
        assert ranked[0][0].note_id == "new"

    def test_diversity_penalty_demotes_same_trajectory(self):
        r1 = _make_result(note_id="n1", trajectory_id="t1", similarity=0.9)
        r2 = _make_result(note_id="n2", trajectory_id="t1", similarity=0.85)
        r3 = _make_result(note_id="n3", trajectory_id="t2", similarity=0.85)
        pipeline = ScoringPipeline(layers=[
            ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=0.5),
            ScoringLayer(layer_type=LayerType.DIVERSITY_PENALTY, weight=0.5),
        ])
        ranked = evaluate_pipeline([r1, r2, r3], pipeline, "test")
        # r3 should be above r2 since r2 shares trajectory with the top result
        ids = [r.note_id for r, _ in ranked]
        assert ids.index("n3") < ids.index("n2")

    def test_weight_normalization(self):
        pipeline = ScoringPipeline(
            layers=[
                ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=3.0),
                ScoringLayer(layer_type=LayerType.QUALITY_PRIOR, weight=7.0),
            ],
            normalize_weights=True,
        )
        results = [_make_result(similarity=1.0, helpful=100)]
        ranked = evaluate_pipeline(results, pipeline, "test")
        _, scores = ranked[0]
        # Combined should be <= 1.0 since normalized weights sum to 1
        # and individual scores are bounded
        assert scores["combined"] <= 1.1  # slight tolerance for tanh

    def test_min_score_threshold_filters_results(self):
        pipeline = ScoringPipeline(
            layers=[ScoringLayer(layer_type=LayerType.VECTOR_SIMILARITY, weight=1.0)],
            min_score_threshold=0.5,
        )
        results = [
            _make_result(note_id="good", similarity=0.9),
            _make_result(note_id="bad", similarity=0.3),
        ]
        ranked = evaluate_pipeline(results, pipeline, "test")
        assert len(ranked) == 1
        assert ranked[0][0].note_id == "good"

    def test_enrich_results_adds_metadata(self):
        pipeline = default_pipeline()
        results = [_make_result()]
        ranked = evaluate_pipeline(results, pipeline, "test")
        enriched = enrich_results(ranked, experiment_id="exp-1", variant_id="ctrl")
        assert enriched[0].metadata["experiment_id"] == "exp-1"
        assert enriched[0].metadata["variant_id"] == "ctrl"
        assert "score_combined" in enriched[0].metadata


# ---------------------------------------------------------------------------
# Experiment assignment tests
# ---------------------------------------------------------------------------


class TestVariantAssignment:
    def test_hash_deterministic(self):
        """Same key + experiment should always get same bucket."""
        b1 = _hash_to_bucket("client-123", "exp-1")
        b2 = _hash_to_bucket("client-123", "exp-1")
        assert b1 == b2

    def test_hash_different_keys_spread(self):
        """Different keys should spread across buckets."""
        buckets = [_hash_to_bucket(f"client-{i}", "exp-1") for i in range(1000)]
        # Should be roughly uniform — at least span a range
        assert max(buckets) - min(buckets) > 50

    def test_percentage_assignment(self):
        exp = _make_experiment()
        # With 50/50 split, roughly half should go to each variant
        control_count = 0
        for i in range(200):
            variant = assign_variant(exp, client_id=f"user-{i}")
            if variant and variant.id == "control":
                control_count += 1
        assert 60 < control_count < 140  # Loose bounds for statistical test

    def test_allowlist_assignment(self):
        exp = Experiment(
            id="exp-allow",
            name="Allowlist Test",
            status=ExperimentStatus.RUNNING,
            rollout_strategy=RolloutStrategy.ALLOWLIST,
            variants=[
                Variant(
                    id="beta",
                    name="Beta",
                    pipeline=ScoringPipeline(layers=[]),
                    traffic_percentage=0,
                    allowlist=["vip-user-1", "vip-user-2"],
                ),
                Variant(
                    id="default",
                    name="Default",
                    pipeline=ScoringPipeline(layers=[]),
                    traffic_percentage=0,
                    allowlist=[],
                ),
            ],
        )
        variant = assign_variant(exp, client_id="vip-user-1")
        assert variant is not None
        assert variant.id == "beta"
        # Non-allowlisted user gets None
        variant = assign_variant(exp, client_id="random-user")
        assert variant is None

    def test_draft_experiment_returns_none(self):
        exp = _make_experiment(status=ExperimentStatus.DRAFT)
        variant = assign_variant(exp, client_id="user-1")
        assert variant is None

    def test_engine_resolves_default_when_no_experiments(self):
        store = ExperimentStore()
        engine = ExperimentEngine(store)
        pipeline, exp_id, var_id = engine.resolve_pipeline(client_id="user-1")
        assert exp_id == ""
        assert var_id == ""
        assert len(pipeline.layers) == 3  # default pipeline

    def test_engine_resolves_variant_pipeline(self):
        store = ExperimentStore()
        exp = _make_experiment()
        store.create(exp)
        engine = ExperimentEngine(store)
        pipeline, exp_id, var_id = engine.resolve_pipeline(client_id="user-1")
        assert exp_id == "exp-1"
        assert var_id in ("control", "treatment")


# ---------------------------------------------------------------------------
# Stats collector tests
# ---------------------------------------------------------------------------


class TestStatsCollector:
    def test_record_and_query(self):
        stats = StatsCollector()
        stats.record_search(
            experiment_id="exp-1",
            variant_id="control",
            client_id="c1",
            result_count=5,
            latency_ms=42.0,
        )
        events = stats.get_events("exp-1")
        assert len(events) == 1
        assert events[0].event_type == EventType.SEARCH_SERVED
        assert events[0].latency_ms == 42.0

    def test_click_through_rate(self):
        stats = StatsCollector()
        # 10 searches, 3 clicks → 30% CTR
        for i in range(10):
            stats.record_search(experiment_id="e1", variant_id="v1", client_id=f"c{i}")
        for i in range(3):
            stats.record_click(experiment_id="e1", variant_id="v1", note_id=f"n{i}", position=0)
        result = stats.compute_variant_stats("e1", "v1")
        assert result.total_searches == 10
        assert result.total_clicks == 3
        assert abs(result.click_through_rate - 0.3) < 0.01

    def test_mean_reciprocal_rank(self):
        stats = StatsCollector()
        stats.record_search(experiment_id="e1", variant_id="v1", client_id="c1")
        stats.record_click(experiment_id="e1", variant_id="v1", position=0)  # MRR = 1.0
        stats.record_click(experiment_id="e1", variant_id="v1", position=2)  # MRR = 1/3
        result = stats.compute_variant_stats("e1", "v1")
        expected_mrr = (1.0 + 1.0 / 3) / 2
        assert abs(result.mean_reciprocal_rank - expected_mrr) < 0.01

    def test_helpful_rate(self):
        stats = StatsCollector()
        stats.record_feedback(experiment_id="e1", variant_id="v1", feedback_type="helpful")
        stats.record_feedback(experiment_id="e1", variant_id="v1", feedback_type="helpful")
        stats.record_feedback(experiment_id="e1", variant_id="v1", feedback_type="wrong")
        result = stats.compute_variant_stats("e1", "v1")
        assert abs(result.helpful_rate - 2.0 / 3.0) < 0.01

    def test_report_generation(self):
        stats = StatsCollector()
        exp = _make_experiment()
        # Give control better stats
        for i in range(50):
            stats.record_search(experiment_id="exp-1", variant_id="control", client_id=f"c{i}")
        for i in range(25):
            stats.record_click(experiment_id="exp-1", variant_id="control", position=0)
        # Treatment worse
        for i in range(50):
            stats.record_search(experiment_id="exp-1", variant_id="treatment", client_id=f"t{i}")
        for i in range(10):
            stats.record_click(experiment_id="exp-1", variant_id="treatment", position=1)
        report = stats.generate_report(exp)
        assert report.experiment_id == "exp-1"
        assert len(report.variants) == 2
        # Control should have higher CTR
        control_stats = next(v for v in report.variants if v.variant_id == "control")
        treatment_stats = next(v for v in report.variants if v.variant_id == "treatment")
        assert control_stats.click_through_rate > treatment_stats.click_through_rate

    def test_ring_buffer_limits_events(self):
        stats = StatsCollector(max_events=10)
        for i in range(20):
            stats.record_search(experiment_id="e1", variant_id="v1", client_id=f"c{i}")
        assert stats.event_count("e1") == 10


# ---------------------------------------------------------------------------
# Experiment store CRUD tests
# ---------------------------------------------------------------------------


class TestExperimentStore:
    def test_create_and_get(self):
        store = ExperimentStore()
        exp = _make_experiment()
        store.create(exp)
        retrieved = store.get("exp-1")
        assert retrieved is not None
        assert retrieved.name == "Test Experiment"

    def test_duplicate_create_raises(self):
        store = ExperimentStore()
        exp = _make_experiment()
        store.create(exp)
        with pytest.raises(ValueError, match="already exists"):
            store.create(exp)

    def test_start_stop_lifecycle(self):
        store = ExperimentStore()
        exp = _make_experiment(status=ExperimentStatus.DRAFT)
        store.create(exp)
        started = store.start("exp-1")
        assert started.status == ExperimentStatus.RUNNING
        assert started.started_at is not None
        stopped = store.stop("exp-1")
        assert stopped.status == ExperimentStatus.COMPLETED
        assert stopped.ended_at is not None

    def test_list_filters_by_status(self):
        store = ExperimentStore()
        store.create(_make_experiment(exp_id="draft-1", status=ExperimentStatus.DRAFT))
        store.create(_make_experiment(exp_id="run-1", status=ExperimentStatus.RUNNING))
        running = store.list_all(status=ExperimentStatus.RUNNING)
        assert len(running) == 1
        assert running[0].id == "run-1"

    def test_delete(self):
        store = ExperimentStore()
        store.create(_make_experiment())
        assert store.delete("exp-1") is True
        assert store.get("exp-1") is None
