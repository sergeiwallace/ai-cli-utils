"""CLI behaviour with fixture registries, transcripts, process probes and tmux."""

from __future__ import annotations

import json
import os
import shlex
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from conftest import run_cli

from ai_cli import config, main
from ai_cli import session_restart as restart
from ai_cli.process_probe import ProcessIdentity, StartTimeMatch

SESSION_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def _is_restart_probe(argv, pane_ai):
    """Match the helper probe; Windows `which` may return the executable in a different case."""
    return (
        len(argv) == 4
        and os.path.normcase(str(argv[0])) == os.path.normcase(str(pane_ai))
        and argv[1:] == ["internal", "restart-pin", "--probe"]
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    # Process probes and signals below simulate POSIX independently of the host.
    # Platform routing is exercised separately, including Windows injection.
    monkeypatch.setattr(restart, "_signal_supported", lambda: True)
    # These are simulated POSIX capabilities, including on hosts whose Python
    # has no Unix socket family or discovers shells with a .exe suffix.
    monkeypatch.setattr(restart.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    root = tmp_path / "home"
    root.mkdir()
    binaries = root / "pane bin"
    binaries.mkdir()
    pane_ai = binaries / ("ai.exe" if sys.platform == "win32" else "ai")
    pane_ai.touch()
    pane_ai.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state with spaces"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup": true}', encoding="utf-8")
    transcript = root / ".claude" / "projects" / "myproject" / f"{SESSION_ID}.jsonl"
    transcript.parent.mkdir(parents=True)
    rows = [
        {
            "type": "assistant",
            "message": {
                "id": "turn-1",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10_000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        },
        {"type": "system", "subtype": "turn_duration", "pendingBackgroundAgentCount": 0},
    ]
    record = {
        "pid": 101,
        "procStart": 101,
        "sessionId": SESSION_ID,
        "name": "myproject-box-1",
        "nameSource": "user",
        "status": "idle",
        "statusUpdatedAt": (time.time() - 1000) * 1000,
        "cwd": str(cwd),
        "tmux": "c-myproject-box-1:@1.%1",
        "bridgeSessionId": "bridge-1",
        "messagingSocketPath": str(tmp_path / "socket"),
        "version": "2.1.1",
    }
    registry = root / ".claude" / "sessions" / "101.json"
    registry.parent.mkdir()
    session = "c-myproject-box-1"
    state = config.get_xdg_state_home()
    config_home = config.get_xdg_config_home()
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: state)
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: config_home)
    meta = {
        "engine": "c",
        "ai_name": record["name"],
        "session": session,
        "prefix": "c-myproject-box-",
        "project_prefix": "myproject-box",
        "worktree_dir": str(cwd),
    }
    script = state / "sessions" / f"{session}.sh"
    main._write_launch_script_if_changed(script, main._engine_script_from_meta(meta))
    (state / f"session-meta-{session}.json").write_text(json.dumps(meta), encoding="utf-8")
    state_obj = SimpleNamespace(
        root=root,
        cwd=cwd,
        rows=rows,
        record=record,
        registry=registry,
        transcript=transcript,
        script=script,
        meta=meta,
        session=session,
        generation="generation-1",
        ended=False,
        identities={},
        children=[],
        signals=[],
        observer="seen",
        injected=[],
        after_prepare=None,
        processes={},
        pane_ai=pane_ai,
        pane_env={"PATH": str(binaries)},
    )
    settings = {
        "restart_session": {
            "min_uptime_seconds": 0,
            "settle_seconds": 0,
            "term_grace_seconds": 0,
            "relaunch_timeout_seconds": 0,
            "cooldown_seconds": 0,
        }
    }
    state_obj.settings = settings

    def write():
        registry.write_text(json.dumps(record), encoding="utf-8")
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    state_obj.write = write
    write()
    probe = SimpleNamespace(
        capture_identity=lambda pid: state_obj.identities.get(pid, ProcessIdentity("fake", pid)),
        has_ended=lambda pid: pid == 101 and state_obj.ended,
        start_time_match=lambda pid, recorded: StartTimeMatch.MATCH if recorded == pid else StartTimeMatch.UNPROVEN,
    )
    state_obj.probe = probe
    monkeypatch.setattr(restart, "probe_for", lambda: probe)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return time.time() - 4000

        def ppid(self):
            return {101: 102, 102: 103, 104: 103, 201: 104}.get(self.pid, 0)

        def parents(self):
            return [Process(102), Process(103)]

        def cmdline(self):
            return ["bash", str(script), "--ai-cli-child-body"]

        def children(self, recursive=False):
            return state_obj.children

        def environ(self):
            assert self.pid == 103
            return state_obj.pane_env.copy()

        def cwd(self):
            assert self.pid == 103
            return str(cwd)

    monkeypatch.setattr(restart.psutil, "Process", Process)
    monkeypatch.setattr(restart, "_script_source", lambda pid, path: path.read_text(encoding="utf-8"))

    def subprocess_run(argv, **kwargs):
        if _is_restart_probe(argv, pane_ai):
            assert kwargs["env"] == state_obj.pane_env
            assert kwargs["cwd"] == cwd
            return SimpleNamespace(returncode=0, stdout="restart-pin-v1\n")
        if argv[0] == "tmux":
            fields = [session, "%1", "103", state_obj.generation]
            if "pane_start_command" in argv[-1]:
                fields.append(shlex.join(["bash", str(script)]))
            return SimpleNamespace(returncode=0, stdout="\t".join(fields) + "\n")
        if argv[:3] == ["claude", "agents", "--json"]:
            replacement = {**record, "pid": 201, "procStart": 201}
            return SimpleNamespace(returncode=0, stdout=json.dumps([replacement]))
        if argv[0] == "observer":
            data = {
                "status": "seen",
                "complete": True,
                "observed_at": time.time(),
                "rows": [{"session_id": SESSION_ID, "title": record["name"], "bridge_session_id": "bridge-1"}],
            }
            if callable(state_obj.observer):
                data = state_obj.observer(data)
            return SimpleNamespace(returncode=0, stdout=json.dumps(data))
        if argv[0] == "inject":
            state_obj.injected.append(argv)
            replace()
            return SimpleNamespace(returncode=0, stdout='{"status":"submitted"}')
        raise AssertionError(f"unexpected subprocess: {argv}")

    state_obj.subprocess_run = subprocess_run
    connection = MagicMock()
    monkeypatch.setattr(restart.socket, "socket", lambda *a: connection)
    state_obj.connection = connection

    def replace():
        state_obj.ended = True
        replacement = {**record, "pid": 201, "procStart": 201}
        (registry.parent / "201.json").write_text(json.dumps(replacement), encoding="utf-8")

    state_obj.replace = replace

    def kill(pid, sig):
        state_obj.signals.append((pid, sig))
        replace()

    monkeypatch.setattr(restart.os, "kill", kill)
    monkeypatch.setattr(restart, "load_commands", lambda: {"observer": ["observer"]})
    state_obj.commands = lambda value: monkeypatch.setattr(restart, "load_commands", lambda: value)

    def cli(*args):
        code, out, err = run_cli(["ai", *args, "-j"], config=settings)
        return code, json.loads(out), err

    state_obj.cli = cli
    # Restore the shared subprocess module before the conftest spawn guard exits.
    # monkeypatch is finalized later and would resurrect that expired guard.
    with patch.object(restart.subprocess, "run", subprocess_run):
        yield state_obj


@pytest.mark.parametrize("mechanism", ["kill", "exit", "fallback"])
@pytest.mark.parametrize("after_prepare", [False, True])
def test_given_supervisor_ai_without_restart_pin_when_restarting_then_refuses_before_action(
    world, monkeypatch, mechanism, after_prepare
):
    world.settings["restart_session"]["mechanism"] = "exit" if mechanism == "exit" else "kill"
    monkeypatch.setattr(restart, "_signal_supported", lambda: mechanism != "fallback")
    world.commands({"inject": ["inject"]})
    original_script = world.script.read_bytes()
    probes = []
    prepared = False
    prepare = restart.prepare_request

    def prepare_and_downgrade(*args):
        nonlocal prepared
        result = prepare(*args)
        prepared = True
        return result

    monkeypatch.setattr(restart, "prepare_request", prepare_and_downgrade)

    def run(argv, **kwargs):
        if _is_restart_probe(argv, world.pane_ai):
            probes.append((argv, kwargs))
            if not after_prepare or prepared:
                return SimpleNamespace(returncode=1, stdout="", stderr="unknown action: restart-pin")
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "supervisor-upgrade-required"
    assert not world.signals and not world.injected
    assert probes and probes[-1][1]["env"] == world.pane_env
    assert probes[-1][1]["cwd"] == world.cwd
    if not after_prepare:
        assert world.script.read_bytes() == original_script
        assert not restart.state_paths(world.session)[2].exists()


def test_given_restart_pin_probe_when_dispatched_then_no_config_or_request_is_read(monkeypatch, capsys):
    def unexpected(*args):
        pytest.fail("capability probe must not read configuration or restart state")

    monkeypatch.setattr(config, "load_config", unexpected)
    monkeypatch.setattr(restart, "child_pin", unexpected)
    with pytest.raises(SystemExit) as exited:
        main._handle_internal(["restart-pin", "--probe"])
    assert exited.value.code == 0 and capsys.readouterr().out.strip() == "restart-pin-v1"


@pytest.mark.parametrize("failure", ["missing-path", "missing-executable", "unreadable", "timeout", "wrong-reply"])
def test_given_unproven_restart_pin_when_checked_then_upgrade_is_required(world, monkeypatch, failure):
    if failure == "missing-path":
        world.pane_env.clear()
    elif failure == "missing-executable":
        world.pane_env["PATH"] = str(world.cwd / "missing")
    elif failure == "unreadable":

        def unreadable(process):
            raise restart.psutil.AccessDenied(process.pid)

        monkeypatch.setattr(restart.psutil.Process, "environ", unreadable)

    def run(argv, **kwargs):
        if failure == "timeout":
            raise restart.subprocess.TimeoutExpired(argv, 15)
        return SimpleNamespace(returncode=0, stdout="unrelated response")

    with patch.object(restart.subprocess, "run", run):
        with pytest.raises(restart.Refusal, match="supervisor-upgrade-required"):
            restart._restart_pin_gate(103, world.cwd)
    assert not world.signals and not world.injected


@pytest.mark.parametrize("entry", ["", ".", "../home/pane bin"])
def test_given_relative_supervisor_path_when_probed_then_child_directory_is_used(world, entry):
    world.pane_env["PATH"] = entry
    executable = world.pane_ai if entry.startswith("..") else world.cwd / world.pane_ai.name
    if executable != world.pane_ai:
        executable.touch()
        executable.chmod(0o755)
    probes = []

    def run(argv, **kwargs):
        probes.append(argv)
        assert Path(argv[0]).resolve() == executable.resolve()
        assert argv[1:] == ["internal", "restart-pin", "--probe"]
        assert kwargs["cwd"] == world.cwd and kwargs["env"] == world.pane_env
        return SimpleNamespace(returncode=0, stdout="restart-pin-v1\n")

    with patch.object(restart.subprocess, "run", run):
        restart._restart_pin_gate(103, world.cwd)
    assert len(probes) == 1


@pytest.mark.parametrize("worktree_dir", [".", ""])
def test_given_unpinned_working_directory_when_checked_then_supervisor_cwd_is_used(world, worktree_dir):
    world.meta["worktree_dir"] = worktree_dir
    meta_path = config.get_xdg_state_home() / f"session-meta-{world.session}.json"
    assert restart._object(meta_path)["worktree_dir"] == str(world.cwd)
    meta_path.write_text(json.dumps(world.meta), encoding="utf-8")
    main._write_launch_script_if_changed(world.script, main._engine_script_from_meta(world.meta))
    code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 0 and report["gates"]["live-supervisor-uptime"] == "PASS"
    assert not world.signals and not world.injected


@pytest.mark.parametrize("target", [SESSION_ID, "myproject-box-1", "c-myproject-box-1"])
@pytest.mark.parametrize("flag", ["-X", "--restart-session"])
def test_given_alias_when_restart_then_one_pid(world, target, flag):
    code, report, _ = world.cli(flag, target)
    assert code == 0, report
    assert (code, report["reason"], report["local"], report["peer"], report["mobile"]) == (
        0,
        "restarted",
        "VERIFIED",
        "VERIFIED",
        "UNVERIFIED",
    )
    assert world.signals == [(101, signal.SIGTERM)]
    assert world.connection.__enter__.return_value.connect.call_count == 2
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "complete"


@pytest.mark.parametrize("check", [False, True])
def test_given_string_birth_markers_when_cli_runs_then_every_identity_check_matches(world, monkeypatch, check):
    world.record["procStart"] = "101"
    del world.rows[-1]["pendingBackgroundAgentCount"]
    world.write()
    kill = restart.os.kill

    def replace_with_string_marker(pid, sig):
        kill(pid, sig)
        path = world.registry.parent / "201.json"
        record = restart._object(path)
        path.write_text(json.dumps({**record, "procStart": "201"}), encoding="utf-8")

    monkeypatch.setattr(restart.os, "kill", replace_with_string_marker)
    code, report, _ = world.cli("-Y" if check else "-X", SESSION_ID)
    assert code == 0, report
    assert report["reason"] == ("ready" if check else "restarted")
    assert world.signals == ([] if check else [(101, signal.SIGTERM)])


def test_given_slot_when_restart_then_no_launch(world, monkeypatch):
    monkeypatch.setattr(main._config, "resolve_project_prefix_by_name", lambda name: "myproject")
    monkeypatch.setattr(main._session_naming, "resolve_host_alias", lambda: "box")
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda: [world.session])
    launch = Mock(side_effect=AssertionError("launch reached"))
    monkeypatch.setattr(main, "_dispatch_session", launch)
    code, report, _ = world.cli("c", "1", "-p", "myproject", "-X")
    assert code == 0 and report["reason"] == "restarted"
    launch.assert_not_called()


def test_given_self_when_restart_then_detached(world, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    spawn = Mock()
    with patch.object(restart.subprocess, "Popen", spawn):
        code, report, _ = world.cli("-X")
    assert code == 0 and report["reason"] == "detached"
    assert spawn.call_args.args[0][-2:] == ["restart-detached", SESSION_ID]
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert world.signals == []


@pytest.mark.parametrize("kind", ["absent", "duplicate", "dead", "birth", "pane", "unknown-program"])
def test_given_bad_target_when_restart_then_no_signal(world, monkeypatch, kind):
    if kind == "absent":
        world.registry.unlink()
    elif kind == "duplicate":
        (world.registry.parent / "duplicate.json").write_text(
            json.dumps({**world.record, "pid": 202}), encoding="utf-8"
        )
    elif kind == "dead":
        world.ended = True
    elif kind == "birth":
        world.record["procStart"] = 999
        world.write()
    elif kind == "pane":
        monkeypatch.setattr(restart, "_run", lambda *a: "wrong\t%1\t103\tgeneration-1\tbash /tmp/other.sh")
    else:
        world.script.write_text("echo unsupported\n", encoding="utf-8")
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("identity", "target", "not-managed", "supervisor-upgrade-required")
    assert world.signals == []


@pytest.mark.parametrize("kind", ["busy", "waiting", "settling", "unfinished"])
def test_given_busy_when_restart_then_refusal(world, kind):
    if kind == "busy":
        world.record["status"] = "busy"
    elif kind == "waiting":
        world.record["waitingFor"] = "permission"
    elif kind == "settling":
        world.settings["restart_session"]["settle_seconds"] = 2000
    else:
        world.rows.append({"type": "assistant", "message": {"content": []}})
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy" and not world.signals


@pytest.mark.parametrize(
    "kind", ["agent-count", "counter-invalid", "shell", "agent", "descendant", "unreadable", "truncated"]
)
def test_given_work_when_restart_then_refusal(world, monkeypatch, kind):
    if kind == "agent-count":
        world.rows[-1]["pendingBackgroundAgentCount"] = 1
    elif kind == "counter-invalid":
        world.rows[-1]["pendingBackgroundAgentCount"] = None
    elif kind in ("shell", "agent"):
        result = (
            {"backgroundTaskId": "task-1"} if kind == "shell" else {"status": "async_launched", "agentId": "agent-1"}
        )
        world.rows.insert(0, {"type": "user", "toolUseResult": result})
    elif kind == "descendant":
        world.children.append(SimpleNamespace(pid=501, ppid=lambda: 101, cmdline=lambda: ["sleep", "300"]))
    world.write()
    if kind == "unreadable":
        world.transcript.write_text("{", encoding="utf-8")
    if kind == "truncated":
        monkeypatch.setattr(restart, "TAIL_LIMIT", 10)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work" and not world.signals


def test_given_context_when_config_changes_then_fresh_threshold(world):
    world.settings["restart_session"]["context_threshold_pct"] = 1
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context" and report["detail"] == "pre-step unavailable"
    assert not world.signals
    world.settings["restart_session"]["context_threshold_pct"] = 5
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"


@pytest.mark.parametrize("kind", ["missing", "false", "unreadable", "project-false", "local-true"])
def test_given_activation_when_restart_then_settings_preserved(world, kind):
    path = world.root / ".claude" / "settings.json"
    if kind == "missing":
        path.unlink()
    elif kind == "false":
        path.write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
    elif kind == "unreadable":
        path.write_text("{", encoding="utf-8")
    else:
        project = world.cwd / ".claude"
        project.mkdir()
        (project / "settings.json").write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
        if kind == "local-true":
            (project / "settings.local.json").write_text('{"remoteControlAtStartup":true}', encoding="utf-8")
    before = {p: p.read_bytes() for p in [path, *world.cwd.glob(".claude/*.json")] if p.exists()}
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == (0 if kind == "local-true" else 1)
    assert report["reason"] == ("restarted" if kind == "local-true" else "rc-activation")
    assert before == {p: p.read_bytes() for p in before}
    if kind != "local-true":
        assert not world.signals


def test_given_inaccessible_settings_when_restart_then_no_signal(world, monkeypatch):
    path = world.cwd / ".claude" / "settings.local.json"
    original = Path.stat

    def stat(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("settings unavailable")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "rc-activation"
    assert not world.signals and not world.injected


@pytest.mark.parametrize(
    "kind",
    [
        "none",
        "unknown",
        "stale",
        "partial",
        "absent",
        "wrong-id",
        "wrong-title",
        "wrong-bridge",
        "duplicate",
        "verified",
    ],
)
def test_given_peer_when_restart_then_separate_verdict(world, kind):
    if kind == "none":
        world.commands({})
    else:

        def observer(data):
            if kind == "unknown":
                return "invalid"
            if kind == "stale":
                data["observed_at"] = 0
            if kind == "partial":
                data["complete"] = False
            if kind == "absent":
                data.update(status="absent", rows=[])
            if kind == "wrong-id":
                data["rows"][0]["session_id"] = OTHER_ID
            if kind == "wrong-title":
                data["rows"][0]["title"] = "other"
            if kind == "wrong-bridge":
                data["rows"][0]["bridge_session_id"] = "old-bridge"
            if kind == "duplicate":
                data["rows"] *= 2
            return data

        world.observer = observer
    code, report, _ = world.cli("-X", SESSION_ID)
    assert report["local"] == "VERIFIED" and report["mobile"] == "UNVERIFIED", report
    if kind == "verified":
        assert code == 0 and report["reason"] == "restarted"
    elif kind in ("absent", "wrong-id", "wrong-title", "wrong-bridge", "duplicate"):
        assert code == 1 and report["reason"] == "verification-failed"
    else:
        assert code == 1 and report["reason"] == "verification-incomplete"


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_given_platform_when_signal_support_checked_then_windows_unsupported(monkeypatch, platform):
    monkeypatch.setattr(restart.sys, "platform", platform)
    assert restart._signal_supported() is (platform != "win32")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("mechanism", ["kill", "exit"])
def test_given_mechanism_when_restart_then_exact_route(world, monkeypatch, platform, mechanism):
    monkeypatch.setattr(restart, "_signal_supported", lambda: platform != "win32")
    world.settings["restart_session"]["mechanism"] = mechanism
    world.commands({"inject": ["inject", "{tmux_pane}", "{command}"], "observer": ["observer"]})
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    if platform == "win32" or mechanism == "exit":
        assert world.injected == [["inject", "%1", "/exit"]] and not world.signals
    else:
        assert world.signals == [(101, signal.SIGTERM)] and not world.injected


@pytest.mark.parametrize("signal_supported", [False, True])
def test_given_no_injector_when_exit_then_refusal(world, monkeypatch, signal_supported):
    monkeypatch.setattr(restart, "_signal_supported", lambda: signal_supported)
    world.settings["restart_session"]["mechanism"] = "exit" if signal_supported else "kill"
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "no-mechanism" and not world.signals


@pytest.mark.parametrize("flag", ["-Y", "--restart-check"])
def test_given_check_when_cli_then_no_action(world, flag):
    code, report, _ = world.cli(flag, SESSION_ID)
    assert code == 0 and report["reason"] == "ready"
    assert all(value == "PASS" for value in report["gates"].values())
    assert set(report["triggers"]) == {"update", "unreachable"}
    assert not world.signals and not world.injected
    assert not restart.state_paths(world.session)[2].exists()


def test_given_locked_session_when_checked_then_all_gate_verdicts_reported(world):
    with restart.session_lock(world.session):
        code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 1 and report["reason"] == "refused"
    assert report["gates"]["lock"] == "locked"
    assert report["gates"]["cooldown"] == "UNKNOWN"
    assert report["gates"]["failure-fence"] == "UNKNOWN"
    assert not world.signals and not world.injected


def test_given_cooldown_when_second_restart_then_refusal(world):
    world.settings["restart_session"]["cooldown_seconds"] = 3600
    outcome = world.cli("-X", SESSION_ID)
    assert outcome[0] == 0, outcome
    # The old record is left present to verify cooldown precedes every act gate.
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "cooldown"  # stale dead records do not create ambiguity
    world.registry.unlink()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "cooldown"
    assert len(world.signals) == 1


def test_given_failure_when_cooldown_expires_then_auto_suppressed(world, monkeypatch):
    world.commands({})
    assert world.cli("-X", SESSION_ID)[1]["reason"] == "verification-incomplete"
    world.registry.unlink()
    monkeypatch.setattr(config, "load_config", lambda: world.settings)
    report = restart.restart(SESSION_ID, automatic=True)
    assert report["reason"] == "previous-verification-failed" and len(world.signals) == 1


@pytest.mark.parametrize("phase", ["pre-step", "relaunch"])
def test_given_contenders_when_locked_then_one_action(world, monkeypatch, phase):
    entered = threading.Event()
    proceed = threading.Event()
    if phase == "pre-step":
        world.settings["restart_session"]["context_threshold_pct"] = 1

        def pre_step(*args):
            entered.set()
            assert proceed.wait(5)
            return "pre-step unavailable"

        monkeypatch.setattr(restart, "context_pre_step", pre_step)
    else:
        original = restart._verify_replacement

        def verify(*args):
            entered.set()
            assert proceed.wait(5)
            return original(*args)

        monkeypatch.setattr(restart, "_verify_replacement", verify)
    monkeypatch.setattr(config, "load_config", lambda: world.settings)
    reports = []
    actor = threading.Thread(target=lambda: reports.append(restart.restart(SESSION_ID)))
    actor.start()
    try:
        assert entered.wait(5)
        # Force the contender to resolve the same original candidate, not pick a replacement.
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(restart, "resolve_target", lambda target: (world.registry, world.record))
            assert restart.restart(SESSION_ID)["reason"] == "locked"
    finally:
        proceed.set()
        actor.join(5)
    assert not actor.is_alive()
    assert len(world.signals) == (0 if phase == "pre-step" else 1)
    if phase == "relaunch":
        assert restart._object(restart.state_paths(world.session)[1])["last_restart"] > 0


def test_given_revalidation_when_busy_then_signal_unreachable(world, monkeypatch):
    original = restart.prepare_request

    def prepare(*args):
        request = original(*args)
        world.record["status"] = "busy"
        world.write()
        return request

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy" and not world.signals
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"


@pytest.mark.parametrize("signal_supported", [False, True])
@pytest.mark.parametrize("birth_type", [int, str])
def test_given_ignored_term_when_grace_expires_then_exact_kill(world, monkeypatch, signal_supported, birth_type):
    world.record["procStart"] = birth_type(101)
    world.write()
    monkeypatch.setattr(restart, "_signal_supported", lambda: signal_supported)
    # The signal and process are simulated; Windows must use injection instead.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    world.commands({"inject": ["inject", "{tmux_pane}", "{command}"], "observer": ["observer"]})

    def kill(pid, sig):
        world.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            world.replace()

    monkeypatch.setattr(restart.os, "kill", kill)
    assert world.cli("-X", SESSION_ID)[0] == 0
    if signal_supported:
        assert world.signals == [(101, signal.SIGTERM), (101, signal.SIGKILL)]
        assert not world.injected
    else:
        assert not world.signals
        assert world.injected == [["inject", "%1", "/exit"]]


def test_given_reused_pid_when_escalating_then_no_kill(world, monkeypatch):
    def kill(pid, sig):
        world.signals.append((pid, sig))
        world.identities[101] = ProcessIdentity("fake", 999)

    monkeypatch.setattr(restart.os, "kill", kill)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("identity", "restart-fenced")
    assert world.signals == [(101, signal.SIGTERM)]


@pytest.mark.parametrize("unix_family_available", [False, True])
def test_given_fixture_when_passively_probed_then_local_verified(request, monkeypatch, unix_family_available):
    if not unix_family_available:
        monkeypatch.delattr(restart.socket, "AF_UNIX", raising=False)
    world = request.getfixturevalue("world")
    world.replace()
    row = restart._object(world.registry.parent / "201.json")
    assert restart.local_verdict(row) == "VERIFIED", (row, restart._run(["claude", "agents", "--json"]))


def test_given_unavailable_socket_family_when_passively_probed_then_local_unknown(world, monkeypatch):
    world.replace()
    row = restart._object(world.registry.parent / "201.json")
    monkeypatch.delattr(restart.socket, "AF_UNIX")
    assert restart.local_verdict(row) == "UNKNOWN"
    assert not world.signals and not world.injected


@pytest.mark.parametrize(
    "fault", ["none", "missing-transcript", "expired", "generation", "old-child", "script", "malformed", "crash"]
)
def test_given_pin_when_validated_then_fence_preserved(world, monkeypatch, fault):
    settings = config.read_restart_config(world.settings)
    with restart.session_lock(world.session):
        managed = restart.managed_gate(world.record, settings)
        request = restart.prepare_request(world.record, managed, settings)
        request["status"] = "acting"
        _, _, path = restart.state_paths(world.session)
        restart._atomic_json(path, request)
        if fault == "old-child":
            world.identities[102] = ProcessIdentity("fake", 999)
            with pytest.raises(restart.Refusal, match="restart-fenced"):
                restart._validate_request(request, world.session, child=False)
            assert not world.signals
            return
        world.probe.has_ended = lambda pid: pid in (101, 102)
        monkeypatch.setattr(restart.os, "getppid", lambda: 104)
        monkeypatch.chdir(world.cwd)
        if fault == "missing-transcript":
            world.transcript.unlink()
        elif fault == "expired":
            request["expires_at"] = 0
            restart._atomic_json(path, request)
        elif fault == "generation":
            world.generation = "changed"
        elif fault == "script":
            world.script.write_text("changed", encoding="utf-8")
        elif fault == "malformed":
            path.write_text("{", encoding="utf-8")
        elif fault == "crash":
            world.identities[request["owner_pid"]] = ProcessIdentity("fake", 999)
        code, stdout, _ = run_cli(["ai", "internal", "restart-pin", world.session, world.record["name"]])
        assert code == (0 if fault == "none" else 1)
        assert stdout.strip() == (str(world.transcript) if fault == "none" else "")
        assert path.exists()  # never consumed, even by a successful resolver query
        if fault == "none":
            assert restart.child_pin(world.session, world.record["name"])[0] == 1


def test_given_no_request_when_resolving_then_ordinary_launch(world):
    assert restart.child_pin(world.session, world.record["name"]) == (3, "")


def test_given_wrong_replacement_when_verified_then_resume_mismatch(world, monkeypatch):
    def replace(pid, sig):
        world.signals.append((pid, sig))
        world.ended = True
        (world.registry.parent / "201.json").write_text(
            json.dumps({**world.record, "pid": 201, "procStart": 201, "sessionId": OTHER_ID}), encoding="utf-8"
        )

    monkeypatch.setattr(restart.os, "kill", replace)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "resume-mismatch"
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"


def test_given_crash_when_verifying_then_durable_failure(world, monkeypatch):
    monkeypatch.setattr(restart, "_verify_replacement", Mock(side_effect=KeyboardInterrupt))
    code, _, _ = run_cli(["ai", "-X", SESSION_ID], config=world.settings)
    assert code == 1
    _, result, request = restart.state_paths(world.session)
    assert restart._object(result)["failed"] is True
    assert restart._object(result)["reason"] == "interrupted"
    assert restart._object(request)["status"] == "acting"


@pytest.mark.parametrize("fault", ["status", "background", "context", "activation", "generation", "old-child"])
def test_given_last_check_when_gate_changes_then_no_action(world, monkeypatch, fault):
    original = restart.prepare_request

    def prepare(*args):
        request = original(*args)
        if fault == "status":
            world.record["status"] = "busy"
        elif fault == "background":
            world.rows[-1]["pendingBackgroundAgentCount"] = 1
        elif fault == "context":
            world.rows[0]["message"]["usage"]["input_tokens"] = 100_000
        elif fault == "activation":
            (world.root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
        elif fault == "generation":
            world.generation = "changed"
        else:
            world.identities[102] = ProcessIdentity("fake", 999)
        world.write()
        return request

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("busy", "background-work", "context", "rc-activation", "identity")
    assert not world.signals


@pytest.mark.parametrize("phase", ["term", "exit", "fallback", "kill"])
def test_given_ac_f1_busy_during_helper_probe_when_acting_then_no_further_action(world, monkeypatch, phase):
    world.commands({"inject": ["inject"], "observer": ["observer"]})
    if phase == "exit":
        world.settings["restart_session"]["mechanism"] = "exit"
    attempts = []

    def kill(pid, sig):
        attempts.append((pid, sig))
        if phase == "fallback":
            raise PermissionError("signal access denied")

    monkeypatch.setattr(restart.os, "kill", kill)

    def run(argv, **kwargs):
        if _is_restart_probe(argv, world.pane_ai):
            request_path = restart.state_paths(world.session)[2]
            acting = request_path.exists() and restart._object(request_path).get("status") == "acting"
            if acting and (phase in ("term", "exit") or attempts):
                world.record["status"] = "busy"
                world.registry.write_text(json.dumps(world.record), encoding="utf-8")
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy"
    assert attempts == ([(101, signal.SIGTERM)] if phase in ("fallback", "kill") else [])
    assert not world.injected


@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("flag", ["-X", "-Y"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_given_remote_slot_when_restart_then_remote_resolution_only(world, monkeypatch, json_output, flag, exit_code):
    calls = []
    monkeypatch.setattr(main._config, "get_remote_machine", lambda *a: {"host": "example.com", "user": "user"})
    monkeypatch.setattr(main, "_resolve_remote_shell", lambda ssh: "bash")

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=exit_code, stdout=json.dumps({"reason": "ready"}))

    args = ["ai", "c", "1", "-R", "-p", "myproject", flag]
    if json_output:
        args.append("-j")
    with patch.object(main.subprocess, "run", run):
        code, out, _ = run_cli(args, config=world.settings)
    assert code == exit_code
    if json_output:
        assert json.loads(out)["reason"] == "ready"
    else:
        assert "reason=ready" in out and not out.lstrip().startswith("{")
    assert calls[0][-1] == f"bash -l -c 'ai c 1 -p myproject --is-remote {flag} -j'"
    assert not world.signals


@pytest.mark.parametrize("changed_birth", [False, True])
def test_given_mac_refresh_when_revalidated_then_original_program_proof_is_fenced(request, monkeypatch, changed_birth):
    # Model native Windows discovery before the fixture switches to macOS.
    monkeypatch.setattr(
        main._session_script, "resolve_session_shell", lambda: "bash" if sys.platform == "darwin" else "bash.exe"
    )
    world = request.getfixturevalue("world")
    # Executable discovery stays native while this test simulates macOS provenance.
    monkeypatch.setattr(restart.shutil, "which", lambda name, *, path: str(world.pane_ai))
    monkeypatch.setattr(restart.sys, "platform", "darwin")
    world.script.write_text(world.script.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    reads = []

    def running_source(pid, path):
        reads.append(pid)
        if len(reads) > 2:
            raise restart.Refusal("supervisor-upgrade-required", "old inode unavailable after refresh")
        return path.read_text(encoding="utf-8")

    monkeypatch.setattr(restart, "_script_source", running_source)
    original = main._write_launch_script_if_changed

    def refresh(path, text):
        original(path, text)
        if changed_birth:
            world.identities[102] = ProcessIdentity("fake", 999)

    monkeypatch.setattr(main, "_write_launch_script_if_changed", refresh)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert reads == [103, 102]
    assert code == (1 if changed_birth else 0)
    assert report["reason"] == ("identity" if changed_birth else "restarted")
    assert bool(world.signals) is not changed_birth


@pytest.mark.parametrize("kind", ["unknown", "override", "auto", "manual", "old-auto", "null", "cached", "extended"])
def test_given_context_evidence_when_checked_then_conservative_window(world, kind):
    if kind in ("unknown", "override"):
        world.rows[0]["message"]["model"] = "unknown-model"
        if kind == "override":
            world.settings["restart_session"]["context_window_tokens"] = 200_000
    elif kind in ("auto", "manual", "old-auto"):
        world.rows.insert(
            0,
            {
                "type": "system",
                "subtype": "compact_boundary",
                "compactMetadata": {"trigger": "manual" if kind == "manual" else "auto"},
            },
        )
        world.rows[1]["message"]["usage"] = None
        if kind == "old-auto":
            world.settings["restart_session"]["compaction_fresh_turns"] = 0
    elif kind == "null":
        world.rows[0]["message"]["usage"] = None
    elif kind == "cached":
        world.rows[0]["message"]["usage"]["cache_read_input_tokens"] = 100_000
    else:
        world.rows[0]["message"]["model"] += "[1m]"
        world.rows[0]["message"]["usage"]["input_tokens"] = 150_000
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    expected = kind in ("override", "auto", "extended")
    assert code == (0 if expected else 1)
    assert report["reason"] == ("restarted" if expected else "context")
    if not expected:
        assert not world.signals and not world.injected


@pytest.mark.parametrize("key", ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"])
@pytest.mark.parametrize("value", [None, True, "0", -1, float("nan"), float("inf")])
def test_given_invalid_token_count_when_restart_then_context_refuses(world, key, value):
    world.rows[0]["message"]["usage"][key] = value
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert not world.signals


@pytest.mark.parametrize("key", ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"])
def test_given_missing_token_count_when_restart_then_context_refuses(world, key):
    del world.rows[0]["message"]["usage"][key]
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert not world.signals


def test_given_text_check_when_cli_then_gates_printed(world):
    code, out, err = run_cli(["ai", "-Y", SESSION_ID], config=world.settings)
    assert code == 0
    assert "gates=" in out and "triggers=" in out and "mobile=UNVERIFIED" in out
    assert not world.signals


@pytest.mark.parametrize("fault", ["timeout", "exit", "malformed", "future", "truncated"])
def test_given_bad_observer_when_restart_then_incomplete(world, monkeypatch, fault):
    original = restart._run

    def run(argv, *args):
        if argv[0] != "observer":
            return original(argv, *args)
        if fault in ("timeout", "exit"):
            raise restart.Refusal("probe-unavailable")
        if fault == "malformed":
            return "{"
        return json.dumps(
            {
                "status": "seen",
                "complete": True,
                "observed_at": time.time() + (1000 if fault == "future" else 0),
                "truncated": fault == "truncated",
                "rows": [],
            }
        )

    monkeypatch.setattr(restart, "_run", run)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "verification-incomplete" and report["local"] == "VERIFIED"


def test_given_signal_denied_when_injector_exists_then_fallback(world, monkeypatch):
    world.commands({"inject": ["inject", "{command}"], "observer": ["observer"]})
    monkeypatch.setattr(restart.os, "kill", Mock(side_effect=PermissionError))
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.injected == [["inject", "/exit"]]


def test_given_detached_helper_when_idle_arrives_then_all_gates_applied(world, monkeypatch):
    world.record["status"] = "busy"
    world.settings["restart_session"]["compaction_wait_seconds"] = 5
    world.write()

    def idle_after_return(seconds):
        world.record["status"] = "idle"
        world.write()

    monkeypatch.setattr(restart.time, "sleep", idle_after_return)
    code, _, _ = run_cli(["ai", "internal", "restart-detached", SESSION_ID], config=world.settings)
    assert code == 0 and world.signals == [(101, signal.SIGTERM)]


def test_given_detached_busy_when_wait_expires_then_no_signal(world):
    world.record["status"] = "busy"
    world.settings["restart_session"]["compaction_wait_seconds"] = 0
    world.write()
    code, _, _ = run_cli(["ai", "internal", "restart-detached", SESSION_ID], config=world.settings)
    assert code == 1 and not world.signals


def test_given_completed_but_malformed_pin_when_read_then_no_fallback(world):
    path = restart.state_paths(world.session)[2]
    restart._atomic_json(path, {"status": "complete"})
    assert restart.child_pin(world.session, world.record["name"]) == (1, "")
    assert path.exists()


def test_given_observer_race_when_generation_changes_then_verification_fails(world):
    def observe(data):
        world.generation = "other-generation"
        return data

    world.observer = observe
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "verification-failed"
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"
