"""Supervisor lifecycle persistence and compatibility."""

import json

import pytest

from ai_cli import session_registry as registry
from ai_cli import session_run_state as state


@pytest.fixture
def registered():
    state.register("c-session-1", recipe={"argv": ["ai", "c", "1"], "cwd": "/work/myproject"}, host="host-a")
    return state.begin("c-session-1")


def record():
    return registry.load_registry()["sessions"][0]


def test_hook_exit_when_restarted_then_end_markers_clear(registered):
    registry.record_exit("c-session-1", cause="manual_exit", evidence={})
    assert state.mark_running("c-session-1", generation=registered, sequence=1)
    assert record()["exit"] is None and record()["ended_at"] is None
    registry.record_exit("c-session-1", cause="manual_exit", evidence={})
    assert state.run_state(record()) == "running"
    assert state.cause(record(), "next-boot") == "host_reboot"


def test_final_exit_when_delayed_start_arrives_then_exit_retained(registered):
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    assert not state.mark_running("c-session-1", generation=registered, sequence=1)
    assert not state.mark_running("c-session-1", generation="old", sequence=3)
    assert state.cause(record(), "next-boot") == "manual_exit"


def test_legacy_records_when_read_then_state_uses_end_marker():
    assert state.run_state({}) == "running"
    assert state.run_state({"ended_at": "time"}) == "exited"


def test_missing_record_when_marked_then_refused_without_creation():
    assert not state.mark_running("c-session-1")
    assert not registry.registry_path().exists()


def test_registry_failure_when_exited_then_safety_file_is_durable(registered, monkeypatch):
    write = registry._write

    def fail_registry(path, doc):
        if path == registry.registry_path():
            raise OSError("disk unavailable")
        write(path, doc)

    monkeypatch.setattr(registry, "_write", fail_registry)
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    path = state.tombstone_path("c-session-1", "host-a")
    assert json.loads(path.read_text())["deny_boot"]
    monkeypatch.setattr(registry, "_write", write)
    assert not state.mark_running("c-session-1", generation=registered, sequence=3)
    generation = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=generation, sequence=3)
    assert not path.exists()


def test_both_stores_failed_when_exiting_then_exit_is_refused(registered, monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(registry, "_write", fail)
    assert not state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)


def test_unsafe_causes_when_loading_then_configuration_refused(monkeypatch):
    monkeypatch.setattr(state.config, "load_config", lambda: {"session_relaunch": {"causes": ["manual_exit"]}})
    with pytest.raises(ValueError, match="permits only"):
        state.settings()


def test_replaced_record_when_reattached_then_policy_and_generation_survive(registered):
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

    registry._mutate(lambda doc: doc["sessions"][0].update(relaunch="never", relaunched_boot_id="boot-1") or True)
    previous = record()
    registry.record_launch(
        kind="local",
        name="c-session-1",
        relaunch_argv=["ai", "c", "-r", "1"],
        cwd="/work",
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=1,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert record()["id"] != previous["id"]
    assert record()["generation"] == registered
    assert record()["relaunch"] == "never"
    assert record()["relaunched_boot_id"] == "boot-1"
    assert record()["boot_recipe"] == previous["boot_recipe"]
