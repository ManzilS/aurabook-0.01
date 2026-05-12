"""Tests for the AuraBook pipeline."""

from __future__ import annotations

import pytest

from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, Message, Role
from aurabook.core.pipeline import Pipeline
from aurabook.core.state import PipelineState
from aurabook.middleware.base import PostMiddleware, PreMiddleware
from aurabook.utils.errors import AdapterNotFoundError
from tests.conftest import FakeAdapter


class AppendPreMiddleware(PreMiddleware):
    name = "append_pre"

    async def process(self, state: PipelineState) -> PipelineState:
        state.request.messages[0].content = f"{state.request.messages[0].text_content()} [pre]"
        return state


class AnnotatePostMiddleware(PostMiddleware):
    name = "annotate_post"

    async def process(self, state: PipelineState) -> PipelineState:
        assert state.response is not None
        state.response.metadata["annotated"] = True
        return state


class EarlyExitMiddleware(PreMiddleware):
    name = "early_exit"

    async def process(self, state: PipelineState) -> PipelineState:
        state.early_exit = True
        state.response = ChatCompletionResponse(
            model="local",
            choices=[Choice(message=Message(role=Role.ASSISTANT, content="stopped"))],
        )
        return state


@pytest.mark.asyncio
async def test_pipeline_runs_pre_and_post_middleware() -> None:
    adapter = FakeAdapter(reply="hello")
    pipeline = Pipeline(
        pre_middleware=[AppendPreMiddleware()],
        post_middleware=[AnnotatePostMiddleware()],
        adapters={"openai_proxy": adapter},
        default_adapter="openai_proxy",
    )
    state = PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")]),
    )

    result = await pipeline.execute(state)

    assert adapter.last_request is not None
    assert adapter.last_request.messages[0].text_content() == "hi [pre]"
    assert result.response is not None
    assert result.response.metadata["annotated"] is True


@pytest.mark.asyncio
async def test_pipeline_early_exit_skips_adapter() -> None:
    adapter = FakeAdapter(reply="should-not-run")
    pipeline = Pipeline(
        pre_middleware=[EarlyExitMiddleware()],
        adapters={"openai_proxy": adapter},
        default_adapter="openai_proxy",
    )
    state = PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")]),
    )

    result = await pipeline.execute(state)

    assert result.early_exit is True
    assert result.response is not None
    assert result.response.text == "stopped"
    assert adapter.last_request is None


@pytest.mark.asyncio
async def test_pipeline_missing_adapter_raises() -> None:
    pipeline = Pipeline(adapters={}, default_adapter="missing")
    state = PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")]),
    )

    with pytest.raises(AdapterNotFoundError):
        await pipeline.execute(state)
