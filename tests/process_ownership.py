"""Shared process-ownership helpers: a test owns every process it starts.

A test that spawns a real process must be structurally unable to orphan it, and
``finally:`` is not enough on its own — it does not run when the run is killed,
when a suite-level timeout fires, or when an xdist worker dies.  So ownership
here is two independent mechanisms, and each one holds when the other does not:

* **A process group of its own.**  :func:`spawn_owned` detaches the child so the
  whole tree it goes on to create can be signalled as one unit.  Killing only the
  direct child orphans its grandchildren, which is how a single unowned spawn
  turns into processes that outlive the run.
* **A bounded lifetime.**  A child spawned through :func:`spawn_owned_python`
  with :data:`OWNED_PROCESS_LIFETIME` exits on its own, so even a teardown that
  never runs at all cannot leave it behind for longer than that.

Never a pattern-matched ``pkill``: it would also match processes this suite does
not own, including the developer's.

Platform behaviour, because the two are genuinely different and a POSIX-only
implementation of this takes the Windows jobs red:

* **POSIX** — ``start_new_session=True`` makes the child a session and process
  group leader; :func:`reap` signals that group with ``SIGTERM`` then ``SIGKILL``.
* **Windows** — ``start_new_session`` is ignored there, so the group comes from
  ``CREATE_NEW_PROCESS_GROUP`` instead, and there is no ``killpg``: ``os.killpg``,
  ``os.getpgid``, ``os.getpgrp`` and ``signal.SIGKILL`` are all POSIX-only and
  referencing any of them unguarded raises ``AttributeError``.  :func:`reap`
  walks the child's descendants with ``psutil`` and kills each, which is the
  closest equivalent teardown available through the handle alone.

Not usable for every spawn.  A test whose subject IS the process group — one that
asserts a child shares the runner's group, or whose payload calls ``os.setsid()``
itself — must keep its own spawn, and says so at the call site.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import psutil

#: Seconds a disposable spawned child sleeps for before exiting by itself.
#: Long enough to outlive any assertion that needs it alive, short enough that a
#: teardown which never runs cannot leave it around for a meaningful time.
OWNED_PROCESS_LIFETIME = 30

#: How long to wait for a signalled process to actually go, per escalation step.
_REAP_TIMEOUT = 5


def group_spawn_kwargs() -> dict[str, Any]:
    """The ``Popen`` keyword arguments that put the child in a group of its own."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def spawn_owned(command: list[str], **kwargs: Any) -> subprocess.Popen:
    """``Popen`` *command* in its own process group, so :func:`reap` can end it.

    Callers may pass any other ``Popen`` keyword argument.  They may not override
    the group arguments: that is the whole point of the helper, and a silent
    override would leave the child in the runner's group where a group signal
    reaches the test runner itself.
    """
    collisions = sorted(set(kwargs) & set(group_spawn_kwargs()))
    if collisions:
        raise TypeError(f"spawn_owned owns the process group; do not pass {', '.join(collisions)}")
    return subprocess.Popen(command, **group_spawn_kwargs(), **kwargs)


def spawn_owned_python(*code: str, **kwargs: Any) -> subprocess.Popen:
    """:func:`spawn_owned` for ``python -c``, with output discarded by default."""
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    return spawn_owned([sys.executable, "-c", *code], **kwargs)


def spawn_owned_sleeper(seconds: int = OWNED_PROCESS_LIFETIME, *extra_argv: str) -> subprocess.Popen:
    """A disposable owned child that sleeps *seconds* and then exits by itself.

    *extra_argv* lands in the child's ``sys.argv``, for tests that classify a
    process by its command line rather than by what it does.
    """
    return spawn_owned_python(f"import time; time.sleep({seconds})", *extra_argv)


def _reap_windows(proc: subprocess.Popen) -> None:
    """Kill the child and every descendant, then ``wait()`` so it is not a zombie.

    ``psutil`` rather than ``taskkill``: the descendant sweep must not depend on
    ``subprocess`` itself, which many of these tests patch with a ``MagicMock``.
    A patched-out teardown does not fail — it silently reaps nothing.
    """
    victims: list[psutil.Process] = []
    with contextlib.suppress(psutil.Error):
        parent = psutil.Process(proc.pid)
        victims = [*parent.children(recursive=True), parent]
    for victim in victims:
        with contextlib.suppress(psutil.Error):
            victim.kill()
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_REAP_TIMEOUT)


def reap(proc: subprocess.Popen) -> None:
    """Reap the process GROUP: ``SIGTERM``, a bounded wait, then ``SIGKILL``.

    The group rather than the child, because killing only the direct child
    orphans any grandchildren it started.  Always ends in a ``wait()`` so the
    child is not left a zombie — a zombie keeps its ``/proc/<pid>`` entry and
    would therefore still read as *live*.

    Safe to call more than once, and on a process that has already exited.
    """
    if sys.platform == "win32":
        _reap_windows(proc)
        return

    try:
        group = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        group = None
    # Refuse to signal our own group even if the spawn somehow did not detach:
    # that would take down the test runner rather than the child.
    if group == os.getpgrp():
        group = None

    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            break
        try:
            if group is not None:
                os.killpg(group, sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


@contextlib.contextmanager
def owned_process(command: list[str], **kwargs: Any) -> Iterator[subprocess.Popen]:
    """Spawn *command* owned, hand it to the caller, and reap its group after.

    The reap runs whether the body passed, failed or raised, so a cleanup can
    never depend on the assertions above it having succeeded.
    """
    proc = spawn_owned(command, **kwargs)
    try:
        yield proc
    finally:
        reap(proc)


@contextlib.contextmanager
def owned_sleeper(seconds: int = OWNED_PROCESS_LIFETIME, *extra_argv: str) -> Iterator[subprocess.Popen]:
    """:func:`owned_process` for the common case: a disposable sleeping child."""
    proc = spawn_owned_sleeper(seconds, *extra_argv)
    try:
        yield proc
    finally:
        reap(proc)
