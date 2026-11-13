"""Boot relaunch policy: automatic writes default to ``auto`` and never mask an owner's choice."""

import json

import pytest
from click.testing import CliRunner
from test_session_begin_recovery import launch_metadata as launch_metadata
from test_session_enrol import add_record
from test_session_enrol import enrolled_world as enrolled_world
from test_session_enrol import invoke as invoke_named_enrol
from test_session_enrol_all import invoke as invoke_enrol
from test_session_enrol_all import live_sessions as live_sessions
from test_session_state_publish import publishing as publishing

from ai_cli import config
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group

BOOT = {"on_boot": True, "causes": list(state.DEFAULTS["causes"])}


def _cli(*args):
    return CliRunner().invoke(_cli_group, list(args))


def _record(name):
    return next(r for r in registry.load_registry()["sessions"] if r.get("name") == name)


def _recipe(cwd="/work/myproject"):
    return {"argv": ["ai", "c", "1"], "cwd": cwd}


# D-1: enrolment and recovery give a canonical session auto, recorded as a default.


def test_given_unregistered_live_session_when_named_enrol_runs_then_policy_is_default_auto(enrolled_world):
    result = invoke_named_enrol(enrolled_world)
    assert result.exit_code == 0, result.output
    record = _record(enrolled_world.name)
    assert (record["relaunch"], record["relaunch_set_by"]) == ("auto", "default")


def test_given_recipe_less_record_without_policy_when_named_enrol_runs_then_policy_is_default_auto(enrolled_world):
    before = add_record(enrolled_world)
    registry._mutate(lambda doc: doc["sessions"][0].pop("relaunch") and True)
    result = invoke_named_enrol(enrolled_world)
    assert result.exit_code == 0, result.output
    after = _record(enrolled_world.name)
    assert (after["relaunch"], after["relaunch_set_by"]) == ("auto", "default")
    assert after["id"] == before["id"]


def test_given_legacy_bulk_enrolment_when_registered_then_policy_is_default_auto(live_sessions):
    live_sessions.add(legacy=True)
    result = invoke_enrol("--all", "--json")
    assert result.exit_code == 0, result.output
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == ("auto", "default")


def test_given_unregistered_launch_when_generation_recovers_then_policy_is_default_auto(launch_metadata, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: {"machine": {"host_id": "host-a"}})
    result = _cli("sessions", "_begin", "c-session-1", "--launch-metadata", json.dumps(launch_metadata))
    assert result.exit_code == 0, result.output
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == ("auto", "default")


def test_given_recovered_session_when_host_reboots_then_boot_replay_allows_it(launch_metadata, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: {"machine": {"host_id": "host-a"}})
    assert _cli("sessions", "_begin", "c-session-1", "--launch-metadata", json.dumps(launch_metadata)).exit_code == 0
    record = _record("c-session-1") | {"boot_id": "boot-before"}
    assert state.boot_veto(record, cfg=BOOT, host="host-a", boot_id="boot-after", alive=False) is None


def test_given_new_launch_when_registered_then_policy_is_default_auto():
    state.register("c-session-1", recipe=_recipe(), host="host-a", new_start=True)
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == ("auto", "default")


def test_given_probe_launch_when_registered_then_policy_stays_default_never():
    state.register("c-probe-1", recipe=_recipe(), host="host-a", new_start=True, probe=True)
    record = _record("c-probe-1")
    assert (record["relaunch"], record["relaunch_set_by"], record["probe"]) == ("never", "default", True)


def test_given_probe_record_when_ordinary_launch_takes_its_title_then_policy_is_default_auto():
    state.register("c-session-1", recipe=_recipe(), host="host-a", new_start=True, probe=True)
    state.register("c-session-1", recipe=_recipe(), host="host-a", new_start=True)
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == ("auto", "default")
    assert "probe" not in record


# D-2: an owner's value is recorded as such and never overwritten automatically.


@pytest.mark.parametrize("policy", ["never", "always"])
def test_given_owner_policy_when_session_relaunches_and_is_tracked_then_owner_policy_survives(publishing, policy):
    assert _cli("sessions", "policy", "c-session-1", policy).exit_code == 0
    state.register("c-session-1", recipe=_recipe(), host="host-a", new_start=True)
    registry.record_launch(
        kind="local",
        name="c-session-1",
        relaunch_argv=["ai", "c", "1"],
        cwd="/work/myproject",
        remote=None,
        tty="/dev/ttys001",
        iterm_session_id=None,
        launcher_pid=123,
        config={
            "enabled": True,
            "tracking": {"enabled": True, "include_local": True, "include_remote": True, "include": [], "exclude": []},
        },
    )
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == (policy, "owner")
    assert record["relaunch_changed_at"]


def test_given_owner_never_when_recipe_less_record_is_enrolled_then_owner_never_survives(enrolled_world):
    add_record(enrolled_world, relaunch="never", relaunch_set_by="owner")
    result = invoke_named_enrol(enrolled_world)
    assert result.exit_code == 0, result.output
    record = _record(enrolled_world.name)
    assert (record["relaunch"], record["relaunch_set_by"]) == ("never", "owner")


def test_given_owner_never_when_generation_recovers_a_recipe_then_owner_never_survives(launch_metadata, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: {"machine": {"host_id": "host-a"}})
    state.register("c-session-1", recipe=_recipe(launch_metadata["worktree_dir"]), host="host-a")

    def owner_never_without_recipe(doc):
        doc["sessions"][0].update(relaunch="never", relaunch_set_by="owner")
        doc["sessions"][0].pop("boot_recipe")
        return True

    registry._mutate(owner_never_without_recipe)
    result = _cli("sessions", "_begin", "c-session-1", "--launch-metadata", json.dumps(launch_metadata))
    assert result.exit_code == 0, result.output
    record = _record("c-session-1")
    assert (record["relaunch"], record["relaunch_set_by"]) == ("never", "owner")


# D-3: a policy change is not a run-state change.


def test_given_running_session_when_policy_changes_then_changed_at_is_untouched_and_policy_time_recorded(
    publishing, monkeypatch
):
    before = _record("c-session-1")["changed_at"]
    listed_before = json.loads(_cli("sessions", "list", "-j").output)[0]["changed_at"]
    monkeypatch.setattr(registry, "_now", lambda: "2099-01-02T03:04:05Z")
    assert _cli("sessions", "policy", "c-session-1", "never").exit_code == 0
    record = _record("c-session-1")
    assert record["changed_at"] == before
    assert record["relaunch_changed_at"] == "2099-01-02T03:04:05Z"
    listed = json.loads(_cli("sessions", "list", "-j").output)[0]
    assert listed["changed_at"] == listed_before
    assert (listed["relaunch"], listed["relaunch_set_by"]) == ("never", "owner")


# D-4: a one-shot repair promotes unattributed never to a default auto.


def _seed(**records):
    """Seed local records on host-a; each value is the fields that differ from a canonical session."""
    for name, fields in records.items():
        state.register(name, recipe=_recipe(), host="host-a")

        def change(doc, name=name, fields=fields):
            record = next(r for r in doc["sessions"] if r["name"] == name)
            record.pop("relaunch_set_by", None)
            record.update(fields)
            return True

        registry._mutate(change)


@pytest.fixture
def host_a(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: {"machine": {"host_id": "host-a"}})


def test_given_unattributed_never_when_repair_runs_then_it_becomes_default_auto_and_others_are_untouched(host_a):
    _seed(
        **{
            "c-legacy-1": {"relaunch": "never"},
            "c-owner-1": {"relaunch": "never", "relaunch_set_by": "owner"},
            "c-probe-1": {"relaunch": "never", "probe": True},
            "c-always-1": {"relaunch": "always"},
            "c-foreign-1": {"relaunch": "never", "owning_host": "host-b"},
        }
    )
    untouched = {r["name"]: r for r in registry.load_registry()["sessions"] if r["name"] != "c-legacy-1"}
    result = _cli("sessions", "repair-policy")
    assert result.exit_code == 0, result.output
    assert "c-legacy-1" in result.output
    legacy = _record("c-legacy-1")
    assert (legacy["relaunch"], legacy["relaunch_set_by"]) == ("auto", "default")
    assert {r["name"]: r for r in registry.load_registry()["sessions"] if r["name"] != "c-legacy-1"} == untouched


def test_given_unattributed_never_when_repair_dry_runs_then_it_reports_and_writes_nothing(host_a):
    _seed(**{"c-legacy-1": {"relaunch": "never"}})
    before = registry.registry_path().read_bytes()
    result = _cli("sessions", "repair-policy", "-d")
    assert result.exit_code == 0, result.output
    assert "c-legacy-1" in result.output and "would" in result.output
    assert registry.registry_path().read_bytes() == before


def test_given_repaired_registry_when_repair_runs_again_then_registry_bytes_are_unchanged(host_a):
    _seed(**{"c-legacy-1": {"relaunch": "never"}})
    assert _cli("sessions", "repair-policy").exit_code == 0
    before = registry.registry_path().read_bytes()
    result = _cli("sessions", "repair-policy")
    assert result.exit_code == 0, result.output
    assert "nothing to repair" in result.output
    assert registry.registry_path().read_bytes() == before


# D-5: a repaired policy does not bypass the exit-cause veto.


@pytest.mark.parametrize("cause", ["manual_exit", "unknown"])
def test_given_repaired_session_that_exited_by_hand_when_boot_replay_decides_then_cause_still_vetoes(host_a, cause):
    _seed(**{"c-legacy-1": {"relaunch": "never"}})
    assert _cli("sessions", "repair-policy").exit_code == 0
    record = _record("c-legacy-1") | {"run_state": "exited", "supervisor_exit": {"cause": cause}}
    veto = state.boot_veto(record, cfg=BOOT, host="host-a", boot_id="boot-after", alive=False)
    assert veto == f"cause {cause} forbids boot relaunch"
