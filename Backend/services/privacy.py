"""Path and privacy sanitizers for uploaded trajectories."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from models.schema import StoredTrajectory, TrajectoryIngestRequest, TrajectoryTurn

_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_>])(?P<path>(?:[A-Za-z]:[\\/]|\\\\[^\\/:*?\"<>|\r\n]+\\[^\\/:*?\"<>|\r\n]+)"
    r"(?:[^\\/:*?\"<>|\r\n]+[\\/])*[^\\/:*?\"<>|\r\n]+(?:\:\d+(?::\d+)?)?)"
)
_UNIX_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_>])(?P<path>/(?:Users|home|tmp|var|private|opt|mnt|srv|workspace|workspaces|repo|app|usr|etc|Volumes)"
    r"[^\s\r\n'\"<>()\],;]*(?:\:\d+(?::\d+)?)?)"
)
_LINE_SUFFIX_PATTERN = re.compile(r"(?P<suffix>:\d+(?::\d+)?)$")
_PLACEHOLDER_REPO_PATTERN = re.compile(
    r"<(?:path|home|temp)>/(?:[^\\/\s\"'<>]+/)*(?:workspace|workspaces)/[^\\/\s\"'<>]+/repo/(?P<relative>[^\s\"'<>:]+(?:/[^\s\"'<>:]+)*)"
)


def sanitize_machine_text(text: str, *, repo_root: Path | None = None) -> str:
    """Normalize absolute machine paths in free text."""
    if not text:
        return text

    cleaned = _WINDOWS_PATH_PATTERN.sub(
        lambda match: _replace_path_token(match.group("path"), repo_root=repo_root),
        text,
    )
    cleaned = _UNIX_PATH_PATTERN.sub(
        lambda match: _replace_path_token(match.group("path"), repo_root=repo_root),
        cleaned,
    )
    return _PLACEHOLDER_REPO_PATTERN.sub(
        lambda match: _join_placeholder("<repo>", match.group("relative")),
        cleaned,
    )


def sanitize_ingest_payload(
    payload: TrajectoryIngestRequest,
    *,
    repo_root: Path | None = None,
) -> TrajectoryIngestRequest:
    """Return a sanitized copy of an ingest payload."""
    return payload.model_copy(
        update={
            "original_goal": _sanitize_optional(payload.original_goal, repo_root=repo_root),
            "original_prompt": sanitize_machine_text(payload.original_prompt, repo_root=repo_root),
            "broken_code": _sanitize_optional(payload.broken_code, repo_root=repo_root),
            "error_log": sanitize_machine_text(payload.error_log, repo_root=repo_root),
            "ai_reflection": _sanitize_optional(payload.ai_reflection, repo_root=repo_root),
            "final_fix": sanitize_machine_text(payload.final_fix, repo_root=repo_root),
            "history": [
                TrajectoryTurn(
                    prompt=sanitize_machine_text(turn.prompt, repo_root=repo_root),
                    response=sanitize_machine_text(turn.response, repo_root=repo_root),
                    model=_sanitize_optional(turn.model, repo_root=repo_root),
                    thought=_sanitize_optional(turn.thought, repo_root=repo_root),
                    tried=_sanitize_optional(turn.tried, repo_root=repo_root),
                    outcome=_sanitize_optional(turn.outcome, repo_root=repo_root),
                    commands=[
                        sanitize_machine_text(command, repo_root=repo_root)
                        for command in turn.commands
                    ],
                    changed_files=[
                        sanitize_machine_text(path, repo_root=repo_root)
                        for path in turn.changed_files
                    ],
                    token_count=turn.token_count,
                    duration_ms=turn.duration_ms,
                    timestamp=turn.timestamp,
                )
                for turn in payload.history
            ],
            "metadata": _sanitize_json_like(payload.metadata, repo_root=repo_root),
        }
    )


def sanitize_stored_trajectory(
    trajectory: StoredTrajectory,
    *,
    repo_root: Path | None = None,
) -> StoredTrajectory:
    """Return a sanitized copy of a stored trajectory row."""
    return trajectory.model_copy(
        update={
            "original_goal": _sanitize_optional(trajectory.original_goal, repo_root=repo_root),
            "original_prompt": sanitize_machine_text(trajectory.original_prompt, repo_root=repo_root),
            "broken_code": _sanitize_optional(trajectory.broken_code, repo_root=repo_root),
            "error_log": sanitize_machine_text(trajectory.error_log, repo_root=repo_root),
            "ai_reflection": _sanitize_optional(trajectory.ai_reflection, repo_root=repo_root),
            "final_fix": sanitize_machine_text(trajectory.final_fix, repo_root=repo_root),
            "history": [
                TrajectoryTurn(
                    prompt=sanitize_machine_text(turn.prompt, repo_root=repo_root),
                    response=sanitize_machine_text(turn.response, repo_root=repo_root),
                    model=_sanitize_optional(turn.model, repo_root=repo_root),
                    thought=_sanitize_optional(turn.thought, repo_root=repo_root),
                    tried=_sanitize_optional(turn.tried, repo_root=repo_root),
                    outcome=_sanitize_optional(turn.outcome, repo_root=repo_root),
                    commands=[
                        sanitize_machine_text(command, repo_root=repo_root)
                        for command in turn.commands
                    ],
                    changed_files=[
                        sanitize_machine_text(path, repo_root=repo_root)
                        for path in turn.changed_files
                    ],
                    token_count=turn.token_count,
                    duration_ms=turn.duration_ms,
                    timestamp=turn.timestamp,
                )
                for turn in trajectory.history
            ],
            "metadata": _sanitize_json_like(trajectory.metadata, repo_root=repo_root),
        }
    )


def _sanitize_optional(text: str | None, *, repo_root: Path | None) -> str | None:
    if text is None:
        return None
    return sanitize_machine_text(text, repo_root=repo_root)


def _sanitize_json_like(value: Any, *, repo_root: Path | None) -> Any:
    if isinstance(value, str):
        return sanitize_machine_text(value, repo_root=repo_root)
    if isinstance(value, list):
        return [_sanitize_json_like(item, repo_root=repo_root) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json_like(item, repo_root=repo_root)
            for key, item in value.items()
        }
    return value


def _replace_path_token(raw_path: str, *, repo_root: Path | None) -> str:
    path_text = raw_path
    suffix_match = _LINE_SUFFIX_PATTERN.search(path_text)
    suffix = ""
    if suffix_match is not None:
        suffix = suffix_match.group("suffix")
        path_text = path_text[: -len(suffix)]

    normalized = _normalize_path(path_text)
    if not normalized:
        return raw_path

    repo_root_normalized = None if repo_root is None else _normalize_path(str(repo_root.resolve()))
    repo_relative = _relative_if_within(normalized, repo_root_normalized)
    if repo_relative is not None:
        return _with_suffix(_join_placeholder("<repo>", repo_relative), suffix)

    inferred_repo_relative = _infer_repo_relative(normalized)
    if inferred_repo_relative is not None:
        return _with_suffix(_join_placeholder("<repo>", inferred_repo_relative), suffix)

    home_relative = _relative_if_within(normalized, _normalize_path(str(Path.home())))
    if home_relative is not None:
        return _with_suffix(_join_placeholder("<home>", _tail_path(home_relative)), suffix)

    temp_relative = _relative_if_within(normalized, _normalize_path(tempfile.gettempdir()))
    if temp_relative is not None:
        return _with_suffix(_join_placeholder("<temp>", _tail_path(temp_relative)), suffix)

    return _with_suffix(_join_placeholder("<path>", _tail_path(normalized)), suffix)


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def _relative_if_within(path: str, root: str | None) -> str | None:
    if not root:
        return None
    path_folded = path.casefold()
    root_folded = root.casefold()
    if path_folded == root_folded:
        return ""
    if path_folded.startswith(f"{root_folded}/"):
        return path[len(root) + 1 :]
    return None


def _tail_path(path: str, *, parts: int = 4) -> str:
    segments = [segment for segment in path.split("/") if segment and not segment.endswith(":")]
    if not segments:
        return ""
    return "/".join(segments[-parts:])


def _infer_repo_relative(path: str) -> str | None:
    segments = [segment for segment in path.split("/") if segment and not segment.endswith(":")]
    lowered = [segment.casefold() for segment in segments]
    if "repo" not in lowered:
        return None
    repo_index = len(lowered) - 1 - lowered[::-1].index("repo")
    if repo_index >= len(segments) - 1:
        return ""
    prefix = lowered[:repo_index]
    if "workspaces" in prefix or ".swarm" in prefix or any(
        marker in prefix for marker in ("tmp", "temp")
    ) or any("tmp" in segment or "temp" in segment for segment in prefix):
        return "/".join(segments[repo_index + 1 :])
    return None


def _join_placeholder(prefix: str, remainder: str) -> str:
    if not remainder:
        return prefix
    return f"{prefix}/{remainder}"


def _with_suffix(path: str, suffix: str) -> str:
    return f"{path}{suffix}" if suffix else path
