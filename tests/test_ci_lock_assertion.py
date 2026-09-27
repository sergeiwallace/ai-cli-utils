"""Standing guard that every CI dependency sync asserts the lock is current.

``uv sync`` without ``--locked`` RE-RESOLVES silently whenever ``pyproject.toml``
and ``uv.lock`` disagree. Nothing in the log says so, so a pull request goes green
having installed versions that are not the ones it ships, and the lock it merges is
stale (AI-CLI-qqnx). ``--locked`` refuses instead.

Measured on one deliberately staled lock (a dependency floor raised in
``pyproject.toml`` only), same sandbox, same command but for the flag:

- ``uv sync --frozen --dev`` exited 0 and installed the stale versions.
- ``uv sync --locked --dev`` exited 1 with
  ``error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.``
  followed by ``hint: To update the lockfile, run `uv lock`.``

That is why ``--frozen`` does NOT satisfy this guard and is asserted not to. Both
flags stop the re-resolve, but ``--frozen`` installs from the lock without ever
comparing it to ``pyproject.toml`` -- it makes the stale lock quiet rather than
loud, which is the defect, not the fix. Only ``--locked`` reports the disagreement.

The flag is one word in a workflow file, so it is exactly the kind of thing a later
edit drops while rewriting a step for an unrelated reason. This guard is what makes
that a test failure instead of a silent return to re-resolving.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Reused rather than reimplemented: this repo already ships a walker that collects
# every `run:` body from a parsed workflow, and a second hand-rolled one would be
# free to miss a shape the shipped one handles. A `run:` key can appear at a depth
# this file has no reason to predict, and a scanner that only read
# `jobs.*.steps[*].run` would report a clean repository while missing the step that
# dropped the flag.
from check_python_version_sync import _walk_run_steps

# Matches one `uv sync` invocation and captures its arguments up to the end of the
# command. A `run: |` block holds several commands, so the argument run stops at a
# shell separator -- without that bound, a `--locked` belonging to a LATER command
# in the same block would satisfy the check for an earlier one that lacks it.
_UV_SYNC_RE = re.compile(r"\buv\s+sync\b([^\n;&|]*)")

_REQUIRED_FLAG = "--locked"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def scan_workflows_for_unlocked_sync(root: Path) -> list[str]:
    """Return one ``file: command`` finding per CI sync step that may re-resolve."""
    findings: list[str] = []
    workflow_dir = root / ".github" / "workflows"
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for script in _walk_run_steps(data):
            findings.extend(
                f"{path.name}: uv sync{match.group(1).rstrip()}"
                for match in _UV_SYNC_RE.finditer(script)
                if _REQUIRED_FLAG not in match.group(1).split()
            )
    return findings


def test_given_the_ci_workflows_when_scanned_then_every_uv_sync_asserts_the_lock() -> None:
    findings = scan_workflows_for_unlocked_sync(_repo_root())
    assert not findings, "CI sync step may silently re-resolve a stale lock -- add `--locked`:\n" + "\n".join(findings)


def test_given_the_ci_workflows_when_scanned_then_at_least_one_sync_step_is_covered() -> None:
    """Guards the guard: a scanner that finds nothing to check cannot fail usefully.

    Without this, renaming the workflow directory or breaking the walker would turn
    the assertion above into a permanent pass -- the failure mode that makes a green
    suite mean less than it appears to.
    """
    scripts = [
        script
        for path in (_repo_root() / ".github" / "workflows").glob("*.yml")
        for script in _walk_run_steps(yaml.safe_load(path.read_text(encoding="utf-8")))
    ]
    syncs = [match.group(0) for script in scripts for match in _UV_SYNC_RE.finditer(script)]

    assert len(syncs) >= 4, f"expected the four CI sync steps, found {len(syncs)}: {syncs}"


def _write_workflow(root: Path, body: str) -> Path:
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    path = workflow_dir / "ci.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_given_a_workflow_syncing_without_the_flag_when_scanned_then_it_is_flagged(tmp_path: Path) -> None:
    """Positive control, in the shape the workflow had before this guard existed."""
    _write_workflow(
        tmp_path,
        "jobs:\n  test:\n    steps:\n      - run: uv sync --dev\n",
    )

    findings = scan_workflows_for_unlocked_sync(tmp_path)

    assert findings == ["ci.yml: uv sync --dev"]


def test_given_a_workflow_syncing_with_frozen_when_scanned_then_it_is_still_flagged(tmp_path: Path) -> None:
    """``--frozen`` stops the re-resolve without checking the lock, so it is not enough.

    Measured on a staled lock: ``--frozen`` exited 0 and installed the stale
    versions, where ``--locked`` exited 1 and named ``uv lock`` as the remedy. A
    guard that accepted either flag would pass the quieter of the two defects.
    """
    _write_workflow(
        tmp_path,
        "jobs:\n  test:\n    steps:\n      - run: uv sync --frozen --dev\n",
    )

    findings = scan_workflows_for_unlocked_sync(tmp_path)

    assert findings == ["ci.yml: uv sync --frozen --dev"]


def test_given_a_multi_command_run_block_when_scanned_then_the_unflagged_sync_is_found(tmp_path: Path) -> None:
    """Control for the bound on the captured argument run.

    Two commands in one ``run: |`` block, the second correctly flagged and the
    first not. A scanner that captured to the end of the block would see the later
    ``--locked`` and clear the earlier command that lacks it.
    """
    _write_workflow(
        tmp_path,
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - run: |\n"
        "          uv sync --dev\n"
        "          uv sync --locked --dev --python 3.14\n",
    )

    findings = scan_workflows_for_unlocked_sync(tmp_path)

    assert findings == ["ci.yml: uv sync --dev"]


def test_given_a_run_step_nested_below_the_steps_list_when_scanned_then_it_is_still_found(tmp_path: Path) -> None:
    """A ``run:`` reached through a shape this file does not predict is still scanned.

    The reason the shipped walker is reused rather than a local
    ``jobs.*.steps[*].run`` lookup: a composite action body, or any future nesting,
    would otherwise drop out of the scan silently.
    """
    _write_workflow(
        tmp_path,
        "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: uv sync --dev\n",
    )

    findings = scan_workflows_for_unlocked_sync(tmp_path)

    assert findings == ["ci.yml: uv sync --dev"]


@pytest.mark.parametrize(
    "command",
    [
        "uv sync --locked --dev",
        "uv sync --locked --dev --python 3.14",
        "uv sync --dev --locked",
    ],
)
def test_given_a_sync_carrying_the_flag_when_scanned_then_it_is_not_flagged(tmp_path: Path, command: str) -> None:
    """Negative control, including flag order, so the guard cannot pass by rejecting everything."""
    _write_workflow(tmp_path, f"jobs:\n  test:\n    steps:\n      - run: {command}\n")

    assert scan_workflows_for_unlocked_sync(tmp_path) == []
