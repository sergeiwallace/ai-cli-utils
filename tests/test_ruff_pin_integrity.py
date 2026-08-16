"""Regression tests for the ruff pin/installed-version skew (BUG-006).

Two independent defects are pinned here:

1. The repo declared ``ruff==0.16.0`` while the project venv had 0.15.11
   installed, so the documented gate (``ruff check src/ tests/``) reported
   "All checks passed!" from the stale binary while the pinned binary reported
   1075 findings. ``check_installed_version`` closes that hole.

2. ``[tool.ruff.lint]`` declared only ``ignore``, never ``select``, so the
   repo inherited whatever ruff's *default* rule set happened to be. 0.16.0
   changed that default from 59 rules to 413, silently redefining the gate.
   The declared-select tests pin the rule set to the repo's own config.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_ruff_version_sync import PYPROJECT_PIN_RE, check_installed_version

REPO_ROOT = Path(__file__).resolve().parent.parent

PRECOMMIT_TEMPLATE = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v{rev}
    hooks:
      - id: ruff-check
"""


def _write_repo(root: Path, rev: str, pin: str) -> None:
    (root / ".pre-commit-config.yaml").write_text(PRECOMMIT_TEMPLATE.format(rev=rev))
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["ruff=={pin}"]\n'
    )


def _write_fake_ruff(path: Path, version: str) -> None:
    """Install a stub `ruff` that reports `version`, so the guard's real
    subprocess boundary is exercised without downloading a second ruff."""
    if sys.platform == "win32":
        path.write_text(f"@echo off\r\necho ruff {version}\r\n")
    else:
        path.write_text(f'#!/usr/bin/env python3\nprint("ruff {version}")\n')
        path.chmod(0o755)


def _fake_ruff_path(root: Path) -> Path:
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "ruff.cmd"
    return root / ".venv" / "bin" / "ruff"


def _add_worktree(main_tree: Path, worktree: Path) -> Path:
    """Attach a linked worktree to `main_tree`, committing an empty root first.

    Identity is set on the fixture repo rather than relied on from the ambient
    environment: a bare `git commit` fails outright on a machine with no
    configured `user.email`, which would make these tests fail for a setup
    reason instead of the behaviour they assert.
    """
    subprocess.run(["git", "-C", str(main_tree), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(main_tree), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(main_tree), "add", "pyproject.toml", ".pre-commit-config.yaml"],
        check=True,
    )
    subprocess.run(["git", "-C", str(main_tree), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(
        ["git", "-C", str(main_tree), "worktree", "add", "-q", "-b", "wt", str(worktree)],
        check=True,
    )
    return worktree


# --- check_installed_version: the version-skew guard ------------------------


def test_given_installed_ruff_matches_pin_when_checked_then_no_errors(tmp_path):
    _write_repo(tmp_path, "0.16.0", "0.16.0")

    assert check_installed_version(tmp_path, installed="0.16.0") == []


def test_given_installed_ruff_older_than_pin_when_checked_then_reports_skew(tmp_path):
    _write_repo(tmp_path, "0.16.0", "0.16.0")

    errors = check_installed_version(tmp_path, installed="0.15.11")

    assert len(errors) == 1
    assert "0.16.0" in errors[0]
    assert "0.15.11" in errors[0]


def test_given_installed_ruff_newer_than_pin_when_checked_then_reports_skew(tmp_path):
    _write_repo(tmp_path, "0.16.0", "0.16.0")

    errors = check_installed_version(tmp_path, installed="0.17.0")

    assert len(errors) == 1
    assert "0.17.0" in errors[0]


def test_given_no_ruff_installed_when_checked_then_reports_missing(tmp_path):
    _write_repo(tmp_path, "0.16.0", "0.16.0")

    errors = check_installed_version(tmp_path, installed=None)

    assert len(errors) == 1
    assert "not installed" in errors[0]


def test_given_pyproject_without_a_pin_when_installed_checked_then_no_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = []\n')

    assert check_installed_version(tmp_path, installed="0.15.11") == []


def test_given_a_linked_worktree_with_no_local_venv_when_checked_then_uses_the_main_trees_venv(tmp_path):
    """A linked worktree carries no `.venv` of its own.

    If the guard only looked at `root/.venv` it would report "not installed" for
    every commit made from a worktree -- degrading to inconclusive in the
    ordinary case, which is what teaches contributors to bypass a hook.
    """
    main_tree = tmp_path / "main"
    main_tree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main_tree)], check=True)
    _write_repo(main_tree, "0.16.0", "0.16.0")
    fake_ruff = _fake_ruff_path(main_tree)
    fake_ruff.parent.mkdir(parents=True)
    _write_fake_ruff(fake_ruff, "0.16.0")
    worktree = _add_worktree(main_tree, tmp_path / "wt")
    assert not (worktree / ".venv").exists()

    assert check_installed_version(worktree) == []


def test_given_a_linked_worktree_whose_main_venv_is_stale_when_checked_then_still_reports_skew(tmp_path):
    """The worktree fallback must not turn a real mismatch into a pass."""
    main_tree = tmp_path / "main"
    main_tree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main_tree)], check=True)
    _write_repo(main_tree, "0.16.0", "0.16.0")
    fake_ruff = _fake_ruff_path(main_tree)
    fake_ruff.parent.mkdir(parents=True)
    _write_fake_ruff(fake_ruff, "0.15.11")
    worktree = _add_worktree(main_tree, tmp_path / "wt")

    errors = check_installed_version(worktree)

    assert len(errors) == 1
    assert "0.15.11" in errors[0]


def test_given_this_repo_when_installed_version_checked_then_matches_the_pin():
    """The live venv this suite runs under must carry the pinned ruff.

    This is the assertion the original defect would have failed: it resolves
    ruff through the running interpreter, not through PATH, so it cannot be
    satisfied by some other ruff that happens to be installed elsewhere.
    """
    assert check_installed_version(REPO_ROOT) == []


# --- declared select: the "default ruleset moved under us" guard ------------


def _declared_lint_config() -> dict:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return pyproject["tool"]["ruff"]["lint"]


def test_given_repo_ruff_config_when_read_then_select_is_declared_explicitly():
    """Without an explicit ``select`` the gate's rule set is ruff's default,
    which a patch-level upgrade is free to change (0.15 -> 0.16 took it from
    59 rules to 413)."""
    assert "select" in _declared_lint_config()


def test_given_declared_ignore_codes_when_compared_to_select_then_each_is_covered():
    """An ``ignore`` entry for a rule outside ``select`` is dead config that
    silently stops documenting anything."""
    lint = _declared_lint_config()
    prefixes = tuple(lint["select"])

    uncovered = [code for code in lint["ignore"] if not code.startswith(prefixes)]

    assert uncovered == []


def test_given_the_gate_runs_when_the_binary_is_asked_its_version_then_it_reports_the_pin():
    """Measure the version at gate time, from the binary that runs the gate.

    The failure this pins is not "the wrong version is installed" but "someone
    measured, and the environment changed underneath the conclusion" -- which
    happened twice while diagnosing BUG-006. A version read at test time from
    the same interpreter that runs the check cannot go stale between the reading
    and the verdict; a number recorded in a report can.
    """
    pin = PYPROJECT_PIN_RE.search((REPO_ROOT / "pyproject.toml").read_text())
    assert pin is not None, "pyproject.toml must carry an exact ruff==X.Y.Z pin"

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["ruff", pin.group(1)], (
        f"gate binary reports {result.stdout.strip()!r}, pin is {pin.group(1)!r}"
    )


@pytest.mark.parametrize("scope", ["src", "tests"])
def test_given_repo_sources_when_pinned_ruff_runs_the_gate_then_it_passes(scope):
    """The documented hard gate must actually pass under the *pinned* binary.

    Runs ruff as a module of the interpreter running this test, so the version
    measured is the one the venv provides -- the exact conflation that let the
    original defect through.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", scope, "--output-format", "concise"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


# --- report-only lint hook: the "widening select mass-autofixes" guard --------


def _precommit_hook_args(hook_id: str) -> list[str]:
    """Return the ``args`` the repo's pre-commit config passes to ``hook_id``.

    Parsed as text rather than with a YAML loader so the guard has no dependency
    the hook itself does not already have.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    lines = config.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"- id: {hook_id}":
            continue
        args: list[str] = []
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if stripped.startswith(("- id:", "- repo:")):
                break
            if stripped.startswith("args:"):
                args = stripped.removeprefix("args:").strip().strip("[]").split(",")
        return [arg.strip() for arg in args if arg.strip()]
    raise AssertionError(f"no {hook_id!r} hook in .pre-commit-config.yaml")


def test_given_the_lint_hook_when_its_args_are_read_then_it_does_not_autofix():
    """``ruff-check`` must report, never rewrite.

    Lint autofix is rule-set-scoped, and the hook only ever sees the files one
    commit happens to touch. So with ``--fix``, widening ``select`` does not
    surface the new family as a reviewable list -- it rewrites those files
    piecemeal, inside unrelated commits, unreviewed. ``--fix`` does not prevent
    the mass autofix, it only removes the review.
    """
    assert "--fix" not in _precommit_hook_args("ruff-check")


def test_given_the_format_hook_when_its_args_are_read_then_autofix_is_still_allowed():
    """The guard above must not be read as "no hook may ever rewrite".

    ``ruff-format`` legitimately rewrites: formatting is not rule-set-scoped, so
    widening ``select`` cannot change what it does. Asserting that here keeps the
    distinction explicit, so a later cleanup does not "consistently" strip the
    formatter's rewrite too.
    """
    assert _precommit_hook_args("ruff-format") == []


def test_given_a_widened_select_when_the_lint_hook_runs_then_the_file_is_not_rewritten(tmp_path):
    """The end-to-end property, on a file that genuinely carries findings.

    This is the probe that distinguishes the two configurations: on a clean tree
    both look identical, so a clean-tree check proves nothing. The fixture module
    is deliberately import-unsorted (an ``I001`` finding, autofixable), then
    edited in an unrelated place -- the exact scenario where ``--fix`` would
    silently rewrite the imports as a side effect of committing the edit.
    """
    module = tmp_path / "sample_module.py"
    module.write_text('"""Fixture."""\n\nimport sys\n\nimport os\n\nVALUE = (os, sys)\n')
    before = module.read_text()

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "I", "--output-format", "concise", str(module)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, "fixture must carry a finding, or this proves nothing"
    assert "I001" in result.stdout
    assert module.read_text() == before, "ruff check without --fix must not rewrite the file"


def test_given_the_same_file_when_ruff_is_asked_to_fix_then_it_does_rewrite(tmp_path):
    """Negative control for the test above.

    Without this, a passing "not rewritten" assertion could just mean the fixture
    had nothing to fix, or that the invocation silently did nothing. Showing the
    same file IS rewritten under ``--fix`` is what makes the other probe able to
    come out the other way.
    """
    module = tmp_path / "sample_module.py"
    module.write_text('"""Fixture."""\n\nimport sys\n\nimport os\n\nVALUE = (os, sys)\n')
    before = module.read_text()

    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "I", "--fix", str(module)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert module.read_text() != before, "--fix must rewrite, or the guard above is vacuous"
