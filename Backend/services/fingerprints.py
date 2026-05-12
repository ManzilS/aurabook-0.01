"""Normalization and fingerprint helpers for AuraBook Cloud Brain data."""

from __future__ import annotations

import hashlib
import re

_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s]+|/[\w./-]+")
_LINE_NUMBER_PATTERN = re.compile(r"line \d+|\:\d+\b", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s+")

_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"settingwithcopywarning", re.IGNORECASE), "pandas slice assignment warning"),
    (re.compile(r"corsmiddleware", re.IGNORECASE), "cors middleware"),
    (re.compile(r"httpx\.asyncclient", re.IGNORECASE), "httpx async client"),
    (re.compile(r"useeffect", re.IGNORECASE), "use effect"),
    (re.compile(r"traceback", re.IGNORECASE), "traceback"),
)


def normalize_text(text: str) -> str:
    """Normalize free text for dedupe and similarity heuristics."""
    normalized = text.lower()
    normalized = _PATH_PATTERN.sub(" ", normalized)
    normalized = _LINE_NUMBER_PATTERN.sub(" ", normalized)
    for pattern, replacement in _ALIASES:
        normalized = pattern.sub(replacement, normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()


def trajectory_fingerprint(
    *,
    original_prompt: str,
    error_log: str,
    final_fix: str,
) -> str:
    """Return a stable dedupe fingerprint for a trajectory."""
    payload = "||".join(
        (
            normalize_text(original_prompt),
            normalize_text(error_log),
            normalize_text(final_fix),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
