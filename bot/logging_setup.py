"""Structured logging.

The point of a log is answering "what broke, when, and had it broken before"
at 3am without reading source code. Human-readable on a terminal, JSON when
something is going to parse it.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# LogRecord's own attributes — anything else was passed via extra= and is ours.
_BUILTIN = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


def _extras(record: logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _BUILTIN}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc)
                          .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Aligned, coloured, and it prints the extras — which are the useful part."""

    COLOURS = {
        "DEBUG": "\033[38;5;244m", "INFO": "\033[38;5;39m",
        "WARNING": "\033[38;5;214m", "ERROR": "\033[38;5;203m",
        "CRITICAL": "\033[48;5;203;38;5;231m",
    }
    RESET = "\033[0m"

    def __init__(self, colour: bool = True):
        super().__init__()
        self.colour = colour and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname[:4]
        if self.colour:
            level = f"{self.COLOURS.get(record.levelname, '')}{level}{self.RESET}"
        extras = _extras(record)
        extras.pop("event", None)
        tail = "  ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{ts} {level:<4} {record.getMessage()}"
        if tail:
            line += f"   {tail}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure(level: str = "INFO", json_logs: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_logs else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
