from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_given_runaway_xdist_worker_when_memory_limit_is_reached_then_worker_is_stopped(tmp_path: Path):
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
    env["PYTEST_WORKER_MEMORY_LIMIT_MB"] = "192"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(repository_root / "pyproject.toml"),
            "-n",
            "1",
            "--max-worker-restart=0",
            "-s",
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
