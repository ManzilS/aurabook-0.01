"""Ghost Pre-Fetch background watcher."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path

from watchfiles import Change, awatch

from aurabook.watcher.cdc import cdc_digest

logger = logging.getLogger(__name__)

WatchBatch = set[tuple[Change, str]]
WatchFactory = Callable[[Path, asyncio.Event, int], AsyncIterator[WatchBatch]]
FileReader = Callable[[Path], str]
HashProvider = Callable[[str], str]
Emitter = Callable[[str], None]
PrefetchHandler = Callable[[Path, str], Awaitable[None]]


class GhostPrefetchWatcher:
    """Watches source files and emits CDC hashes for cloud pre-fetching."""

    def __init__(
        self,
        *,
        root: Path,
        extensions: Sequence[str],
        debounce_ms: int = 200,
        settle_time_ms: int = 300,
        emitter: Emitter = print,
        watch_factory: WatchFactory | None = None,
        file_reader: FileReader | None = None,
        hash_provider: HashProvider = cdc_digest,
        prefetch_handler: PrefetchHandler | None = None,
    ) -> None:
        self._root = root
        self._extensions = {extension.lower() for extension in extensions}
        self._debounce_ms = debounce_ms
        self._settle_time_s = settle_time_ms / 1000
        self._emitter = emitter
        self._watch_factory = watch_factory or self._default_watch_factory
        self._file_reader = file_reader or self._read_file
        self._hash_provider = hash_provider
        self._prefetch_handler = prefetch_handler
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the watcher in a background task."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run(), name="ghost-prefetch-watcher")
        logger.info("Ghost Pre-Fetch watcher started for %s", self._root)

    async def stop(self) -> None:
        """Stop the watcher and wait for the task to exit."""
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        logger.info("Ghost Pre-Fetch watcher stopped")

    async def run(self) -> None:
        """Run the watch loop until the stop event is set."""
        if not self._root.exists():
            logger.warning("Watch root does not exist: %s", self._root)
            return

        async for changes in self._watch_factory(self._root, self._stop_event, self._debounce_ms):
            for change_type, raw_path in changes:
                if change_type == Change.deleted:
                    continue

                path = Path(raw_path)
                if not self._is_supported(path):
                    continue

                if not await self._wait_for_settle(path):
                    continue

                try:
                    await self.process_path(path)
                except (OSError, ValueError) as exc:
                    logger.exception("Failed to hash %s: %s", path, exc)
                except Exception as exc:  # pragma: no cover - safety net for background task isolation
                    logger.exception("Unexpected watcher failure for %s: %s", path, exc)

        logger.info("Ghost Pre-Fetch watcher loop exited")

    async def process_path(self, path: Path) -> None:
        """Hash one file and emit the mock cloud upload message."""
        contents = self._file_reader(path)
        digest = self._hash_provider(contents)
        message = (
            f"[Ghost Pre-Fetch] Hashed {path.name} - Hash: {digest} - "
            "Sending to Cloud Brain..."
        )
        self._emitter(message)
        logger.info(message)
        if self._prefetch_handler is not None:
            await self._prefetch_handler(path, digest)

    async def _wait_for_settle(self, path: Path, *, max_wait: float = 30.0) -> bool:
        deadline = time.monotonic() + max_wait
        last_size = -1
        stable_since = -1.0

        while time.monotonic() < deadline:
            try:
                size = path.stat().st_size
            except OSError:
                return False

            now = time.monotonic()
            if size == last_size:
                if stable_since >= 0 and (now - stable_since) >= self._settle_time_s:
                    return True
                if stable_since < 0:
                    stable_since = now
            else:
                last_size = size
                stable_since = -1.0

            await asyncio.sleep(min(0.1, max(self._settle_time_s / 4, 0.01)))

        logger.warning("File did not settle within %.0fs: %s", max_wait, path)
        return False

    async def _default_watch_factory(
        self,
        root: Path,
        stop_event: asyncio.Event,
        debounce_ms: int,
    ) -> AsyncIterator[WatchBatch]:
        async for changes in awatch(
            str(root),
            stop_event=stop_event,
            debounce=debounce_ms,
            recursive=True,
        ):
            yield changes

    def _is_supported(self, path: Path) -> bool:
        return path.suffix.lower() in self._extensions

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="ignore")
