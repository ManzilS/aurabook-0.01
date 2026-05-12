"""Tests for PII scrubbing middleware."""

from __future__ import annotations

import pytest

from aurabook.core.models import ChatCompletionRequest, ContentPart, Message, Role
from aurabook.core.state import PipelineState
from aurabook.middleware.pii import PIIScrubberMiddleware


@pytest.mark.asyncio
async def test_pii_scrubber_redacts_plain_text() -> None:
    middleware = PIIScrubberMiddleware()
    state = PipelineState(
        request=ChatCompletionRequest(
            messages=[
                Message(
                    role=Role.USER,
                    content="Reach me at user@example.com from 127.0.0.1 using sk-abcdefghijklmnopqrstuvwxyz",
                )
            ]
        )
    )

    result = await middleware.process(state)
    text = result.request.messages[0].text_content()

    assert "[REDACTED_EMAIL]" in text
    assert "[REDACTED_IP]" in text
    assert "[REDACTED_API_KEY]" in text
    assert result.extras["pii_redactions"] == 3


@pytest.mark.asyncio
async def test_pii_scrubber_redacts_structured_content() -> None:
    middleware = PIIScrubberMiddleware()
    state = PipelineState(
        request=ChatCompletionRequest(
            messages=[
                Message(
                    role=Role.USER,
                    content=[ContentPart(type="text", text="Email root@example.com")],
                )
            ]
        )
    )

    result = await middleware.process(state)
    parts = result.request.messages[0].content

    assert isinstance(parts, list)
    assert parts[0].text == "Email [REDACTED_EMAIL]"


def test_pii_scrubber_redacts_env_assignments_and_jwts() -> None:
    middleware = PIIScrubberMiddleware()

    cleaned, redactions = middleware.scrub_text(
        "TOKEN=secret-value\nAuthorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
    )

    assert redactions >= 2
    assert "[REDACTED_ENV_ASSIGNMENT]" in cleaned
    assert "[REDACTED_JWT]" in cleaned
