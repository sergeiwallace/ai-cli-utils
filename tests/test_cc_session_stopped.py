"""Exiting a session can leave its process STOPPED, not dead — and the name reserved.

The reported defect, measured on a Linux host: a session's process was sent
``SIGTERM``, the kill returned 0, and four seconds later ``/proc/<pid>`` was
still there with its state field reading ``T``.  Only ``SIGKILL`` removed it.

Two consequences, both tested here:

* **State ``T`` is stopped, not gone.**  A stopped process is fully present to
  ``/proc`` and to any pid-liveness predicate, so the launcher's registry check
  called an abandoned session "still running", refused to resume the name, and
  started a differently-named session instead.  Pid existence cannot tell "alive
  and in use" from "alive but abandoned"; the state field can.
* **A stopped process never *handles* the SIGTERM.**  The signal is queued until
  something continues the process, which is exactly why the terminate looked
  like it had worked.  Ending one takes ``SIGTERM``, ``SIGCONT``, a bounded wait,
  then ``SIGKILL`` — and the only trustworthy confirmation is the absence of
  ``/proc/<pid>``, never a kill's return code.

Every test that asserts about a stopped process drives a **real** stopped child:
``SIGSTOP`` on a process this suite spawned.  A mocked ``/proc``, or a stubbed
liveness predicate, would mock the exact boundary the defect lives on.

The spawned sleepers are deliberately *orphans* (the intermediate process exits
immediately, so ``init`` inherits and reaps them).  A direct child of the test
process would linger as a zombie after being killed, which measures the test
runner's failure to ``wait()`` rather than the reclamation.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_cli.main import _bare_engine_command, _cc_project_dir, _cc_record_liveness, _cc_session_is_live

_HAS_POSIX_PROC = hasattr(os, "fork") and Path("/proc/self/stat").exists()

pytestmark = pytest.mark.skipif(
    not _HAS_POSIX_PROC, reason="needs POSIX fork/signals and /proc to create a genuinely stopped process"
)

# A sleeper that orphans itself, optionally leads its own process group, and
# optionally forks members into that group.  It reports its own pid and its
# members' pids on stdout, then closes stdout so the parent's read ends.
_SLEEPER = """
import os, sys, time

members = int(sys.argv[1])
detach = sys.argv[2] == "detach"

if os.fork():
    os._exit(0)          # orphan the leader so init reaps it and /proc empties
if detach:
    os.setsid()          # own session and process group

kids = []
for _ in range(members):
    kid = os.fork()
    if kid == 0:
        os.close(1)
        time.sleep(300)
        os._exit(0)
    kids.append(kid)

sys.stdout.write(" ".join(str(p) for p in [os.getpid(), *kids]) + "\\n")
sys.stdout.flush()
os.close(1)
time.sleep(300)
"""


def _present(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def _state(pid: int) -> str | None:
    """The state field of ``/proc/<pid>/stat``, read from after the last ``)``."""
    try:
        line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = line.rpartition(")")[2].split()
    return fields[0] if fields else None


def _proc_start(pid: int) -> int:
    """Field 22 (``starttime``) of ``/proc/<pid>/stat``, as the registry records it."""
    line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(line.rpartition(")")[2].split()[19])


def _wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _stop(pid: int) -> None:
    """SIGSTOP ``pid`` and wait until the kernel really reports state ``T``."""
    os.kill(pid, signal.SIGSTOP)
    assert _wait_for(lambda: _state(pid) == "T"), f"pid {pid} never reached state T (state={_state(pid)})"


def _write_registry(home: Path, pid: int, session_id: str, proc_start: int | None = None, name: str = "") -> Path:
    """Write the ``~/.claude/sessions/<pid>.json`` record that marks a live session."""
    sessions = home / ".claude" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    record: dict = {"pid": pid, "sessionId": session_id, "kind": "background"}
    if proc_start is not None:
        record["procStart"] = proc_start
    if name:
        record["name"] = name
    entry = sessions / f"{pid}.json"
    entry.write_text(json.dumps(record))
    return entry


def _write_transcript(project_dir: Path, uuid: str, title: str) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{uuid}.jsonl"
    path.write_text(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": uuid}) + "\n")
    return path


def _write_proc_stat(proc_dir: Path, pid: int, state: str, starttime: int, comm: str = "claude (worker) 1") -> None:
    """A synthetic ``/proc/<pid>/stat`` whose comm carries spaces and parens."""
    (proc_dir / str(pid)).mkdir(parents=True, exist_ok=True)
    fields = [state] + [str(n) for n in range(4, 22)] + [str(starttime)]
    (proc_dir / str(pid) / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")


@pytest.fixture
def sleeper():
    """Spawn orphaned sleeper processes; guarantee every one is reaped afterwards."""
    spawned: list[int] = []

    def spawn(members: int = 0, detach: bool = True) -> list[int]:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SLEEPER, str(members), "detach" if detach else "attach"],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None
        line = proc.stdout.readline()
        proc.stdout.close()
        proc.wait(timeout=30)
        pids = [int(part) for part in line.split()]
        spawned.extend(pids)
        assert all(_present(pid) for pid in pids), f"sleeper did not start: {line!r}"
        return pids

    yield spawn

    for pid in spawned:
        for sig in (signal.SIGCONT, signal.SIGKILL):
            with contextlib.suppress(OSError):
                os.kill(pid, sig)


# --- AC-1 / AC-5: a stopped process is not a session in use ---------------------


def test_given_stopped_session_process_when_liveness_checked_then_not_reported_live(tmp_path, monkeypatch, sleeper):
    """The reported symptom: state ``T`` read as "still running" and blocked the name."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "aaaaaaaa-0000-4000-8000-0000000000a1"
    (pid,) = sleeper()
    _stop(pid)
    _write_registry(tmp_path, pid, session_id, proc_start=_proc_start(pid), name="myproject-3")

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (False, None)


def test_given_running_session_process_when_liveness_checked_then_live_and_process_survives(
    tmp_path, monkeypatch, sleeper
):
    """The negative constraint: a session that really is in use must still block, unkilled.

    Without this, "never report live" and "kill nothing that matters" are both
    satisfiable by a predicate that always answers False.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "bbbbbbbb-0000-4000-8000-0000000000b2"
    (pid,) = sleeper()
    _write_registry(tmp_path, pid, session_id, proc_start=_proc_start(pid), name="myproject-4")

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (True, pid)
    assert _present(pid), "a live session's process must not be terminated"
    assert (tmp_path / ".claude" / "sessions" / f"{pid}.json").exists()


def test_given_stopped_stat_with_parens_in_comm_when_classified_then_not_live(tmp_path):
    """The state field is field 3 *after the last* ``)`` — a comm with parens must parse.

    Splitting the whole line shifts every field for a process named
    ``claude (worker) 1`` and would read the wrong character as the state.
    """
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, state="T", starttime=777)

    assert _cc_record_liveness({"pid": 4242, "procStart": 777}, proc_dir) != "live"


def test_given_running_stat_with_parens_in_comm_when_classified_then_live(tmp_path):
    """Control for the test above: the same parsing must still recognise a running process."""
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, state="S", starttime=777)

    assert _cc_record_liveness({"pid": 4242, "procStart": 777}, proc_dir) == "live"


# --- AC-3 / AC-4: the process is ended, and its group with it -------------------


def test_given_stopped_session_process_when_liveness_checked_then_process_is_gone(tmp_path, monkeypatch, sleeper):
    """AC-3: gone within a bounded time, proven by the absence of ``/proc/<pid>``.

    A ``SIGTERM`` alone leaves a stopped process in state ``T`` — the kill returns
    0 and nothing happens — so the escalation has to continue it and then, if
    needed, ``SIGKILL`` it.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "cccccccc-0000-4000-8000-0000000000c3"
    (pid,) = sleeper()
    _stop(pid)
    _write_registry(tmp_path, pid, session_id, proc_start=_proc_start(pid), name="myproject-3")

    _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert _wait_for(lambda: not _present(pid)), f"pid {pid} still present in state {_state(pid)}"


def test_given_stopped_session_leader_when_liveness_checked_then_its_process_group_is_reaped(
    tmp_path, monkeypatch, sleeper
):
    """AC-4: the group, not just the recorded pid — a wrapper's children must not orphan."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "dddddddd-0000-4000-8000-0000000000d4"
    leader, member = sleeper(members=1)
    assert os.getpgid(member) == os.getpgid(leader) != os.getpgrp()
    _stop(leader)
    _write_registry(tmp_path, leader, session_id, proc_start=_proc_start(leader), name="myproject-3")

    _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert _wait_for(lambda: not _present(leader)), f"leader {leader} survived in state {_state(leader)}"
    assert _wait_for(lambda: not _present(member)), f"group member {member} was left orphaned"


def test_given_stopped_session_record_when_process_is_ended_then_record_is_pruned(tmp_path, monkeypatch, sleeper):
    """AC-7: the record must not be left behind to be re-interpreted later."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "eeeeeeee-0000-4000-8000-0000000000e5"
    (pid,) = sleeper()
    _stop(pid)
    entry = _write_registry(tmp_path, pid, session_id, proc_start=_proc_start(pid), name="myproject-3")

    _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert not entry.exists()


# --- negative constraints: nothing else may be touched -------------------------


def test_given_stopped_process_of_another_session_when_liveness_checked_then_left_alone(tmp_path, monkeypatch, sleeper):
    """Reclamation is scoped to the session being resumed, not a fleet-wide reaper.

    Every ``ai c`` launch walks the whole registry.  Ending someone else's
    abandoned process from that walk would turn a resume into a sweep.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (pid,) = sleeper()
    _stop(pid)
    entry = _write_registry(
        tmp_path, pid, "ffffffff-0000-4000-8000-0000000000f6", proc_start=_proc_start(pid), name="myproject-9"
    )

    resumed = "11111111-0000-4000-8000-000000000117"
    assert _cc_session_is_live(Path(f"/x/{resumed}.jsonl")) == (False, None)

    assert _present(pid) and _state(pid) == "T", "another session's stopped process must survive"
    assert entry.exists(), "another session's record must survive"


def test_given_stopped_session_in_the_callers_own_process_group_when_reclaimed_then_group_is_not_signalled(
    tmp_path, monkeypatch, sleeper
):
    """A group signal aimed at the caller's own group would kill the launcher itself.

    ``os.killpg`` is replaced by a recorder for this test only — a real group
    signal here would take down the test runner, which is the failure this
    asserts against.  The pid-scoped fallback still goes through real signals,
    and the group path itself is exercised for real by the AC-4 test above.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "22222222-0000-4000-8000-000000000228"
    leader, sibling = sleeper(members=1, detach=False)
    assert os.getpgid(leader) == os.getpgrp(), "sleeper should share the caller's process group"
    _stop(leader)
    _write_registry(tmp_path, leader, session_id, proc_start=_proc_start(leader), name="myproject-3")

    group_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: group_signals.append((pgid, sig)))

    _cc_session_is_live(Path(f"/x/{session_id}.jsonl"))

    assert group_signals == [], f"signalled the caller's own process group: {group_signals}"
    assert _wait_for(lambda: not _present(leader)), f"leader {leader} survived in state {_state(leader)}"
    assert _present(sibling), "a sibling sharing the caller's process group must survive"


# --- AC-2: the launcher resumes the session instead of naming a new one ---------


def test_given_stopped_session_when_bare_claude_launches_then_it_continues_and_says_what_it_found(
    tmp_path, monkeypatch, sleeper, capsys
):
    """The operator-visible symptom: ``--continue`` is dropped and a fresh session starts."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    session_id = "33333333-0000-4000-8000-000000000339"
    transcript = _write_transcript(_cc_project_dir(target), session_id, "myproject-3")
    os.utime(transcript, (1, 1))
    (pid,) = sleeper()
    _stop(pid)
    _write_registry(tmp_path, pid, session_id, proc_start=_proc_start(pid), name="myproject-3")

    argv = _bare_engine_command("c", "myproject-3", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" in argv
    stderr = capsys.readouterr().err
    assert "still running" not in stderr
    assert f"pid {pid}" in stderr, "the launcher must say what it found and what it did"
