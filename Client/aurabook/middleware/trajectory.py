"""Trajectory capture middleware for successful coding sessions."""

from __future__ import annotations

import logging
import re
from collections import deque
from threading import Lock
from typing import Callable

from aurabook.core.models import Message, Role, TrajectoryTurn, TrajectoryUploadPayload
from aurabook.core.state import PipelineState
from aurabook.middleware.base import PostMiddleware
from aurabook.services.cloud_client import CloudBrainClient
from aurabook.services.community import CommunityContextService
from aurabook.services.ingest_queue import FailedTrajectoryQueue
from aurabook.services.status import RuntimeStatusStore

Emitter = Callable[[str], None]
logger = logging.getLogger(__name__)

_SUCCESS_MARKERS = ("that fixed it", "thanks", "works now")
_TRACEBACK_PATTERN = re.compile(r"traceback[\s\S]*", re.IGNORECASE)
_ERROR_BLOCK_PATTERN = re.compile(
    r"((?:traceback[\s\S]*?)(?:(?:\n\n)|$)|(?:.*\b(?:error|exception|warning|failed)\b.*(?:\n.*)*))",
    re.IGNORECASE,
)
_ERROR_LINE_PATTERN = re.compile(r".*\b(?:error|exception|warning|failed)\b.*", re.IGNORECASE)
_CODE_BLOCK_PATTERN = re.compile(r"```(?:[\w.+-]+)?\n(?P<code>[\s\S]*?)```")
_REFLECTION_PATTERN = re.compile(r"\b(?:because|caused by|due to|failed|use instead|avoid)\b", re.IGNORECASE)


class TrajectoryCaptureMiddleware(PostMiddleware):
    """Captures a short rolling interaction window for successful fixes."""

    name = "trajectory_capture"

    def __init__(
        self,
        *,
        emitter: Emitter = print,
        history_size: int = 3,
        cloud_client: CloudBrainClient | None = None,
        community_service: CommunityContextService | None = None,
        client_id: str = "",
        failed_upload_queue: FailedTrajectoryQueue | None = None,
        status_store: RuntimeStatusStore | None = None,
    ) -> None:
        self._emitter = emitter
        self._history: deque[TrajectoryTurn] = deque(maxlen=history_size)
        self._lock = Lock()
        self._cloud_client = cloud_client
        self._community_service = community_service
        self._client_id = client_id
        self._failed_upload_queue = failed_upload_queue
        self._status_store = status_store

    async def process(self, state: PipelineState) -> PipelineState:
        """Store the prompt/response pair and emit a success payload when needed."""
        if state.response is None:
            return state

        prompt = self._last_user_prompt(state.request.messages)
        response_text = state.response.text
        payload: TrajectoryUploadPayload | None = None

        with self._lock:
            previous_history = list(self._history)
            if self._is_success_prompt(prompt):
                payload = self._build_payload(
                    original_prompt=prompt,
                    history=previous_history,
                    fallback_response=response_text,
                    state=state,
                )

            self._history.append(
                TrajectoryTurn(
                    prompt=prompt,
                    response=response_text,
                    model=state.request.model,
                )
            )

        if payload is not None:
            try:
                await self._upload_payload(payload)
            except Exception as exc:
                logger.exception("Trajectory upload failed: %s", exc)

        return state

    def history_snapshot(self) -> list[TrajectoryTurn]:
        """Return a copy of the current rolling history."""
        with self._lock:
            return list(self._history)

    def _is_success_prompt(self, prompt: str) -> bool:
        lowered_prompt = prompt.lower()
        return any(marker in lowered_prompt for marker in _SUCCESS_MARKERS)

    def _build_payload(
        self,
        *,
        original_prompt: str,
        history: list[TrajectoryTurn],
        fallback_response: str,
        state: PipelineState,
    ) -> TrajectoryUploadPayload | None:
        if not history and not fallback_response:
            return None

        previous_error_log = self._find_error_log(history)
        final_code = self._find_final_code(history) or fallback_response
        reflection = self._find_ai_reflection(history)

        return TrajectoryUploadPayload(
            client_id=self._client_id,
            cdc_hash=None if self._community_service is None else self._community_service.latest_cdc_hash(),
            original_goal=self._find_original_goal(history),
            original_prompt=self._find_original_prompt(history, fallback=original_prompt),
            broken_code=self._find_broken_code(history),
            error_log=previous_error_log,
            ai_reflection=reflection,
            final_fix=final_code,
            history=history,
            metadata={
                "community_gradient": state.extras.get("community_gradient", "pass_through"),
                "request_model": state.request.model,
            },
        )

    async def _upload_payload(self, payload: TrajectoryUploadPayload) -> None:
        snippet = payload.model_dump_json()[:400]
        if self._cloud_client is None:
            self._emitter(f"[Trajectory Capture] Success identified. Uploading: {snippet}")
            if self._status_store is not None:
                self._status_store.record_upload(
                    success=True,
                    queued=False,
                    trajectory_id=None,
                )
            return

        try:
            response = await self._cloud_client.ingest(payload)
        except Exception as exc:
            if self._failed_upload_queue is not None:
                self._failed_upload_queue.enqueue(payload, error_message=str(exc))
            if self._status_store is not None:
                self._status_store.record_upload(
                    success=False,
                    queued=self._failed_upload_queue is not None,
                    trajectory_id=None,
                    error_message=str(exc),
                )
            raise

        if self._status_store is not None:
            trajectory_id = response.get("trajectory_id")
            self._status_store.record_upload(
                success=True,
                queued=False,
                trajectory_id=None if trajectory_id is None else str(trajectory_id),
            )
        self._emitter(f"[Trajectory Capture] Success identified. Uploading: {snippet}")

    def _find_error_log(self, history: list[TrajectoryTurn]) -> str:
        for turn in reversed(history):
            for source in (turn.prompt, turn.response):
                traceback_match = _TRACEBACK_PATTERN.search(source)
                if traceback_match:
                    return traceback_match.group(0).strip()
                error_block = _ERROR_BLOCK_PATTERN.search(source)
                if error_block:
                    return error_block.group(0).strip()
                error_match = _ERROR_LINE_PATTERN.search(source)
                if error_match:
                    return error_match.group(0).strip()
        return ""

    def _find_final_code(self, history: list[TrajectoryTurn]) -> str:
        for turn in reversed(history):
            code_block = self._extract_code_block(turn.response)
            if code_block:
                return code_block
        for turn in reversed(history):
            if turn.response.strip():
                return turn.response
        return ""

    def _extract_code_block(self, text: str) -> str:
        match = _CODE_BLOCK_PATTERN.search(text)
        if not match:
            return ""
        return match.group("code").strip()

    def _find_ai_reflection(self, history: list[TrajectoryTurn]) -> str:
        for turn in reversed(history):
            response = turn.response.strip()
            if not response:
                continue
            if self._extract_code_block(response):
                continue
            if _REFLECTION_PATTERN.search(response):
                return response
        for turn in reversed(history):
            if turn.response.strip():
                return turn.response.strip()
        return ""

    def _find_broken_code(self, history: list[TrajectoryTurn]) -> str:
        for turn in reversed(history):
            code_block = self._extract_code_block(turn.prompt)
            if code_block:
                return code_block
        for turn in reversed(history):
            if turn.prompt.strip().startswith(("from ", "import ", "def ", "class ", "useEffect(")):
                return turn.prompt.strip()
        return ""

    def _find_original_goal(self, history: list[TrajectoryTurn]) -> str | None:
        for turn in history:
            prompt = turn.prompt.strip()
            lowered = prompt.lower()
            if not prompt:
                continue
            if any(marker in lowered for marker in _SUCCESS_MARKERS):
                continue
            if _ERROR_LINE_PATTERN.search(prompt):
                continue
            if self._extract_code_block(prompt):
                continue
            return prompt.splitlines()[0].strip()
        return None

    def _find_original_prompt(self, history: list[TrajectoryTurn], *, fallback: str) -> str:
        for turn in history:
            prompt = turn.prompt.strip()
            lowered = prompt.lower()
            if not prompt:
                continue
            if any(marker in lowered for marker in _SUCCESS_MARKERS):
                continue
            return prompt
        return fallback

    def _last_user_prompt(self, messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == Role.USER:
                return message.text_content()
        return ""
