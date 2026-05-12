"""Tests for structured logging helpers."""

from __future__ import annotations

import json
import logging
import time

from aurabook.utils.logging import DevFormatter, StructuredFormatter, request_id_var, request_start_var


def test_structured_formatter_includes_request_id() -> None:
    request_id_var.set("req-123")
    request_start_var.set(time.time() - 0.01)
    record = logging.LogRecord(
        name="aurabook.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello",
        args=(),
        exc_info=None,
    )

    formatted = StructuredFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["request_id"] == "req-123"
    assert payload["msg"] == "hello"
    assert "elapsed_ms" in payload


def test_dev_formatter_is_human_readable() -> None:
    request_id_var.set("req-999")
    request_start_var.set(0.0)
    record = logging.LogRecord(
        name="aurabook.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=25,
        msg="problem",
        args=(),
        exc_info=None,
    )

    formatted = DevFormatter().format(record)

    assert "req=req-999" in formatted
    assert "problem" in formatted
