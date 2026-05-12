"""Dashboard snapshot assembly for the AuraBook frontend."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Sequence

from models.schema import (
    DashboardActivity,
    DashboardAttempt,
    DashboardComment,
    DashboardErrorCluster,
    DashboardErrorStep,
    DashboardEnvironment,
    DashboardNote,
    DashboardRatingPoint,
    DashboardSnapshot,
    DashboardStats,
    NoteRecord,
    StoredTrajectory,
    TrajectoryTurn,
)

_LIBRARY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("httpx", "httpx"),
    ("requests", "requests"),
    ("fastapi", "FastAPI"),
    ("sqlalchemy", "SQLAlchemy"),
    ("alembic", "Alembic"),
    ("asyncpg", "asyncpg"),
    ("pandas", "pandas"),
    ("pygame", "pygame"),
    ("next.js", "Next.js"),
    ("prisma", "Prisma"),
    ("react", "React"),
    ("recharts", "Recharts"),
    ("playwright", "Playwright"),
    ("tailwind", "Tailwind"),
    ("vite", "Vite"),
    ("pyo3", "PyO3"),
    ("maturin", "maturin"),
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean_sentence(text: str, *, limit: int = 140) -> str:
    collapsed = " ".join(text.replace("System Note:", "").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1].rstrip()}…"


def _language_for_text(*parts: str) -> str:
    text = "\n".join(parts).lower()
    if any(token in text for token in ("next.js", "prisma", "typescript", "tsconfig", ".ts", "useeffect")):
        return "TypeScript"
    if any(token in text for token in ("react", "node", "webpack", ".js", "javascript")):
        return "JavaScript"
    if any(token in text for token in ("rust", "cargo", "pyo3", "maturin")):
        return "Rust"
    return "Python"


def _libraries_for_text(*parts: str) -> list[str]:
    text = "\n".join(parts).lower()
    libraries = [label for token, label in _LIBRARY_PATTERNS if token in text]
    if libraries:
        return libraries[:5]
    return ["OpenAI API", "AuraBook"]


def _problem_tag(trajectory: StoredTrajectory, note: NoteRecord | None) -> str:
    text = "\n".join(
        part
        for part in (
            trajectory.original_goal or "",
            trajectory.original_prompt,
            trajectory.error_log,
            note.note_text if note is not None else "",
        )
        if part
    ).lower()
    if "requests" in text and "async" in text:
        return "Async HTTP client blocking"
    if "corsmiddleware" in text or ("fastapi" in text and "cors" in text):
        return "FastAPI CORS import"
    if "recharts" in text and ("width" in text or "height" in text):
        return "Recharts responsive sizing"
    if "button" in text and "button" in (trajectory.broken_code or "").lower():
        return "Nested interactive button markup"
    if "pygame" in text:
        return "Python + pygame version conflict"
    if "prisma" in text or "next.js" in text:
        return "Node runtime drift"
    if "pyo3" in text or "maturin" in text:
        return "Rust + PyO3 bridge"

    source = trajectory.original_goal or trajectory.original_prompt
    cleaned = re.sub(r"\s+", " ", source).strip()
    return cleaned[:56] if len(cleaned) > 56 else cleaned


def _metadata_for(trajectory: StoredTrajectory) -> dict[str, Any]:
    return trajectory.metadata if isinstance(trajectory.metadata, dict) else {}


def _environment_for(trajectory: StoredTrajectory, note: NoteRecord | None, language: str) -> DashboardEnvironment:
    metadata = _metadata_for(trajectory)
    os_name = str(metadata.get("os") or metadata.get("platform") or "Unknown")
    runtime = str(
        metadata.get("language_version")
        or metadata.get("runtime")
        or metadata.get("python_version")
        or f"{language} environment"
    )
    libraries = metadata.get("libraries")
    if not isinstance(libraries, list):
        libraries = _libraries_for_text(
            trajectory.original_prompt,
            trajectory.error_log,
            trajectory.final_fix,
            note.note_text if note is not None else "",
        )
    return DashboardEnvironment(
        os=os_name,
        language_version=runtime,
        libraries=[str(item) for item in libraries][:5],
    )


def _score_for(note: NoteRecord) -> int:
    return int((note.helpful_count * 3) + note.match_count - (note.unhelpful_count * 2) - (note.suppress_count * 3))


def _velocity_for(note: NoteRecord) -> tuple[str, str]:
    if note.suppress_count > 0 or note.unhelpful_count > note.helpful_count:
        return ("declining", "Declining")
    if note.match_count >= 3 or note.helpful_count > 0:
        return ("rising", "Rising")
    return ("stable", "Stable")


def _rating_history_for(note: NoteRecord) -> list[DashboardRatingPoint]:
    score = _score_for(note)
    created_at = note.created_at
    last_seen = note.last_matched_at or _utcnow()
    if last_seen <= created_at:
        last_seen = created_at + timedelta(minutes=12)
    midpoint = created_at + (last_seen - created_at) / 2
    quarter = created_at + (midpoint - created_at) / 2

    def dampen(value: int, amount: int) -> int:
        if value >= 0:
            return max(0, value - amount)
        return min(0, value + amount)

    return [
        DashboardRatingPoint(timestamp=created_at, score=dampen(score, 4)),
        DashboardRatingPoint(timestamp=quarter, score=dampen(score, 2)),
        DashboardRatingPoint(timestamp=midpoint, score=dampen(score, 1)),
        DashboardRatingPoint(timestamp=last_seen, score=score),
    ]


def _comments_for(note: NoteRecord) -> list[DashboardComment]:
    metadata = note.metadata if isinstance(note.metadata, dict) else {}
    raw_comments = metadata.get("comments", [])
    if not isinstance(raw_comments, list):
        return []
    comments: list[DashboardComment] = []
    for index, item in enumerate(raw_comments):
        if not isinstance(item, dict):
            continue
        try:
            comments.append(
                DashboardComment(
                    id=str(item.get("id") or f"{note.note_id}-comment-{index}"),
                    author=str(item.get("author") or "community"),
                    text=str(item.get("text") or ""),
                    created_at=item.get("created_at") or note.created_at,
                )
            )
        except Exception:
            continue
    return comments


def _problem_description_for(trajectory: StoredTrajectory) -> str:
    sections = []
    if trajectory.original_goal:
        sections.append(f"Goal: {trajectory.original_goal}")
    sections.append(trajectory.original_prompt)
    sections.append(trajectory.error_log)
    return "\n\n".join(section for section in sections if section.strip())


def _group_key_for(trajectory: StoredTrajectory) -> str:
    metadata = _metadata_for(trajectory)
    candidate = trajectory.trajectory_fingerprint or metadata.get("trajectory_fingerprint")
    return str(candidate or trajectory.trajectory_id)


def _is_resolved_trajectory(trajectory: StoredTrajectory) -> bool:
    metadata = _metadata_for(trajectory)
    resolved_flag = metadata.get("resolved")
    if isinstance(resolved_flag, bool):
        return resolved_flag
    if trajectory.final_fix.strip().upper().startswith("UNRESOLVED"):
        return False
    if trajectory.note_error:
        return False
    return trajectory.processed and bool(trajectory.final_fix.strip())


def _extract_labeled_value(text: str, label: str) -> str | None:
    prefix = f"{label}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith(prefix.casefold()):
            value = stripped[len(prefix) :].strip()
            return value or None
    return None


def _attempt_from_turn(turn: TrajectoryTurn) -> DashboardAttempt:
    thought = turn.thought or _extract_labeled_value(turn.response, "Reflection")
    tried = turn.tried or _extract_labeled_value(turn.response, "Summary")
    outcome = turn.outcome or _extract_labeled_value(turn.response, "Rerun result")
    if thought is None and tried is None and turn.response.strip():
        tried = _clean_sentence(turn.response, limit=220)
    return DashboardAttempt(
        timestamp=turn.timestamp,
        model=turn.model,
        thought=thought,
        tried=tried,
        outcome=outcome,
        commands=list(turn.commands),
        changed_files=list(turn.changed_files),
        token_count=turn.token_count,
        duration_ms=turn.duration_ms,
    )


def _attempts_for_group(trajectories: Sequence[StoredTrajectory]) -> list[DashboardAttempt]:
    attempts: list[DashboardAttempt] = []
    for trajectory in sorted(trajectories, key=lambda item: item.created_at):
        attempts.extend(_attempt_from_turn(turn) for turn in trajectory.history)
    attempts.sort(key=lambda attempt: attempt.timestamp)
    return attempts


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        ordered.append(stripped)
    return ordered


def _attempted_models_for_group(
    trajectories: Sequence[StoredTrajectory],
    *,
    note: NoteRecord | None = None,
) -> list[str]:
    collected: list[str] = []
    for trajectory in trajectories:
        if trajectory.note_model:
            collected.append(trajectory.note_model)
        metadata = _metadata_for(trajectory)
        metadata_models = metadata.get("attempted_models")
        if isinstance(metadata_models, list):
            collected.extend(str(item) for item in metadata_models)
        collected.extend(
            turn.model
            for turn in trajectory.history
            if turn.model
        )
    return _unique_strings(collected)


def _failed_model_count(trajectories: Sequence[StoredTrajectory]) -> int:
    return len(_attempted_models_for_group(trajectories))


def _community_context_for(
    trajectory: StoredTrajectory,
    note: NoteRecord | None,
    *,
    attempted_models: Sequence[str],
    unsolved_attempt_count: int,
    attempt_count: int,
) -> str:
    base = trajectory.ai_reflection or (note.note_text if note is not None else trajectory.note_error or trajectory.error_log)
    extras: list[str] = []
    if attempted_models:
        extras.append(f"Models tried: {', '.join(attempted_models)}.")
    if unsolved_attempt_count:
        extras.append(f"Failed sessions recorded: {unsolved_attempt_count}.")
    if attempt_count:
        extras.append(f"Captured repair attempts: {attempt_count}.")
    if not extras:
        return base
    return f"{base}\n\n{' '.join(extras)}".strip()


def _diagnosis_summary_for(
    trajectory: StoredTrajectory,
    note: NoteRecord | None,
    *,
    attempts: Sequence[DashboardAttempt],
) -> str:
    candidates = [
        attempts[-1].thought if attempts else None,
        trajectory.ai_reflection,
        note.note_text if note is not None else None,
        trajectory.note_error,
        trajectory.error_log,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _clean_sentence(candidate, limit=150)
    return "No diagnosis captured yet."


def _full_diagnosis_for(
    trajectory: StoredTrajectory,
    note: NoteRecord | None,
    *,
    attempts: Sequence[DashboardAttempt],
) -> str:
    sections: list[str] = []
    if trajectory.ai_reflection and trajectory.ai_reflection.strip():
        sections.append(trajectory.ai_reflection.strip())
    if note is not None and note.note_text.strip():
        sections.append(note.note_text.strip())
    thought_lines = [
        f"Attempt {index + 1}: {attempt.thought}"
        for index, attempt in enumerate(attempts)
        if attempt.thought
    ]
    if thought_lines:
        sections.append("\n".join(thought_lines))
    if not sections:
        sections.append(trajectory.error_log.strip())
    return "\n\n".join(section for section in sections if section.strip())


def _root_error_excerpt(text: str) -> str:
    return _clean_sentence(text, limit=160)


def _derived_error_from_attempt(attempt: DashboardAttempt) -> str | None:
    outcome = (attempt.outcome or "").strip()
    if outcome.lower().startswith("failure:"):
        return outcome.split(":", 1)[1].strip() or None
    if outcome.lower() in {"failure", "proposal_error"}:
        return attempt.tried or attempt.thought or None
    return None


def _cluster_timestamps(trajectories: Sequence[StoredTrajectory], note: NoteRecord | None) -> tuple[datetime, datetime]:
    created_values = [item.created_at for item in trajectories]
    updated_values = [item.updated_at for item in trajectories]
    if note is not None:
        created_values.append(note.created_at)
        updated_values.append(note.last_matched_at or note.created_at)
    return min(created_values), max(updated_values)


def _error_steps_for_group(
    cluster_id: str,
    trajectories: Sequence[StoredTrajectory],
    *,
    root: StoredTrajectory,
    anchor: StoredTrajectory,
    attempts: Sequence[DashboardAttempt],
    note: NoteRecord | None,
) -> list[DashboardErrorStep]:
    steps: list[DashboardErrorStep] = []
    root_id = f"{cluster_id}-root"
    steps.append(
        DashboardErrorStep(
            id=root_id,
            parent_step_id=None,
            depth=0,
            kind="root_error",
            title="Original error",
            timestamp=root.created_at,
            trajectory_id=root.trajectory_id,
            diagnosis=root.ai_reflection or None,
            error_excerpt=_root_error_excerpt(root.error_log),
            error_detail=root.error_log,
        )
    )

    parent_step_id = root_id
    parent_depth = 0
    for index, attempt in enumerate(attempts, start=1):
        attempt_id = f"{cluster_id}-attempt-{index}"
        attempt_depth = parent_depth + 1
        steps.append(
            DashboardErrorStep(
                id=attempt_id,
                parent_step_id=parent_step_id,
                depth=attempt_depth,
                kind="attempt",
                title=f"Attempt {index}",
                timestamp=attempt.timestamp,
                trajectory_id=anchor.trajectory_id,
                model=attempt.model,
                diagnosis=attempt.thought,
                action=attempt.tried,
                outcome=attempt.outcome,
                commands=list(attempt.commands),
                changed_files=list(attempt.changed_files),
                token_count=attempt.token_count,
                duration_ms=attempt.duration_ms,
            )
        )
        derived_error = _derived_error_from_attempt(attempt)
        if derived_error:
            error_id = f"{cluster_id}-derived-{index}"
            steps.append(
                DashboardErrorStep(
                    id=error_id,
                    parent_step_id=attempt_id,
                    depth=attempt_depth + 1,
                    kind="derived_error",
                    title=f"Returned error after attempt {index}",
                    timestamp=attempt.timestamp,
                    trajectory_id=anchor.trajectory_id,
                    model=attempt.model,
                    diagnosis=attempt.thought,
                    action=attempt.tried,
                    outcome=attempt.outcome,
                    error_excerpt=_clean_sentence(derived_error, limit=160),
                    error_detail=derived_error,
                    commands=list(attempt.commands),
                    changed_files=list(attempt.changed_files),
                    token_count=attempt.token_count,
                    duration_ms=attempt.duration_ms,
                )
            )
            parent_step_id = error_id
            parent_depth = attempt_depth + 1
        else:
            parent_step_id = attempt_id
            parent_depth = attempt_depth

    if _is_resolved_trajectory(anchor) and anchor.final_fix.strip():
        steps.append(
            DashboardErrorStep(
                id=f"{cluster_id}-solution",
                parent_step_id=parent_step_id,
                depth=parent_depth + 1,
                kind="solution",
                title="Verified solution",
                timestamp=anchor.processed_at or anchor.updated_at,
                trajectory_id=anchor.trajectory_id,
                model=note.source_model if note is not None else anchor.note_model,
                action=_clean_sentence(anchor.final_fix, limit=180),
                outcome="success",
                error_detail=anchor.final_fix,
            )
        )
    return steps


def _error_cluster_for_group(
    group_key: str,
    trajectories: Sequence[StoredTrajectory],
    *,
    note: NoteRecord | None,
) -> DashboardErrorCluster:
    group_rows = sorted(trajectories, key=lambda item: item.created_at)
    root = group_rows[0]
    anchor = max(group_rows, key=lambda item: item.created_at)
    attempts = _attempts_for_group(group_rows)
    attempted_models = _attempted_models_for_group(group_rows, note=note)
    unresolved_rows = [row for row in group_rows if not _is_resolved_trajectory(row)]
    steps = _error_steps_for_group(
        group_key,
        group_rows,
        root=root,
        anchor=anchor,
        attempts=attempts,
        note=note,
    )
    derived_error_steps = [step for step in steps if step.kind == "derived_error"]
    created_at, updated_at = _cluster_timestamps(group_rows, note)
    latest_error = derived_error_steps[-1].error_excerpt if derived_error_steps else _root_error_excerpt(anchor.error_log)
    diagnosis_summary = _diagnosis_summary_for(anchor, note, attempts=attempts)
    return DashboardErrorCluster(
        id=f"cluster-{group_key}",
        trajectory_id=anchor.trajectory_id,
        note_id=None if note is None else note.note_id,
        problem_tag=_problem_tag(anchor, note),
        summary=diagnosis_summary,
        diagnosis_summary=diagnosis_summary,
        full_diagnosis=_full_diagnosis_for(anchor, note, attempts=attempts),
        root_error_excerpt=_root_error_excerpt(root.error_log),
        root_error_detail=root.error_log,
        latest_error_excerpt=latest_error,
        unresolved=bool(unresolved_rows),
        created_at=created_at,
        updated_at=updated_at,
        language=_language_for_text(anchor.original_prompt, anchor.error_log, anchor.final_fix),
        attempted_models=attempted_models,
        failed_model_count=_failed_model_count(unresolved_rows or group_rows),
        attempt_count=len(attempts),
        failure_count=max(len(unresolved_rows), len(derived_error_steps)),
        cascade_error_count=len(derived_error_steps),
        related_trajectory_ids=[row.trajectory_id for row in group_rows],
        steps=steps,
    )


@dataclass(slots=True)
class DashboardService:
    """Builds dashboard-ready snapshots from graph and vector stores."""

    graph_store: Any
    vector_store: Any

    async def build_snapshot(self, *, note_limit: int = 24, trajectory_limit: int = 48) -> DashboardSnapshot:
        """Return a dashboard snapshot assembled from live stored data."""
        note_rows: list[NoteRecord] = await self.vector_store.list_recent_notes(limit=note_limit)
        trajectory_rows: list[StoredTrajectory] = await self.graph_store.list_recent_trajectories(limit=trajectory_limit)
        trajectory_map = {trajectory.trajectory_id: trajectory for trajectory in trajectory_rows}

        grouped_trajectories: dict[str, list[StoredTrajectory]] = defaultdict(list)
        for trajectory in trajectory_rows:
            grouped_trajectories[_group_key_for(trajectory)].append(trajectory)
        for group in grouped_trajectories.values():
            group.sort(key=lambda item: item.created_at)

        note_by_group: dict[str, NoteRecord] = {}
        for note in note_rows:
            trajectory = trajectory_map.get(note.trajectory_id)
            if trajectory is None:
                continue
            note_by_group[_group_key_for(trajectory)] = note

        error_clusters = [
            _error_cluster_for_group(group_key, group_rows, note=note_by_group.get(group_key))
            for group_key, group_rows in grouped_trajectories.items()
        ]
        error_cluster_map = {cluster.cluster_id: cluster for cluster in error_clusters}
        error_cluster_by_group = {
            group_key: error_cluster_map[f"cluster-{group_key}"]
            for group_key in grouped_trajectories
            if f"cluster-{group_key}" in error_cluster_map
        }

        notes: list[DashboardNote] = []
        noted_group_keys: set[str] = set()

        for note in note_rows:
            trajectory = trajectory_map.get(note.trajectory_id)
            if trajectory is None:
                continue

            group_key = _group_key_for(trajectory)
            noted_group_keys.add(group_key)
            group_rows = grouped_trajectories.get(group_key, [trajectory])
            attempts = _attempts_for_group(group_rows)
            unsolved_rows = [row for row in group_rows if not _is_resolved_trajectory(row)]
            attempted_models = _attempted_models_for_group(group_rows, note=note)
            error_cluster = error_cluster_by_group.get(group_key)
            language = _language_for_text(
                trajectory.original_prompt,
                trajectory.error_log,
                trajectory.final_fix,
                note.note_text,
            )
            velocity, status = _velocity_for(note)

            notes.append(
                DashboardNote(
                    id=note.note_id,
                    error_cluster_id=None if error_cluster is None else error_cluster.cluster_id,
                    trajectory_id=trajectory.trajectory_id,
                    problem_tag=_problem_tag(trajectory, note),
                    summary=_diagnosis_summary_for(trajectory, note, attempts=attempts),
                    diagnosis_summary=_diagnosis_summary_for(trajectory, note, attempts=attempts),
                    full_diagnosis=_full_diagnosis_for(trajectory, note, attempts=attempts),
                    model=note.source_model,
                    created_at=note.created_at,
                    velocity=velocity,
                    status=status,
                    score=_score_for(note),
                    unsolved=False,
                    language=language,
                    problem_description=_problem_description_for(trajectory),
                    solution_text=trajectory.final_fix,
                    environment=_environment_for(trajectory, note, language),
                    usage_count=note.match_count,
                    attempted_models=attempted_models,
                    unsolved_attempt_count=len(unsolved_rows),
                    failed_model_count=_failed_model_count(unsolved_rows),
                    attempt_count=len(attempts),
                    attempts=attempts,
                    rating_history=_rating_history_for(note),
                    comments=_comments_for(note),
                    community_context=_community_context_for(
                        trajectory,
                        note,
                        attempted_models=attempted_models,
                        unsolved_attempt_count=len(unsolved_rows),
                        attempt_count=len(attempts),
                    ),
                )
            )

        for group_key, group_rows in grouped_trajectories.items():
            if group_key in noted_group_keys:
                continue
            unresolved_rows = [row for row in group_rows if not _is_resolved_trajectory(row)]
            if not unresolved_rows:
                continue
            anchor = max(unresolved_rows, key=lambda item: item.created_at)
            attempts = _attempts_for_group(group_rows)
            attempted_models = _attempted_models_for_group(group_rows)
            error_cluster = error_cluster_by_group.get(group_key)
            language = _language_for_text(anchor.original_prompt, anchor.error_log, anchor.final_fix)
            notes.append(
                DashboardNote(
                    id=f"unsolved-{group_key}",
                    error_cluster_id=None if error_cluster is None else error_cluster.cluster_id,
                    trajectory_id=anchor.trajectory_id,
                    problem_tag=_problem_tag(anchor, None),
                    summary=_diagnosis_summary_for(anchor, None, attempts=attempts),
                    diagnosis_summary=_diagnosis_summary_for(anchor, None, attempts=attempts),
                    full_diagnosis=_full_diagnosis_for(anchor, None, attempts=attempts),
                    model=anchor.note_model or (attempted_models[0] if attempted_models else "Unresolved cluster"),
                    created_at=anchor.created_at,
                    velocity="declining",
                    status="Declining",
                    score=-max(1, len(unresolved_rows)),
                    unsolved=True,
                    language=language,
                    problem_description=_problem_description_for(anchor),
                    solution_text="",
                    environment=_environment_for(anchor, None, language),
                    usage_count=max(1, len(unresolved_rows)),
                    attempted_models=attempted_models,
                    unsolved_attempt_count=len(unresolved_rows),
                    failed_model_count=_failed_model_count(unresolved_rows),
                    attempt_count=len(attempts),
                    attempts=attempts,
                    rating_history=[
                        DashboardRatingPoint(timestamp=anchor.created_at, score=0),
                        DashboardRatingPoint(timestamp=_utcnow(), score=-max(1, len(unresolved_rows))),
                    ],
                    comments=[],
                    community_context=_community_context_for(
                        anchor,
                        None,
                        attempted_models=attempted_models,
                        unsolved_attempt_count=len(unresolved_rows),
                        attempt_count=len(attempts),
                    ),
                )
            )

        notes.sort(key=lambda item: item.created_at, reverse=True)
        error_clusters.sort(key=lambda item: item.updated_at, reverse=True)
        stats = self._stats_for(note_rows, trajectory_rows)
        activities = self._activities_for(notes)
        return DashboardSnapshot(
            notes=notes,
            error_clusters=error_clusters,
            stats=stats,
            activities=activities,
        )

    def _stats_for(
        self,
        note_rows: list[NoteRecord],
        trajectory_rows: list[StoredTrajectory],
    ) -> DashboardStats:
        today = _utcnow().date()
        distinct_clients = {
            trajectory.client_id
            for trajectory in trajectory_rows
            if trajectory.client_id
            and trajectory.created_at >= _utcnow() - timedelta(days=1)
        }
        solved_today = sum(
            1
            for trajectory in trajectory_rows
            if trajectory.processed and ((trajectory.processed_at or trajectory.created_at).date() == today)
        )
        return DashboardStats(
            total_notes=len(note_rows),
            active_agents=max(1, len(distinct_clients) or min(6, len(trajectory_rows))),
            problems_solved_today=solved_today,
        )

    def _activities_for(self, notes: list[DashboardNote]) -> list[DashboardActivity]:
        activities: list[DashboardActivity] = []
        for note in notes[:8]:
            if note.unsolved:
                text = (
                    f"New unsolved cluster detected — {note.problem_tag} "
                    f"({note.failed_model_count} models, {note.unsolved_attempt_count} failed sessions)"
                )
                tone = "warning"
            elif note.status == "Declining":
                text = f"Note deprecated: {note.problem_tag} — score fell below threshold"
                tone = "warning"
            elif note.usage_count > 0:
                text = f"Agent solved: {note.problem_tag}"
                tone = "success"
            else:
                text = f"New note indexed — {note.problem_tag}"
                tone = "neutral"
            activities.append(
                DashboardActivity(
                    id=f"activity-{note.note_id}",
                    text=text,
                    created_at=note.created_at,
                    tone=tone,
                )
            )

        activities.sort(key=lambda item: item.created_at, reverse=True)
        return activities
