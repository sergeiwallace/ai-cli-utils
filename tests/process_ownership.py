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
import select
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import psutil

# Teardown must survive test-level subprocess mocks.
_REAL_POPEN = subprocess.Popen
_TMUX_BINARY = shutil.which("tmux")

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

    Returns only once that command line is readable. On Linux ``Popen`` returns
    when ``execve`` passes its point of no return, before the kernel has set the
    new image's argument range, so ``/proc/<pid>/cmdline`` can still read empty.
    """
    proc = spawn_owned_python(f"import time; time.sleep({seconds})", *extra_argv)
    deadline = monotonic() + _REAP_TIMEOUT
    while monotonic() < deadline:
        try:
            if psutil.Process(proc.pid).cmdline():
                break
        except psutil.Error:
            break
        sleep(0.01)
    return proc


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
    # Bounded: a child that survives SIGKILL (stuck in an uninterruptible write)
    # must fail the test with a traceback, not hold the job to its time cap.
    proc.wait(timeout=_REAP_TIMEOUT)


def reap_tree(proc: subprocess.Popen) -> None:
    """Kill *proc*, every descendant it has now, and each descendant's process group.

    For a child whose own descendants leave its group: a job-control shell, or a
    supervisor whose child calls ``setpgrp()``. :func:`reap` signals only the
    root's group, so it misses those, and killing only the root reparents them
    to init, where nothing in this suite can find them again. That is how a
    timed-out supervisor test left its parked child running for days. So the tree
    is read BEFORE anything dies, while the descendants are still reachable
    through their parent, and each one's group is signalled as well, which also
    catches a member that has already been reparented.

    Never this process's own group, which would take down the test runner.
    Safe on a process that has already exited, and always ends in a bounded
    ``wait()``.
    """
    victims: list[psutil.Process] = []
    with contextlib.suppress(psutil.Error):
        root = psutil.Process(proc.pid)
        root.suspend()  # a stopped root cannot start a child the snapshot misses
        victims = [*root.children(recursive=True), root]
    groups: set[int] = set()
    if sys.platform != "win32":
        for victim in victims:
            with contextlib.suppress(OSError):
                groups.add(os.getpgid(victim.pid))
        groups.discard(os.getpgrp())
    for victim in victims:
        with contextlib.suppress(psutil.Error):
            victim.kill()
    for group in groups:
        with contextlib.suppress(OSError):
            os.killpg(group, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait(timeout=_REAP_TIMEOUT)


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


@contextlib.contextmanager
def watch_tmux_directory(directory: Path) -> Iterator[None]:
    """Own servers in a freshly created private socket directory, even after SIGKILL.

    The detached watcher sees EOF when this worker exits or closes its pipe. It
    remembers descendants while the server runs, including setsid heartbeat
    tickers which a process-group reap cannot reach. Never use a shared directory.
    """
    assert directory.is_dir() and not directory.is_symlink() and not list(directory.iterdir()), (
        "watch only a new, empty private directory"
    )
    if os.name != "posix":
        # Native Windows cannot host these POSIX tmux trees.
        try:
            yield
        finally:
            shutil.rmtree(directory)
        return
    watcher = _REAL_POPEN(
        [sys.executable, str(Path(__file__).resolve()), str(directory), _TMUX_BINARY or "tmux"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        start_new_session=True,
        cwd=directory,
    )
    assert watcher.stdin is not None and watcher.stdout is not None
    try:
        assert watcher.stdout.readline() == b"ready\n", "tmux cleanup watcher did not start"
        yield
    finally:
        watcher.stdin.close()
        watcher.stdout.close()
        assert watcher.wait(timeout=30) == 0, "tmux cleanup watcher failed"
        assert not directory.exists(), "tmux cleanup left its socket directory behind"


def _watch_tmux_directory(directory: Path, tmux: str) -> None:
    """The watcher owns only servers reached through sockets in its private dir."""
    servers: dict[Path, psutil.Process] = {}
    owned: set[psutil.Process] = set()

    def snapshot() -> None:
        for socket in directory.rglob("*"):
            server = servers.get(socket)
            if server is not None:
                with contextlib.suppress(psutil.NoSuchProcess):
                    if server.is_running() and server.status() != psutil.STATUS_ZOMBIE:
                        continue
                del servers[socket]
            if socket.is_socket() and not socket.is_symlink() and socket not in servers:
                probe = subprocess.run(
                    [tmux, "-S", str(socket), "display-message", "-p", "#{pid}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if probe.returncode == 0 and probe.stdout.strip().isdigit():
                    with contextlib.suppress(psutil.NoSuchProcess):
                        server = psutil.Process(int(probe.stdout))
                        assert server.name().startswith("tmux"), "socket did not identify a tmux server"
                        servers[socket] = server
        for server in servers.values():
            with contextlib.suppress(psutil.NoSuchProcess):
                owned.update([server, *server.children(recursive=True)])

    print("ready", flush=True)
    while not select.select([sys.stdin], [], [], 0.05)[0]:
        snapshot()
    snapshot()
    # Stop parents before taking a final descendant snapshot so none can spawn
    # another child during cleanup. psutil pins identity with PID + creation time.
    # A process this user may not inspect or signal (macOS /bin/ps is setuid root, and the
    # session's suspend watchdog polls it) raises AccessDenied. It must not abort the walk:
    # that left the already-stopped tree alive and the directory behind (AI-CLI-57fp).
    pending = list(owned)
    while pending:
        process = pending.pop()
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.ppid()
            process.cwd()
            process.suspend()
            for child in process.children(recursive=True):
                if child not in owned:
                    owned.add(child)
                    pending.append(child)
    for process in owned:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.kill()
    deadline = monotonic() + _REAP_TIMEOUT
    while True:
        alive = []
        for process in owned:
            with contextlib.suppress(psutil.NoSuchProcess):
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(process)
        if not alive:
            break
        assert monotonic() < deadline, "tmux descendants survived cleanup"
        sleep(0.01)
    # This exact directory was created empty by the caller and checked before use.
    if directory.exists():
        assert directory.is_dir() and not directory.is_symlink()
        shutil.rmtree(directory)


if __name__ == "__main__":
    _watch_tmux_directory(Path(sys.argv[1]), sys.argv[2])
