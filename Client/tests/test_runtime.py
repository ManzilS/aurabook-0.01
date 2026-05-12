"""Tests for runtime dependency wiring."""

from __future__ import annotations

from aurabook.core.models import ResponseGradient
from aurabook.core.runtime import build_runtime
from aurabook.middleware.community import CommunityContextMiddleware
from aurabook.middleware.pii import PIIScrubberMiddleware
from aurabook.middleware.trajectory import TrajectoryCaptureMiddleware
from aurabook.utils.config import Settings
from aurabook.watcher.prefetch import GhostPrefetchWatcher
from tests.conftest import FakeAdapter, StubWatcher


def test_build_runtime_uses_default_dependencies(settings) -> None:
    runtime = build_runtime(settings=settings, emitter=lambda _: None)

    assert isinstance(runtime.pipeline.pre_middleware[0], PIIScrubberMiddleware)
    assert isinstance(runtime.pipeline.pre_middleware[1], CommunityContextMiddleware)
    assert isinstance(runtime.pipeline.post_middleware[0], TrajectoryCaptureMiddleware)
    assert isinstance(runtime.watcher, GhostPrefetchWatcher)
    assert settings.default_adapter in runtime.pipeline.adapters
    assert runtime.identity_store.get_client_id()
    assert runtime.background_services
    assert runtime.status_store.snapshot().client_id
    assert {"openai", "ollama", "lmstudio", "llmstudio", "qwen", "claude", "anthropic", "gemini"} <= set(
        runtime.pipeline.adapters.keys()
    )


def test_build_runtime_accepts_custom_dependencies(settings) -> None:
    adapter = FakeAdapter(reply="custom")
    watcher = StubWatcher()

    runtime = build_runtime(
        settings=settings,
        adapter=adapter,
        pre_middleware=[],
        post_middleware=[],
        watcher=watcher,
    )

    assert runtime.pipeline.adapters["openai_proxy"] is adapter
    assert runtime.watcher is watcher
    assert runtime.pipeline.pre_middleware == []
    assert runtime.pipeline.post_middleware == []


def test_build_runtime_uses_demo_adapter_when_demo_mode(tmp_path) -> None:
    runtime = build_runtime(
        settings=Settings(
            demo_mode=True,
            cloud_enabled=True,
            cloud_demo_mode=True,
            watch_root=str(tmp_path),
            state_dir=str(tmp_path / ".aurabook"),
        ),
        emitter=lambda _: None,
    )

    assert runtime.pipeline.adapters["openai_proxy"].__class__.__name__ == "DemoAdapter"


def test_build_runtime_applies_repo_policy_and_enables_persisted_cache(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_dir = tmp_path / ".aurabook"
    settings = Settings(
        watch_root=str(repo_root),
        state_dir=str(state_dir),
        cloud_enabled=True,
        upload_enabled=True,
    )
    preferences_store = build_runtime(settings=settings, emitter=lambda _: None).preferences_store
    preferences_store.set_repo_policy(
        repo_root,
        max_gradient=ResponseGradient.NOTES,
        cloud_enabled=False,
        upload_enabled=False,
    )

    runtime = build_runtime(settings=settings, emitter=lambda _: None)

    assert runtime.settings.max_gradient == ResponseGradient.NOTES.value
    assert runtime.settings.cloud_enabled is False
    assert runtime.settings.upload_enabled is False
    assert runtime.memory_store._persist_path == settings.resolve_cache_path()
