"""Tests for the provider-backed trajectory summarizer."""

from __future__ import annotations

import pytest

from services.model_providers import GeneratedText, ProviderRequestError
from worker.summarizer import HeuristicTrajectoryNoteSummarizer, TrajectoryNoteSummarizer


class FakeGenerationProvider:
    """Fake text-generation provider used in summarizer tests."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_first = False
        self.model_name = "fake-summary-model"

    async def generate_text(self, *, system_prompt: str, user_prompt: str) -> GeneratedText:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ProviderRequestError(
                "rate limited",
                provider="fake",
                retryable=True,
            )
        return GeneratedText(
            text="System Note: Use httpx.AsyncClient for async HTTP requests.",
            total_tokens=37,
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_summarizer_returns_normalized_note(settings, stored_trajectory) -> None:
    provider = FakeGenerationProvider()
    summarizer = TrajectoryNoteSummarizer(
        settings,
        provider=provider,
        retryable_exceptions=(ProviderRequestError,),
    )

    note = await summarizer.summarize(stored_trajectory)

    assert note.startswith("System Note:")
    assert note.endswith(".")
    assert summarizer.total_tokens_used == 37


@pytest.mark.asyncio
async def test_summarizer_retries_rate_limits(settings, stored_trajectory) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    provider = FakeGenerationProvider()
    provider.fail_first = True
    summarizer = TrajectoryNoteSummarizer(
        settings,
        provider=provider,
        sleep_func=fake_sleep,
        retryable_exceptions=(ProviderRequestError,),
    )

    note = await summarizer.summarize(stored_trajectory)

    assert provider.calls == 2
    assert sleep_calls == [settings.openai_retry_base_delay_s]
    assert "httpx.AsyncClient" in note
    assert summarizer.total_tokens_used == 37


@pytest.mark.asyncio
async def test_heuristic_summarizer_works_without_openai(stored_trajectory) -> None:
    summarizer = HeuristicTrajectoryNoteSummarizer()

    note = await summarizer.summarize(stored_trajectory)

    assert note.startswith("System Note:")
    assert "blocking HTTP client" in note
    assert summarizer.total_tokens_used == 0


def test_summarizer_prompt_includes_attempt_history(settings, stored_trajectory) -> None:
    summarizer = TrajectoryNoteSummarizer(
        settings,
        provider=FakeGenerationProvider(),
        retryable_exceptions=(ProviderRequestError,),
    )

    prompt = summarizer._build_prompt(stored_trajectory)

    assert "Attempt history:" in prompt
    assert "Model: gpt-5.4-mini" in prompt
    assert "Tried: Swapped requests for httpx.AsyncClient and reran tests." in prompt
