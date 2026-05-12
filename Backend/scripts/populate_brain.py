"""End-to-end pipeline: generate synthetic trajectories → process into notes → verify search.

Usage
-----
    # Demo mode (no LLM, no Supabase needed)
    python scripts/populate_brain.py --samples 50 --demo

    # Live mode (uses configured OpenAI + Supabase)
    python scripts/populate_brain.py --samples 100 --concurrency 8

    # With LLM-seeded extra cases and a custom cloud API URL
    python scripts/populate_brain.py --samples 50 --llm-seeds 10 --cloud-url http://127.0.0.1:8001

Run this from the aurabook-cloud/ directory, or pass --store-path explicitly.
"""

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
from worker.janitor import _run_job as _run_janitor

logger = logging.getLogger(__name__)

# Queries used to verify that search actually works after population.
_SMOKE_QUERIES = [
    "time.sleep blocks async event loop",
    "CORSMiddleware not defined import",
    "pandas DataFrame slice SettingWithCopyWarning",
    "SQLAlchemy commit coroutine never awaited",
    "setInterval stale closure React state",
    "unhandled promise rejection Express async",
    "TypeScript implicit any parameter",
    "Docker ModuleNotFoundError WORKDIR",
    "pytest parametrize syntax error",
    "circular import partially initialized module",
]


async def _wait_for_api(base_url: str, timeout: float = 30.0) -> bool:
    """Poll /health until the cloud API is reachable or timeout expires."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("Cloud API ready — mode=%s", data.get("mode", "?"))
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    logger.error("Cloud API did not become reachable within %.0fs at %s", timeout, base_url)
    return False


async def _generate(settings: Settings, args: argparse.Namespace) -> int:
    """Run the swarm generator and return the number of uploaded trajectories."""
    # Import here to keep startup fast when just checking health.
    from scripts.swarm_generator import SwarmGenerator

    generator = SwarmGenerator(settings, concurrency=args.concurrency)
    try:
        extra_cases = []
        if args.llm_seeds > 0:
            logger.info("Requesting %d LLM-generated failure seeds…", args.llm_seeds)
            extra_cases = await generator.generate_llm_seeds(args.llm_seeds)
            if extra_cases:
                logger.info("  → received %d new seeds", len(extra_cases))

        logger.info("Generating %d synthetic trajectories (concurrency=%d)…", args.samples, args.concurrency)
        uploaded = await generator.run(
            args.samples,
            extra_cases=extra_cases,
            shuffle=True,
        )
        logger.info("  → uploaded %d trajectories", len(uploaded))
        return len(uploaded)
    finally:
        await generator.close()


async def _drain_janitor(settings: Settings) -> tuple[int, int]:
    """Run the janitor in a loop until no unprocessed trajectories remain.

    Returns (total_processed, total_failed).
    """
    total_processed = 0
    total_failed = 0
    batch_num = 0
    while True:
        batch_num += 1
        result = await _run_janitor(settings)
        total_processed += result.processed
        total_failed += result.failed
        logger.info(
            "Janitor batch %d: processed=%d failed=%d tokens=%d",
            batch_num,
            result.processed,
            result.failed,
            result.llm_tokens_used,
        )
        if result.requested == 0:
            break  # nothing left to process
    return total_processed, total_failed


def _sign_request(body_bytes: bytes, shared_secret: str) -> tuple[str, str]:
    """Return (timestamp_str, signature_hex) for a request body."""
    timestamp = str(int(time.time()))
    message = f"{timestamp}.{body_bytes.decode('utf-8')}".encode("utf-8")
    sig = hmac.new(
        shared_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return timestamp, sig


async def _smoke_search(base_url: str, demo_mode: bool, shared_secret: str = "") -> dict[str, bool]:
    """Run the smoke queries and return a dict of query → hit (bool)."""
    # Always pass an explicit threshold so the test is independent of whatever the
    # server has configured. Demo uses deterministic token-hash embeddings (low scores),
    # live Supabase mode uses real OpenAI embeddings (scores top out ~0.55 for
    # long-document vs short-query retrieval).
    threshold = 0.15 if demo_mode else 0.30
    results: dict[str, bool] = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        for query in _SMOKE_QUERIES:
            body: dict = {"prompt": query, "limit": 1, "match_threshold": threshold}
            try:
                body_bytes = json.dumps(body).encode("utf-8")
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if shared_secret:
                    ts, sig = _sign_request(body_bytes, shared_secret)
                    headers["X-AuraBook-Timestamp"] = ts
                    headers["X-AuraBook-Signature"] = sig
                resp = await client.post("/v1/search", content=body_bytes, headers=headers)
                resp.raise_for_status()
                hits = resp.json().get("matches", [])
                results[query] = bool(hits)
            except Exception as exc:
                logger.warning("Search request failed for %r: %s", query[:40], exc)
                results[query] = False
    return results


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()

    # Override demo mode and store path from CLI flags.
    overrides: dict = {}
    if args.demo:
        overrides["demo_mode"] = True
    if args.store_path:
        overrides["demo_store_path"] = args.store_path
    if overrides:
        settings = settings.model_copy(update=overrides)

    cloud_url = args.cloud_url.rstrip("/")

    # ── 1. Verify the cloud API is reachable ─────────────────────────────────
    logger.info("Checking cloud API at %s…", cloud_url)
    if not await _wait_for_api(cloud_url, timeout=args.api_timeout):
        logger.error(
            "Start the cloud API first:\n"
            "  cd aurabook-cloud && python api/main.py\n"
            "or with demo mode:\n"
            "  AURABOOK_CLOUD_DEMO_MODE=true python api/main.py"
        )
        raise SystemExit(1)

    # ── 2. Snapshot before ───────────────────────────────────────────────────
    async with httpx.AsyncClient(base_url=cloud_url, timeout=10.0) as client:
        snap_before = (await client.get("/v1/dashboard/snapshot")).json()
    notes_before = len(snap_before.get("notes", []))
    logger.info("Notes before: %d", notes_before)

    t0 = time.monotonic()

    # ── 3. Generate trajectories ─────────────────────────────────────────────
    # Override the cloud API base URL in settings so the generator uploads to
    # the same instance we verified above.
    gen_settings = settings.model_copy(update={"cloud_api_base_url": cloud_url})
    uploaded = await _generate(gen_settings, args)

    # ── 4. Drain the janitor (only if auto-janitor on ingest is not active) ──
    # When running against a live API with background_tasks, the janitor fires
    # automatically per trajectory. We still drain here to catch any that were
    # queued before this run or failed to process inline.
    if not args.skip_janitor:
        logger.info("Draining janitor…")
        processed, failed = await _drain_janitor(settings)
        logger.info("Janitor finished: processed=%d failed=%d", processed, failed)
    else:
        logger.info("--skip-janitor set; skipping manual janitor drain")

    elapsed = time.monotonic() - t0

    # Give background janitor tasks a moment to finish writing before we snapshot.
    if args.skip_janitor:
        await asyncio.sleep(2.0)

    # ── 5. Snapshot after ────────────────────────────────────────────────────
    async with httpx.AsyncClient(base_url=cloud_url, timeout=10.0) as client:
        snap_after = (await client.get("/v1/dashboard/snapshot")).json()
    notes_after = len(snap_after.get("notes", []))

    # ── 6. Smoke-test search ─────────────────────────────────────────────────
    logger.info("Running search smoke tests…")
    search_results = await _smoke_search(
        cloud_url,
        demo_mode=settings.demo_mode,
        shared_secret=settings.api_shared_secret or "",
    )
    hits = sum(1 for v in search_results.values() if v)

    # ── 7. Report ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  AuraBook Brain Population Report")
    print("=" * 60)
    print(f"  Uploaded trajectories : {uploaded}")
    print(f"  Notes before          : {notes_before}")
    print(f"  Notes after           : {notes_after}")
    print(f"  New notes created     : {notes_after - notes_before}")
    print(f"  Elapsed               : {elapsed:.1f}s")
    print(f"  Search smoke hits     : {hits}/{len(_SMOKE_QUERIES)}")
    print()
    print("  Search results:")
    for query, hit in search_results.items():
        status = "[OK]" if hit else "[--]"
        print(f"    {status}  {query}")
    print("=" * 60 + "\n")

    if hits < len(_SMOKE_QUERIES) // 2:
        logger.warning(
            "Fewer than half the smoke queries found matches. "
            "Run with more --samples or check that the janitor processed all trajectories."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate the AuraBook Cloud Brain with synthetic trajectories and verify search.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--samples", type=int, default=50,
        help="Number of synthetic trajectories to generate.",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Parallel generator requests.",
    )
    parser.add_argument(
        "--llm-seeds", type=int, default=0, metavar="N",
        help="Ask the LLM to generate N additional failure case seeds before sampling.",
    )
    parser.add_argument(
        "--cloud-url", default="http://127.0.0.1:8000",
        help="Base URL of the running cloud API.",
    )
    parser.add_argument(
        "--store-path", default=None, metavar="PATH",
        help="Override the demo store JSON path (useful when running from a non-standard directory).",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Force demo mode (no OpenAI calls, no Supabase).",
    )
    parser.add_argument(
        "--skip-janitor", action="store_true",
        help="Skip the manual janitor drain (use when the API auto-processes on ingest).",
    )
    parser.add_argument(
        "--api-timeout", type=float, default=30.0, metavar="SECS",
        help="Seconds to wait for the cloud API to become reachable.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
