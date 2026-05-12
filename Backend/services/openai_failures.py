"""Shared helpers for fatal LLM quota or token failures."""

from __future__ import annotations

_HALT_TOKEN_PATTERNS = (
    "insufficient_quota",
    "exceeded your current quota",
    "out of tokens",
    "out of quota",
    "billing hard limit",
    "context length",
    "maximum context length",
    "context_length_exceeded",
    "max_output_tokens",
    "prompt is too long",
    "input is too long",
)


class OpenAIHaltError(RuntimeError):
    """Fatal provider error that should stop the current batch immediately."""


def is_openai_halt_error(exc: Exception) -> bool:
    """Return True when a provider error means the batch should stop immediately."""
    message = str(exc).lower()
    return any(pattern in message for pattern in _HALT_TOKEN_PATTERNS)
