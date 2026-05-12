"""Tests for community memory cache metrics and eviction."""

from __future__ import annotations

from aurabook.core.models import CommunityPrefetchMatch, CommunitySearchResponse
from aurabook.services.memory import CommunityMemoryStore


def test_memory_store_tracks_evictions_and_cache_hits(tmp_path) -> None:
    store = CommunityMemoryStore(max_file_hashes=2, max_search_entries=1)
    match = CommunityPrefetchMatch(
        trajectory_id="traj-1",
        cdc_hash="hash-1",
        final_fix="fixed()",
    )

    store.store_prefetch_result(path=tmp_path / "a.py", cdc_hash="hash-1", match=match)
    store.store_prefetch_result(path=tmp_path / "b.py", cdc_hash="hash-2", match=None)
    store.store_prefetch_result(path=tmp_path / "c.py", cdc_hash="hash-3", match=None)
    store.store_search_response("one", CommunitySearchResponse(), ttl_s=60)
    store.store_search_response("two", CommunitySearchResponse(), ttl_s=60)
    assert store.get_search_response("two") is not None
    assert store.get_search_response("missing") is None
    assert store.recent_file_hashes(max_age_s=60, limit=5)[0].cdc_hash == "hash-2"

    metrics = store.metrics_snapshot()

    assert metrics["prefetch_evictions"] == 1
    assert metrics["search_evictions"] == 1
    assert metrics["search_cache_hits"] == 1
    assert metrics["search_cache_misses"] == 1


def test_memory_store_persists_prefetch_and_search_entries(tmp_path) -> None:
    persist_path = tmp_path / "cache.json"
    store = CommunityMemoryStore(persist_path=persist_path)
    match = CommunityPrefetchMatch(
        trajectory_id="traj-1",
        cdc_hash="hash-1",
        note_id="note-1",
        final_fix="fixed()",
    )
    search_response = CommunitySearchResponse()

    store.store_prefetch_result(path=tmp_path / "a.py", cdc_hash="hash-1", match=match)
    store.store_search_response("query", search_response, ttl_s=60)

    reloaded = CommunityMemoryStore(persist_path=persist_path)

    assert reloaded.latest_cdc_hash(max_age_s=60) == "hash-1"
    assert reloaded.get_search_response("query") is not None
