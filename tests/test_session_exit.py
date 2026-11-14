"""Durable exit only tears down identities belonging to the requested run."""

import json
import signal
import subprocess
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import psutil
import pytest
from process_ownership import reap, spawn_owned_python, spawn_owned_sleeper

from ai_cli import session_exit as exit_lib
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state


@pytest.fixture
def running(tmp_path):
    state.register(
        "c-session-1",
        recipe={"argv": ["ai", "c"], "cwd": str(tmp_path)},
        host="host-a",
    )
    generation = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=generation, sequence=1)
    return generation


@pytest.fixture
def child():
    proc = spawn_owned_sleeper()
    try:
        yield proc
    finally:
        reap(proc)


def identity(proc):
    return exit_lib.ProcessIdentity(proc.pid, psutil.Process(proc.pid).create_time())


def record():
    return registry.load_registry()["sessions"][0]


def finish(generation, **kwargs):
    return exit_lib.exit_session("c-session-1", host="host-a", generation=generation, timeout=0.1, **kwargs)


def test_running_when_exited_then_state_precedes_every_signal(running, child, monkeypatch):
    original = psutil.Process.send_signal
    seen = []

    def observe(proc, sig):
        row = record()
        assert row["run_state"] == "exited"
        assert row["changed_at"] == row["ended_at"]
        assert row["exit"] == row["supervisor_exit"]
        assert row["exit"]["cause"] == "manual_exit"
        assert row["transition_seq"] > 1
        seen.append(sig)
        return original(proc, sig)

    monkeypatch.setattr(psutil.Process, "send_signal", observe)
    assert finish(running, agent=identity(child)).complete
    assert seen
    assert not state.mark_running("c-session-1", generation=running, sequence=10**30)
    child.wait(timeout=2)


def test_write_failure_when_exiting_then_no_signal_and_recovery_evidence(running, child, monkeypatch):
    write = registry._write

    def fail(path, doc):
        if path == registry.registry_path():
            raise OSError("storage unavailable")
        write(path, doc)

    monkeypatch.setattr(registry, "_write", fail)
    result = finish(running, agent=identity(child))
    assert not result.registry_committed and not result.complete
    assert child.poll() is None
    saved = json.loads(state.tombstone_path("c-session-1", "host-a").read_text())
    assert saved["record"]["exit"]["cause"] == "manual_exit"
    monkeypatch.setattr(registry, "_write", write)
    assert finish(running, agent=identity(child)).complete


def test_reused_identity_when_exiting_then_replacement_survives(running, child):
    result = finish(running, agent=replace(identity(child), started_at=1.0))
    assert not result.complete
    assert child.poll() is None
    assert "identity" in " ".join(result.errors)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stop and continue signals")
def test_stopped_target_when_exiting_then_cont_precedes_term(running, child, monkeypatch):
    proc = psutil.Process(child.pid)
    proc.suspend()
    deadline = time.monotonic() + 15
    while proc.status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
        time.sleep(0.01)
    # Fail here, not later: a loaded runner can be slow to report the stop, and a target that
    # never looked stopped would (correctly) get no SIGCONT, which reads as a code failure.
    assert proc.status() == psutil.STATUS_STOPPED
    original = psutil.Process.send_signal
    seen = []

    def observe(proc, sig):
        seen.append(sig)
        return original(proc, sig)

    monkeypatch.setattr(psutil.Process, "send_signal", observe)
    assert finish(running, agent=identity(child)).complete
    assert seen[:2] == [signal.SIGCONT, signal.SIGTERM]
    child.wait(timeout=2)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stop and continue signals")
def test_stopped_target_whose_status_reads_running_when_exiting_then_it_is_continued_and_ends(
    running, child, monkeypatch
):
    # macOS CI once read a stopped target's status as not stopped inside the exit, so the
    # target got SIGTERM alone and stayed stopped. The exit must not depend on that read.
    proc = psutil.Process(child.pid)
    proc.suspend()
    deadline = time.monotonic() + 15
    while proc.status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
        time.sleep(0.01)
    assert proc.status() == psutil.STATUS_STOPPED
    original = psutil.Process.send_signal
    seen = []

    def observe(proc, sig):
        seen.append(sig)
        return original(proc, sig)

    real_status = psutil.Process.status

    def misread(proc):
        status = real_status(proc)
        return psutil.STATUS_SLEEPING if status == psutil.STATUS_STOPPED else status

    monkeypatch.setattr(psutil.Process, "send_signal", observe)
    monkeypatch.setattr(psutil.Process, "status", misread)
    assert finish(running, agent=identity(child)).complete
    assert seen[:2] == [signal.SIGCONT, signal.SIGTERM]
    assert child.wait(timeout=5) == -signal.SIGTERM


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX TERM resistance")
def test_survivor_when_exiting_then_identity_verified_before_kill(running):
    proc = spawn_owned_python(
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)",
        stdout=subprocess.PIPE,
    )
    try:
        assert proc.stdout.readline() == b"ready\n"
        assert finish(running, agent=identity(proc)).complete
        assert proc.wait(timeout=2) == -signal.SIGKILL
    finally:
        proc.stdout.close()
        reap(proc)


def test_completed_exit_when_repeated_then_cleanup_is_idempotent(running, child):
    target = identity(child)
    assert finish(running, agent=target).complete
    child.wait(timeout=2)
    first = record()
    assert finish(running, agent=target).complete
    assert record()["transition_seq"] == first["transition_seq"]
    assert record()["ended_at"] == first["ended_at"]


def test_tmux_session_when_exited_then_fenced_id_used_without_server_signal(running, child, monkeypatch):
    commands = []

    def tmux(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, f"$42|token-1|{child.pid}\n", "")

    monkeypatch.setattr(exit_lib.subprocess, "run", tmux)
    tmux_id = exit_lib.TmuxIdentity("$42", "token-1")
    assert finish(running, tmux=tmux_id).complete
    assert child.poll() is None
    assert commands[-1][1] == "if-shell"
    assert "kill-session -t '$42'" in commands[-1]
    assert all("kill-server" not in arg for args in commands for arg in args)


def test_tmux_server_as_target_when_exiting_then_no_signal(running, child, monkeypatch):
    monkeypatch.setattr(
        exit_lib.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, f"$42|token-1|{child.pid}\n", ""),
    )
    assert not finish(running, agent=identity(child), tmux=exit_lib.TmuxIdentity("$42", "token-1")).complete
    assert child.poll() is None


@pytest.mark.parametrize(("host", "generation"), [("host-b", None), ("host-a", "stale")])
def test_foreign_run_when_exiting_then_no_signal(running, child, host, generation):
    result = exit_lib.exit_session(
        "c-session-1", host=host, generation=generation or running, agent=identity(child), timeout=0.1
    )
    assert not result.complete
    assert child.poll() is None
    assert record()["run_state"] == "running"


def test_failed_cleanup_when_retried_then_saved_targets_are_resumed(running, child, monkeypatch):
    original = exit_lib._cleanup
    with monkeypatch.context() as patcher:
        patcher.setattr(exit_lib, "_cleanup", lambda *args: ["temporary failure"])
        result = finish(running, agent=identity(child))
    assert result.registry_committed and not result.complete
    assert state.exit_teardown(record())["errors"] == ["temporary failure"]
    assert child.poll() is None
    assert exit_lib._cleanup is original
    assert finish(running).complete
    child.wait(timeout=2)
    assert state.exit_teardown(record())["errors"] == []


def test_changed_identity_when_escalating_then_kill_is_refused(running, child, monkeypatch):
    original = exit_lib._process
    calls = 0

    def changed(target):
        nonlocal calls
        calls += 1
        # Initial validation, TERM check, wait check, then escalation.
        if calls >= 4:
            raise ValueError("process identity mismatch")
        return original(target)

    monkeypatch.setattr(exit_lib, "_process", changed)
    monkeypatch.setattr(psutil.Process, "send_signal", lambda *args: None)
    result = exit_lib.exit_session("c-session-1", host="host-a", generation=running, agent=identity(child), timeout=0)
    assert not result.complete
    assert child.poll() is None
    assert "identity mismatch" in " ".join(result.errors)


def test_tmux_ownership_changed_when_exiting_then_no_signal_or_removal(running, child, monkeypatch):
    calls = []

    def changed(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "$42|replacement|123\n", "")

    monkeypatch.setattr(exit_lib.subprocess, "run", changed)
    assert not finish(running, agent=identity(child), tmux=exit_lib.TmuxIdentity("$42", "token-1")).complete
    assert child.poll() is None
    assert len(calls) == 1


def test_tmux_absent_when_repeated_then_cleanup_completes(running, monkeypatch):
    monkeypatch.setattr(
        exit_lib.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "can't find session: $42"),
    )
    assert finish(running, tmux=exit_lib.TmuxIdentity("$42", "token-1")).complete
    assert finish(running).complete


def test_new_generation_when_old_exit_retried_then_no_signal(running, child):
    target = identity(child)
    state.begin("c-session-1")
    assert not finish(running, agent=target).complete
    assert child.poll() is None


def test_supervisor_and_agent_when_exited_then_both_terminate(running, child):
    supervisor = spawn_owned_sleeper()
    try:
        assert finish(running, agent=identity(child), supervisor=identity(supervisor)).complete
        child.wait(timeout=2)
        supervisor.wait(timeout=2)
    finally:
        reap(supervisor)


def test_stale_agent_when_exiting_then_supervisor_is_preserved(running, child):
    supervisor = spawn_owned_sleeper()
    try:
        assert not finish(
            running, agent=replace(identity(child), started_at=1.0), supervisor=identity(supervisor)
        ).complete
        assert child.poll() is None
        assert supervisor.poll() is None
    finally:
        reap(supervisor)


def test_wind_down_requested_when_manually_exited_then_manual_cause_wins(running):
    registry._mutate(lambda doc: state.request_wind_down(doc["sessions"][0]) or True)
    assert finish(running).complete
    assert record()["exit"]["cause"] == "manual_exit"
    assert not state.mark_exited("c-session-1", "unknown", {}, generation=running)
    generation = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=generation)


def test_generation_changed_before_cleanup_when_exiting_then_no_signal(running, child, monkeypatch):
    original = state.request_manual_exit

    def change_generation(*args, **kwargs):
        saved = original(*args, **kwargs)
        state.begin("c-session-1")
        return saved

    monkeypatch.setattr(state, "request_manual_exit", change_generation)
    result = finish(running, agent=identity(child))
    assert result.registry_committed and not result.complete
    assert child.poll() is None


def test_tmux_process_without_session_target_when_exited_then_server_is_preserved(running, child, monkeypatch):
    monkeypatch.setattr(psutil.Process, "name", lambda proc: "tmux: server")
    assert not finish(running, agent=identity(child)).complete
    assert child.poll() is None


def test_lock_failure_when_exiting_then_failure_is_returned_without_signal(running, child, monkeypatch):
    from portalocker.exceptions import LockException

    def locked(*args, **kwargs):
        raise LockException("registry is busy")

    monkeypatch.setattr(registry, "_mutate", locked)
    result = finish(running, agent=identity(child))
    assert not result.registry_committed and not result.complete
    assert child.poll() is None


def test_windows_target_when_exited_then_only_portable_signals_are_used(running, child, monkeypatch):
    original = psutil.Process.send_signal
    seen = []

    def observe(proc, sig):
        seen.append(sig)
        return original(proc, sig)

    monkeypatch.setattr(exit_lib, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(psutil.Process, "send_signal", observe)
    assert finish(running, agent=identity(child)).complete
    assert seen == [signal.SIGTERM]
    child.wait(timeout=2)


def test_reattached_record_when_cleanup_retried_then_fence_and_targets_survive(running, child, monkeypatch):
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

    with monkeypatch.context() as patcher:
        patcher.setattr(exit_lib, "_cleanup", lambda *args: ["temporary failure"])
        assert not finish(running, agent=identity(child)).complete
    registry.record_launch(
        kind="local",
        name="c-session-1",
        relaunch_argv=["ai", "c"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=1,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert not state.mark_running("c-session-1", generation=running, sequence=10**30)
    assert finish(running).complete
    child.wait(timeout=2)
    assert record()["exit"] == record()["supervisor_exit"]


def test_safety_recovered_when_cleanup_retried_then_saved_targets_are_resumed(running, child, monkeypatch):
    write = registry._write

    def fail(path, doc):
        if path == registry.registry_path():
            raise OSError("storage unavailable")
        write(path, doc)

    with monkeypatch.context() as patcher:
        patcher.setattr(registry, "_write", fail)
        assert not finish(running, agent=identity(child)).registry_committed
    state.reconcile_safety()
    assert not state.mark_running("c-session-1", generation=running, sequence=10**30)
    assert finish(running).complete
    child.wait(timeout=2)


def test_empty_tmux_probe_when_exiting_then_unknown_ownership_refuses_signals(running, child, monkeypatch):
    monkeypatch.setattr(exit_lib.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    assert not finish(running, agent=identity(child), tmux=exit_lib.TmuxIdentity("$42", "token-1")).complete
    assert child.poll() is None
