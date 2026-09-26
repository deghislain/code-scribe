"""
Structured logging for Code-Scribe.

All agent and API code should use `get_logger(__name__)` to obtain a standard
Python logger.  Log records at INFO and above are:

  1. Written to stderr in a human-readable format (useful during development
     and in container log aggregators).
  2. Forwarded to the WebSocket log bus via `emit_sync` so the frontend
     receives live progress updates without coupling agent code directly to
     the WebSocket layer.

Usage
-----
    from backend.logger import get_logger

    log = get_logger(__name__)
    log.info("Step %d complete: %s", step_num, detail)
    log.warning("Retrying after rate-limit: attempt %d/3", attempt)
    log.error("Clone failed: %s", exc, exc_info=True)
"""

from __future__ import annotations

import logging
import sys

# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

_FMT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"

_formatter = logging.Formatter(fmt=_FMT, datefmt=_DATE_FMT)


# ---------------------------------------------------------------------------
# WebSocket bridge handler
# ---------------------------------------------------------------------------

class _WebSocketHandler(logging.Handler):
    """
    Forwards every log record to the WebSocket log bus (via `emit_sync`)
    so the frontend receives live progress without blocking.

    The import of `emit_sync` is deferred until the first `emit` call so that
    the logging system can be initialised before the FastAPI app is created.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        try:
            from backend.api.websocket import emit_sync  # lazy — avoids circular import
            msg = self.format(record)
            emit_sync(msg)
        except Exception:  # pragma: no cover — fire-and-forget
            self.handleError(record)


# ---------------------------------------------------------------------------
# Root logger setup (called once at import time)
# ---------------------------------------------------------------------------

def _configure_root_logger() -> None:
    root = logging.getLogger("backend")
    if root.handlers:
        return  # already configured (e.g. during tests)

    root.setLevel(logging.DEBUG)

    # Stderr handler — always active
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(_formatter)
    stderr_handler.setLevel(logging.DEBUG)
    root.addHandler(stderr_handler)

    # WebSocket bridge — INFO and above only (DEBUG is too noisy for the UI)
    ws_handler = _WebSocketHandler()
    ws_handler.setFormatter(logging.Formatter(fmt="%(message)s"))
    ws_handler.setLevel(logging.INFO)
    root.addHandler(ws_handler)

    # Silence noisy third-party loggers
    for name in ("apscheduler", "weasyprint", "fonttools", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_root_logger()


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the `backend` namespace."""
    # Normalise names that don't start with `backend.`
    if not name.startswith("backend"):
        name = f"backend.{name}"
    return logging.getLogger(name)
