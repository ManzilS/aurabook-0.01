"""Cold-start seeding pipeline for new AuraBook deployments.

Orchestrates the full bootstrap sequence:
  1. Run database migrations (Alembic or ensure_schema fallback)
  2. Generate and ingest synthetic trajectories via swarm_generator
  3. Run the janitor to convert trajectories into searchable notes
  4. Verify the deployment is ready (notes exist, search works)

Usage:
    aurabook-cloud-seed                     # defaults: 20 trajectories
    aurabook-cloud-seed --samples 50        # more initial content
    aurabook-cloud-seed --skip-migrate      # skip migration step
    aurabook-cloud-seed --verify-only       # just check readiness
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, get_settings

logger = logging.getLogger(__name__)


async def run_migrations(settings: Settings) -> bool:
    """Run Alembic migrations if available, else fall back to ensure_schema."""
    backend = _detect_backend(settings)
    if backend == "demo":
        logger.info("Demo mode — no migrations needed")
        return True

    alembic_ini = PROJECT_ROOT / "alembic.ini"
    if alembic_ini.exists() and backend == "postgres":
        try:
            import subprocess
            env = {"AURABOOK_CLOUD_DATABASE_URL": settings.database_url}
            import os
            full_env = {**os.environ, **env}
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=str(PROJECT_ROOT),
                env=full_env,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info("Alembic migrations applied successfully")
                return True
            logger.warning("Alembic failed: %s", result.stderr)
        except Exception as exc:
            logger.warning("Alembic not available (%s), falling back to ensure_schema", exc)

    if backend == "postgres":
        from db.postgres import PostgresConnectionProvider
        provider = PostgresConnectionProvider(settings.database_url)
        try:
            await provider.ensure_schema()
            from api.auth import api_keys_ddl
            pool = await provider.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(api_keys_ddl())
            logger.info("Schema ensured via ensure_schema()")
            return True
        finally:
            await provider.close()

    logger.info("Non-postgres backend — skipping migrations")
    return True


async def seed_trajectories(settings: Settings, samples: int, concurrency: int) -> int:
    """Generate and ingest synthetic trajectories."""
    from scripts.swarm_generator import SwarmGenerator

    generator = SwarmGenerator(settings, concurrency=concurrency)
    try:
        uploaded = await generator.run(samples, shuffle=True)
        logger.info("Seeded %d synthetic trajectories", len(uploaded))
        return len(uploaded)
    finally:
        await generator.close()


async def run_janitor(settings: Settings, batch_size: int = 10) -> int:
    """Convert unprocessed trajectories into searchable notes."""
    from api.main import build_services
    from worker.janitor import NotesJanitorJob
    from worker.summarizer import build_summarizer

    services = build_services(settings)
    summarizer = build_summarizer(settings)
    job = NotesJanitorJob(
        graph_store=services.graph_store,
        vector_store=services.vector_store,
        summarizer=summarizer,
        embedding_service=services.embedding_service,
        summary_model=settings.resolve_summary_model(),
        batch_size=batch_size,
    )

    total_processed = 0
    while True:
        result = await job.run_once()
        total_processed += result.processed
        if result.processed == 0:
            break
        logger.info("Janitor batch: processed=%d, total=%d", result.processed, total_processed)

    closeable = services._closeable
    if closeable is not None:
        await closeable.close()
    close = getattr(services.embedding_service, "close", None)
    if callable(close):
        await close()

    return total_processed


async def verify_readiness(settings: Settings) -> dict[str, bool | int | str]:
    """Check that the deployment has notes and can serve search requests."""
    from api.main import build_services

    services = build_services(settings)
    checks: dict[str, bool | int | str] = {}

    try:
        note_count = await services.vector_store.count()
        checks["note_count"] = note_count
        checks["has_notes"] = note_count > 0
    except Exception as exc:
        checks["has_notes"] = False
        checks["note_error"] = str(exc)

    try:
        unprocessed = await services.graph_store.list_unprocessed(limit=1)
        checks["unprocessed_remaining"] = len(unprocessed)
    except Exception as exc:
        checks["unprocessed_remaining"] = -1
        checks["unprocessed_error"] = str(exc)

    if checks.get("has_notes"):
        try:
            embedding = await services.embedding_service.embed_text("test query")
            results = await services.vector_store.search(
                embedding, query_text="test query", limit=1, match_threshold=0.0
            )
            checks["search_works"] = len(results) > 0
        except Exception as exc:
            checks["search_works"] = False
            checks["search_error"] = str(exc)
    else:
        checks["search_works"] = False

    checks["ready"] = bool(checks.get("has_notes") and checks.get("search_works"))

    closeable = services._closeable
    if closeable is not None:
        await closeable.close()
    close = getattr(services.embedding_service, "close", None)
    if callable(close):
        await close()

    return checks


def _detect_backend(settings: Settings) -> str:
    if settings.demo_mode:
        return "demo"
    if settings.db_backend.strip():
        return settings.db_backend.strip().lower()
    if settings.database_url.strip():
        return "postgres"
    return "demo"


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.verify_only:
        checks = await verify_readiness(settings)
        for key, value in sorted(checks.items()):
            logger.info("  %s: %s", key, value)
        if checks.get("ready"):
            logger.info("Deployment is READY")
        else:
            logger.warning("Deployment is NOT READY")
            sys.exit(1)
        return

    if not args.skip_migrate:
        logger.info("Step 1/4: Running migrations...")
        await run_migrations(settings)
    else:
        logger.info("Step 1/4: Migrations skipped")

    logger.info("Step 2/4: Seeding %d trajectories...", args.samples)
    seeded = await seed_trajectories(settings, args.samples, args.concurrency)

    if seeded > 0 and settings.embedding_provider_ready():
        logger.info("Step 3/4: Running janitor to generate notes...")
        processed = await run_janitor(settings)
        logger.info("Janitor processed %d trajectories into notes", processed)
    else:
        if seeded == 0:
            logger.info("Step 3/4: No trajectories to process")
        else:
            logger.warning("Step 3/4: Embedding provider not configured — skipping note generation")

    logger.info("Step 4/4: Verifying readiness...")
    checks = await verify_readiness(settings)
    for key, value in sorted(checks.items()):
        logger.info("  %s: %s", key, value)

    if checks.get("ready"):
        logger.info("Cold-start seeding COMPLETE — deployment is ready")
    else:
        logger.warning("Seeding finished but deployment is not fully ready (see checks above)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cold-start seeding pipeline for AuraBook Cloud Brain."
    )
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
