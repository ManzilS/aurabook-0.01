"""Tests for the AuraBook CLI."""

from __future__ import annotations

import json

from typer.testing import CliRunner

import aurabook.cli as cli_module
from aurabook.services.status import RuntimeStatusStore


def test_start_command_builds_runtime_and_runs_uvicorn(monkeypatch) -> None:
    captured: dict[str, object] = {}
    runner = CliRunner()

    def fake_build_runtime(*, settings):
        captured["settings"] = settings
        return "runtime"

    def fake_create_app(runtime):
        captured["runtime"] = runtime
        return "app"

    def fake_run(app, *, host, port, log_level, timeout_keep_alive):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level
        captured["timeout"] = timeout_keep_alive

    monkeypatch.setattr(cli_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_module, "create_app", fake_create_app)
    monkeypatch.setattr(cli_module.uvicorn, "run", fake_run)

    result = runner.invoke(cli_module.cli, ["start", "--host", "0.0.0.0", "--port", "9090"])

    assert result.exit_code == 0
    assert captured["runtime"] == "runtime"
    assert captured["app"] == "app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9090
    assert captured["timeout"] == 30


def test_status_command_reads_persisted_snapshot(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    RuntimeStatusStore(settings.resolve_status_path()).initialize(
        client_id="client-123",
        cloud_enabled=True,
        cloud_demo_mode=True,
        max_gradient="notes",
    )

    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    result = runner.invoke(cli_module.cli, ["status"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["client_id"] == "client-123"
    assert payload["cloud_demo_mode"] is True


def test_audit_command_redacts_sensitive_text(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    result = runner.invoke(
        cli_module.cli,
        [
            "audit",
            "--text",
            "ghp_abcdefghijklmnopqrstuvwxyz123456 dev@example.com",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["redactions"] >= 2
    assert "[REDACTED_GITHUB_TOKEN]" in payload["scrubbed_text"]
    assert "[REDACTED_EMAIL]" in payload["scrubbed_text"]


def test_preferences_set_gradient_persists_value(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    result = runner.invoke(cli_module.cli, ["preferences", "set-gradient", "guide"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["max_gradient"] == "guide"


def test_interventions_command_reads_status_history(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    store = RuntimeStatusStore(settings.resolve_status_path())
    store.initialize(
        client_id="client-123",
        cloud_enabled=True,
        cloud_demo_mode=True,
        max_gradient="notes",
    )
    store.record_intervention(
        gradient="notes",
        source="search_remote",
        note_id="note-1",
        trajectory_id="traj-1",
        repository=str(tmp_path),
    )
    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    result = runner.invoke(cli_module.cli, ["interventions"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["note_id"] == "note-1"


def test_rotate_client_id_command_rewrites_identity(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    settings = cli_module.Settings(
        state_dir=str(tmp_path / ".aurabook"),
        watch_root=str(tmp_path),
        cloud_enabled=False,
    )

    class FakeRuntime:
        def __init__(self) -> None:
            from aurabook.services.identity import ClientIdentityStore

            self.identity_store = ClientIdentityStore(settings.resolve_state_dir())

    monkeypatch.setattr(cli_module, "Settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_runtime", lambda **kwargs: FakeRuntime())

    result = runner.invoke(cli_module.cli, ["rotate-client-id"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["client_id"]


def test_preferences_repo_commands_persist_policy_and_suppressions(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    set_result = runner.invoke(
        cli_module.cli,
        [
            "preferences",
            "set-repo",
            "--repo",
            str(repo),
            "--gradient",
            "guide",
            "--no-cloud-enabled",
            "--no-upload-enabled",
        ],
    )
    note_result = runner.invoke(
        cli_module.cli,
        ["preferences", "suppress-note", "note-1", "--repo", str(repo)],
    )
    trajectory_result = runner.invoke(
        cli_module.cli,
        ["preferences", "suppress-trajectory", "traj-1", "--repo", str(repo)],
    )

    assert set_result.exit_code == 0
    assert note_result.exit_code == 0
    assert trajectory_result.exit_code == 0
    payload = json.loads(trajectory_result.stdout)
    policy = payload["repo_profiles"][str(repo.resolve())]
    assert policy["max_gradient"] == "guide"
    assert policy["cloud_enabled"] is False
    assert policy["upload_enabled"] is False
    assert policy["suppressed_note_ids"] == ["note-1"]
    assert policy["suppressed_trajectory_ids"] == ["traj-1"]


def test_preferences_private_mode_command_sets_repo_private_mode(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))
    monkeypatch.setattr(cli_module, "Settings", lambda: settings)

    result = runner.invoke(
        cli_module.cli,
        ["preferences", "private-mode", "--repo", str(repo)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    policy = payload["repo_profiles"][str(repo.resolve())]
    assert policy["cloud_enabled"] is False
    assert policy["upload_enabled"] is False


def test_feedback_command_submits_and_suppresses_locally(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = cli_module.Settings(state_dir=str(tmp_path / ".aurabook"))

    class FakeIdentityStore:
        def get_client_id(self) -> str:
            return "client-123"

    class FakePreferencesStore:
        def __init__(self) -> None:
            self.suppressed_note_ids: list[str] = []

        def suppress_note(self, repo_root, note_id: str) -> None:
            self.suppressed_note_ids.append(note_id)

        def suppress_trajectory(self, repo_root, trajectory_id: str) -> None:
            return None

    class FakeCommunityService:
        def __init__(self) -> None:
            self.suppressed_note_ids: list[str] = []
            self.recorded = []

        def suppress_note(self, note_id: str) -> None:
            self.suppressed_note_ids.append(note_id)

        def suppress_trajectory(self, trajectory_id: str) -> None:
            return None

        def record_intervention(self, **kwargs) -> None:
            self.recorded.append(kwargs)

    class FakeCloudClient:
        async def submit_feedback(self, payload):
            class Response:
                def model_dump(self, mode: str = "json") -> dict[str, object]:
                    return {
                        "accepted": True,
                        "note_id": payload.note_id,
                        "trajectory_id": payload.trajectory_id,
                        "feedback": payload.feedback.value,
                    }

            return Response()

        async def close(self) -> None:
            return None

    class FakeRuntime:
        def __init__(self) -> None:
            self.cloud_client = FakeCloudClient()
            self.identity_store = FakeIdentityStore()
            self.preferences_store = FakePreferencesStore()
            self.community_service = FakeCommunityService()

    monkeypatch.setattr(cli_module, "Settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_runtime", lambda **kwargs: FakeRuntime())

    result = runner.invoke(
        cli_module.cli,
        ["feedback", "wrong", "--note-id", "note-1", "--repo", str(repo)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["accepted"] is True
    assert payload["note_id"] == "note-1"
