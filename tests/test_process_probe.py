"""Session reclamation must work on macOS and Windows, not only on Linux.

The defect these tests pin was two coupled halves:

* **Linux-only inspection.** Everything the launcher asked about a recorded pid --
  its state, and whether it is still the process the record was written for -- was
  read straight out of ``/proc/<pid>/stat``. macOS and Windows have no procfs, so
  every reader answered None, the "is this really that process" gate could never be
  satisfied, and reclamation returned False before touching anything. No error was
  raised: the feature was a silent, permanent no-op on two of three platforms.
* **Signals that do not exist.** The escalation sent ``signal.SIGCONT`` and
  ``signal.SIGKILL``, neither of which Windows defines (measured: only ``SIGTERM``
  of the three exists there). Reaching that code would have raised
  ``AttributeError`` -- so fixing the first half alone converts a silent no-op into
  a hard crash, which is why one change has to answer both.

The tests below are arranged as inspection, identity, termination, resolution, then
the whole registry path end to end. The end-to-end section forces the no-procfs
implementation on *any* host (by pointing the procfs root at somewhere that does not
exist), so the platform whose reclamation was dead is exercised on every machine
that runs this suite rather than only on a Mac or a Windows box.

Every process these tests reason about is real, and every termination is real. The
one thing faked is ``psutil.Process.status`` reporting ``stopped``: Windows has no
job-control stop, and psutil only answers ``stopped`` there when it can see every
thread suspended -- a suspended sleeping process still read ``running`` when
measured on Windows 11. That single value is the system boundary; the process, the
registry record, the escalation and the prune are all genuine.
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

import psutil
import pytest

from ai_cli import process_probe
from ai_cli.main import _cc_record_liveness, _cc_session_is_live
from ai_cli.process_probe import ProcfsProbe, PsutilProbe, StartTimeMatch, probe_for

#: Long enough that no test races the process's own exit, short enough that one
#: escaping a killed run cannot linger for the rest of the day.
_SLEEP_SECONDS = 60

_SLEEPER = f"import time; time.sleep({_SLEEP_SECONDS})"

#: A sleeper that wraps a second one, the shape a session wrapper has: the pid in the
#: registry is the leader, and its child must be reaped with it rather than orphaned.
_WRAPPER = f"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", {_SLEEPER!r}])
sys.stdout.write(str(child.pid) + "\\n")
sys.stdout.flush()
time.sleep({_SLEEP_SECONDS})
"""

#: A Windows FILETIME counts 100-nanosecond intervals from 1601-01-01 UTC.
_FILETIME_TICKS_PER_SECOND = 10_000_000
_FILETIME_EPOCH_OFFSET_SECONDS = 11_644_473_600


def _filetime(epoch_seconds: float) -> str:
    """Render ``epoch_seconds`` the way a real record does on Windows.

    Measured on Windows 11: ``procStart`` is a decimal *string* holding a FILETIME,
    which agreed with ``psutil.create_time()`` to under a microsecond for every live
    session on the host.
    """
    return str(round((epoch_seconds + _FILETIME_EPOCH_OFFSET_SECONDS) * _FILETIME_TICKS_PER_SECOND))


def _wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _write_stat(proc_dir: Path, pid: int, state: str = "S", starttime: int = 777) -> None:
    """A synthetic ``/proc/<pid>/stat`` whose comm carries spaces and parens."""
    (proc_dir / str(pid)).mkdir(parents=True, exist_ok=True)
    fields = [state] + [str(n) for n in range(4, 22)] + [str(starttime)]
    (proc_dir / str(pid) / "stat").write_text(f"{pid} (claude (worker) 1) " + " ".join(fields) + "\n")


def _write_registry(home: Path, pid: int, session_id: str, proc_start: object = None, name: str = "") -> Path:
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


@pytest.fixture
def sleeper():
    """Spawn real sleeping processes; guarantee every one is ended and reaped.

    The leader is returned as its ``Popen``, which gives each test an exit oracle
    (``poll()``) that owes nothing to the code under test -- and reaps it, so a
    terminated child never lingers as a zombie that the next assertion misreads.
    """
    leaders: list[subprocess.Popen] = []
    wrapped: list[int] = []

    def spawn(with_child: bool = False) -> tuple[subprocess.Popen, int]:
        if not with_child:
            proc = subprocess.Popen([sys.executable, "-c", _SLEEPER])
            leaders.append(proc)
            assert _wait_for(lambda: psutil.pid_exists(proc.pid)), "sleeper never started"
            return proc, 0
        proc = subprocess.Popen([sys.executable, "-c", _WRAPPER], stdout=subprocess.PIPE, text=True)
        leaders.append(proc)
        assert proc.stdout is not None
        child_pid = int(proc.stdout.readline())
        proc.stdout.close()
        wrapped.append(child_pid)
        return proc, child_pid

    yield spawn

    for pid in wrapped:
        with contextlib.suppress(psutil.Error, OSError):
            psutil.Process(pid).kill()
    for proc in leaders:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=15)


@pytest.fixture
def no_procfs(monkeypatch, tmp_path):
    """Resolve every probe to psutil, as on macOS and Windows, whatever the host."""
    monkeypatch.setattr(process_probe, "_procfs_root", lambda: tmp_path / "no-procfs-here")


def _report_stopped(monkeypatch, pid: int) -> None:
    """Make psutil report ``pid`` as stopped for as long as it is really running.

    The passthrough matters: once the process has actually exited, the real status
    (or the real ``NoSuchProcess``) must come back through, or the termination's own
    confirmation would be reading the fake and could never see it end.
    """
    real_status = psutil.Process.status

    def status(self):
        actual = real_status(self)
        if self.pid == pid and actual not in PsutilProbe.ended_states:
            return psutil.STATUS_STOPPED
        return actual

    monkeypatch.setattr(psutil.Process, "status", status)


# --- inspection: presence, state, and what those states mean --------------------


def test_given_a_running_process_when_probed_without_procfs_then_present_and_not_ended(sleeper):
    proc, _ = sleeper()
    probe = PsutilProbe()

    assert probe.is_present(proc.pid) is True
    assert probe.has_ended(proc.pid) is False
    assert probe.is_abandoned(proc.pid) is False
    assert probe.state(proc.pid) in {psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING, psutil.STATUS_IDLE}


def test_given_an_exited_process_when_probed_without_procfs_then_it_has_ended(sleeper):
    proc, _ = sleeper()
    proc.kill()
    proc.wait(timeout=15)

    assert PsutilProbe().has_ended(proc.pid) is True


def test_given_a_stopped_process_when_probed_without_procfs_then_abandoned(monkeypatch, sleeper):
    proc, _ = sleeper()
    _report_stopped(monkeypatch, proc.pid)

    probe = PsutilProbe()
    assert probe.state(proc.pid) == psutil.STATUS_STOPPED
    assert probe.is_abandoned(proc.pid) is True
    assert probe.has_ended(proc.pid) is False, "a stopped process is present, not gone"


def test_given_psutil_raises_zombie_process_when_state_is_read_then_it_reads_as_a_zombie(monkeypatch):
    """macOS can raise rather than answer for an exited-but-unreaped process."""

    def _raise(self):
        raise psutil.ZombieProcess(self.pid)

    monkeypatch.setattr(psutil.Process, "status", _raise)
    probe = PsutilProbe()

    assert probe.state(os.getpid()) == psutil.STATUS_ZOMBIE
    assert probe.has_ended(os.getpid()) is True


def test_given_psutil_denies_access_when_state_is_read_then_the_state_is_unknown(monkeypatch):
    """A process owned by somebody else must degrade, not raise -- and not read as gone."""

    def _deny(self):
        raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil.Process, "status", _deny)
    probe = PsutilProbe()

    assert probe.state(os.getpid()) is None
    assert probe.has_ended(os.getpid()) is False


def test_given_procfs_stat_with_parens_in_comm_when_probed_then_the_state_is_field_three(tmp_path):
    """Splitting the whole line shifts every field for a process named ``claude (worker) 1``."""
    _write_stat(tmp_path, 4242, state="T")
    probe = ProcfsProbe(tmp_path)

    assert probe.state(4242) == "T"
    assert probe.is_abandoned(4242) is True
    assert probe.has_ended(4242) is False


def test_given_a_procfs_zombie_when_probed_then_it_has_ended(tmp_path):
    _write_stat(tmp_path, 4242, state="Z")

    assert ProcfsProbe(tmp_path).has_ended(4242) is True


def test_given_a_running_procfs_process_when_probed_then_neither_ended_nor_abandoned(tmp_path):
    """Control for the two above: the same parsing must still recognise a running process."""
    _write_stat(tmp_path, 4242, state="S")
    probe = ProcfsProbe(tmp_path)

    assert probe.has_ended(4242) is False
    assert probe.is_abandoned(4242) is False


def test_given_no_procfs_entry_when_probed_then_absent_and_ended(tmp_path):
    probe = ProcfsProbe(tmp_path)

    assert probe.is_present(4242) is False
    assert probe.has_ended(4242) is True
    assert probe.state(4242) is None


# --- identity: is this pid still the process the record was written for? --------


def test_given_a_windows_filetime_string_when_matched_against_the_live_process_then_identified(sleeper):
    """The measured shape of a real Windows record: a decimal string holding a FILETIME."""
    proc, _ = sleeper()
    recorded = _filetime(psutil.Process(proc.pid).create_time())

    assert PsutilProbe().start_time_match(proc.pid, recorded) is StartTimeMatch.MATCH


def test_given_epoch_seconds_when_matched_against_the_live_process_then_identified(sleeper):
    proc, _ = sleeper()
    recorded = round(psutil.Process(proc.pid).create_time())

    assert PsutilProbe().start_time_match(proc.pid, recorded) is StartTimeMatch.MATCH


def test_given_epoch_milliseconds_when_matched_against_the_live_process_then_identified(sleeper):
    """The unit these same records use for ``startedAt``."""
    proc, _ = sleeper()
    recorded = round(psutil.Process(proc.pid).create_time() * 1000)

    assert PsutilProbe().start_time_match(proc.pid, recorded) is StartTimeMatch.MATCH


def test_given_a_start_time_from_an_hour_earlier_when_matched_then_unproven(sleeper):
    """A recycled pid: the same number, a different process. It must not be identified."""
    proc, _ = sleeper()
    recorded = _filetime(psutil.Process(proc.pid).create_time() - 3600)

    assert PsutilProbe().start_time_match(proc.pid, recorded) is StartTimeMatch.UNPROVEN


def test_given_no_recorded_start_time_when_matched_then_unrecorded(sleeper):
    proc, _ = sleeper()

    assert PsutilProbe().start_time_match(proc.pid, None) is StartTimeMatch.UNRECORDED


def test_given_a_non_numeric_recorded_start_time_when_matched_then_unrecorded(sleeper):
    proc, _ = sleeper()

    assert PsutilProbe().start_time_match(proc.pid, "not-a-time") is StartTimeMatch.UNRECORDED


def test_given_a_boolean_recorded_start_time_when_matched_then_unrecorded(sleeper):
    """``True`` is an ``int`` in Python; it is not a clock reading."""
    proc, _ = sleeper()

    assert PsutilProbe().start_time_match(proc.pid, True) is StartTimeMatch.UNRECORDED


def test_given_a_procfs_tick_count_when_matched_without_procfs_then_unrecorded(sleeper):
    """A Linux ``starttime`` is in boot ticks, and means nothing to a psutil clock.

    Answering ``UNRECORDED`` rather than guessing keeps the launcher's historical
    refusal instead of authorising a termination from a number in an unknown unit.
    """
    proc, _ = sleeper()

    assert PsutilProbe().start_time_match(proc.pid, 777) is StartTimeMatch.UNRECORDED


def test_given_a_pid_that_no_longer_exists_when_matched_then_unproven(sleeper):
    proc, _ = sleeper()
    recorded = _filetime(psutil.Process(proc.pid).create_time())
    proc.kill()
    proc.wait(timeout=15)

    assert PsutilProbe().start_time_match(proc.pid, recorded) is StartTimeMatch.UNPROVEN


def test_given_matching_procfs_start_ticks_when_matched_then_identified(tmp_path):
    _write_stat(tmp_path, 4242, starttime=777)

    assert ProcfsProbe(tmp_path).start_time_match(4242, 777) is StartTimeMatch.MATCH


def test_given_different_procfs_start_ticks_when_matched_then_unproven(tmp_path):
    _write_stat(tmp_path, 4242, starttime=999)

    assert ProcfsProbe(tmp_path).start_time_match(4242, 777) is StartTimeMatch.UNPROVEN


def test_given_a_procfs_start_time_as_a_string_when_matched_then_unrecorded(tmp_path):
    """``/proc`` ticks are compared as exact integers; anything else cannot refute."""
    _write_stat(tmp_path, 4242, starttime=777)

    assert ProcfsProbe(tmp_path).start_time_match(4242, "777") is StartTimeMatch.UNRECORDED


@pytest.mark.parametrize("stat_body", ["", "4242 (claude) S", "4242 claude S 1 2 3", "no-parens-at-all"])
def test_given_a_malformed_procfs_stat_when_matched_then_unproven(tmp_path, stat_body):
    (tmp_path / "4242").mkdir()
    (tmp_path / "4242" / "stat").write_text(stat_body)

    assert ProcfsProbe(tmp_path).start_time_match(4242, 777) is StartTimeMatch.UNPROVEN


# --- termination without procfs, and without POSIX-only signals -----------------


def test_given_a_wrapped_process_when_ended_without_procfs_then_the_whole_tree_goes(sleeper):
    """A wrapper's child must be reaped with it, not left running with no parent."""
    proc, child_pid = sleeper(with_child=True)

    assert PsutilProbe().end_process(proc.pid) is True
    assert proc.poll() is not None
    assert _wait_for(lambda: not psutil.pid_exists(child_pid)), f"wrapped pid {child_pid} was orphaned"


def test_given_no_sigkill_or_sigcont_when_a_process_is_ended_then_it_still_ends(sleeper, monkeypatch):
    """The Windows half of the defect: neither signal exists there, so neither is used.

    Both names are removed while the escalation runs, so reintroducing a
    ``signal.SIGKILL`` into the no-procfs path fails here with the same
    ``AttributeError`` a Windows host would have raised.
    """
    proc, _ = sleeper()
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    monkeypatch.delattr(signal, "SIGCONT", raising=False)

    ended = PsutilProbe().end_process(proc.pid)
    # Restored before leaving the test: on POSIX ``Popen.kill`` reads
    # ``signal.SIGKILL`` itself, so the fixture's own cleanup needs it back.
    monkeypatch.undo()

    assert ended is True
    assert proc.poll() is not None


def test_given_a_process_that_ignores_the_graceful_request_when_ended_then_it_is_killed(sleeper, monkeypatch):
    """The escalation must not stop at the polite request, which can change nothing."""
    proc, _ = sleeper()
    monkeypatch.setattr(psutil.Process, "terminate", lambda self: None)

    assert PsutilProbe().end_process(proc.pid, timeout=0.5) is True
    assert proc.poll() is not None


def test_given_a_pid_that_is_already_gone_when_ended_then_it_reports_ended(sleeper):
    proc, _ = sleeper()
    proc.kill()
    proc.wait(timeout=15)

    assert PsutilProbe().end_process(proc.pid, timeout=0.5) is True


def test_given_the_recorded_pid_is_the_caller_when_ended_then_it_is_not_signalled(sleeper, monkeypatch):
    """``ai c`` can be launched from inside the session being reclaimed.

    The launcher is impersonated by pointing ``os.getpid`` at the sleeper, so the
    guard can be driven through the real entry point without the test asking a
    process to kill the pytest worker it is running in.
    """
    proc, _ = sleeper()
    monkeypatch.setattr(os, "getpid", lambda: proc.pid)

    assert PsutilProbe().end_process(proc.pid, timeout=0.3) is False
    assert proc.poll() is None, "the caller's own process must never be signalled"


def test_given_a_platform_without_procfs_when_a_manual_hint_is_asked_for_then_it_is_ascii(sleeper):
    """Printed to a console, so it must survive a code page that has no dashes."""
    hint = PsutilProbe().manual_end_hint(4242)

    assert "4242" in hint
    assert hint.isascii()


def test_given_procfs_when_a_manual_hint_is_asked_for_then_it_continues_before_terminating(tmp_path):
    """The remedy has to include the continue: a stopped process cannot act otherwise."""
    hint = ProcfsProbe(tmp_path).manual_end_hint(4242)

    assert "kill -CONT 4242" in hint
    assert hint.isascii()


# --- one place decides which implementation answers -----------------------------


def test_given_procfs_is_absent_when_a_probe_is_resolved_then_psutil_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(process_probe, "_procfs_root", lambda: tmp_path / "absent")

    assert isinstance(probe_for(), PsutilProbe)


def test_given_procfs_is_present_when_a_probe_is_resolved_then_procfs_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(process_probe, "_procfs_root", lambda: tmp_path)
    _write_stat(tmp_path, 4242, state="T")

    probe = probe_for()

    assert isinstance(probe, ProcfsProbe)
    assert probe.state(4242) == "T", "the resolved probe must read the procfs it was given"


def test_given_an_injected_proc_dir_when_a_probe_is_resolved_then_it_reads_that_directory(tmp_path):
    _write_stat(tmp_path, 4242, state="T")

    assert probe_for(tmp_path).state(4242) == "T"


def test_given_an_injected_proc_dir_that_does_not_exist_when_a_probe_is_resolved_then_psutil_answers(tmp_path):
    """The rule the pid-liveness check has always applied, kept intact."""
    assert isinstance(probe_for(tmp_path / "absent"), PsutilProbe)


# --- the registry path, end to end, on a platform with no procfs ----------------


def test_given_a_stopped_session_without_procfs_when_liveness_checked_then_it_is_reclaimed(
    tmp_path, monkeypatch, sleeper, no_procfs, capsys
):
    """The defect itself: off Linux this returned "live" and reclaimed nothing, forever.

    Nothing here is mocked except the one status word Windows cannot produce. The
    process, its registry record in the shape a real Windows host writes, the
    termination and the prune are all real.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "aaaaaaaa-0000-4000-8000-0000000000a1"
    proc, _ = sleeper()
    entry = _write_registry(
        tmp_path,
        proc.pid,
        session_id,
        proc_start=_filetime(psutil.Process(proc.pid).create_time()),
        name="myproject-3",
    )
    _report_stopped(monkeypatch, proc.pid)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (False, None)

    assert _wait_for(lambda: proc.poll() is not None), "the abandoned process was never ended"
    assert not entry.exists(), "the stale record must be pruned once the process is gone"
    assert f"pid {proc.pid}" in capsys.readouterr().err


def test_given_a_running_session_without_procfs_when_liveness_checked_then_it_blocks_and_survives(
    tmp_path, monkeypatch, sleeper, no_procfs
):
    """The negative constraint, and the proof that identity matching really matches.

    Without it, "reclaim the abandoned ones" is satisfiable by a probe that answers
    False to everything. It also fails if the FILETIME identity token stops being
    recognised, because an unrecognised one answers "unproven" rather than "live".
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "bbbbbbbb-0000-4000-8000-0000000000b2"
    proc, _ = sleeper()
    entry = _write_registry(
        tmp_path,
        proc.pid,
        session_id,
        proc_start=_filetime(psutil.Process(proc.pid).create_time()),
        name="myproject-4",
    )

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (True, proc.pid)
    assert proc.poll() is None, "a session that is genuinely in use must not be terminated"
    assert entry.exists()


def test_given_a_recycled_pid_without_procfs_when_liveness_checked_then_the_name_is_free(
    tmp_path, monkeypatch, sleeper, no_procfs
):
    """A live pid whose start time is not the record's is a stranger, not this session.

    Off Linux this answered "live" and reserved the session name for as long as the
    unrelated process happened to hold that pid.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "cccccccc-0000-4000-8000-0000000000c3"
    proc, _ = sleeper()
    entry = _write_registry(
        tmp_path,
        proc.pid,
        session_id,
        proc_start=_filetime(psutil.Process(proc.pid).create_time() - 3600),
        name="myproject-5",
    )

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (False, None)
    assert proc.poll() is None, "a process the record cannot claim must never be signalled"
    assert entry.exists(), "only a provably gone pid is pruned"


def test_given_a_stopped_session_that_cannot_be_identified_without_procfs_then_it_is_left_alone(
    tmp_path, monkeypatch, sleeper, no_procfs, capsys
):
    """Killing on a pid alone would eventually kill a stranger that inherited it."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "dddddddd-0000-4000-8000-0000000000d4"
    proc, _ = sleeper()
    entry = _write_registry(
        tmp_path,
        proc.pid,
        session_id,
        proc_start=_filetime(psutil.Process(proc.pid).create_time() - 3600),
        name="myproject-6",
    )
    _report_stopped(monkeypatch, proc.pid)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (False, None)

    assert proc.poll() is None, "an unidentifiable process must survive"
    assert entry.exists()
    assert "cannot prove" in capsys.readouterr().err


def test_given_another_sessions_stopped_process_without_procfs_when_liveness_checked_then_left_alone(
    tmp_path, monkeypatch, sleeper, no_procfs
):
    """Every launch walks the whole registry; ending someone else's process would sweep."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc, _ = sleeper()
    entry = _write_registry(
        tmp_path,
        proc.pid,
        "eeeeeeee-0000-4000-8000-0000000000e5",
        proc_start=_filetime(psutil.Process(proc.pid).create_time()),
        name="myproject-9",
    )
    _report_stopped(monkeypatch, proc.pid)

    resumed = "ffffffff-0000-4000-8000-0000000000f6"
    assert _cc_session_is_live(Path(f"/x/{resumed}.jsonl")) == (False, None)

    assert proc.poll() is None, "another session's stopped process must survive"
    assert entry.exists(), "another session's record must survive"


def test_given_a_dead_pid_without_procfs_when_liveness_checked_then_the_record_is_pruned(
    tmp_path, monkeypatch, sleeper, no_procfs
):
    """The record outlives the process, and nothing else ages it out."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc, _ = sleeper()
    proc.kill()
    proc.wait(timeout=15)
    entry = _write_registry(tmp_path, proc.pid, "11111111-0000-4000-8000-000000000117", proc_start=_filetime(0))

    assert _cc_session_is_live(Path("/x/22222222-0000-4000-8000-000000000228.jsonl")) == (False, None)
    assert not entry.exists()


def test_given_a_stopped_process_without_procfs_when_a_record_is_classified_then_abandoned(
    tmp_path, sleeper, monkeypatch, no_procfs
):
    """The verdict the launcher acts on, isolated from the walk that reclaims it."""
    proc, _ = sleeper()
    _report_stopped(monkeypatch, proc.pid)
    recorded = _filetime(psutil.Process(proc.pid).create_time())

    assert _cc_record_liveness({"pid": proc.pid, "procStart": recorded}) == "abandoned"


def test_given_a_running_process_without_procfs_when_a_record_is_classified_then_live(sleeper, no_procfs):
    """Control for the verdict above, and for the identity comparison behind it."""
    proc, _ = sleeper()
    recorded = _filetime(psutil.Process(proc.pid).create_time())

    assert _cc_record_liveness({"pid": proc.pid, "procStart": recorded}) == "live"


def test_given_a_recycled_pid_without_procfs_when_a_record_is_classified_then_unproven(sleeper, no_procfs):
    proc, _ = sleeper()
    recorded = _filetime(psutil.Process(proc.pid).create_time() - 3600)

    assert _cc_record_liveness({"pid": proc.pid, "procStart": recorded}) == "unproven"
