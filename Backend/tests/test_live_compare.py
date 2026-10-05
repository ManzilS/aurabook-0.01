"""Live provider routing without paid requests."""

import pytest

from services.live_compare import _build_provider, run_live_compare


@pytest.mark.asyncio
async def test_deepseek_provider_keeps_its_identity():
    provider = _build_provider(
        "deepseek", "https://api.deepseek.com/v1", "deepseek-chat", "test-key", 1
    )
    try:
        assert provider._provider_name == "deepseek"
        assert provider._base_url == "https://api.deepseek.com/v1"
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected_before_any_network_request():
    with pytest.raises(ValueError, match="Unsupported demo provider"):
        await run_live_compare(
            prompt="hello", api_key="test-key", injected_note=None, provider="unknown"
        )
