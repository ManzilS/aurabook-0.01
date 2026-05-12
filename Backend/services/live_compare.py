"""Live demo comparison with real provider calls on both sides."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.model_providers import (
    AnthropicTextProvider,
    GeminiTextProvider,
    OpenAICompatibleTextProvider,
    ProviderRequestError,
    TextGenerationProvider,
)

logger = logging.getLogger(__name__)

_SYSTEM_WITH_NOTE = """\
You are a helpful AI coding assistant powered by AuraBook, a shared agent memory network.

The community has surfaced a relevant knowledge note for this request:
---
{note}
---

Use this context to solve the user's request efficiently. Skip the trial-and-error paths the \
community has already documented. Keep your answer concise (3-5 sentences)."""

_SYSTEM_NO_NOTE = """\
You are a helpful AI coding assistant. \
Answer the user's coding request concisely and precisely in 3-5 sentences."""


@dataclass(slots=True)
class LiveDemoEvent:
    at: int
    kind: str
    text: str
    token_count: int
    status_label: str


@dataclass(slots=True)
class LiveCompareResult:
    injected_note: str | None
    left_events: list[LiveDemoEvent]
    right_events: list[LiveDemoEvent]
    baseline_tokens: int
    aurabook_tokens: int
    total_tokens: int
    provider_used: str
    model_used: str
    provider_call_count: int


def _detect_provider(api_key: str) -> tuple[str, str, str]:
    """Return (provider_name, base_url, model_name) inferred from the API key prefix."""
    key = api_key.strip()
    if key.startswith("sk-ant-"):
        return "claude", "https://api.anthropic.com", "claude-haiku-4-5-20251001"
    if key.startswith("AIza"):
        return "gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-1.5-flash"
    return "openai", "https://api.openai.com/v1", "gpt-4o-mini"


def _build_provider(
    provider_name: str,
    base_url: str,
    model_name: str,
    api_key: str,
    timeout: float,
) -> TextGenerationProvider:
    if provider_name == "claude":
        return AnthropicTextProvider(
            base_url=base_url,
            api_key=api_key,
            api_version="2023-06-01",
            model_name=model_name,
            timeout=timeout,
            max_output_tokens=512,
        )
    if provider_name == "gemini":
        return GeminiTextProvider(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            timeout=timeout,
        )
    return OpenAICompatibleTextProvider(
        provider_name="openai",
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        timeout=timeout,
    )


async def run_live_compare(
    *,
    prompt: str,
    api_key: str,
    injected_note: str | None,
    model_name: str | None = None,
    timeout: float = 30.0,
) -> LiveCompareResult:
    """Make real baseline and AuraBook provider calls with the user's key."""
    provider_name, base_url, detected_model_name = _detect_provider(api_key)
    resolved_model_name = model_name.strip() if model_name and model_name.strip() else detected_model_name

    left_events: list[LiveDemoEvent] = [
        LiveDemoEvent(
            at=200,
            kind="agent",
            text=f"Calling {resolved_model_name} without AuraBook context...",
            token_count=0,
            status_label=f"Calling {provider_name}",
        )
    ]
    right_events: list[LiveDemoEvent] = [
        LiveDemoEvent(
            at=200,
            kind="agent",
            text="Querying AuraBook knowledge base for community context...",
            token_count=0,
            status_label="Searching knowledge base",
        )
    ]

    if injected_note:
        right_events.append(
            LiveDemoEvent(
                at=700,
                kind="note",
                text=f"Community note found:\n\n{injected_note}",
                token_count=0,
                status_label="Note injected",
            )
        )
    else:
        right_events.append(
            LiveDemoEvent(
                at=700,
                kind="agent",
                text="No matching community note — proceeding with standard context.",
                token_count=0,
                status_label="No note found",
            )
        )

    right_events.append(
        LiveDemoEvent(
            at=1000,
            kind="agent",
            text=f"Calling {resolved_model_name} with injected community context...",
            token_count=0,
            status_label=f"Calling {provider_name}",
        )
    )

    baseline_provider = _build_provider(provider_name, base_url, resolved_model_name, api_key, timeout)
    aurabook_provider = _build_provider(provider_name, base_url, resolved_model_name, api_key, timeout)
    baseline_tokens = 0
    aurabook_tokens = 0

    try:
        baseline_result = await baseline_provider.generate_text(
            system_prompt=_SYSTEM_NO_NOTE,
            user_prompt=prompt,
        )
    except ProviderRequestError as exc:
        logger.warning("Live compare baseline provider error: %s", exc)
        left_events.append(
            LiveDemoEvent(
                at=2500,
                kind="error",
                text=f"Baseline API error ({provider_name}): {exc}",
                token_count=0,
                status_label="Error",
            )
        )
    else:
        baseline_tokens = baseline_result.total_tokens
        left_events.append(
            LiveDemoEvent(
                at=2500,
                kind="success",
                text=baseline_result.text,
                token_count=baseline_tokens,
                status_label="Complete",
            )
        )
    finally:
        await baseline_provider.close()

    system_prompt = (
        _SYSTEM_WITH_NOTE.format(note=injected_note) if injected_note else _SYSTEM_NO_NOTE
    )
    try:
        aurabook_result = await aurabook_provider.generate_text(
            system_prompt=system_prompt,
            user_prompt=prompt,
        )
    except ProviderRequestError as exc:
        logger.warning("Live compare AuraBook provider error: %s", exc)
        right_events.append(
            LiveDemoEvent(
                at=2800,
                kind="error",
                text=f"AuraBook API error ({provider_name}): {exc}",
                token_count=0,
                status_label="Error",
            )
        )
    else:
        aurabook_tokens = aurabook_result.total_tokens
        right_events.append(
            LiveDemoEvent(
                at=2800,
                kind="success",
                text=aurabook_result.text,
                token_count=aurabook_tokens,
                status_label="Complete",
            )
        )
    finally:
        await aurabook_provider.close()

    logger.info(
        "Live compare complete — provider=%s model=%s baseline_tokens=%d aurabook_tokens=%d note_injected=%s",
        provider_name,
        resolved_model_name,
        baseline_tokens,
        aurabook_tokens,
        injected_note is not None,
    )

    return LiveCompareResult(
        injected_note=injected_note,
        left_events=left_events,
        right_events=right_events,
        baseline_tokens=baseline_tokens,
        aurabook_tokens=aurabook_tokens,
        total_tokens=baseline_tokens + aurabook_tokens,
        provider_used=provider_name,
        model_used=resolved_model_name,
        provider_call_count=2,
    )
