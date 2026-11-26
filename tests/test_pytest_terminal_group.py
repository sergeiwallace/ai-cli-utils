"""Exercise terminal test scheduling with the repository's default pytest options."""

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from process_wait import scaled

from pytest_ci_shard import shard_of

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="terminal tests share an xdist group on macOS")
@pytest.mark.parametrize("sharded", [False, True])
def test_given_terminal_and_regular_cases_when_distributed_then_only_terminal_cases_share_a_worker(tmp_path, sharded):
    (tmp_path / "test_workers.py").write_text(
        dedent("""\
        import pytest

        @pytest.fixture
        def record_worker(request, worker_id, pytestconfig):
            path = pytestconfig.rootpath / request.node.name
            path.write_text(worker_id, encoding="utf-8")

        @pytest.mark.real_tmux
        @pytest.mark.parametrize("case", range(4))
        def test_tmux(case, record_worker):
            pass

        @pytest.mark.real_pty
        @pytest.mark.parametrize("case", range(4))
        def test_pty(case, record_worker):
            pass

        @pytest.mark.parametrize("case", range(8))
        def test_regular(case, record_worker):
            pass
        """),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    env.pop("PYTEST_ADDOPTS", None)
    args = ["-p", "pytest_ci_shard", "--ci-shard=1/2"] if sharded else []
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(REPO / "pyproject.toml"),
            "--rootdir",
            ".",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "-q",
            *args,
            "test_workers.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=scaled(60),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("test_*]")}
    expected = {
        f"test_{kind}[{case}]" for kind, count in (("tmux", 4), ("pty", 4), ("regular", 8)) for case in range(count)
    }
    if sharded:
        expected = {name for name in expected if shard_of(f"test_workers.py::{name}", 2) == 1}
    assert records.keys() == expected
    terminal_workers = {worker for name, worker in records.items() if not name.startswith("test_regular")}
    assert len(terminal_workers) == 1, records
    assert set(records.values()) - terminal_workers, records
