"""In-memory sliding-window rate limiter for the Cloud Brain API.

Uses a per-key token-bucket stored in a dict.  Good enough for a
single-process deployment on a DigitalOcean droplet; swap for Redis
when you scale horizontally.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request


@dataclass(slots=True)
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class RateLimiter:
    """Token-bucket rate limiter keyed by client identifier.

    Parameters:
        requests_per_minute:  Sustained rate.
        burst:                Max tokens that can accumulate.
    """

    requests_per_minute: float = 60.0
    burst: float = 20.0
    _buckets: dict[str, _Bucket] = field(default_factory=dict, repr=False)

    def _refill(self, bucket: _Bucket) -> None:
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(
            self.burst,
            bucket.tokens + elapsed * (self.requests_per_minute / 60.0),
        )
        bucket.last_refill = now

    def allow(self, key: str) -> bool:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.burst, last_refill=time.monotonic())
            self._buckets[key] = bucket
        self._refill(bucket)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False

    def check(self, request: Request) -> None:
        """Raise 429 if the request exceeds the rate limit."""
        key = self._identify(request)
        if not self.allow(key):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again shortly.",
                headers={"Retry-After": str(int(60 / self.requests_per_minute) + 1)},
            )

    @staticmethod
    def _identify(request: Request) -> str:
        """Derive a rate-limit key from the request."""
        client_id = request.headers.get("X-AuraBook-Client-ID", "")
        if client_id:
            return f"client:{client_id}"
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return f"key:{api_key[:16]}"
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        client = request.client
        if client:
            return f"ip:{client.host}"
        return "anon"
