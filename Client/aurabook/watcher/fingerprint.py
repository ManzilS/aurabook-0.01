"""Fast content fingerprint helpers reused by the CDC watcher."""

from __future__ import annotations

import xxhash


def chunk_fingerprint(text: str) -> str:
    """Return an xxHash64 digest for a text chunk."""
    return xxhash.xxh64(text.encode("utf-8")).hexdigest()
