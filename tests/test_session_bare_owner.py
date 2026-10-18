"""Bare exec preserves process identity and has an independent durable exit owner."""

import io
import subprocess
from unittest.mock import Mock

import psutil
import pytest
from process_ownership import reap, spawn_owned_python

from ai_cli import session_bare_owner as owner
from ai_cli import session_launch_state as launch
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state


@pytest.fixture
def generation(tmp_path):
    state.register("c-session-1", recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=generation)
    return generation


def test_given_bare_process_when_it_exits_then_owner_records_manual_exit(generation, monkeypatch):
    process = spawn_owned_python("import sys; sys.stdin.read()", stdin=subprocess.PIPE)
    started = psutil.Process(process.pid).create_time()
    monkeypatch.setattr(owner.sys, "stdin", Mock(buffer=io.BytesIO()))
    try:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=10)
        owner.observe(process.pid, started, "c-session-1", generation)
    finally:
        reap(process)
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert state.cause(record, "next-boot") == "manual_exit"


def test_given_bare_handoff_when_exec_returns_then_command_is_unchanged_and_owner_cancels(generation, monkeypatch):
    monitor = Mock(stdin=io.BytesIO())
    monkeypatch.setattr(launch.subprocess, "Popen", Mock(return_value=monitor))
    delegate = Mock(return_value=7)
    command = ["direnv", "exec", "/work/myproject", "example-command"]
    assert launch.handoff("c-session-1", delegate)("direnv", command) == 7
    delegate.assert_called_once_with("direnv", command)
    monitor.wait.assert_called_once_with()
    assert monitor.stdin.closed
    record = registry.load_registry()["sessions"][0]
    assert record["boot_recipe"]["command"] == command
    assert record["run_state"] == "exited"
    assert record["supervisor_exit"]["cause"] == "manual_exit"


def test_given_failed_final_write_when_owner_settles_then_retry_preserves_exit(generation, monkeypatch):
    mark = state.mark_exited
    attempts = []

    def retry(*args, **kwargs):
        attempts.append(args)
        return False if len(attempts) == 1 else mark(*args, **kwargs)

    monkeypatch.setattr(state, "mark_exited", retry)
    monkeypatch.setattr(owner.time, "sleep", lambda seconds: None)
    owner.settle("c-session-1", generation)
    assert len(attempts) == 2
    assert registry.load_registry()["sessions"][0]["run_state"] == "exited"


def test_given_owner_without_stdin_when_handoff_returns_then_final_exit_is_persisted(generation, monkeypatch):
    monitor = Mock(stdin=None)
    monkeypatch.setattr(launch.subprocess, "Popen", Mock(return_value=monitor))
    delegate = Mock(return_value=7)
    command = ["example-command"]
    assert launch.handoff("c-session-1", delegate)(command[0], command) == 7
    monitor.wait.assert_called_once_with()
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert state.cause(record, "next-boot") == "manual_exit"


def test_given_new_generation_when_old_owner_settles_then_running_state_is_preserved(generation):
    current = state.begin("c-session-1")
    owner.settle("c-session-1", generation)
    record = registry.load_registry()["sessions"][0]
    assert record["generation"] == current
    assert record["run_state"] == "running"


def test_given_cancelled_handoff_when_owner_observes_then_no_exit_is_recorded(generation, monkeypatch):
    monkeypatch.setattr(owner.sys, "stdin", Mock(buffer=io.BytesIO(b"cancel")))
    owner.observe(0, 0, "c-session-1", generation)
    assert registry.load_registry()["sessions"][0]["run_state"] == "running"


def test_given_owner_cannot_start_when_bare_launch_attempted_then_agent_is_refused(generation, monkeypatch):
    monkeypatch.setattr(launch.subprocess, "Popen", Mock(side_effect=OSError("process unavailable")))
    delegate = Mock()
    with pytest.raises(OSError, match="process unavailable"):
        launch.handoff("c-session-1", delegate)("example-command", ["example-command"])
    delegate.assert_not_called()
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert state.cause(record, "next-boot") == "unknown"


def test_given_startup_write_fails_when_bare_launch_attempted_then_agent_is_refused(generation, monkeypatch):
    monkeypatch.setattr(state, "mark_running", lambda *args, **kwargs: False)
    delegate = Mock()
    with pytest.raises(RuntimeError, match="startup persistence failed"):
        launch.handoff("c-session-1", delegate)("example-command", ["example-command"])
    delegate.assert_not_called()
