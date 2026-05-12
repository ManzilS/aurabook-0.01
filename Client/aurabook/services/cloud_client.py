"""HTTP client for talking to the AuraBook Cloud Brain."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from aurabook.core.models import (
    CommunityFeedbackRequest,
    CommunityFeedbackResponse,
    CommunityPrefetchResponse,
    CommunitySearchResponse,
    TrajectoryUploadPayload,
)
from aurabook.utils.config import Settings
from aurabook.utils.errors import CloudBrainError

logger = logging.getLogger(__name__)


class CloudBrainClient:
    """Typed async client for AuraBook Cloud Brain endpoints."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client_id = client_id
        self._client = client or httpx.AsyncClient(
            base_url=settings.cloud_api_base_url.rstrip("/"),
            timeout=settings.cloud_request_timeout,
        )
        self._owns_client = client is None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def prefetch(self, cdc_hash: str) -> CommunityPrefetchResponse:
        """Fetch an exact-match context by CDC hash."""
        payload = await self._post("/v1/prefetch", {"cdc_hash": cdc_hash})
        return CommunityPrefetchResponse.model_validate(payload)

    async def search(
        self,
        *,
        prompt: str,
        error_log: str | None = None,
        limit: int = 5,
        match_threshold: float | None = None,
    ) -> CommunitySearchResponse:
        """Search the Cloud Brain for a semantic match."""
        request_payload: dict[str, Any] = {
            "prompt": prompt,
            "error_log": error_log,
            "limit": limit,
        }
        if match_threshold is not None:
            request_payload["match_threshold"] = match_threshold
        payload = await self._post("/v1/search", request_payload)
        return CommunitySearchResponse.model_validate(payload)

    async def ingest(self, payload: TrajectoryUploadPayload) -> dict[str, Any]:
        """Upload a successful trajectory to the Cloud Brain."""
        json_payload = payload.model_dump(mode="json")
        return await self._post(
            "/v1/ingest",
            json_payload,
            idempotency_key=self._build_idempotency_key("/v1/ingest", json_payload),
        )

    async def submit_feedback(
        self,
        payload: CommunityFeedbackRequest,
    ) -> CommunityFeedbackResponse:
        """Submit user feedback for one intervention."""
        json_payload = payload.model_dump(mode="json")
        response = await self._post(
            "/v1/feedback",
            json_payload,
            idempotency_key=self._build_idempotency_key("/v1/feedback", json_payload),
        )
        return CommunityFeedbackResponse.model_validate(response)

    async def close(self) -> None:
        """Close the underlying HTTP client when owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self,
        path: str,
        json_payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_circuit_allows_request(path)
        payload_body = json.dumps(json_payload, separators=(",", ":"), sort_keys=True)
        headers = {
            "X-AuraBook-Client-ID": self._client_id,
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            headers["X-AuraBook-Idempotency-Key"] = idempotency_key
        if self._settings.cloud_shared_secret:
            timestamp = str(int(time.time()))
            signature = self._build_signature(timestamp=timestamp, body=payload_body)
            headers["X-AuraBook-Timestamp"] = timestamp
            headers["X-AuraBook-Signature"] = signature
        attempts = max(self._settings.cloud_retry_attempts, 1)

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(path, content=payload_body, headers=headers)
                response.raise_for_status()
                self._consecutive_failures = 0
                return response.json()
            except httpx.HTTPStatusError as exc:
                if not self._is_retryable_status(exc.response.status_code) or attempt >= attempts:
                    self._record_failure()
                    raise CloudBrainError(
                        f"Cloud Brain request failed for {path}: {exc}"
                    ) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= attempts:
                    self._record_failure()
                    raise CloudBrainError(
                        f"Cloud Brain request failed for {path}: {exc}"
                    ) from exc
            delay = self._settings.cloud_retry_base_delay_s * (2 ** (attempt - 1))
            logger.warning(
                "Cloud Brain request %s attempt %d/%d failed; retrying in %.2fs",
                path,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)

        self._record_failure()
        raise CloudBrainError(f"Cloud Brain request failed for {path}: retry loop exited")

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _build_idempotency_key(self, path: str, json_payload: dict[str, Any]) -> str:
        raw = f"{path}:{json.dumps(json_payload, sort_keys=True, separators=(',', ':'))}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _build_signature(self, *, timestamp: str, body: str) -> str:
        message = f"{timestamp}.{body}".encode("utf-8")
        secret = self._settings.cloud_shared_secret.encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._settings.cloud_circuit_breaker_threshold:
            self._circuit_open_until = time.time() + self._settings.cloud_circuit_breaker_reset_s
            logger.warning(
                "Cloud Brain circuit breaker opened for %.1fs after %d consecutive failures",
                self._settings.cloud_circuit_breaker_reset_s,
                self._consecutive_failures,
            )

    def _ensure_circuit_allows_request(self, path: str) -> None:
        if self._circuit_open_until <= time.time():
            return
        raise CloudBrainError(
            f"Cloud Brain circuit breaker is open for {path} until {self._circuit_open_until:.0f}"
        )
