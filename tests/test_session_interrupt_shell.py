"""Interrupt the full generated wrapper through an isolated tmux pane."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import TEST_BOOT_ID, tmux_runnable
from process_ownership import watch_tmux_directory
from process_wait import scaled

from ai_cli import session, session_commands, session_lifecycle_script, session_migrate
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_script import get_engine_script

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(os.name != "posix", reason="session wrappers require POSIX terminals"),
    pytest.mark.timeout(120),
]

_REAL_TMUX_NAMES = session._tmux_session_names


def _wait(predicate):
    deadline = time.monotonic() + scaled(25)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(params=["bash", "zsh"])
def pane_session(request, tmp_path, monkeypatch):
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} is unavailable")
    runnable, reason = tmux_runnable()
    if not runnable:
        pytest.skip(reason)
    # Pin the same test boot identity in lifecycle subprocesses as in pytest.
    lifecycle_command = session_lifecycle_script.lifecycle_command

    def fixed_boot_command(path):
        command = lifecycle_command(path)
        command[-1] = command[-1].replace(
            "from ai_cli.main import cli",
            f"registry.current_boot_id = lambda: {TEST_BOOT_ID!r}; from ai_cli.main import cli",
        )
        return command

    monkeypatch.setattr(session_lifecycle_script, "lifecycle_command", fixed_boot_command)
    monkeypatch.setattr("ai_cli.session_script.resolve_session_shell", lambda: shell)
    launches = tmp_path / "launches"
    action = tmp_path / "action"
    handoff_pause = tmp_path / "handoff-pause"
    handoff_ready = tmp_path / "handoff-ready"
    handoff_release = tmp_path / "handoff-release"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # All project/engine services are fakes; terminal, signals, wrapper, lifecycle
    # persistence and tmux remain real. No inherited login files or direnv run.
    (binaries / "ai").write_text(
        '#!/bin/sh\nif [ "$2" = _begin ]; then echo "$TEST_GENERATION"; fi\n'
        'if [ "$7" = "Auto-restart stopped. Returning to an interactive shell." ] && '
        f"[ -f {shlex.quote(str(handoff_pause))} ]; then\n"
        f"  touch {shlex.quote(str(handoff_ready))}\n"
        f"  while [ ! -f {shlex.quote(str(handoff_release))} ]; do sleep 0.02; done\n"
        "fi\nexit 0\n"
    )
    (binaries / "direnv").write_text("#!/bin/sh\nexit 0\n")
    agent = (
        f"#!{sys.executable}\n"
        "import os, signal, sys, time, tty\nfrom pathlib import Path\n"
        "import json\nfrom ai_cli.process_probe import probe_for\n"
        "records = Path.home() / '.claude' / 'sessions'\nrecords.mkdir(parents=True, exist_ok=True)\n"
        "pid = os.getpid()\n"
        "(records / f'{pid}.json').write_text(json.dumps({'pid': pid, 'name': 'session-1', "
        "'sessionId': 'test-session', 'cwd': os.getcwd(), "
        "'procStart': probe_for().capture_identity(pid).marker}))\n"
        f"action = Path({str(action)!r})\n"
        "mode = os.environ['TEST_AGENT_MODE']\n"
        "signal.signal(signal.SIGINT, lambda *_: sys.exit(130))\n"
        "if mode == 'ignore': signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "if mode.startswith('raw'): tty.setraw(0)\n"
        f"with open({str(launches)!r}, 'a') as out: out.write(str(os.getpid()) + '\\n')\n"
        "if mode.startswith('raw'):\n"
        "    while b'\\x03' not in os.read(0, 64): pass\n"
        "    sys.exit(0 if mode == 'raw-clean' else 130)\n"
        "while not action.exists(): time.sleep(0.02)\n"
        "command = action.read_text()\naction.unlink()\n"
        "if command == 'crash': os.kill(os.getpid(), signal.SIGKILL)\n"
        "sys.exit(int(command))\n"
    )
    for name in ("claude", "gemini"):
        (binaries / name).write_text(agent)
    (binaries / "python3").symlink_to(sys.executable)
    for binary in binaries.iterdir():
        if not binary.is_symlink():
            binary.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("PS1='test-shell> '\n")
    (home / ".bashrc").write_text("PS1='test-shell> '\n")
    sock_dir = Path(tempfile.mkdtemp(prefix="interrupt-"))
    socket = str(sock_dir / "tmux.sock")

    def tmux(*args):
        return subprocess.run(["tmux", "-S", socket, *args], capture_output=True, text=True, check=False, timeout=10)

    class Pane:
        name = "c-session-1"

        def start(self, *, engine="c", remote=False, mode="signal", policy="auto"):
            state.register(self.name, recipe={"argv": ["ai", engine, "1"], "cwd": str(tmp_path)}, host="host-a")
            generation = state.begin(self.name)
            self.set_policy(policy)
            script = tmp_path / "session.sh"
            script.write_text(
                get_engine_script(
                    engine,
                    "session-1",
                    self.name,
                    f"{engine}-session-",
                    "myproject",
                    worktree_dir=str(tmp_path),
                    is_remote=remote,
                )
            )
            env = {key: value for key, value in os.environ.items() if not key.startswith("DIRENV_")}
            env.update(
                PATH=str(binaries) + os.pathsep + env["PATH"],
                HOME=str(home),
                ZDOTDIR=str(home),
                SHELL=shell,
                AI_HOST="host-a",
                TEST_GENERATION=generation,
                TEST_AGENT_MODE=mode,
            )
            result = subprocess.run(
                [
                    "tmux",
                    "-S",
                    socket,
                    "-f",
                    os.devnull,
                    "new-session",
                    "-d",
                    "-s",
                    self.name,
                    "-x",
                    "120",
                    "-y",
                    "30",
                    shell,
                    str(script),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert _wait(lambda: self.count() > 0), self.capture()

        def set_policy(self, policy):
            def change(doc):
                doc["sessions"][0]["relaunch"] = policy
                return True

            registry._mutate(change)

        def count(self):
            return len(launches.read_text().splitlines()) if launches.exists() else 0

        def capture(self):
            return tmux("capture-pane", "-p", "-J", "-S", "-", "-t", self.name).stdout

        def stop(self):
            monkeypatch.setenv("TMUX", f"{socket},0,0")
            monkeypatch.setattr(session, "_tmux_session_names", _REAL_TMUX_NAMES)
            return session_commands.stop_session("session-1", timeout=scaled(30), claude_home=home / ".claude")

        def alive(self):
            return tmux("has-session", "-t", f"={self.name}").returncode == 0

        def interrupt(self):
            assert tmux("send-keys", "-t", self.name, "C-c").returncode == 0

        def exit_agent(self, command):
            action.write_text(command)

        def interrupt_handoff(self):
            handoff_pause.touch()
            self.interrupt()
            assert _wait(handoff_ready.exists), self.capture()
            self.interrupt()
            handoff_release.touch()

        def assert_shell(self):
            assert _wait(lambda: "test-shell>" in self.capture()), self.capture()
            assert tmux("has-session", "-t", self.name).returncode == 0
            # Execute a command: an alive/dead pane or an echoed keystroke is not a shell.
            proof = tmp_path / "shell-proof"
            command = f"printf '%s' interactive > {shlex.quote(str(proof))}"
            assert tmux("send-keys", "-t", self.name, command, "Enter").returncode == 0
            assert _wait(proof.exists), self.capture()
            assert proof.read_text() == "interactive"
            before = self.count()
            self.interrupt()
            time.sleep(0.2)
            assert self.count() == before, "Ctrl+C at the returned shell restarted the agent"
            assert tmux("has-session", "-t", self.name).returncode == 0
            record = registry.load_registry()["sessions"][0]
            assert record["run_state"] == "exited", json.dumps(record)

    with watch_tmux_directory(sock_dir):
        try:
            yield Pane()
        finally:
            tmux("kill-server")


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("engine", ["c", "g"])
@pytest.mark.parametrize("mode", ["signal", "raw", "raw-clean"])
def test_given_running_engine_when_terminal_interrupt_exits_then_same_pane_returns_to_shell(
    pane_session, engine, remote, mode
):
    pane_session.start(engine=engine, remote=remote, mode=mode)
    # A clean TUI exit after a healthy run must stop too; in raw mode the
    # wrapper receives no SIGINT and cannot rely on its own interrupt marker.
    if mode == "raw-clean":
        time.sleep(3.2)
    pane_session.interrupt()
    pane_session.assert_shell()
    assert pane_session.count() == 1, "an intentional interrupt relaunched the engine"


def test_given_engine_ignoring_interrupt_when_double_ctrl_c_then_same_pane_returns_to_shell(pane_session):
    pane_session.start(mode="ignore")
    pane_session.interrupt()
    assert _wait(lambda: "Ctrl+C again" in pane_session.capture()), pane_session.capture()
    pane_session.interrupt()
    pane_session.assert_shell()
    assert pane_session.count() == 1


def test_given_shell_handoff_when_terminal_is_interrupted_again_then_same_pane_returns_to_shell(pane_session):
    pane_session.start()
    pane_session.interrupt_handoff()
    pane_session.assert_shell()
    assert pane_session.count() == 1


@pytest.mark.parametrize("command", ["1", "crash"])
def test_given_auto_policy_when_engine_crashes_then_wrapper_restarts(pane_session, command):
    pane_session.start()
    # Preserve the existing three-second quick-failure guard.
    time.sleep(3.2)
    pane_session.exit_agent(command)
    assert _wait(lambda: pane_session.count() == 2), pane_session.capture()


@pytest.mark.parametrize("remote", [False, True])
def test_given_policy_changed_to_never_when_engine_exits_then_same_pane_returns_to_shell(pane_session, remote):
    pane_session.start(remote=remote)
    pane_session.set_policy("never")
    time.sleep(3.2)
    pane_session.exit_agent("1")
    pane_session.assert_shell()
    assert pane_session.count() == 1


@pytest.mark.parametrize("remote", [False, True])
def test_given_running_tmux_session_when_command_stops_then_pane_closes_and_migration_preflight_passes(
    pane_session, remote, tmp_path
):
    pane_session.start(remote=remote)
    time.sleep(3.2)
    report = pane_session.stop()
    assert report.signalled, "stop did not signal the running agent"
    assert report.stopped, f"stop left occupants: {report.remaining}"
    assert not pane_session.alive(), "stop left an interactive shell in tmux"
    assert pane_session.count() == 1, "stop relaunched the agent"
    session_migrate._refuse_live(
        "session-1",
        "session-2",
        tmp_path,
        tmp_path / "new",
        tmp_path / "home" / ".claude",
        None,
        session_migrate._tmux_names(),
    )
