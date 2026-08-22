"""Tests for structured JSON logging."""

from __future__ import annotations

import json
import logging

from home_ops.logging_setup import JsonFormatter


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_emits_single_line_json() -> None:
    formatter = JsonFormatter()
    record = _make_record("scan started")
    out = formatter.format(record)
    # Must be a single line and valid JSON.
    assert "\n" not in out
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "scan started"
    assert "ts" in payload


def test_includes_exception_on_error() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed")
        record.exc_info = __import__("sys").exc_info()
        out = formatter.format(record)
    payload = json.loads(out)
    assert "exc_info" in payload
    assert "boom" in payload["exc_info"]
