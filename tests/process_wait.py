"""How long a test may wait on a real process, scaled to how busy the host is.

A test that starts a real process and waits for it to reach some state needs a
deadline. On a host shared with other work, every exec can cost ten times what it
costs on an idle one, so a fixed deadline that is generous on an idle host fails
under load, and one raised to cover load makes every genuine failure slow
(AI-CLI-4d7j).

So the deadline scales with what an exec costs on this host right now. Each time a
test asks for a deadline, :func:`load_scale` times a few starts of a bare
interpreter and divides the median by what one costs on an idle host, clamped to
``[1, MAX_SCALE]``. Pinning the wait under xdist would not do it: the measured
failures came from other sessions loading the host, which xdist cannot see. Nor
would measuring once per test process: a worker lives for the whole run, and a
scale taken in a quiet moment at its start left every later deadline unscaled
when the load arrived. Three starts of an interpreter with no site packages cost
well under a tenth of a second on an idle host, so the measurement stays cheap.

The probe alone misses sustained contention. The scheduler serves a fresh
short-lived task almost at once however many tasks share the cores, so on a host
with 44 runnable tasks on 16 cores and CPU pressure near 50% the probe still read
an idle 1.0, while a child pytest that needs seconds of CPU got about a third of a
core. So the scale is also at least the one-minute load average per core, which
counts those tasks, and on Linux it is stretched further by the share of the last
ten seconds some task spent stalled on memory (``/proc/pressure/memory``), which
is what swapping costs. Where either reading is unavailable it counts as idle.

``AI_CLI_TEST_PROCESS_WAIT_SECONDS`` still sets the long observe windows
explicitly and wins over the measurement.

The probe starts ``sys.executable`` rather than tmux or a shell, so it measures
the same thing on every platform.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

WAIT_SECONDS_VARIABLE = "AI_CLI_TEST_PROCESS_WAIT_SECONDS"
DEFAULT_WAIT_SECONDS = 60.0
MAX_SCALE = 4.0
# A bare `python -I -S -c ''` start on an idle developer machine.
IDLE_SPAWN_SECONDS = 0.025
_PROBE_RUNS = 3
MEMORY_PRESSURE_PATH = Path("/proc/pressure/memory")
# A stall share past this already stretches the deadline to the cap.
_MAX_STALL = 1 - 1 / MAX_SCALE


def scale_for(spawn_seconds: float) -> float:
    """The deadline multiplier for a host where a bare interpreter start takes ``spawn_seconds``."""
    return min(max(spawn_seconds / IDLE_SPAWN_SECONDS, 1.0), MAX_SCALE)


def load_per_core() -> float:
    """Tasks running or waiting to run per core over the last minute; 0 where unknown."""
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):  # Windows has no load average.
        return 0.0
    cores = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return load / cores


def memory_stall_fraction() -> float:
    """Share of the last ten seconds some task stalled on memory; 0 where unknown.

    Linux pressure stall information only; other platforms have no equivalent file.
    """
    try:
        first = MEMORY_PRESSURE_PATH.read_text(encoding="ascii").splitlines()[0]
        fields = dict(field.split("=", 1) for field in first.split()[1:])
        share = float(fields["avg10"]) / 100
    except (OSError, IndexError, KeyError, ValueError):
        return 0.0
    return min(max(share, 0.0), _MAX_STALL)


def load_scale() -> float:
    """The deadline multiplier for this host, measured now."""
    samples = []
    for _ in range(_PROBE_RUNS):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-S", "-c", ""], check=True, capture_output=True)
        samples.append(time.perf_counter() - started)
    contention = max(scale_for(statistics.median(samples)), load_per_core())
    return min(contention / (1 - memory_stall_fraction()), MAX_SCALE)


def scaled(seconds: float) -> float:
    """``seconds`` stretched by how busy the host is: a short budget for one process step."""
    return seconds * load_scale()


def process_wait_seconds(default: float = DEFAULT_WAIT_SECONDS) -> float:
    """The observe window for a real process: the explicit setting, else ``default`` scaled."""
    explicit = os.environ.get(WAIT_SECONDS_VARIABLE)
    if explicit:
        return float(explicit)
    return scaled(default)
