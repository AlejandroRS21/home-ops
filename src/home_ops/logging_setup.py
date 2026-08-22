"""Structured logging for Home-Ops.

Opt-in JSON logging to stdout via the ``HOME_OPS_LOG_JSON`` env var
(e.g. ``HOME_OPS_LOG_JSON=1``). Default remains plain-text logging.
JSON logs are one object per line — greppable, and ingestible by any
log shipper (Loki, Datadog, etc.) without a dedicated dependency.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time


class JsonFormatter(logging.Formatter):
    """Emit each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure the root logger once; JSON output when HOME_OPS_LOG_JSON set."""
    if os.environ.get("HOME_OPS_LOG_JSON"):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(logging.INFO)
