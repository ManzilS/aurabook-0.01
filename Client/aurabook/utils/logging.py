"""Structured logging helpers for AuraBook."""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_start_var: ContextVar[float] = ContextVar("request_start", default=0.0)


def get_request_elapsed_ms() -> float:
    """Return the current request elapsed time in milliseconds."""
    start = request_start_var.get()
    if start == 0.0:
        return 0.0
    return (time.time() - start) * 1000


class StructuredFormatter(logging.Formatter):
    """JSON formatter used for production logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON document."""
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        elapsed = get_request_elapsed_ms()
        if elapsed > 0:
            payload["elapsed_ms"] = round(elapsed, 1)
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }
        return json.dumps(payload)


class DevFormatter(logging.Formatter):
    """Human-readable formatter used in development mode."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record for local development."""
        request_id = request_id_var.get()
        elapsed = get_request_elapsed_ms()
        request_part = f" req={request_id}" if request_id != "-" else ""
        elapsed_part = f" [{elapsed:.0f}ms]" if elapsed > 0 else ""
        base = (
            f"{self.formatTime(record)} | {record.levelname:<8s} | "
            f"{record.name}{request_part}{elapsed_part} | {record.getMessage()}"
        )
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def setup_logging(log_level: str = "INFO", dev_mode: bool = False) -> None:
    """Configure the root logger for the current process."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(DevFormatter() if dev_mode else StructuredFormatter())
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
