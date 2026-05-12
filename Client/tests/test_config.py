"""Tests for application settings."""

from __future__ import annotations

from aurabook.utils.config import Settings


def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("AURABOOK_PORT", "9090")
    monkeypatch.setenv("AURABOOK_WATCH_EXTENSIONS_CSV", ".py,.rs")

    settings = Settings()

    assert settings.port == 9090
    assert settings.allowed_extensions() == (".py", ".rs")


def test_settings_paths_and_urls(tmp_path) -> None:
    settings = Settings(
        upstream_base_url="https://api.example.com/v1/",
        watch_root=str(tmp_path),
    )

    assert settings.resolve_watch_root() == tmp_path.resolve()
    assert settings.upstream_chat_completions_url() == "https://api.example.com/v1/chat/completions"
