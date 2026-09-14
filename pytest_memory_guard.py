"""Keep one runaway pytest process from exhausting its host's memory."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

_DEFAULT_MEMORY_LIMIT_MB = 2048
_MINIMUM_MEMORY_LIMIT_MB = 64
_MEMORY_LIMIT_ENV = "PYTEST_WORKER_MEMORY_LIMIT_MB"
_WATCHDOG_ATTRIBUTE = "_pytest_memory_guard_watchdog"


def _memory_limit_bytes() -> int:
    raw_limit = os.environ.get(_MEMORY_LIMIT_ENV, str(_DEFAULT_MEMORY_LIMIT_MB))
    try:
        limit_mb = int(raw_limit)
    except ValueError as exc:
        raise pytest.UsageError(f"{_MEMORY_LIMIT_ENV} must be an integer number of MiB") from exc
    if limit_mb < _MINIMUM_MEMORY_LIMIT_MB:
        raise pytest.UsageError(f"{_MEMORY_LIMIT_ENV} must be at least {_MINIMUM_MEMORY_LIMIT_MB} MiB")
    return limit_mb * 1024 * 1024


def _is_test_process(config: Any) -> bool:
    """Exclude only an xdist controller; guard serial pytest and every worker."""
    if hasattr(config, "workerinput"):
        return True
    return not getattr(config.option, "numprocesses", None)


def _apply_linux_address_space_limit(limit_bytes: int) -> None:
    # RLIMIT_AS is a kernel-enforced ceiling on Linux. Import resource only on
    # the platform where this path is used because the module is absent on Windows.
    import resource

    _, hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    effective_limit = limit_bytes
    if hard_limit != resource.RLIM_INFINITY:
        effective_limit = min(effective_limit, hard_limit)
    resource.setrlimit(resource.RLIMIT_AS, (effective_limit, effective_limit))


def _start_rss_watchdog(limit_bytes: int) -> subprocess.Popen[bytes]:
    """Start the portable RSS fallback used where RLIMIT_AS is unavailable."""
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--watch-parent",
            str(os.getpid()),
            str(limit_bytes),
        ],
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )


def pytest_configure(config: Any) -> None:
    if not _is_test_process(config):
        return

    limit_bytes = _memory_limit_bytes()
    if _MEMORY_LIMIT_ENV in os.environ:
        limit_mb = limit_bytes // 1024 // 1024
        os.write(2, f"pytest memory guard: limiting this process to {limit_mb} MiB\n".encode())

    if sys.platform.startswith("linux"):
        _apply_linux_address_space_limit(limit_bytes)
        return

    # macOS processes map a very large shared address space, making RLIMIT_AS
    # unusable as an RSS ceiling. Windows has no resource module. A separate
    # psutil process remains schedulable if a runaway test monopolizes the GIL.
    setattr(config, _WATCHDOG_ATTRIBUTE, _start_rss_watchdog(limit_bytes))


def pytest_unconfigure(config: Any) -> None:
    watchdog = getattr(config, _WATCHDOG_ATTRIBUTE, None)
    if watchdog is None or watchdog.poll() is not None:
        return
    watchdog.terminate()
    try:
        watchdog.wait(timeout=2)
    except subprocess.TimeoutExpired:
        watchdog.kill()
        watchdog.wait(timeout=2)


def _watch_parent(parent_pid: int, limit_bytes: int) -> int:
    parent = psutil.Process(parent_pid)
    while True:
        try:
            rss_bytes = parent.memory_info().rss
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            return 0
        if rss_bytes >= limit_bytes:
            limit_mb = limit_bytes // 1024 // 1024
            rss_mb = rss_bytes // 1024 // 1024
            os.write(
                2,
                (
                    f"pytest memory guard: process exceeded its {limit_mb} MiB RSS ceiling "
                    f"({rss_mb} MiB); terminating it\n"
                ).encode(),
            )
            parent.kill()
            return 0
        time.sleep(0.05)


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--watch-parent":
        return 2
    return _watch_parent(int(argv[1]), int(argv[2]))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
