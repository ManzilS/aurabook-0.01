"""CLI entry point for the AuraBook local daemon."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn

from aurabook.core.models import CommunityFeedbackRequest, FeedbackKind, ResponseGradient
from aurabook.core.runtime import build_runtime
from aurabook.middleware.pii import PIIScrubberMiddleware
from aurabook.server.main import create_app
from aurabook.services.preferences import UserPreferencesStore
from aurabook.services.status import RuntimeStatusStore
from aurabook.utils.config import Settings

cli = typer.Typer(help="AuraBook local proxy and memory daemon.")
preferences_cli = typer.Typer(help="Manage persisted AuraBook user preferences.")
cli.add_typer(preferences_cli, name="preferences")


@cli.callback()
def callback() -> None:
    """Top-level CLI group for AuraBook commands."""


@cli.command("start")
def start(
    host: str | None = typer.Option(None, help="Host interface to bind."),
    port: int | None = typer.Option(None, help="Port to bind."),
    demo: bool | None = typer.Option(None, help="Force offline demo mode."),
) -> None:
    """Start the AuraBook background proxy daemon."""
    settings = Settings()
    if host is not None:
        settings.host = host
    if port is not None:
        settings.port = port
    if demo is not None:
        settings.demo_mode = demo
        settings.cloud_demo_mode = demo

    runtime = build_runtime(settings=settings)
    app = create_app(runtime)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=30,
    )


@cli.command("status")
def status() -> None:
    """Show the last persisted AuraBook runtime status snapshot."""
    settings = Settings()
    snapshot = RuntimeStatusStore(settings.resolve_status_path()).snapshot()
    typer.echo(json.dumps(snapshot.model_dump(mode="json"), indent=2))


@cli.command("interventions")
def interventions() -> None:
    """Show recent intervention history from the local status store."""
    settings = Settings()
    snapshot = RuntimeStatusStore(settings.resolve_status_path()).snapshot()
    typer.echo(
        json.dumps(
            [entry.model_dump(mode="json") for entry in snapshot.recent_interventions],
            indent=2,
        )
    )


@cli.command("rotate-client-id")
def rotate_client_id() -> None:
    """Rotate the persisted anonymous AuraBook client identifier."""
    settings = Settings()
    identity_store = build_runtime(settings=settings, emitter=lambda _: None).identity_store
    typer.echo(json.dumps({"client_id": identity_store.rotate_client_id()}, indent=2))


@cli.command("audit")
def audit(
    text: str | None = typer.Option(None, help="Inline text to scrub."),
    path: Path | None = typer.Option(None, help="Optional file to scrub locally."),
) -> None:
    """Show what AuraBook would redact before a payload leaves the machine."""
    if text is None and path is None:
        raise typer.BadParameter("Provide either --text or --path.")

    source_text = text
    if path is not None:
        source_text = path.read_text(encoding="utf-8")

    if source_text is None:
        raise typer.BadParameter("Unable to load text for auditing.")

    scrubber = PIIScrubberMiddleware()
    cleaned, redactions = scrubber.scrub_text(source_text)
    typer.echo(
        json.dumps(
            {
                "redactions": redactions,
                "original_length": len(source_text),
                "scrubbed_text": cleaned,
            },
            indent=2,
        )
    )


@preferences_cli.command("show")
def show_preferences() -> None:
    """Print the persisted user preference overrides."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).load()
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@preferences_cli.command("set-gradient")
def set_gradient(gradient: ResponseGradient) -> None:
    """Persist the maximum community intervention gradient."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).set_max_gradient(
        gradient
    )
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@preferences_cli.command("set-repo")
def set_repo_policy(
    repo: Path = typer.Option(..., help="Repository root to target."),
    gradient: ResponseGradient | None = typer.Option(None, help="Maximum gradient for this repo."),
    cloud_enabled: bool | None = typer.Option(None, help="Enable cloud lookups for this repo."),
    upload_enabled: bool | None = typer.Option(None, help="Enable trajectory uploads for this repo."),
) -> None:
    """Persist repo-specific policy overrides."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).set_repo_policy(
        repo,
        max_gradient=gradient,
        cloud_enabled=cloud_enabled,
        upload_enabled=upload_enabled,
    )
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@preferences_cli.command("private-mode")
def set_private_mode(
    repo: Path = typer.Option(..., help="Repository root to target."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Toggle private mode."),
) -> None:
    """Enable or disable repo-level private mode."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).set_private_mode(
        repo,
        enabled=enabled,
    )
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@preferences_cli.command("suppress-note")
def suppress_note(
    note_id: str = typer.Argument(..., help="Community note ID to suppress."),
    repo: Path = typer.Option(Path("."), help="Repository root."),
) -> None:
    """Suppress a note for the provided repo."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).suppress_note(
        repo,
        note_id,
    )
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@preferences_cli.command("suppress-trajectory")
def suppress_trajectory(
    trajectory_id: str = typer.Argument(..., help="Trajectory ID to suppress."),
    repo: Path = typer.Option(Path("."), help="Repository root."),
) -> None:
    """Suppress a trajectory for the provided repo."""
    settings = Settings()
    preferences = UserPreferencesStore(settings.resolve_preferences_path()).suppress_trajectory(
        repo,
        trajectory_id,
    )
    typer.echo(json.dumps(preferences.model_dump(mode="json"), indent=2))


@cli.command("feedback")
def feedback(
    feedback: FeedbackKind = typer.Argument(..., help="Feedback label."),
    note_id: str | None = typer.Option(None, help="Optional note ID."),
    trajectory_id: str | None = typer.Option(None, help="Optional trajectory ID."),
    repo: Path = typer.Option(Path("."), help="Repository root."),
) -> None:
    """Submit intervention feedback to the cloud and update local suppression state."""
    if note_id is None and trajectory_id is None:
        raise typer.BadParameter("Provide --note-id or --trajectory-id.")

    settings = Settings()
    settings.watch_root = str(repo)
    runtime = build_runtime(settings=settings, emitter=lambda _: None)
    payload = CommunityFeedbackRequest(
        note_id=note_id,
        trajectory_id=trajectory_id,
        client_id=runtime.identity_store.get_client_id(),
        feedback=feedback,
        repository=str(repo.resolve()),
    )

    async def _submit() -> dict[str, object]:
        if runtime.cloud_client is None:
            return {"accepted": False, "reason": "cloud_disabled"}
        submit_feedback = getattr(runtime.cloud_client, "submit_feedback", None)
        if not callable(submit_feedback):
            return {"accepted": False, "reason": "feedback_unsupported"}
        response = await submit_feedback(payload)
        if settings.auto_suppress_negative_feedback and feedback != FeedbackKind.HELPFUL:
            if note_id is not None:
                runtime.preferences_store.suppress_note(repo, note_id)
                runtime.community_service.suppress_note(note_id)
            if trajectory_id is not None:
                runtime.preferences_store.suppress_trajectory(repo, trajectory_id)
                runtime.community_service.suppress_trajectory(trajectory_id)
        runtime.community_service.record_intervention(
            gradient="feedback",
            source="cli_feedback",
            note_id=note_id,
            trajectory_id=trajectory_id,
            feedback=feedback.value,
        )
        await runtime.cloud_client.close()
        return response.model_dump(mode="json")

    import asyncio

    typer.echo(json.dumps(asyncio.run(_submit()), indent=2))


def main() -> None:
    """Run the CLI application."""
    cli()


if __name__ == "__main__":
    main()
