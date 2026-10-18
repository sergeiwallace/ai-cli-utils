"""Automatic evaluation uses fake sessions and never touches a live pane."""

import json
import signal
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest
from conftest import run_cli
from test_session_restart import pre_step_world as pre_step_world
from test_session_restart import world as world

from ai_cli import config
from ai_cli import session_restart as restart


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: tmp_path)
    monkeypatch.setattr(restart.Path, "home", lambda: tmp_path)
    return {"restart_session": {"auto": True, "auto_interval_seconds": 600}}


def heartbeat(settings, session="session-1"):
    return run_cli(["ai", "internal", "publish-heartbeat", session, "{}"], config=settings)


def test_given_ac_4_1_ac_4_6_due_heartbeat_when_published_then_evaluator_detaches(scheduler):
    with patch.object(restart.subprocess, "Popen") as spawn:
        code, _, _ = heartbeat(scheduler)
        assert code == 0
        spawn.assert_called_once_with(
            [sys.executable, "-m", "ai_cli.main", "internal", "restart-auto", "session-1"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        spawn.return_value.wait.assert_not_called()
        spawn.return_value.communicate.assert_not_called()


def test_given_ac_4_1_ac_4_5_recent_evaluation_when_heartbeat_then_throttled_per_session(scheduler, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(restart.time, "time", lambda: clock[0])
    with patch.object(restart.subprocess, "Popen") as spawn:
        assert heartbeat(scheduler)[0] == 0
        clock[0] += 599
        assert heartbeat(scheduler)[0] == 0
        assert spawn.call_count == 1
        assert heartbeat(scheduler, "session-2")[0] == 0
        assert spawn.call_count == 2
        clock[0] += 1
        assert heartbeat(scheduler)[0] == 0
        assert spawn.call_count == 3


@pytest.mark.parametrize("settings", [{}, {"restart_session": {"auto": False}}])
def test_given_ac_4_3_disabled_when_heartbeat_then_no_evaluator(scheduler, settings):
    with patch.object(restart.subprocess, "Popen") as spawn:
        assert heartbeat(settings)[0] == 0
        spawn.assert_not_called()


@pytest.fixture
def auto_world(world):
    world.settings["restart_session"]["auto"] = True
    world.settings["restart_session"]["compaction_wait_seconds"] = 0
    world.footer = ""

    def run(argv, **kwargs):
        if argv[:2] == ["tmux", "capture-pane"]:
            return Mock(returncode=0, stdout=world.footer)
        if argv == ["claude", "agents", "--json"] and not world.ended:
            return Mock(returncode=0, stdout=json.dumps([world.record]))
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        yield world


def evaluate(world):
    return run_cli(["ai", "internal", "restart-auto", world.session], config=world.settings)


def log_rows():
    return [
        json.loads(line)
        for line in (config.get_xdg_state_home() / "restart-session" / "auto.log").read_text().splitlines()
    ]


@pytest.mark.parametrize("trigger", ["update", "unreachable"])
def test_given_ac_4_2_ac_4_5_one_positive_trigger_when_evaluated_then_guarded_restart(auto_world, trigger):
    if trigger == "update":
        auto_world.footer = "✓ Update installed · Restart to apply"
    else:
        auto_world.observer = lambda data: {**data, "status": "absent", "rows": []} if not auto_world.ended else data
    assert evaluate(auto_world)[0] == 0
    assert auto_world.signals == [(101, signal.SIGTERM)]
    row = log_rows()[0]
    assert row["triggers"][trigger] == "YES"
    assert row["triggers"]["unreachable" if trigger == "update" else "update"] != "YES"
    assert row["outcome"] == "restarted"


def test_given_ac_4_2_ac_4_4_no_trigger_when_evaluated_twice_then_no_action_and_one_line_each(auto_world):
    assert evaluate(auto_world)[0] == 0
    assert evaluate(auto_world)[0] == 0
    assert not auto_world.signals and not auto_world.injected
    rows = log_rows()
    assert len(rows) == 2
    assert all(row["session"] == auto_world.session and row["outcome"] == "no-trigger" for row in rows)
    assert all(row["triggers"]["unreachable"] == "NO" and row["triggers"]["update"] != "YES" for row in rows)


def test_given_ac_4_2_ac_4_5_trigger_with_background_work_when_evaluated_then_gate_refuses(auto_world):
    auto_world.footer = "Update installed · Restart to apply"
    auto_world.rows[-1]["pendingBackgroundAgentCount"] = 1
    auto_world.write()
    assert evaluate(auto_world)[0] == 1
    assert not auto_world.signals and not auto_world.injected
    assert log_rows()[0]["outcome"] == "background-work"


@pytest.mark.parametrize("verdict", ["LOCAL_ONLY", "UNKNOWN"])
def test_given_ac_4_5_old_unobserved_verdict_when_evaluated_then_no_action(auto_world, monkeypatch, verdict):
    auto_world.record["statusUpdatedAt"] = 1
    auto_world.write()
    monkeypatch.setattr(restart, "trigger_verdicts", lambda *args: {"update": "NO", "unreachable": verdict})
    assert evaluate(auto_world)[0] == 0
    assert not auto_world.signals and not auto_world.injected
    assert log_rows()[0]["outcome"] == "no-trigger"


@pytest.mark.parametrize("fault", ["timeout", "nonzero", "malformed", "truncated", "notice"])
def test_given_ac_4_5_unknown_observer_when_evaluated_then_no_absence_restart(auto_world, fault):
    def run(argv, **kwargs):
        if argv[0] == "observer":
            if fault == "timeout":
                raise subprocess.TimeoutExpired(argv, 15)
            data = {"complete": True, "status": "absent", "observed_at": restart.time.time(), "rows": []}
            if fault == "truncated":
                data["truncated"] = True
            if fault == "notice":
                data["notice"] = "sessions beyond the first pages are missing from this listing"
            return Mock(
                returncode=1 if fault == "nonzero" else 0, stdout="{" if fault == "malformed" else json.dumps(data)
            )
        if argv[:2] == ["tmux", "capture-pane"]:
            return Mock(returncode=0, stdout="")
        if argv == ["claude", "agents", "--json"]:
            return Mock(returncode=0, stdout=json.dumps([auto_world.record]))
        return auto_world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        assert evaluate(auto_world)[0] == 0
    assert not auto_world.signals and not auto_world.injected
    assert log_rows()[0]["triggers"]["unreachable"] == "UNKNOWN"


def test_given_ac_4_2_high_context_when_automatic_then_save_state_precedes_restart(pre_step_world, monkeypatch):
    pre_step_world.settings["restart_session"]["auto"] = True
    monkeypatch.setattr(restart, "trigger_verdicts", lambda *args: {"update": "YES", "unreachable": "NO"})
    assert evaluate(pre_step_world)[0] == 0
    assert pre_step_world.injected == [["inject", "%1", "/save-state"]]
    assert pre_step_world.signals == [(101, signal.SIGTERM)]


def test_given_ac_4_4_invalid_target_when_evaluated_then_refusal_is_logged(scheduler):
    assert run_cli(["ai", "internal", "restart-auto", "session-1"], config=scheduler)[0] == 1
    assert log_rows()[0] == {
        "session": "session-1",
        "triggers": {"update": "UNKNOWN", "unreachable": "UNKNOWN"},
        "outcome": "target",
    }


@pytest.mark.parametrize("fault", ["locked", "bad-config", "bad-state", "spawn"])
def test_given_ac_4_1_schedule_failure_when_heartbeat_then_publication_survives(scheduler, fault):
    if fault == "bad-config":
        scheduler["restart_session"]["auto_interval_seconds"] = "invalid"
    if fault == "bad-state":
        path = restart.state_paths("session-1")[0].with_suffix(".auto.json")
        path.parent.mkdir(parents=True)
        path.write_text("{} broken", encoding="utf-8")
    with patch.object(restart.subprocess, "Popen", side_effect=OSError) as spawn:
        if fault == "locked":
            with restart.session_lock("session-1"):
                assert heartbeat(scheduler)[0] == 0
        else:
            assert heartbeat(scheduler)[0] == 0
        assert spawn.call_count == (1 if fault == "spawn" else 0)


@pytest.mark.parametrize(
    ("installed", "expected"), [("2.2.0", "YES"), ("2.1.1", "NO"), ("2.0.9", "NO"), ("invalid", "UNKNOWN")]
)
def test_given_ac_4_2_native_version_when_evaluated_then_verified_newer_version_triggers(
    auto_world, monkeypatch, installed, expected
):
    original_resolve = restart.Path.resolve
    binary = auto_world.root / ".local" / "bin" / "claude"
    target = auto_world.root / ".local" / "share" / "claude" / "versions" / installed
    monkeypatch.setattr(
        restart.Path, "resolve", lambda path, **kwargs: target if path == binary else original_resolve(path, **kwargs)
    )
    assert evaluate(auto_world)[0] == 0
    assert log_rows()[0]["triggers"]["update"] == expected
    assert len(auto_world.signals) == (1 if expected == "YES" else 0)


def test_given_ac_4_2_failed_verification_when_automatic_retried_then_no_second_action(auto_world):
    auto_world.footer = "Update installed · Restart to apply"
    auto_world.commands({})
    assert evaluate(auto_world)[0] == 1
    assert log_rows()[0]["outcome"] == "verification-incomplete"
    assert len(auto_world.signals) == 1
    assert evaluate(auto_world)[0] == 1
    assert log_rows()[1]["outcome"] == "previous-verification-failed"
    assert len(auto_world.signals) == 1


@pytest.mark.parametrize(
    "fault", [ConnectionRefusedError(111, "refused"), PermissionError(), FileNotFoundError(), TimeoutError()]
)
def test_given_ac_4_5_socket_probe_error_when_trigger_checked_then_only_refusal_is_positive(auto_world, fault):
    import errno

    fault.errno = errno.ECONNREFUSED if isinstance(fault, ConnectionRefusedError) else fault.errno
    auto_world.connection.__enter__.return_value.connect.side_effect = fault
    assert evaluate(auto_world)[0] == (1 if isinstance(fault, ConnectionRefusedError) else 0)
    assert log_rows()[0]["triggers"]["unreachable"] == (
        "YES" if isinstance(fault, ConnectionRefusedError) else "UNKNOWN"
    )
    if not isinstance(fault, ConnectionRefusedError):
        assert not auto_world.signals and not auto_world.injected


def test_given_ac_4_3_disabled_after_spawn_when_evaluated_then_no_action(auto_world):
    auto_world.settings["restart_session"]["auto"] = False
    auto_world.footer = "Update installed · Restart to apply"
    assert evaluate(auto_world)[0] == 0
    assert not auto_world.signals and not auto_world.injected
    assert log_rows()[0]["outcome"] == "disabled"


def test_given_ac_4_2_target_moved_after_probe_when_restarting_then_no_other_session_is_touched(
    auto_world, monkeypatch
):
    def triggers(*args):
        auto_world.record["tmux"] = "session-2:@2.%2"
        auto_world.write()
        return {"update": "YES", "unreachable": "NO"}

    monkeypatch.setattr(restart, "trigger_verdicts", triggers)
    assert evaluate(auto_world)[0] == 1
    assert log_rows()[0]["outcome"] == "target"
    assert not auto_world.signals and not auto_world.injected


def test_given_ac_4_2_configured_title_drift_when_evaluated_then_restart_identity_gate_is_preserved(auto_world):
    auto_world.record["name"] = "generated-title"
    auto_world.write()
    assert evaluate(auto_world)[0] == 1
    assert log_rows()[0]["triggers"]["unreachable"] == "YES"
    assert log_rows()[0]["outcome"] == "supervisor-upgrade-required"
    assert not auto_world.signals and not auto_world.injected
