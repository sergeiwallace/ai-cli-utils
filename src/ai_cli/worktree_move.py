"""Move a linked git worktree, including one that holds initialised submodules.

``git worktree move`` refuses a worktree containing submodules ("working trees
containing submodules cannot be moved or removed"). Git's documented alternative is to
move the directory and run ``git worktree repair``. That alone leaves every submodule
broken, though: each submodule's git dir lives under the worktree's admin directory and
records ``core.worktree`` as a path RELATIVE to itself that names the old worktree
directory, so ``git status`` in the moved worktree fails on the first submodule.

:func:`move_worktree` therefore tries ``git worktree move`` first and falls back only on
that one refusal: rename the directory, ``git worktree repair`` it, then repoint each
submodule's ``core.worktree`` at its new location. A locked worktree is never moved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .git_repair import _git_env

_SUBMODULE_REFUSAL = "containing submodules"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=_git_env(), check=False)


def _locked(repo: Path, worktree: Path) -> bool:
    listed = _git(repo, "worktree", "list", "--porcelain").stdout or ""
    current: Path | None = None
    for line in listed.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :]).resolve()
        elif line.startswith("locked") and current == worktree.resolve():
            return True
    return False


def _repoint_submodules(repo: Path, admin: Path, old: Path, new: Path) -> list[str]:
    """Rewrite each submodule's ``core.worktree`` under ``admin/modules`` from ``old`` to ``new``.

    Read and written with ``--file`` from ``repo``, never from inside the submodule's git
    dir: git run there follows the very ``core.worktree`` being repaired and dies on it.
    """
    errors: list[str] = []
    modules = admin / "modules"
    if not modules.is_dir():
        return errors
    for config in sorted(modules.rglob("config")):
        gitdir = config.parent
        value = _git(repo, "config", "--file", str(config), "core.worktree").stdout.strip()
        if not value:
            continue
        target = (gitdir / value).resolve(strict=False)
        try:
            relative = target.relative_to(old)
        except ValueError:
            continue
        fixed = os.path.relpath(new / relative, gitdir)
        res = _git(repo, "config", "--file", str(config), "core.worktree", fixed)
        if res.returncode != 0:
            errors.append(f"{config}: {(res.stderr or '').strip()}")
    return errors


def move_worktree(repo: Path, source: Path, dest: Path) -> tuple[bool, str]:
    """Move worktree ``source`` to ``dest``. Returns ``(moved, git's stderr or our reason)``."""
    moved = _git(repo, "worktree", "move", str(source), str(dest))
    if moved.returncode == 0:
        return True, ""
    stderr = (moved.stderr or "").strip()
    if _SUBMODULE_REFUSAL not in stderr:
        return False, stderr
    if _locked(repo, source):
        return False, f"{stderr}; and it is locked, so it is not moved by hand either"
    old, new = source.resolve(), dest.resolve(strict=False)
    # The rename and the repair are two writes: if the process dies between them, the
    # directory is at its new path with a stale registration, and the remedy is the
    # same `git worktree repair <dest>` this function runs next.
    old.rename(new)
    repaired = _git(repo, "worktree", "repair", str(new))
    if repaired.returncode != 0:
        return False, (
            f"moved {old} to {new} by hand (git refuses worktrees with submodules), but "
            f"`git worktree repair {new}` failed: {(repaired.stderr or '').strip()}"
        )
    admin = _git(new, "rev-parse", "--absolute-git-dir").stdout.strip()
    errors = _repoint_submodules(repo, Path(admin), old, new) if admin else ["could not resolve the worktree's git dir"]
    if errors:
        return False, f"moved {old} to {new} and repaired it, but a submodule was not repointed: {'; '.join(errors)}"
    return True, ""
