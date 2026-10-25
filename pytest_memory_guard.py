"""Keep one runaway pytest process from exhausting its host's memory."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
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
_WATCHDOG_ATTRIBUTE = "_pytest_memory_guard_watchdog"
_WATCHDOG_LIMIT_ATTRIBUTE = "_pytest_memory_guard_watchdog_limit"
_WATCHDOG_READY = b"r"
_WATCHDOG_READY_TIMEOUT_S = 30.0
_RSS_SETTLE_TIMEOUT_S = 10.0
# Reads the watched process's resident bytes (None once it is gone), and kills it.
_Probe = tuple[Callable[[], int | None], Callable[[], None]]


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


def _start_rss_watchdog(limit_bytes: int) -> subprocess.Popen[bytes]:
    """Start a separate process that kills this one past its RSS ceiling."""
    probe = "proc" if sys.platform.startswith("linux") else "psutil"
    watchdog = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--watch-parent",
            str(os.getpid()),
            str(limit_bytes),
            probe,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        close_fds=True,
    )
    _wait_for_first_sample(watchdog)
    return watchdog


def _wait_for_first_sample(watchdog: subprocess.Popen[bytes]) -> None:
    # The ceiling is enforced only once the watchdog has sampled this process.
    # On a loaded host its interpreter can take longer to start than the next
    # test takes to run, so wait for its first sample rather than returning.
    stdout = watchdog.stdout
    if stdout is None:
        return
    received: list[bytes] = []

    # A thread bounds the wait on every platform; select() cannot poll a pipe
    # on Windows.
    def read_marker() -> None:
        received.append(stdout.read(1))

    reader = threading.Thread(target=read_marker, daemon=True)
    reader.start()
    reader.join(_WATCHDOG_READY_TIMEOUT_S)
    if received and received[0] == _WATCHDOG_READY:
        stdout.close()
        return
    if received:
        reason = f"exited before its first sample (exit code {watchdog.poll()})"
    else:
        reason = f"took no sample within {_WATCHDOG_READY_TIMEOUT_S:g} s"
    os.write(
        2,
        f"pytest memory guard: the RSS watchdog {reason}; not guarded until it does\n".encode(),
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

    # RLIMIT_AS is not used anywhere: it caps reserved address space, which
    # children inherit, so Go (gitleaks) and Chromium die reserving gigabytes
    # they never touch. A separate process stays schedulable if a runaway test
    # monopolizes the GIL.
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
def pytest_runtest_protocol(item: pytest.Item):
    """Lift this process's memory limit only while a marked test executes."""
    if item.get_closest_marker(_UNCONSTRAINED_MEMORY_MARKER) is None:
        yield
        return

    watchdog_limit = getattr(item.config, _WATCHDOG_LIMIT_ATTRIBUTE, None)
    if watchdog_limit is None:
        yield
        return

    _stop_rss_watchdog(item.config)
    try:
        yield
    finally:
        _wait_for_rss_below(watchdog_limit)
        setattr(
            item.config,
            _WATCHDOG_ATTRIBUTE,
            _start_rss_watchdog(watchdog_limit),
        )


def _own_resident_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        try:
            with Path("/proc/self/statm").open("rb") as statm:
                return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            return None
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().memory_info().rss


def _wait_for_rss_below(limit_bytes: int) -> None:
    # macOS keeps a large freed allocation in this process's RSS for about two
    # seconds, so a watchdog restarted straight after an unconstrained test
    # would kill the process for memory that test already released.
    deadline = time.monotonic() + _RSS_SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        resident_bytes = _own_resident_bytes()
        if resident_bytes is None or resident_bytes < limit_bytes:
            return
        time.sleep(0.05)


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


def _proc_resident_bytes(parent_pid: int) -> int | None:
    # The watchdog is the parent's direct child, so a changed parent id means
    # the parent exited and its pid may already belong to another process.
    if os.getppid() != parent_pid:
        return None
    try:
        with Path(f"/proc/{parent_pid}/statm").open("rb") as statm:
            resident_pages = int(statm.read().split()[1])
    except OSError as exc:
        if os.getppid() == parent_pid:
            reason = f"cannot read {exc.filename}: {exc.strerror}"
            os.write(2, f"pytest memory guard: {reason}; not guarding\n".encode())
        return None
    return resident_pages * os.sysconf("SC_PAGE_SIZE")


def _proc_probe(parent_pid: int) -> _Probe:
    # Linux reads procfs, so Linux repositories adopt the guard without
    # psutil, a dependency only macOS and Windows need. A pidfd names this
    # exact process, so a reused pid is never signalled.
    try:
        parent_fd = os.pidfd_open(parent_pid)
    except OSError:
        parent_fd = None

    def resident_bytes() -> int | None:
        return _proc_resident_bytes(parent_pid)

    def kill_parent() -> None:
        if parent_fd is None:
            os.kill(parent_pid, signal.SIGKILL)
        else:
            signal.pidfd_send_signal(parent_fd, signal.SIGKILL)

    return resident_bytes, kill_parent


def _psutil_probe(parent_pid: int) -> _Probe:
    import psutil

    parent = psutil.Process(parent_pid)

    def resident_bytes() -> int | None:
        try:
            return parent.memory_info().rss
        except psutil.Error:
            return None

    return resident_bytes, parent.kill


def _report_first_sample() -> None:
    # The parent waits on this byte; closing stdout afterwards leaves the pipe
    # with nothing more to carry.
    try:
        os.write(1, _WATCHDOG_READY)
        os.close(1)
    except OSError:
        return


def _watch_parent(
    parent_pid: int,
    limit_bytes: int,
    probe: str,
    on_first_sample: Callable[[], None] | None = None,
) -> int:
    make_probe = _proc_probe if probe == "proc" else _psutil_probe
    resident_bytes, kill_parent = make_probe(parent_pid)
    while True:
        rss_bytes = resident_bytes()
        if rss_bytes is None:
            return 0
        if rss_bytes >= limit_bytes:
            limit_mb = limit_bytes // 1024 // 1024
            rss_mb = rss_bytes // 1024 // 1024
            ceiling = f"process exceeded its {limit_mb} MiB RSS ceiling"
            notice = f"pytest memory guard: {ceiling} ({rss_mb} MiB); terminating it\n"
            _write_termination_notice(notice.encode())
            kill_parent()
            return 0
        if on_first_sample is not None:
            on_first_sample()
            on_first_sample = None
        time.sleep(0.05)


def _main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "--watch-parent":
        return 2
    return _watch_parent(int(argv[1]), int(argv[2]), argv[3], _report_first_sample)


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
