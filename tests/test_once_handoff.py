"""A --once launch hands tmux a command that really starts the agent, and logs why it ended.

The command under test is the one the launcher passes to ``tmux new-session``: it is
taken from the launch itself and run here, on a terminal, with a stub agent on PATH.
"""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_session_registry import state_home  # noqa: F401
from test_session_wake_watcher import launch  # noqa: F401

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the tmux --once handoff is POSIX-only")

_STUB = """#!{python}
import json, os, sys
with open(os.environ["STUB_REPORT"], "w") as fh:
    json.dump({{"stdin": os.isatty(0), "stdout": os.isatty(1), "stderr": os.isatty(2), "argv": sys.argv[1:]}}, fh)
sys.exit(int(os.environ.get("STUB_STATUS", "0")))
"""


def _stub_agent(tmp_path: Path, monkeypatch) -> None:
    """Put a stub ``claude`` first on PATH, which the launched command then resolves."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _handoff_shell_command(launch, **options) -> tuple[str, str]:  # noqa: F811
    launch.invoke(once=True, **options)
    creation = next(argv for argv, _ in launch.calls if argv[:2] == ["tmux", "new-session"])
    return creation[creation.index("--") + 1], creation[-1]


def _run_on_terminal(shell: str, command: str, env: dict) -> int:
    """Run ``shell -c command`` with a pty as its stdin, stdout and stderr, as a tmux pane would."""
    master, slave = os.openpty()
    try:
        proc = subprocess.Popen([shell, "-c", command], stdin=slave, stdout=slave, stderr=slave, env=env)
    finally:
        os.close(slave)
    deadline = time.monotonic() + 120
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    os.read(master, 4096)
                except OSError:
                    pass
        return proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    finally:
        os.close(master)


def _env(report: Path, **extra) -> dict:
    env = dict(os.environ)
    env["STUB_REPORT"] = str(report)
    env.update(extra)
    return env


def test_given_once_launch_when_its_tmux_command_runs_on_a_terminal_then_the_agent_gets_that_terminal(
    launch,  # noqa: F811
    state_home,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _stub_agent(tmp_path, monkeypatch)
    report = tmp_path / "report.json"
    shell, command = _handoff_shell_command(launch)

    status = _run_on_terminal(shell, command, _env(report))

    assert status == 0
    seen = json.loads(report.read_text(encoding="utf-8"))
    # An agent whose stdout is a pipe is not interactive: Claude Code takes it as
    # --print and exits at once, which is the bare `[exited]` this pins.
    assert seen == {
        "stdin": True,
        "stdout": True,
        "stderr": True,
        "argv": ["--dangerously-skip-permissions", "--name", "myproject-tst-1"],
    }


def test_given_detached_once_launch_when_the_agent_exits_then_the_launch_log_records_the_cause(
    launch,  # noqa: F811
    state_home,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _stub_agent(tmp_path, monkeypatch)
    log = tmp_path / "launch.log"
    log.write_text("", encoding="utf-8")
    launch.invoke(once=True, detached=True, attach=False, launch_log_path=str(log))
    creation = next(argv for argv, _ in launch.calls if argv[:2] == ["tmux", "new-session"])
    assert "-d" in creation
    shell, command = creation[creation.index("--") + 1], creation[-1]

    status = _run_on_terminal(shell, command, _env(tmp_path / "report.json", STUB_STATUS="3"))

    assert status == 3
    text = log.read_text(encoding="utf-8")
    assert "claude exited with status 3" in text
    assert "c-myproject-tst-1" in text
