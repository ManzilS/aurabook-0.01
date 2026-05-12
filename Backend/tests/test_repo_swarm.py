"""Tests for the repository build swarm."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from config import Settings
from models.schema import TrajectoryIngestRequest
from services.repo_swarm import (
    BuildCommand,
    ChainedFixProvider,
    FixContext,
    FixProposal,
    FixProviderStage,
    LiveTrajectoryPublisher,
    ProposedFileEdit,
    RepoBuildManifest,
    RepoBuildSwarm,
    RepoBuildTarget,
    SwarmHaltError,
)


class FakeFixProvider:
    """Deterministic fix provider used by swarm tests."""

    def __init__(
        self,
        responses: dict[tuple[str, int], FixProposal],
        token_costs: dict[tuple[str, int], int] | None = None,
        exceptions: dict[tuple[str, int], Exception] | None = None,
        model_name: str = "fake-fix-provider",
    ) -> None:
        self._responses = responses
        self._token_costs = token_costs or {}
        self._exceptions = exceptions or {}
        self.model_name = model_name
        self.calls: list[tuple[str, int]] = []
        self.total_tokens_used = 0

    async def propose_fix(self, context: FixContext) -> FixProposal:
        key = (context.target.target_id, context.attempt_number)
        self.calls.append(key)
        self.total_tokens_used += self._token_costs.get(key, 0)
        if key in self._exceptions:
            raise self._exceptions[key]
        return self._responses[key]

    async def close(self) -> None:
        return None


class FakePublisher:
    """In-memory publisher that records uploaded payloads."""

    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self.events = events
        self.payloads: list[TrajectoryIngestRequest] = []

    async def publish(self, payload: TrajectoryIngestRequest) -> dict[str, Any]:
        self.payloads.append(payload)
        self.events.append(("publish", payload.metadata.get("resolved")))
        return {"trajectory_id": payload.trajectory_id, "created": True}

    async def close(self) -> None:
        return None


@dataclass
class FakeJanitorResult:
    """Fake janitor result for logging compatibility."""

    requested: int = 1
    processed: int = 1
    failed: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "requested": self.requested,
            "processed": self.processed,
            "failed": self.failed,
        }


def _make_settings() -> Settings:
    return Settings(
        demo_mode=True,
        openai_api_key="",
        cloud_api_base_url="http://127.0.0.1:8000",
        api_shared_secret="",
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def _janitor_stub(events: list[tuple[str, Any]], settings: Settings) -> FakeJanitorResult:
    events.append(("janitor", settings.cloud_api_base_url))
    return FakeJanitorResult()


async def test_repo_swarm_solves_then_uploads_failures_after_janitor(tmp_path: Path) -> None:
    solved_repo = tmp_path / "solved-repo"
    failed_repo = tmp_path / "failed-repo"
    _write(solved_repo / "app.py", "print(unknown_name)\n")
    _write(failed_repo / "app.py", "print(missing_name)\n")

    manifest = RepoBuildManifest(
        targets=[
            RepoBuildTarget(
                target_id="solved",
                repository=str(solved_repo),
                goal="Repair the broken python script.",
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
                max_attempts=3,
            ),
            RepoBuildTarget(
                target_id="failed",
                repository=str(failed_repo),
                goal="Try to repair the other broken python script.",
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
                max_attempts=3,
            ),
        ]
    )

    fix_provider = FakeFixProvider(
        {
            ("solved", 1): FixProposal(
                reflection="Replace the undefined variable with a concrete value.",
                answer_summary="Write a fixed print statement.",
                files=[ProposedFileEdit(path="app.py", content="print('fixed')\n")],
            ),
            ("failed", 1): FixProposal(
                reflection="First guess still leaves the script broken.",
                answer_summary="Attempt one did not repair the undefined name.",
                files=[ProposedFileEdit(path="app.py", content="print(still_broken)\n")],
            ),
            ("failed", 2): FixProposal(
                reflection="Second guess is still wrong.",
                answer_summary="Attempt two keeps the script broken.",
                files=[ProposedFileEdit(path="app.py", content="print(still_broken_again)\n")],
            ),
            ("failed", 3): FixProposal(
                reflection="Third guess also fails.",
                answer_summary="Attempt three still does not resolve the issue.",
                files=[ProposedFileEdit(path="app.py", content="print(last_bad_guess)\n")],
            ),
        }
    )
    events: list[tuple[str, Any]] = []
    publisher = FakePublisher(events)
    swarm = RepoBuildSwarm(
        _make_settings(),
        fix_provider=fix_provider,
        publisher=publisher,
        janitor_runner=lambda settings: _janitor_stub(events, settings),
        workspace_root=tmp_path / "workspaces",
    )

    outcomes = await swarm.run_manifest(manifest)
    await swarm.close()

    assert [outcome.status for outcome in outcomes] == ["solved", "failed"]
    assert fix_provider.calls == [("solved", 1), ("failed", 1), ("failed", 2), ("failed", 3)]
    assert events == [("publish", True), ("janitor", "http://127.0.0.1:8000"), ("publish", False)]

    solved_payload, failed_payload = publisher.payloads
    assert solved_payload.metadata["resolved"] is True
    assert solved_payload.metadata["attempts_used"] == 1
    assert solved_payload.metadata["attempted_models"] == ["fake-fix-provider"]
    assert "print('fixed')" in solved_payload.final_fix
    assert solved_payload.history[0].model == "fake-fix-provider"
    assert solved_payload.history[0].thought == "Replace the undefined variable with a concrete value."
    assert solved_payload.history[0].tried == "Write a fixed print statement."
    assert solved_payload.history[0].commands == ["python app.py"]
    assert failed_payload.metadata["resolved"] is False
    assert failed_payload.metadata["attempts_used"] == 3
    assert failed_payload.metadata["failed_model_count"] == 1
    assert failed_payload.final_fix.startswith("UNRESOLVED after 3 attempts.")


async def test_repo_swarm_skips_upload_when_build_is_already_clean(tmp_path: Path) -> None:
    clean_repo = tmp_path / "clean-repo"
    _write(clean_repo / "app.py", "print('ok')\n")

    manifest = RepoBuildManifest(
        targets=[
            RepoBuildTarget(
                target_id="clean",
                repository=str(clean_repo),
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
            )
        ]
    )

    events: list[tuple[str, Any]] = []
    swarm = RepoBuildSwarm(
        _make_settings(),
        fix_provider=FakeFixProvider({}),
        publisher=FakePublisher(events),
        janitor_runner=lambda settings: _janitor_stub(events, settings),
        workspace_root=tmp_path / "workspaces",
    )

    outcomes = await swarm.run_manifest(manifest)
    await swarm.close()

    assert outcomes[0].status == "clean"
    assert events == []


async def test_repo_swarm_honors_token_budget(tmp_path: Path) -> None:
    first_repo = tmp_path / "first-repo"
    second_repo = tmp_path / "second-repo"
    _write(first_repo / "app.py", "print(unknown_name)\n")
    _write(second_repo / "app.py", "print(other_name)\n")

    manifest = RepoBuildManifest(
        targets=[
            RepoBuildTarget(
                target_id="first",
                repository=str(first_repo),
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
            ),
            RepoBuildTarget(
                target_id="second",
                repository=str(second_repo),
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
            ),
        ]
    )

    fix_provider = FakeFixProvider(
        {
            ("first", 1): FixProposal(
                reflection="Fix the first repo.",
                answer_summary="Repair first repo.",
                files=[ProposedFileEdit(path="app.py", content="print('one')\n")],
            ),
            ("second", 1): FixProposal(
                reflection="Fix the second repo.",
                answer_summary="Repair second repo.",
                files=[ProposedFileEdit(path="app.py", content="print('two')\n")],
            ),
        },
        token_costs={
            ("first", 1): 80,
            ("second", 1): 80,
        },
    )
    events: list[tuple[str, Any]] = []
    swarm = RepoBuildSwarm(
        _make_settings(),
        fix_provider=fix_provider,
        publisher=FakePublisher(events),
        janitor_runner=lambda settings: _janitor_stub(events, settings),
        workspace_root=tmp_path / "workspaces",
    )

    report = await swarm.run_until_token_budget(manifest, token_budget=100)
    await swarm.close()

    assert [outcome.target_id for outcome in report.outcomes] == ["first", "second"]
    assert report.total_tokens_used == 160
    assert report.budget_reached is True
    assert events == [
        ("publish", True),
        ("janitor", "http://127.0.0.1:8000"),
        ("publish", True),
        ("janitor", "http://127.0.0.1:8000"),
    ]


async def test_repo_swarm_halts_without_uploading_quota_failures(tmp_path: Path) -> None:
    failing_repo = tmp_path / "quota-repo"
    _write(failing_repo / "app.py", "print(missing_name)\n")

    manifest = RepoBuildManifest(
        targets=[
            RepoBuildTarget(
                target_id="quota",
                repository=str(failing_repo),
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
            )
        ]
    )

    events: list[tuple[str, Any]] = []
    publisher = FakePublisher(events)
    swarm = RepoBuildSwarm(
        _make_settings(),
        fix_provider=FakeFixProvider(
            {},
            exceptions={("quota", 1): SwarmHaltError("insufficient_quota")},
        ),
        publisher=publisher,
        janitor_runner=lambda settings: _janitor_stub(events, settings),
        workspace_root=tmp_path / "workspaces",
    )

    with pytest.raises(SwarmHaltError):
        await swarm.run_manifest(manifest)
    await swarm.close()

    assert publisher.payloads == []
    assert events == []


async def test_repo_swarm_normalizes_workspace_paths_before_upload(tmp_path: Path) -> None:
    failing_repo = tmp_path / "path-repo"
    _write(failing_repo / "app.py", "raise RuntimeError('bad')\n")

    manifest = RepoBuildManifest(
        targets=[
            RepoBuildTarget(
                target_id="paths",
                repository=str(failing_repo),
                build_commands=[BuildCommand(argv=["python", "app.py"])],
                focus_files=["app.py"],
                max_attempts=1,
            )
        ]
    )

    fix_provider = FakeFixProvider(
        {
            ("paths", 1): FixProposal(
                reflection=r"Fix the failing file at E:\Users\manzi\AppData\Local\Temp\tmp123\repo\app.py.",
                answer_summary="Replace the raised exception with a valid print call.",
                files=[ProposedFileEdit(path="app.py", content="print('fixed')\n")],
            ),
        }
    )
    events: list[tuple[str, Any]] = []
    publisher = FakePublisher(events)
    swarm = RepoBuildSwarm(
        _make_settings(),
        fix_provider=fix_provider,
        publisher=publisher,
        janitor_runner=lambda settings: _janitor_stub(events, settings),
        workspace_root=tmp_path / "workspaces",
    )

    outcomes = await swarm.run_manifest(manifest)
    await swarm.close()

    assert outcomes[0].status == "solved"
    payload = publisher.payloads[0]
    assert "<repo>/app.py" in payload.error_log
    assert "<repo>/app.py" in (payload.ai_reflection or "")
    assert "Users\\manzi" not in (payload.ai_reflection or "")
    assert "tmp" not in payload.error_log.casefold()


async def test_live_publisher_includes_idempotency_header() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"trajectory_id": "abc", "created": True}

    class FakeClient:
        async def post(self, path: str, *, content: str, headers: dict[str, str]) -> FakeResponse:
            captured["path"] = path
            captured["content"] = content
            captured["headers"] = headers
            return FakeResponse()

        async def aclose(self) -> None:
            return None

    publisher = LiveTrajectoryPublisher(_make_settings(), client=FakeClient())
    payload = TrajectoryIngestRequest(
        source="swarm",
        original_prompt="Build the project",
        error_log="Traceback",
        final_fix="Fixed output",
    )

    response = await publisher.publish(payload)

    assert response["created"] is True
    assert captured["path"] == "/v1/ingest"
    assert "X-AuraBook-Idempotency-Key" in captured["headers"]


async def test_chained_fix_provider_uses_fallback_after_empty_primary() -> None:
    context = FixContext(
        target=RepoBuildTarget(target_id="repo", repository="https://example.com/repo.git"),
        workspace=Path("."),
        attempt_number=1,
        max_attempts=3,
        repo_tree="repo",
        build_output="Traceback",
        candidate_files="app.py",
        command_history="python -m pytest",
    )
    primary = FakeFixProvider(
        {
            ("repo", 1): FixProposal(
                reflection="Primary provider has no safe edit.",
                answer_summary="No file edits available.",
                files=[],
            )
        },
        model_name="lmstudio/qwen",
    )
    fallback = FakeFixProvider(
        {
            ("repo", 1): FixProposal(
                reflection="Fallback provider patches app.py.",
                answer_summary="Apply the fallback patch.",
                files=[ProposedFileEdit(path="app.py", content="print('fixed')\n")],
            )
        },
        token_costs={("repo", 1): 91},
        model_name="openai/gpt-5-codex",
    )
    provider = ChainedFixProvider(
        [
            FixProviderStage(provider=primary, count_toward_total=False),
            FixProviderStage(provider=fallback, token_budget=20_000, count_toward_total=True),
        ]
    )

    proposal = await provider.propose_fix(context)
    await provider.close()

    assert proposal.files[0].path == "app.py"
    assert primary.calls == [("repo", 1)]
    assert fallback.calls == [("repo", 1)]
    assert provider.model_name == "openai/gpt-5-codex"
    assert provider.total_tokens_used == 91


async def test_chained_fix_provider_skips_over_budget_fallback() -> None:
    context = FixContext(
        target=RepoBuildTarget(target_id="repo", repository="https://example.com/repo.git"),
        workspace=Path("."),
        attempt_number=2,
        max_attempts=3,
        repo_tree="repo",
        build_output="Traceback",
        candidate_files="app.py",
        command_history="python -m pytest",
    )
    primary = FakeFixProvider(
        {
            ("repo", 2): FixProposal(
                reflection="Primary provider has no safe edit.",
                answer_summary="No file edits available.",
                files=[],
            )
        },
        model_name="lmstudio/qwen",
    )
    fallback = FakeFixProvider({}, model_name="openai/gpt-5-codex")
    fallback.total_tokens_used = 20_000
    provider = ChainedFixProvider(
        [
            FixProviderStage(provider=primary, count_toward_total=False),
            FixProviderStage(provider=fallback, token_budget=20_000, count_toward_total=True),
        ]
    )

    proposal = await provider.propose_fix(context)
    await provider.close()

    assert proposal.files == []
    assert primary.calls == [("repo", 2)]
    assert fallback.calls == []
    assert provider.model_name == "lmstudio/qwen"


async def test_chained_fix_provider_propagates_halt_errors() -> None:
    context = FixContext(
        target=RepoBuildTarget(target_id="repo", repository="https://example.com/repo.git"),
        workspace=Path("."),
        attempt_number=3,
        max_attempts=3,
        repo_tree="repo",
        build_output="Traceback",
        candidate_files="app.py",
        command_history="python -m pytest",
    )
    primary = FakeFixProvider(
        {},
        exceptions={("repo", 3): SwarmHaltError("insufficient_quota")},
        model_name="openai/gpt-5-codex",
    )
    fallback = FakeFixProvider({}, model_name="lmstudio/qwen")
    provider = ChainedFixProvider(
        [
            FixProviderStage(provider=primary, count_toward_total=True),
            FixProviderStage(provider=fallback, count_toward_total=False),
        ]
    )

    with pytest.raises(SwarmHaltError):
        await provider.propose_fix(context)
    await provider.close()

    assert primary.calls == [("repo", 3)]
    assert fallback.calls == []
