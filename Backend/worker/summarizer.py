"""Trajectory-to-note summarizer built on the configured generation provider."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from config import Settings
from models.schema import StoredTrajectory
from services.model_providers import ProviderRequestError, build_text_generation_provider
from services.openai_failures import OpenAIHaltError, is_openai_halt_error

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You convert one coding failure trajectory into a concise system note. "
    "Output one to two sentences only. Start with 'System Note:'. "
    "Sentence 1: state the root cause — why this error happens at a mechanical level. "
    "Sentence 2 (optional): state the concrete corrective action, naming the correct API, pattern, or flag. "
    "Prefer the safer library, API, or implementation pattern when relevant. "
    "Use the attempt history to avoid generic advice and focus on the fix that was actually verified. "
    "Do not include markdown, bullets, code fences, or line breaks."
)


class TrajectoryNoteSummarizer:
    """Produces one-sentence system notes from raw trajectories."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: Any | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retryable_exceptions: Sequence[type[Exception]] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or build_text_generation_provider(settings, purpose="summary")
        self._sleep = sleep_func
        self.total_tokens_used = 0
        self._retryable_exceptions = tuple(
            retryable_exceptions or (ProviderRequestError,)
        )

    async def summarize(self, trajectory: StoredTrajectory) -> str:
        """Summarize a stored trajectory into one injected system note."""
        prompt = self._build_prompt(trajectory)

        for attempt in range(1, self._settings.openai_retry_attempts + 1):
            try:
                response = await self._provider.generate_text(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=prompt,
                )
                self.total_tokens_used += int(response.total_tokens or 0)
                note_text = self._normalize_note(response.text)
                logger.info("Generated note for trajectory %s", trajectory.trajectory_id)
                return note_text
            except self._retryable_exceptions as exc:
                if getattr(exc, "halt", False) or is_openai_halt_error(exc):
                    raise OpenAIHaltError(str(exc)) from exc
                if not getattr(exc, "retryable", True):
                    raise
                if attempt >= self._settings.openai_retry_attempts:
                    logger.error(
                        "Summarization failed after %d attempts for trajectory %s",
                        attempt,
                        trajectory.trajectory_id,
                    )
                    raise
                delay = self._settings.openai_retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Summarization attempt %d failed with %s; retrying in %.1fs",
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await self._sleep(delay)

        raise RuntimeError("Summarization retry loop exited unexpectedly")

    async def close(self) -> None:
        """Close the underlying provider when supported."""
        close = getattr(self._provider, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result

    def _build_prompt(self, trajectory: StoredTrajectory) -> str:
        history_lines: list[str] = []
        for index, turn in enumerate(trajectory.history, start=1):
            history_lines.append(
                "\n".join(
                    part
                    for part in (
                        f"Attempt {index}",
                        f"Model: {turn.model}" if turn.model else "",
                        f"Thought: {turn.thought}" if turn.thought else "",
                        f"Tried: {turn.tried}" if turn.tried else "",
                        f"Outcome: {turn.outcome}" if turn.outcome else "",
                        f"Commands: {', '.join(turn.commands)}" if turn.commands else "",
                        f"Changed files: {', '.join(turn.changed_files)}" if turn.changed_files else "",
                    )
                    if part
                )
            )
        metadata = trajectory.metadata if isinstance(trajectory.metadata, dict) else {}
        return (
            f"Original goal:\n{trajectory.original_goal or ''}\n\n"
            f"Original prompt:\n{trajectory.original_prompt}\n\n"
            f"Broken code or attempt:\n{trajectory.broken_code or ''}\n\n"
            f"Error log:\n{trajectory.error_log}\n\n"
            f"Attempt history:\n{'\n\n'.join(history_lines) if history_lines else 'No recorded attempts.'}\n\n"
            f"Attempted models:\n{metadata.get('attempted_models', [])}\n\n"
            f"Final fix:\n{trajectory.final_fix}\n"
        )

    def _normalize_note(self, text: str) -> str:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            raise ValueError("OpenAI returned an empty note")
        if not cleaned.lower().startswith("system note:"):
            cleaned = f"System Note: {cleaned}"

        # Allow up to two sentences; drop any third sentence or beyond.
        sentence_parts = re.split(r"(?<=[.!?])\s+", cleaned)
        note = " ".join(sentence_parts[:2]).strip()
        if not note.endswith((".", "!", "?")):
            note = f"{note.rstrip('.!?')}."
        return note


class HeuristicTrajectoryNoteSummarizer:
    """Deterministic summarizer used when OpenAI is unavailable."""

    def __init__(self) -> None:
        self.total_tokens_used = 0

    async def summarize(self, trajectory: StoredTrajectory) -> str:
        if trajectory.ai_reflection:
            base = trajectory.ai_reflection.strip().rstrip(".")
            return f"System Note: {base}."
        fix = " ".join(trajectory.final_fix.strip().split())
        if fix:
            return f"System Note: Prefer the verified repair pattern: {fix[:180].rstrip('.')}."
        error = " ".join(trajectory.error_log.strip().split())
        return f"System Note: Avoid the previous failure mode: {error[:180].rstrip('.')}."

    async def close(self) -> None:
        return None


def build_summarizer(settings: Settings) -> TrajectoryNoteSummarizer | HeuristicTrajectoryNoteSummarizer:
    """Return the best available summarizer implementation for the current settings."""
    if settings.demo_mode or not settings.generation_provider_ready():
        return HeuristicTrajectoryNoteSummarizer()
    return TrajectoryNoteSummarizer(settings)
