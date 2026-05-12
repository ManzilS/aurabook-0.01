"""Mutable pipeline state passed through the AuraBook orchestrator."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse


class PipelineState(BaseModel):
    """State object that flows through middleware and adapters."""

    request: ChatCompletionRequest
    response: ChatCompletionResponse | None = None

    loop_count: int = 0
    max_loops: int = 1
    should_loop: bool = False
    early_exit: bool = False

    pending_tool_results: list[dict[str, Any]] = Field(default_factory=list)
    fan_out_targets: list[str] = Field(default_factory=list)
    fan_out_responses: list[ChatCompletionResponse] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)

    @property
    def can_loop(self) -> bool:
        """Return whether the orchestrator may perform another loop."""
        return self.should_loop and self.loop_count < self.max_loops

    def increment_loop(self) -> None:
        """Advance the loop counter and clear the loop flag."""
        self.loop_count += 1
        self.should_loop = False
