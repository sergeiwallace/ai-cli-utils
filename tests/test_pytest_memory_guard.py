from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_FORCE_WATCHDOG_PLUGIN = "force_pytest_memory_guard_watchdog"
_RUNAWAY_COMPLETED = "the runaway xdist worker completed past its memory ceiling"
_KILL_NOTICE = "pytest memory guard: process exceeded its 384 MiB RSS ceiling"
_HAS_PSUTIL = importlib.util.find_spec("psutil") is not None
_NO_PSUTIL = "psutil is not installed, so the portable watchdog cannot run here"
_requires_xdist = pytest.mark.skipif(
    importlib.util.find_spec("xdist") is None,
    reason="pytest-xdist is not installed, so there are no xdist workers to guard",
)


@pytest.fixture(
    params=("native", "watchdog") if sys.platform.startswith("linux") else ("native",),
)
def memory_guard_mode(request: pytest.FixtureRequest) -> str:
    """Exercise Linux's native guard plus the portable guard on Linux."""
    if request.param == "watchdog" and not _HAS_PSUTIL:
        pytest.skip(_NO_PSUTIL)
    return str(request.param)


def _memory_guard_invocation(
    tmp_path: Path,
    memory_guard_mode: str,
) -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = "384"
    if memory_guard_mode != "watchdog":
        return env, []

    # Keep the host platform intact and override only this plugin's platform
    # decision. The helper is loaded in the controller and every xdist worker.
    force_watchdog_plugin = tmp_path / f"{_FORCE_WATCHDOG_PLUGIN}.py"
    force_watchdog_plugin.write_text(
        """
import sys
from types import SimpleNamespace


def pytest_load_initial_conftests():
    import pytest_memory_guard

    pytest_memory_guard.sys = SimpleNamespace(
        argv=sys.argv,
        executable=sys.executable,
        platform="darwin",
    )
""",
        encoding="utf-8",
    )
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(tmp_path)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    return env, ["-p", _FORCE_WATCHDOG_PLUGIN]


def _pytest_config_args(repository_root: Path) -> list[str]:
    """``-c <path>`` for whichever config this repo actually uses, or none.

    project-template's own layout uses ``pytest.ini``/``pytest.ini.jinja``; a
    downstream consumer repo (this file is copier-propagated) more commonly
    configures pytest via ``[tool.pytest.ini_options]`` in ``pyproject.toml``.
    Passing no ``-c`` at all lets pytest fall back to its own auto-discovery
    from ``cwd``, which is always correct but only used as the last resort so
    an explicit config file is still preferred when one exists.
    """
    for name in ("pytest.ini", "pytest.ini.jinja", "pyproject.toml"):
        candidate = repository_root / name
        if candidate.exists():
            return ["-c", str(candidate)]
    return []


def test_plugin_import_does_not_require_portable_watchdog_dependency():
    """Linux can load the RLIMIT guard even when psutil is not installed."""
    repository_root = Path(__file__).resolve().parents[1]
    script = """
import builtins

real_import = builtins.__import__


def import_without_psutil(name, *args, **kwargs):
    if name == "psutil":
        raise ModuleNotFoundError("blocked psutil for Linux guard import test")
    return real_import(name, *args, **kwargs)


builtins.__import__ = import_without_psutil
import pytest_memory_guard
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@_requires_xdist
def test_given_runaway_xdist_worker_when_memory_limit_is_reached_then_worker_is_stopped(
    tmp_path: Path,
    memory_guard_mode: str,
):
    runaway_test = tmp_path / "test_synthetic_runaway.py"
    runaway_test.write_text(
        """
import time


def test_synthetic_runaway_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(64)]
    print(f"allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args = _memory_guard_invocation(tmp_path, memory_guard_mode)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "1",
            "--max-worker-restart=0",
            str(runaway_test),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, _RUNAWAY_COMPLETED
    assert "allocation completed" not in output
    assert "pytest memory guard" in output


@_requires_xdist
def test_given_unconstrained_memory_marker_when_limit_is_reached_then_allocation_completes(
    tmp_path: Path,
    memory_guard_mode: str,
):
    unconstrained_test = tmp_path / "test_synthetic_unconstrained_memory.py"
    unconstrained_test.write_text(
        """
import time

import pytest


@pytest.mark.unconstrained_memory
def test_synthetic_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(64)]
    print(f"allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args = _memory_guard_invocation(tmp_path, memory_guard_mode)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "1",
            "--max-worker-restart=0",
            "-rP",
            str(unconstrained_test),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "allocation completed: 64 chunks" in output
    assert "pytest memory guard" in output


@_requires_xdist
def test_given_unconstrained_memory_marker_when_next_test_runs_then_guard_is_restored(
    tmp_path: Path,
    memory_guard_mode: str,
):
    mixed_test = tmp_path / "test_synthetic_memory_scoping.py"
    mixed_test.write_text(
        """
import time

import pytest


@pytest.mark.unconstrained_memory
def test_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(64)]
    print(f"unconstrained allocation completed: {len(chunks)} chunks")


def test_guarded_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(64)]
    print(f"guarded allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args = _memory_guard_invocation(tmp_path, memory_guard_mode)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
            "-p",
            "no:randomly",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "1",
            "--max-worker-restart=0",
            "-rP",
            str(mixed_test),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "unconstrained allocation completed: 64 chunks" in output
    assert "guarded allocation completed" not in output
    assert "pytest memory guard" in output


@_requires_xdist
def test_given_worker_stderr_is_not_forwarded_then_controller_reports_guard_notices(
    tmp_path: Path,
):
    if not _HAS_PSUTIL:
        pytest.skip(_NO_PSUTIL)
    runaway_test = tmp_path / "test_synthetic_runaway.py"
    runaway_test.write_text(
        """
import time


def test_synthetic_runaway_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(64)]
    print(f"allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args = _memory_guard_invocation(tmp_path, "watchdog")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "1",
            "--max-worker-restart=0",
            str(runaway_test),
        ],
        cwd=repository_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0, _RUNAWAY_COMPLETED
    assert "pytest memory guard: limiting each test process to 384 MiB" in result.stdout
    assert _KILL_NOTICE in result.stdout
