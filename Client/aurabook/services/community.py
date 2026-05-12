"""Community context coordination between the local proxy and the Cloud Brain."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from aurabook.core.models import (
    CommunityPrefetchMatch,
    CommunitySearchResponse,
    CommunitySearchResult,
    ResponseGradient,
)
from aurabook.services.cloud_client import CloudBrainClient
from aurabook.services.memory import CommunityMemoryStore
from aurabook.services.status import RuntimeStatusStore
from aurabook.utils.config import Settings
from aurabook.utils.errors import CloudBrainError

logger = logging.getLogger(__name__)

_ERROR_SIGNAL_PATTERN = re.compile(r".*\b(?:error|exception|traceback|warning|failed)\b.*", re.IGNORECASE)
_GRADIENT_ORDER: dict[ResponseGradient, int] = {
    ResponseGradient.PASS_THROUGH: 0,
    ResponseGradient.NOTES: 1,
    ResponseGradient.GUIDE: 2,
    ResponseGradient.WALKTHROUGH: 3,
    ResponseGradient.FULL: 4,
}


@dataclass(frozen=True, slots=True)
class CommunityContext:
    """Resolved community context for one user prompt."""

    gradient: ResponseGradient
    source: str = "none"
    exact_match: CommunityPrefetchMatch | None = None
    search_match: CommunitySearchResult | None = None


class CommunityContextService:
    """Resolves exact-match and semantic community context."""

    def __init__(
        self,
        settings: Settings,
        *,
        cloud_client: CloudBrainClient | None,
        memory_store: CommunityMemoryStore,
        status_store: RuntimeStatusStore | None = None,
        suppressed_note_ids: tuple[str, ...] = (),
        suppressed_trajectory_ids: tuple[str, ...] = (),
        repository_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._cloud_client = cloud_client
        self._memory_store = memory_store
        self._status_store = status_store
        self._suppressed_note_ids = set(suppressed_note_ids)
        self._suppressed_trajectory_ids = set(suppressed_trajectory_ids)
        self._repository_root = repository_root

    async def prefetch_saved_file(self, path: Path, cdc_hash: str) -> None:
        """Look up an exact hash match after a local file save."""
        match = None
        if self._settings.cloud_enabled and self._cloud_client is not None:
            try:
                response = await self._cloud_client.prefetch(cdc_hash)
                match = response.match if response.found else None
            except CloudBrainError as exc:
                logger.error("Ghost Pre-Fetch failed for %s: %s", path, exc)

        self._memory_store.store_prefetch_result(path=path, cdc_hash=cdc_hash, match=match)

    async def resolve_context(self, prompt: str) -> CommunityContext:
        """Resolve the best available community context for a prompt."""
        exact_match = self._latest_exact_match()
        search_match = None
        source = "none"

        if exact_match is not None:
            if self._is_suppressed_match(exact_match):
                exact_match = None
            else:
                source = "prefetch_cache"
        elif self._settings.cloud_enabled and self._cloud_client is not None:
            query_key = self._normalize_query_key(prompt)
            search_response = self._memory_store.get_search_response(query_key)
            if search_response is None:
                try:
                    search_response = await self._cloud_client.search(
                        prompt=prompt,
                        error_log=self._extract_error_log(prompt),
                        limit=5,
                    )
                    self._memory_store.store_search_response(
                        query_key,
                        search_response,
                        ttl_s=self._settings.search_cache_ttl_s,
                    )
                    source = "search_remote"
                except CloudBrainError as exc:
                    logger.error("Semantic search failed: %s", exc)
                    search_response = CommunitySearchResponse()
                    source = "none"
            else:
                source = "search_cache"
            matches = [
                match
                for match in search_response.matches
                if not self._is_suppressed_match(match)
            ]
            search_match = matches[0] if matches else None

        gradient = self._select_gradient(exact_match=exact_match, search_match=search_match)
        context = CommunityContext(
            gradient=gradient,
            source=source,
            exact_match=exact_match,
            search_match=search_match,
        )
        if self._status_store is not None:
            self._status_store.record_search(
                source=source,
                gradient=context.gradient.value,
                similarity=None if search_match is None else search_match.similarity,
            )
        return context

    def repository_root(self) -> Path | None:
        """Return the repository root used for repo-specific policy decisions."""
        return self._repository_root

    def suppress_note(self, note_id: str) -> None:
        """Suppress one note for the lifetime of the current runtime."""
        self._suppressed_note_ids.add(note_id)

    def suppress_trajectory(self, trajectory_id: str) -> None:
        """Suppress one trajectory for the lifetime of the current runtime."""
        self._suppressed_trajectory_ids.add(trajectory_id)

    def record_intervention(
        self,
        *,
        gradient: ResponseGradient | str,
        source: str,
        note_id: str | None = None,
        trajectory_id: str | None = None,
        feedback: str | None = None,
    ) -> None:
        """Persist one intervention event into local status history."""
        if self._status_store is None:
            return
        gradient_value = gradient.value if isinstance(gradient, ResponseGradient) else gradient
        self._status_store.record_intervention(
            gradient=gradient_value,
            source=source,
            note_id=note_id,
            trajectory_id=trajectory_id,
            repository=None if self._repository_root is None else str(self._repository_root),
            feedback=feedback,
        )

    def latest_cdc_hash(self) -> str | None:
        """Return the latest file hash observed by Ghost Pre-Fetch."""
        return self._memory_store.latest_cdc_hash(max_age_s=self._settings.recent_hash_window_s)

    def _latest_exact_match(self) -> CommunityPrefetchMatch | None:
        matches = self._memory_store.recent_exact_matches(
            max_age_s=self._settings.recent_hash_window_s,
            limit=1,
        )
        if not matches:
            return None
        return matches[0]

    def _normalize_query_key(self, prompt: str) -> str:
        return " ".join(prompt.lower().split())

    def _extract_error_log(self, prompt: str) -> str | None:
        lines = [line.strip() for line in prompt.splitlines() if _ERROR_SIGNAL_PATTERN.match(line)]
        if not lines:
            return None
        return "\n".join(lines)

    def _is_suppressed_match(
        self,
        match: CommunityPrefetchMatch | CommunitySearchResult,
    ) -> bool:
        note_id = getattr(match, "note_id", None)
        trajectory_id = getattr(match, "trajectory_id", None)
        if note_id and note_id in self._suppressed_note_ids:
            return True
        if trajectory_id and trajectory_id in self._suppressed_trajectory_ids:
            return True
        return False

    def _select_gradient(
        self,
        *,
        exact_match: CommunityPrefetchMatch | None,
        search_match: CommunitySearchResult | None,
    ) -> ResponseGradient:
        max_gradient = ResponseGradient(self._settings.max_gradient)
        for candidate in (
            ResponseGradient.FULL,
            ResponseGradient.WALKTHROUGH,
            ResponseGradient.GUIDE,
            ResponseGradient.NOTES,
        ):
            if _GRADIENT_ORDER[candidate] > _GRADIENT_ORDER[max_gradient]:
                continue
            if self._supports_gradient(candidate, exact_match=exact_match, search_match=search_match):
                return candidate
        return ResponseGradient.PASS_THROUGH

    def _supports_gradient(
        self,
        gradient: ResponseGradient,
        *,
        exact_match: CommunityPrefetchMatch | None,
        search_match: CommunitySearchResult | None,
    ) -> bool:
        if gradient == ResponseGradient.FULL:
            return exact_match is not None and bool(exact_match.final_fix.strip())
        if gradient == ResponseGradient.WALKTHROUGH:
            if exact_match is not None and exact_match.final_fix.strip():
                return True
            return (
                search_match is not None
                and search_match.similarity >= self._settings.walkthrough_similarity_threshold
                and bool((search_match.final_fix or "").strip())
            )
        if gradient == ResponseGradient.GUIDE:
            if exact_match is not None and (exact_match.final_fix.strip() or (exact_match.note_text or "").strip()):
                return True
            return (
                search_match is not None
                and search_match.similarity >= self._settings.guide_similarity_threshold
                and bool((search_match.final_fix or search_match.note_text or "").strip())
            )
        if gradient == ResponseGradient.NOTES:
            if exact_match is not None and bool((exact_match.note_text or "").strip()):
                return True
            return (
                search_match is not None
                and search_match.similarity >= self._settings.notes_similarity_threshold
                and bool(search_match.note_text.strip())
            )
        return False
