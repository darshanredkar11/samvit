"""
Structured JSON logging for Samvit.

Usage:
    from samvit.logutil import log, bind

    log.info("Task created", extra={"task_id": tid, "agent": handle})

Environment:
    LOG_FORMAT   — "json" (default) or "text"
    LOG_LEVEL    — logging level (default INFO)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit log records as newline-delimited JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            obj["exception"] = self.formatException(record.exc_info)

        # Merge structured extra fields (skip reserved attrs)
        extras = {k: v for k, v in vars(record).items()
                  if k not in logging.LogRecord.__dict__
                  and not k.startswith("_")}
        if extras:
            obj["data"] = extras

        return json.dumps(obj, default=str)


def setup() -> None:
    """Configure the root logger. Called once at startup."""
    fmt = os.environ.get("LOG_FORMAT", "json")
    level = os.environ.get("LOG_LEVEL", "INFO")

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))

    root = logging.getLogger()
    root.setLevel(level)
    # Remove default handlers to avoid duplicate output
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)
