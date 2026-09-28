"""Small, persistent stderr progress reports for interactive session launches.

One grammar for every launcher-owned line, so the operator never has to work out
which of several prefixes a line belongs to::

    [launch] <Phase>: <outcome>
    [launch] <Phase>: <verb-ing ...>                     start, before work that may block
    [launch] <Phase>: still <verb-ing ...> (10s elapsed) heartbeat while a started phase runs
    [launch] <Phase>: <outcome> (12.4s)                  outcome; elapsed shown when slow
    [launch] Warning: <text>                             never suppressed by --quiet
    [launch] Error: <text>                               never suppressed by --quiet
    [launch] Ready: handing off to <Engine> (<session>)  last line before every exec/attach

Colour is applied per token through ``click.style`` and only reaches a stream that
is a TTY; ``click.echo`` strips it otherwise, and ``NO_COLOR`` / ``TERM=dumb`` turn
it off even on a TTY. Nothing here owns terminal state, so nothing needs tearing
down before the launcher replaces itself with the engine.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import AbstractContextManager
from enum import StrEnum
from time import monotonic
from typing import TextIO

import click

PREFIX = "[launch]"
HEARTBEAT_SECONDS = 10.0
SLOW_OUTCOME_SECONDS = 2.0

_PHASE_STYLE = {"bold": True, "fg": "cyan"}
_READY_STYLE = {"bold": True, "fg": "green"}
_WARNING_STYLE = {"bold": True, "fg": "yellow"}
_ERROR_STYLE = {"bold": True, "fg": "red"}
_DIM_STYLE = {"dim": True}


class InstallOrigin(StrEnum):
    """Evidence-backed categories for the currently running installation."""

    EDITABLE_CHECKOUT = "editable checkout"
    DIRECT_URL_OR_VCS = "direct URL/VCS"
    LOCAL_BUILD = "local package build"
    PACKAGE_INDEX = "package index"
    UNKNOWN = "source unknown"


def _stream_is_tty(stream: TextIO | None) -> bool:
    target = stream if stream is not None else sys.stderr
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def _wants_color(stream: TextIO | None) -> bool:
    # Click strips ANSI for a non-TTY on its own; the NO_COLOR convention
    # (no-color.org) and TERM=dumb are the two opt-outs it does not implement.
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return _stream_is_tty(stream)


def _elapsed_text(seconds: float) -> str:
    return f"{seconds:.1f}s"


class _Phase(AbstractContextManager["_Phase"]):
    def __init__(self, reporter: LaunchReporter, name: str, start: str | None):
        self.reporter = reporter
        self.name = name
        self.started_at = monotonic()
        self.has_outcome = False
        self._start = start
        self._finished = threading.Event()
        self._heartbeat: threading.Timer | None = None
        if start is not None:
            reporter._emit(name, start)
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
            self.name,
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
        self.reporter._emit(self.name, self._with_elapsed(message))

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._finish()
        if isinstance(exc, KeyboardInterrupt):
            self.reporter._emit(
                self.name,
                f"interrupted after {_elapsed_text(self.elapsed)}",
                level=logging.WARNING,
                outcome_style=_ERROR_STYLE,
                always=True,
            )
        elif isinstance(exc, SystemExit):
            # The refusal that raised this already printed its own Error line.
            pass
        elif exc is not None:
            self.reporter._emit(
                self.name,
                f"failed after {_elapsed_text(self.elapsed)}: {exc}",
                level=logging.ERROR,
                outcome_style=_ERROR_STYLE,
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

    def _render(self, phase: str, outcome: str, phase_style: dict, outcome_style: dict | None) -> str:
        label = click.style(f"{phase}:", **phase_style)
        body = click.style(outcome, **outcome_style) if outcome_style else outcome
        return f"{click.style(PREFIX, **_DIM_STYLE)} {label} {body}"

    def _emit(
        self,
        phase: str,
        outcome: str,
        *,
        level: int = logging.INFO,
        phase_style: dict = _PHASE_STYLE,
        outcome_style: dict | None = None,
        always: bool = False,
    ) -> None:
        if self.logger is not None:
            self.logger.log(level, "%s %s: %s", PREFIX, phase, outcome)
        if self.quiet and not always:
            return
        rendered = self._render(phase, outcome, phase_style, outcome_style)
        with self._lock:
            click.echo(rendered, err=True, file=self.stream, color=_wants_color(self.stream))

    def start(self, *, engine: str, mode: str, continuing: bool = False) -> None:
        verb = "Continuing" if continuing else "Starting"
        self._emit(f"{verb} {engine} session", mode)

    def phase(self, name: str, start: str | None = None) -> _Phase:
        """A phase; with ``start`` it announces itself and heartbeats until its outcome."""
        return _Phase(self, name, start)

    def detail(self, name: str, outcome: str) -> None:
        if self.logger is not None:
            self.logger.debug("%s %s: %s", PREFIX, name, outcome)
        if self.verbose and not self.quiet:
            with self._lock:
                click.echo(
                    self._render(name, outcome, _PHASE_STYLE, _DIM_STYLE),
                    err=True,
                    file=self.stream,
                    color=_wants_color(self.stream),
                )

    def warning(self, message: str) -> None:
        self._emit("Warning", message, level=logging.WARNING, phase_style=_WARNING_STYLE, always=True)

    def error(self, message: str) -> None:
        self._emit("Error", message, level=logging.ERROR, phase_style=_ERROR_STYLE, always=True)

    def handoff(self, *, engine: str, session: str) -> None:
        self._emit("Ready", f"handing off to {engine} ({session})", phase_style=_READY_STYLE)


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
