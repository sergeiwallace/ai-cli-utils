"""The supervisor must promote the process group the child actually created.

The defect this exists for, measured on a live wedged pane 2026-09-24: the supervisor knows only
`$!`, the pid it backgrounded, and it promoted that pid as a process group id. That is correct only
when every wrapper between the shell and the shim `exec`s through, and one does not. On a host where
`python3` resolves to a shell shim running `uv run`, `uv run` SPAWNS the interpreter as a child and
waits, so the shim -- and therefore `setpgrp()` -- runs one level further down and creates a group
whose id is the grandchild's pid.

Promoting `$!` then hands the terminal to a process group with no members. All three measured
consequences follow from that one fact: the stopped shim never received its SIGCONT because the
signal went to the empty group, so the pane printed nothing; the promotion loop spun its whole
30-second budget and failed; and Ctrl+C was delivered to an empty group, so the pane could not even
be interrupted -- fifteen recorded `^C` presses did nothing.

These tests run the real shim on a real pty behind a real spawn layer. Nothing is mocked at the
boundary the defect lives on, because the defect IS that boundary: process-group identity across an
exec-versus-spawn wrapper.
"""

from __future__ import annotations

import sys

import pytest

# `fcntl`, `pty` and `termios` do not exist on Windows, so this module cannot be IMPORTED there.
# An unguarded import is therefore a collection ERROR, not a failing test, and a collection error
# aborts the entire run: it took all three Windows jobs red while Linux stayed green, which is what
# made it look like a platform mystery rather than one missing guard.
#
# The skip must come before those imports and be module-level for that reason. It is not the
# skip-after-expensive-setup pattern tests/test_skip_hygiene.py forbids — nothing has been set up
# when it runs. Ruff permits the imports below it because a platform guard is an accepted reason for
# a late import (verified against this repo's own select list, which includes E4).
if sys.platform == "win32":
    pytest.skip(
        "POSIX-only: needs fcntl/pty/termios, a controlling terminal, and real process groups",
        allow_module_level=True,
    )

import fcntl
import json
import os
import pty
import signal
import tempfile
import termios
import time
from pathlib import Path

from ai_cli.session_script import CHILD_BODY_SHIM

# A faithful stand-in for `uv run`: it SPAWNS the interpreter and waits rather than exec'ing
# through. That single extra process layer is the entire defect, so the test creates a real one
# instead of simulating its effect.
SPAWN_WRAPPER = "import subprocess, sys; raise SystemExit(subprocess.run([sys.executable] + sys.argv[1:]).returncode)"

_READY_TIMEOUT_S = 20.0


def _pgid_of(pid: int) -> int | None:
    """The process group of `pid`, or None once it is gone."""
    try:
        with Path(f"/proc/{pid}/status").open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("NSpgid:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _group_members(pgid: int) -> list[int]:
    """Every live pid in process group `pgid`. Empty means the group does not exist."""
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and _pgid_of(int(entry.name)) == pgid:
            members.append(int(entry.name))
    return members


def _child_argv(spawn_layer: bool) -> list[str]:
    shim = [sys.executable, "-c", CHILD_BODY_SHIM, "/bin/sleep", "30"]
    if not spawn_layer:
        return shim
    return [sys.executable, "-c", SPAWN_WRAPPER, "-c", CHILD_BODY_SHIM, "/bin/sleep", "30"]


def _supervise(ready_path: str, slave: int, write_fd: int, spawn_layer: bool, promote: str) -> None:
    """Body of the forked session leader. Never returns."""
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    for target in (0, 1, 2):
        os.dup2(slave, target)
    os.environ["AI_CLI_SUPERVISOR_CHILD_READY_PATH"] = ready_path
    os.environ["AI_CLI_SUPERVISOR_TERMINAL_FD"] = "0"
    os.environ.pop("AI_CLI_SUPERVISOR_LEASE_FD", None)

    child = os.fork()
    if child == 0:
        argv = _child_argv(spawn_layer)
        os.execv(argv[0], argv)

    deadline = time.monotonic() + _READY_TIMEOUT_S
    reported = ""
    while time.monotonic() < deadline:
        try:
            reported = Path(ready_path).read_text(encoding="utf-8").strip()
        except OSError:
            reported = ""
        if reported:
            break
        time.sleep(0.01)

    target = reported if promote == "reported" else str(child)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    promoted, errno_value = False, None
    if target.isdigit():
        try:
            os.tcsetpgrp(0, int(target))
            os.killpg(int(target), signal.SIGCONT)
            promoted = True
        except OSError as exc:
            errno_value = exc.errno
    # Membership is measured HERE, before this session leader exits. Its exit revokes the
    # controlling terminal and SIGHUPs the foreground group, so a parent that looked afterwards
    # would find every group empty and could not tell a real group from the empty one.
    os.write(
        write_fd,
        json.dumps(
            {
                "child": child,
                "reported": reported,
                "promoted": promoted,
                "errno": errno_value,
                "foreground": os.tcgetpgrp(0),
                "reported_group_members": (_group_members(int(reported)) if reported.isdigit() else []),
                "backgrounded_group_members": _group_members(child),
            }
        ).encode(),
    )


def _run_handoff(*, spawn_layer: bool, promote: str) -> dict:
    """Perform one supervisor/child terminal handoff and report what happened.

    Forks a session leader owning a pty as its controlling terminal, exactly as the supervisor owns
    the tmux pane's terminal, because `tcsetpgrp` is only permitted from inside that session.
    `promote` selects which id reaches `tcsetpgrp`: "reported" is the fixed behaviour (the pgid the
    child wrote) and "backgrounded" is the old behaviour (`$!`).
    """
    ready_fd, ready_path = tempfile.mkstemp(prefix="handoff-ready.")
    os.close(ready_fd)
    master, slave = pty.openpty()
    read_fd, write_fd = os.pipe()

    supervisor = os.fork()
    if supervisor == 0:  # pragma: no cover - forked child
        try:
            os.close(read_fd)
            os.close(master)
            _supervise(ready_path, slave, write_fd, spawn_layer, promote)
            os._exit(0)
        except BaseException:
            os._exit(1)

    os.close(write_fd)
    os.close(slave)
    payload = b""
    while chunk := os.read(read_fd, 4096):
        payload += chunk
    os.close(read_fd)
    os.waitpid(supervisor, 0)
    result = json.loads(payload) if payload else {}

    if result:
        _reap(result)
    os.close(master)
    Path(ready_path).unlink()
    return result


def _reap(result: dict) -> None:
    """A test owns every process it starts: continue the group first, then kill it."""
    for pgid in {result.get("reported"), result.get("child")}:
        if pgid and str(pgid).isdigit():
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.killpg(int(pgid), sig)
                except OSError:
                    pass
    child = result.get("child")
    if child:
        for sig in (signal.SIGCONT, signal.SIGKILL):
            try:
                os.kill(child, sig)
            except OSError:
                pass
        try:
            os.waitpid(child, os.WNOHANG)
        except OSError:
            pass


@pytest.mark.parametrize("spawn_layer", [False, True], ids=["exec-through", "spawn-layer"])
def test_the_child_reports_the_process_group_it_actually_created(spawn_layer):
    """The ready file must carry a pgid, not a fixed word the supervisor cannot act on.

    Parametrised over both wrapper shapes because the supervisor cannot know which one it got.
    """
    result = _run_handoff(spawn_layer=spawn_layer, promote="reported")

    assert result, "the forked supervisor reported nothing"
    assert result["reported"].isdigit(), (
        f"the child wrote {result['reported']!r} rather than its process group id, so the supervisor "
        f"has nothing to promote except the pid it backgrounded"
    )
    assert result["reported_group_members"], "the reported group had no members, so it was never real"


def test_a_spawning_wrapper_makes_the_backgrounded_pid_the_wrong_group():
    """The mechanism as a test: behind a spawn layer, `$!` is not the new group's id.

    Negative control for the fix. If this fails, the wrapper under test stopped spawning and every
    other test here would pass for a reason that does not hold in production.
    """
    result = _run_handoff(spawn_layer=True, promote="reported")

    assert result["reported"].isdigit()
    assert int(result["reported"]) != result["child"], (
        "the spawn layer added no process layer, so this suite no longer exercises the defect"
    )
    assert not result["backgrounded_group_members"], (
        "the backgrounded pid names a group with members, so promoting it would have worked and this "
        "no longer reproduces the reported failure"
    )


def test_promoting_the_reported_group_puts_a_live_group_in_the_foreground():
    """The fix's contract: the terminal ends up owned by a group that has members."""
    result = _run_handoff(spawn_layer=True, promote="reported")

    assert result["promoted"], f"promotion failed with errno {result['errno']}"
    assert result["foreground"] == int(result["reported"])
    assert result["reported_group_members"], "the promoted group is empty"


def test_promoting_the_backgrounded_pid_leaves_an_empty_foreground_group():
    """The old behaviour, pinned so a regression is visible rather than merely slow.

    An empty foreground group is why Ctrl+C did nothing on the wedged pane: the signal goes to the
    terminal's foreground group, and that group contained no processes at all.
    """
    result = _run_handoff(spawn_layer=True, promote="backgrounded")

    assert not result["backgrounded_group_members"], (
        "the group the old code promoted has members, so the wedge cannot have happened this way"
    )
    if result["promoted"]:
        assert result["foreground"] == result["child"]
        assert not _group_members(result["foreground"]), (
            "promoting the backgrounded pid left a non-empty foreground group, contradicting the "
            "measurement this fix rests on"
        )


def test_the_readiness_write_is_unbuffered_so_the_stop_cannot_strand_it():
    """The child stops itself immediately after reporting, so the write must already have landed.

    A buffered write flushed only when the file object is garbage collected races the SIGSTOP on the
    very next statement, and the supervisor is the thing that would resume it.
    """
    assert "os.write(" in CHILD_BODY_SHIM, (
        "the readiness write is no longer unbuffered; a buffered write depends on refcount timing "
        "for something the handoff cannot retry"
    )
    assert "SIGSTOP" in CHILD_BODY_SHIM
    assert CHILD_BODY_SHIM.index("os.write(") < CHILD_BODY_SHIM.index("SIGSTOP"), (
        "the group is reported after the process stops itself, so the supervisor can never read it"
    )
