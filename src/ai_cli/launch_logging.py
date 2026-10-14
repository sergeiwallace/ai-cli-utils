"""Persistent, bounded log files for session-launch diagnostics.

Every record a launch produces reaches its log through the output module's file
sink (AI-CLI-2gq3), rendered from the record itself, so a line's level is the
record's own. The stderr mirror here only adds what is not a record: a traceback,
or a line some library printed. A log that cannot be created or written is
skipped: it never changes what the terminal shows.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from . import config
from . import output as _out
from .output import inert

_LOG_DIRECTORY = "launch-logs"
_MAX_LOGS_PER_SESSION = 20

# Breaks a launch-log path tie when the clock cannot. See create_launch_log.
_PATH_UNIQUIFIER = itertools.count()


def _safe_session_name(session_name: str) -> str:
    """Return a filename-safe session identifier without exposing a path."""
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_name).strip(".-")
    return normalized[:64] or "launch"


class _LoggingStderr(_out.MirroredStream):
    """Mirror the text written to launch stderr that is not a record into the launch log."""

    def __init__(self, stream: TextIO, log: LaunchLog):
        self._stream = stream
        self._log_file = log
        self._partial = ""

    @property
    def unmirrored(self) -> TextIO:
        return self._stream

    def write(self, message: str) -> int:
        written = self._stream.write(message)
        self._partial += message
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self._log(line)
        return written

    def _log(self, line: str) -> None:
        line = inert(line)
        if line.strip():
            self._log_file.write_line(_out.file_text_line(line))

    def flush(self) -> None:
        if self._partial:
            self._log(self._partial)
            self._partial = ""
        self._stream.flush()

    # typing.TextIO defines these as stubs returning None, so __getattr__ never
    # sees them: without explicit delegation a terminal reads as "not a TTY".
    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return self._stream.encoding

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


@dataclass
class LaunchLog:
    """A launch log and its stderr mirror, kept alive until process handoff.

    ``path`` is None when the log could not be created; every write is then a no-op.
    """

    path: Path | None
    stderr: TextIO = field(init=False)
    _file: TextIO | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.stderr = _LoggingStderr(sys.stderr, self)

    def write_line(self, line: str) -> None:
        """Append one line; a log that stops accepting writes is closed and the line dropped."""
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(f"{line}\n")
                self._file.flush()
            except (OSError, ValueError):
                with contextlib.suppress(OSError, ValueError):
                    self._file.close()
                self._file = None

    def install_stderr(self) -> None:
        """Capture subsequent launch diagnostics while preserving live stderr."""
        sys.stderr = self.stderr  # type: ignore[assignment]

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                with contextlib.suppress(OSError, ValueError):
                    self._file.close()
                self._file = None

    @property
    def live_stderr(self) -> TextIO:
        """Return the original stderr stream without re-recording a line."""
        return self.stderr._stream  # type: ignore[attr-defined]


def create_launch_log(session_name: str) -> LaunchLog:
    """Create a timestamped launch log and retain only recent logs per session.

    If the log directory or file cannot be created, the launch gets a log that
    records nothing rather than an error: diagnostics are never why a launch fails.
    """
    safe_name = _safe_session_name(session_name)
    log_dir = config.get_xdg_state_home() / _LOG_DIRECTORY
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return LaunchLog(path=None)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = log_dir / f"{safe_name}-{timestamp}-{os.getpid()}.log"
    while path.exists():
        # The timestamp is only as unique as the platform clock is fine-grained. This
        # format asks for microseconds, but `time()` granularity is about 15.6 ms on
        # Windows, so two launches inside one tick resolve to the same path and the
        # second silently reopens the first's file. One process writes one launch log,
        # so production never reached this; the retention count did, reporting fewer
        # files than the limit because several of them were the same file.
        path = log_dir / f"{safe_name}-{timestamp}-{os.getpid()}-{next(_PATH_UNIQUIFIER)}.log"

    for stale in sorted(log_dir.glob(f"{safe_name}-*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[
        _MAX_LOGS_PER_SESSION - 1 :
    ]:
        # A log that cannot be removed is one another process still holds open, which
        # means it is still being written -- so keeping it is the correct outcome, not
        # a concession to one platform. POSIX unlinks an open file happily (the inode
        # outlives the directory entry), which is exactly why this was invisible there.
        # Windows raises `PermissionError: [WinError 32]`, which `missing_ok` does not
        # suppress, so `ai c` died inside log setup before launching anything as soon
        # as a prune candidate belonged to a live session (AI-CLI-4tno).
        with contextlib.suppress(OSError):
            stale.unlink(missing_ok=True)

    try:
        log_file = path.open("a", encoding="utf-8")
    except OSError:
        return LaunchLog(path=None)
    launch_log = LaunchLog(path=path, _file=log_file)
    launch_log.write_line(_out.file_text_line(f"launch log started for session {safe_name}"))
    return launch_log
