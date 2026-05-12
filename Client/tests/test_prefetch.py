"""Tests for the Ghost Pre-Fetch watcher."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from watchfiles import Change

from aurabook.watcher.prefetch import GhostPrefetchWatcher


@pytest.mark.asyncio
async def test_process_path_emits_hash_message(tmp_path) -> None:
    file_path = tmp_path / "app.py"
    file_path.write_text("print('hi')", encoding="utf-8")
    emitted: list[str] = []
    prefetched: list[tuple[str, str]] = []
    watcher = GhostPrefetchWatcher(
        root=tmp_path,
        extensions=(".py",),
        emitter=emitted.append,
        hash_provider=lambda _: "hash123",
        prefetch_handler=lambda path, digest: _record_prefetch(prefetched, path, digest),
    )

    await watcher.process_path(file_path)

    assert emitted == [
        "[Ghost Pre-Fetch] Hashed app.py - Hash: hash123 - Sending to Cloud Brain..."
    ]
    assert prefetched == [(str(file_path), "hash123")]


@pytest.mark.asyncio
async def test_run_filters_unsupported_changes(tmp_path, monkeypatch) -> None:
    emitted: list[str] = []
    reader = Mock(return_value="print('hi')")

    async def fake_watch_factory(root, stop_event, debounce_ms):
        yield {
            (Change.modified, str(tmp_path / "app.py")),
            (Change.modified, str(tmp_path / "notes.txt")),
            (Change.deleted, str(tmp_path / "gone.ts")),
        }
        stop_event.set()

    watcher = GhostPrefetchWatcher(
        root=tmp_path,
        extensions=(".py", ".ts"),
        emitter=emitted.append,
        watch_factory=fake_watch_factory,
        file_reader=reader,
        hash_provider=lambda _: "digest",
    )
    monkeypatch.setattr(watcher, "_wait_for_settle", AsyncMock(return_value=True))

    await watcher.run()

    reader.assert_called_once_with(tmp_path / "app.py")
    assert emitted == [
        "[Ghost Pre-Fetch] Hashed app.py - Hash: digest - Sending to Cloud Brain..."
    ]


@pytest.mark.asyncio
async def test_start_and_stop_manage_background_task(tmp_path) -> None:
    async def fake_watch_factory(root, stop_event, debounce_ms):
        while not stop_event.is_set():
            await asyncio.sleep(0)
        if False:
            yield set()

    watcher = GhostPrefetchWatcher(
        root=tmp_path,
        extensions=(".py",),
        watch_factory=fake_watch_factory,
        file_reader=lambda _: "print('hi')",
        hash_provider=lambda _: "digest",
    )

    await watcher.start()
    assert watcher._task is not None

    await watcher.stop()

    assert watcher._task is None


async def _record_prefetch(
    calls: list[tuple[str, str]],
    path,
    digest: str,
) -> None:
    calls.append((str(path), digest))
