"""The real-process deadline a test gets scales with host load, within bounds (AI-CLI-4d7j)."""

import pytest
from process_wait import (
    DEFAULT_WAIT_SECONDS,
    IDLE_SPAWN_SECONDS,
    MAX_SCALE,
    WAIT_SECONDS_VARIABLE,
    load_scale,
    process_wait_seconds,
    scale_for,
    scaled,
)


def test_given_an_explicit_wait_setting_when_a_test_asks_for_its_window_then_the_setting_wins(monkeypatch):
    monkeypatch.setenv(WAIT_SECONDS_VARIABLE, "7.5")

    assert process_wait_seconds() == 7.5


def test_given_no_wait_setting_when_a_test_asks_for_its_window_then_it_is_the_default_scaled_by_load(monkeypatch):
    monkeypatch.delenv(WAIT_SECONDS_VARIABLE, raising=False)

    window = process_wait_seconds()

    assert window == DEFAULT_WAIT_SECONDS * load_scale()
    assert DEFAULT_WAIT_SECONDS <= window <= DEFAULT_WAIT_SECONDS * MAX_SCALE


def test_given_an_idle_host_when_scaling_then_the_deadline_is_unchanged():
    assert scale_for(IDLE_SPAWN_SECONDS / 2) == 1.0


def test_given_execs_three_times_slower_than_idle_when_scaling_then_the_deadline_triples():
    assert scale_for(IDLE_SPAWN_SECONDS * 3) == pytest.approx(3.0)


def test_given_a_saturated_host_when_scaling_then_the_deadline_stops_at_the_cap():
    """A host twenty times slower still fails a hung process within MAX_SCALE times the budget."""
    assert scale_for(IDLE_SPAWN_SECONDS * 20) == MAX_SCALE


def test_given_a_short_budget_when_scaled_then_it_stretches_by_the_measured_load():
    assert scaled(2.0) == 2.0 * load_scale()
