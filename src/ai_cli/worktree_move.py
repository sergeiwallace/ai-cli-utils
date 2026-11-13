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

A moved checkout keeps its ``__pycache__/*.pyc`` files, and each one's ``co_filename`` names
the old directory, so anything reading source through a code object finds nothing.
:func:`purge_moved_bytecode` removes those caches after every successful move.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from .git_repair import _git_env

_SUBMODULE_REFUSAL = "containing submodules"
_BYTECODE_SUFFIXES = (".pyc", ".pyo")


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


def _tracked_bytecode(checkout: Path) -> set[Path]:
    listed = _git(checkout, "ls-files", "-z", "--", ":(glob)**/__pycache__/**").stdout or ""
    return {checkout / name for name in listed.split("\0") if name}


def purge_moved_bytecode(checkout: Path) -> None:
    """Remove the stale ``__pycache__`` bytecode a ``git worktree move`` carried into ``checkout``.

    Only ``*.pyc``/``*.pyo`` files inside a ``__pycache__`` directory go, then that directory if
    it is left empty. Skipped: any virtual environment (``.venv`` or a directory holding
    ``pyvenv.cfg``), any nested git checkout (a submodule), every file git tracks, and every
    symlink, which is never followed out of the checkout. Python rebuilds the caches on import.
    """
    tracked = _tracked_bytecode(checkout)
    for root, dirs, _files in os.walk(checkout):
        here = Path(root)
        if here != checkout and ((here / "pyvenv.cfg").exists() or (here / ".git").exists()):
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if d not in (".git", ".venv") and not (here / d).is_symlink()]
        if here.name != "__pycache__":
            continue
        for entry in here.iterdir():
            if (
                entry.suffix in _BYTECODE_SUFFIXES
                and not entry.is_symlink()
                and entry.is_file()
                and entry not in tracked
            ):
                entry.unlink(missing_ok=True)
        with contextlib.suppress(OSError):  # still holds a tracked or unrecognised file
            here.rmdir()


def move_worktree(repo: Path, source: Path, dest: Path) -> tuple[bool, str]:
    """Move worktree ``source`` to ``dest``. Returns ``(moved, git's stderr or our reason)``."""
    moved = _git(repo, "worktree", "move", str(source), str(dest))
    if moved.returncode == 0:
        purge_moved_bytecode(dest)
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
    purge_moved_bytecode(new)
    return True, ""
