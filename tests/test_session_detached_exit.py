"""Launch exit status across real process and isolated tmux boundaries."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import tmux_runnable
from process_ownership import reap, spawn_owned

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(sys.platform == "win32", reason="detached POSIX launch contract"),
    # A hang in a real tmux or pty step must fail with a traceback, not run to the job cap.
    pytest.mark.timeout(120),
]

# Run the real Click command and handoff in a new session with no controlling TTY.
# Replace unrelated startup services and the engine, never tmux or its attach.
_LAUNCH = r"""
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ai_cli import main

mode = sys.argv[1]
cfg = {"machine": {"host_id": "host-a"}, "worktree": {"enabled": False}}
with ExitStack() as stack:
    for target, value in {
        "ai_cli.config.load_config": cfg,
        "ai_cli.config.get_current_project_name": "myproject",
        "ai_cli.config.get_session_map": {},
        "ai_cli.config.detect_machine_profile": {"host_id": "host-a"},
        "ai_cli.session_naming.resolve_or_prompt_host_alias": "tst",
        "ai_cli.session._resolve_is_remote": False,
        "ai_cli.session_script.get_engine_script": "exec sleep 60\n",
        "ai_cli.session_registry.current_boot_id": "test-boot",
        "ai_cli.iterm2._load_iterm2_config": {},
        "ai_cli.iterm2._assign_iterm2_color_slot": None,
        "ai_cli.main._auto_update_if_stale": False,
        "ai_cli.main._bare_engine_command": ["missing-example-engine"] if mode == "missing" else [sys.executable, "-c", "raise SystemExit(0)"],
    }.items():
        stack.enter_context(patch(target, return_value=value))
    for target in (
        "ai_cli.main._ensure_dolt_server",
        "ai_cli.main._ensure_wake_sidecar",
        "ai_cli.main.trigger_background_update",
        "ai_cli.main._finish_recorded_migrations",
        "ai_cli.main._start_session_wake_watcher",
        "ai_cli.main._track_launch",
        "ai_cli.process_manager._start_stale_session_reaper_for_launch",
        "ai_cli.tunnel._ensure_nats_tunnel",
        "ai_cli.session.cleanup_stale_sessions",
        "ai_cli.trust.ensure_workspace_trusted",
        "ai_cli.iterm2._emit_iterm2_profile_setup",
        "ai_cli.iterm2._configure_tmux_for_iterm2",
        "ai_cli.iterm2._rename_tmux_window",
    ):
        stack.enter_context(patch(target))
    if mode == "create-failure":
        real_run = subprocess.run
        def run(argv, *args, **kwargs):
            if argv[:2] == ["tmux", "new-session"]:
                return subprocess.CompletedProcess(argv, 1, "", "example creation failure")
            return real_run(argv, *args, **kwargs)
        stack.enter_context(patch("subprocess.run", side_effect=run))
    args = ["c", "1", "-W", "-D", "--project-prefix", "myproject"]
    if mode in ("bare", "missing"):
        args.append("-b")
    elif mode == "resume":
        args.append("-r")
    elif mode == "once":
        args.append("-o")
    main._cli_group.main(args=args, prog_name="ai")
"""


@pytest.fixture
def detached_cli(tmp_path, monkeypatch):
    runnable, reason = tmux_runnable()
    if not runnable:
        pytest.skip(reason)
    for variable, directory in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_DATA_HOME", "data"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / directory))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("TERM", "xterm-256color")
    for variable in ("ITERM_SESSION_ID", "TERM_PROGRAM"):
        monkeypatch.delenv(variable, raising=False)
    binary = tmp_path / "bin"
    binary.mkdir()
    engine = binary / "claude"
    engine.write_text("#!/bin/sh\nexec sleep 60\n")
    engine.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])

    def launch(mode):
        process = spawn_owned(
            [sys.executable, "-c", _LAUNCH, mode],
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=30)
            return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        finally:
            reap(process)

    return launch


def _panes():
    return subprocess.run(
        ["tmux", "list-panes", "-t", "=c-myproject-tst-1", "-F", "#{pane_dead}"],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("mode", ["new", "existing", "resume", "once"])
def test_given_no_terminal_when_session_launches_then_live_session_returns_zero(detached_cli, mode):
    if mode in ("existing", "resume"):
        subprocess.run(["tmux", "new-session", "-d", "-s", "c-myproject-tst-1", "sleep 60"], check=True)
    result = detached_cli(mode)
    panes = _panes()
    assert panes.returncode == 0 and panes.stdout.strip() == "0", result.stderr
    assert result.returncode == 0, result.stderr
    assert "running detached" in result.stderr
    assert "not a terminal" not in result.stderr


@pytest.mark.parametrize(("mode", "code", "message"), [("bare", 0, ""), ("missing", 1, "not found on PATH.")])
def test_given_bare_engine_when_launched_without_terminal_then_engine_status_is_preserved(
    detached_cli, mode, code, message
):
    result = detached_cli(mode)
    assert result.returncode == code, result.stderr
    assert message in result.stderr


def test_given_tmux_creation_fails_when_launched_without_terminal_then_existing_error_is_preserved(detached_cli):
    result = detached_cli("create-failure")
    assert result.returncode == 1
    assert "example creation failure" in result.stderr
    assert _panes().returncode != 0


def test_given_dead_pane_when_resumed_without_terminal_then_launch_returns_nonzero(detached_cli):
    subprocess.run(["tmux", "new-session", "-d", "-s", "c-myproject-tst-1", "sleep 60"], check=True)
    subprocess.run(["tmux", "set-window-option", "-t", "=c-myproject-tst-1", "remain-on-exit", "on"], check=True)
    # Replace only the stand-in this test created, leaving a real dead pane behind.
    subprocess.run(["tmux", "respawn-pane", "-k", "-t", "=c-myproject-tst-1:", "exit 0"], check=True)
    deadline = time.monotonic() + 30
    while _panes().stdout.strip() != "1":
        assert time.monotonic() < deadline, "stand-in pane did not exit"
        time.sleep(0.01)
    result = detached_cli("resume")
    assert result.returncode == 1, result.stderr
    assert "has no live pane after launch" in result.stderr
    assert "running detached" not in result.stderr


@pytest.mark.parametrize("mode", ["new", "existing", "resume", "once"])
def test_given_terminal_when_session_launches_then_attach_and_detach_keep_zero_status(detached_cli, tmp_path, mode):
    # POSIX-only: give the launcher a controlling terminal, as an interactive shell does.
    import fcntl
    import termios

    def claim_terminal():
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    if mode in ("existing", "resume"):
        subprocess.run(["tmux", "new-session", "-d", "-s", "c-myproject-tst-1", "sleep 60"], check=True)
    master, slave = os.openpty()
    process = None
    try:
        process = spawn_owned(
            [sys.executable, "-c", _LAUNCH, mode],
            cwd=tmp_path,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=claim_terminal,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            clients = subprocess.run(
                ["tmux", "list-clients", "-t", "=c-myproject-tst-1", "-F", "#{client_pid}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if clients.returncode == 0 and clients.stdout.strip():
                break
            time.sleep(0.01)
        else:
            pytest.fail("interactive launcher did not attach a tmux client")
        subprocess.run(["tmux", "detach-client", "-s", "=c-myproject-tst-1"], check=True)
        assert process.wait(timeout=30) == 0
        assert _panes().stdout.strip() == "0"
    finally:
        if process is not None:
            reap(process)
        os.close(slave)
        os.close(master)
