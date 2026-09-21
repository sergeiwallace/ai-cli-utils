"""Persistent, bounded log files for session-launch diagnostics."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from . import config

_LOG_DIRECTORY = "launch-logs"
_MAX_LOGS_PER_SESSION = 20


def _safe_session_name(session_name: str) -> str:
    """Return a filename-safe session identifier without exposing a path."""
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_name).strip(".-")
    return normalized[:64] or "launch"


def _log_level(message: str) -> int:
    stripped = message.lstrip()
    if stripped.startswith("Error:") or " failed" in stripped.lower():
        return logging.ERROR
    if stripped.startswith("Warning:") or "warning:" in stripped.lower():
        return logging.WARNING
    return logging.INFO


class _LoggingStderr:
    """Mirror launch stderr into the standard logging pipeline line by line."""

    def __init__(self, stream: TextIO, logger: logging.Logger):
        self._stream = stream
        self._logger = logger
        self._partial = ""

    def write(self, message: str) -> int:
        written = self._stream.write(message)
        self._partial += message
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line:
                self._logger.log(_log_level(line), line)
        return written

    def flush(self) -> None:
        if self._partial:
            self._logger.log(_log_level(self._partial), self._partial)
            self._partial = ""
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


@dataclass
class LaunchLog:
    """A launch log and its stderr mirror, kept alive until process handoff."""

    path: Path
    logger: logging.Logger
    stderr: TextIO

    def install_stderr(self) -> None:
        """Capture subsequent launch diagnostics while preserving live stderr."""
        sys.stderr = self.stderr  # type: ignore[assignment]

    @property
    def live_stderr(self) -> TextIO:
        """Return the original stderr stream without re-recording a line."""
        return self.stderr._stream  # type: ignore[attr-defined]


def create_launch_log(session_name: str) -> LaunchLog:
    """Create a timestamped launch log and retain only recent logs per session."""
    safe_name = _safe_session_name(session_name)
    log_dir = config.get_xdg_state_home() / _LOG_DIRECTORY
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = log_dir / f"{safe_name}-{timestamp}-{os.getpid()}.log"
    for stale in sorted(log_dir.glob(f"{safe_name}-*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[
        _MAX_LOGS_PER_SESSION - 1 :
    ]:
        stale.unlink(missing_ok=True)

    logger = logging.getLogger(f"ai_cli.launch.{timestamp}.{id(path)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.info("launch log started for session %s", safe_name)
    return LaunchLog(path=path, logger=logger, stderr=_LoggingStderr(sys.stderr, logger))
