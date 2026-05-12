"""Tests for the shared pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, ContentPart, Message, Role


def test_message_text_content_flattens_parts() -> None:
    message = Message(
        role=Role.USER,
        content=[
            ContentPart(type="text", text="hello"),
            ContentPart(type="image_url"),
            ContentPart(type="text", text="world"),
        ],
    )

    assert message.text_content() == "hello\nworld"


def test_request_requires_messages() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest(messages=[])


def test_response_text_uses_first_choice() -> None:
    response = ChatCompletionResponse(
        model="gpt-test",
        choices=[
            Choice(message=Message(role=Role.ASSISTANT, content="done")),
        ],
    )

    assert response.text == "done"
