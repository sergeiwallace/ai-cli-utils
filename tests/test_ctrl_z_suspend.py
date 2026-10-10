"""Ctrl+Z in an ``ai c`` session must not leave it frozen with no way back (AI-CLI-y6el).

Measured on a Linux host against a throwaway ``ai c`` session (see
docs/bugs/ai-cli-y6el-ctrl-z-suspends-session-with-no-way-back.md): Claude Code
reads Ctrl+Z in raw mode, restores the terminal, prints "Run `fg` to bring Claude
Code back", and sends SIGTSTP to its own process group. That group is the pane's
foreground group -- the child body, its watcher, Claude Code and every MCP server
-- so all of it enters state ``T``. The only process left running is the
supervisor, a non-interactive ``set +m`` shell in a background group blocked in
``wait``, which does not return for a stopped child. There is no job-control
shell anywhere in the pane, so ``fg`` is just text echoed into a line buffer that
nothing reads, and the only way out was to kill the session and lose everything
it owned.

Every test drives a real stop of a real process group; none mocks ``/proc``, the
terminal, or the signal.
"""

from __future__ import annotations

import contextlib
import json
import os
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
_HAS_PROC = Path("/proc/self/stat").exists()

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(signal, "SIGTSTP"), reason="job-control stop needs POSIX signals"
)

#: Generous because these run real shells under `-n auto`; every wait returns as soon as it is satisfied.
_OBSERVE_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))
#: How long a stopped session may stay stopped before the test calls it frozen. The
#: watchdog polls once a second; this leaves room for a loaded scheduler.
_RESUME_BOUND_SECONDS = 15.0


def _wait_for(predicate, timeout: float = _OBSERVE_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _state(pid: int) -> str | None:
    if _HAS_PROC:
        try:
            line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        fields = line.rpartition(")")[2].split()
        return fields[0] if fields else None
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return out.stdout.strip()[:1] or None


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest.fixture
def tmux_socket():
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-ctrlz-")
    sock = f"{sock_dir}/tmux.sock"
    yield sock
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    shutil.rmtree(sock_dir, ignore_errors=True)


def _fake_claude(started: Path, resumed: Path) -> str:
    """A stand-in that handles Ctrl+Z exactly the way Claude Code 2.1 was measured to.

    Raw mode means the terminal does not turn Ctrl+Z into SIGTSTP; the program reads
    the 0x1a byte, restores the line discipline, prints its suspend notice, and stops
    its own process GROUP (``kill(0, SIGTSTP)``). Everything after that line runs only
    once something continues the group.
    """
    return (
        f"#!{sys.executable}\n"
        "import os, select, signal, termios, time, tty\n"
        f"open({str(started)!r}, 'a').write(f'{{os.getpid()}}\\n')\n"
        "saved = termios.tcgetattr(0)\n"
        "tty.setraw(0)\n"
        "end = time.monotonic() + 300\n"
        "while time.monotonic() < end:\n"
        "    if select.select([0], [], [], 0.1)[0] and b'\\x1a' in os.read(0, 64):\n"
        "        termios.tcsetattr(0, termios.TCSADRAIN, saved)\n"
        "        os.write(1, b'Claude Code has been suspended. Run `fg` to bring Claude Code back.\\r\\n')\n"
        "        os.kill(0, signal.SIGTSTP)\n"
        "        tty.setraw(0)\n"
        f"        open({str(resumed)!r}, 'a').write(f'{{os.getpid()}}\\n')\n"
    )


def _start_supervised_session(sock: str, tmp_path: Path, shell: str, session: str, fake_claude: str) -> None:
    """Run the full generated session -- supervisor, promoted child body, agent -- as a real tmux pane.

    The pane command is the stable session script itself, exactly as ``ai c`` hands
    it to ``tmux new-session``; only ``claude`` and ``ai`` are stand-ins.
    """
    fake_bin = tmp_path / "fakes"
    fake_bin.mkdir()
    (fake_bin / "claude").write_text(fake_claude)
    (fake_bin / "ai").write_text(
        '#!/bin/sh\nif [ "$1" = internal ] && [ "$2" = get-version ]; then printf "%s\\n" unknown; fi\nexit 0\n'
    )
    (fake_bin / "python3").symlink_to(sys.executable)
    for fake in fake_bin.iterdir():
        if not fake.is_symlink():
            fake.chmod(0o755)
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()
    sessions = tmp_path / "state" / "ai-cli-utils" / "sessions"
    sessions.mkdir(parents=True)
    script = sessions / f"{session}.sh"
    script.write_text(get_engine_script("c", "ctrlz", session, "c-ctrlz-", "ctrlz", worktree_dir=str(tmp_path)))
    created = subprocess.run(
        ["tmux", "-S", sock, "new-session", "-d", "-s", session, "-x", "120", "-y", "30", shell, str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", ""))),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "ZDOTDIR": str(zdotdir),
        },
        check=False,
    )
    assert created.returncode == 0, created.stderr


def _pane(sock: str, session: str) -> str:
    return subprocess.run(
        ["tmux", "-S", sock, "capture-pane", "-p", "-t", session], capture_output=True, text=True, check=False
    ).stdout


def _release(pids: list[int]) -> None:
    """Teardown for anything a failing test left stopped: continue it, then end it."""
    for pid in pids:
        for sig in (signal.SIGCONT, signal.SIGKILL):
            with contextlib.suppress(OSError):
                os.kill(pid, sig)


# --- AC-2 / AC-3: in the pane, without anything the operator has to type ---------


@pytest.mark.real_tmux
@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_given_ai_c_session_when_operator_presses_ctrl_z_then_the_same_agent_resumes_without_a_restart(
    tmux_socket, tmp_path, shell_name
):
    """The reported outage: Ctrl+Z froze the whole pane and nothing typed into it helped.

    The keypress goes through tmux to the real pane; the agent stops its own group
    the way Claude Code does. The session must come back by itself, as the SAME
    process (a restart would kill every background agent and shell it owns), and
    within a bound -- not wait for a human to find another terminal.
    """
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")
    session = f"ctrlz-{shell_name}"
    started, resumed = tmp_path / "started", tmp_path / "resumed"
    _start_supervised_session(tmux_socket, tmp_path, shell, session, _fake_claude(started, resumed))
    try:
        assert _wait_for(lambda: bool(_lines(started))), f"the agent never started: {_pane(tmux_socket, session)!r}"
        (agent_pid,) = (int(pid) for pid in _lines(started))
        time.sleep(0.5)

        subprocess.run(["tmux", "-S", tmux_socket, "send-keys", "-t", session, "C-z"], check=True)
        assert _wait_for(lambda: "has been suspended" in _pane(tmux_socket, session), timeout=10), (
            "the stand-in never saw Ctrl+Z, so this run proves nothing about resuming"
        )

        assert _wait_for(lambda: bool(_lines(resumed)), timeout=_RESUME_BOUND_SECONDS), (
            f"the session stayed frozen after Ctrl+Z: agent {agent_pid} state {_state(agent_pid)!r}; "
            f"pane: {_pane(tmux_socket, session)!r}"
        )
        assert _lines(resumed) == [str(agent_pid)], "the resumed process is not the agent that was suspended"
        assert _lines(started) == [str(agent_pid)], "the session restarted its agent instead of resuming it"
        assert _state(agent_pid) != "T"
    finally:
        _release([int(pid) for pid in _lines(started)])


# --- AC-3: the launcher, when the session is found already stopped ----------------


@pytest.mark.real_tmux
def test_given_session_whose_pane_foreground_is_stopped_when_ai_c_reattaches_then_it_is_resumed(tmp_path, monkeypatch):
    """A session can already be stopped when the operator comes back to it.

    Sessions started from a template that predates the in-pane watchdog have no
    one to resume them, and neither does a pane whose watchdog has died. ``ai c
    <n>`` is the operator's way back in, so re-attaching must continue the stopped
    foreground group instead of attaching them to a pane that cannot respond.
    """
    from test_session_launch_shell_resolution import _drive_launch_against_real_tmux

    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-ctrlz-attach-")
    sock = f"{sock_dir}/tmux.sock"
    agent_pid_file, resumed = tmp_path / "agent-pid", tmp_path / "resumed"
    # The production shape, not a simplification of it: the pane leader stays
    # running in a background group (as the supervisor does) while a child it
    # promoted to the terminal foreground stops its own group (as Claude Code does).
    # A pane leader that stopped ITSELF would prove nothing -- tmux continues its own
    # stopped pane process, which is exactly why it never helps the real case.
    promoter = (
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTTOU, signal.SIG_IGN)\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setpgid(0, 0)\n"
        "    while os.tcgetpgrp(0) != os.getpgrp():\n"
        "        time.sleep(0.01)\n"
        "    open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "    os.kill(0, signal.SIGTSTP)\n"
        "    open(sys.argv[2], 'w').write('resumed')\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "os.setpgid(pid, pid)\n"
        "os.tcsetpgrp(0, pid)\n"
        "os.waitpid(pid, 0)\n"
    )
    (tmp_path / "promoter.py").write_text(promoter)
    body = f"{sys.executable} {tmp_path / 'promoter.py'} {agent_pid_file} {resumed}\nsleep 60\n"
    agent_pid = 0
    try:
        _drive_launch_against_real_tmux(sock, tmp_path, name="1", script_body=body)
        assert _wait_for(lambda: bool(_lines(agent_pid_file))), "the pane's agent never started"
        agent_pid = int(_lines(agent_pid_file)[0])
        assert _wait_for(lambda: _state(agent_pid) == "T"), f"the agent never stopped: {_state(agent_pid)!r}"
        time.sleep(1)
        assert _state(agent_pid) == "T" and not resumed.exists(), "something other than ai c resumed it"

        _drive_launch_against_real_tmux(sock, tmp_path, name="1", script_body=body)

        assert _wait_for(resumed.exists, timeout=_RESUME_BOUND_SECONDS), (
            f"ai c re-attached to a stopped session without resuming it (state {_state(agent_pid)!r})"
        )
    finally:
        if agent_pid:
            _release([agent_pid])
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
        shutil.rmtree(sock_dir, ignore_errors=True)


# --- AC-3 reconciled with AI-CLI-2139: resume what is suspended, reclaim what is orphaned ---


def _leads_its_terminal_foreground(pid: int) -> bool:
    """True once ``pid`` has exec'd ``sleep`` and its group is its own terminal's foreground."""
    try:
        line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    comm = line.partition("(")[2].rpartition(")")[0]
    fields = line.rpartition(")")[2].split()
    pgrp, tty_nr, tpgid = int(fields[2]), int(fields[4]), int(fields[5])
    return comm == "sleep" and tty_nr != 0 and pgrp == pid == tpgid


@pytest.fixture
def suspended_in_terminal():
    """A real process that leads its own terminal's foreground group, then is stopped.

    That is the exact shape a Ctrl+Z'd ``ai c`` agent is in: still the terminal's
    foreground, so still the thing its pane is showing. Contrast the orphaned,
    terminal-less sleepers in test_cc_session_stopped.py, which 2139 reclaims.
    """
    import pty

    spawned: list[int] = []
    masters: list[int] = []

    def spawn() -> int:
        pid, master = pty.fork()
        if pid == 0:
            os.execvp("sleep", ["sleep", "300"])
        spawned.append(pid)
        masters.append(master)
        # Waiting for "running" is not enough: forkpty returns in the parent before the
        # child has called setsid() and taken its terminal, and a child stopped in that
        # gap really does have no terminal -- 2139's case, not this one. Measured: 184
        # of 300 fork-then-stop attempts landed in the gap with no load at all.
        assert _wait_for(lambda: _leads_its_terminal_foreground(pid)), "the sleeper never took its terminal"
        os.kill(pid, signal.SIGSTOP)
        assert _wait_for(lambda: _state(pid) == "T"), "the terminal sleeper never stopped"
        return pid

    yield spawn
    _release(spawned)
    for master in masters:
        os.close(master)
    for pid in spawned:
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)


def _proc_start(pid: int) -> int:
    return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rpartition(")")[2].split()[19])


@pytest.mark.skipif(not _HAS_PROC, reason="the CC session registry records procStart from /proc")
def test_given_session_record_for_a_process_suspended_in_its_terminal_when_liveness_checked_then_resumed_not_ended(
    tmp_path, monkeypatch, suspended_in_terminal
):
    """AI-CLI-2139 ends a stopped session process; a Ctrl+Z'd one must be resumed instead.

    2139's reclamation exists for a process stopped with nothing attached to it.
    The same state ``T`` also describes an agent the operator just suspended in its
    own pane, and ending that one is the outage this bug reports. The terminal is
    what tells them apart: a group that still owns its terminal's foreground was
    suspended in place.
    """
    from ai_cli.main import _cc_session_is_live

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "dddddddd-0000-4000-8000-0000000000d4"
    pid = suspended_in_terminal()
    sessions = tmp_path / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    record = {"pid": pid, "sessionId": session_id, "procStart": _proc_start(pid), "name": "ctrlz-4"}
    (sessions / f"{pid}.json").write_text(json.dumps(record))

    verdict = _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert _wait_for(lambda: _state(pid) in {"S", "R"}, timeout=5), f"left in state {_state(pid)!r}, not resumed"
    assert verdict == (True, pid), "a resumed session is in use and must still block a second launch"
    assert (sessions / f"{pid}.json").exists(), "a resumed session's record must not be pruned"


@pytest.mark.skipif(not _HAS_PROC, reason="the CC session registry records procStart from /proc")
def test_given_suspended_session_continued_after_liveness_classified_it_when_checked_then_not_ended(
    tmp_path, monkeypatch, suspended_in_terminal
):
    """The in-pane watchdog and a launcher's liveness check run independently of each other.

    If the watchdog continues the group after the launcher has classified the
    process as stopped, the launcher is holding a stale reading. It must decide on
    what the process is now, which is running and in use, and never end it. The
    interleaving is forced by continuing the real group the moment the real
    classification returns; nothing about the process or its terminal is faked.
    """
    from ai_cli.main import _cc_session_is_live
    from ai_cli.process_probe import ProcfsProbe

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "dddddddd-0000-4000-8000-0000000000d5"
    pid = suspended_in_terminal()
    sessions = tmp_path / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    record = {"pid": pid, "sessionId": session_id, "procStart": _proc_start(pid), "name": "ctrlz-5"}
    (sessions / f"{pid}.json").write_text(json.dumps(record))

    classify = ProcfsProbe.is_abandoned

    def classify_then_watchdog_wins(self, target: int) -> bool:
        stopped = classify(self, target)
        if stopped and target == pid:
            os.killpg(pid, signal.SIGCONT)
            assert _wait_for(lambda: _state(pid) in {"S", "R"}, timeout=5), "the watchdog stand-in did not resume it"
        return stopped

    monkeypatch.setattr(ProcfsProbe, "is_abandoned", classify_then_watchdog_wins)

    verdict = _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert _state(pid) in {"S", "R"}, f"a session the watchdog had already resumed was ended (state {_state(pid)!r})"
    assert verdict == (True, pid), "a running session is in use and must still block a second launch"
    assert (sessions / f"{pid}.json").exists(), "a running session's record must not be pruned"


_LAUNCHER_IN_A_TERMINAL = """
import json, os, signal, sys, time
from pathlib import Path
from ai_cli.main import _cc_session_is_live

def stat(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    except OSError:
        return None

me = stat(os.getpid())
leader = os.fork()
if leader == 0:
    time.sleep(300)
    os._exit(0)
os.kill(leader, signal.SIGSTOP)
while stat(leader)[0] != "T":
    time.sleep(0.01)
sessions = Path.home() / ".claude" / "sessions"
sessions.mkdir(parents=True)
session_id = sys.argv[1]
(sessions / f"{leader}.json").write_text(json.dumps(
    {"pid": leader, "sessionId": session_id, "procStart": int(stat(leader)[19]), "name": "myproject-6"}))
_cc_session_is_live(Path(f"/x/{session_id}.jsonl"))
deadline = time.monotonic() + 15
while time.monotonic() < deadline and stat(leader) is not None and stat(leader)[0] not in "ZX":
    time.sleep(0.05)
left = stat(leader)
print("RESULT " + json.dumps({
    "launcher_leads_terminal_foreground": int(me[4]) != 0 and int(me[2]) == int(me[5]),
    "left_state": left[0] if left else None,
}), flush=True)
os.kill(leader, signal.SIGKILL)
"""


@pytest.mark.skipif(not _HAS_PROC, reason="the CC session registry records procStart from /proc")
def test_given_stopped_session_in_a_terminal_launchers_own_group_when_checked_then_reclaimed_not_resumed(tmp_path):
    """A launcher run from a terminal shares its foreground group with anything it forked.

    A process stopped in that group sits in its terminal's foreground, but it was
    not suspended there: Ctrl+Z stops the whole group, and the launcher reading
    this is in the same group and running. So it is 2139's stopped process and is
    reclaimed, not "resumed". The launcher here runs in a real terminal of its own,
    so the answer does not depend on whether the test runner has one (before this,
    the 2139 own-group test passed under xdist and failed from an interactive shell).
    """
    import pty

    # Built before the fork: the child of a multi-threaded runner should do nothing but exec.
    argv = [sys.executable, "-c", _LAUNCHER_IN_A_TERMINAL, "eeeeeeee-0000-4000-8000-0000000000e6"]
    env = {**os.environ, "HOME": str(tmp_path)}
    pid, master = pty.fork()
    if pid == 0:
        os.execve(sys.executable, argv, env)
    output = b""
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    finally:
        os.close(master)
        os.waitpid(pid, 0)
    text = output.decode(errors="replace")
    result_line = next((line for line in text.splitlines() if line.startswith("RESULT ")), None)
    assert result_line is not None, text
    result = json.loads(result_line.removeprefix("RESULT "))
    assert result["launcher_leads_terminal_foreground"], f"precondition: the launcher must own its terminal: {text}"
    assert result["left_state"] in {None, "Z", "X"}, f"stopped process left in state {result['left_state']!r}: {text}"
