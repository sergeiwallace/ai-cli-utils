#!/usr/bin/env python3
"""Run one required CI check on this machine, and optionally report it as a commit status.

The fallback for when hosted runners are starved and no self-hosted runner is attached.
The commands come from the workflow file itself: every single-line ``uv`` step of the
check's job that a self-hosted runner would run, in order, with each matrix value of a
sharded job. So a local run cannot drift from what CI runs.

    uv run python scripts/run_local_ci.py lint
    uv run python scripts/run_local_ci.py -r test-macos

``-r/--report`` posts the result on ``HEAD`` as a commit status named after the check,
through the GitHub CLI. The ruleset names its required contexts with no required
source, so that status satisfies the check. It refuses to report from a tree with
uncommitted changes, or from an operating system the check does not run on.
docs/procedures/github-actions-status.md describes the whole fallback route.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Check:
    workflow: str
    job: str
    platforms: tuple[str, ...]
    tools: tuple[str, ...] = ()


# `lint` is a Linux job in CI, but nothing it runs depends on the operating system, so a
# macOS host may report it too. Each test check reports only from its own system.
CHECKS = {
    "lint": Check("ci.yml", "lint", ("linux", "darwin")),
    "test": Check("ci.yml", "test", ("linux",), ("tmux", "dolt")),
    "test-macos": Check("ci-macos.yml", "test-macos-shard", ("darwin",), ("tmux", "dolt")),
    "test-windows": Check("ci-windows.yml", "test-windows-shard", ("win32",), ("tmux", "dolt")),
}

MATRIX_SHARD = "${{ matrix.shard }}"
WRAPPER_PREFIX = "${CI_TEST_WRAPPER:-} "


class Refusal(Exception):
    """A reason to run nothing."""


def _runs_self_hosted(step: dict[str, Any]) -> bool:
    condition = str(step.get("if", "")).strip()
    if not condition:
        return True
    return condition == "runner.environment == 'self-hosted'"


def _is_check_command(run: str) -> bool:
    command = run.strip()
    if "\n" in command:
        return False
    command = command.removeprefix(WRAPPER_PREFIX)
    return command.split(" ", 1)[0] == "uv"


def commands(check: Check, root: Path = ROOT) -> list[str]:
    """Every command the check's job runs, one entry per matrix value for a sharded job."""
    workflow = yaml.safe_load((root / ".github" / "workflows" / check.workflow).read_text(encoding="utf-8"))
    job = workflow["jobs"][check.job]
    steps = [step["run"].strip() for step in job["steps"] if "run" in step and _runs_self_hosted(step)]
    steps = [run for run in steps if _is_check_command(run)]
    shards = job.get("strategy", {}).get("matrix", {}).get("shard")
    if shards is None:
        found = steps
    else:
        setup = [run for run in steps if MATRIX_SHARD not in run]
        sharded = [run for run in steps if MATRIX_SHARD in run]
        found = setup + [run.replace(MATRIX_SHARD, str(shard)) for shard in shards for run in sharded]
    leftover = [run for run in found if "${{" in run]
    if leftover:
        raise Refusal(f"{check.workflow}:{check.job}: a step uses an expression this script cannot fill: {leftover[0]}")
    if not found:
        raise Refusal(f"{check.workflow}:{check.job}: no command found")
    return found


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def _preflight(name: str, check: Check, report: bool, root: Path) -> str | None:
    if sys.platform not in check.platforms:
        raise Refusal(f"{name} runs on {', '.join(check.platforms)}; this machine is {sys.platform}")
    if sys.platform == "win32" and not shutil.which("bash"):
        raise Refusal(f"{name} runs its steps in an MSYS2 shell; start this script from one")
    missing = [tool for tool in check.tools if shutil.which(tool) is None]
    if missing:
        raise Refusal(f"{name} needs {', '.join(missing)} on PATH, as its CI job installs")
    if not report:
        return None
    if shutil.which("gh") is None:
        raise Refusal("--report posts through the GitHub CLI, and gh is not on PATH")
    if _git(root, "status", "--porcelain"):
        raise Refusal("--report needs a clean tree, so the status describes the commit that ran")
    return _git(root, "rev-parse", "HEAD")


def _post_status(name: str, sha: str, passed: bool, root: Path) -> int:
    state = "success" if passed else "failure"
    description = f"Local run on {sys.platform} by scripts/run_local_ci.py"
    result = subprocess.run(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{{owner}}/{{repo}}/statuses/{sha}",
            "-f",
            f"state={state}",
            "-f",
            f"context={name}",
            "-f",
            f"description={description}",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"run_local_ci: posting the {name} status failed: {result.stderr.strip()}", file=sys.stderr)
        return 2
    print(f"run_local_ci: reported {name}={state} on {sha}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=sorted(CHECKS), help="the required check to run")
    parser.add_argument("-n", "--dry-run", action="store_true", help="print the commands and run nothing")
    parser.add_argument("-r", "--report", action="store_true", help="post the result on HEAD as a commit status")
    args = parser.parse_args(argv)
    check = CHECKS[args.check]

    try:
        planned = commands(check)
        if args.dry_run:
            print("\n".join(planned))
            return 0
        sha = _preflight(args.check, check, args.report, ROOT)
    except Refusal as refusal:
        print(f"run_local_ci: {refusal}", file=sys.stderr)
        return 2

    passed = True
    for command in planned:
        print(f"run_local_ci: $ {command}", flush=True)
        if subprocess.run(["bash", "-c", command], cwd=ROOT, check=False).returncode != 0:
            passed = False
            break
    print(f"run_local_ci: {args.check} {'passed' if passed else 'failed'}")

    if sha is not None:
        if _git(ROOT, "rev-parse", "HEAD") != sha:
            print("run_local_ci: HEAD moved during the run; reporting nothing", file=sys.stderr)
            return 2
        posted = _post_status(args.check, sha, passed, ROOT)
        if posted:
            return posted
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
