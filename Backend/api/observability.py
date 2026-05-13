"""Observability middleware and metrics for the Cloud Brain API.

Provides:
  - Structured JSON request logging with timing
  - In-process counters exposed at ``/metrics`` (Prometheus text format)
  - A ``/ready`` probe that verifies the database connection
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


@dataclass
class Metrics:
    """Simple in-process counters. Thread-safe enough for single-worker uvicorn."""

    requests_total: int = 0
    requests_by_status: dict[int, int] = field(default_factory=dict)
    requests_by_path: dict[str, int] = field(default_factory=dict)
    errors_total: int = 0
    latency_sum_ms: float = 0.0
    latency_count: int = 0

    def record(self, path: str, status: int, latency_ms: float) -> None:
        self.requests_total += 1
        self.requests_by_status[status] = self.requests_by_status.get(status, 0) + 1
        self.requests_by_path[path] = self.requests_by_path.get(path, 0) + 1
        self.latency_sum_ms += latency_ms
        self.latency_count += 1
        if status >= 500:
            self.errors_total += 1

    def prometheus_text(self) -> str:
        lines: list[str] = []
        lines.append(f"aurabook_requests_total {self.requests_total}")
        lines.append(f"aurabook_errors_total {self.errors_total}")
        if self.latency_count:
            avg = self.latency_sum_ms / self.latency_count
            lines.append(f"aurabook_latency_avg_ms {avg:.1f}")
        for status, count in sorted(self.requests_by_status.items()):
            lines.append(f'aurabook_requests_by_status{{status="{status}"}} {count}')
        for path, count in sorted(self.requests_by_path.items()):
            lines.append(f'aurabook_requests_by_path{{path="{path}"}} {count}')
        return "\n".join(lines) + "\n"


_global_metrics = Metrics()


def get_metrics() -> Metrics:
    return _global_metrics


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with method, path, status, and latency."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            status = response.status_code if response else 500
            path = request.url.path
            _global_metrics.record(path, status, elapsed_ms)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(elapsed_ms, 1),
                    "client": request.headers.get(
                        "X-AuraBook-Client-ID",
                        request.client.host if request.client else "unknown",
                    ),
                },
            )


def register_observability_routes(app: FastAPI, services: Any) -> None:
    """Add /metrics and /ready endpoints to the app."""

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=get_metrics().prometheus_text(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        checks: dict[str, str] = {}

        try:
            count = await services.vector_store.count()
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"

        embedding_ready = getattr(services.settings, "embedding_provider_ready", None)
        if callable(embedding_ready):
            checks["embeddings"] = "ok" if embedding_ready() else "not_configured"
        else:
            checks["embeddings"] = "unknown"

        all_ok = all(v == "ok" for v in checks.values() if v != "not_configured")
        return {"ready": all_ok, "checks": checks}
