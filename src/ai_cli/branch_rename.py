"""Rename a local branch with a durable anchor at its tip first.

``git branch -m`` deletes the old ref and creates the new one. A rename interrupted
between the two, or refused by a ``reference-transaction`` hook that judges the
deletion of an unintegrated branch, must never leave the tip unreachable. So the tip
is first written to ``refs/preserved/session-migrate/<old branch>`` and only then is
the branch renamed. The anchor stays after a successful rename: it is what lets a
ref-deletion guard prove the old tip is contained, and it costs one small ref.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .git_repair import _git_env

ANCHOR_NAMESPACE = "refs/preserved/session-migrate"


@dataclass
class BranchRename:
    """The outcome (or, under ``dry_run``, the plan) of one anchored rename."""

    old: str
    new: str
    anchor: str
    tip: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(), check=False)


def _resolve(repo: Path, ref: str) -> str | None:
    res = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None


def anchor_ref(branch: str) -> str:
    return f"{ANCHOR_NAMESPACE}/{branch}"


def rename_branch_anchored(repo_root: Path, old: str, new: str, *, dry_run: bool = False) -> BranchRename:
    """Anchor ``old``'s tip under :data:`ANCHOR_NAMESPACE`, then ``git branch -m old new``.

    Never raises for a git refusal: the returned :class:`BranchRename` carries the
    reason in ``error``, quoting git's stderr. The rename is not attempted unless
    the anchor is in place, and an existing anchor pointing at a different commit
    is never overwritten (it preserves some earlier tip).
    """
    result = BranchRename(old=old, new=new, anchor=anchor_ref(old))
    tip = _resolve(repo_root, f"refs/heads/{old}")
    if tip is None:
        result.error = f"branch {old} does not exist"
        return result
    result.tip = tip
    if _resolve(repo_root, f"refs/heads/{new}") is not None:
        result.error = f"branch {new} already exists"
        return result
    existing = _resolve(repo_root, result.anchor)
    if existing is not None and existing != tip:
        result.error = f"anchor {result.anchor} already points at {existing}, not {old}'s tip {tip}"
        return result
    if dry_run:
        return result

    if existing is None:
        # The empty old value makes the write fail if the ref appeared meanwhile.
        written = _git(repo_root, "update-ref", "-m", f"session-migrate: anchor {old}", result.anchor, tip, "")
        if written.returncode != 0:
            result.error = f"could not write anchor {result.anchor}: {(written.stderr or '').strip()}"
            return result
    renamed = _git(repo_root, "branch", "-m", old, new)
    if renamed.returncode != 0:
        result.error = f"git branch -m {old} {new} was refused: {(renamed.stderr or '').strip()}"
    return result
