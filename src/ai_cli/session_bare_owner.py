"""Observe a directly exec'd bare agent without changing its terminal process."""

import sys
import time

import psutil

from . import session_registry as registry
from . import session_run_state as state


def settle(name: str, generation: str, cause: str = "manual_exit") -> None:
    """Retain final-exit retry ownership until persisted or a new launch takes over."""
    while True:
        record = next((r for r in registry.load_registry()["sessions"] if r.get("name") == name), {})
        if record.get("generation") != generation:
            return
        if state.mark_exited(name, cause, {"source": "bare-owner"}, generation=generation):
            return
        time.sleep(1)


def observe(pid: int, started: float, name: str, generation: str) -> None:
    # The launcher's pipe closes on successful exec. A returning handoff cancels
    # observation, including test handoffs that never replace the process.
    if sys.stdin.buffer.read():
        return
    try:
        process = psutil.Process(pid)
        while process.create_time() == started and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
            time.sleep(0.1)
    except psutil.NoSuchProcess:
        pass
    settle(name, generation)


if __name__ == "__main__":
    observe(int(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4])
