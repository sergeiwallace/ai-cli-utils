"""Small, persistent stderr progress reports for interactive session launches.

One grammar for every launcher-owned line, so the operator never has to work out
which of several prefixes a line belongs to. A phase is an operation from the
``[output]`` vocabulary, rendered as the line's operation element::

    [launch] start: Starting <Engine> session (<mode>)
    [launch] <phase>: <outcome>
    [launch] <phase>: <verb-ing ...>                     start, before work that may block
    [launch] <phase>: still <verb-ing ...> (10s elapsed) heartbeat while a started phase runs
    [launch] <phase>: <outcome> (12.4s)                  outcome; elapsed shown when slow
    [launch] <phase>: Warning: <text>                    never suppressed by --quiet
    [launch] <phase>: Error: <text>                      never suppressed by --quiet
    [launch] handoff: ready, handing off to <Engine> (<session>)  last line before every exec/attach

Every line is a record written by :mod:`ai_cli.output` under ``Tag.LAUNCH``, whose
renderer owns every element's style and the TTY / ``NO_COLOR`` / ``TERM=dumb``
rules; this module names only which style an outcome takes.
Nothing here owns terminal state, so nothing needs tearing down before the
launcher replaces itself with the engine.
"""

from __future__ import annotations

import logging
import threading
from contextlib import AbstractContextManager
from enum import StrEnum
from time import monotonic
from typing import TextIO

from . import output
from .output import Tag

PREFIX = f"[{Tag.LAUNCH.value}]"
HEARTBEAT_SECONDS = 10.0
SLOW_OUTCOME_SECONDS = 2.0

_DIM_STYLE = "dim"


class InstallOrigin(StrEnum):
    """Evidence-backed categories for the currently running installation."""

    EDITABLE_CHECKOUT = "editable checkout"
    DIRECT_URL_OR_VCS = "direct URL/VCS"
    LOCAL_BUILD = "local package build"
    PACKAGE_INDEX = "package index"
    UNKNOWN = "source unknown"


def _elapsed_text(seconds: float) -> str:
    return f"{seconds:.1f}s"


class _Phase(AbstractContextManager["_Phase"]):
    def __init__(self, reporter: LaunchReporter, op: str, start: str | None):
        self.reporter = reporter
        self.op = output.require_op(op)
        self.started_at = monotonic()
        self.has_outcome = False
        self._start = start
        self._finished = threading.Event()
        self._heartbeat: threading.Timer | None = None
        if start is not None:
            reporter._emit(op, start)
            self._arm_heartbeat()

    @property
    def elapsed(self) -> float:
        return monotonic() - self.started_at

    def _arm_heartbeat(self) -> None:
        timer = threading.Timer(self.reporter.heartbeat_seconds, self._beat)
        timer.daemon = True
        self._heartbeat = timer
        timer.start()

    def _beat(self) -> None:
        if self._finished.is_set():
            return
        elapsed = self.elapsed
        self.reporter._emit(
            self.op,
            f"still {self._start} ({elapsed:.0f}s elapsed)",
            outcome_style=_DIM_STYLE,
        )
        if not self._finished.is_set():
            self._arm_heartbeat()

    def _finish(self) -> None:
        self._finished.set()
        if self._heartbeat is not None:
            self._heartbeat.cancel()

    def _with_elapsed(self, message: str) -> str:
        if self._start is None:
            return message
        elapsed = self.elapsed
        if elapsed >= SLOW_OUTCOME_SECONDS or self.reporter.verbose:
            return f"{message} ({_elapsed_text(elapsed)})"
        return message

    def outcome(self, message: str) -> None:
        self._finish()
        self.has_outcome = True
        self.reporter._emit(self.op, self._with_elapsed(message))

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._finish()
        if isinstance(exc, KeyboardInterrupt):
            self.reporter._emit(
                self.op,
                f"interrupted after {_elapsed_text(self.elapsed)}",
                level=logging.WARNING,
                always=True,
            )
        elif isinstance(exc, SystemExit):
            # The refusal that raised this already printed its own Error line.
            pass
        elif exc is not None:
            self.reporter._emit(
                self.op,
                f"failed after {_elapsed_text(self.elapsed)}: {exc}",
                level=logging.ERROR,
                always=True,
            )
        elif self._start is not None and not self.has_outcome:
            # A started phase owes an outcome line; make the omission visible
            # rather than leaving the start line dangling.
            self.outcome("done")


class LaunchReporter:
    """Emit durable launch phase lines without adding a logging dependency."""

    def __init__(
        self,
        *,
        quiet: bool = False,
        verbose: bool = False,
        logger: logging.Logger | None = None,
        stream: TextIO | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ):
        self.quiet = quiet
        self.verbose = verbose
        self.logger = logger
        self.stream = stream
        self.heartbeat_seconds = heartbeat_seconds
        self._lock = threading.Lock()

    def activate(self) -> LaunchReporter | None:
        """Make this the reporter :func:`active` returns; give back the previous one."""
        return activate(self)

    def _emit(
        self,
        op: str,
        outcome: str,
        *,
        level: int = logging.INFO,
        outcome_style: str = "",
        always: bool = False,
    ) -> None:
        output.require_op(op)
        record_level = {logging.WARNING: output.Level.WARNING, logging.ERROR: output.Level.ERROR}.get(
            level, output.Level.INFO
        )
        if self.logger is not None:
            label = f"{record_level.value.capitalize()}: " if record_level is not output.Level.INFO else ""
            self.logger.log(level, "%s %s: %s%s", PREFIX, op, label, outcome)
        if self.quiet and not always:
            return
        with self._lock:
            output.emit(
                Tag.LAUNCH, op, outcome, err=True, file=self.stream, level=record_level, body_style=outcome_style
            )

    def start(self, *, engine: str, mode: str, continuing: bool = False) -> None:
        verb = "Continuing" if continuing else "Starting"
        self._emit("start", f"{verb} {engine} session ({mode})")

    def phase(self, op: str, start: str | None = None) -> _Phase:
        """A phase named by operation ``op``; with ``start`` it announces itself and heartbeats until its outcome."""
        return _Phase(self, op, start)

    def detail(self, op: str, outcome: str) -> None:
        output.require_op(op)
        if self.logger is not None:
            self.logger.debug("%s %s: %s", PREFIX, op, outcome)
        if self.verbose and not self.quiet:
            with self._lock:
                output.emit(
                    Tag.LAUNCH,
                    op,
                    outcome,
                    err=True,
                    file=self.stream,
                    level=output.Level.DETAIL,
                    body_style=_DIM_STYLE,
                )

    def warning(self, op: str, message: str) -> None:
        self._emit(op, message, level=logging.WARNING, always=True)

    def error(self, op: str, message: str) -> None:
        self._emit(op, message, level=logging.ERROR, always=True)

    def handoff(self, *, engine: str, session: str) -> None:
        self._emit("handoff", f"ready, handing off to {engine} ({session})")


_active: LaunchReporter | None = None


def activate(reporter: LaunchReporter | None) -> LaunchReporter | None:
    """Install ``reporter`` as the active one (``None`` clears it); return the previous."""
    global _active
    previous = _active
    _active = reporter
    return previous


def active() -> LaunchReporter:
    """The reporter of the launch in progress, or a plain default when none is.

    Helpers two or three calls below the launcher (worktree creation, the direnv
    and tmux preflights, the self-update) report through this rather than taking a
    reporter parameter for a logging concern. Outside a launch it still prints, so
    an event is never lost for want of a caller that installed one.
    """
    global _active
    if _active is None:
        _active = LaunchReporter()
    return _active
