"""The local fallback for a required check: ``scripts/run_local_ci.py``.

Each test runs the script as a maintainer would, through its command line. The run
tests copy it with the real workflow files into a scratch repository and put stub
``uv`` and ``gh`` programs first on PATH, so they prove which commands run, in what
order, and what status would be posted, without running the suite or calling GitHub.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "run_local_ci.py"
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="stubs are POSIX shell scripts"
)

# A check that never runs on this machine, and one with tools that does.
FOREIGN_CHECK = "test-macos" if sys.platform != "darwin" else "test"
NATIVE_TOOL_CHECK = {"linux": "test", "darwin": "test-macos"}.get(sys.platform)


def _run(*args: str, cwd: Path = REPO, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = cwd / "scripts" / "run_local_ci.py"
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True, env=env, check=False
    )


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def scratch(tmp_path: Path) -> dict[str, Path]:
    """A committed repository holding the script and the real workflows, and a stub-only PATH."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "run_local_ci.py")
    shutil.copytree(REPO / ".github" / "workflows", repo / ".github" / "workflows")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "bash"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    log = tmp_path / "calls.log"
    _stub(
        bin_dir, "uv", f'echo "uv $*" >> "{log}"\ncase "$*" in *"$FAIL_ON"*) [ -n "$FAIL_ON" ] && exit 3;; esac\nexit 0'
    )
    _stub(bin_dir, "gh", f'echo "gh $*" >> "{log}"')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    return {"repo": repo, "bin": bin_dir, "log": log}


def _env(scratch: dict[str, Path], **extra: str) -> dict[str, str]:
    return {"PATH": str(scratch["bin"]), "HOME": str(scratch["repo"].parent), **extra}


def _calls(scratch: dict[str, Path]) -> list[str]:
    log = scratch["log"]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_given_a_sharded_check_when_dry_run_then_it_prints_the_sync_and_one_test_command_per_shard() -> None:
    shards = yaml.safe_load((REPO / ".github" / "workflows" / "ci-windows.yml").read_text(encoding="utf-8"))["jobs"][
        "test-windows-shard"
    ]["strategy"]["matrix"]["shard"]
    result = _run("-n", "test-windows")
    lines = result.stdout.splitlines()

    assert result.returncode == 0, result.stderr
    assert lines[0].startswith("uv sync --locked")
    assert [line for line in lines if "pytest" in line] == [
        f"uv run --python 3.14 pytest --tb=short -p pytest_ci_shard --ci-shard={shard}/{len(shards)} --durations=15"
        for shard in shards
    ]


def test_given_the_lint_check_when_dry_run_then_it_keeps_the_self_hosted_variant_and_drops_the_hosted_one() -> None:
    lines = _run("-n", "lint").stdout.splitlines()

    assert "uv run python scripts/lint_doc_alignment.py" in lines
    assert "python scripts/lint_doc_alignment.py" not in lines
    assert "uv run pyright src/" in lines
    assert all(line.startswith("uv ") for line in lines), lines


@POSIX_ONLY
def test_given_a_step_with_an_expression_it_cannot_fill_when_dry_run_then_it_refuses(
    scratch: dict[str, Path],
) -> None:
    workflow = scratch["repo"] / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(text.replace("uv run pyright src/", "uv run pyright ${{ inputs.target }}"), encoding="utf-8")

    result = _run("-n", "lint", cwd=scratch["repo"], env=_env(scratch))

    assert result.returncode == 2
    assert "cannot fill" in result.stderr


@POSIX_ONLY
def test_given_a_uv_step_only_a_hosted_runner_runs_when_dry_run_then_it_is_left_out(scratch: dict[str, Path]) -> None:
    workflow = scratch["repo"] / ".github" / "workflows" / "ci.yml"
    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    data["jobs"]["lint"]["steps"].append({"if": "runner.environment == 'github-hosted'", "run": "uv run hosted-only"})
    data["jobs"]["lint"]["steps"].append(
        {"if": "runner.environment == 'self-hosted'", "run": "uv run self-hosted-only"}
    )
    workflow.write_text(yaml.safe_dump(data), encoding="utf-8")

    lines = _run("-n", "lint", cwd=scratch["repo"], env=_env(scratch)).stdout.splitlines()

    assert "uv run self-hosted-only" in lines
    assert "uv run hosted-only" not in lines


def test_given_a_check_for_another_operating_system_when_run_then_it_refuses_and_runs_nothing() -> None:
    result = _run(FOREIGN_CHECK)

    assert result.returncode == 2
    assert f"this machine is {sys.platform}" in result.stderr
    assert "$ uv" not in result.stdout


@POSIX_ONLY
def test_given_the_lint_check_when_run_then_every_command_runs_in_workflow_order(scratch: dict[str, Path]) -> None:
    planned = _run("-n", "lint").stdout.splitlines()

    result = _run("lint", cwd=scratch["repo"], env=_env(scratch))

    assert result.returncode == 0, result.stderr
    assert _calls(scratch) == planned
    assert "lint passed" in result.stdout


@POSIX_ONLY
def test_given_a_failing_command_when_run_then_it_stops_there_and_exits_one(scratch: dict[str, Path]) -> None:
    result = _run("lint", cwd=scratch["repo"], env=_env(scratch, FAIL_ON="ruff check"))

    assert result.returncode == 1
    assert _calls(scratch)[-1] == "uv run ruff check src/ tests/"
    assert "lint failed" in result.stdout


@POSIX_ONLY
@pytest.mark.parametrize(("fail_on", "state", "code"), [("", "success", 0), ("pyright", "failure", 1)])
def test_given_report_when_the_check_finishes_then_its_result_is_posted_on_head_under_the_check_name(
    scratch: dict[str, Path], fail_on: str, state: str, code: int
) -> None:
    result = _run("-r", "lint", cwd=scratch["repo"], env=_env(scratch, FAIL_ON=fail_on))
    posts = [call for call in _calls(scratch) if call.startswith("gh ")]

    assert result.returncode == code, result.stderr
    assert posts == [
        f"gh api -X POST repos/{{owner}}/{{repo}}/statuses/{_head(scratch['repo'])} -f state={state} -f context=lint "
        f"-f description=Local run on {sys.platform} by scripts/run_local_ci.py"
    ]


@POSIX_ONLY
def test_given_report_with_uncommitted_changes_when_run_then_it_refuses_and_runs_nothing(
    scratch: dict[str, Path],
) -> None:
    (scratch["repo"] / "scripts" / "extra.py").write_text("", encoding="utf-8")

    result = _run("-r", "lint", cwd=scratch["repo"], env=_env(scratch))

    assert result.returncode == 2
    assert "clean tree" in result.stderr
    assert _calls(scratch) == []


@POSIX_ONLY
@pytest.mark.skipif(NATIVE_TOOL_CHECK is None, reason="no test check runs on this operating system")
def test_given_a_test_check_without_its_tools_when_run_then_it_names_them_and_runs_nothing(
    scratch: dict[str, Path],
) -> None:
    assert NATIVE_TOOL_CHECK is not None
    result = _run(NATIVE_TOOL_CHECK, cwd=scratch["repo"], env=_env(scratch))

    assert result.returncode == 2
    assert "needs tmux, dolt on PATH" in result.stderr
    assert _calls(scratch) == []


@POSIX_ONLY
@pytest.mark.skipif(NATIVE_TOOL_CHECK is None, reason="no test check runs on this operating system")
def test_given_a_test_check_with_its_tools_when_run_then_the_suite_command_runs_through_the_wrapper_prefix(
    scratch: dict[str, Path],
) -> None:
    assert NATIVE_TOOL_CHECK is not None
    for tool in ("tmux", "dolt"):
        _stub(scratch["bin"], tool, "exit 0")

    result = _run(NATIVE_TOOL_CHECK, cwd=scratch["repo"], env=_env(scratch))

    assert result.returncode == 0, result.stderr
    assert any(call.startswith("uv run pytest") for call in _calls(scratch))
