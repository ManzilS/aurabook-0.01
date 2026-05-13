"""Connectivity orchestration for AuraBook.

Defines the three operating modes and a DataRouter that directs
search, prefetch, ingest, and feedback calls to LocalBrain,
the cloud backend, or both — depending on the chosen mode.

Modes:
    local_only  – All data stays on-device via LocalBrain.
    hybrid      – LocalBrain for local context, cloud for community.
    cloud_only  – Cloud backend only, no LocalBrain dependency.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import httpx

from aurabook.core.models import (
    CommunityFeedbackRequest,
    CommunityFeedbackResponse,
    CommunityPrefetchMatch,
    CommunityPrefetchResponse,
    CommunitySearchResponse,
    CommunitySearchResult,
    FeedbackKind,
    TrajectoryUploadPayload,
)

logger = logging.getLogger(__name__)


class ConnectivityMode(str, Enum):
    """Operating mode that controls where data is read from and written to."""

    LOCAL_ONLY = "local_only"
    HYBRID = "hybrid"
    CLOUD_ONLY = "cloud_only"


class LocalBrainClient:
    """HTTP client that talks to the LocalBrain gateway API.

    Adapts LocalBrain's endpoints into the same response shapes used
    by CloudBrainClient so the DataRouter can merge results uniformly.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8100",
        *,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )
        self._owns_client = client is None

    async def search(
        self,
        *,
        prompt: str,
        error_log: str | None = None,
        limit: int = 5,
        match_threshold: float | None = None,
    ) -> CommunitySearchResponse:
        """Run a keyword search against LocalBrain and wrap results."""
        query = prompt
        if error_log:
            query = f"{prompt} {error_log}"
        try:
            response = await self._client.get(
                "/v1/search",
                params={"q": query, "limit": limit, "mode": "keyword"},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, httpx.TransportError) as exc:
            logger.warning("LocalBrain search failed: %s", exc)
            return CommunitySearchResponse()

        matches: list[CommunitySearchResult] = []
        for result in data.get("results", []):
            score = abs(result.get("score", 0))
            normalized = min(score / 20.0, 1.0) if score else 0.0
            if match_threshold is not None and normalized < match_threshold:
                continue
            matches.append(
                CommunitySearchResult(
                    note_id=f"local-{result.get('chunk_id', '')}",
                    trajectory_id=f"local-{result.get('file_id', '')}",
                    note_text=result.get("content", result.get("snippet", "")),
                    source_model="localbrain",
                    similarity=round(normalized, 4),
                    metadata={"source": "localbrain", "path": result.get("path", "")},
                    original_prompt=prompt,
                )
            )
        return CommunitySearchResponse(matches=matches[:limit])

    async def prefetch(self, cdc_hash: str) -> CommunityPrefetchResponse:
        """LocalBrain doesn't support CDC hash lookup — always misses."""
        return CommunityPrefetchResponse(found=False, match=None)

    async def ingest(self, payload: TrajectoryUploadPayload) -> dict[str, Any]:
        """Store a trajectory locally by triggering LocalBrain file ingest."""
        return {"trajectory_id": "local-noop", "created": False}

    async def submit_feedback(
        self,
        payload: CommunityFeedbackRequest,
    ) -> CommunityFeedbackResponse:
        """Feedback is acknowledged locally but not persisted to cloud."""
        return CommunityFeedbackResponse(
            accepted=True,
            note_id=payload.note_id,
            trajectory_id=payload.trajectory_id,
            feedback=payload.feedback,
        )

    async def health(self) -> dict[str, Any]:
        """Check whether LocalBrain is reachable."""
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.TransportError) as exc:
            logger.warning("LocalBrain health check failed: %s", exc)
            return {"status": "unreachable", "error": str(exc)}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class DataRouter:
    """Routes data operations to LocalBrain, cloud, or both.

    Implements the same interface as CloudBrainClient so it can be
    used as a drop-in replacement anywhere the cloud client is expected.
    """

    def __init__(
        self,
        mode: ConnectivityMode,
        *,
        local_client: LocalBrainClient | None = None,
        cloud_client: Any | None = None,
    ) -> None:
        self._mode = mode
        self._local = local_client
        self._cloud = cloud_client

        if mode == ConnectivityMode.LOCAL_ONLY and local_client is None:
            raise ValueError("local_only mode requires a LocalBrainClient")
        if mode == ConnectivityMode.CLOUD_ONLY and cloud_client is None:
            raise ValueError("cloud_only mode requires a cloud client")
        if mode == ConnectivityMode.HYBRID and (local_client is None or cloud_client is None):
            raise ValueError("hybrid mode requires both a LocalBrainClient and a cloud client")

    @property
    def mode(self) -> ConnectivityMode:
        return self._mode

    @property
    def cloud_client(self) -> Any | None:
        return self._cloud

    @property
    def local_client(self) -> LocalBrainClient | None:
        return self._local

    async def prefetch(self, cdc_hash: str) -> CommunityPrefetchResponse:
        if self._mode == ConnectivityMode.LOCAL_ONLY:
            assert self._local is not None
            return await self._local.prefetch(cdc_hash)

        if self._mode == ConnectivityMode.CLOUD_ONLY:
            assert self._cloud is not None
            return await self._cloud.prefetch(cdc_hash)

        # hybrid: try cloud first (it has CDC hash index), fall back to local
        assert self._cloud is not None and self._local is not None
        response = await self._cloud.prefetch(cdc_hash)
        if response.found:
            return response
        return await self._local.prefetch(cdc_hash)

    async def search(
        self,
        *,
        prompt: str,
        error_log: str | None = None,
        limit: int = 5,
        match_threshold: float | None = None,
    ) -> CommunitySearchResponse:
        if self._mode == ConnectivityMode.LOCAL_ONLY:
            assert self._local is not None
            return await self._local.search(
                prompt=prompt,
                error_log=error_log,
                limit=limit,
                match_threshold=match_threshold,
            )

        if self._mode == ConnectivityMode.CLOUD_ONLY:
            assert self._cloud is not None
            return await self._cloud.search(
                prompt=prompt,
                error_log=error_log,
                limit=limit,
                match_threshold=match_threshold,
            )

        # hybrid: query both, interleave by similarity, deduplicate
        assert self._local is not None and self._cloud is not None
        local_response = await self._local.search(
            prompt=prompt,
            error_log=error_log,
            limit=limit,
            match_threshold=match_threshold,
        )
        cloud_response = await self._cloud.search(
            prompt=prompt,
            error_log=error_log,
            limit=limit,
            match_threshold=match_threshold,
        )
        merged = sorted(
            local_response.matches + cloud_response.matches,
            key=lambda m: m.similarity,
            reverse=True,
        )
        seen: set[str] = set()
        deduped: list[CommunitySearchResult] = []
        for match in merged:
            if match.note_id in seen:
                continue
            seen.add(match.note_id)
            deduped.append(match)
            if len(deduped) >= limit:
                break
        return CommunitySearchResponse(matches=deduped)

    async def ingest(self, payload: TrajectoryUploadPayload) -> dict[str, Any]:
        if self._mode == ConnectivityMode.LOCAL_ONLY:
            assert self._local is not None
            return await self._local.ingest(payload)

        if self._mode == ConnectivityMode.CLOUD_ONLY:
            assert self._cloud is not None
            return await self._cloud.ingest(payload)

        # hybrid: upload to cloud, local is fire-and-forget
        assert self._cloud is not None and self._local is not None
        result = await self._cloud.ingest(payload)
        try:
            await self._local.ingest(payload)
        except Exception as exc:
            logger.warning("LocalBrain ingest failed (non-fatal): %s", exc)
        return result

    async def submit_feedback(
        self,
        payload: CommunityFeedbackRequest,
    ) -> CommunityFeedbackResponse:
        if self._mode == ConnectivityMode.LOCAL_ONLY:
            assert self._local is not None
            return await self._local.submit_feedback(payload)

        if self._mode == ConnectivityMode.CLOUD_ONLY:
            assert self._cloud is not None
            return await self._cloud.submit_feedback(payload)

        assert self._cloud is not None and self._local is not None
        response = await self._cloud.submit_feedback(payload)
        try:
            await self._local.submit_feedback(payload)
        except Exception as exc:
            logger.warning("LocalBrain feedback failed (non-fatal): %s", exc)
        return response

    async def close(self) -> None:
        if self._local is not None:
            await self._local.close()
        if self._cloud is not None and hasattr(self._cloud, "close"):
            await self._cloud.close()
