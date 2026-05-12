"""Repository build swarm that retries fixes and uploads trajectories."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from config import Settings
from models.schema import TrajectoryIngestRequest, TrajectoryTurn
from services.model_providers import ProviderRequestError, build_text_generation_provider
from services.openai_failures import OpenAIHaltError, is_openai_halt_error
from services.privacy import sanitize_ingest_payload, sanitize_machine_text
from worker.janitor import _run_job

logger = logging.getLogger(__name__)

_FIX_SYSTEM_PROMPT = (
    "You are AuraBook's repository repair agent. "
    "Your job is to fix build or test failures with the smallest viable file edits. "
    "Return JSON only. Do not wrap it in markdown. "
    "Schema: "
    '{"reflection":"one-paragraph diagnosis","answer_summary":"one concise summary of the fix",'
    '"files":[{"path":"relative/path","content":"full replacement file content"}]}. '
    "Only include repository-relative file paths. "
    "Do not invent files outside the repository. "
    "If you are not confident, still return your best minimal fix."
)

_TEXT_FILE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".txt",
    ".ini",
    ".cfg",
    ".env",
    ".cjs",
    ".mjs",
    ".rs",
}

_ROOT_PRIORITY_FILES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pytest.ini",
    "Cargo.toml",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
)

def _stable_trajectory_id(target_id: str, repository: str, error_log: str) -> str:
    """Return a stable trajectory UUID for idempotent swarm uploads."""
    source = f"https://aurabook.dev/swarm/{target_id}:{repository}:{error_log[:240]}"
    return str(uuid5(NAMESPACE_URL, source))


class BuildCommand(BaseModel):
    """One setup or build command executed by the swarm."""

    argv: list[StrictStr] | None = None
    shell: StrictStr | None = None
    timeout_s: StrictInt = 600

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_shape(self) -> "BuildCommand":
        has_argv = bool(self.argv)
        has_shell = bool(self.shell)
        if has_argv == has_shell:
            raise ValueError("exactly one of argv or shell is required")
        return self

    def display(self) -> str:
        """Return a human-readable command string."""
        if self.shell is not None:
            return self.shell
        assert self.argv is not None
        return " ".join(self.argv)


class RepoBuildTarget(BaseModel):
    """One repository that the swarm should attempt to build and repair."""

    target_id: StrictStr
    repository: StrictStr
    goal: StrictStr = "Build and repair this project until the build succeeds."
    ref: StrictStr | None = None
    workdir: StrictStr | None = None
    setup_commands: list[BuildCommand] = Field(default_factory=list)
    build_commands: list[BuildCommand] = Field(default_factory=list)
    focus_files: list[StrictStr] = Field(default_factory=list)
    env: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    max_attempts: StrictInt = 3
    max_candidate_files: StrictInt = 8
    max_file_bytes: StrictInt = 14_000

    model_config = ConfigDict(extra="forbid")


class RepoBuildManifest(BaseModel):
    """Collection of repositories processed by the swarm."""

    targets: list[RepoBuildTarget]

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_path(cls, path: Path) -> "RepoBuildManifest":
        """Load a manifest from a JSON file."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class ProposedFileEdit(BaseModel):
    """One file replacement emitted by the fix model."""

    path: StrictStr
    content: StrictStr

    model_config = ConfigDict(extra="forbid")


class FixProposal(BaseModel):
    """Model-produced fix proposal for one failed build attempt."""

    reflection: StrictStr
    answer_summary: StrictStr
    files: list[ProposedFileEdit] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


@dataclass(slots=True)
class CommandResult:
    """Captured stdout/stderr for one executed command."""

    command_display: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        """Return combined stderr and stdout with stderr first."""
        parts = [part.strip() for part in (self.stderr, self.stdout) if part.strip()]
        return "\n\n".join(parts)


@dataclass(slots=True)
class FixContext:
    """Context passed to the fix provider for one attempt."""

    target: RepoBuildTarget
    workspace: Path
    attempt_number: int
    max_attempts: int
    repo_tree: str
    build_output: str
    candidate_files: str
    command_history: str


@dataclass(slots=True)
class RepoBuildOutcome:
    """High-level result for one repository target."""

    target_id: str
    repository: str
    status: str
    attempts_used: int
    trajectory_id: str | None = None
    message: str = ""


@dataclass(slots=True)
class RepoBuildBudgetReport:
    """Summary of a token-budgeted swarm run."""

    outcomes: list[RepoBuildOutcome]
    total_tokens_used: int
    token_budget: int
    budget_reached: bool


class SwarmHaltError(OpenAIHaltError):
    """Fatal error that should stop the swarm without uploading a failed trajectory."""


class FixProvider(Protocol):
    """Behavior required for any build-fix provider."""

    async def propose_fix(self, context: FixContext) -> FixProposal:
        """Return the next fix proposal for the failed repository build."""

    async def close(self) -> None:
        """Release any provider resources."""

    @property
    def total_tokens_used(self) -> int:
        """Return the cumulative OpenAI response-token usage."""

    @property
    def model_name(self) -> str:
        """Return the identifier used to attribute repair attempts."""


@dataclass(slots=True)
class FixProviderStage:
    """One provider stage in a chained fixer."""

    provider: FixProvider
    token_budget: int | None = None
    count_toward_total: bool = False


class TrajectoryPublisher(Protocol):
    """Behavior required for publishing trajectories to the cloud."""

    async def publish(self, payload: TrajectoryIngestRequest) -> dict[str, Any]:
        """Upload one trajectory payload."""

    async def close(self) -> None:
        """Release any publisher resources."""


class OpenAIFixProvider:
    """Provider-backed repository fixer that outputs JSON file replacements."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: Any | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retryable_exceptions: Sequence[type[Exception]] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or build_text_generation_provider(settings, purpose="fix")
        self._sleep = sleep_func
        self._total_tokens_used = 0
        self._retryable_exceptions = tuple(
            retryable_exceptions or (ProviderRequestError,)
        )

    async def propose_fix(self, context: FixContext) -> FixProposal:
        """Return a fix proposal for the current repository state."""
        prompt = self._build_prompt(context)
        for attempt in range(1, self._settings.openai_retry_attempts + 1):
            try:
                response = await self._provider.generate_text(
                    system_prompt=_FIX_SYSTEM_PROMPT,
                    user_prompt=prompt,
                )
                self._total_tokens_used += int(response.total_tokens or 0)
                return self._parse_response(response.text)
            except self._retryable_exceptions as exc:
                if getattr(exc, "halt", False) or is_openai_halt_error(exc):
                    raise SwarmHaltError(str(exc)) from exc
                if not getattr(exc, "retryable", True):
                    raise
                if attempt >= self._settings.openai_retry_attempts:
                    raise
                delay = self._settings.openai_retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Fix proposal attempt %d failed with %s; retrying in %.1fs",
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await self._sleep(delay)

    async def close(self) -> None:
        """Close the underlying provider when supported."""
        close = getattr(self._provider, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result

    @property
    def total_tokens_used(self) -> int:
        """Return the cumulative OpenAI response-token usage."""
        return self._total_tokens_used

    @property
    def model_name(self) -> str:
        """Return the configured fix model name."""
        return str(getattr(self._provider, "model_name", self._settings.resolve_fix_model()))

    def _build_prompt(self, context: FixContext) -> str:
        build_output = context.build_output[:10_000]
        candidate_files = context.candidate_files[:32_000]
        repo_tree = context.repo_tree[:6_000]
        return (
            f"Repository: {context.target.repository}\n"
            f"Goal: {context.target.goal}\n"
            f"Attempt: {context.attempt_number}/{context.max_attempts}\n"
            f"Workdir: {context.workspace}\n"
            f"Build commands: {context.command_history}\n\n"
            f"Current failing build output:\n{build_output}\n\n"
            f"Repository tree:\n{repo_tree}\n\n"
            f"Candidate files:\n{candidate_files}\n"
        )

    def _parse_response(self, raw_text: str) -> FixProposal:
        stripped = raw_text.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("OpenAI fix response did not contain JSON")
        candidate = stripped[start : end + 1]
        try:
            return FixProposal.model_validate(json.loads(candidate))
        except JSONDecodeError:
            cleaned = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r"\\\\", candidate)
            return FixProposal.model_validate(json.loads(cleaned))


class HeuristicFixProvider:
    """Fallback fixer with a few deterministic repairs for common failures."""

    @property
    def total_tokens_used(self) -> int:
        """Heuristic mode does not spend OpenAI response tokens."""
        return 0

    @property
    def model_name(self) -> str:
        """Return the static heuristic provider name."""
        return "heuristic-fix-provider"

    async def propose_fix(self, context: FixContext) -> FixProposal:
        """Return a best-effort heuristic fix proposal."""
        output = context.build_output.lower()
        if "corsmiddleware" in output and "not defined" in output:
            for path, content in _iter_candidate_file_pairs(context.candidate_files):
                if "fastapi" in content and "CORSMiddleware" in content and "from fastapi.middleware.cors import CORSMiddleware" not in content:
                    updated = (
                        "from fastapi.middleware.cors import CORSMiddleware\n" + content
                        if not content.startswith("from fastapi.middleware.cors import CORSMiddleware")
                        else content
                    )
                    return FixProposal(
                        reflection="The build failed because CORSMiddleware was used without importing it.",
                        answer_summary="Import CORSMiddleware from fastapi.middleware.cors before registering middleware.",
                        files=[ProposedFileEdit(path=path, content=updated)],
                    )
        return FixProposal(
            reflection="No deterministic repair was available for this failure.",
            answer_summary="No confident automatic fix found.",
            files=[],
        )

    async def close(self) -> None:
        """No-op close hook."""
        return None


class ChainedFixProvider:
    """Try multiple fix providers in order, with optional per-provider token caps."""

    def __init__(self, stages: Sequence[FixProviderStage]) -> None:
        if not stages:
            raise ValueError("At least one fix-provider stage is required")
        self._stages = list(stages)
        self._last_model_name = self._provider_name(self._stages[0].provider)
        self._budget_logged: set[str] = set()

    async def propose_fix(self, context: FixContext) -> FixProposal:
        """Return the first viable proposal from the provider chain."""
        last_exception: Exception | None = None
        last_empty_proposal: FixProposal | None = None
        attempted_provider = False

        for stage in self._stages:
            provider_name = self._provider_name(stage.provider)
            if stage.token_budget is not None and int(stage.provider.total_tokens_used or 0) >= stage.token_budget:
                if provider_name not in self._budget_logged:
                    logger.info(
                        "Skipping fix provider %s because it reached its token budget (%d).",
                        provider_name,
                        stage.token_budget,
                    )
                    self._budget_logged.add(provider_name)
                continue

            attempted_provider = True
            self._last_model_name = provider_name
            try:
                proposal = await stage.provider.propose_fix(context)
            except SwarmHaltError:
                raise
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    "Fix provider %s failed for %s attempt %d: %s",
                    provider_name,
                    context.target.repository,
                    context.attempt_number,
                    exc,
                )
                continue

            if proposal.files:
                return proposal

            last_empty_proposal = proposal
            logger.info(
                "Fix provider %s returned no file edits for %s attempt %d; trying the next provider.",
                provider_name,
                context.target.repository,
                context.attempt_number,
            )

        if last_empty_proposal is not None:
            return last_empty_proposal
        if last_exception is not None:
            raise last_exception
        if not attempted_provider:
            return FixProposal(
                reflection="All configured repair providers were unavailable or over budget.",
                answer_summary="No fix provider available within the configured token budget.",
                files=[],
            )
        return FixProposal(
            reflection="No configured repair provider produced a usable file edit.",
            answer_summary="No confident automatic fix found.",
            files=[],
        )

    async def close(self) -> None:
        """Close all unique providers in the chain."""
        closed_ids: set[int] = set()
        for stage in self._stages:
            provider_id = id(stage.provider)
            if provider_id in closed_ids:
                continue
            closed_ids.add(provider_id)
            await stage.provider.close()

    @property
    def total_tokens_used(self) -> int:
        """Return the counted token usage across budget-tracked stages."""
        total = 0
        for stage in self._stages:
            if not stage.count_toward_total:
                continue
            total += int(stage.provider.total_tokens_used or 0)
        return total

    @property
    def model_name(self) -> str:
        """Return the provider/model name used most recently."""
        return self._last_model_name

    @staticmethod
    def _provider_name(provider: FixProvider) -> str:
        """Return a human-readable provider/model name."""
        return str(getattr(provider, "model_name", "") or "unknown-fix-provider")


class LiveTrajectoryPublisher:
    """Signed HTTP publisher for Cloud Brain ingest requests."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.cloud_api_base_url.rstrip("/"),
            timeout=20.0,
        )
        self._owns_client = client is None

    async def publish(self, payload: TrajectoryIngestRequest) -> dict[str, Any]:
        """Upload one trajectory payload with signing and idempotency."""
        body = json.dumps(payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        response = await self._client.post(
            "/v1/ingest",
            content=body,
            headers=self._headers(body),
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        """Close the underlying HTTP client when owned here."""
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, body: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-AuraBook-Idempotency-Key": hashlib.sha256(
                f"/v1/ingest:{body}".encode("utf-8")
            ).hexdigest(),
        }
        if not self._settings.api_shared_secret:
            return headers
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self._settings.api_shared_secret.encode("utf-8"),
            f"{timestamp}.{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["X-AuraBook-Timestamp"] = timestamp
        headers["X-AuraBook-Signature"] = signature
        return headers


class RepoBuildSwarm:
    """Runs repository builds, retries fixes, and uploads trajectories."""

    def __init__(
        self,
        settings: Settings,
        *,
        fix_provider: FixProvider,
        publisher: TrajectoryPublisher,
        janitor_runner: Callable[[Settings], Awaitable[Any]] | None = _run_job,
        workspace_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._fix_provider = fix_provider
        self._publisher = publisher
        self._janitor_runner = janitor_runner
        self._workspace_root = workspace_root or Path(".swarm/workspaces").resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    async def run_manifest(
        self,
        manifest: RepoBuildManifest,
        *,
        max_projects: int | None = None,
        run_janitor_for_solved: bool = True,
    ) -> list[RepoBuildOutcome]:
        """Process the targets in order and publish solved/failed trajectories."""
        solved_payloads: list[TrajectoryIngestRequest] = []
        failed_payloads: list[TrajectoryIngestRequest] = []
        outcomes: list[RepoBuildOutcome] = []
        targets = manifest.targets[: max_projects] if max_projects is not None else manifest.targets

        for target in targets:
            outcome, solved_payload, failed_payload = await self._run_target(target)
            outcomes.append(outcome)
            if solved_payload is not None:
                solved_payloads.append(solved_payload)
            if failed_payload is not None:
                failed_payloads.append(failed_payload)

        for payload in solved_payloads:
            response = await self._publisher.publish(payload)
            logger.info("Uploaded solved trajectory %s created=%s", response.get("trajectory_id"), response.get("created"))
        if solved_payloads and run_janitor_for_solved and self._janitor_runner is not None:
            result = await self._janitor_runner(self._settings)
            logger.info("Solved janitor batch finished: %s", getattr(result, "model_dump", lambda: result)())
        for payload in failed_payloads:
            response = await self._publisher.publish(payload)
            logger.info("Uploaded failed trajectory %s created=%s", response.get("trajectory_id"), response.get("created"))

        return outcomes

    async def run_until_token_budget(
        self,
        manifest: RepoBuildManifest,
        *,
        token_budget: int,
        max_projects: int | None = None,
        run_janitor_for_solved: bool = True,
    ) -> RepoBuildBudgetReport:
        """Process targets until the OpenAI token budget is exhausted or the manifest ends."""
        outcomes: list[RepoBuildOutcome] = []
        total_tokens_used = 0
        targets = manifest.targets[: max_projects] if max_projects is not None else manifest.targets

        for target in targets:
            if total_tokens_used >= token_budget:
                break

            before_fix_tokens = self._fix_provider_tokens()
            outcome, solved_payload, failed_payload = await self._run_target(target)
            outcomes.append(outcome)
            total_tokens_used += max(0, self._fix_provider_tokens() - before_fix_tokens)

            if solved_payload is not None:
                response = await self._publisher.publish(solved_payload)
                logger.info(
                    "Uploaded solved trajectory %s created=%s",
                    response.get("trajectory_id"),
                    response.get("created"),
                )
                if run_janitor_for_solved and self._janitor_runner is not None:
                    result = await self._janitor_runner(self._settings)
                    janitor_tokens = int(getattr(result, "llm_tokens_used", 0) or 0)
                    total_tokens_used += janitor_tokens
                    logger.info(
                        "Solved janitor batch finished: %s (tokens=%d)",
                        getattr(result, "model_dump", lambda: result)(),
                        janitor_tokens,
                    )
            elif failed_payload is not None:
                response = await self._publisher.publish(failed_payload)
                logger.info(
                    "Uploaded failed trajectory %s created=%s",
                    response.get("trajectory_id"),
                    response.get("created"),
                )

        return RepoBuildBudgetReport(
            outcomes=outcomes,
            total_tokens_used=total_tokens_used,
            token_budget=token_budget,
            budget_reached=total_tokens_used >= token_budget,
        )

    async def close(self) -> None:
        """Close dependent services."""
        await self._fix_provider.close()
        await self._publisher.close()

    def _fix_provider_tokens(self) -> int:
        """Return cumulative token usage from the fix provider."""
        return int(getattr(self._fix_provider, "total_tokens_used", 0) or 0)

    def _fix_provider_name(self) -> str:
        """Return the active fixer model/provider name."""
        provider_name = getattr(self._fix_provider, "model_name", "")
        return str(provider_name or "unknown-fix-provider")

    def _attempt_duration_ms(self, started_at: float) -> int:
        """Return elapsed wall-clock time for one repair attempt."""
        return max(0, int((time.perf_counter() - started_at) * 1000))

    def _attempt_outcome(self, result: CommandResult) -> str:
        """Return a concise outcome summary for one verification rerun."""
        if result.returncode == 0:
            return "success"
        summary = sanitize_machine_text(result.combined_output[:240], repo_root=None).strip()
        if not summary:
            return "failure"
        first_line = summary.splitlines()[0].strip()
        return f"failure: {first_line}"

    def _history_attempted_models(self, history: Sequence[TrajectoryTurn]) -> list[str]:
        """Return the distinct model/provider names seen in the attempt history."""
        seen: set[str] = set()
        ordered: list[str] = []
        for turn in history:
            if not turn.model or turn.model in seen:
                continue
            seen.add(turn.model)
            ordered.append(turn.model)
        return ordered

    async def _run_target(
        self,
        target: RepoBuildTarget,
    ) -> tuple[RepoBuildOutcome, TrajectoryIngestRequest | None, TrajectoryIngestRequest | None]:
        with tempfile.TemporaryDirectory(dir=self._workspace_root) as temp_dir:
            workspace_root = Path(temp_dir).resolve()
            repo_root = await self._prepare_workspace(target, workspace_root)
            effective_root = (repo_root / target.workdir).resolve() if target.workdir else repo_root
            if not effective_root.is_dir():
                raise FileNotFoundError(f"Target workdir does not exist: {effective_root}")

            setup_commands, build_commands = self._resolved_commands(target, effective_root)
            if not build_commands:
                return (
                    RepoBuildOutcome(
                        target_id=target.target_id,
                        repository=target.repository,
                        status="skipped",
                        attempts_used=0,
                        message="No build command could be inferred.",
                    ),
                    None,
                    None,
                )

            command_env = await self._build_command_env(target, effective_root)
            initial_state = self._render_candidate_files(effective_root, target, error_text="")
            initial_result = await self._run_command_set(
                setup_commands + build_commands,
                effective_root,
                command_env,
            )
            if initial_result.returncode == 0:
                return (
                    RepoBuildOutcome(
                        target_id=target.target_id,
                        repository=target.repository,
                        status="clean",
                        attempts_used=0,
                        message="Build succeeded without intervention.",
                    ),
                    None,
                    None,
                )

            history: list[TrajectoryTurn] = []
            last_result = initial_result
            last_proposal: FixProposal | None = None
            repo_tree = self._repo_tree(effective_root)
            build_command_history = " | ".join(command.display() for command in setup_commands + build_commands)
            command_sequence = [command.display() for command in setup_commands + build_commands]
            for attempt_number in range(1, target.max_attempts + 1):
                attempt_prompt = (
                    f"Attempt {attempt_number}: {last_result.command_display or build_command_history}\n\n"
                    f"Build commands:\n{build_command_history}\n\n"
                    f"Build output:\n{sanitize_machine_text(last_result.combined_output[:4000], repo_root=effective_root)}"
                )
                candidate_files = self._render_candidate_files(
                    effective_root,
                    target,
                    error_text=last_result.combined_output,
                )
                before_tokens = self._fix_provider_tokens()
                started_at = time.perf_counter()
                try:
                    proposal = await self._fix_provider.propose_fix(
                        FixContext(
                            target=target,
                            workspace=effective_root,
                            attempt_number=attempt_number,
                            max_attempts=target.max_attempts,
                            repo_tree=repo_tree,
                            build_output=sanitize_machine_text(
                                last_result.combined_output,
                                repo_root=effective_root,
                            ),
                            candidate_files=candidate_files,
                            command_history=build_command_history,
                        )
                    )
                    duration_ms = self._attempt_duration_ms(started_at)
                    token_delta = max(0, self._fix_provider_tokens() - before_tokens)
                    last_proposal = proposal
                    changed_paths = self._apply_proposal(effective_root, proposal)
                except SwarmHaltError:
                    logger.error(
                        "Stopping swarm during %s because OpenAI returned a fatal token/quota error.",
                        target.repository,
                    )
                    raise
                except Exception as exc:
                    logger.exception(
                        "Attempt %d/%d failed before rerun for %s: %s",
                        attempt_number,
                        target.max_attempts,
                        target.repository,
                        exc,
                    )
                    duration_ms = self._attempt_duration_ms(started_at)
                    token_delta = max(0, self._fix_provider_tokens() - before_tokens)
                    error_summary = sanitize_machine_text(
                        f"{type(exc).__name__}: {exc}",
                        repo_root=effective_root,
                    )
                    history.append(
                        TrajectoryTurn(
                            prompt=attempt_prompt,
                            response=(
                                "Reflection: The repair attempt failed before it could be applied.\n"
                                f"Summary: {error_summary}"
                            ),
                            model=self._fix_provider_name(),
                            thought="The repair attempt failed before any file changes were applied.",
                            tried=error_summary,
                            outcome="proposal_error",
                            commands=command_sequence,
                            changed_files=[],
                            token_count=token_delta or None,
                            duration_ms=duration_ms,
                        )
                    )
                    last_proposal = FixProposal(
                        reflection="The repair attempt failed before any file changes were applied.",
                        answer_summary=error_summary,
                        files=[],
                    )
                    continue
                rerun_result = await self._run_command_set(
                    setup_commands + build_commands,
                    effective_root,
                    command_env,
                )
                history.append(
                    TrajectoryTurn(
                        prompt=attempt_prompt,
                        response=(
                            f"Reflection: {sanitize_machine_text(proposal.reflection, repo_root=effective_root)}\n"
                            f"Summary: {sanitize_machine_text(proposal.answer_summary, repo_root=effective_root)}\n"
                            f"Changed files: {', '.join(changed_paths) if changed_paths else 'none'}\n"
                            f"Rerun result: {self._attempt_outcome(rerun_result)}"
                        ),
                        model=self._fix_provider_name(),
                        thought=sanitize_machine_text(proposal.reflection, repo_root=effective_root),
                        tried=sanitize_machine_text(proposal.answer_summary, repo_root=effective_root),
                        outcome=self._attempt_outcome(rerun_result),
                        commands=command_sequence,
                        changed_files=changed_paths,
                        token_count=token_delta or None,
                        duration_ms=duration_ms,
                    )
                )
                if rerun_result.returncode == 0:
                    payload = self._success_payload(
                        target=target,
                        repo_root=effective_root,
                        setup_commands=setup_commands,
                        build_commands=build_commands,
                        initial_state=initial_state,
                        proposal=proposal,
                        history=history,
                        final_files=changed_paths,
                        final_result=rerun_result,
                    )
                    return (
                        RepoBuildOutcome(
                            target_id=target.target_id,
                            repository=target.repository,
                            status="solved",
                            attempts_used=attempt_number,
                            trajectory_id=payload.trajectory_id,
                            message=proposal.answer_summary,
                        ),
                        payload,
                        None,
                    )
                last_result = rerun_result

            payload = self._failure_payload(
                target=target,
                repo_root=effective_root,
                setup_commands=setup_commands,
                build_commands=build_commands,
                initial_state=initial_state,
                last_result=last_result,
                history=history,
                last_proposal=last_proposal,
            )
            return (
                RepoBuildOutcome(
                    target_id=target.target_id,
                    repository=target.repository,
                    status="failed",
                    attempts_used=target.max_attempts,
                    trajectory_id=payload.trajectory_id,
                    message="Failed after max attempts.",
                ),
                None,
                payload,
            )

    async def _prepare_workspace(self, target: RepoBuildTarget, workspace_root: Path) -> Path:
        """Clone or copy the target repository into a temporary workspace."""
        if _is_local_repository(target.repository):
            source = Path(target.repository).expanduser().resolve()
            destination = workspace_root / "repo"
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "dist", "build", "__pycache__"))
            return destination

        destination = workspace_root / "repo"
        clone_result = await self._run_process(
            ["git", "clone", "--depth", "1", target.repository, str(destination)],
            cwd=workspace_root,
            env={},
            timeout_s=900,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"git clone failed for {target.repository}: {clone_result.combined_output}")
        if target.ref:
            checkout_result = await self._run_process(
                ["git", "checkout", target.ref],
                cwd=destination,
                env={},
                timeout_s=300,
            )
            if checkout_result.returncode != 0:
                raise RuntimeError(f"git checkout failed for {target.ref}: {checkout_result.combined_output}")
        return destination

    def _resolved_commands(
        self,
        target: RepoBuildTarget,
        repo_root: Path,
    ) -> tuple[list[BuildCommand], list[BuildCommand]]:
        """Return setup and build commands, inferring defaults when omitted."""
        if target.build_commands:
            return target.setup_commands, target.build_commands

        package_json = repo_root / "package.json"
        pyproject = repo_root / "pyproject.toml"
        requirements = repo_root / "requirements.txt"
        cargo = repo_root / "Cargo.toml"

        if package_json.exists():
            scripts = _package_scripts(package_json)
            setup = list(target.setup_commands)
            if not setup:
                setup = [BuildCommand(argv=["npm", "install"], timeout_s=1200)]
            if "build" in scripts:
                return setup, [BuildCommand(argv=["npm", "run", "build"], timeout_s=1200)]
            if "test" in scripts:
                return setup, [BuildCommand(argv=["npm", "test", "--", "--runInBand"], timeout_s=1200)]
            return setup, [BuildCommand(argv=["npm", "exec", "tsc", "--", "--noEmit"], timeout_s=1200)]

        if pyproject.exists() or requirements.exists() or (repo_root / "setup.py").exists():
            setup = list(target.setup_commands)
            if not setup:
                if pyproject.exists() or (repo_root / "setup.py").exists():
                    setup = [BuildCommand(argv=["python", "-m", "pip", "install", "-e", "."], timeout_s=1200)]
                elif requirements.exists():
                    setup = [BuildCommand(argv=["python", "-m", "pip", "install", "-r", "requirements.txt"], timeout_s=1200)]
            if (repo_root / "tests").exists() or (repo_root / "pytest.ini").exists():
                return setup, [BuildCommand(argv=["python", "-m", "pytest", "-q"], timeout_s=1200)]
            return setup, [BuildCommand(argv=["python", "-m", "compileall", "."], timeout_s=1200)]

        if cargo.exists():
            return list(target.setup_commands), [BuildCommand(argv=["cargo", "build"], timeout_s=1200)]

        return list(target.setup_commands), []

    async def _build_command_env(
        self,
        target: RepoBuildTarget,
        repo_root: Path,
    ) -> dict[str, str]:
        """Return command environment overrides, including an isolated Python venv when needed."""
        env = dict(target.env)
        if not self._needs_python_venv(target, repo_root):
            return env

        venv_root = repo_root / ".aurabook-venv"
        python_executable = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python_executable.exists():
            create_result = await self._run_process(
                ["python", "-m", "venv", str(venv_root)],
                cwd=repo_root,
                env={},
                timeout_s=600,
                display=f"python -m venv {venv_root}",
            )
            if create_result.returncode != 0:
                raise RuntimeError(
                    f"failed to create isolated venv for {target.repository}: {create_result.combined_output}"
                )

        scripts_dir = python_executable.parent
        current_path = env.get("PATH", os.environ.get("PATH", ""))
        env["PATH"] = f"{scripts_dir}{os.pathsep}{current_path}" if current_path else str(scripts_dir)
        env["VIRTUAL_ENV"] = str(venv_root)
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        env.setdefault("PYTHONNOUSERSITE", "1")
        return env

    def _needs_python_venv(
        self,
        target: RepoBuildTarget,
        repo_root: Path,
    ) -> bool:
        """Return True when the target appears to execute Python tooling."""
        if any((repo_root / marker).exists() for marker in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")):
            return True
        commands = [*target.setup_commands, *target.build_commands]
        for command in commands:
            if command.argv is not None:
                joined = " ".join(command.argv).lower()
            else:
                joined = (command.shell or "").lower()
            if any(token in joined for token in ("python", "pip", "pytest")):
                return True
        return False

    async def _run_command_set(
        self,
        commands: Sequence[BuildCommand],
        cwd: Path,
        extra_env: Mapping[str, str],
    ) -> CommandResult:
        """Run setup/build commands sequentially until one fails."""
        last_result = CommandResult(command_display="", returncode=0, stdout="", stderr="")
        for command in commands:
            last_result = await self._execute_command(command, cwd, extra_env)
            if last_result.returncode != 0:
                return last_result
        return last_result

    async def _execute_command(
        self,
        command: BuildCommand,
        cwd: Path,
        extra_env: Mapping[str, str],
    ) -> CommandResult:
        """Run one command."""
        if command.argv is not None:
            argv = list(command.argv)
        else:
            assert command.shell is not None
            argv = _platform_shell_argv(command.shell)
        return await self._run_process(
            argv,
            cwd=cwd,
            env=extra_env,
            timeout_s=command.timeout_s,
            display=command.display(),
        )

    async def _run_process(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: int,
        display: str | None = None,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env={**os.environ, **env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            timed_out = True
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return CommandResult(
            command_display=display or " ".join(argv),
            returncode=124 if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )

    def _render_candidate_files(self, repo_root: Path, target: RepoBuildTarget, *, error_text: str) -> str:
        """Render a bounded set of candidate file contents for fixing."""
        candidates: list[Path] = []
        seen: set[Path] = set()

        def add_path(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                return
            if not resolved.is_relative_to(repo_root):
                return
            if resolved.suffix and resolved.suffix.lower() not in _TEXT_FILE_SUFFIXES:
                return
            if resolved.stat().st_size > target.max_file_bytes:
                return
            seen.add(resolved)
            candidates.append(resolved)

        for relative_path in target.focus_files:
            add_path(repo_root / relative_path)

        for token in _extract_path_tokens(error_text):
            add_path(repo_root / token)

        for root_file in _ROOT_PRIORITY_FILES:
            add_path(repo_root / root_file)

        if len(candidates) < target.max_candidate_files:
            for path in repo_root.rglob("*"):
                if len(candidates) >= target.max_candidate_files:
                    break
                add_path(path)

        if not candidates:
            return "(no candidate files found)"

        rendered: list[str] = []
        for path in candidates[: target.max_candidate_files]:
            relative = path.relative_to(repo_root).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rendered.append(f"--- {relative} ---\n{content.strip()}\n")
        return "\n".join(rendered)

    def _repo_tree(self, repo_root: Path, *, limit: int = 160) -> str:
        """Render a compact repository tree for model context."""
        entries: list[str] = []
        for path in sorted(repo_root.rglob("*")):
            if len(entries) >= limit:
                break
            if any(part in {"node_modules", ".git", ".venv", "__pycache__", "dist", "build"} for part in path.parts):
                continue
            relative = path.relative_to(repo_root).as_posix()
            entries.append(relative + ("/" if path.is_dir() else ""))
        return "\n".join(entries)

    def _apply_proposal(self, repo_root: Path, proposal: FixProposal) -> list[str]:
        """Apply file replacements produced by the fix provider."""
        changed_paths: list[str] = []
        for file_edit in proposal.files:
            target_path = (repo_root / file_edit.path).resolve()
            if not target_path.is_relative_to(repo_root):
                raise ValueError(f"Proposed file escapes repository: {file_edit.path}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(file_edit.content, encoding="utf-8")
            changed_paths.append(target_path.relative_to(repo_root).as_posix())
        return changed_paths

    def _success_payload(
        self,
        *,
        target: RepoBuildTarget,
        repo_root: Path,
        setup_commands: Sequence[BuildCommand],
        build_commands: Sequence[BuildCommand],
        initial_state: str,
        proposal: FixProposal,
        history: list[TrajectoryTurn],
        final_files: list[str],
        final_result: CommandResult,
    ) -> TrajectoryIngestRequest:
        """Build the solved trajectory payload."""
        rendered_files = self._render_final_files(repo_root, target, final_files)
        error_log = history[0].prompt.split("Build output:\n", maxsplit=1)[-1]
        final_fix = (
            f"{sanitize_machine_text(proposal.answer_summary, repo_root=repo_root)}\n\n"
            f"Applied files:\n{rendered_files}\n\n"
            f"Verification output:\n{sanitize_machine_text(final_result.combined_output or 'Command completed successfully.', repo_root=repo_root)}"
        ).strip()
        trajectory_id = _stable_trajectory_id(target.target_id, target.repository, error_log)
        return sanitize_ingest_payload(
            TrajectoryIngestRequest(
            trajectory_id=trajectory_id,
            source="swarm",
            client_id="repo-build-swarm",
            original_goal=target.goal,
            original_prompt=f"Build and repair repository {target.repository}",
            broken_code=initial_state,
            error_log=error_log,
            ai_reflection=proposal.reflection,
            final_fix=final_fix,
            history=history,
            metadata={
                "repository": target.repository,
                "target_id": target.target_id,
                "ref": target.ref,
                "workdir": target.workdir,
                "resolved": True,
                "attempt_limit": target.max_attempts,
                "attempts_used": len(history),
                "attempt_count": len(history),
                "attempted_models": self._history_attempted_models(history),
                "failed_model_count": len(self._history_attempted_models(history)),
                "setup_commands": [command.display() for command in setup_commands],
                "build_commands": [command.display() for command in build_commands],
                "focus_files": list(target.focus_files),
                "build_agent": "repo-build-swarm",
            },
            ),
            repo_root=repo_root,
        )

    def _failure_payload(
        self,
        *,
        target: RepoBuildTarget,
        repo_root: Path,
        setup_commands: Sequence[BuildCommand],
        build_commands: Sequence[BuildCommand],
        initial_state: str,
        last_result: CommandResult,
        history: list[TrajectoryTurn],
        last_proposal: FixProposal | None,
    ) -> TrajectoryIngestRequest:
        """Build the unresolved trajectory payload."""
        initial_error = history[0].prompt.split("Build output:\n", maxsplit=1)[-1] if history else last_result.combined_output
        reflection = (
            last_proposal.reflection
            if last_proposal is not None
            else "The swarm exhausted its repair budget without producing a passing build."
        )
        final_fix = (
            f"UNRESOLVED after {target.max_attempts} attempts.\n\n"
            f"Last proposal summary: {sanitize_machine_text((last_proposal.answer_summary if last_proposal is not None else 'No proposal generated.'), repo_root=repo_root)}\n\n"
            f"Latest build output:\n{sanitize_machine_text(last_result.combined_output, repo_root=repo_root)}"
        ).strip()
        trajectory_id = _stable_trajectory_id(target.target_id, target.repository, initial_error)
        return sanitize_ingest_payload(
            TrajectoryIngestRequest(
            trajectory_id=trajectory_id,
            source="swarm",
            client_id="repo-build-swarm",
            original_goal=target.goal,
            original_prompt=f"Build and repair repository {target.repository}",
            broken_code=initial_state,
            error_log=initial_error,
            ai_reflection=reflection,
            final_fix=final_fix,
            history=history,
            metadata={
                "repository": target.repository,
                "target_id": target.target_id,
                "ref": target.ref,
                "workdir": target.workdir,
                "resolved": False,
                "attempt_limit": target.max_attempts,
                "attempts_used": len(history),
                "attempt_count": len(history),
                "attempted_models": self._history_attempted_models(history),
                "failed_model_count": len(self._history_attempted_models(history)),
                "setup_commands": [command.display() for command in setup_commands],
                "build_commands": [command.display() for command in build_commands],
                "focus_files": list(target.focus_files),
                "latest_error": last_result.combined_output[:8_000],
                "build_agent": "repo-build-swarm",
            },
            ),
            repo_root=repo_root,
        )

    def _render_final_files(
        self,
        repo_root: Path,
        target: RepoBuildTarget,
        changed_paths: Sequence[str],
    ) -> str:
        """Render the final changed files after a successful repair."""
        if not changed_paths:
            return "(no file changes were applied)"
        rendered: list[str] = []
        for relative_path in changed_paths[: target.max_candidate_files]:
            path = (repo_root / relative_path).resolve()
            if not path.is_relative_to(repo_root) or not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rendered.append(f"--- {relative_path} ---\n{content.strip()}\n")
        if not rendered:
            return "(no file changes were applied)"
        return "\n".join(rendered)


def _package_scripts(path: Path) -> dict[str, Any]:
    """Read package.json scripts if available."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = raw.get("scripts", {})
    return scripts if isinstance(scripts, dict) else {}


def _extract_path_tokens(text: str) -> list[str]:
    """Extract likely file paths from build output."""
    pattern = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|js|jsx|ts|tsx|json|toml|ya?ml|rs|cjs|mjs))")
    return pattern.findall(text)


def _iter_candidate_file_pairs(rendered_files: str) -> Iterable[tuple[str, str]]:
    """Yield (path, content) pairs from the rendered candidate file bundle."""
    current_path: str | None = None
    current_lines: list[str] = []
    for line in rendered_files.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_path is not None:
                yield current_path, "\n".join(current_lines)
            current_path = line.removeprefix("--- ").removesuffix(" ---").strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_path is not None:
        yield current_path, "\n".join(current_lines)


def _platform_shell_argv(command: str) -> list[str]:
    """Return a platform-appropriate shell argv for one string command."""
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-Command", command]
    return ["bash", "-lc", command]


def _is_local_repository(source: str) -> bool:
    """Return True when the repository source points to a local directory."""
    if "://" in source:
        return False
    return Path(source).expanduser().exists()
