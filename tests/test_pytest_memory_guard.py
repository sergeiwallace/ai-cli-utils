from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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


def test_given_runaway_xdist_worker_when_memory_limit_is_reached_then_worker_is_stopped(
    tmp_path: Path,
):
    runaway_test = tmp_path / "test_synthetic_runaway.py"
    runaway_test.write_text(
        """
import time


def test_synthetic_runaway_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(32)]
    print(f"allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = "384"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
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
    assert result.returncode != 0, "the runaway xdist worker completed despite exceeding its memory ceiling"
    assert "allocation completed" not in output
    assert "pytest memory guard" in output


def test_given_unconstrained_memory_marker_when_limit_is_reached_then_allocation_completes(
    tmp_path: Path,
):
    unconstrained_test = tmp_path / "test_synthetic_unconstrained_memory.py"
    unconstrained_test.write_text(
        """
import time

import pytest


@pytest.mark.unconstrained_memory
def test_synthetic_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(32)]
    print(f"allocation completed: {len(chunks)} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = "384"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
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
    assert "allocation completed: 32 chunks" in output
    assert "pytest memory guard" in output


def test_given_unconstrained_memory_marker_when_next_test_runs_then_guard_is_restored(
    tmp_path: Path,
):
    mixed_test = tmp_path / "test_synthetic_memory_scoping.py"
    mixed_test.write_text(
        """
import pytest


@pytest.mark.unconstrained_memory
def test_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(32)]
    print(f"unconstrained allocation completed: {len(chunks)} chunks")


def test_guarded_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range(32)]
    print(f"guarded allocation completed: {len(chunks)} chunks")
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = "384"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
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
    assert "unconstrained allocation completed: 32 chunks" in output
    assert "guarded allocation completed" not in output
    assert "pytest memory guard" in output
