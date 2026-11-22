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

from ai_cli import config, session, session_commands, session_lifecycle_script, session_migrate
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.codex_session import confirm, prepare
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
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # All project/engine services are fakes; terminal, signals, wrapper, lifecycle
    # persistence and tmux remain real. No inherited login files or direnv run.
    (binaries / "ai").write_text(
        '#!/bin/sh\nif [ "$2" = _begin ]; then echo "$TEST_GENERATION"; fi\n'
        f'if [ "$1" = internal ] && [ "$2" = codex-session-confirm ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        "exit 0\n"
    )
    (binaries / "direnv").write_text("#!/bin/sh\nexit 0\n")
    agent = (
        f"#!{sys.executable}\n"
        "import os, shlex, signal, subprocess, sys, time, tomllib, tty\nfrom pathlib import Path\n"
        "import json\nfrom ai_cli.process_probe import probe_for\n"
        "if Path(sys.argv[0]).name == 'codex':\n"
        "    if sys.argv[1:] == ['--version']:\n"
        "        print('codex-cli 0.169.0'); sys.exit(0)\n"
        "    args = sys.argv[1:]\n"
        "    config = tomllib.loads(args[args.index('-c') + 1])\n"
        "    command = config['hooks']['SessionStart'][0]['hooks'][0]['command']\n"
        "    event = {'hook_event_name': 'SessionStart', 'session_id': args[args.index('resume') + 1],\n"
        "             'cwd': os.getcwd(), 'transcript_path': os.environ['TEST_CODEX_ROLLOUT']}\n"
        "    subprocess.run(shlex.split(command), input=json.dumps(event), text=True, check=True)\n"
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
    for name in ("claude", "gemini", "codex"):
        (binaries / name).write_text(agent)
    (binaries / "python3").symlink_to(sys.executable)
    for binary in binaries.iterdir():
        if not binary.is_symlink():
            binary.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    prompts = tmp_path / "shell-prompts"
    prompt_stamp = f"printf '%s\\n' ready >> {shlex.quote(str(prompts))}"
    (home / ".zshrc").write_text(f"PS1='test-shell> '\nprecmd() {{ {prompt_stamp}; }}\n")
    (home / ".bashrc").write_text(f"PS1='test-shell> '\nPROMPT_COMMAND={shlex.quote(prompt_stamp)}\n")
    sock_dir = Path(tempfile.mkdtemp(prefix="interrupt-"))
    socket = str(sock_dir / "tmux.sock")

    def tmux(*args):
        return subprocess.run(["tmux", "-S", socket, *args], capture_output=True, text=True, check=False, timeout=10)

    class Pane:
        name = "c-session-1"

        def start(self, *, engine="c", remote=False, mode="signal", policy="auto", caller=False, quick_exit=False):
            if engine == "cx":
                # Give prepare real, synthetic confirmation and persistence evidence.
                # Version queries must not count as an interactive engine launch.
                native_home = tmp_path / "codex"
                monkeypatch.setenv("CODEX_HOME", str(native_home))
                record = config.get_xdg_state_home() / "sessions" / f"{self.name}.codex.json"
                prepare(record, tmp_path, self.name, fresh=True, route="local-tmux", client_version="0.169.0")
                thread = "11111111-2222-4333-8444-555555555555"
                rollout = native_home / "sessions" / "2026" / "11" / "20" / f"rollout-{thread}.jsonl"
                rollout.parent.mkdir(parents=True)
                rollout.write_text(
                    json.dumps({"type": "session_meta", "payload": {"id": thread, "cwd": str(tmp_path)}})
                )
                confirm(
                    record,
                    json.loads(record.read_text())["in_flight"]["token"],
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": thread,
                        "cwd": str(tmp_path),
                        "transcript_path": str(rollout),
                    },
                )
                doc = json.loads(record.read_text())
                doc["in_flight"]["writer"]["pid"] = 99999999
                record.write_text(json.dumps(doc))
                monkeypatch.setenv("TEST_CODEX_ROLLOUT", str(rollout))
            if quick_exit:
                date = binaries / "date"
                date.write_text(
                    '#!/bin/sh\nif [ "$1" = +%s ]; then echo 1000000000; '
                    f'else exec {shlex.quote(shutil.which("date"))} "$@"; fi\n'
                )
                date.chmod(0o755)
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
            self.output = tmp_path / "pane-output"
            self.output_done = tmp_path / "pane-output-done"
            pipe = f"cat > {shlex.quote(str(self.output))}; touch {shlex.quote(str(self.output_done))}"
            assert tmux("pipe-pane", "-O", "-t", f"={self.name}:", pipe).returncode == 0
            if caller:
                self.attach_caller(remote=remote)

        def attach_caller(self, *, remote):
            local_dir = tmp_path / "local"
            local_dir.mkdir()
            self.before = tmp_path / "before"
            self.after = tmp_path / "after"
            # The remote executable stands in for ssh: it attaches to the real
            # session, without contacting a host or replacing the calling shell.
            attach = [shutil.which("tmux"), "-S", socket, "attach-session", "-t", f"={self.name}"]
            fake_remote = binaries / "remote"
            fake_remote.write_text("#!/bin/sh\nunset TMUX\nexec " + shlex.join(attach) + "\n")
            fake_remote.chmod(0o755)
            runner = tmp_path / "attach.py"
            runner.write_text(
                "import os, sys\nos.environ.pop('TMUX', None)\n"
                + (
                    "from ai_cli.transport import run_ssh_with_reconnect\n"
                    f"sys.exit(run_ssh_with_reconnect({[str(fake_remote)]!r}, "
                    f"{[sys.executable, '-c', 'pass']!r}))\n"
                    if remote
                    else f"from ai_cli.main import _exec_handoff\n_exec_handoff({attach[0]!r}, {attach!r})\n"
                )
            )
            assert tmux("new-session", "-d", "-s", "caller", "-c", str(local_dir), shell, "-i").returncode == 0
            stamp = 'printf \'%s\\n%s\\n\' "$$" "$PWD" > '
            command = (
                stamp
                + shlex.quote(str(self.before))
                + "; "
                + shlex.join([sys.executable, str(runner)])
                + "; "
                + stamp
                + shlex.quote(str(self.after))
            )
            assert tmux("send-keys", "-t", "=caller:", command, "Enter").returncode == 0
            assert _wait(lambda: bool(tmux("list-clients", "-t", f"={self.name}").stdout)), self.capture()

        def assert_caller(self):
            assert _wait(lambda: self.after.exists() and self.after.read_text() == self.before.read_text()), (
                self.capture()
            )
            assert self.before.read_text() == self.after.read_text()
            assert self.after.read_text().splitlines()[1] == str(tmp_path / "local")
            assert tmux("has-session", "-t", "=caller").returncode == 0
            assert not self.alive(), "the session left a shell or a restarting agent in tmux"

            def prompt_count():
                return len(prompts.read_text().splitlines()) if prompts.exists() else 0

            def caller_screen():
                return tmux("capture-pane", "-p", "-t", "=caller:").stdout

            def at_drawn_prompt():
                lines = [line.rstrip() for line in caller_screen().splitlines() if line.strip()]
                return bool(lines) and lines[-1] == "test-shell>"

            assert _wait(lambda: prompt_count() >= 2)
            # The prompt hook runs before the line editor has taken over the terminal. A Ctrl+C
            # sent in that gap drew no new prompt under bash 5 on a Linux CI runner. The editor
            # draws the prompt itself once it is reading, so wait for that before the press.
            assert _wait(at_drawn_prompt), caller_screen()
            before_interrupt = prompt_count()
            assert tmux("send-keys", "-t", "=caller:", "C-c").returncode == 0
            # An interrupt can discard queued terminal input. Wait for the
            # shell to finish handling it before submitting the proof command.
            assert _wait(lambda: prompt_count() > before_interrupt)
            proof = tmp_path / "caller-proof"
            command = f"printf usable > {shlex.quote(str(proof))}"
            assert tmux("send-keys", "-t", "=caller:", command, "Enter").returncode == 0
            assert _wait(lambda: proof.exists() and proof.read_text() == "usable")
            assert proof.read_text() == "usable"
            assert _wait(self.output_done.exists)
            assert "KeyboardInterrupt" not in self.output.read_text()
            assert "Traceback" not in self.output.read_text()

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
            target = "=caller:" if hasattr(self, "before") else self.name
            assert tmux("send-keys", "-t", target, "C-c").returncode == 0

        def exit_agent(self, command):
            # The agent treats the presence of action as a complete command.
            pending = action.with_suffix(".tmp")
            pending.write_text(command)
            pending.replace(action)

        def type_line(self, command):
            assert tmux("send-keys", "-t", "=caller:", command, "Enter").returncode == 0

        def assert_shell(self):
            assert _wait(lambda: "test-shell>" in self.capture()), self.capture()
            assert tmux("has-session", "-t", self.name).returncode == 0
            # Execute a command: an alive/dead pane or an echoed keystroke is not a shell.
            proof = tmp_path / "shell-proof"
            pending = shlex.quote(str(proof.with_suffix(".tmp")))
            command = f"printf '%s' interactive > {pending} && mv {pending} {shlex.quote(str(proof))}"
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


@pytest.mark.parametrize("pane_session", ["bash"], indirect=True)
def test_given_waiting_agent_when_exit_command_is_written_then_only_complete_command_is_visible(
    pane_session, tmp_path, monkeypatch
):
    action = tmp_path / "action"
    early_commands = []
    real_open = Path.open

    def observe_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        # Schedule the reader after the real write-open, before any bytes are written.
        if mode == "w" and path.parent == action.parent and action.exists():
            early_commands.append(action.read_text())
        return stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", observe_open)
        pane_session.exit_agent("0")

    assert early_commands == []
    assert action.read_text() == "0"


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("engine", ["c", "cx"])
def test_given_attached_launcher_when_interrupted_then_original_shell_survives(pane_session, engine, remote):
    pane_session.start(engine=engine, remote=remote, caller=True)
    pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1


@pytest.mark.parametrize("engine", ["c", "cx"])
def test_given_early_raw_interrupt_when_remote_agent_exits_cleanly_then_original_shell_survives(pane_session, engine):
    # Pin elapsed time below the startup guard even on a busy host. A TUI can
    # consume Ctrl+C and return zero immediately after it starts.
    pane_session.start(engine=engine, remote=True, mode="raw-clean", caller=True, quick_exit=True)
    pane_session.interrupt()
    assert _wait(lambda: pane_session.after.exists() or "close this session" in pane_session.capture())
    if pane_session.alive():
        pane_session.interrupt()
        pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1


@pytest.mark.parametrize("engine", ["c", "cx"])
def test_given_unregistered_session_when_interrupted_then_original_shell_survives(pane_session, engine):
    pane_session.start(engine=engine, remote=True, caller=True)

    def unregister(doc):
        doc["sessions"] = [record for record in doc["sessions"] if record["name"] != pane_session.name]
        return True

    registry._mutate(unregister)
    pane_session.interrupt()
    assert _wait(lambda: "Session exit persistence failed" in pane_session.capture()), pane_session.capture()
    pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1


@pytest.mark.parametrize("engine", ["c", "cx"])
def test_given_remote_recovery_shell_when_exited_then_original_shell_survives(pane_session, engine):
    # Exercise the quick-failure path even if launching and attaching the caller takes seconds.
    pane_session.start(engine=engine, remote=True, caller=True, quick_exit=True)
    pane_session.exit_agent("1")
    assert _wait(lambda: "close this session" in pane_session.capture()), pane_session.capture()
    assert _wait(lambda: "test-shell>" in pane_session.capture()), pane_session.capture()
    pane_session.type_line("exit")
    pane_session.assert_caller()
    assert pane_session.count() == 1


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("engine", ["c", "g", "cx"])
@pytest.mark.parametrize("mode", ["signal", "raw", "raw-clean"])
def test_given_running_engine_when_terminal_interrupt_exits_then_original_shell_returns(
    pane_session, engine, remote, mode
):
    pane_session.start(engine=engine, remote=remote, mode=mode, caller=True)
    # A raw TUI that exits 0 on Ctrl+C after a healthy run looks like /exit, so it
    # restarts unless the next Ctrl+C lands in the wrapper's restart window.
    if mode == "raw-clean":
        time.sleep(3.2)
    pane_session.interrupt()
    if mode == "raw-clean":
        assert _wait(lambda: "Ctrl+C to stop" in pane_session.capture()), pane_session.capture()
        pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1, "an intentional interrupt relaunched the engine"


def test_given_engine_ignoring_interrupt_when_double_ctrl_c_then_original_shell_returns(pane_session):
    pane_session.start(mode="ignore", caller=True)
    pane_session.interrupt()
    assert _wait(lambda: "Ctrl+C again" in pane_session.capture()), pane_session.capture()
    pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1


def test_given_session_teardown_when_interrupted_again_then_original_shell_returns(pane_session):
    pane_session.start(caller=True)
    pane_session.interrupt()
    pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1


@pytest.mark.parametrize("command", ["1", "crash"])
def test_given_auto_policy_when_engine_crashes_then_wrapper_restarts(pane_session, command):
    pane_session.start()
    # Preserve the existing three-second quick-failure guard.
    time.sleep(3.2)
    pane_session.exit_agent(command)
    assert _wait(lambda: pane_session.count() == 2), pane_session.capture()


@pytest.mark.parametrize("remote", [False, True])
def test_given_healthy_run_when_agent_exits_cleanly_then_wrapper_restarts_it(pane_session, remote):
    # `/exit`, Ctrl+D and Claude Code's own double Ctrl+C all return 0.
    pane_session.start(remote=remote)
    time.sleep(3.2)
    pane_session.exit_agent("0")
    assert _wait(lambda: pane_session.count() == 2), pane_session.capture()


@pytest.mark.parametrize("remote", [False, True])
def test_given_clean_exit_restart_window_when_interrupted_then_original_shell_returns(pane_session, remote):
    pane_session.start(remote=remote, caller=True)
    time.sleep(3.2)
    pane_session.exit_agent("0")
    assert _wait(lambda: "Ctrl+C to stop" in pane_session.capture()), pane_session.capture()
    pane_session.interrupt()
    pane_session.assert_caller()
    assert pane_session.count() == 1, "an interrupt in the restart window relaunched the engine"


@pytest.mark.parametrize(("window", "notice"), [("0", False), ("1", True)])
def test_given_stop_window_set_while_running_when_agent_exits_cleanly_then_the_window_it_names_applies(
    pane_session, window, notice
):
    pane_session.start()
    # Written after launch: the wrapper reads the window at the exit, not at launch.
    path = Path(config.config_file_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[session]\nrestart_stop_window_seconds = {window}\n", encoding="utf-8")
    time.sleep(3.2)
    pane_session.exit_agent("0")
    assert _wait(lambda: pane_session.count() == 2), pane_session.capture()
    assert ("restarting in 1s. Press Ctrl+C to stop" in pane_session.capture()) is notice, pane_session.capture()


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
