"""Keep one runaway pytest process from exhausting its host's memory."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

_DEFAULT_MEMORY_LIMIT_MB = 2048
_MINIMUM_MEMORY_LIMIT_MB = 64
_MEMORY_LIMIT_ENV = "PYTEST_WORKER_MEMORY_LIMIT_MB"
_NOTICE_FILE_ENV = "PYTEST_MEMORY_GUARD_NOTICE_FILE"
_UNCONSTRAINED_MEMORY_MARKER = "unconstrained_memory"
_CONTROLLER_LIMIT_ATTRIBUTE = "_pytest_memory_guard_controller_limit"
_NOTICE_DIRECTORY_ATTRIBUTE = "_pytest_memory_guard_notice_directory"
_PREVIOUS_NOTICE_FILE_ATTRIBUTE = "_pytest_memory_guard_previous_notice_file"
_LINUX_ADDRESS_SPACE_LIMIT_ATTRIBUTE = "_pytest_memory_guard_linux_address_space_limit"
_WATCHDOG_ATTRIBUTE = "_pytest_memory_guard_watchdog"
_WATCHDOG_LIMIT_ATTRIBUTE = "_pytest_memory_guard_watchdog_limit"


def _memory_limit_bytes() -> int:
    raw_limit = os.environ.get(_MEMORY_LIMIT_ENV, str(_DEFAULT_MEMORY_LIMIT_MB))
    try:
        limit_mb = int(raw_limit)
    except ValueError as exc:
        raise pytest.UsageError(
            f"{_MEMORY_LIMIT_ENV} must be an integer number of MiB",
        ) from exc
    if limit_mb < _MINIMUM_MEMORY_LIMIT_MB:
        raise pytest.UsageError(
            f"{_MEMORY_LIMIT_ENV} must be at least {_MINIMUM_MEMORY_LIMIT_MB} MiB",
        )
    return limit_mb * 1024 * 1024


def _is_test_process(config: Any) -> bool:
    """Exclude only an xdist controller; guard serial pytest and every worker."""
    if hasattr(config, "workerinput"):
        return True
    return not getattr(config.option, "numprocesses", None)


def _configure_controller(config: Any, limit_bytes: int) -> None:
    notice_directory = Path(tempfile.mkdtemp(prefix="pytest-memory-guard-"))
    notice_file = notice_directory / "notices"
    notice_file.touch(mode=0o600)
    previous_notice_file = (
        _NOTICE_FILE_ENV in os.environ,
        os.environ.get(_NOTICE_FILE_ENV),
    )
    os.environ[_NOTICE_FILE_ENV] = str(notice_file)
    setattr(config, _CONTROLLER_LIMIT_ATTRIBUTE, limit_bytes)
    setattr(config, _NOTICE_DIRECTORY_ATTRIBUTE, notice_directory)
    setattr(config, _PREVIOUS_NOTICE_FILE_ATTRIBUTE, previous_notice_file)


def _cleanup_controller_notice_file(config: Any) -> None:
    notice_directory = getattr(config, _NOTICE_DIRECTORY_ATTRIBUTE, None)
    if notice_directory is None:
        return

    notice_file = notice_directory / "notices"
    if notice_file.exists():
        notice_file.unlink()
    notice_directory.rmdir()
    setattr(config, _NOTICE_DIRECTORY_ATTRIBUTE, None)

    had_previous_value, previous_value = getattr(
        config,
        _PREVIOUS_NOTICE_FILE_ATTRIBUTE,
    )
    if had_previous_value:
        os.environ[_NOTICE_FILE_ENV] = previous_value
    else:
        os.environ.pop(_NOTICE_FILE_ENV, None)


def _apply_linux_address_space_limit(limit_bytes: int) -> int:
    # RLIMIT_AS is a kernel-enforced ceiling on Linux. Import resource only on
    # the platform where this path is used because the module is absent on Windows.
    import resource

    _, hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    effective_limit = limit_bytes
    if hard_limit != resource.RLIM_INFINITY:
        effective_limit = min(effective_limit, hard_limit)
    # Preserve the inherited hard limit so a marked test can temporarily remove
    # this guard's soft ceiling and restore it afterwards.
    resource.setrlimit(resource.RLIMIT_AS, (effective_limit, hard_limit))
    return effective_limit


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
    config.addinivalue_line(
        "markers",
        f"{_UNCONSTRAINED_MEMORY_MARKER}: run this test without the memory guard",
    )
    limit_bytes = _memory_limit_bytes()
    if not hasattr(config, "workerinput"):
        _configure_controller(config, limit_bytes)
    if not _is_test_process(config):
        return

    if _MEMORY_LIMIT_ENV in os.environ:
        limit_mb = limit_bytes // 1024 // 1024
        os.write(
            2,
            f"pytest memory guard: limiting this process to {limit_mb} MiB\n".encode(),
        )

    if sys.platform.startswith("linux"):
        setattr(
            config,
            _LINUX_ADDRESS_SPACE_LIMIT_ATTRIBUTE,
            _apply_linux_address_space_limit(limit_bytes),
        )
        return

    # macOS processes map a very large shared address space, making RLIMIT_AS
    # unusable as an RSS ceiling. Windows has no resource module. A separate
    # psutil process remains schedulable if a runaway test monopolizes the GIL.
    setattr(config, _WATCHDOG_LIMIT_ATTRIBUTE, limit_bytes)
    setattr(config, _WATCHDOG_ATTRIBUTE, _start_rss_watchdog(limit_bytes))


def pytest_sessionstart(session: pytest.Session) -> None:
    limit_bytes = getattr(session.config, _CONTROLLER_LIMIT_ATTRIBUTE, None)
    if limit_bytes is None:
        return
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is not None:
        limit_mb = limit_bytes // 1024 // 1024
        terminal_reporter.write_line(
            f"pytest memory guard: limiting each test process to {limit_mb} MiB",
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    """Lift this process's memory limit only while a marked test executes."""
    if item.get_closest_marker(_UNCONSTRAINED_MEMORY_MARKER) is None:
        yield
        return

    watchdog_limit = getattr(item.config, _WATCHDOG_LIMIT_ATTRIBUTE, None)
    if watchdog_limit is not None:
        _stop_rss_watchdog(item.config)
        try:
            yield
        finally:
            setattr(
                item.config,
                _WATCHDOG_ATTRIBUTE,
                _start_rss_watchdog(watchdog_limit),
            )
        return

    limit_bytes = getattr(item.config, _LINUX_ADDRESS_SPACE_LIMIT_ATTRIBUTE, None)
    if limit_bytes is None:
        yield
        return

    import resource

    _, hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (hard_limit, hard_limit))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard_limit))


def _stop_rss_watchdog(config: Any) -> None:
    watchdog = getattr(config, _WATCHDOG_ATTRIBUTE, None)
    setattr(config, _WATCHDOG_ATTRIBUTE, None)
    if watchdog is None or watchdog.poll() is not None:
        return
    watchdog.terminate()
    try:
        watchdog.wait(timeout=2)
    except subprocess.TimeoutExpired:
        watchdog.kill()
        watchdog.wait(timeout=2)


def pytest_unconfigure(config: Any) -> None:
    _stop_rss_watchdog(config)
    _cleanup_controller_notice_file(config)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    notice_directory = getattr(
        terminalreporter.config,
        _NOTICE_DIRECTORY_ATTRIBUTE,
        None,
    )
    if notice_directory is None:
        return

    notice_file = notice_directory / "notices"
    for notice in notice_file.read_text(encoding="utf-8").splitlines():
        terminalreporter.write_line(notice)
    _cleanup_controller_notice_file(terminalreporter.config)


def _write_termination_notice(notice: bytes) -> None:
    os.write(2, notice)
    notice_file = os.environ.get(_NOTICE_FILE_ENV)
    if notice_file is None:
        return

    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(notice_file, flags, 0o600)
        try:
            os.write(descriptor, notice)
        finally:
            os.close(descriptor)
    except OSError:
        # A reporting failure must never prevent the watchdog from enforcing
        # the memory ceiling.
        return


def _watch_parent(parent_pid: int, limit_bytes: int) -> int:
    # Linux uses RLIMIT_AS and does not need psutil. Import it only in the
    # portable watchdog process so Linux repositories can adopt the guard
    # without adding a dependency they never execute.
    import psutil

    parent = psutil.Process(parent_pid)
    while True:
        try:
            rss_bytes = parent.memory_info().rss
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            return 0
        if rss_bytes >= limit_bytes:
            limit_mb = limit_bytes // 1024 // 1024
            rss_mb = rss_bytes // 1024 // 1024
            ceiling = f"process exceeded its {limit_mb} MiB RSS ceiling"
            notice = f"pytest memory guard: {ceiling} ({rss_mb} MiB); terminating it\n"
            _write_termination_notice(notice.encode())
            parent.kill()
            return 0
        time.sleep(0.05)


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--watch-parent":
        return 2
    return _watch_parent(int(argv[1]), int(argv[2]))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
