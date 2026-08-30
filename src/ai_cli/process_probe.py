"""Cross-platform process inspection and termination for the session registry.

Why this module exists
---------------------
Claude Code records every running session in ``~/.claude/sessions/<pid>.json``, and
the launcher reads those records to decide whether a session name is still in use.
Deciding that needs three answers from the operating system -- does the recorded pid
exist, is that process actually *running* the session (rather than stopped, or
exited and unreaped), and is it the same process the record was written for -- plus
a fourth once the answer is "abandoned": end it.

All four were originally implemented directly against Linux ``/proc``. Off Linux the
readers returned None for every question, so the "is this the recorded process"
gate could never be satisfied and reclamation returned False before doing anything:
a silent, permanent no-op on macOS and Windows with no error to show for it. The
termination escalation was Linux-only for a second reason -- ``signal.SIGCONT`` and
``signal.SIGKILL`` do not exist on Windows at all, so merely *reaching* it there
would have raised ``AttributeError``. The missing procfs was the only thing hiding
that second defect, which is why both have to be answered by one change.

Two implementations, not three
------------------------------
* :class:`ProcfsProbe` -- Linux. Keeps reading ``/proc/<pid>/stat`` directly: the
  ``starttime`` field is an exact integer identity token that the records carry
  verbatim, the state character separates "stopped" from "running", and the
  escalation can aim its signals at the process *group*, which reaps a wrapper's
  children instead of orphaning them. psutil offers nothing as precise, and this is
  the behaviour a frozen regression suite already pins.
* :class:`PsutilProbe` -- macOS and Windows. psutil is already this package's
  cross-platform process layer (a hard dependency) and answers all four questions on
  both: ``pid_exists()``, ``status()``, ``create_time()``, and
  ``terminate()``/``resume()``/``kill()`` over ``children(recursive=True)``. The two
  platforms are not split into separate classes because the mechanism is identical
  on both: ``resume()`` is ``SIGCONT`` on POSIX and ``ResumeThread`` on Windows, so
  one escalation preserves the POSIX semantics that matter (a stopped process queues
  a ``SIGTERM`` and does not act on it until something continues it) while remaining
  legal where no such signal exists. The single thing that differs between them is
  the by-hand remedy printed to an operator, which is one string.

Callers ask :func:`probe_for` for an implementation and never branch on the platform
themselves.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

import psutil

#: Seconds allowed for each half of a termination escalation (graceful, then forced).
_END_TIMEOUT_SECONDS = 5.0

#: How often the bounded wait re-reads whether the process has gone.
_POLL_SECONDS = 0.05

#: Recorded start times are compared to psutil's with a tolerance rather than for
#: equality: ``create_time()`` is a float derived from the same clock the record was
#: written from, and a record may carry that clock truncated to whole milliseconds.
#: One second is far larger than either error and far smaller than any realistic
#: pid-reuse window, since the process holding a recycled pid can only have started
#: after the recorded one exited.
_START_TIME_TOLERANCE_SECONDS = 1.0

#: A Windows FILETIME counts 100-nanosecond intervals from 1601-01-01 UTC.
_FILETIME_TICKS_PER_SECOND = 10_000_000
_FILETIME_EPOCH_OFFSET_SECONDS = 11_644_473_600

#: Magnitude floors that identify the unit of a recorded start time, since the record
#: does not name it. Today epoch seconds are ~1.8e9, epoch milliseconds ~1.8e12 and a
#: Windows FILETIME ~1.3e17; the gaps between the bands are centuries wide.
_FILETIME_FLOOR = 1e16
_EPOCH_MILLIS_FLOOR = 1e11
_EPOCH_SECONDS_FLOOR = 1e9


def _procfs_root() -> Path:
    """Where a platform with procfs exposes it.

    A function rather than a constant so a test can point it at somewhere that does
    not exist and exercise the no-procfs path on any host, including Linux.
    """
    return Path("/proc")


def _posix_end_hint(pid: int) -> str:
    """The by-hand escalation for a stopped process on a platform with signals."""
    return f"`kill -CONT {pid}; kill -TERM {pid}`"


class StartTimeMatch(Enum):
    """Whether a record's ``procStart`` identifies the process now holding its pid.

    ``UNRECORDED`` and ``UNPROVEN`` are deliberately distinct. A record that carries
    no start time this platform can compare cannot *refute* the pid's identity, so a
    running pid stays live; a record that carries one nothing can confirm must never
    be acted on, so the process is left unsignalled. Only ``MATCH`` may authorise a
    termination.
    """

    MATCH = "match"
    UNRECORDED = "unrecorded"
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class ProcessIdentity:
    """An immutable, backend-specific process-birth identity.

    ``marker`` deliberately remains in the unit supplied by the backend.  A
    procfs start-time tick and a psutil epoch timestamp are not interchangeable,
    so callers may compare identities only for exact equality.
    """

    backend: str
    marker: int | float


class ProcessProbe(ABC):
    """What the session registry needs to know about a recorded pid, per platform.

    Each implementation names its own state words, because they come from the
    platform: ``T`` on Linux, ``stopped`` on macOS. Declared without defaults on
    purpose -- an empty default would turn a subclass that forgot them into exactly
    the silent always-False no-op this whole module exists to remove, where the
    missing attribute raises instead.
    """

    #: State words meaning the process has exited and holds nothing open.
    ended_states: ClassVar[frozenset[str]]

    #: State words meaning present, but not running the session that recorded it.
    abandoned_states: ClassVar[frozenset[str]]

    @abstractmethod
    def is_present(self, pid: int) -> bool:
        """Whether ``pid`` names a process at all."""

    @abstractmethod
    def state(self, pid: int) -> str | None:
        """This platform's own word for what ``pid`` is doing, or None if unreadable.

        Reported to operators verbatim, so it stays platform-native (``T`` on Linux,
        ``stopped`` on macOS) rather than being flattened into a shared vocabulary
        that would describe neither.
        """

    @abstractmethod
    def start_time_match(self, pid: int, recorded: object) -> StartTimeMatch:
        """Compare ``recorded`` against the start time of the process holding ``pid``.

        ``recorded`` is a raw registry value of unknown type, because the record is
        written by another program and may carry any shape (or none).
        """

    @abstractmethod
    def capture_identity(self, pid: int) -> ProcessIdentity | None:
        """Capture this backend's process-birth marker, or ``None`` if unknown.

        ``None`` is intentionally not synthesized from pid existence.  Consumers
        that need a safe identity must treat it as unavailable evidence.
        """

    @abstractmethod
    def end_process(self, pid: int, timeout: float = _END_TIMEOUT_SECONDS) -> bool:
        """End ``pid`` and anything it wraps, then confirm by re-reading the OS.

        Never answers from a signal's or a call's return value: a terminate that
        "succeeded" against a stopped process and changed nothing is the exact trap
        this escalation exists to survive.
        """

    @abstractmethod
    def manual_end_hint(self, pid: int) -> str:
        """The command an operator can run by hand when reclamation could not act."""

    def has_ended(self, pid: int) -> bool:
        """True when ``pid`` is gone, or present only as an already-exited husk.

        A zombie counts as ended: it has exited and holds nothing open, and what
        remains outstanding is its parent's ``wait()``, which a launcher that did not
        spawn the process cannot perform (and a parent that is itself stopped never
        will).
        """
        return not self.is_present(pid) or self.state(pid) in self.ended_states

    def is_abandoned(self, pid: int) -> bool:
        """True when ``pid`` is present but is not running the session.

        Pid existence alone cannot tell a session an operator is using from one that
        was abandoned; the state can. A stopped process holds its pid and its open
        files, never resumes on its own, and cannot even act on a ``SIGTERM`` until
        something continues it -- which is how an exited session kept its name
        reserved forever.
        """
        return self.state(pid) in self.abandoned_states

    def _ended_within(self, pid: int, seconds: float) -> bool:
        """Poll :meth:`has_ended` until it answers True or ``seconds`` elapse."""
        deadline = time.monotonic() + seconds
        while True:
            if self.has_ended(pid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_SECONDS)


class ProcfsProbe(ProcessProbe):
    """Linux: inspection through ``/proc``, termination through POSIX signals.

    Only ever selected where the procfs directory really exists, which is also the
    only place the signals this class sends are defined.
    """

    #: ``/proc/<pid>/stat`` state characters for a process that has already exited and
    #: is only waiting to be reaped by its parent.
    ended_states: ClassVar[frozenset[str]] = frozenset({"Z", "X", "x"})

    #: Plus ``T`` job-control stop and ``t`` tracing stop: present, but not running.
    abandoned_states: ClassVar[frozenset[str]] = ended_states | frozenset({"T", "t"})

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else _procfs_root()

    def _stat_fields(self, pid: int) -> list[str]:
        """``/proc/<pid>/stat`` fields, counted from after the *last* ``)``.

        Field 2 is the executable name, parenthesized, and may itself contain spaces
        and parens -- so splitting the whole line shifts every later field for a
        process named e.g. ``claude (worker) 1`` and silently reads the wrong value.
        An unreadable or malformed file yields no fields.
        """
        try:
            line = (self._root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        if ")" not in line:
            return []
        return line.rpartition(")")[2].split()

    def is_present(self, pid: int) -> bool:
        return (self._root / str(pid)).exists()

    def state(self, pid: int) -> str | None:
        fields = self._stat_fields(pid)
        return fields[0] if fields else None

    def start_time_match(self, pid: int, recorded: object) -> StartTimeMatch:
        """Compare field 22 (``starttime``) exactly: it is a count of ticks, not a clock."""
        if not isinstance(recorded, int) or isinstance(recorded, bool):
            return StartTimeMatch.UNRECORDED
        fields = self._stat_fields(pid)
        if len(fields) < 20:
            return StartTimeMatch.UNPROVEN
        try:
            actual = int(fields[19])
        except ValueError:
            return StartTimeMatch.UNPROVEN
        return StartTimeMatch.MATCH if actual == recorded else StartTimeMatch.UNPROVEN

    def capture_identity(self, pid: int) -> ProcessIdentity | None:
        """Return procfs field 22 unchanged, or ``None`` when it cannot be read."""
        if pid <= 0:
            return None
        fields = self._stat_fields(pid)
        if len(fields) < 20:
            return None
        try:
            return ProcessIdentity("procfs", int(fields[19]))
        except ValueError:
            return None

    def end_process(self, pid: int, timeout: float = _END_TIMEOUT_SECONDS) -> bool:
        """SIGTERM, SIGCONT, bounded wait, SIGKILL -- then answer from ``/proc``.

        The SIGCONT is part of the escalation rather than belt-and-braces: a stopped
        process *queues* the SIGTERM and does not act on it until something continues
        it, which is exactly how a ``kill -TERM`` that returned 0 left an exited
        session alive in state ``T``.

        Signals are aimed at the process *group*, so a wrapper's children go with it
        instead of being orphaned. The one exception is a group that is this process's
        own: a group signal there would kill the launcher itself, and everything else
        sharing its job, so the recorded pid alone is signalled.

        The outcome is confirmed against the real ``/proc`` even when this probe was
        built on an injected directory -- the signals are real, so confirming
        anywhere else would only make the answer dishonest.
        """
        real_root = _procfs_root()
        confirm = self if self._root == real_root else ProcfsProbe(real_root)
        group = self._signal_group(pid)

        def send(sig: int) -> None:
            # Failures are ignored on purpose: whether the process ended is read back
            # from /proc below, never inferred from a signal's return.
            with contextlib.suppress(OSError):
                if group:
                    os.killpg(group, sig)
                else:
                    os.kill(pid, sig)

        send(signal.SIGTERM)
        send(signal.SIGCONT)
        if confirm._ended_within(pid, timeout):
            return True
        send(signal.SIGKILL)
        return confirm._ended_within(pid, timeout)

    def _signal_group(self, pid: int) -> int:
        """The process group to signal, or 0 to signal ``pid`` on its own."""
        if not (hasattr(os, "killpg") and hasattr(os, "getpgid")):
            return 0
        try:
            pgid = os.getpgid(pid)
        except OSError:
            return 0
        return pgid if pgid > 1 and pgid != os.getpgrp() else 0

    def manual_end_hint(self, pid: int) -> str:
        return _posix_end_hint(pid)


class PsutilProbe(ProcessProbe):
    """macOS and Windows: inspection and termination through psutil.

    Windows reports far less than macOS does here, and the difference is honest
    rather than papered over: it has no job-control stop and no zombies, and psutil's
    ``status()`` only answers ``stopped`` when it can see every thread suspended
    (measured on Windows 11, a suspended sleeping process still read ``running``).
    So :meth:`is_abandoned` is in practice a POSIX answer, while identity matching
    and termination work fully on both.
    """

    ended_states: ClassVar[frozenset[str]] = frozenset({psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD})
    abandoned_states: ClassVar[frozenset[str]] = ended_states | frozenset(
        {psutil.STATUS_STOPPED, psutil.STATUS_TRACING_STOP}
    )

    def is_present(self, pid: int) -> bool:
        return psutil.pid_exists(pid)

    def has_ended(self, pid: int) -> bool:
        """Use a process handle to avoid a stale ``pid_exists`` result on macOS."""
        try:
            proc = psutil.Process(pid)
            return not proc.is_running() or proc.status() in self.ended_states
        except psutil.AccessDenied:
            return False
        except (psutil.NoSuchProcess, OSError):
            return True

    def state(self, pid: int) -> str | None:
        try:
            return psutil.Process(pid).status()
        except psutil.ZombieProcess:
            # Raising rather than answering for a process that has exited and not been
            # reaped is itself the answer.
            return psutil.STATUS_ZOMBIE
        except (psutil.Error, OSError):
            return None

    def start_time_match(self, pid: int, recorded: object) -> StartTimeMatch:
        expected = _recorded_epoch_seconds(recorded)
        if expected is None:
            return StartTimeMatch.UNRECORDED
        try:
            actual = psutil.Process(pid).create_time()
        except (psutil.Error, OSError):
            return StartTimeMatch.UNPROVEN
        if abs(actual - expected) <= _START_TIME_TOLERANCE_SECONDS:
            return StartTimeMatch.MATCH
        return StartTimeMatch.UNPROVEN

    def capture_identity(self, pid: int) -> ProcessIdentity | None:
        """Return psutil's native creation timestamp without unit conversion."""
        if pid <= 0:
            return None
        try:
            marker = psutil.Process(pid).create_time()
        except (psutil.Error, OSError):
            return None
        if not isinstance(marker, (int, float)) or isinstance(marker, bool) or not math.isfinite(marker):
            return None
        return ProcessIdentity("psutil", marker)

    def end_process(self, pid: int, timeout: float = _END_TIMEOUT_SECONDS) -> bool:
        """Terminate, continue, bounded wait, kill -- then answer from psutil.

        ``resume()`` is this layer's ``SIGCONT`` and is part of the mechanism on
        POSIX, where a stopped process queues the ``terminate()``'s SIGTERM and does
        not act on it until something continues it. On Windows it is a harmless
        no-op, because terminating there does not need the target to be running.
        """
        targets = self._tree(pid)
        _for_each(targets, psutil.Process.terminate)
        _for_each(targets, psutil.Process.resume)
        if self._ended_within(pid, timeout):
            return True
        _for_each(targets, psutil.Process.kill)
        return self._ended_within(pid, timeout)

    def _tree(self, pid: int) -> list[psutil.Process]:
        """``pid`` and its descendants, collected before anything is signalled.

        Read up front because once the recorded process is gone its descendants can
        no longer be found through it, and a wrapper's children have to be reaped
        rather than orphaned -- the same requirement the Linux escalation meets by
        signalling the process group.

        The current process is never a target. ``ai c`` can be launched from inside
        the very session being reclaimed, and the descendant walk would otherwise
        include the launcher itself; this is the counterpart of the Linux guard
        against signalling the caller's own process group.
        """
        try:
            proc = psutil.Process(pid)
        except (psutil.Error, OSError):
            return []
        try:
            found = [proc, *proc.children(recursive=True)]
        except (psutil.Error, OSError):
            # Process enumeration can be denied even when the recorded process
            # itself remains signalable. Reclaim that process rather than turning
            # an unavailable descendant scan into a complete no-op.
            found = [proc]
        return [target for target in found if target.pid != os.getpid()]

    def manual_end_hint(self, pid: int) -> str:
        """The one place this probe's two platforms differ: Windows has no ``kill``."""
        if sys.platform == "win32":
            return f"`taskkill /PID {pid} /T /F`"
        return _posix_end_hint(pid)


def _for_each(targets: list[psutil.Process], action: Callable[[psutil.Process], None]) -> None:
    """Apply ``action`` to every target, ignoring every failure.

    Whether the processes ended is read back from psutil afterwards, so a call that
    fails because its target has already gone carries no information worth raising.
    """
    for target in targets:
        with contextlib.suppress(psutil.Error, OSError):
            action(target)


def _recorded_epoch_seconds(recorded: object) -> float | None:
    """Interpret a registry ``procStart`` value as epoch seconds, or None.

    Measured on Windows 11: Claude Code writes ``procStart`` as a decimal *string*
    holding a Windows FILETIME, which matched ``psutil.create_time()`` to under a
    microsecond for every live session on the host. Epoch seconds are also accepted
    (psutil's own unit) and epoch milliseconds (the unit these same records use for
    ``startedAt``).

    The unit has to be inferred from magnitude because the record does not name it.
    A value in no recognised band, or one recognised as the wrong unit, yields no
    match -- so a misread token can only ever make a reclamation decline to act, and
    never authorise one against the wrong process.
    """
    if isinstance(recorded, bool):
        return None
    if isinstance(recorded, str):
        try:
            recorded = int(recorded)
        except ValueError:
            return None
    if not isinstance(recorded, (int, float)):
        return None
    value = float(recorded)
    if value >= _FILETIME_FLOOR:
        return value / _FILETIME_TICKS_PER_SECOND - _FILETIME_EPOCH_OFFSET_SECONDS
    if value >= _EPOCH_MILLIS_FLOOR:
        return value / 1000.0
    if value >= _EPOCH_SECONDS_FLOOR:
        return value
    return None


def probe_for(proc_dir: Path | None = None) -> ProcessProbe:
    """Return the process probe for this platform, resolved once for every caller.

    ``proc_dir`` is the long-standing test seam: a directory of synthetic
    ``<pid>/stat`` files standing in for procfs. The rule is the one the pid-liveness
    check has always applied -- read procfs when the directory is really there, and
    psutil otherwise -- so a host without procfs and an injected path that does not
    exist both resolve to psutil.
    """
    root = proc_dir if proc_dir is not None else _procfs_root()
    if root.is_dir():
        return ProcfsProbe(root)
    return PsutilProbe()
