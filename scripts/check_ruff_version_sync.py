#!/usr/bin/env python3
"""Fail if pyproject.toml's ruff pin and .pre-commit-config.yaml's ruff rev
disagree (SW ruff-unification sweep, 2026-07-27).

The pre-commit hook (ruff-pre-commit) and a developer's `uv run ruff` are two
separate binaries provisioned from two separate pins. Nothing keeps them in
sync by default, so a repo can be green in the pre-commit gate and red at the
terminal (or vice versa) with no warning. This script is wired in as a local
pre-commit hook so the disagreement is caught at commit time, not discovered
months later by a confused agent.

A floor (`ruff>=X`) in pyproject.toml is also treated as a failure even if the
resolved lockfile version happens to match today: a floor lets `uv lock
--upgrade` (or a fresh clone with no lockfile) silently drift past the
pre-commit pin. Only an exact `ruff==X.Y.Z` pin is accepted.

Agreeing pins are necessary but not sufficient (BUG-006): the version actually
*installed* in the project venv is a third, independent value, and a pin bump
does not touch an already-provisioned venv. A venv one minor behind reported
"All checks passed!" for a tree the pinned binary found 1075 errors in, so
`check_installed_version` compares the pin against the venv's own ruff.
"""

from __future__ import annotations

import re
import subprocess
import sys
import sysconfig
from pathlib import Path

PRECOMMIT_REV_RE = re.compile(
    r"repo:\s*https://github\.com/astral-sh/ruff-pre-commit\s*\n\s*rev:\s*v([0-9][0-9A-Za-z.]*)"
)
PYPROJECT_PIN_RE = re.compile(r'"ruff==([0-9][0-9A-Za-z.]*)"')
PYPROJECT_FLOOR_RE = re.compile(r'"ruff(>=|>|~=|<=|<|!=)[0-9]')

_UNSET = "<unset>"


def check(root: Path) -> list[str]:
    """Return a list of error messages; empty means everything is in sync."""
    precommit_path = root / ".pre-commit-config.yaml"
    pyproject_path = root / "pyproject.toml"

    if not precommit_path.exists() or not pyproject_path.exists():
        return []

    precommit_text = precommit_path.read_text()
    pyproject_text = pyproject_path.read_text()

    if "astral-sh/ruff-pre-commit" not in precommit_text:
        return []  # non-python tech stack, or ruff hook not present here

    errors: list[str] = []

    precommit_rev = PRECOMMIT_REV_RE.search(precommit_text)
    if precommit_rev is None:
        errors.append("ruff-version-sync: could not parse the ruff-pre-commit rev out of .pre-commit-config.yaml")
        return errors

    pyproject_pin = PYPROJECT_PIN_RE.search(pyproject_text)
    if pyproject_pin is None:
        if PYPROJECT_FLOOR_RE.search(pyproject_text):
            errors.append(
                "ruff-version-sync: pyproject.toml pins ruff with a floor "
                "(e.g. ruff>=X) instead of an exact ruff==X.Y.Z pin -- a floor "
                "lets `uv lock --upgrade` drift away from the pre-commit rev "
                f"(currently v{precommit_rev.group(1)})"
            )
        else:
            errors.append(
                "ruff-version-sync: pyproject.toml has no ruff dependency pin "
                f"to compare against the pre-commit rev (v{precommit_rev.group(1)})"
            )
        return errors

    if precommit_rev.group(1) != pyproject_pin.group(1):
        errors.append(
            f"ruff-version-sync: pre-commit pins ruff {precommit_rev.group(1)} "
            f"but pyproject.toml pins ruff {pyproject_pin.group(1)} -- these "
            "must be identical (the pre-commit hook and `uv run ruff` are "
            "different binaries; a mismatch means green-in-gate can be "
            "red-in-terminal, or vice versa)."
        )

    return errors


def _main_worktree(root: Path) -> Path | None:
    """Return the main working tree's root when `root` is a linked worktree.

    Linked worktrees do not carry their own `.venv`, so a guard that only looks
    at `root/.venv` reports "not installed" for every commit made from one --
    an inconclusive verdict in the ordinary case, which teaches contributors to
    bypass the hook. Mirrors `session.detect_repo_root`'s `--git-common-dir`
    approach.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    common = Path(out.stdout.strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    return common.parent


def _venv_ruff(root: Path) -> Path | None:
    """Locate the project venv's ruff executable.

    Deliberately does not use `shutil.which`: a pre-commit `language: system`
    hook runs under whatever `python3` is first on the committing shell's PATH
    (a conda interpreter with no ruff at all, on at least one supported
    workstation), so resolving through PATH measures the wrong environment --
    the exact conflation this guard exists to catch.

    `root`'s own environment is authoritative, then the main worktree's (a
    linked worktree has no `.venv`). The running interpreter is consulted only
    as a last resort, for a venv at a non-conventional path: preferring it
    would make the guard report on whatever environment happens to be running
    rather than on the repo being checked.
    """
    exe = "ruff" + (sysconfig.get_config_var("EXE") or "")
    executable_names = [exe]
    if sys.platform == "win32":
        # A venv normally provides ruff.exe, but command wrappers are also
        # executable from Windows shells and are common in lightweight setups.
        executable_names.append("ruff.cmd")
    roots = [root]
    main_tree = _main_worktree(root)
    if main_tree is not None and main_tree != root:
        roots.append(main_tree)

    candidates = [r / ".venv" / sub / name for r in roots for sub in ("bin", "Scripts") for name in executable_names]
    candidates.extend(Path(sysconfig.get_path("scripts")) / name for name in executable_names)

    return next((c for c in candidates if c.is_file()), None)


def _installed_ruff_version(root: Path) -> str | None:
    ruff = _venv_ruff(root)
    if ruff is None:
        return None
    try:
        out = subprocess.run([str(ruff), "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    match = re.search(r"([0-9][0-9A-Za-z.]*)", out.stdout)
    return match.group(1) if match else None


def check_installed_version(root: Path, installed: str | None = _UNSET) -> list[str]:
    """Return errors if the installed ruff disagrees with pyproject.toml's pin.

    `installed` is injectable so the mismatch branches are testable without
    provisioning a second venv; left unset, the real venv is measured.
    """
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        return []

    pyproject_pin = PYPROJECT_PIN_RE.search(pyproject_path.read_text())
    if pyproject_pin is None:
        return []  # `check()` already reports a missing/floor pin

    pin = pyproject_pin.group(1)
    if installed is _UNSET:
        installed = _installed_ruff_version(root)

    if installed is None:
        return [
            f"ruff-version-sync: pyproject.toml pins ruff {pin} but ruff is "
            "not installed in the project environment -- run `uv sync --dev`. "
            "Until then `ruff check` resolves to some other binary (or none), "
            "so the hard gate's verdict says nothing about this repo."
        ]

    if installed != pin:
        return [
            f"ruff-version-sync: pyproject.toml pins ruff {pin} but the "
            f"project environment has ruff {installed} installed -- run "
            "`uv sync --dev`. A stale venv makes the documented hard gate "
            "report a different verdict than the pinned pre-commit hook, so "
            "a green gate proves nothing about whether the commit survives."
        ]

    return []


def main() -> int:
    root = Path.cwd()
    errors = check(root) + check_installed_version(root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
