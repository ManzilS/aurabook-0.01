"""Content-defined chunking helpers for Ghost Pre-Fetch hashing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aurabook.watcher.fingerprint import chunk_fingerprint

_BOUNDARIES: list[re.Pattern[str]] = [
    re.compile(r"\n#{1,6}\s"),
    re.compile(r"\n\n"),
    re.compile(r"\ndef "),
    re.compile(r"\nclass "),
    re.compile(r"\nfunction "),
    re.compile(r"\n\}\n"),
    re.compile(r"\n---+\n"),
    re.compile(r"\n"),
]
_SNAP_TOLERANCE = 128


@dataclass(frozen=True, slots=True)
class ChunkBoundary:
    """Describes a single CDC chunk and its fingerprint."""

    offset: int
    length: int
    fingerprint: str


def _gear_hash_cut(text: str, start: int, min_size: int, max_size: int, mask: int) -> int:
    h = 0
    end = min(start + max_size, len(text))
    scan_from = start + min_size

    for index in range(start, end):
        h = ((h << 1) + ord(text[index])) & 0xFFFF_FFFF_FFFF_FFFF
        if index >= scan_from and (h & mask) == 0:
            return index + 1

    return end


def _snap_to_boundary(text: str, raw_cut: int, tolerance: int = _SNAP_TOLERANCE) -> int:
    lo = max(0, raw_cut - tolerance)
    hi = min(len(text), raw_cut + tolerance)
    window = text[lo:hi]

    best_position: int | None = None
    for pattern in _BOUNDARIES:
        match = pattern.search(window)
        if match:
            candidate = lo + match.end()
            if best_position is None or abs(candidate - raw_cut) < abs(best_position - raw_cut):
                best_position = candidate
            break

    return best_position if best_position is not None else raw_cut


def cdc_chunk(
    content: str,
    *,
    min_size: int = 256,
    max_size: int = 4096,
    target_size: int = 1024,
) -> list[ChunkBoundary]:
    """Split text into CDC chunks and fingerprint each chunk."""
    if not content:
        return []

    bits = max(1, target_size.bit_length() - 1)
    mask = (1 << bits) - 1

    boundaries: list[ChunkBoundary] = []
    position = 0

    while position < len(content):
        remaining = len(content) - position
        if remaining <= max_size:
            chunk_text = content[position:]
            boundaries.append(
                ChunkBoundary(
                    offset=position,
                    length=len(chunk_text),
                    fingerprint=chunk_fingerprint(chunk_text),
                )
            )
            break

        raw_cut = _gear_hash_cut(content, position, min_size, max_size, mask)
        cut = _snap_to_boundary(content, raw_cut)
        cut = min(cut, position + max_size)
        cut = max(cut, position + min_size)

        chunk_text = content[position:cut]
        boundaries.append(
            ChunkBoundary(
                offset=position,
                length=len(chunk_text),
                fingerprint=chunk_fingerprint(chunk_text),
            )
        )
        position = cut

    return boundaries


def cdc_digest(
    content: str,
    *,
    min_size: int = 256,
    max_size: int = 4096,
    target_size: int = 1024,
) -> str:
    """Return a deterministic digest derived from the file's CDC chunks."""
    boundaries = cdc_chunk(
        content,
        min_size=min_size,
        max_size=max_size,
        target_size=target_size,
    )
    if not boundaries:
        return chunk_fingerprint("")
    joined_fingerprints = "|".join(boundary.fingerprint for boundary in boundaries)
    return chunk_fingerprint(joined_fingerprints)
