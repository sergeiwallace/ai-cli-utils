"""Configuration and durable fallback contracts, including native Windows storage."""

import json
import time
from itertools import pairwise
from types import SimpleNamespace

import pytest

from ai_cli import session_lifecycle as lifecycle
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_lifecycle import EXIT_PERSISTENCE_DEFAULTS, persistence_settings, store_safety


@pytest.fixture
def safety_context(tmp_path):
    name = "c-session-1"
    state.register(name, recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin(name)
    return {
        "registry_path": str(registry.registry_path()),
        "safety_path": str(state.tombstone_path(name, "host-a")),
    }, generation


@pytest.mark.parametrize("key", EXIT_PERSISTENCE_DEFAULTS)
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "1"])
def test_given_invalid_persistence_bound_when_settings_load_then_configuration_is_refused(key, value):
    with pytest.raises(ValueError, match="positive finite number"):
        persistence_settings({"session_relaunch": {"exit_persistence": {key: value}}})


def test_given_backoff_above_cap_when_settings_load_then_configuration_is_refused():
    with pytest.raises(ValueError, match="exceeds"):
        persistence_settings({"session_relaunch": {"exit_persistence": {"max_backoff_seconds": 0.01}}})


def test_given_partial_persistence_config_when_settings_load_then_only_selected_bound_changes():
    assert persistence_settings({"session_relaunch": {"exit_persistence": {"deadline_seconds": 7}}}) == (
        EXIT_PERSISTENCE_DEFAULTS | {"deadline_seconds": 7}
    )


def test_given_repeated_failure_when_backoff_increases_then_delays_stop_at_the_configured_cap(
    safety_context, monkeypatch
):
    context, generation = safety_context
    clock = [0.0]
    attempts = []

    def call(*_args):
        attempts.append(clock[0])
        clock[0] += 0.001
        return

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(lifecycle, "_call", call)
    monkeypatch.setattr(
        lifecycle, "time", SimpleNamespace(monotonic=lambda: clock[0], monotonic_ns=time.monotonic_ns, sleep=sleep)
    )
    context.update(
        command=["unused"],
        policy=EXIT_PERSISTENCE_DEFAULTS | {"deadline_seconds": 1, "backoff_seconds": 0.1, "max_backoff_seconds": 0.2},
    )
    assert not lifecycle.transition(context, "c-session-1", generation, "exited", "manual_exit", "{}")
    assert len(attempts) == 6
    assert [later - earlier for earlier, later in pairwise(attempts)] == pytest.approx(
        [0.101, 0.201, 0.201, 0.201, 0.201], abs=0.002
    )


def test_given_cli_fallback_when_registry_recovers_then_exit_reconciles_and_new_generation_can_run(safety_context):
    context, generation = safety_context
    store_safety(context, "c-session-1", generation, "manual_exit", {"reason": "CLI unavailable"})
    assert not state.mark_running("c-session-1", generation=generation)
    state.reconcile_safety()
    record = registry.load_registry()["sessions"][0]
    assert state.cause(record, "next-boot") == "manual_exit"
    assert record["supervisor_exit"]["evidence"] == {"reason": "CLI unavailable"}
    replacement = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=replacement)
    assert not state.tombstone_path("c-session-1", "host-a").exists()


def test_given_replaced_generation_when_old_fallback_runs_then_current_safety_record_is_preserved(safety_context):
    context, generation = safety_context
    replacement = state.begin("c-session-1")
    store_safety(context, "c-session-1", replacement, "manual_exit", {})
    path = state.tombstone_path("c-session-1", "host-a")
    before = path.read_bytes()
    store_safety(context, "c-session-1", generation, "unknown", {})
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", ["{", "[]", '{"sessions": null}'])
def test_given_corrupt_registry_when_cli_fallback_runs_then_safety_record_still_persists(safety_context, contents):
    context, generation = safety_context
    path = registry.registry_path()
    assert path.read_bytes()
    path.write_text(contents)
    store_safety(context, "c-session-1", generation, "manual_exit", {})
    saved = json.loads(state.tombstone_path("c-session-1", "host-a").read_text())
    assert saved["deny_boot"] and saved["generation"] == generation
