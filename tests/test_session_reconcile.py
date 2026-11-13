"""Run-state reconciliation through CLI and the existing periodic worker."""

import json
import logging
import os
import signal
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from process_ownership import reap, spawn_owned_sleeper

from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group
from ai_cli.session_migrate import MigrationError, _plan_iterm2
from ai_cli.stale_session_reaper import run_stale_session_reaper

_LOCAL_SESSION_ALIVE = registry._local_session_alive


@pytest.fixture
def stale_record(monkeypatch, tmp_path):
    monkeypatch.setattr(state.config, "load_config", lambda **_kw: {"machine": {"host_id": "host-a"}})
    state.register("c-session-1", recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    state.begin("c-session-1")
    assert state.mark_running("c-session-1")
    registry._mutate(lambda doc: doc["sessions"][0].update(changed_at="2026-01-01T00:00:00Z") or True)
    # External inputs only: fake tmux and a complete empty process table.
    monkeypatch.setattr(registry, "_local_session_alive", lambda _name: False)
    monkeypatch.setattr("ai_cli.session_migrate._tmux_names", list)
    monkeypatch.setattr("ai_cli.session_enrol.inspect_pane", Mock(side_effect=ValueError("no live pane")))
    monkeypatch.setattr("psutil.process_iter", lambda *args, **kwargs: iter(()))
    return registry.load_registry()["sessions"][0]


@pytest.mark.parametrize("entry", ["list", "tick", "worker", "enrol", "stop"])
def test_given_same_boot_missing_session_when_observed_then_exit_and_evidence_are_persisted(
    stale_record, caplog, entry
):
    caplog.set_level(logging.INFO)
    if entry == "worker":

        def stop(seconds):
            assert seconds == 60
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run_stale_session_reaper({}, reaper_factory=lambda *a, **kw: Mock(), sleep=stop)
    else:
        args = {
            "list": ["list", "-j"],
            "tick": ["publish", "--tick"],
            "enrol": ["enrol", "c-session-1"],
            "stop": ["stop", "session-1", "-t", "0"],
        }[entry]
        result = CliRunner().invoke(_cli_group, ["sessions", *args])
        if entry != "enrol":
            assert result.exit_code == 0, result.output
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert record["exit"] == record["supervisor_exit"]
    assert record["exit"]["cause"] == "unknown"
    evidence = record["exit"]["evidence"]
    assert evidence["tmux_alive"] is False and evidence["process_alive"] is False
    assert evidence["checked_at"] == record["ended_at"]
    assert evidence["boot_id"] == stale_record["boot_id"]
    lines = [line for line in caplog.messages if '"event": "session_reconciled"' in line]
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "c-session-1"
    assert json.loads(lines[0])["result"] == "exited"
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX abrupt process termination")
@pytest.mark.parametrize("ending", ["SIGHUP", "SIGTERM", "SIGKILL"])
def test_given_orphan_process_when_it_dies_without_callback_then_next_observation_settles_run(
    stale_record, monkeypatch, tmp_path, ending
):
    import psutil

    child = spawn_owned_sleeper(60, str(registry.registry_path().parent / "sessions/c-session-1.sh"))
    try:
        process = psutil.Process(child.pid)
        process.info = {"pid": child.pid, "username": process.username(), "status": process.status()}
        monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process] if child.poll() is None else []))
        assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
        assert registry.load_registry()["sessions"][0]["run_state"] == "running"
        child.send_signal(getattr(signal, ending))
        child.wait(timeout=10)
        assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
        assert registry.load_registry()["sessions"][0]["run_state"] == "exited"
    finally:
        reap(child)


@pytest.mark.parametrize(
    "change", [{"boot_id": "previous-boot"}, {"boot_id": None}, {"kind": "remote"}, {"owning_host": "host-b"}]
)
def test_given_unowned_or_other_boot_record_when_listed_then_evidence_is_preserved(stale_record, change):
    registry._mutate(lambda doc: doc["sessions"][0].update(change) or True)
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize("alive", [True, None])
def test_given_live_or_unknown_tmux_when_listed_then_running_is_preserved(stale_record, monkeypatch, alive):
    monkeypatch.setattr(registry, "_local_session_alive", lambda _name: alive)
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize("guard", ["tmux", "unknown_tmux", "agent", "launcher", "safety", "recent", "process_table"])
def test_given_live_or_unprovable_run_when_migration_preflights_then_running_is_preserved(
    stale_record, monkeypatch, guard
):
    import psutil

    if guard in {"tmux", "unknown_tmux"}:
        monkeypatch.setattr(registry, "_local_session_alive", lambda _name: True if guard == "tmux" else None)
    elif guard in {"agent", "launcher"}:
        process = Mock()
        process.info = {
            "pid": -1,
            "username": psutil.Process().username(),
            "status": "running",
        }
        process.create_time.return_value = 0
        process.cmdline.return_value = ["claude", "--name", "session-1"]
        process.environ.return_value = {}
        monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
        if guard == "launcher":
            registry._mutate(lambda doc: doc["sessions"][0].update(launcher_pid=-1) or True)
            process.cmdline.return_value = ["bash"]
    elif guard == "safety":
        path = state.tombstone_path("c-session-1", "host-a")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    elif guard == "recent":
        assert state.mark_running("c-session-1")
    else:
        monkeypatch.setattr(psutil, "process_iter", Mock(side_effect=psutil.AccessDenied(42)))
    before = registry.registry_path().read_bytes()
    blocker = {"tmux": "live tmux session", "agent": "live agent", "launcher": "live launcher pid"}.get(
        guard, "unprovable"
    )
    with pytest.raises(MigrationError, match=blocker):
        _plan_iterm2("session-1", "session-2", Path("/nonexistent/old-worktree"))
    assert registry.registry_path().read_bytes() == before


def test_given_legacy_record_when_listed_then_state_is_explicit_and_other_boot_is_unknown(stale_record):
    def change(doc):
        first = doc["sessions"][0]
        first.pop("run_state")
        first.pop("ended_at")
        other = deepcopy(first)
        other.update(id="other", name="c-session-2", boot_id="previous-boot")
        doc["sessions"].append(other)
        return True

    registry._mutate(change)
    result = CliRunner().invoke(_cli_group, ["sessions", "list", "-j"])
    assert result.exit_code == 0, result.output
    assert [r["run_state"] for r in json.loads(result.stdout)] == ["exited", "unknown"]


@pytest.mark.parametrize("failure", ["access", "iteration"])
def test_given_unreadable_process_table_when_listed_then_running_is_preserved(stale_record, monkeypatch, failure):
    import psutil

    def processes(*args, **kwargs):
        if failure == "iteration":
            raise psutil.AccessDenied(42)
        process = Mock()
        process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running"}
        process.cmdline.return_value = ["claude"]
        process.environ.side_effect = psutil.AccessDenied(42)
        yield process

    monkeypatch.setattr(psutil, "process_iter", processes)
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


def test_given_registry_write_failure_when_reconciled_then_safety_retains_exit_and_next_read_recovers(
    stale_record, monkeypatch
):
    write = registry._write

    def fail(path, doc):
        if path == registry.registry_path():
            raise OSError("storage unavailable")
        write(path, doc)

    monkeypatch.setattr(registry, "_write", fail)
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    saved = json.loads(state.tombstone_path("c-session-1", "host-a").read_text())
    assert saved["deny_boot"]
    assert saved["record"]["run_state"] == "exited"
    monkeypatch.setattr(registry, "_write", write)
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.load_registry()["sessions"][0]["supervisor_exit"] == saved["record"]["supervisor_exit"]


def test_given_final_exit_safety_when_reconciled_then_original_cause_is_preserved(stale_record, monkeypatch):
    write = registry._write

    def fail(path, doc):
        if path == registry.registry_path():
            raise OSError("storage unavailable")
        write(path, doc)

    monkeypatch.setattr(registry, "_write", fail)
    assert state.mark_exited("c-session-1", "manual_exit", {"source": "supervisor"})
    monkeypatch.setattr(registry, "_write", write)
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert record["supervisor_exit"]["cause"] == "manual_exit"


def test_given_fresh_start_when_listed_then_startup_grace_preserves_run(stale_record):
    assert state.mark_running("c-session-1")
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize(("code", "error"), [(1, b"permission denied"), (1, b"protocol version mismatch"), (2, b"")])
def test_given_tmux_probe_error_when_listed_then_failure_is_not_absence(stale_record, monkeypatch, code, error):
    monkeypatch.setattr(registry, "_local_session_alive", _LOCAL_SESSION_ALIVE)
    monkeypatch.setattr(
        registry.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, code, b"", error)
    )
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


def test_given_launch_during_probe_when_reconciled_then_new_generation_is_preserved(stale_record, monkeypatch):
    import psutil

    def processes(*args, **kwargs):
        state.begin("c-session-1")
        assert state.mark_running("c-session-1")
        return iter(())

    monkeypatch.setattr(psutil, "process_iter", processes)
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    record = registry.load_registry()["sessions"][0]
    assert record["generation"] != stale_record["generation"]
    assert record["run_state"] == "running"


@pytest.mark.parametrize("command", ["worker", "publisher", "ticker"])
def test_given_helper_with_inherited_environment_when_listed_then_it_does_not_impersonate_an_agent(
    stale_record, monkeypatch, command
):
    import psutil

    argv = {
        "worker": [sys.executable, "-m", "ai_cli.main", "session-reaper", "run"],
        "publisher": [sys.executable, "-m", "ai_cli.main", "sessions", "publish", "--tick"],
        "ticker": [
            "bash",
            str(registry.registry_path().parent / "sessions/c-session-1.sh"),
            "--ai-cli-heartbeat-ticker",
        ],
    }[command]
    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running"}
    process.cmdline.return_value = argv
    process.environ.return_value = {"AI_TMUX_SESSION": "c-session-1"}
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.load_registry()["sessions"][0]["run_state"] == "exited"


def test_given_hook_evidence_when_reconciled_then_it_is_retained_without_logging_its_payload(stale_record, caplog):
    registry.record_exit("c-session-1", cause="manual_exit", evidence={"detail": "private-test-payload"})
    original = registry.load_registry()["sessions"][0]["exit"]
    caplog.set_level(logging.INFO)
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert record["exit"]["cause"] == "unknown"
    assert record["exit"]["evidence"]["previous_exit"] == original
    assert "private-test-payload" not in caplog.text


def test_given_new_run_that_vanishes_when_next_minute_passes_then_worker_settles_after_startup_grace(stale_record):
    from freezegun import freeze_time

    with freeze_time("2026-01-01T00:00:00Z") as clock:
        assert state.mark_running("c-session-1")
        ticks = []

        def pause(seconds):
            assert seconds == 60
            ticks.append(registry.load_registry()["sessions"][0]["run_state"])
            if len(ticks) == 2:
                raise KeyboardInterrupt
            clock.tick(seconds)

        with pytest.raises(KeyboardInterrupt):
            run_stale_session_reaper({}, reaper_factory=lambda *a, **kw: Mock(), sleep=pause)
    assert ticks == ["running", "exited"]


@pytest.mark.parametrize("arguments", ["empty", "unreadable"])
def test_unrelated_process_when_arguments_unavailable_then_dead_session_settles(stale_record, monkeypatch, arguments):
    import psutil

    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": "helper"}
    if arguments == "empty":
        process.cmdline.return_value = []
    else:
        process.cmdline.side_effect = psutil.AccessDenied(42)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.load_registry()["sessions"][0]["run_state"] == "exited"


@pytest.mark.parametrize("executable", ["python", "node", "nodejs"])
def test_helper_when_session_markers_are_inherited_then_dead_session_settles(stale_record, monkeypatch, executable):
    import psutil

    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": executable}
    process.cmdline.return_value = [executable, "helper.py", "--name", "session-1"]
    process.environ.return_value = {"AI_TMUX_SESSION": "c-session-1", "CLAUDE_CODE_TASK_LIST_ID": "session-1"}
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.load_registry()["sessions"][0]["run_state"] == "exited"


@pytest.mark.parametrize("executable", ["claude", "node", "python"])
def test_possible_agent_when_arguments_unreadable_then_liveness_remains_unknown(stale_record, monkeypatch, executable):
    import psutil

    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": executable}
    process.cmdline.side_effect = psutil.AccessDenied(42)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize(
    "argv", [["node", "/packages/@anthropic-ai/claude-code/cli.js"], ["python", "claude.py"], ["claude"]]
)
def test_agent_entry_point_when_session_marker_matches_then_running_is_preserved(stale_record, monkeypatch, argv):
    import psutil

    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": argv[0]}
    process.cmdline.return_value = argv
    process.environ.return_value = {"AI_TMUX_SESSION": "c-session-1"}
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize("server", [False, True, None])
def test_given_missing_tmux_when_launcher_is_dead_then_absence_or_unknown_is_reported(
    stale_record, monkeypatch, server
):
    import psutil

    monkeypatch.setattr(registry, "_local_session_alive", _LOCAL_SESSION_ALIVE)
    monkeypatch.setattr(registry.subprocess, "run", Mock(side_effect=FileNotFoundError("tmux")))
    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": "tmux: server"}
    if server is None:
        monkeypatch.setattr(psutil, "process_iter", Mock(side_effect=psutil.AccessDenied(42)))
    else:
        monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process] if server else []))
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    if server is False:
        assert registry.load_registry()["sessions"][0]["run_state"] == "exited"
        assert _LOCAL_SESSION_ALIVE("c-session-1") is False
    else:
        assert registry.registry_path().read_bytes() == before
        assert _LOCAL_SESSION_ALIVE("c-session-1") is None


@pytest.mark.parametrize(
    "entry",
    [
        "list",
        pytest.param(
            "boot-relaunch",
            marks=pytest.mark.skipif(sys.platform == "win32", reason="boot relaunch requires macOS or Linux"),
        ),
    ],
)
def test_same_boot_veto_when_run_is_provably_dead_then_record_reconciles_to_exited(stale_record, monkeypatch, entry):
    boot = stale_record["boot_id"]
    registry._mutate(lambda doc: doc["sessions"][0].update(relaunched_boot_id=boot) or True)
    monkeypatch.setattr(
        state.config,
        "load_config",
        lambda **kw: {"machine": {"host_id": "host-a"}, "session_relaunch": {"on_boot": True}},
    )
    row = registry.load_registry()["sessions"][0]
    assert (
        state.boot_veto(row, cfg=state.settings(), host="host-a", boot_id=boot, alive=False)
        == "already attempted in this boot"
    )
    result = CliRunner().invoke(
        _cli_group, ["sessions", *(["list", "-j"] if entry == "list" else ["relaunch", "--boot"])]
    )
    assert result.exit_code == 0, result.output
    settled = registry.load_registry()["sessions"][0]
    assert settled["run_state"] == "exited"
    assert settled["relaunched_boot_id"] == boot
    assert settled["generation"] == stale_record["generation"]


@pytest.mark.parametrize("executable", ["claude", "node", "bash"])
def test_possible_session_process_when_arguments_are_empty_then_liveness_remains_unknown(
    stale_record, monkeypatch, executable
):
    import psutil

    process = Mock()
    process.info = {"pid": 42, "username": psutil.Process().username(), "status": "running", "name": executable}
    process.cmdline.return_value = []
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    before = registry.registry_path().read_bytes()
    assert CliRunner().invoke(_cli_group, ["sessions", "list", "-j"]).exit_code == 0
    assert registry.registry_path().read_bytes() == before
