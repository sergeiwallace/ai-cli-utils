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

3. ``pull_rebase_autostash()`` — ``git pull --rebase --autostash`` returns exit
   code ``0`` even when the automatic stash pop conflicted. Measured directly
   on git 2.55.0 (macOS) and 2.43.0 (Linux): a same-line local/remote edit
   leaves three unmerged index stages, conflict markers on disk and the
   autostash still on the stack, with no rebase/merge marker file to signal an
   operation in progress — and the wrapping ``git pull`` still exits ``0``. A
   caller checking only ``returncode != 0`` therefore cannot see it. This
   wrapper measures repo STATE either side of the pull instead of trusting the
   exit code, so the caller can fail loudly rather than proceed into a
   half-applied tree. See ``docs/bugs/stranded-autostash.md``.
4. ``detect_missing_tracked_symlinks()`` — a git-tracked symlink (mode
   ``120000``) that HEAD lists but that is absent from the working tree
   (``lstat`` fails). Confirmed root cause of AIH-443 Shape A: a Claude Code
   ``isolation: worktree`` sub-agent checkout dropped 21 tracked symlinks
   (verified via ``git ls-tree`` mode + target-resolution testing) while every
   regular file checked out correctly, with no error surfaced anywhere.
5. ``detect_phantom_deleted_files()`` — AIH-443 Shape C, a REGULAR tracked file
   (mode ``100644``/``100755``) that the index still holds but that is absent
   from disk, with no stranded stash anywhere. Both detectors above are blind
   to it by construction: (3) finds nothing because ``git stash list`` is
   empty, and (4) skips it because the mode is not ``120000``.

   Shape C is kept alive by pre-commit, not by git. Every pre-commit-managed
   hook invocation (``pre-commit``, ``pre-push``, ``post-merge``,
   ``post-checkout``, ``post-rewrite``) enters
   ``pre_commit/staged_files_only.py::_unstaged_changes_cleared``, whose
   ``git diff-index`` sees the lone deletion as an unstaged change and takes
   the ``retcode == 1 and diff_stdout.strip()`` branch. That branch writes the
   deletion to ``~/.cache/pre-commit/patch<epoch>-<pid>``, runs
   ``git checkout -- .`` (which RESTORES the file to disk), and then
   re-applies the saved deletion patch in its ``finally:`` block — removing the
   file again. pre-commit therefore runs the exact command that would heal the
   worktree and then faithfully undoes it, leaving the tree byte-identical to
   how it started and raising nothing. Directly observed on
   ``aido/.worktrees/aido-1``: the index's cached stat for the missing path was
   rewritten with the real size/inode at 19:11:45 (the ``git checkout -- .``)
   and the containing directory's mtime moved again at 19:11:47 (the re-apply).

   A plain ``git checkout -- <path>`` DOES fix it durably: once the tree is
   clean, ``git diff-index`` returns 0 and pre-commit never captures a patch,
   so nothing is left to replay.
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


class GitProbeError(RuntimeError):
    """A read-only git state probe could not be answered.

    Raised rather than returning an empty result so a caller gating on repo
    state fails CLOSED. "I could not look" and "I looked and it was clean" are
    the same value otherwise, which is how a gate ends up inert.
    """


def _probe(repo_root: Path, *args: str) -> str:
    """Run a read-only git query, raising ``GitProbeError`` if it cannot answer.

    Decoded with the filesystem encoding and ``surrogateescape``, the same way
    Python decodes paths: a filename that is not valid UTF-8 (possible on Linux
    whenever ``core.quotePath`` is off) then round-trips as surrogates instead
    of raising ``UnicodeDecodeError`` inside a gate.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding=sys.getfilesystemencoding(),
        errors="surrogateescape",
        env=_git_env(),
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise GitProbeError(f"`git {' '.join(args)}` failed (exit {result.returncode}): {stderr}")
    return result.stdout


def unmerged_paths(repo_root: Path) -> set[str]:
    """Return paths carrying unmerged (conflict) stages in the index.

    This is the unambiguous signal that a repo is mid-conflict: unlike a stash
    entry it cannot be produced by ordinary user activity, and unlike
    ``.git/MERGE_HEAD``/``rebase-merge`` it survives an operation that
    "completed" while leaving conflicts behind — exactly the stranded autostash
    signature.

    Uses ``-z`` so paths arrive verbatim rather than shell-quoted, which keeps
    spaces, tabs and newlines in filenames from being mistaken for separators.
    Raises ``GitProbeError`` if git cannot answer.
    """
    out = _probe(repo_root, "ls-files", "--unmerged", "-z")
    paths = set()
    for record in out.split("\0"):
        if not record:
            continue
        _, _, path = record.partition("\t")
        if path:
            paths.add(path)
    return paths


def stash_entries(repo_root: Path) -> set[str]:
    """Return the commit ids currently on the stash stack.

    Identities, not a count: a count cannot tell "this pull stranded one" from
    "another process dropped one and this pull added one". Raises
    ``GitProbeError`` if git cannot answer.
    """
    out = _probe(repo_root, "stash", "list", "--format=%H")
    return {line for line in out.split() if line}


def operation_in_progress(repo_root: Path) -> str | None:
    """Return the name of an in-flight git operation, or ``None``.

    The discriminator between the two ways a repo ends up with conflict stages.
    An ordinary rebase/merge conflict leaves a marker saying so, and git tells
    the user what to do about it. A stranded autostash pop leaves conflict
    stages with NO marker at all — measured: rebase-body conflict leaves
    ``rebase-merge`` and exits 1, an autostash-pop conflict leaves nothing and
    exits 0. Only the second is silent, and only the second is this defect.
    """
    git_dir = Path(_probe(repo_root, "rev-parse", "--absolute-git-dir").strip())
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir / marker).exists():
            return marker
    return None


def pull_rebase_autostash(repo_root: Path) -> tuple[subprocess.CompletedProcess, str | None]:
    """Run ``git pull --rebase --autostash`` and verify what it actually did.

    Returns ``(completed_process, stranded_reason)``. ``stranded_reason`` is
    ``None`` when the repo is usable afterwards, and a human-readable
    description when THIS pull stranded it.

    Git's exit code does not cover the autostash pop: when the pop conflicts,
    git warns on stderr and still exits ``0`` (measured on 2.43.0 and 2.55.0),
    leaving conflict stages and the autostash behind. So the strand is detected
    from repo state either side of the call:

    * unmerged index paths that were not there before, or
    * stash entries that were not there before (the autostash never popped),
    * and NO git operation left in progress.

    That last clause matters. A plain rebase conflict also produces unmerged
    paths, but it exits non-zero and leaves ``rebase-merge`` behind, so git has
    already told the user. Reporting it here would refuse the very session that
    could resolve it, and would offer stash advice for a pull that never
    stashed anything.

    Both deltas are measured against a "before" snapshot, so a conflict the user
    was already resolving, or unrelated WIP stashes, are not misattributed.

    A non-zero exit with a clean tree (no network, say) is NOT a strand: the
    caller can carry on from the existing checkout, and gets the exit code via
    the returned process object.

    If a state probe cannot run at all, that is reported AS a strand — an
    unverifiable repo is treated as unsafe rather than assumed clean.
    """
    try:
        before_unmerged = unmerged_paths(repo_root)
        before_stashes = stash_entries(repo_root)
    except GitProbeError as e:
        before_unmerged, before_stashes = None, None
        probe_failure: str | None = str(e)
    else:
        probe_failure = None

    result = subprocess.run(
        ["git", "-C", str(repo_root), "pull", "--rebase", "--autostash"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )

    if probe_failure:
        return result, f"repo state could not be verified before the pull — {probe_failure}"

    try:
        new_unmerged = unmerged_paths(repo_root) - before_unmerged
        new_stashes = stash_entries(repo_root) - before_stashes
        in_progress = operation_in_progress(repo_root)
    except GitProbeError as e:
        return result, f"repo state could not be verified after the pull — {e}"

    if in_progress:
        # git left an explicit marker, so the conflict is visible by ordinary
        # means and the caller's own non-zero handling applies.
        return result, None

    reasons = []
    if new_unmerged:
        reasons.append(f"{len(new_unmerged)} newly conflicted path(s): {', '.join(sorted(new_unmerged))}")
    if new_stashes:
        reasons.append(f"{len(new_stashes)} autostash entr(y/ies) left on the stash stack")
    if not reasons:
        return result, None
    return result, "; ".join(reasons)


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


def detect_phantom_deleted_files(repo_root: Path) -> list[str]:
    """Return tracked REGULAR file paths the index holds but that are gone from disk.

    AIH-443 Shape C. ``git ls-files --deleted`` lists exactly the index entries
    whose working-tree file fails ``lstat``, which is the ``" D"`` status
    signature: the index (and HEAD) still carry the blob, so committing would
    delete real content that is still live on ``origin/main``.

    Symlink entries are excluded so this never double-reports Shape A, which
    ``detect_missing_tracked_symlinks()`` already covers with its own,
    differently-actionable warning.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--deleted", "-z"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        return []

    phantom: list[str] = []
    for path in result.stdout.split("\0"):
        if not path:
            continue
        # `git ls-files --deleted` cannot report the mode itself, so ask the
        # index per path. Only reached when something is already missing, so
        # the healthy case costs exactly one subprocess.
        entry = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-s", "--", path],
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        fields = entry.stdout.split()
        if entry.returncode != 0 or not fields:
            continue
        if fields[0] == "120000":
            continue
        phantom.append(path)
    return phantom
