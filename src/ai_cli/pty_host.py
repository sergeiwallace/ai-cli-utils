"""Run a bare session's agent on a pty the launcher owns, so a prompt can be submitted into it.

Depends on: config.py, output.py, transport.py.

POSIX-only, and imported only on the pty path. ``compact_transport.resolve()`` never
picks the pty on a platform without ``os.openpty`` (Windows), so a bare launch there
keeps replacing itself with the agent through ``execvp``. A Windows pseudo-console
would need a dependency this package does not carry (``AI-CLI-ysht``).

A bare launch used to ``execvp`` the agent, which then owned the operator's terminal
and could never be typed into by anyone else: ``TIOCSTI`` is privileged, and a running
process's controlling terminal cannot be moved. So the launcher stays alive instead,
starts the agent on the slave side of a pty, and relays bytes between the operator's
terminal and the master. Anything written to the master is read by the agent as typed
input, and the launcher exposes that write path as a per-session FIFO
(:func:`submit_fifo_path`, also passed to the agent as ``AI_CLI_SUBMIT_FIFO``). That
is how the auto-compact prompt reaches a session that tmux does not host.

SCOPE. The pty carries the submission; it does not host the session's UI. The
operator's terminal stays the terminal: it is put in raw mode and every byte is copied
through unchanged, both ways. There is no screen model, no scrollback, no
detach/reattach and no multiplexing, and none should be added here: they are what tmux
is for, and tmux stays the preferred transport. ``TIOCSTI`` is never used.

* Resize: ``SIGWINCH`` on the launcher copies the operator's window size onto the pty
  with ``TIOCSWINSZ``, and the kernel delivers ``SIGWINCH`` to the agent.
* Ctrl-C: the operator's terminal is raw, so it raises no ``SIGINT``. The ``0x03`` byte
  goes to the pty, whose line discipline (as the agent set it) raises ``SIGINT`` in the
  agent or hands the agent the byte. The launcher itself is never interrupted.
* ``SIGTERM``/``SIGHUP``: forwarded to the agent by
  :func:`transport._terminate_after_child`; the relay keeps running until the agent
  exits, then the terminal is restored and the launcher exits ``128 + signum``.
"""

import contextlib
import errno
import fcntl
import os
import select
import signal
import stat
import subprocess
import sys
import termios
import threading
import tty
from pathlib import Path
from typing import Any

from . import output as _out
from .config import get_xdg_state_home
from .transport import _terminate_after_child, restore_terminal

#: The variable that tells the agent (and its hooks) where its submission FIFO is.
SUBMIT_FIFO_ENV = "AI_CLI_SUBMIT_FIFO"

_CHUNK = 65536


def submit_fifo_path(session: str) -> Path:
    """Where a submitter writes text for *session* to read as typed input."""
    return get_xdg_state_home() / "pty" / f"{session}.in"


def _copy_winsize(src: int, dst: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.ioctl(dst, termios.TIOCSWINSZ, fcntl.ioctl(src, termios.TIOCGWINSZ, b"\0" * 8))


def _claim_controlling_terminal() -> None:
    # Runs in the child after setsid(): stdin is the pty slave, and claiming it is what
    # makes the agent's terminal the one the launcher can write into.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _make_fifo(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISFIFO(existing.st_mode):
            raise FileExistsError(errno.EEXIST, "not a FIFO, refusing to replace it", str(path))
        # A FIFO left by a launcher that died before its cleanup.
        path.unlink()
    os.mkfifo(path, 0o600)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _relay(master: int, stdin_fd: int, stdout_fd: int, fifo_fd: int, proc: subprocess.Popen) -> None:
    """Copy operator input and FIFO submissions to the agent, and its output back, until it exits."""
    wake_r, wake_w = os.pipe()
    for fd in (wake_r, wake_w):
        os.set_blocking(fd, False)
    on_main = threading.current_thread() is threading.main_thread()
    restore: list[tuple[signal.Signals, Any]] = []
    previous_wakeup = -1
    if on_main:
        # SIGWINCH and SIGCHLD need a Python handler for the wakeup fd to be written;
        # the work itself happens in the loop, not in the handler.
        for signum in (signal.SIGWINCH, signal.SIGCHLD):
            restore.append((signum, signal.signal(signum, lambda *_: None)))
        previous_wakeup = signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
    readers = [master, fifo_fd, wake_r, stdin_fd]
    output_open = True
    try:
        while True:
            readable, _, _ = select.select(readers, [], [], None if on_main else 0.1)
            if wake_r in readable:
                woken = os.read(wake_r, 512)
                if signal.SIGWINCH in woken:
                    _copy_winsize(stdin_fd, master)
            if stdin_fd in readable:
                try:
                    typed = os.read(stdin_fd, _CHUNK)
                except OSError:
                    # The operator's terminal hung up; its SIGHUP ends the session.
                    typed = b""
                if typed:
                    _write_all(master, typed)
                else:
                    readers.remove(stdin_fd)
            if fifo_fd in readable:
                with contextlib.suppress(BlockingIOError):
                    _write_all(master, os.read(fifo_fd, _CHUNK))
            if master in readable:
                try:
                    shown = os.read(master, _CHUNK)
                except OSError as exc:
                    # Linux reports EIO once the last slave descriptor closes; macOS reads EOF.
                    if exc.errno != errno.EIO:
                        raise
                    shown = b""
                if not shown:
                    return
                if output_open:
                    try:
                        _write_all(stdout_fd, shown)
                    except OSError:
                        # The operator's terminal went away (a SIGHUP is on its way). Keep
                        # draining the master so the agent never blocks writing to it.
                        output_open = False
            if proc.poll() is not None:
                # A process the agent started can still hold the slave open, so the
                # master may never read EOF: the agent's own exit ends the relay.
                os.set_blocking(master, False)
                with contextlib.suppress(OSError):
                    while output_open and (rest := os.read(master, _CHUNK)):
                        _write_all(stdout_fd, rest)
                return
    finally:
        if on_main:
            signal.set_wakeup_fd(previous_wakeup)
            for signum, handler in restore:
                signal.signal(signum, handler)
        os.close(wake_r)
        os.close(wake_w)


def host(file: str, args: list[str], *, session: str) -> int:
    """Run ``args`` on a launcher-owned pty and return its exit status.

    Raises ``FileNotFoundError`` before touching the terminal when *file* cannot be
    run, so the caller can fall back exactly as it does for an ``execvp``.
    """
    _out.flush_sinks()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    fifo = submit_fifo_path(session)
    master, slave = os.openpty()
    fds = [master]
    # Installed before the spawn, so a SIGTERM or SIGHUP at any point either unwinds
    # straight through the cleanup below or is forwarded to the agent.
    with _terminate_after_child() as raise_if_terminated:
        try:
            _copy_winsize(stdin_fd, slave)
            _make_fifo(fifo)
            try:
                proc = subprocess.Popen(
                    args,
                    executable=file,
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    start_new_session=True,
                    preexec_fn=_claim_controlling_terminal,
                    env={**os.environ, SUBMIT_FIFO_ENV: str(fifo)},
                )
            finally:
                os.close(slave)
            # The keeper write end means the reader never sees EOF between submitters.
            fifo_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fifo_fd)
            fds.append(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            saved = termios.tcgetattr(stdin_fd) if os.isatty(stdin_fd) else None
            try:
                if saved is not None:
                    tty.setraw(stdin_fd, termios.TCSANOW)
                _relay(master, stdin_fd, stdout_fd, fifo_fd, proc)
                returncode = proc.wait()
                raise_if_terminated()
            finally:
                if saved is not None:
                    with contextlib.suppress(termios.error, OSError):
                        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
                restore_terminal(sys.stdout)
        finally:
            # Closing the master also hangs up the agent's terminal, so an agent left
            # behind by an unexpected error gets SIGHUP rather than running on unseen.
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                fifo.unlink()
    return 128 - returncode if returncode < 0 else returncode
