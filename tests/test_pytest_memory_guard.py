from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_FORCE_WATCHDOG_PLUGIN = "force_pytest_memory_guard_watchdog"
_RUNAWAY_COMPLETED = "the runaway xdist worker completed past its memory ceiling"
_BASELINE_PREFIX = "pytest memory guard baseline:"
_MIB = 1024 * 1024
_BASELINE_MARGIN_MB = 64
_ALLOCATION_MB = _BASELINE_MARGIN_MB * 2
_ALLOCATION_CHUNKS = _ALLOCATION_MB // 8
_CHILD_RESERVATION_BYTES = 8 * 1024 * _MIB
_HAS_PSUTIL = importlib.util.find_spec("psutil") is not None
# Every guarded child runs with plugin autoload off (see _memory_guard_args), so
# pytest-randomly never loads there and test order is already kept.
_KEEP_TEST_ORDER: list[str] = []
_NO_PSUTIL = "psutil is not installed, so the portable watchdog cannot run here"
_requires_xdist = pytest.mark.skipif(
    importlib.util.find_spec("xdist") is None,
    reason="pytest-xdist is not installed, so there are no xdist workers to guard",
)


@pytest.fixture(
    params=("native", "watchdog") if sys.platform.startswith("linux") else ("native",),
)
def memory_guard_mode(request: pytest.FixtureRequest) -> str:
    """Exercise Linux's procfs watchdog plus the psutil watchdog on Linux."""
    if request.param == "watchdog" and not _HAS_PSUTIL:
        pytest.skip(_NO_PSUTIL)
    return str(request.param)


def _memory_guard_invocation(
    tmp_path: Path,
    memory_guard_mode: str,
) -> tuple[dict[str, str], list[str], int]:
    env = os.environ.copy()
    guard_args = _memory_guard_args(tmp_path, memory_guard_mode, env)
    baseline_bytes = _measure_worker_memory_baseline(tmp_path, env, guard_args)
    limit_mb = (baseline_bytes + _BASELINE_MARGIN_MB * _MIB + _MIB - 1) // _MIB
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = str(limit_mb)
    return env, guard_args, limit_mb


def _memory_guard_args(
    tmp_path: Path,
    memory_guard_mode: str,
    env: dict[str, str],
) -> list[str]:
    # A host's locale archive can occupy hundreds of MiB of virtual address
    # space without resident pages. Keep that unrelated mapping out of the
    # native address-space probes without raising their allocation ceiling.
    env["LC_ALL"] = "C"
    # These small synthetic suites need only xdist and the guard. Unrelated
    # plugins can exhaust the address-space budget during collection, before
    # an unconstrained test's marker can lift it. With autoload off, the
    # plugins they do use are loaded by name.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    plugin_args = ["-p", "xdist.plugin"]
    if memory_guard_mode != "watchdog":
        return plugin_args

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
    return [*plugin_args, "-p", _FORCE_WATCHDOG_PLUGIN]


def _measure_worker_memory_baseline(
    tmp_path: Path,
    env: dict[str, str],
    guard_args: list[str],
) -> int:
    baseline_test = tmp_path / "test_synthetic_memory_baseline.py"
    baseline_test.write_text(
        f"""
import os
import sys


def test_report_memory_baseline():
    if sys.platform.startswith("linux"):
        statm = open("/proc/self/statm", encoding="utf-8").read().split()
        baseline_bytes = int(statm[1]) * os.sysconf("SC_PAGE_SIZE")
    else:
        import psutil

        baseline_bytes = psutil.Process().memory_info().rss
    print("{_BASELINE_PREFIX}", baseline_bytes, "bytes")
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    measurement_env = env.copy()
    measurement_env.pop("PYTEST_WORKER_MEMORY_LIMIT_MB", None)
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
            str(baseline_test),
        ],
        cwd=repository_root,
        env=measurement_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    match = re.search(rf"{_BASELINE_PREFIX} (\d+) bytes", output)
    assert match is not None, output
    return int(match.group(1))


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


def test_given_unrelated_installed_plugin_when_memory_probe_runs_then_it_is_not_loaded(tmp_path, monkeypatch):
    metadata = tmp_path / "unrelated_probe-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: unrelated-probe\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text("[pytest11]\nunrelated_probe = unrelated_probe\n")
    (tmp_path / "unrelated_probe.py").write_text('raise RuntimeError("unrelated plugin loaded by memory probe")\n')
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(tmp_path), os.environ.get("PYTHONPATH", ""))))
    probe = tmp_path / "test_probe.py"
    probe.write_text("def test_given_probe_when_run_then_it_completes():\n    pass\n")
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, _ = _memory_guard_invocation(tmp_path, "native")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "1",
            str(probe),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_given_host_locale_when_guarded_probe_allocates_below_ceiling_then_allocation_completes(tmp_path):
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "def test_given_small_allocation_when_guarded_then_it_completes():\n"
        "    chunks = [bytearray(8 * 1024 * 1024) for _ in range(4)]\n"
        "    assert len(chunks) == 4\n"
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, _ = _memory_guard_invocation(tmp_path, "native")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *guard_args,
            "-p",
            "pytest_memory_guard",
            *_pytest_config_args(repository_root),
            "-n",
            "0",
            str(probe),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_import_does_not_require_portable_watchdog_dependency():
    """Linux can load the procfs guard even when psutil is not installed."""
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
        f"""
import time


def test_synthetic_runaway_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range({_ALLOCATION_CHUNKS})]
    print(f"allocation completed: {{len(chunks)}} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, _ = _memory_guard_invocation(tmp_path, memory_guard_mode)

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
        f"""
import time

import pytest


@pytest.mark.unconstrained_memory
def test_synthetic_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range({_ALLOCATION_CHUNKS})]
    print(f"allocation completed: {{len(chunks)}} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, _ = _memory_guard_invocation(tmp_path, memory_guard_mode)

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
    assert f"allocation completed: {_ALLOCATION_CHUNKS} chunks" in output
    assert "pytest memory guard" in output


@_requires_xdist
def test_given_unconstrained_memory_marker_when_next_test_runs_then_guard_is_restored(
    tmp_path: Path,
    memory_guard_mode: str,
):
    mixed_test = tmp_path / "test_synthetic_memory_scoping.py"
    mixed_test.write_text(
        f"""
import time

import pytest


@pytest.mark.unconstrained_memory
def test_unconstrained_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range({_ALLOCATION_CHUNKS})]
    print(f"unconstrained allocation completed: {{len(chunks)}} chunks")


def test_guarded_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range({_ALLOCATION_CHUNKS})]
    print(f"guarded allocation completed: {{len(chunks)}} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, _ = _memory_guard_invocation(tmp_path, memory_guard_mode)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:fleet_testkit",
            *_KEEP_TEST_ORDER,
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
    assert f"unconstrained allocation completed: {_ALLOCATION_CHUNKS} chunks" in output
    assert "guarded allocation completed" not in output
    assert "pytest memory guard" in output


@_requires_xdist
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows mmap commits what it maps, so it cannot only reserve address space",
)
def test_given_child_reserving_address_space_past_the_limit_when_guarded_then_child_completes(
    tmp_path: Path,
    memory_guard_mode: str,
):
    """Go's wazero allocator (gitleaks) and Chromium reserve gigabytes of address
    space at start-up while staying small in resident memory. A guarded test's
    child doing that must start, under the default limit."""
    reservation_test = tmp_path / "test_synthetic_child_reservation.py"
    reservation_test.write_text(
        f"""
import subprocess
import sys


def test_child_reserves_address_space():
    reserve = "import mmap; mmap.mmap(-1, {_CHILD_RESERVATION_BYTES}, prot=0)"
    result = subprocess.run(
        [sys.executable, "-c", reserve + "; print('reservation completed')"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout, result.stderr)
    assert result.returncode == 0
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("PYTEST_WORKER_MEMORY_LIMIT_MB", None)
    guard_args = _memory_guard_args(tmp_path, memory_guard_mode, env)

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
            str(reservation_test),
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "reservation completed" in output
    assert "limiting each test process to 2048 MiB" in output


@_requires_xdist
def test_given_worker_stderr_is_not_forwarded_then_controller_reports_guard_notices(
    tmp_path: Path,
):
    if not _HAS_PSUTIL:
        pytest.skip(_NO_PSUTIL)
    runaway_test = tmp_path / "test_synthetic_runaway.py"
    runaway_test.write_text(
        f"""
import time


def test_synthetic_runaway_allocation():
    chunks = [bytearray(8 * 1024 * 1024) for _ in range({_ALLOCATION_CHUNKS})]
    print(f"allocation completed: {{len(chunks)}} chunks")
    time.sleep(1)
""",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    env, guard_args, limit_mb = _memory_guard_invocation(tmp_path, "watchdog")

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
    limit_notice = f"limiting each test process to {limit_mb} MiB"
    assert limit_notice in result.stdout
    kill_notice = f"process exceeded its {limit_mb} MiB RSS ceiling"
    assert kill_notice in result.stdout
