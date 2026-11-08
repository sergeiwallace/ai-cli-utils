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

WAIT_SECONDS_VARIABLE = "AI_CLI_TEST_PROCESS_WAIT_SECONDS"
DEFAULT_WAIT_SECONDS = 60.0
MAX_SCALE = 4.0
# A bare `python -I -S -c ''` start on an idle developer machine.
IDLE_SPAWN_SECONDS = 0.025
_PROBE_RUNS = 3


def scale_for(spawn_seconds: float) -> float:
    """The deadline multiplier for a host where a bare interpreter start takes ``spawn_seconds``."""
    return min(max(spawn_seconds / IDLE_SPAWN_SECONDS, 1.0), MAX_SCALE)


def load_scale() -> float:
    """The deadline multiplier for this host, measured now."""
    samples = []
    for _ in range(_PROBE_RUNS):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-S", "-c", ""], check=True, capture_output=True)
        samples.append(time.perf_counter() - started)
    return scale_for(statistics.median(samples))


def scaled(seconds: float) -> float:
    """``seconds`` stretched by how busy the host is: a short budget for one process step."""
    return seconds * load_scale()


def process_wait_seconds(default: float = DEFAULT_WAIT_SECONDS) -> float:
    """The observe window for a real process: the explicit setting, else ``default`` scaled."""
    explicit = os.environ.get(WAIT_SECONDS_VARIABLE)
    if explicit:
        return float(explicit)
    return scaled(default)
