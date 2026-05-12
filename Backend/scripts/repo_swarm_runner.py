"""CLI entry point for the repository build swarm."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from services.openai_failures import OpenAIHaltError
from services.repo_swarm import (
    BuildCommand,
    ChainedFixProvider,
    FixProviderStage,
    HeuristicFixProvider,
    LiveTrajectoryPublisher,
    OpenAIFixProvider,
    RepoBuildManifest,
    RepoBuildSwarm,
    RepoBuildTarget,
)

logger = logging.getLogger(__name__)


def _override_generation_settings(
    settings,
    *,
    provider: str | None,
    model: str | None,
    summary_model: str | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
):
    """Clone settings with generation-provider overrides for this process."""
    updates: dict[str, object] = {}
    if provider:
        updates["generation_provider"] = provider
        updates["generation_model"] = ""
        updates["fix_model"] = ""
        updates["summary_model"] = ""
    if model is not None:
        updates["fix_model"] = model
    if summary_model is not None:
        updates["summary_model"] = summary_model
    if embedding_provider:
        updates["embedding_provider"] = embedding_provider
        updates["embedding_model"] = ""
    if embedding_model is not None:
        updates["embedding_model"] = embedding_model
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _build_fix_provider(settings, args: argparse.Namespace):
    """Construct the configured fix-provider chain."""
    primary_provider_name = args.primary_provider or settings.generation_provider
    primary_model = args.primary_model
    primary_settings = _override_generation_settings(
        settings,
        provider=primary_provider_name,
        model=primary_model,
        summary_model=primary_model,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
    )
    primary_fix_provider = (
        OpenAIFixProvider(primary_settings)
        if primary_settings.generation_provider_ready()
        else HeuristicFixProvider()
    )

    if not args.fallback_provider:
        return primary_settings, primary_fix_provider

    fallback_settings = _override_generation_settings(
        settings,
        provider=args.fallback_provider,
        model=args.fallback_model,
    )
    fallback_fix_provider = (
        OpenAIFixProvider(fallback_settings)
        if fallback_settings.generation_provider_ready()
        else HeuristicFixProvider()
    )
    chained_provider = ChainedFixProvider(
        [
            FixProviderStage(provider=primary_fix_provider, count_toward_total=False),
            FixProviderStage(
                provider=fallback_fix_provider,
                token_budget=args.fallback_token_budget,
                count_toward_total=True,
            ),
        ]
    )
    return primary_settings, chained_provider


def _manifest_from_args(args: argparse.Namespace) -> RepoBuildManifest:
    """Construct a manifest from CLI arguments when no JSON manifest is supplied."""
    if args.manifest:
        return RepoBuildManifest.from_path(Path(args.manifest).expanduser().resolve())
    if not args.repository:
        raise ValueError("Provide --manifest or at least one --repository")

    target = RepoBuildTarget(
        target_id=args.target_id or "adhoc-target",
        repository=args.repository,
        goal=args.goal,
        ref=args.ref,
        workdir=args.workdir,
        setup_commands=[BuildCommand(shell=command) for command in args.setup_shell],
        build_commands=[BuildCommand(shell=command) for command in args.build_shell],
        focus_files=args.focus_file,
        max_attempts=args.max_attempts,
    )
    return RepoBuildManifest(targets=[target])


async def _main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    manifest = _manifest_from_args(args)
    settings, fix_provider = _build_fix_provider(settings, args)
    publisher = LiveTrajectoryPublisher(settings)
    swarm = RepoBuildSwarm(
        settings,
        fix_provider=fix_provider,
        publisher=publisher,
        workspace_root=Path(args.workspace_root).expanduser().resolve(),
    )
    outcomes = []
    cycle = 0
    try:
        try:
            while True:
                cycle += 1
                logger.info("Starting repo swarm cycle %d", cycle)
                if args.token_budget is not None:
                    report = await swarm.run_until_token_budget(
                        manifest,
                        token_budget=args.token_budget,
                        max_projects=args.max_projects,
                        run_janitor_for_solved=not args.skip_janitor,
                    )
                    outcomes.extend(report.outcomes)
                    logger.info(
                        "token_usage=%d budget=%d budget_reached=%s",
                        report.total_tokens_used,
                        report.token_budget,
                        report.budget_reached,
                    )
                    break

                cycle_outcomes = await swarm.run_manifest(
                    manifest,
                    max_projects=args.max_projects,
                    run_janitor_for_solved=not args.skip_janitor,
                )
                outcomes.extend(cycle_outcomes)
                if not args.repeat:
                    break
                await asyncio.sleep(args.repeat_delay_s)
        except OpenAIHaltError as exc:
            logger.error(
                "Stopping repo swarm because the configured LLM provider returned a fatal token/quota error: %s",
                exc,
            )
            raise SystemExit(2) from exc
    finally:
        await swarm.close()

    for outcome in outcomes:
        logger.info(
            "target=%s status=%s attempts=%d trajectory=%s message=%s",
            outcome.target_id,
            outcome.status,
            outcome.attempts_used,
            outcome.trajectory_id,
            outcome.message,
        )


def main() -> None:
    """Run the repo build swarm from the CLI."""
    parser = argparse.ArgumentParser(description="Build repos, retry fixes up to 3 times, and upload trajectories.")
    parser.add_argument("--manifest", default=None, help="Path to a JSON manifest of repository targets.")
    parser.add_argument("--repository", default=None, help="One repository URL or local path for ad hoc mode.")
    parser.add_argument("--target-id", default=None, help="Override the generated target id in ad hoc mode.")
    parser.add_argument(
        "--goal",
        default="Build and repair this project until the build succeeds.",
        help="High-level goal recorded in uploaded trajectories.",
    )
    parser.add_argument("--ref", default=None, help="Optional git ref to checkout after cloning.")
    parser.add_argument("--workdir", default=None, help="Optional subdirectory inside the repository to build.")
    parser.add_argument(
        "--setup-shell",
        action="append",
        default=[],
        help="Setup shell command. Repeat for multiple setup commands. If omitted, the swarm infers setup steps.",
    )
    parser.add_argument(
        "--build-shell",
        action="append",
        default=[],
        help="Build shell command. Repeat for multiple commands. If omitted, the swarm infers build steps.",
    )
    parser.add_argument(
        "--focus-file",
        action="append",
        default=[],
        help="Repository-relative file to prioritize in model context. Repeat to add more.",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum repair attempts per repository.")
    parser.add_argument("--max-projects", type=int, default=None, help="Only process the first N targets.")
    parser.add_argument(
        "--primary-provider",
        default=None,
        help="Override the primary fix provider for this run (for example: lmstudio, openai, ollama).",
    )
    parser.add_argument(
        "--primary-model",
        default=None,
        help="Override the primary fix model for this run.",
    )
    parser.add_argument(
        "--fallback-provider",
        default=None,
        help="Optional fallback fix provider used when the primary provider produces no edits or errors.",
    )
    parser.add_argument(
        "--fallback-model",
        default=None,
        help="Optional fallback fix model name.",
    )
    parser.add_argument(
        "--fallback-token-budget",
        type=int,
        default=None,
        help="Optional token budget for the fallback provider. Once exhausted, only the primary provider continues.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        help="Optional embedding provider override for janitor/search writes during this run.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Optional embedding model override for janitor/search writes during this run.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Stop after this many OpenAI response tokens are spent by repair and janitor runs.",
    )
    parser.add_argument(
        "--workspace-root",
        default=".swarm/workspaces",
        help="Directory used for temporary cloned/copied repositories.",
    )
    parser.add_argument(
        "--skip-janitor",
        action="store_true",
        help="Upload solved trajectories without immediately summarizing them into notes.",
    )
    parser.add_argument(
        "--repeat",
        action="store_true",
        help="Keep cycling through the manifest until stopped or a fatal provider error occurs.",
    )
    parser.add_argument(
        "--repeat-delay-s",
        type=float,
        default=5.0,
        help="Seconds to wait between manifest cycles when --repeat is enabled.",
    )
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
