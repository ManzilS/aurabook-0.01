"""In-memory cache for Ghost Pre-Fetch and semantic search results."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field

from aurabook.core.models import CommunityPrefetchMatch, CommunitySearchResponse
from aurabook.services.status import RuntimeStatusStore


@dataclass(frozen=True, slots=True)
class FileHashEntry:
    """One observed file hash and optional prefetched match."""

    path: Path
    cdc_hash: str
    observed_at: float
    match: CommunityPrefetchMatch | None = None


class PersistedFileHashEntry(BaseModel):
    """Serialized file hash entry."""

    path: str
    cdc_hash: str
    observed_at: float
    match: CommunityPrefetchMatch | None = None

    model_config = ConfigDict(extra="forbid")


class PersistedSearchEntry(BaseModel):
    """Serialized semantic cache entry."""

    query_key: str
    expires_at: float
    response: CommunitySearchResponse

    model_config = ConfigDict(extra="forbid")


class PersistedCommunityCache(BaseModel):
    """On-disk representation of the community cache."""

    file_hashes: list[PersistedFileHashEntry] = Field(default_factory=list)
    search_entries: list[PersistedSearchEntry] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class CommunityMemoryStore:
    """Thread-safe in-memory storage for recent project context."""

    def __init__(
        self,
        *,
        max_file_hashes: int = 100,
        max_search_entries: int = 128,
        status_store: RuntimeStatusStore | None = None,
        persist_path: Path | None = None,
    ) -> None:
        self._lock = Lock()
        self._max_file_hashes = max_file_hashes
        self._max_search_entries = max_search_entries
        self._status_store = status_store
        self._persist_path = persist_path
        self._file_hashes: list[FileHashEntry] = []
        self._search_cache: dict[str, tuple[float, CommunitySearchResponse]] = {}
        self._metrics = {
            "prefetch_entries": 0,
            "search_entries": 0,
            "prefetch_evictions": 0,
            "search_evictions": 0,
            "exact_match_hits": 0,
            "search_cache_hits": 0,
            "search_cache_misses": 0,
        }
        self._load_persisted_cache()

    def store_prefetch_result(
        self,
        *,
        path: Path,
        cdc_hash: str,
        match: CommunityPrefetchMatch | None,
    ) -> None:
        """Store the latest file hash and any exact match returned by the cloud."""
        now = time.time()
        entry = FileHashEntry(path=path, cdc_hash=cdc_hash, observed_at=now, match=match)
        with self._lock:
            self._file_hashes.append(entry)
            if len(self._file_hashes) > self._max_file_hashes:
                evictions = len(self._file_hashes) - self._max_file_hashes
                self._file_hashes = self._file_hashes[-self._max_file_hashes :]
                self._metrics["prefetch_evictions"] += evictions
            self._metrics["prefetch_entries"] = len(self._file_hashes)
            self._sync_status_locked(path=path, cdc_hash=cdc_hash, exact_match=match is not None)
            self._persist_locked()

    def recent_exact_matches(
        self,
        *,
        max_age_s: float,
        limit: int = 3,
    ) -> list[CommunityPrefetchMatch]:
        """Return recent unique exact-match results."""
        cutoff = time.time() - max_age_s
        with self._lock:
            self._prune_file_hashes(cutoff)
            seen: set[str] = set()
            matches: list[CommunityPrefetchMatch] = []
            for entry in reversed(self._file_hashes):
                if entry.match is None:
                    continue
                if entry.match.trajectory_id in seen:
                    continue
                seen.add(entry.match.trajectory_id)
                matches.append(entry.match)
                if len(matches) >= limit:
                    break
            if matches:
                self._metrics["exact_match_hits"] += 1
                self._sync_status_locked()
                self._persist_locked()
            return matches

    def latest_cdc_hash(self, *, max_age_s: float) -> str | None:
        """Return the latest recent file hash."""
        cutoff = time.time() - max_age_s
        with self._lock:
            self._prune_file_hashes(cutoff)
            if not self._file_hashes:
                return None
            return self._file_hashes[-1].cdc_hash

    def get_search_response(self, query_key: str) -> CommunitySearchResponse | None:
        """Return a cached semantic search response when it is still fresh."""
        now = time.time()
        with self._lock:
            cached = self._search_cache.get(query_key)
            if cached is None:
                self._metrics["search_cache_misses"] += 1
                self._sync_status_locked()
                return None
            expires_at, response = cached
            if expires_at <= now:
                self._search_cache.pop(query_key, None)
                self._metrics["search_entries"] = len(self._search_cache)
                self._metrics["search_cache_misses"] += 1
                self._sync_status_locked()
                self._persist_locked()
                return None
            self._metrics["search_cache_hits"] += 1
            self._sync_status_locked()
            return response

    def store_search_response(
        self,
        query_key: str,
        response: CommunitySearchResponse,
        *,
        ttl_s: float,
    ) -> None:
        """Cache a semantic search response for a short period."""
        expires_at = time.time() + ttl_s
        with self._lock:
            self._search_cache[query_key] = (expires_at, response)
            while len(self._search_cache) > self._max_search_entries:
                oldest_key = next(iter(self._search_cache))
                self._search_cache.pop(oldest_key, None)
                self._metrics["search_evictions"] += 1
            self._metrics["search_entries"] = len(self._search_cache)
            self._sync_status_locked()
            self._persist_locked()

    def recent_file_hashes(
        self,
        *,
        max_age_s: float,
        limit: int = 5,
    ) -> list[FileHashEntry]:
        """Return recent observed file hashes for status rendering."""
        cutoff = time.time() - max_age_s
        with self._lock:
            self._prune_file_hashes(cutoff)
            return list(self._file_hashes[-limit:])

    def metrics_snapshot(self) -> dict[str, int]:
        """Return a copy of the current cache metrics."""
        with self._lock:
            self._metrics["prefetch_entries"] = len(self._file_hashes)
            self._metrics["search_entries"] = len(self._search_cache)
            return dict(self._metrics)

    def _prune_file_hashes(self, cutoff: float) -> None:
        self._file_hashes = [entry for entry in self._file_hashes if entry.observed_at >= cutoff]
        self._metrics["prefetch_entries"] = len(self._file_hashes)

    def _sync_status_locked(
        self,
        *,
        path: Path | None = None,
        cdc_hash: str | None = None,
        exact_match: bool | None = None,
    ) -> None:
        if self._status_store is None:
            return
        self._status_store.update_cache_metrics(dict(self._metrics))
        if path is not None and cdc_hash is not None and exact_match is not None:
            self._status_store.record_prefetch(
                path=path,
                cdc_hash=cdc_hash,
                exact_match=exact_match,
            )

    def _load_persisted_cache(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            payload = PersistedCommunityCache.model_validate(raw)
        except (OSError, ValueError):
            return
        now = time.time()
        self._file_hashes = [
            FileHashEntry(
                path=Path(entry.path),
                cdc_hash=entry.cdc_hash,
                observed_at=entry.observed_at,
                match=entry.match,
            )
            for entry in payload.file_hashes
        ]
        self._search_cache = {
            entry.query_key: (entry.expires_at, entry.response)
            for entry in payload.search_entries
            if entry.expires_at > now
        }
        self._metrics["prefetch_entries"] = len(self._file_hashes)
        self._metrics["search_entries"] = len(self._search_cache)

    def _persist_locked(self) -> None:
        if self._persist_path is None:
            return
        payload = PersistedCommunityCache(
            file_hashes=[
                PersistedFileHashEntry(
                    path=str(entry.path),
                    cdc_hash=entry.cdc_hash,
                    observed_at=entry.observed_at,
                    match=entry.match,
                )
                for entry in self._file_hashes
            ],
            search_entries=[
                PersistedSearchEntry(
                    query_key=query_key,
                    expires_at=expires_at,
                    response=response,
                )
                for query_key, (expires_at, response) in self._search_cache.items()
            ],
        )
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(payload.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
