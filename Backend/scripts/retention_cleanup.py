"""Run retention cleanup against the AuraBook Cloud Brain admin API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, get_settings

logger = logging.getLogger(__name__)


def _signed_headers(*, settings: Settings, body: str) -> dict[str, str]:
    """Build AuraBook admin request headers."""
    headers = {"Content-Type": "application/json"}
    if not settings.api_shared_secret:
        return headers
    timestamp = str(int(time.time()))
    signature = hmac.new(
        settings.api_shared_secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers["X-AuraBook-Timestamp"] = timestamp
    headers["X-AuraBook-Signature"] = signature
    return headers


async def prune_old_trajectories(settings: Settings) -> dict[str, int]:
    """Call the cloud retention endpoint and return the deletion summary."""
    payload_body = json.dumps({}, separators=(",", ":"), sort_keys=True)
    async with httpx.AsyncClient(
        base_url=settings.cloud_api_base_url.rstrip("/"),
        timeout=15.0,
    ) as client:
        response = await client.post(
            "/v1/admin/prune",
            content=payload_body,
            headers=_signed_headers(settings=settings, body=payload_body),
        )
        response.raise_for_status()
        data = response.json()
        return {"deleted": int(data.get("deleted", 0))}


def main() -> None:
    """CLI entry point for retention cleanup."""
    parser = argparse.ArgumentParser(description="Run AuraBook Cloud Brain retention cleanup.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the configured Cloud Brain base URL.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.base_url:
        settings.cloud_api_base_url = args.base_url

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    result = asyncio.run(prune_old_trajectories(settings))
    logger.info("Retention cleanup finished: %s", result)


if __name__ == "__main__":
    main()
