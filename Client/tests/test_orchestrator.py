"""Tests for the orchestrator."""

from __future__ import annotations

import pytest

from aurabook.core.models import ChatCompletionRequest, Message, Role
from aurabook.core.orchestrator import Orchestrator
from aurabook.core.pipeline import Pipeline
from aurabook.core.state import PipelineState
from aurabook.middleware.base import PostMiddleware
from tests.conftest import FakeAdapter


class LoopOnceMiddleware(PostMiddleware):
    name = "loop_once"

    async def process(self, state: PipelineState) -> PipelineState:
        state.extras["runs"] = state.extras.get("runs", 0) + 1
        if state.loop_count == 0:
            state.should_loop = True
        return state


@pytest.mark.asyncio
async def test_orchestrator_loops_once_when_requested() -> None:
    pipeline = Pipeline(
        post_middleware=[LoopOnceMiddleware()],
        adapters={"openai_proxy": FakeAdapter(reply="ok")},
        default_adapter="openai_proxy",
    )
    orchestrator = Orchestrator(pipeline=pipeline, max_loops=2)
    state = PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")]),
    )

    result = await orchestrator.run(state)

    assert result.loop_count == 1
    assert result.response is not None
    assert result.extras["runs"] == 2
