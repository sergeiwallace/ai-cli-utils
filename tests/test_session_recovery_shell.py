"""A stopped remote session ends, and with it the tmux session its client is attached to.

The reported path: the agent's credentials expire, ``/login`` forces it to exit, the
session relaunches it, and the relaunch exits at once because nothing is logged in
yet. The launch loop correctly stops on a run that short. A remote session used to
hand its pane to a recovery shell at that point, which left the human inside the
remote tmux session instead of back at the shell that ran ``ai c -R``. The session now
ends the way a local one does: the stop line names the retry, the tmux session closes,
and the client returns to the launching shell, where ``ai c -R`` relaunches the agent
once the login is fixed.

These tests run the whole generated remote session in a real tmux pane, as ``ai c``
starts it: the stable script, its supervisor and the promoted child body. Only
``claude`` and ``ai`` are stand-ins. The stand-in agent exits immediately, as one with
no valid login does.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import tmux_runnable
from legacy_run_state import legacy_run_state_helpers, legacy_state_functions  # noqa: F401  (autouse fixture)
from process_ownership import watch_tmux_directory
from process_wait import process_wait_seconds

from ai_cli.session_script import get_engine_script

_TMUX_RUNNABLE, _TMUX_SKIP_REASON = tmux_runnable()

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")

_OBSERVE_SECONDS = process_wait_seconds()
# The loop stops on its first quick exit, or, when child startup takes longer than
# three seconds, on the breaker's third consecutive exit. Measured on an idle macOS
# host, each child body took 16-20 s to reach the agent, so three relaunches under a
# parallel suite need well over the usual budget.
_STOP_SECONDS = 5 * _OBSERVE_SECONDS
_STOP_LINE = "— stopping."


def _wait_for(predicate, timeout: float = _OBSERVE_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest.fixture(params=["bash", "zsh"])
def login_shell(request):
    """The human's ``$SHELL``, which the recovery shell runs as. zsh is the usual one."""
    found = shutil.which(request.param)
    if found is None:
        pytest.skip(f"{request.param} is not installed")
    return found


@pytest.fixture
def tmux_socket():
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-recovery-")
    sock = f"{sock_dir}/tmux.sock"
    with watch_tmux_directory(Path(sock_dir)):
        yield sock


class _RemoteSession:
    """A remote-mode session in a real tmux pane whose agent needs a fresh login."""

    def __init__(self, sock: str, tmp_path: Path, session: str, login_shell: str) -> None:
        self.sock = sock
        self.session = session
        self.launches = tmp_path / "launches"
        self.logged_in = tmp_path / "logged-in"
        fake_bin = tmp_path / "fakes"
        fake_bin.mkdir()
        sessions = tmp_path / "state" / "ai-cli-utils" / "sessions"
        sessions.mkdir(parents=True)
        script = sessions / f"{session}.sh"
        (fake_bin / "ai").write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = sessions ] && [ "$2" = _sequence ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
            'if [ "$1" = internal ] && [ "$2" = get-version ]; then printf "%s\\n" unknown; fi\nexit 0\n'
        )
        (fake_bin / "claude").write_text(
            f"#!{sys.executable}\nimport os, sys, time\n"
            f"open({str(self.launches)!r}, 'a').write(f'{{os.getpid()}}\\n')\n"
            f"if not os.path.exists({str(self.logged_in)!r}):\n"
            "    print('credentials expired, please log in again')\n"
            "    sys.exit(1)\n"
            "time.sleep(300)\n"
        )
        (fake_bin / "python3").symlink_to(sys.executable)
        for fake in fake_bin.iterdir():
            if not fake.is_symlink():
                fake.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        # A home with no zsh startup file makes zsh (where its newuser module is built
        # in, as on Debian and Ubuntu) run the full-screen newuser wizard, which clears
        # the pane before the stop line and banner can be read.
        (home / ".zshrc").write_text("# test home\n")
        with legacy_state_functions():
            script.write_text(
                get_engine_script(
                    "c", "recovery", session, "c-recovery-", "recovery", worktree_dir=str(tmp_path), is_remote=True
                )
            )
        script.chmod(0o700)
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("DIRENV_")}
        created = subprocess.run(
            ["tmux", "-S", sock, "new-session", "-d", "-s", session, "-x", "120", "-y", "30", "bash", str(script)],
            capture_output=True,
            text=True,
            env={
                **inherited,
                "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", ""))),
                "XDG_STATE_HOME": str(tmp_path / "state"),
                "HOME": str(home),
                "ZDOTDIR": str(home),
                "SHELL": login_shell,
            },
            check=False,
        )
        assert created.returncode == 0, created.stderr
        self.output = tmp_path / "pane-output"
        self.output_done = tmp_path / "pane-output-done"
        pipe = f"cat > {shlex.quote(str(self.output))}; touch {shlex.quote(str(self.output_done))}"
        subprocess.run(["tmux", "-S", sock, "pipe-pane", "-O", "-t", f"={session}:", pipe], check=True)

    def pane(self) -> str:
        # -J joins a line the 120-column pane wrapped, so a long record still reads as one line.
        return subprocess.run(
            ["tmux", "-S", self.sock, "capture-pane", "-p", "-J", "-S", "-", "-t", self.session],
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    def alive(self) -> bool:
        probe = subprocess.run(
            ["tmux", "-S", self.sock, "has-session", "-t", self.session], capture_output=True, check=False
        )
        return probe.returncode == 0

    def release(self) -> None:
        for pid in _lines(self.launches):
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)


@pytest.mark.real_tmux
def test_given_a_stopped_remote_session_when_the_launch_loop_stops_then_its_tmux_session_closes(
    tmux_socket, tmp_path, login_shell
):
    session = _RemoteSession(tmux_socket, tmp_path, "recovery-stop", login_shell)
    try:
        assert _wait_for(lambda: not session.alive(), _STOP_SECONDS), (
            f"the stopped session stayed up in tmux; pane: {session.pane()!r}"
        )
        assert _wait_for(session.output_done.exists)
        shown = session.output.read_text(encoding="utf-8", errors="replace")
        assert _STOP_LINE in shown, f"the launch loop never said it stopped; pane: {shown!r}"
        assert "Run 'ai c' to retry." in shown, f"the stop line names no way back to the agent; pane: {shown!r}"
        assert "close this session" not in shown, f"a recovery shell was offered in the pane; pane: {shown!r}"
    finally:
        session.release()
