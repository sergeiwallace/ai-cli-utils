"""A run's pytest temp dirs live in this suite's own root, out of the host-wide shared one.

pytest keeps every run's base dir under ``<temp>/pytest-of-<user>/pytest-N``, one namespace
shared by every pytest run on the host, of every project, and by anything that sweeps it. A
full local run here lost its live base dir partway through: every later ``tmp_path`` setup
raised ``FileNotFoundError`` from ``os.scandir`` while CI, alone on its host, stayed green.
``pytest_configure`` in ``conftest.py`` therefore points ``PYTEST_DEBUG_TEMPROOT`` at the
suite's own root, inside which pytest's numbered dirs, lock files and retention work as usual.

**Why these drive a real inner pytest run.** Where a run's base dir goes is decided once, by
the conftest, before any test exists; only a fresh pytest process shows it. Each inner run gets
a private ``TMPDIR``, so the shared root it would otherwise use is a sandbox this test may
sweep without touching any other run on the host.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import suite_temproot

_INNER = "AI_CLI_TEMPROOT_INNER"
_REPORT = "AI_CLI_TEMPROOT_REPORT"
_GO = "AI_CLI_TEMPROOT_GO"
_inner_only = pytest.mark.skipif(not os.environ.get(_INNER), reason="only runs inside the outer test's pytest")

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent


@_inner_only
def test_inner_given_a_fresh_run_when_tmp_path_is_requested_then_its_path_is_reported(tmp_path):
    Path(os.environ[_REPORT]).write_text(str(tmp_path), encoding="utf-8")


@_inner_only
def test_inner_given_a_live_run_when_the_host_sweeps_then_its_tmp_path_survives(tmp_path, tmp_path_factory):
    (tmp_path / "held").write_text("x", encoding="utf-8")
    Path(os.environ[_REPORT]).write_text(str(tmp_path), encoding="utf-8")
    go = Path(os.environ[_GO])
    deadline = time.monotonic() + 120
    while not go.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert go.exists(), "the outer test never released this run"
    assert (tmp_path / "held").read_text(encoding="utf-8") == "x"
    assert tmp_path_factory.mktemp("after-sweep").is_dir()


def _inner_env(sandbox: Path, report: Path, go: Path | None = None) -> dict[str, str]:
    """The outer run's environment with no temp root chosen, and ``sandbox`` as the system temp dir."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_DEBUG_TEMPROOT"}
    env.update({_INNER: "1", _REPORT: str(report), "TMPDIR": str(sandbox), "TMP": str(sandbox), "TEMP": str(sandbox)})
    if go is not None:
        env[_GO] = str(go)
    return env


def _inner_argv(test_name: str, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        f"{_THIS_FILE}::{test_name}",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "-n",
        "0",
        *extra,
    ]


def _shared_root(sandbox: Path) -> Path:
    """Where pytest would put this run's base dirs if the suite did not relocate them."""
    return sandbox / f"pytest-of-{getpass.getuser()}"


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "temp"
    root.mkdir()
    return root


def _run(argv, env, timeout=300):
    return subprocess.run(argv, cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def test_given_no_operator_choice_when_the_suite_runs_then_tmp_path_is_outside_the_shared_pytest_root(
    sandbox, tmp_path
):
    report = tmp_path / "report"
    result = _run(
        _inner_argv("test_inner_given_a_fresh_run_when_tmp_path_is_requested_then_its_path_is_reported"),
        _inner_env(sandbox, report),
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    inner_tmp = Path(report.read_text(encoding="utf-8")).resolve()
    assert suite_temproot(str(sandbox)).resolve() in inner_tmp.parents
    assert _shared_root(sandbox).resolve() not in inner_tmp.parents


def test_given_a_live_run_when_the_shared_root_is_swept_and_a_second_run_finishes_then_neither_loses_its_base_dir(
    sandbox, tmp_path
):
    report = tmp_path / "report-live"
    go = tmp_path / "go"
    live = subprocess.Popen(
        _inner_argv("test_inner_given_a_live_run_when_the_host_sweeps_then_its_tmp_path_survives"),
        cwd=_REPO_ROOT,
        env=_inner_env(sandbox, report, go),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 120
        while not report.exists() and time.monotonic() < deadline and live.poll() is None:
            time.sleep(0.05)
        assert report.exists(), "the live run never reached its test"

        # Another process prunes the host-wide pytest root: every numbered dir in it goes.
        for numbered in _shared_root(sandbox).glob("pytest-*"):
            shutil.rmtree(numbered, ignore_errors=True)

        second_report = tmp_path / "report-second"
        second = _run(
            _inner_argv("test_inner_given_a_fresh_run_when_tmp_path_is_requested_then_its_path_is_reported"),
            _inner_env(sandbox, second_report),
        )
        assert second.returncode == 0, second.stdout[-2000:] + second.stderr[-2000:]
        assert Path(second_report.read_text(encoding="utf-8")) != Path(report.read_text(encoding="utf-8"))
    finally:
        go.write_text("go", encoding="utf-8")
        out, _ = live.communicate(timeout=300)
    assert live.returncode == 0, out[-3000:]


def test_given_an_explicit_basetemp_when_the_suite_runs_then_tmp_path_is_under_it(sandbox, tmp_path):
    report = tmp_path / "report"
    explicit = tmp_path / "explicit-basetemp"
    result = _run(
        _inner_argv(
            "test_inner_given_a_fresh_run_when_tmp_path_is_requested_then_its_path_is_reported",
            f"--basetemp={explicit}",
        ),
        _inner_env(sandbox, report),
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert explicit.resolve() in Path(report.read_text(encoding="utf-8")).resolve().parents


def _fake_checkout_with_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A checkout with a Beads config and one linked worktree, shaped like a real one."""
    checkout = tmp_path / "myrepo"
    (checkout / ".git").mkdir(parents=True)
    (checkout / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    worktree = checkout / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {checkout / '.git' / 'worktrees' / 'session-1'}\n", encoding="utf-8")
    beads_config = checkout / ".beads" / "config.yaml"
    beads_config.parent.mkdir()
    beads_config.write_text('issue-prefix: "myrepo"\n', encoding="utf-8")
    return checkout, worktree, beads_config


@pytest.mark.parametrize("via", ["basetemp", "tmpdir"])
def test_given_a_temp_root_inside_a_worktree_when_the_suite_runs_then_it_refuses_before_touching_the_checkout(
    sandbox, tmp_path, via
):
    checkout, worktree, beads_config = _fake_checkout_with_worktree(tmp_path)
    scratch = worktree / ".scratch"
    scratch.mkdir()
    register_test = (
        f"{_THIS_FILE.parent / 'test_cli_dispatch.py'}::TestRegisterCommand::"
        "test_given_repository_and_prefix_when_register_then_persists_registry_entry"
    )
    argv = [
        sys.executable,
        "-m",
        "pytest",
        register_test,
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "-n",
        "0",
    ]
    env = _inner_env(sandbox, tmp_path / "report")
    if via == "basetemp":
        argv.append(f"--basetemp={scratch / 'bt'}")
    else:
        env.update({"TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch)})

    result = _run(argv, env)

    assert beads_config.read_text(encoding="utf-8") == 'issue-prefix: "myrepo"\n'
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout[-2000:] + result.stderr[-2000:]
    assert str(checkout.resolve()) in result.stderr
