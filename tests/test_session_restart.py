"""CLI behaviour with fixture registries, transcripts, process probes and tmux."""

from __future__ import annotations

import json
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from conftest import run_cli

from ai_cli import config, main
from ai_cli import session_restart as restart
from ai_cli.process_probe import ProcessIdentity, StartTimeMatch

SESSION_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup": true}')
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
    (state / f"session-meta-{session}.json").write_text(json.dumps(meta))
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
        registry.write_text(json.dumps(record))
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))

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

    monkeypatch.setattr(restart.psutil, "Process", Process)
    monkeypatch.setattr(restart, "_script_source", lambda pid, path: path.read_text())

    def subprocess_run(argv, **kwargs):
        if argv[0] == "tmux":
            fields = [session, "%1", "103", state_obj.generation]
            if "pane_start_command" in argv[-1]:
                fields.append(f"bash {script}")
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

    monkeypatch.setattr(restart.subprocess, "run", subprocess_run)
    state_obj.subprocess_run = subprocess_run
    connection = MagicMock()
    monkeypatch.setattr(restart.socket, "socket", lambda *a: connection)
    state_obj.connection = connection

    def replace():
        state_obj.ended = True
        replacement = {**record, "pid": 201, "procStart": 201}
        (registry.parent / "201.json").write_text(json.dumps(replacement))

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
    return state_obj


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
    monkeypatch.setattr(restart.subprocess, "Popen", spawn)
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
        (world.registry.parent / "duplicate.json").write_text(json.dumps({**world.record, "pid": 202}))
    elif kind == "dead":
        world.ended = True
    elif kind == "birth":
        world.record["procStart"] = 999
        world.write()
    elif kind == "pane":
        monkeypatch.setattr(restart, "_run", lambda *a: "wrong\t%1\t103\tgeneration-1\tbash /tmp/other.sh")
    else:
        world.script.write_text("echo unsupported\n")
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
    "kind", ["agent-count", "counter-missing", "shell", "agent", "descendant", "unreadable", "truncated"]
)
def test_given_work_when_restart_then_refusal(world, monkeypatch, kind):
    if kind == "agent-count":
        world.rows[-1]["pendingBackgroundAgentCount"] = 1
    elif kind == "counter-missing":
        del world.rows[-1]["pendingBackgroundAgentCount"]
    elif kind in ("shell", "agent"):
        result = (
            {"backgroundTaskId": "task-1"} if kind == "shell" else {"status": "async_launched", "agentId": "agent-1"}
        )
        world.rows.insert(0, {"type": "user", "toolUseResult": result})
    elif kind == "descendant":
        world.children.append(SimpleNamespace(pid=501, ppid=lambda: 101, cmdline=lambda: ["sleep", "300"]))
    world.write()
    if kind == "unreadable":
        world.transcript.write_text("{")
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
        path.write_text('{"remoteControlAtStartup":false}')
    elif kind == "unreadable":
        path.write_text("{")
    else:
        project = world.cwd / ".claude"
        project.mkdir()
        (project / "settings.json").write_text('{"remoteControlAtStartup":false}')
        if kind == "local-true":
            (project / "settings.local.json").write_text('{"remoteControlAtStartup":true}')
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


def test_given_no_injector_when_exit_then_refusal(world):
    world.settings["restart_session"]["mechanism"] = "exit"
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


def test_given_ignored_term_when_grace_expires_then_exact_kill(world, monkeypatch):
    def kill(pid, sig):
        world.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            world.replace()

    monkeypatch.setattr(restart.os, "kill", kill)
    assert world.cli("-X", SESSION_ID)[0] == 0
    assert world.signals == [(101, signal.SIGTERM), (101, signal.SIGKILL)]


def test_given_reused_pid_when_escalating_then_no_kill(world, monkeypatch):
    def kill(pid, sig):
        world.signals.append((pid, sig))
        world.identities[101] = ProcessIdentity("fake", 999)

    monkeypatch.setattr(restart.os, "kill", kill)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("identity", "restart-fenced")
    assert world.signals == [(101, signal.SIGTERM)]


def test_given_fixture_when_passively_probed_then_local_verified(world):
    world.replace()
    row = restart._object(world.registry.parent / "201.json")
    assert restart.local_verdict(row) == "VERIFIED", (row, restart._run(["claude", "agents", "--json"]))


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
            world.script.write_text("changed")
        elif fault == "malformed":
            path.write_text("{")
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
            json.dumps({**world.record, "pid": 201, "procStart": 201, "sessionId": OTHER_ID})
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
            (world.root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup":false}')
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


def test_given_remote_slot_when_restart_then_remote_resolution_only(world, monkeypatch):
    calls = []
    monkeypatch.setattr(main._config, "get_remote_machine", lambda *a: {"host": "example.com", "user": "user"})
    monkeypatch.setattr(main, "_resolve_remote_shell", lambda ssh: "bash")

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"reason": "restarted"}))

    monkeypatch.setattr(main.subprocess, "run", run)
    code, report, _ = world.cli("c", "1", "-R", "-p", "myproject", "-X")
    assert code == 0 and report["reason"] == "restarted"
    assert calls[0][-1] == "bash -l -c 'ai c 1 -p myproject --is-remote -X -j'"
    assert not world.signals


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
