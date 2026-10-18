"""A stopped remote session must leave the human a way back to the agent and a way out (AI-CLI-tozg).

The reported path: the agent's credentials expire, ``/login`` forces it to exit, the
session relaunches it, and the relaunch exits at once because nothing is logged in
yet. The launch loop correctly stops on a run that short, and on a remote session it
hands the pane to a recovery shell. That shell used to say only "Exit shell to close
tmux session.", and ``exit`` from it tore the whole session down, so the one thing
the human needed after logging in again, the agent back in this pane, had no route.

These tests run the whole generated remote session in a real tmux pane, as ``ai c``
starts it: the stable script, its supervisor, the promoted child body and the
recovery shell. Only ``claude`` and ``ai`` are stand-ins. The stand-in agent exits
immediately until a "logged in" marker exists, then stays up like a real session.
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

from ai_cli.session_script import get_engine_script

_TMUX_RUNNABLE, _TMUX_SKIP_REASON = tmux_runnable()

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")

_OBSERVE_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))
# The loop stops on its first quick exit, or, when child startup takes longer than
# three seconds, on the breaker's third consecutive exit. Measured on an idle macOS
# host, each child body took 16-20 s to reach the agent, so three relaunches under a
# parallel suite need well over the usual budget.
_STOP_SECONDS = 5 * _OBSERVE_SECONDS
_STOP_LINE = "— stopping."
_RELAUNCH_COMMAND = "exit 78"


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
    yield sock
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    shutil.rmtree(sock_dir, ignore_errors=True)


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

    def type_line(self, text: str) -> None:
        subprocess.run(["tmux", "-S", self.sock, "send-keys", "-t", self.session, text, "Enter"], check=True)

    def wait_for_recovery_shell(self) -> None:
        # The banner prints before the shell starts reading; type only once a prompt
        # has had the chance to appear, or the keystrokes race the shell's startup.
        assert _wait_for(lambda: _STOP_LINE in self.pane(), _STOP_SECONDS), (
            f"the launch loop never stopped; pane: {self.pane()!r}"
        )
        # The banner is rendered after the stop line by its own process, so the stop
        # line alone does not mean the banner is on screen yet.
        assert _wait_for(lambda: "close this session" in self.pane(), _STOP_SECONDS), (
            f"the recovery banner never appeared; pane: {self.pane()!r}"
        )
        time.sleep(1)

    def release(self) -> None:
        for pid in _lines(self.launches):
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)


@pytest.mark.real_tmux
def test_given_a_stopped_remote_session_when_the_pane_is_handed_back_then_it_names_restart_and_close(
    tmux_socket, tmp_path, login_shell
):
    session = _RemoteSession(tmux_socket, tmp_path, "recovery-banner", login_shell)
    try:
        session.wait_for_recovery_shell()
        pane = session.pane()
        assert _RELAUNCH_COMMAND in pane, f"the recovery shell names no way to get the agent back; pane: {pane!r}"
        assert "close this session" in pane, f"the recovery shell no longer says how to leave; pane: {pane!r}"
    finally:
        session.release()


@pytest.mark.real_tmux
def test_given_the_recovery_shell_after_a_fresh_login_when_the_human_asks_to_restart_then_the_agent_relaunches(
    tmux_socket, tmp_path, login_shell
):
    session = _RemoteSession(tmux_socket, tmp_path, "recovery-restart", login_shell)
    try:
        session.wait_for_recovery_shell()
        before = len(_lines(session.launches))
        session.logged_in.touch()
        session.type_line(_RELAUNCH_COMMAND)
        assert _wait_for(lambda: len(_lines(session.launches)) > before, _STOP_SECONDS), (
            f"asking the recovery shell to restart never relaunched the agent; pane: {session.pane()!r}"
        )
        time.sleep(4)
        assert session.alive(), "the relaunched agent did not keep the session up"
        assert session.pane().count(_STOP_LINE) == 1, (
            f"the relaunched agent hit the quick-exit stop again; pane: {session.pane()!r}"
        )
    finally:
        session.release()


@pytest.mark.real_tmux
def test_given_the_recovery_shell_when_the_human_exits_then_the_tmux_session_closes(tmux_socket, tmp_path, login_shell):
    session = _RemoteSession(tmux_socket, tmp_path, "recovery-close", login_shell)
    try:
        session.wait_for_recovery_shell()
        session.type_line("exit")
        assert _wait_for(lambda: not session.alive()), (
            f"exiting the recovery shell left the human in the pane; pane: {session.pane()!r}"
        )
    finally:
        session.release()
