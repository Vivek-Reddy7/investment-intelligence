"""Structured logging, with a run id on every line.

Architecture §2.8 said "structured JSON logs with a request/job ID from the
first commit" and then this project did not do it for sixteen phases, which is
exactly how that item usually goes. Retrofitting observability is miserable
precisely because the correlation id has to be threaded through everything, so
the cost of the delay is real and is being paid now.

Two properties matter more than the format:

**Correlation.** Every line emitted during a run carries that run's id. A log
without one is a pile of statements; with one, "what happened in run 47" is a
grep. The id is held in a `ContextVar`, so it does not have to be passed
through every function signature.

**Machine-readable by default.** JSON to stdout, because that is what a log
aggregator ingests and what GitHub Actions preserves. A human reading a CI log
gets slightly worse ergonomics; a human debugging a three-week-old failure
gets a queryable record. The second is the case that matters.

Deliberately not a dependency. `structlog` would be a better library and this
is ~70 lines against the standard library.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Values that must never reach a log line, matched on key name. A token in a
# log is a credential in a place nobody thinks to protect -- CI logs are
# readable by anyone with repository access, and log aggregators outlive the
# secrets they contain.
REDACTED_KEYS = frozenset({
    "token", "token_hash", "password", "secret", "authorization", "cookie",
    "database_url", "dsn", "email",
})
REDACTED = "[redacted]"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        run = _run_id.get()
        if run is not None:
            payload["run_id"] = run
        payload.update(_context.get())

        # Anything passed as `extra=`. Redacted by key name rather than by
        # value inspection, because a token looks like any other string.
        for key, value in getattr(record, "fields", {}).items():
            payload[key] = REDACTED if key.lower() in REDACTED_KEYS else value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure(level: str | None = None, stream=None) -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    root = logging.getLogger()
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


@contextmanager
def run_context(run_id: str | int, **fields: Any) -> Iterator[None]:
    """Tag every log line in this block with a run id and extra fields.

    Nested contexts merge rather than replace, so an inner block can add the
    instrument it is working on without losing the run it belongs to.
    """
    run_token = _run_id.set(str(run_id))
    merged = {**_context.get(), **fields}
    ctx_token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(ctx_token)
        _run_id.reset(run_token)


def log(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a line with structured fields.

    A thin wrapper because `logger.info(msg, extra={"fields": {...}})` is the
    only way to get arbitrary keys through the stdlib, and writing that at
    every call site invites people to skip it.
    """
    logger.log(level, message, extra={"fields": fields})


def info(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.INFO, message, **fields)


def warning(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.WARNING, message, **fields)


def error(logger: logging.Logger, message: str, **fields: Any) -> None:
    log(logger, logging.ERROR, message, **fields)
