"""Shared git-subprocess safety helpers (AI-CLI-99).

Prevents the recurring ``core.bare=true`` / stale ``core.worktree`` corruption
class on a repo's main working tree. Root cause: worktree tooling runs ``git``
subprocesses that INHERIT the parent process's environment. When the parent is
itself running inside a git worktree (e.g. a nested CC session launched from
``.worktrees/sw-N``), git context env vars (``GIT_DIR``, ``GIT_WORK_TREE``, ...)
leak into ``git worktree add/remove`` subprocess calls. Because many
sessions/worktrees share one gitdir, this can write ``core.bare``/
``core.worktree`` onto the SHARED main-repo config, corrupting every session
operating from the main tree path (``fatal: this operation must be run in a
work tree``).

Two layers of defense:

1. ``_git_env()`` — strip git-targeting env vars before every git subprocess
   call that touches repo/worktree structure, so it always targets the repo
   passed via ``-C``/``cwd``, never one redirected by an inherited
   ``GIT_DIR``/``GIT_WORK_TREE``.
2. ``repair_bare_worktree_config()`` — a deterministic backstop: assert +
   repair a normal working tree's ``core.bare``/``core.worktree`` regardless
   of source. This covers corruption paths we don't control (e.g. Claude
   Code's own ``isolation: worktree`` sub-agent tool).

A third, detection-only layer (AIH-443) covers two distinct "phantom deletion"
signatures found across six worktrees in three repos, both silent — ``git
status`` reports a tracked path as deleted, with no error anywhere:

3. ``detect_stranded_autostash()`` — ``git pull --rebase --autostash`` can
   return exit code ``0`` even when the automatic stash pop conflicted
   (reproduced directly: a same-line local/remote edit conflict leaves
   ``UU`` markers and a stash entry behind, while the wrapping ``git pull``
   still reports success). The launcher's existing autostash-conflict guard
   (``if pull.returncode != 0`` in ``main.py``) cannot see this — it never
   fires, because the return code lies. This finds the leftover stash so a
   caller can warn instead of silently trusting the return code.
4. ``detect_missing_tracked_symlinks()`` — a git-tracked symlink (mode
   ``120000``) that HEAD lists but that is absent from the working tree
   (``lstat`` fails). Confirmed root cause of AIH-443 Shape A: a Claude Code
   ``isolation: worktree`` sub-agent checkout dropped 21 tracked symlinks
   (verified via ``git ls-tree`` mode + target-resolution testing) while every
   regular file checked out correctly, with no error surfaced anywhere.
"""

import os
import subprocess
import sys
from pathlib import Path

# Vars that redirect git's repo/worktree targeting. Stripping these prevents a
# subprocess from silently operating on a different repo/worktree than the one
# named via -C/cwd. Innocuous GIT_* vars (SSH, prompts, author/committer
# identity) are deliberately NOT in this list and stay untouched.
_GIT_TARGETING_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
)


def _git_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict safe to pass as ``env=`` to a ``git`` subprocess.

    Strips vars that redirect git's repo/worktree targeting (``GIT_DIR``,
    ``GIT_WORK_TREE``, etc.) so the subprocess always operates on the repo
    given via ``-C``/``cwd``, never one inherited from the caller's own git
    context. Keeps other ``GIT_*`` vars (``GIT_SSH*``, ``GIT_TERMINAL_PROMPT``,
    ``GIT_AUTHOR_*``, ``GIT_COMMITTER_*``, ...) untouched.
    """
    env = dict(base_env if base_env is not None else os.environ)
    for var in _GIT_TARGETING_VARS:
        env.pop(var, None)
    return env


def repair_bare_worktree_config(repo_root: Path) -> bool:
    """Assert + repair a normal working tree's ``core.bare``/``core.worktree``.

    ``repo_root`` must be a NORMAL git working tree (never intentionally
    bare). If ``core.bare`` has been flipped to ``true``, or a stale
    ``core.worktree`` is set, this repairs both and returns ``True`` (logging
    a visible warning so recurrence is observable). A healthy repo is a no-op
    returning ``False``.
    """
    repaired = False

    bare = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--get", "core.bare"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if bare.returncode == 0 and bare.stdout.strip() == "true":
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "core.bare", "false"],
            capture_output=True,
            env=_git_env(),
        )
        print(
            f"WARNING: repaired core.bare=true corruption on {repo_root} (reset to false)",
            file=sys.stderr,
        )
        repaired = True

    worktree_cfg = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--get", "core.worktree"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if worktree_cfg.returncode == 0 and worktree_cfg.stdout.strip():
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "--unset", "core.worktree"],
            capture_output=True,
            env=_git_env(),
        )
        print(
            f"WARNING: repaired stale core.worktree={worktree_cfg.stdout.strip()!r} on {repo_root} (unset)",
            file=sys.stderr,
        )
        repaired = True

    return repaired


def detect_stranded_autostash(repo_root: Path) -> str | None:
    """Return the top ``git stash list`` entry if it looks like a leftover
    ``--autostash``, else ``None``.

    An autostash is meant to be transient: created before a rebase, popped
    (and dropped) after. If the pop conflicts, git can leave the entry behind
    and print a warning to stderr WITHOUT the wrapping ``git pull --rebase
    --autostash`` command's exit code reflecting the failure (reproduced
    directly — see module docstring). Any stash entry present immediately
    after that call in this launcher's flow is presumptively this leftover:
    no other step in this flow creates one.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "stash", "list"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    if not lines:
        return None
    return lines[0].strip()


def detect_missing_tracked_symlinks(repo_root: Path) -> list[str]:
    """Return tracked symlink paths (mode ``120000`` in HEAD) absent from disk.

    A normal checkout always materializes every tracked path, symlink or not.
    A path that HEAD lists as a symlink but that fails ``lstat`` on disk is
    exactly AIH-443 Shape A's signature: ``git status`` reports it deleted,
    ``git show HEAD:<path>`` and ``origin/main`` both still have it, and
    nothing on this fleet's side ever touched it (verified by full sub-agent
    transcript audit — the checkout itself never materialized these entries).
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "-r", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        return []
    missing: list[str] = []
    for line in result.stdout.splitlines():
        meta, _, path = line.partition("\t")
        if not path:
            continue
        mode = meta.split()[0] if meta.split() else ""
        if mode != "120000":
            continue
        if not os.path.lexists(repo_root / path):
            missing.append(path)
    return missing
