"""Exercise exit-session through the public command with owned processes."""

import json
import sys

import psutil
import pytest
from click.testing import CliRunner
from process_ownership import reap, spawn_owned_sleeper

from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group as cli


@pytest.fixture
def exit_target(tmp_path, monkeypatch):
    # Process ancestry is denied by some macOS sandboxes; signal targets remain real.
    monkeypatch.setattr(psutil.Process, "parents", lambda self: [])
    proc = spawn_owned_sleeper()
    try:
        name = "c-session-1"
        host = (
            state.config.load_config(read_only=True).get("machine", {}).get("host_id")
            or registry.load_registry()["machine"]
        )
        state.register(name, recipe={"argv": ["ai", "c"], "cwd": str(tmp_path)}, host=host)
        generation = state.begin(name)
        state.mark_running(name, generation=generation)

        def bind(doc):
            doc["sessions"][0]["supervisor_identity"] = {
                "pid": proc.pid,
                "started_at": psutil.Process(proc.pid).create_time(),
                "generation": generation,
            }
            return True

        registry._mutate(bind)
        yield proc, name
    finally:
        reap(proc)


def test_named_session_when_command_exits_then_state_precedes_signal(exit_target, monkeypatch):
    proc, name = exit_target
    original = psutil.Process.send_signal
    seen = []

    def observe(target, sig):
        row = registry.load_registry()["sessions"][0]
        assert row["run_state"] == "exited"
        assert row["supervisor_exit"]["cause"] == "manual_exit"
        seen.append(target.pid)
        return original(target, sig)

    monkeypatch.setattr(psutil.Process, "send_signal", observe)
    result = CliRunner().invoke(cli, ["exit-session", name, "-t", "0.1"])
    assert result.exit_code == 0, result.output
    assert "exited" in result.output
    assert seen == [proc.pid]
    proc.wait(timeout=3)


def test_unverified_current_session_when_command_exits_then_no_write_or_signal(exit_target, monkeypatch):
    proc, _ = exit_target
    before = registry.registry_path().read_bytes()
    monkeypatch.setenv("AI_CLI_RUN_GENERATION", "untrusted")
    result = CliRunner().invoke(cli, ["exit-session"])
    assert result.exit_code != 0
    assert "cannot verify current session" in result.output
    assert registry.registry_path().read_bytes() == before
    assert proc.poll() is None


def test_named_session_when_dry_run_then_no_files_change_or_signals(exit_target):
    proc, name = exit_target
    root = registry.registry_path().parent
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(cli, ["exit-session", name, "-d"])
    assert result.exit_code == 0, result.output
    assert "Would exit" in result.output
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert proc.poll() is None


def test_remote_session_when_exit_requested_then_receipt_support_refused(exit_target):
    proc, name = exit_target
    before = registry.registry_path().read_bytes()
    result = CliRunner().invoke(cli, ["exit-session", name, "-r"])
    assert result.exit_code != 0
    assert "remote receipts" in result.output.lower()
    assert registry.registry_path().read_bytes() == before
    assert proc.poll() is None


@pytest.mark.parametrize("change", ["generation", "started_at"])
def test_stale_supervisor_when_exit_requested_then_no_signal(exit_target, change):
    proc, name = exit_target

    def stale(doc):
        doc["sessions"][0]["supervisor_identity"][change] = "stale" if change == "generation" else 1.0
        return True

    registry._mutate(stale)
    result = CliRunner().invoke(cli, ["exit-session", name])
    assert result.exit_code != 0
    assert proc.poll() is None


def test_unrelated_agent_record_when_current_exit_requested_then_refused(exit_target, tmp_path, monkeypatch):
    proc, name = exit_target
    home = tmp_path / "home"
    sessions = home / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{proc.pid}.json").write_text(json.dumps({"pid": proc.pid, "name": name, "procStart": 1}))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    result = CliRunner().invoke(cli, ["exit-session"])
    assert result.exit_code != 0
    assert "cannot verify current session" in result.output
    assert proc.poll() is None


def test_verified_ancestor_when_current_exit_runs_then_independent_worker_finishes(exit_target, tmp_path, monkeypatch):
    from ai_cli import session_exit
    from ai_cli.process_probe import probe_for

    proc, name = exit_target
    home = tmp_path / "home"
    records = home / ".claude" / "sessions"
    records.mkdir(parents=True)
    marker = probe_for().capture_identity(proc.pid).marker
    (records / f"{proc.pid}.json").write_text(json.dumps({"pid": proc.pid, "name": name, "procStart": marker}))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    # Only the ancestry read is substituted: macOS sandboxing refuses that read.
    monkeypatch.setattr(psutil.Process, "parents", lambda self: [psutil.Process(proc.pid)])
    original = session_exit.detach_cleanup
    workers = []

    def detach(*args, **kwargs):
        worker = original(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(session_exit, "detach_cleanup", detach)
    try:
        result = CliRunner().invoke(cli, ["exit-session", "-t", "0.1"])
        assert result.exit_code == 0, result.output
        assert "independent worker" in result.output
        assert workers[0].wait(timeout=10) == 0
        proc.wait(timeout=3)
        row = registry.load_registry()["sessions"][0]
        assert row["run_state"] == "exited"
        assert state.exit_teardown(row)["complete"]
    finally:
        for worker in workers:
            # The worker starts a separate process group that this test owns.
            if sys.platform != "win32":
                worker.owned_pgid = worker.pid
            reap(worker)


def test_registry_write_failure_when_command_exits_then_target_survives(exit_target, monkeypatch):
    proc, name = exit_target
    original = registry._write

    def fail(path, doc):
        if path == registry.registry_path():
            raise OSError("storage unavailable")
        original(path, doc)

    monkeypatch.setattr(registry, "_write", fail)
    result = CliRunner().invoke(cli, ["exit-session", name])
    assert result.exit_code != 0
    assert "nothing signalled" in result.output
    assert proc.poll() is None


def test_foreign_session_when_command_exits_then_no_change_or_signal(exit_target):
    proc, name = exit_target

    def foreign(doc):
        doc["sessions"][0]["owning_host"] = "other-host"
        return True

    registry._mutate(foreign)
    before = registry.registry_path().read_bytes()
    result = CliRunner().invoke(cli, ["exit-session", name])
    assert result.exit_code != 0
    assert registry.registry_path().read_bytes() == before
    assert proc.poll() is None


def test_unprovable_agent_record_when_named_exit_runs_then_no_signal_or_write(exit_target, tmp_path, monkeypatch):
    proc, name = exit_target
    home = tmp_path / "home"
    records = home / ".claude" / "sessions"
    records.mkdir(parents=True)
    (records / f"{proc.pid}.json").write_text(json.dumps({"pid": proc.pid, "name": name}))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    before = registry.registry_path().read_bytes()
    result = CliRunner().invoke(cli, ["exit-session", name])
    assert result.exit_code != 0
    assert "start time" in result.output
    assert proc.poll() is None
    assert registry.registry_path().read_bytes() == before


def test_fenced_tmux_when_supervisor_binds_then_command_removes_exact_session(exit_target, monkeypatch):
    from ai_cli import session_exit

    proc, name = exit_target
    row = registry.load_registry()["sessions"][0]
    commands = []

    def tmux(args):
        commands.append(args)
        return "$42|token-1|999999" if args[0] == "display-message" else ""

    monkeypatch.setattr(session_exit, "_tmux", tmux)
    binding = CliRunner().invoke(cli, ["sessions", "_bind-exit-tmux", name, row["generation"], "$42", "token-1"])
    assert binding.exit_code == 0, binding.output
    result = CliRunner().invoke(cli, ["exit-session", name, "-t", "0.1"])
    assert result.exit_code == 0, result.output
    assert "kill-session -t '$42'" in commands[-1]
    proc.wait(timeout=3)


def test_supervisor_identity_when_generation_begins_then_birth_is_fenced_and_reattach_preserves_it(exit_target):
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

    proc, name = exit_target
    old_generation = registry.load_registry()["sessions"][0]["generation"]
    result = CliRunner().invoke(cli, ["sessions", "_begin", name, "--supervisor-pid", str(proc.pid)])
    assert result.exit_code == 0, result.output
    row = registry.load_registry()["sessions"][0]
    assert row["generation"] != old_generation
    identity = row["supervisor_identity"]
    assert identity == {
        "pid": proc.pid,
        "started_at": psutil.Process(proc.pid).create_time(),
        "generation": row["generation"],
    }
    registry.record_launch(
        kind="local",
        name=name,
        relaunch_argv=["ai", "c"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=proc.pid,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert registry.load_registry()["sessions"][0]["supervisor_identity"] == identity
