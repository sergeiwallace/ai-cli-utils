"""Small, persistent stderr progress reports for interactive session launches."""

from __future__ import annotations

from contextlib import AbstractContextManager
from enum import StrEnum
from time import monotonic

import click


class InstallOrigin(StrEnum):
    """Evidence-backed categories for the currently running installation."""

    EDITABLE_CHECKOUT = "editable checkout"
    DIRECT_URL_OR_VCS = "direct URL/VCS"
    LOCAL_BUILD = "local package build"
    PACKAGE_INDEX = "package index"
    UNKNOWN = "source unknown"


class _Phase(AbstractContextManager["_Phase"]):
    def __init__(self, reporter: LaunchReporter, name: str, start: str | None):
        self.reporter = reporter
        self.name = name
        self.started_at = monotonic()
        self.has_outcome = False
        if start is not None:
            reporter._emit(name, start)

    def outcome(self, message: str) -> None:
        self.has_outcome = True
        self.reporter._emit(self.name, message)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is not None:
            elapsed = monotonic() - self.started_at
            self.reporter._emit(self.name, f"failed after {elapsed:.1f}s: {exc}")
        return False


class LaunchReporter:
    """Emit durable launch phase lines without adding a logging dependency."""

    def __init__(self, *, quiet: bool = False, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose

    def _emit(self, phase: str, outcome: str) -> None:
        if not self.quiet:
            click.echo(f"[launch] {phase}: {outcome}", err=True)

    def start(self, *, engine: str, mode: str, continuing: bool = False) -> None:
        verb = "Continuing" if continuing else "Starting"
        self._emit(f"{verb} {engine} session", mode)

    def phase(self, name: str, start: str | None = None) -> _Phase:
        return _Phase(self, name, start)

    def detail(self, name: str, outcome: str) -> None:
        if self.verbose:
            self._emit(name, outcome)

    def handoff(self, *, engine: str, session: str) -> None:
        self._emit("Ready", f"handing off to {engine} ({session})")
