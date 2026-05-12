"""Tests for pipeline state helpers."""

from aurabook.core.models import ChatCompletionRequest, Message, Role
from aurabook.core.state import PipelineState


def test_state_loop_helpers() -> None:
    state = PipelineState(
        request=ChatCompletionRequest(messages=[Message(role=Role.USER, content="hi")]),
        should_loop=True,
        max_loops=2,
    )

    assert state.can_loop is True

    state.increment_loop()

    assert state.loop_count == 1
    assert state.should_loop is False
