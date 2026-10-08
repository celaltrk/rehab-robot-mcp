"""Logging and audit sinks: a diagnostic trace, audit.jsonl, mcp_interactions.jsonl."""
from __future__ import annotations
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_AUDIT_FILE = _LOG_DIR / "audit.jsonl"
_INTERACTION_FILE = _LOG_DIR / "mcp_interactions.jsonl"
_DIAG_FILE = _LOG_DIR / "robot_control.log"

logger = logging.getLogger("robot_control.audit")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class _JsonlSink:
    """Append-only JSON-lines file, mirrored to stderr. Never raises."""

    def __init__(self, path: Path, tag: str) -> None:
        self.path = path
        self.tag = tag
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _write(self, entry: dict) -> None:
        line = json.dumps(entry, default=str)
        try:
            with self.path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass
        print(f"[{self.tag}] {line}", file=sys.stderr, flush=True)


class AuditLog(_JsonlSink):
    """Per-command clinical audit trail (logs/audit.jsonl)."""

    def __init__(self, path: Path = _AUDIT_FILE) -> None:
        super().__init__(path, "audit")

    def record(self, event: str, robot: str, **fields) -> None:
        self._write({"ts": _utc_now(), "event": event, "robot": robot, **fields})


class InteractionLog(_JsonlSink):
    """The AI boundary trail (logs/mcp_interactions.jsonl), logged in full both ways."""

    def __init__(self, path: Path = _INTERACTION_FILE) -> None:
        super().__init__(path, "ai")

    def record(self, direction: str, kind: str, **fields) -> None:
        self._write({"ts": _utc_now(), "direction": direction, "kind": kind, **fields})


def _has_handler(root: logging.Logger, flag: str) -> bool:
    return any(getattr(h, flag, False) for h in root.handlers)


def configure_logging() -> None:
    """Route diagnostic logging to stderr and a rotating file, never stdout. Idempotent."""
    level_name = os.environ.get("ROBOT_CONTROL_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not _has_handler(root, "_rc_stderr"):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh._rc_stderr = True
        root.addHandler(sh)

    if not _has_handler(root, "_rc_file"):
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(_DIAG_FILE, maxBytes=2_000_000, backupCount=5)
            fh.setFormatter(fmt)
            fh._rc_file = True
            root.addHandler(fh)
        except OSError:
            pass
