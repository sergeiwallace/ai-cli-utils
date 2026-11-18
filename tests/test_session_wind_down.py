"""``ai sessions wind-down`` ends a session for a maintenance window so the relaunch brings it back.

A clean exit through the session's own wrapper records ``manual_exit``, which keeps a
session down on every boot. A wind-down records ``maintenance_wind_down`` on the record
first, then drives that same clean exit, and the exit keeps the wind-down's cause.
"""

import json
import sys
import time
from copy import deepcopy

import pytest
from click.testing import CliRunner
from conftest import run_cli
from test_restart_supervisor import SESSION, _wait_for
from test_restart_supervisor import supervisor_fixture as supervisor_fixture
from test_session_boot_relaunch import boot_cli as boot_cli
from test_session_boot_relaunch import boot_record as boot_record
from test_session_boot_relaunch import detached_launch as detached_launch
from test_sessions_stop import _launches, _list_slot_while_supervisor_runs

from ai_cli import main, session_commands
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state

NAME = "c-myproject-host-1"
GENERATION = "generation-1"


def _cli(*args):
    return CliRunner().invoke(main._cli_group, ["sessions", *args])


def _record(name=NAME):
    return next(r for r in registry.load_registry()["sessions"] if r["name"] == name)


@pytest.fixture
def live_session(boot_cli, monkeypatch):
    """A running owned session whose stop drives the wrapper's clean exit, as ``ai sessions stop`` does."""
    registry._mutate(lambda doc: doc["sessions"][0].update(generation=GENERATION, transition_seq=1) or True)
    alive = {NAME}
    stops = []
    monkeypatch.setattr(registry, "_local_session_alive", lambda name: name in alive)

    def stop_session(name, *, timeout):
        stops.append((name, timeout))
        tmux_name = next(n for n in alive if n.endswith(f"-{name}"))
        # The wrapper's final exit: the lifecycle helper's `sessions mark ... exited --cause manual_exit`.
        result = _cli(
            "mark",
            tmux_name,
            "exited",
            "-c",
            "manual_exit",
            "-e",
            '{"source": "supervisor"}',
            "--generation",
            _record(tmux_name)["generation"],
            "--sequence",
            str(time.monotonic_ns()),
        )
        assert result.exit_code == 0, result.output
        alive.discard(tmux_name)
        return session_commands.StopReport(name=name, marked=[tmux_name], supervisors=[1])

    monkeypatch.setattr(session_commands, "stop_session", stop_session)
    boot_cli.alive = alive
    boot_cli.stops = stops
    return boot_cli


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_given_wound_down_session_when_next_boot_relaunch_runs_then_it_is_relaunched(
    live_session, monkeypatch, platform
):
    monkeypatch.setattr(session_commands.sys, "platform", platform)

    wound = _cli("wind-down", NAME)
    boot = live_session.invoke()

    assert wound.exit_code == 0, wound.output
    assert boot.exit_code == 0, boot.output
    assert [record["name"] for record in live_session.launches] == [NAME]
    assert _record()["boot_decision"]["cause"] == "maintenance_wind_down"
    assert f"{NAME}: relaunched detached" in boot.output


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_given_one_wound_down_and_one_manually_exited_when_boot_relaunch_runs_then_only_the_wound_down_returns(
    live_session, monkeypatch, platform
):
    monkeypatch.setattr(session_commands.sys, "platform", platform)
    sibling = "c-myproject-host-2"

    def add_sibling(doc):
        record = deepcopy(doc["sessions"][0])
        record.update(id="record-2", name=sibling)
        record["boot_recipe"].update(session_name=sibling, argv=["ai", "c", sibling])
        doc["sessions"].append(record)
        return True

    registry._mutate(add_sibling)
    live_session.alive.add(sibling)

    wound = _cli("wind-down", NAME)
    # A person's own exit of the sibling goes through the same wrapper path, with no wind-down.
    stopped = session_commands.stop_session("myproject-host-2", timeout=1)
    boot = live_session.invoke()

    assert wound.exit_code == 0, wound.output
    assert stopped.stopped
    assert _record(sibling)["exit"]["cause"] == "manual_exit"
    assert [record["name"] for record in live_session.launches] == [NAME]
    assert f"{sibling}: cause manual_exit forbids boot relaunch" in boot.output


def test_given_wind_down_request_when_clean_exit_marks_manual_exit_then_cause_stays_maintenance_wind_down(
    live_session,
):
    result = _cli("wind-down", "-t", "5", NAME)

    assert result.exit_code == 0, result.output
    record = _record()
    assert record["run_state"] == "exited"
    assert record["exit"]["cause"] == "maintenance_wind_down"
    assert record["supervisor_exit"]["cause"] == "maintenance_wind_down"
    assert "wind_down" not in record
    assert live_session.stops == [("myproject-host-1", 5.0)]
    assert "maintenance_wind_down" in result.output


def test_given_wind_down_from_an_earlier_generation_when_a_later_run_exits_by_hand_then_it_is_manual_exit(
    live_session,
):
    registry._mutate(
        lambda doc: (
            doc["sessions"][0].update(wind_down={"cause": "maintenance_wind_down", "generation": "older"}) or True
        )
    )

    assert state.mark_exited(NAME, "manual_exit", {}, generation=GENERATION)

    assert _record()["exit"]["cause"] == "manual_exit"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_given_wound_down_and_manual_records_when_boot_dry_run_then_each_cause_and_decision_prints(
    boot_cli, monkeypatch, platform
):
    monkeypatch.setattr(session_commands.sys, "platform", platform)

    def two_exits(doc):
        wound = doc["sessions"][0]
        wound.update(run_state="exited", supervisor_exit={"cause": "maintenance_wind_down"})
        manual = deepcopy(wound)
        manual.update(id="record-2", name="c-myproject-host-2", supervisor_exit={"cause": "manual_exit"})
        manual["boot_recipe"].update(session_name="c-myproject-host-2")
        doc["sessions"].append(manual)
        return True

    registry._mutate(two_exits)
    before = registry.registry_path().read_bytes()

    result = _cli("relaunch", "-b", "-d")

    assert result.exit_code == 0, result.output
    assert f"{NAME}: would relaunch detached: maintenance_wind_down" in result.output
    assert "c-myproject-host-2: cause manual_exit forbids boot relaunch" in result.output
    assert registry.registry_path().read_bytes() == before
    assert not boot_cli.launches


@pytest.mark.parametrize("policy", ["auto", "always"])
def test_given_wound_down_record_when_boot_checked_then_policy_allows_it_and_never_still_vetoes(boot_record, policy):
    boot_record.update(run_state="exited", supervisor_exit={"cause": "maintenance_wind_down"}, relaunch=policy)
    args = {"cfg": state.DEFAULTS | {"on_boot": True}, "host": "host-a", "boot_id": "current-boot", "alive": False}

    assert "maintenance_wind_down" in state.DEFAULTS["causes"]
    assert state.boot_veto(boot_record, **args) is None
    boot_record["relaunch"] = "never"
    assert state.boot_veto(boot_record, **args) == "policy never forbids boot relaunch"


def test_given_profile_listing_maintenance_wind_down_when_settings_load_then_it_is_accepted(monkeypatch):
    monkeypatch.setattr(
        state.config, "load_config", lambda **_kwargs: {"session_relaunch": {"causes": ["maintenance_wind_down"]}}
    )
    assert state.settings()["causes"] == ["maintenance_wind_down"]


def test_given_session_not_running_when_wound_down_then_refused_and_record_unchanged(live_session):
    live_session.alive.clear()
    before = registry.registry_path().read_bytes()

    result = _cli("wind-down", NAME)

    assert result.exit_code != 0
    assert f"{NAME}: not running; nothing to wind down" in result.output
    assert registry.registry_path().read_bytes() == before
    assert not live_session.stops


@pytest.mark.parametrize("change", ["foreign", "remote"])
def test_given_foreign_or_remote_session_when_wound_down_then_refused_without_stopping_it(live_session, change):
    update = {"owning_host": "host-b"} if change == "foreign" else {"kind": "remote"}
    registry._mutate(lambda doc: doc["sessions"][0].update(update) or True)
    before = registry.registry_path().read_bytes()

    result = _cli("wind-down", NAME)

    assert result.exit_code != 0
    assert f"{NAME}: foreign or remote session; not owned by this host" in result.output
    assert registry.registry_path().read_bytes() == before
    assert not live_session.stops


def test_given_unknown_session_when_wound_down_then_refused(live_session):
    result = _cli("wind-down", "c-myproject-host-9")

    assert result.exit_code != 0
    assert "unknown session: c-myproject-host-9" in result.output
    assert not live_session.stops


def test_given_running_session_when_wind_down_dry_run_then_it_prints_the_plan_and_changes_nothing(live_session):
    before = registry.registry_path().read_bytes()

    result = _cli("wind-down", "-d", NAME)

    assert result.exit_code == 0, result.output
    assert f"{NAME}: would record maintenance_wind_down and stop it" in result.output
    assert registry.registry_path().read_bytes() == before
    assert not live_session.stops


def test_given_session_still_running_after_timeout_when_wound_down_then_it_exits_nonzero(live_session, monkeypatch):
    monkeypatch.setattr(
        session_commands,
        "stop_session",
        lambda name, *, timeout: session_commands.StopReport(name=name, remaining=["supervisor pid 1"]),
    )

    result = _cli("wind-down", NAME)

    assert result.exit_code != 0
    assert "still running" in result.output
    assert _record()["wind_down"]["cause"] == "maintenance_wind_down"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_given_wound_down_session_and_no_reboot_when_relaunched_by_name_then_it_comes_back(
    live_session, detached_launch, monkeypatch, platform
):
    live_session.cfg["session_relaunch"]["on_boot"] = False
    monkeypatch.setattr(session_commands, "_launch_recipe", live_session.real_launch)
    monkeypatch.setattr(state.config, "load_config", lambda **_kwargs: live_session.cfg)
    monkeypatch.setattr(session_commands.sys, "platform", platform)
    monkeypatch.setattr(main.sys, "platform", platform)

    wound = _cli("wind-down", NAME)
    result = _cli("relaunch", NAME)

    assert wound.exit_code == 0, wound.output
    assert result.exit_code == 0, result.output
    assert f"{NAME}: relaunched detached" in result.output
    calls, _ = detached_launch
    created = [argv for argv, kw in calls if argv[:2] == ["tmux", "new-session"]]
    assert len(created) == 1 and created[0][created[0].index("-s") + 1] == NAME


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture")
@pytest.mark.real_tmux
@pytest.mark.parametrize("supervisor_fixture", ["current"], indirect=True)
def test_given_running_managed_session_when_wound_down_then_its_wrapper_exit_keeps_maintenance_wind_down(
    supervisor_fixture,
):
    root, _script, supervisor = supervisor_fixture
    _list_slot_while_supervisor_runs(root / "bin" / "tmux")
    _wait_for(lambda: _launches(root), supervisor)
    started = time.monotonic()
    _wait_for(lambda: time.monotonic() - started >= 4, supervisor)

    code, out, err = run_cli(
        ["ai", "sessions", "wind-down", SESSION, "-t", "60"], config={"machine": {"host_id": "host-a"}}
    )

    assert code == 0, out + err
    assert supervisor.wait(timeout=30) == 0
    record = next(r for r in registry.load_registry()["sessions"] if r["name"] == SESSION)
    assert record["exit"]["cause"] == "maintenance_wind_down", json.dumps(record)
    assert len(_launches(root)) == 1
