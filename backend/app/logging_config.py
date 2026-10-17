"""Structured logging setup.

Emits either human-friendly text (development) or single-line JSON
(production), so logs are greppable and machine readable.  A ``request_id`` can
be bound via :func:`get_logger` context data.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render log records as compact JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Readable console formatter with any extra structured fields appended."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.environment == "production":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter(
                "%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str, **context: Any) -> logging.LoggerAdapter:
    """Return a logger that attaches ``context`` to every record."""
    return logging.LoggerAdapter(logging.getLogger(name), context)


__all__ = ["configure_logging", "get_logger", "JsonFormatter", "TextFormatter"]
