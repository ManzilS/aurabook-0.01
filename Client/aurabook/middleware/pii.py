"""Regex-based PII scrubbing middleware."""

from __future__ import annotations

import re
from typing import ClassVar

from aurabook.core.models import ContentPart
from aurabook.core.state import PipelineState
from aurabook.middleware.base import PreMiddleware


class PIIScrubberMiddleware(PreMiddleware):
    """Redacts common sensitive data before a prompt leaves the machine."""

    name = "pii_scrubber"

    _PATTERNS: ClassVar[tuple[tuple[re.Pattern[str], str], ...]] = (
        (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"), "[REDACTED_API_KEY]"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
        (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
        (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_GITLAB_TOKEN]"),
        (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
        (re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[REDACTED_STRIPE_KEY]"),
        (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "[REDACTED_NPM_TOKEN]"),
        (re.compile(r"\bAIza[0-9A-Za-z\\-_]{35}\b"), "[REDACTED_GOOGLE_API_KEY]"),
        (
            re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\b"),
            "[REDACTED_JWT]",
        ),
        (
            re.compile(r"\b(?:Bearer|bearer)\s+[A-Za-z0-9._\-]{20,}\b"),
            "Bearer [REDACTED_TOKEN]",
        ),
        (
            re.compile(
                r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
                re.IGNORECASE,
            ),
            "[REDACTED_EMAIL]",
        ),
        (
            re.compile(
                r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?):\/\/[^\s]+",
                re.IGNORECASE,
            ),
            "[REDACTED_DATABASE_URL]",
        ),
        (
            re.compile(
                r"(?im)\b(?:api[_-]?key|secret|token|password|access[_-]?token)\s*=\s*['\"]?[^'\"\s]+['\"]?"
            ),
            "[REDACTED_ENV_ASSIGNMENT]",
        ),
        (
            re.compile(r"(?im)\bcookie:\s*[^;\n]+(?:;[^\n]+)*"),
            "Cookie: [REDACTED_COOKIE]",
        ),
        (
            re.compile(
                r"\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
                r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}\b"
            ),
            "[REDACTED_IP]",
        ),
        (
            re.compile(
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
            ),
            "[REDACTED_PRIVATE_KEY]",
        ),
    )

    async def process(self, state: PipelineState) -> PipelineState:
        """Redact matching values from all outbound messages."""
        total_redactions = 0
        for message in state.request.messages:
            cleaned_content, redactions = self._sanitize_content(message.content)
            message.content = cleaned_content
            total_redactions += redactions

        state.extras["pii_redactions"] = total_redactions
        return state

    def scrub_text(self, text: str) -> tuple[str, int]:
        """Redact sensitive values from a plain-text string."""
        cleaned = text
        total_matches = 0
        for pattern, replacement in self._PATTERNS:
            cleaned, matches = pattern.subn(replacement, cleaned)
            total_matches += matches
        return cleaned, total_matches

    def _sanitize_content(
        self,
        content: str | list[ContentPart] | None,
    ) -> tuple[str | list[ContentPart] | None, int]:
        if isinstance(content, str):
            return self.scrub_text(content)
        if content is None:
            return None, 0

        total_redactions = 0
        sanitized_parts: list[ContentPart] = []
        for part in content:
            updated_part = part.model_copy(deep=True)
            if updated_part.text:
                updated_part.text, redactions = self.scrub_text(updated_part.text)
                total_redactions += redactions
            sanitized_parts.append(updated_part)
        return sanitized_parts, total_redactions
