"""The CI shard plugin, driven the way the workflow drives it: a real pytest run in a subprocess."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from process_wait import scaled

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEST_COUNT = 40


@pytest.fixture
def synthetic_suite(tmp_path: Path) -> Path:
    # An empty ini pins rootdir and config here, away from this repo's own addopts.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_many.py").write_text(
        f"import pytest\n\n\n@pytest.mark.parametrize('n', range({TEST_COUNT}))\ndef test_case(n):\n    pass\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_pytest(suite: Path, *args: str, randomly: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPOSITORY_ROOT), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *([] if randomly else ["-p", "no:randomly"]), *args],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=scaled(60),
        check=False,
    )


def _collected_ids(suite: Path, shard: str, *extra: str) -> set[str]:
    result = _run_pytest(
        suite, "-p", "pytest_ci_shard", f"--ci-shard={shard}", "--collect-only", "-q", *extra, randomly=bool(extra)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if line.startswith("test_many.py::")}


def test_given_two_shards_when_each_collects_then_every_test_lands_in_exactly_one(synthetic_suite):
    first = _collected_ids(synthetic_suite, "1/2")
    second = _collected_ids(synthetic_suite, "2/2")

    assert first, "shard 1/2 selected nothing, so the split is not a split"
    assert second, "shard 2/2 selected nothing, so the split is not a split"
    assert first.isdisjoint(second)
    assert len(first | second) == TEST_COUNT


def test_given_jobs_shuffled_with_different_seeds_when_sharded_then_they_still_split_the_suite_exactly(
    synthetic_suite,
):
    # Each CI job draws its own pytest-randomly seed, and randomly reorders the items before
    # any other plugin sees them, so a split by position would differ from job to job.
    first = _collected_ids(synthetic_suite, "1/2", "--randomly-seed=1")
    second = _collected_ids(synthetic_suite, "2/2", "--randomly-seed=2")

    assert first == _collected_ids(synthetic_suite, "1/2")
    assert first.isdisjoint(second)
    assert len(first | second) == TEST_COUNT


def test_given_a_shard_under_xdist_when_run_then_every_worker_runs_the_same_selection(synthetic_suite):
    serial = _collected_ids(synthetic_suite, "1/2")

    result = _run_pytest(synthetic_suite, "-p", "pytest_ci_shard", "--ci-shard=1/2", "-n", "2")

    # Only the count that ran is checked: xdist workers deselect on their own, and the
    # controller's summary never reports their deselections.
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert re.search(rf"\b{len(serial)} passed\b", output), output


@pytest.mark.parametrize("value", ["2", "0/2", "3/2", "1/0", "a/b"])
def test_given_a_malformed_shard_when_pytest_starts_then_it_refuses_naming_the_value(synthetic_suite, value):
    result = _run_pytest(synthetic_suite, "-p", "pytest_ci_shard", f"--ci-shard={value}", "--collect-only", "-q")

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert repr(value) in result.stderr


def test_given_the_plugin_not_loaded_when_the_shard_option_is_passed_then_pytest_refuses(synthetic_suite):
    result = _run_pytest(synthetic_suite, "--ci-shard=1/2", "--collect-only", "-q")

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "--ci-shard" in result.stderr
