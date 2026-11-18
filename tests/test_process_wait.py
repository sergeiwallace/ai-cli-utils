"""The real-process deadline a test gets scales with host load, within bounds (AI-CLI-4d7j)."""

from pathlib import Path
from types import SimpleNamespace

import process_wait
import pytest
from process_wait import (
    DEFAULT_WAIT_SECONDS,
    IDLE_SPAWN_SECONDS,
    MAX_SCALE,
    WAIT_SECONDS_VARIABLE,
    process_wait_seconds,
    scale_for,
    scaled,
)


class _ProbeClock:
    """Stands in for the probe's clock: every interpreter start takes ``spawn`` seconds."""

    def __init__(self, spawn: float) -> None:
        self.spawn = spawn
        self.now = 0.0

    def perf_counter(self) -> float:
        self.now += self.spawn
        return self.now


@pytest.fixture
def probe_clock(monkeypatch) -> _ProbeClock:
    clock = _ProbeClock(IDLE_SPAWN_SECONDS)
    monkeypatch.setattr(process_wait, "subprocess", SimpleNamespace(run=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(process_wait, "time", SimpleNamespace(perf_counter=clock.perf_counter))
    monkeypatch.setattr(process_wait.os, "getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)
    monkeypatch.setattr(process_wait, "MEMORY_PRESSURE_PATH", Path("/nonexistent/pressure/memory"))
    return clock


def _host_load(monkeypatch, tmp_path, *, per_core: float, memory_avg10: float | None) -> None:
    cores = 4
    monkeypatch.setattr(process_wait.os, "process_cpu_count", lambda: cores, raising=False)
    monkeypatch.setattr(process_wait.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(process_wait.os, "getloadavg", lambda: (per_core * cores, 0.0, 0.0), raising=False)
    pressure = tmp_path / "memory"
    if memory_avg10 is not None:
        pressure.write_text(
            f"some avg10={memory_avg10:.2f} avg60=0.00 avg300=0.00 total=1\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=1\n",
            encoding="ascii",
        )
    monkeypatch.setattr(process_wait, "MEMORY_PRESSURE_PATH", pressure)


def test_given_an_explicit_wait_setting_when_a_test_asks_for_its_window_then_the_setting_wins(monkeypatch):
    monkeypatch.setenv(WAIT_SECONDS_VARIABLE, "7.5")

    assert process_wait_seconds() == 7.5


def test_given_no_wait_setting_when_a_test_asks_for_its_window_then_it_is_the_default_scaled_by_load(
    monkeypatch, probe_clock
):
    monkeypatch.delenv(WAIT_SECONDS_VARIABLE, raising=False)
    probe_clock.spawn = IDLE_SPAWN_SECONDS * 2

    assert process_wait_seconds() == pytest.approx(DEFAULT_WAIT_SECONDS * 2)


def test_given_an_idle_host_when_scaling_then_the_deadline_is_unchanged():
    assert scale_for(IDLE_SPAWN_SECONDS / 2) == 1.0


def test_given_execs_three_times_slower_than_idle_when_scaling_then_the_deadline_triples():
    assert scale_for(IDLE_SPAWN_SECONDS * 3) == pytest.approx(3.0)


def test_given_a_saturated_host_when_scaling_then_the_deadline_stops_at_the_cap():
    """A host twenty times slower still fails a hung process within MAX_SCALE times the budget."""
    assert scale_for(IDLE_SPAWN_SECONDS * 20) == MAX_SCALE


def test_given_a_short_budget_when_scaled_then_it_stretches_by_the_measured_load(probe_clock):
    probe_clock.spawn = IDLE_SPAWN_SECONDS * 3

    assert scaled(2.0) == pytest.approx(6.0)


def test_given_the_host_slows_down_mid_run_when_scaled_again_then_the_new_load_is_used(probe_clock):
    """A worker lives for the whole run, so a scale measured once at its start goes stale.

    Each probe start takes idle time at first, then three times idle, as when other
    work lands on the host mid-run.
    """
    before = scaled(2.0)
    probe_clock.spawn = IDLE_SPAWN_SECONDS * 3
    after = scaled(2.0)

    assert before == pytest.approx(2.0)
    assert after == pytest.approx(6.0)


def test_given_a_real_host_when_scaled_then_the_deadline_stays_within_the_cap():
    assert 2.0 <= scaled(2.0) <= 2.0 * MAX_SCALE


def test_given_an_idle_probe_and_three_tasks_per_core_when_scaled_then_the_deadline_triples(
    monkeypatch, tmp_path, probe_clock
):
    """A short probe start is served at once on a contended host, so the load average decides."""
    _host_load(monkeypatch, tmp_path, per_core=3.0, memory_avg10=None)

    assert scaled(2.0) == pytest.approx(6.0)


def test_given_a_host_stalled_on_memory_half_the_time_when_scaled_then_the_deadline_doubles(
    monkeypatch, tmp_path, probe_clock
):
    _host_load(monkeypatch, tmp_path, per_core=0.5, memory_avg10=50.0)

    assert scaled(2.0) == pytest.approx(4.0)


def test_given_contention_and_memory_stalls_together_when_scaled_then_the_deadline_stops_at_the_cap(
    monkeypatch, tmp_path, probe_clock
):
    _host_load(monkeypatch, tmp_path, per_core=3.0, memory_avg10=60.0)

    assert scaled(2.0) == pytest.approx(2.0 * MAX_SCALE)


def test_given_a_platform_without_a_load_average_or_pressure_file_when_scaled_then_the_probe_alone_decides(
    monkeypatch, probe_clock
):
    monkeypatch.delattr(process_wait.os, "getloadavg", raising=False)
    probe_clock.spawn = IDLE_SPAWN_SECONDS * 2

    assert scaled(2.0) == pytest.approx(4.0)


def test_given_an_unreadable_pressure_line_when_scaled_then_memory_counts_as_idle(monkeypatch, tmp_path, probe_clock):
    pressure = tmp_path / "memory"
    pressure.write_text("some garbled\n", encoding="ascii")
    monkeypatch.setattr(process_wait, "MEMORY_PRESSURE_PATH", pressure)

    assert scaled(2.0) == pytest.approx(2.0)
