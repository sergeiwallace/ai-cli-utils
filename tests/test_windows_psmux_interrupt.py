"""Ctrl+C in an `ai c` session on Windows: the generated wrapper in a real psmux pane.

Claude Code puts the console in raw mode, so a lone Ctrl+C is a key it reads, and its own
double Ctrl+C ends it with status 0. Presses that arrive while it shuts down, and after,
reach the Git Bash wrapper as SIGINT. Those presses are the user's way out of the restart
loop. Under Git Bash the wrapper backgrounded the native agent and blocked in `wait` for
it, and once those presses arrived while the agent exited, the wait never returned: the
stop was recorded and never acted on, and the pane could only be left by detaching.

The stand-in agent here does what Claude Code does at that boundary: raw console input,
a double Ctrl+C that exits 0, and a shutdown of a few seconds that ignores console control
events. Only the agent and the `ai` renderer are stand-ins; psmux, the console, Git Bash,
the wrapper and its lifecycle helper are real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import tmux_runnable

from ai_cli import session_run_state as state
from ai_cli.session_script import get_engine_script

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(sys.platform != "win32", reason="the psmux console path is Windows-only"),
    pytest.mark.timeout(600),
]

NAME = "c-session-1"

_AGENT = """#!{python}
import ctypes, msvcrt, os, sys, time
LOG = {log!r}
def note(text):
    with open(LOG, "a") as out:
        out.write(text + "\\n")
note("start " + str(os.getpid()))
kernel = ctypes.windll.kernel32
console = kernel.GetStdHandle(-10)
mode = ctypes.c_uint()
kernel.GetConsoleMode(console, ctypes.byref(mode))
kernel.SetConsoleMode(console, mode.value & ~0x7)
last = 0.0
while True:
    key = msvcrt.getwch()
    if key != "\\x03":
        continue
    note("key ctrl-c")
    now = time.monotonic()
    if now - last < 2:
        break
    last = now
    print("Press Ctrl-C again to exit", flush=True)
kernel.SetConsoleMode(console, mode.value)
handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
ignore = handler_type(lambda kind: 1)
kernel.SetConsoleCtrlHandler(ignore, 1)
time.sleep(3)
note("exit 0")
sys.exit(0)
"""


@pytest.fixture
def psmux_session(tmp_path, monkeypatch):
    runnable, reason = tmux_runnable()
    if not runnable:
        pytest.skip(reason)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("the session wrapper needs Git Bash")
    shell_state = tmp_path / "shell-state"
    shell_state.mkdir()
    agent_log = tmp_path / "agent.log"
    launch_log = tmp_path / "launch.log"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "ai").write_text(
        '#!/bin/sh\nif [ "$2" = _begin ]; then echo "$TEST_GENERATION"; fi\nexit 0\n', encoding="utf-8"
    )
    (binaries / "direnv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "claude").write_text(
        _AGENT.format(python=Path(sys.executable).as_posix(), log=str(agent_log)), encoding="utf-8"
    )
    monkeypatch.setattr("ai_cli.session_script.resolve_session_shell", lambda: bash)
    state.register(NAME, recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin(NAME)
    script = tmp_path / "session.sh"
    script.write_text(
        get_engine_script(
            "c",
            "session-1",
            NAME,
            "c-session-",
            "myproject",
            worktree_dir=str(tmp_path),
            launch_log_path=launch_log.as_posix(),
        ),
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("DIRENV_")}
    env.update(
        PATH=binaries.as_posix() + os.pathsep + env["PATH"],
        XDG_STATE_HOME=str(shell_state),
        AI_HOST="host-a",
        TEST_GENERATION=generation,
    )
    server = f"ai-cli-test-{os.getpid()}-{time.monotonic_ns()}"

    def tmux(*args):
        return subprocess.run(
            ["tmux", "-L", server, *args], capture_output=True, text=True, check=False, timeout=30, env=env
        )

    class Session:
        def launches(self) -> int:
            text = agent_log.read_text() if agent_log.exists() else ""
            return sum(line.startswith("start ") for line in text.splitlines())

        def agent_lines(self) -> list[str]:
            return agent_log.read_text().splitlines() if agent_log.exists() else []

        def alive(self) -> bool:
            return tmux("has-session", "-t", NAME).returncode == 0

        def press(self) -> None:
            tmux("send-keys", "-t", NAME, "C-c")

        def stop_markers(self) -> list[str]:
            return sorted(path.name for path in (shell_state / "ai-cli-utils").glob("session-int-*"))

        def evidence(self) -> str:
            log = launch_log.read_text() if launch_log.exists() else "(no launch log)"
            return f"agent: {self.agent_lines()}\nmarkers: {self.stop_markers()}\nlaunch log:\n{log}"

    result = tmux("-f", os.devnull, "new-session", "-d", "-s", NAME, "-x", "120", "-y", "30", bash, script.as_posix())
    assert result.returncode == 0, result.stderr
    session = Session()
    try:
        # Startup runs several native helpers, each a slow process start on Windows.
        deadline = time.monotonic() + 240
        while session.launches() == 0 and time.monotonic() < deadline:
            time.sleep(0.5)
        assert session.launches() == 1, session.evidence()
        # Past the wrapper's 3 s startup guard, where a clean exit already ends the session.
        time.sleep(4)
        yield session
    finally:
        tmux("kill-server")


def test_given_running_agent_when_ctrl_c_is_pressed_repeatedly_then_agent_and_restart_loop_end(psmux_session):
    # A user leaving the session keeps pressing Ctrl+C until the pane is gone.
    deadline = time.monotonic() + 240
    while psmux_session.alive() and time.monotonic() < deadline:
        psmux_session.press()
        time.sleep(0.5)

    assert not psmux_session.alive(), psmux_session.evidence()
    assert "exit 0" in psmux_session.agent_lines(), psmux_session.evidence()
    assert psmux_session.launches() == 1, psmux_session.evidence()
    assert psmux_session.stop_markers() == [], psmux_session.evidence()


def test_given_running_agent_when_ctrl_c_is_pressed_once_then_agent_reads_it_and_keeps_running(psmux_session):
    psmux_session.press()
    deadline = time.monotonic() + 30
    while "key ctrl-c" not in psmux_session.agent_lines() and time.monotonic() < deadline:
        time.sleep(0.5)
    time.sleep(5)

    assert psmux_session.agent_lines().count("key ctrl-c") == 1, psmux_session.evidence()
    assert "exit 0" not in psmux_session.agent_lines(), psmux_session.evidence()
    assert psmux_session.alive(), psmux_session.evidence()
    assert psmux_session.launches() == 1, psmux_session.evidence()
