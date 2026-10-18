"""Enrolment uses fixture storage and never acts on a live session."""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from test_session_restart import world as world

from ai_cli import config, main, session_enrol, session_restart
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_enrol import inspect_pane


@pytest.fixture
def enrolled_world(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    path = root / "registry.json"
    monkeypatch.setattr(registry, "registry_path", lambda: path)
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: root)
    monkeypatch.setattr(config, "load_config", lambda: {"machine": {"host_id": "host-a"}})
    monkeypatch.setenv("AI_HOST", "host-a")
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    name = "c-r-myproject-1"
    meta = {
        "engine": "c",
        "ai_name": "myproject-1",
        "session": name,
        "prefix": "c-r-myproject-",
        "project_prefix": "myproject",
        "project_name": "myproject",
        "is_remote": True,
        "sandbox": True,
    }
    script = root / "sessions" / f"{name}.sh"
    script.parent.mkdir()
    script.write_text(main._engine_script_from_meta(meta), encoding="utf-8")
    pane = Mock(return_value=(str(cwd), ["bash", str(script)]))
    monkeypatch.setattr(session_enrol, "inspect_pane", pane)
    signal = Mock(side_effect=AssertionError("enrolment must not signal"))
    command = Mock(side_effect=AssertionError("enrolment must not invoke a command"))
    monkeypatch.setattr(session_enrol.os, "kill", signal)
    monkeypatch.setattr(session_enrol.subprocess, "run", command)
    registry._mutate(lambda doc: True)
    return SimpleNamespace(
        root=root,
        cwd=cwd,
        path=path,
        name=name,
        script=script,
        pane=pane,
        signal=signal,
        command=command,
        runner=CliRunner(),
    )


def invoke(world, *args):
    return world.runner.invoke(main._cli_group, ["sessions", "enrol", world.name, *args])


def add_record(world, **changes):
    record = registry._new_record(
        kind="local",
        name=world.name,
        relaunch_argv=["ai", "c", world.name, "--is-remote"],
        cwd=str(world.cwd),
        remote=None,
        iterm2={"window": 1},
        launcher_pid=123,
    )
    record.update(generation="generation-1", relaunch="always", owning_host="host-a", custom={"value": 1})
    record.update(changes)
    registry._mutate(lambda doc: doc["sessions"].append(record) or True)
    return record


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
def test_given_registry_record_when_enrolled_then_ac_e_1_only_recipe_and_policy_change(enrolled_world, engine):
    w = enrolled_world
    w.name = f"{engine}-myproject-1"
    before = add_record(w, relaunch_argv=["ai", engine, "1", "-p", "myproject", "--once"])
    # The first source wins even when no usable generated script exists.
    assert not (w.script.parent / f"{w.name}.sh").exists()
    result = invoke(w)
    assert result.exit_code == 0, result.output
    recipe = json.loads(result.output)
    assert recipe["argv"] == before["relaunch_argv"]
    assert recipe["engine"] == engine and recipe["project"] == "myproject"
    assert recipe["options"]["once"]
    after = registry.load_registry()["sessions"][0]
    assert after == before | {"boot_recipe": recipe, "relaunch": "never"}
    assert state.valid_boot_recipe(after)
    w.signal.assert_not_called()
    w.command.assert_not_called()


def test_given_generated_script_when_enrolled_then_ac_e_2_record_and_recipe_preserve_remote_flags(enrolled_world):
    w = enrolled_world
    result = invoke(w)
    assert result.exit_code == 0, result.output
    recipe = json.loads(result.output)
    record = registry.load_registry()["sessions"][0]
    assert record["name"] == w.name and record["kind"] == "local"
    assert record["relaunch"] == "never" and record["boot_recipe"] == recipe
    assert recipe["owning_host"] == "host-a" and recipe["cwd"] == str(w.cwd)
    assert recipe["project"] == "myproject"
    assert recipe["options"]["is_remote"] and recipe["options"]["sandbox"]
    assert "--is-remote" in recipe["argv"] and "--sandbox" in recipe["argv"]
    assert state.valid_boot_recipe(record)
    w.signal.assert_not_called()
    w.command.assert_not_called()


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_given_registry_lock_when_enrolled_then_pane_inspection_runs_outside_lock(
    enrolled_world, monkeypatch, registered, dry_run
):
    w = enrolled_world
    if registered:
        add_record(w)
    locked = registry._locked
    lock_held = False
    lock_entries = []

    @contextmanager
    def track_lock(timeout=None):
        nonlocal lock_held
        with locked(timeout) as path:
            lock_held = True
            lock_entries.append(timeout)
            try:
                yield path
            finally:
                lock_held = False

    def inspect(name):
        assert not lock_held, "pane inspection must run outside the registry lock"
        return str(w.cwd), ["bash", str(w.script)]

    monkeypatch.setattr(registry, "_locked", track_lock)
    w.pane.side_effect = inspect
    result = invoke(w, *(["--dry-run"] if dry_run else []))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["session_name"] == w.name
    w.pane.assert_called_once_with(w.name)
    assert lock_entries == ([] if dry_run else [2])


@pytest.mark.parametrize("change", ["added", "removed", "valid", "arguments", "kind", "duplicate"])
def test_given_record_changed_when_enrol_locks_then_refuses_without_writing(enrolled_world, monkeypatch, change):
    w = enrolled_world
    if change != "added":
        add_record(w)
    mutate = registry._mutate
    concurrent_bytes = []

    def mutate_after_change(callback, **kwargs):
        def concurrent_change(doc):
            if change == "added":
                doc["sessions"].append({"name": w.name, "kind": "local"})
            elif change == "removed":
                doc["sessions"].clear()
            elif change == "valid":
                record = doc["sessions"][0]
                record["boot_recipe"] = session_enrol._record_recipe(record, "c", "host-a")
                assert state.valid_boot_recipe(record)
            elif change == "arguments":
                doc["sessions"][0]["relaunch_argv"].append("--once")
            elif change == "kind":
                doc["sessions"][0]["kind"] = "remote"
            else:
                doc["sessions"].append(doc["sessions"][0].copy())
            return True

        mutate(concurrent_change)
        concurrent_bytes.append(w.path.read_bytes())
        return mutate(callback, **kwargs)

    monkeypatch.setattr(registry, "_mutate", mutate_after_change)
    result = invoke(w)
    assert result.exit_code != 0, result.output
    assert "registry record check failed" in result.output and "changed" in result.output
    assert len(concurrent_bytes) == 1 and w.path.read_bytes() == concurrent_bytes[0]
    w.pane.assert_called_once_with(w.name)
    w.signal.assert_not_called()
    w.command.assert_not_called()


def test_given_legacy_record_without_host_when_enrolled_then_ac_e_1_uses_launcher_host(enrolled_world):
    w = enrolled_world
    add_record(w)
    registry._mutate(lambda doc: doc["sessions"][0].pop("owning_host") is not None)
    result = invoke(w)
    assert result.exit_code == 0, result.output
    record = registry.load_registry()["sessions"][0]
    assert record["owning_host"] == record["boot_recipe"]["owning_host"] == "host-a"
    assert state.valid_boot_recipe(record)


def test_given_engine_arguments_when_enrolled_then_ac_e_1_shipped_parser_preserves_arguments(enrolled_world):
    w = enrolled_world
    argv = ["ai", "c", w.name, "--project=myproject", "--", "--model", "example-model", "-s"]
    add_record(w, relaunch_argv=argv)
    result = invoke(w)
    assert result.exit_code == 0, result.output
    recipe = json.loads(result.output)
    assert recipe["argv"] == argv
    assert recipe["extra_args"] == ["--model", "example-model", "-s"]
    assert recipe["project"] == "myproject" and not recipe["options"]["sandbox"]


def test_given_missing_registry_when_dry_run_then_ac_e_6_creates_no_registry_or_lock(enrolled_world):
    w = enrolled_world
    absent = w.root / "absent.json"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(registry, "registry_path", lambda: absent)
        result = invoke(w, "--dry-run")
    assert result.exit_code == 0, result.output
    assert not absent.exists() and not absent.with_suffix(".json.lock").exists()


@pytest.mark.parametrize(
    "failure", ["leader", "shell-text", "extra-argument", "missing", "metadata", "flags", "identity", "host"]
)
def test_given_unproven_script_when_enrolled_then_ac_e_3_refuses_without_write(enrolled_world, monkeypatch, failure):
    w = enrolled_world
    source = w.script.read_text(encoding="utf-8")
    if failure in ("leader", "shell-text", "extra-argument"):
        argv = {
            "leader": ["bash", str(w.script) + ".other"],
            "shell-text": ["bash", "-c", str(w.script)],
            "extra-argument": ["bash", str(w.script), "extra"],
        }[failure]
        w.pane.return_value = (str(w.cwd), argv)
    elif failure == "missing":
        # Inspect the precise fixture target before removing it.
        assert w.script.is_file() and "run_agent claude" in source
        w.script.unlink()
    elif failure == "metadata":
        w.script.write_text("unparseable", encoding="utf-8")
    elif failure == "flags":
        w.script.write_text(source.replace("run_agent claude ", "run_agent claude --unknown "), encoding="utf-8")
    elif failure == "identity":
        w.script.write_text(source.replace("ai_name=myproject-1", "ai_name=other-1"), encoding="utf-8")
    else:
        monkeypatch.delenv("AI_HOST")
    before = w.path.read_bytes()
    result = invoke(w)
    assert result.exit_code != 0
    assert "check failed" in result.output
    assert w.path.read_bytes() == before
    w.signal.assert_not_called()
    w.command.assert_not_called()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cwd": "relative"}, "valid_boot_recipe"),
        ({"relaunch_argv": ["wrong", "c"]}, "valid_boot_recipe"),
        ({"relaunch_argv": None}, "valid_boot_recipe"),
        ({"relaunch_argv": ["ai", "c", "-b"]}, "valid_boot_recipe"),
        ({"owning_host": "host-b"}, "owning host"),
        ({"kind": "remote"}, "registry record"),
    ],
)
def test_given_invalid_record_when_enrolled_then_ac_e_4_names_failed_check_and_writes_nothing(
    enrolled_world, changes, message
):
    w = enrolled_world
    add_record(w, **changes)
    before = w.path.read_bytes()
    result = invoke(w)
    assert result.exit_code != 0 and message in result.output
    assert w.path.read_bytes() == before


def test_given_mismatched_cwd_when_enrolled_then_ac_e_4_refuses_without_write(enrolled_world):
    w = enrolled_world
    add_record(w, cwd=str(w.root))
    before = w.path.read_bytes()
    result = invoke(w)
    assert result.exit_code != 0 and "working directory check failed" in result.output
    assert w.path.read_bytes() == before


def test_given_valid_recipe_when_enrolled_twice_then_ac_e_5_bytes_inode_and_mtime_are_unchanged(enrolled_world):
    w = enrolled_world
    assert invoke(w).exit_code == 0
    before, stat = w.path.read_bytes(), w.path.stat()
    w.pane.side_effect = AssertionError("already enrolled must not inspect process")
    for _ in range(2):
        result = invoke(w)
        assert result.exit_code == 0 and "already has a valid recipe; unchanged" in result.output, result.output
        assert w.path.read_bytes() == before
        assert (w.path.stat().st_ino, w.path.stat().st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("flag", ["-d", "--dry-run"])
def test_given_legacy_session_when_dry_run_then_ac_e_6_prints_without_writes_or_actions(
    enrolled_world, registered, flag
):
    w = enrolled_world
    if registered:
        add_record(w)
    before = w.path.read_bytes()
    result = invoke(w, flag)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["session_name"] == w.name
    assert w.path.read_bytes() == before
    w.signal.assert_not_called()
    w.command.assert_not_called()


@pytest.mark.parametrize("activation", [True, False])
def test_given_enrolled_remote_session_when_restarted_then_ac_e_7_activation_gate_and_remote_control_survive(
    world, monkeypatch, activation
):
    w = world
    old_session = w.session
    session = old_session.replace("c-", "c-r-", 1)
    script = w.script.with_name(f"{session}.sh")
    meta = w.meta | {"session": session, "is_remote": True}
    script.write_text(main._engine_script_from_meta(meta), encoding="utf-8")
    (script.parent.parent / f"session-meta-{session}.json").write_text(json.dumps(meta), encoding="utf-8")
    w.record["tmux"] = w.record["tmux"].replace(old_session, session)
    w.write()
    monkeypatch.setattr(registry, "registry_path", lambda: w.root / "enrol-registry.json")
    monkeypatch.setenv("AI_HOST", "host-a")
    monkeypatch.setattr(session_enrol, "inspect_pane", lambda name: (str(w.cwd), ["bash", str(script)]))
    result = CliRunner().invoke(main._cli_group, ["sessions", "enrol", session])
    assert result.exit_code == 0, result.output
    assert not w.signals and not w.injected
    assert state.valid_boot_recipe(registry.load_registry()["sessions"][0])
    settings = w.root / ".claude/settings.json"
    assert json.loads(settings.read_text(encoding="utf-8"))["remoteControlAtStartup"]
    settings.write_text(json.dumps({"remoteControlAtStartup": activation}), encoding="utf-8")
    original = w.subprocess_run

    def run(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[0] == "tmux":
            result.stdout = result.stdout.replace(old_session, session)
        return result

    monkeypatch.setattr(session_restart.subprocess, "run", run)
    gate = Mock(wraps=session_restart.activation_gate)
    monkeypatch.setattr(session_restart, "activation_gate", gate)
    code, report, _ = w.cli("-X", w.record["sessionId"])
    assert gate.call_count > 0
    if activation:
        assert code == 0 and report["reason"] == "restarted", report
        assert report["local"] == "VERIFIED" and report["peer"] == "VERIFIED"
        replacement = json.loads((w.registry.parent / "201.json").read_text(encoding="utf-8"))
        assert replacement["bridgeSessionId"] and replacement["tmux"].startswith(session + ":")
    else:
        assert code != 0 and report["reason"] == "rc-activation"
        assert not w.signals and not w.injected


def test_given_documentation_when_enrol_checked_then_ac_e_8_command_and_new_criterion_are_present():
    root = Path(__file__).resolve().parents[1]
    assert "ai sessions enrol <name>" in (root / "README.md").read_text(encoding="utf-8")
    plans = list((root / "docs/plans").glob("*restart-idle-sessions-plan.md"))
    assert len(plans) == 1
    plan = plans[0].read_text(encoding="utf-8")
    assert "AC-2.19" in plan and "ai sessions enrol <name>" in plan


@pytest.mark.parametrize("failure", ["none", "multiple", "foreign", "dead", "error"])
def test_given_tmux_panes_when_inspected_then_only_one_exact_live_leader_is_accepted(
    enrolled_world, monkeypatch, failure
):
    w = enrolled_world
    line = f"{w.name}\t123\t{w.cwd}\n"
    response = {"none": line, "multiple": line + line, "foreign": "other-1\t123\t/tmp\n", "dead": line, "error": line}[
        failure
    ]
    run = Mock(return_value=SimpleNamespace(stdout=response))
    if failure == "error":
        run.side_effect = subprocess.TimeoutExpired("tmux", 5)
    process = Mock(return_value=SimpleNamespace(cmdline=lambda: ["bash", str(w.script)]))
    if failure == "dead":
        process.side_effect = session_enrol.psutil.NoSuchProcess(123)
    monkeypatch.setattr(session_enrol.subprocess, "run", run)
    monkeypatch.setattr(session_enrol.psutil, "Process", process)
    if failure == "none":
        assert inspect_pane(w.name) == (str(w.cwd), ["bash", str(w.script)])
    else:
        with pytest.raises(ValueError, match="live pane check failed"):
            inspect_pane(w.name)
    assert run.call_args.args[0][:2] == ["tmux", "list-panes"]
