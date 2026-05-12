"""Tests for CDC hashing helpers."""

from aurabook.watcher.cdc import cdc_chunk, cdc_digest
from aurabook.watcher.fingerprint import chunk_fingerprint


def test_chunk_fingerprint_is_stable() -> None:
    assert chunk_fingerprint("hello") == chunk_fingerprint("hello")


def test_cdc_chunk_covers_entire_content() -> None:
    text = ("def example():\n    return 1\n\n" * 64).strip()
    chunks = cdc_chunk(text, min_size=32, max_size=128, target_size=64)

    assert sum(chunk.length for chunk in chunks) == len(text)
    assert all(chunk.fingerprint for chunk in chunks)


def test_cdc_digest_is_deterministic() -> None:
    text = "deterministic content " * 100

    assert cdc_digest(text) == cdc_digest(text)
