"""SIGTERM and SIGHUP to a remote session's launcher still clean up (AI-CLI-a1vv).

With no handler, either signal ended Python inside the ssh call: the runner's
``finally`` never ran, so the pane kept the remote's mouse modes, the session's
cleanup command never ran and no outcome was recorded. The naive fix, raising
from a handler, is worse: ``subprocess.call`` then SIGKILLs ssh, which never
gets to take the terminal out of raw mode.

Each test runs the real runner in a child Python on a real pty, with a stand-in
ssh that does what ssh does to a terminal: it arms SGR mouse tracking, puts the
tty in raw mode, and restores the tty when it is sent SIGTERM or SIGHUP.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from ai_cli.transport import _TERMINAL_RESTORE

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and a pty")

_STAND_IN_SSH = """\
#!/usr/bin/env bash
saved=$(stty -g)
restore() {{ stty "$saved"; exit 143; }}
trap restore TERM HUP
printf '\\033[?1000h\\033[?1006h'
stty raw -echo
touch {ready}
while :; do sleep 0.1; done
"""

_RUNNER = """\
import sys
from pathlib import Path
from ai_cli.transport import run_ssh_with_reconnect
run_ssh_with_reconnect(
    [sys.argv[1]],
    ["touch", sys.argv[2]],
    max_attempts=1,
    outcome_path=Path(sys.argv[3]),
)
"""


def _wait_for(path: Path, deadline: float) -> None:
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.05)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["sigterm", "sighup"])
def test_given_a_remote_session_under_ssh_when_the_launcher_is_signalled_then_the_terminal_cleanup_and_outcome_survive(
    tmp_path, signum
):
    import pty
    import termios

    ready = tmp_path / "ssh-ready"
    cleaned = tmp_path / "cleanup-ran"
    outcome = tmp_path / "outcome.json"
    ssh = tmp_path / "ssh"
    ssh.write_text(_STAND_IN_SSH.format(ready=ready))
    ssh.chmod(0o755)

    leader, follower = pty.openpty()
    written: list[bytes] = []
    since_signal: list[bytes] = []
    signalled = threading.Event()

    def drain() -> None:
        # Drain throughout: macOS drops unread pty output once the follower closes.
        while True:
            try:
                chunk = os.read(leader, 65536)
            except OSError:
                return
            if not chunk:
                return
            written.append(chunk)
            if signalled.is_set():
                since_signal.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _RUNNER, str(ssh), str(cleaned), str(outcome)],
            stdin=follower,
            stdout=follower,
            stderr=follower,
            start_new_session=True,
        )
        _wait_for(ready, time.monotonic() + 60)
        signalled.set()
        proc.send_signal(signum)
        proc.wait(timeout=60)

        attrs = termios.tcgetattr(follower)
        assert attrs[3] & termios.ICANON, "the terminal was left out of canonical mode"
        assert attrs[3] & termios.ECHO, "the terminal was left with echo off"
        assert attrs[1] & termios.OPOST, "the terminal was left without output post-processing"
        assert cleaned.exists(), "the session's cleanup command never ran"
        assert json.loads(outcome.read_text())["verdict"] == "terminated"
        assert proc.returncode == 128 + signum
    finally:
        if proc is not None:
            # The runner and its stand-in ssh share the session started above; a
            # failing run leaves the ssh behind holding the pty, so end the group.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        os.close(follower)
        reader.join(timeout=10)
        os.close(leader)

    assert _TERMINAL_RESTORE.encode() in b"".join(since_signal), "the mouse modes were never switched off"


def test_given_the_runner_waiting_between_attempts_when_it_is_signalled_then_it_unwinds_through_cleanup(tmp_path):
    cleaned = tmp_path / "cleanup-ran"
    outcome = tmp_path / "outcome.json"
    waiting = tmp_path / "waiting"
    runner = textwrap.dedent(
        """\
        import sys, time
        from pathlib import Path
        from ai_cli import transport

        real_sleep = time.sleep

        def backoff(seconds):
            Path(sys.argv[3]).touch()
            real_sleep(60)

        transport.time.sleep = backoff
        transport.run_ssh_with_reconnect(
            ["false"], ["touch", sys.argv[1]], max_attempts=5, outcome_path=Path(sys.argv[2])
        )
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", runner, str(cleaned), str(outcome), str(waiting)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for(waiting, time.monotonic() + 60)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=60)

    assert cleaned.exists()
    assert json.loads(outcome.read_text())["verdict"] == "terminated"
    assert proc.returncode == 128 + signal.SIGTERM
