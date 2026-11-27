"""Shared git-subprocess safety helpers (AI-CLI-99).

Prevents the recurring ``core.bare=true`` / stale ``core.worktree`` corruption
class on a repo's main working tree. Root cause: worktree tooling runs ``git``
subprocesses that INHERIT the parent process's environment. When the parent is
itself running inside a git worktree (e.g. a nested CC session launched from
``.worktrees/session-N``), git context env vars (``GIT_DIR``, ``GIT_WORK_TREE``, ...)
leak into ``git worktree add/remove`` subprocess calls. Because many
sessions/worktrees share one gitdir, this can write ``core.bare``/
``core.worktree`` onto the SHARED main-repo config, corrupting every session
operating from the main tree path (``fatal: this operation must be run in a
work tree``).

Two layers of defense:

1. ``_git_env()`` — strip git-targeting env vars before every git subprocess
   call that touches repo/worktree structure, so it always targets the repo
   passed via ``-C``/``cwd``, never one redirected by an inherited
   ``GIT_DIR``/``GIT_WORK_TREE``. ``_creator_env()`` extends it with the
   worktree-creator attestation that the environment's ``post-checkout`` backstop
   reads, and belongs here because that attestation must ride on the same
   scrubbed environment every ``git worktree add`` already uses.
2. ``repair_bare_worktree_config()`` — a deterministic backstop: assert +
   repair a normal working tree's ``core.bare``/``core.worktree`` regardless
   of source. This covers corruption paths we don't control (e.g. Claude
   Code's own ``isolation: worktree`` sub-agent tool).

A third, detection-only layer covers two distinct "phantom deletion"
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
   (``lstat`` fails). Confirmed root cause of Shape A: a Claude Code
   ``isolation: worktree`` sub-agent checkout dropped 21 tracked symlinks
   (verified via ``git ls-tree`` mode + target-resolution testing) while every
   regular file checked out correctly, with no error surfaced anywhere.
5. ``detect_phantom_deleted_files()`` — Shape C, a REGULAR tracked file
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
   ``myproject/.worktrees/myproject-1``: the index's cached stat for the missing path was
   rewritten with the real size/inode at 19:11:45 (the ``git checkout -- .``)
   and the containing directory's mtime moved again at 19:11:47 (the re-apply).

   A plain ``git checkout -- <path>`` DOES fix it durably: once the tree is
   clean, ``git diff-index`` returns 0 and pre-commit never captures a patch,
   so nothing is left to replay.
"""

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import output as _out
from .output import Tag

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
    context. Keeps other ``GIT_*`` vars (``GIT_SSH*``, ``GIT_AUTHOR_*``,
    ``GIT_COMMITTER_*``, ...) untouched.

    Also defaults ``GIT_TERMINAL_PROMPT=0`` so a git subprocess can never stop
    to ask for a credential. Every git call routed through here is unattended
    automation reading back a captured result, and git writes its credential
    prompt to ``/dev/tty`` rather than to the subprocess's stdin/stdout — so
    ``capture_output=True`` does NOT contain it. A remote git cannot
    authenticate to (an https remote on a machine whose credential helper holds
    nothing for that host, say) therefore hung ``ai c <session>`` on a bare
    ``Username for 'https://...':`` prompt during the worktree sync, instead of
    letting the caller's own degraded-network handling report it and carry on
    from the existing checkout. Failing fast is what makes that handling
    reachable. A caller that has explicitly set the var keeps its own value.
    """
    env = dict(base_env if base_env is not None else os.environ)
    for var in _GIT_TARGETING_VARS:
        env.pop(var, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return with_git_config(env, GIT_LONG_PATHS)


# Git for Windows refuses a working-tree path over 260 characters ("Filename too long") unless
# core.longpaths is on, and git elsewhere ignores the key. A session worktree nests every tracked
# path under `<repo>/.worktrees/<name>/`, so a file that fits the main checkout can fail the
# session's checkout with "Could not reset index file to revision 'HEAD'".
GIT_LONG_PATHS = (("core.longpaths", "true"),)


def with_git_config(env: dict[str, str], pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Append ``pairs`` to the ``GIT_CONFIG_COUNT`` entries ``env`` already carries, in place."""
    try:
        count = max(0, int(env.get("GIT_CONFIG_COUNT") or 0))
    except ValueError:
        count = 0
    present = {(env.get(f"GIT_CONFIG_KEY_{i}"), env.get(f"GIT_CONFIG_VALUE_{i}")) for i in range(count)}
    for key, value in pairs:
        if (key, value) in present:
            continue
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def _creator_env(creator: str, canonical_leaf: str | None = None) -> dict[str, str]:
    """``_git_env()`` plus the worktree-creator attestation, for a ``git worktree add``.

    An environment may install a ``post-checkout`` backstop
    (``scripts/worktree_creation_guard.py``) that undoes a worktree created flat under
    ``<repo>/.worktrees/``, because that root is reserved for a Claude Code session's own
    checkout. The backstop admits a creation only when the creating process's environment
    names a trusted creator, so this launcher has to identify itself or every ``ai c <n>``
    would have its own session home removed at creation.

    The two variable names are a cross-repo contract and are spelled as literals on both
    sides deliberately: the backstop runs as a standalone hook script in another repository
    and cannot import anything from here, so a shared constant would only hide a drift that
    the tests on each side are what actually catch.

    ``canonical_leaf`` is the exact destination directory name, and only the ``ai-c``
    creator carries one — the backstop admits ``ai-c`` for that ONE declared path rather
    than for the canonical leaf *shape*, so passing a session's own slot name is what
    distinguishes it from a hand-run ``git worktree add .worktrees/<name>``. When no leaf is
    given the variable is removed rather than left alone: the attestation must describe this
    call, never a value inherited from whatever launched the process.
    """
    env = _git_env()
    env["AIH_WORKTREE_CREATOR"] = creator
    if canonical_leaf is None:
        env.pop("AIH_WORKTREE_CANONICAL_LEAF", None)
    else:
        env["AIH_WORKTREE_CANONICAL_LEAF"] = canonical_leaf
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
        check=False,
    )
    if bare.returncode == 0 and bare.stdout.strip() == "true":
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "core.bare", "false"],
            capture_output=True,
            env=_git_env(),
            check=False,
        )
        _out.warning(Tag.GIT, "repair", f"repaired core.bare=true corruption on {repo_root} (reset to false)")
        repaired = True

    worktree_cfg = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--get", "core.worktree"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if worktree_cfg.returncode == 0 and worktree_cfg.stdout.strip():
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "--local", "--unset", "core.worktree"],
            capture_output=True,
            env=_git_env(),
            check=False,
        )
        _out.warning(
            Tag.GIT, "repair", f"repaired stale core.worktree={worktree_cfg.stdout.strip()!r} on {repo_root} (unset)"
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
        check=False,
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


def _head_identity(repo_root: Path) -> tuple[str, str] | None:
    """Return ``(head_commit, stash_branch_label)`` for this worktree, or ``None`` when unborn.

    The label is the one ``git stash`` writes into an entry's subject: the short
    branch name, or ``(no branch)`` on a detached HEAD. An unborn branch has no
    commit to stash against, so nothing can be attributed to it. Raises
    ``GitProbeError`` if git cannot answer.
    """
    try:
        commit = _probe(repo_root, "rev-parse", "--verify", "-q", "HEAD").strip()
    except GitProbeError:
        _probe(repo_root, "rev-parse", "--git-dir")
        return None
    try:
        symbolic = _probe(repo_root, "symbolic-ref", "-q", "HEAD").strip()
    except GitProbeError:
        symbolic = ""  # HEAD resolved above, so the only refusal left is a detached HEAD
    label = symbolic.removeprefix("refs/heads/") if symbolic else "(no branch)"
    return commit, label


def own_autostash_entries(repo_root: Path, head: tuple[str, str] | None) -> set[str]:
    """Return the stash entries that are this worktree's own autostash for ``head``.

    The stash stack is shared by every worktree of a repository, so a new entry
    after a pull is not necessarily the pull's. Git's autostash is recognisable
    three ways at once, and all three must hold:

    * its stash reflog message is the bare word ``autostash`` (``git stash push
      -m autostash`` records ``On <branch>: autostash`` instead),
    * its commit subject is ``On <branch>: autostash`` for THIS worktree's branch,
      and two worktrees cannot have the same branch checked out,
    * its first parent is the commit this worktree had checked out before the pull.

    Raises ``GitProbeError`` if git cannot answer.
    """
    if head is None:
        return set()
    commit, label = head
    out = _probe(repo_root, "stash", "list", "--format=%H%x00%gs%x00%s%x00%P")
    own = set()
    for line in out.splitlines():
        fields = line.split("\0")
        if len(fields) != 4:
            continue
        stash_id, reflog_subject, subject, parents = fields
        if reflog_subject == "autostash" and subject == f"On {label}: autostash" and parents.split(" ", 1)[0] == commit:
            own.add(stash_id)
    return own


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


def _pull_refspec(repo_root: Path) -> list[str]:
    """Return explicit ``["origin", "<branch>"]`` when the branch has no upstream.

    A bare ``git pull`` requires tracking information. A checkout parked on an unpushed
    local branch (``fix/…``) has none, so the pull aborts with "There is no tracking
    information for the current branch" and a wall of git advice — it never contacts the
    remote at all. Naming the remote and branch explicitly makes the pull work there.

    Returns ``[]`` when an upstream exists (let git use it) or in a detached HEAD, where
    there is no branch to pull into and guessing one would be wrong.
    """
    upstream = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if upstream.returncode == 0:
        return []
    branch = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    current = branch.stdout.strip()
    if branch.returncode != 0 or not current or current == "HEAD":
        return []
    return ["origin", current]


_BEADS_ISSUES_MIRROR = ".beads/issues.jsonl"


def _repair_beads_issues_autostash_strand(repo_root: Path, new_unmerged: set[str], new_stashes: set[str]) -> bool:
    """Regenerate the Beads issues mirror for its one safe conflict shape.

    ``.beads/issues.jsonl`` is a generated projection of the live Beads store;
    no other file, including ``.beads/interactions.jsonl``, has this property.
    Every failure leaves the original strand for callers to refuse as usual.
    """
    if new_unmerged != {_BEADS_ISSUES_MIRROR}:
        return False

    try:
        doctor = subprocess.run(
            ["bd", "doctor"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            check=False,
        )
    except OSError:
        return False
    if doctor.returncode != 0:
        return False

    try:
        export = subprocess.run(
            ["bd", "export", "--output", _BEADS_ISSUES_MIRROR],
            capture_output=True,
            text=True,
            cwd=repo_root,
            check=False,
        )
    except OSError:
        return False
    if export.returncode != 0:
        return False

    try:
        staged = subprocess.run(
            ["git", "-C", str(repo_root), "add", "--", _BEADS_ISSUES_MIRROR],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    except OSError:
        return False
    if staged.returncode != 0:
        return False

    dropped_stashes = []
    for stash_id in sorted(new_stashes):
        try:
            shown = subprocess.run(
                ["git", "-C", str(repo_root), "stash", "show", "--format=", "--name-only", "-z", stash_id],
                capture_output=True,
                text=True,
                env=_git_env(),
                check=False,
            )
        except OSError:
            continue
        stash_paths = {path for path in shown.stdout.split("\0") if path}
        if shown.returncode != 0 or stash_paths != {_BEADS_ISSUES_MIRROR}:
            continue
        try:
            dropped = subprocess.run(
                ["git", "-C", str(repo_root), "stash", "drop", stash_id],
                capture_output=True,
                text=True,
                env=_git_env(),
                check=False,
            )
        except OSError:
            continue
        if dropped.returncode == 0:
            dropped_stashes.append(stash_id)

    if dropped_stashes:
        _out.emit(
            Tag.GIT,
            "repair",
            "Auto-resolved a beads-only autostash strand: regenerated "
            f"{_BEADS_ISSUES_MIRROR} from the live bd store, dropped stash "
            f"{', '.join(dropped_stashes)}.",
            err=True,
        )
    elif new_stashes:
        _out.emit(
            Tag.GIT,
            "repair",
            "Auto-resolved a beads-only autostash strand: regenerated "
            f"{_BEADS_ISSUES_MIRROR} from the live bd store; left newly created "
            "stash entries in place because their contents were not safely verified.",
            err=True,
        )
    else:
        _out.emit(
            Tag.GIT,
            "repair",
            f"Auto-resolved a beads-only autostash strand: regenerated {_BEADS_ISSUES_MIRROR} from the live bd store.",
            err=True,
        )
    return True


def _is_worktree_dirty(repo_root: Path) -> bool:
    """Return whether tracked files have unstaged or staged modifications."""
    try:
        out = _probe(repo_root, "status", "--porcelain", "-uno")
    except GitProbeError:
        return False
    return bool(out.strip())


def _snapshot_strand_baseline(
    repo_root: Path,
) -> tuple[set[str] | None, set[str] | None, tuple[str, str] | None, str | None]:
    """Capture unmerged paths, stash stack, and HEAD identity before a git operation."""
    try:
        before_unmerged = unmerged_paths(repo_root)
        before_stashes = stash_entries(repo_root)
        before_head = _head_identity(repo_root)
    except GitProbeError as e:
        return None, None, None, str(e)
    return before_unmerged, before_stashes, before_head, None


def _detect_strand_after_op(
    repo_root: Path,
    before_unmerged: set[str] | None,
    before_stashes: set[str] | None,
    before_head: tuple[str, str] | None,
    probe_failure: str | None,
    op_label: str,
) -> str | None:
    """Compare post-operation repo state against the pre-operation baseline."""
    if probe_failure:
        return f"repo state could not be verified before the {op_label} — {probe_failure}"

    assert before_unmerged is not None
    assert before_stashes is not None
    try:
        new_unmerged = unmerged_paths(repo_root) - before_unmerged
        new_stashes = own_autostash_entries(repo_root, before_head) - before_stashes
        in_progress = operation_in_progress(repo_root)
    except GitProbeError as e:
        return f"repo state could not be verified after the {op_label} — {e}"

    if in_progress:
        return None

    reasons = []
    if new_unmerged:
        reasons.append(f"{len(new_unmerged)} newly conflicted path(s): {', '.join(sorted(new_unmerged))}")
    if new_stashes:
        shas = ", ".join(sorted(new_stashes))
        reasons.append(f"{len(new_stashes)} autostash entr(y/ies) left on the stash stack ({shas})")
    if not reasons:
        return None
    if _repair_beads_issues_autostash_strand(repo_root, new_unmerged, new_stashes):
        return None
    return "; ".join(reasons)


def pull_rebase_autostash(repo_root: Path) -> tuple[subprocess.CompletedProcess, str | None]:
    """Run ``git pull --rebase`` (adding ``--autostash`` only when dirty) and verify state.

    Returns ``(completed_process, stranded_reason)``. ``stranded_reason`` is
    ``None`` when the repo is usable afterwards, and a human-readable
    description (including stranded stash SHAs) when THIS pull stranded it.

    Git's exit code does not cover the autostash pop: when the pop conflicts,
    git warns on stderr and still exits ``0`` (measured on 2.43.0 and 2.55.0),
    leaving conflict stages and the autostash behind. So the strand is detected
    from repo state either side of the call:

    * unmerged index paths that were not there before, or
    * this worktree's own autostash entries that were not there before (the
      autostash never popped). The stash stack is shared by every worktree, so
      an entry another session pushes while this pull runs is not counted,
    * and NO git operation left in progress.

    That last clause matters. A plain rebase conflict also produces unmerged
    paths, but it exits non-zero and leaves ``rebase-merge`` behind, so git has
    already told the user. Reporting it here would refuse the very session that
    could resolve it, and would offer stash advice for a pull that never
    stashed anything.

    Both deltas are measured against a "before" snapshot, so a conflict the user
    was already resolving, or unrelated WIP stashes, are not misattributed.

    The sole auto-repair exception is a newly conflicted
    ``.beads/issues.jsonl`` alone. If ``bd doctor`` can query the live Beads
    store, its generated mirror is exported and staged; all other conflict
    shapes retain the returned strand reason unchanged.

    A non-zero exit with a clean tree (no network, say) is NOT a strand: the
    caller can carry on from the existing checkout, and gets the exit code via
    the returned process object.

    If a state probe cannot run at all, that is reported AS a strand — an
    unverifiable repo is treated as unsafe rather than assumed clean.
    """
    before_unmerged, before_stashes, before_head, probe_failure = _snapshot_strand_baseline(repo_root)

    pull_args = ["git", "-C", str(repo_root), "pull", "--rebase"]
    if _is_worktree_dirty(repo_root):
        pull_args.append("--autostash")
    pull_args.extend(_pull_refspec(repo_root))

    result = subprocess.run(
        pull_args,
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )

    stranded = _detect_strand_after_op(
        repo_root,
        before_unmerged,
        before_stashes,
        before_head,
        probe_failure,
        "pull",
    )
    return result, stranded


@dataclass(frozen=True)
class WorktreeSyncResult:
    """Outcome of syncing a session worktree at launch."""

    arm: str
    process: subprocess.CompletedProcess
    stranded: str | None
    summary: str


def _git_query(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a non-interactive read-only git command against *repo_root*."""
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )


def _ref_exists(repo_root: Path, ref: str) -> bool:
    """Return whether *ref* resolves in *repo_root*."""
    return _git_query(repo_root, "rev-parse", "--verify", "-q", ref).returncode == 0


def _remote_exists(repo_root: Path, remote: str) -> bool:
    """Return whether *remote* has a configured URL in *repo_root*."""
    return _git_query(repo_root, "remote", "get-url", remote).returncode == 0


def _push_remote_for_branch(repo_root: Path, current: str, fallback_remote: str) -> str:
    """Return the configured push remote for *current*, falling back to *fallback_remote*."""
    for key in (f"branch.{current}.pushRemote", "remote.pushDefault"):
        proc = _git_query(repo_root, "config", "--get", key)
        value = proc.stdout.strip() if proc.returncode == 0 else ""
        if value:
            return value
    return fallback_remote


def _integration_remote_ref(repo_root: Path, remote: str, current: str, upstream: str | None) -> str | None:
    """Resolve the remote integration branch ref (for example ``origin/main``)."""
    if upstream and upstream != f"{remote}/{current}":
        return upstream
    sym = _git_query(repo_root, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    if sym.returncode == 0 and sym.stdout.strip():
        return sym.stdout.strip()
    for fallback in ("main", "master"):
        if _ref_exists(repo_root, f"refs/remotes/{remote}/{fallback}"):
            return f"{remote}/{fallback}"
    return None


def _ls_remote_branch_sha(
    repo_root: Path, remote: str, branch: str
) -> tuple[bool, str | None, subprocess.CompletedProcess]:
    """Probe *remote* for ``refs/heads/<branch>``, returning ``(ok, sha_or_none, process)``."""
    proc = _git_query(repo_root, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    if proc.returncode != 0:
        return False, None, proc
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == f"refs/heads/{branch}":
            return True, parts[0], proc
    return True, None, proc


def _resolve_published_branch(
    repo_root: Path,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None, subprocess.CompletedProcess | None]:
    """Identify whether ``HEAD`` is published on its push or fetch remote.

    Returns ``(current, upstream, push_remote, integration_remote, published_ref,
    integration_ref, probe_failure_proc)``. When a branch has no locally cached
    remote-tracking ref on its push remote, probes the remote with ``git ls-remote``
    before concluding it is unpublished.
    """
    branch_proc = _git_query(repo_root, "symbolic-ref", "--short", "-q", "HEAD")
    current = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
    if not current:
        return None, None, None, None, None, None, None

    upstream_proc = _git_query(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = upstream_proc.stdout.strip() if upstream_proc.returncode == 0 else None
    fetch_remote = upstream.split("/", 1)[0] if upstream and "/" in upstream else "origin"
    push_remote = _push_remote_for_branch(repo_root, current, fetch_remote)
    integration_ref = _integration_remote_ref(repo_root, fetch_remote, current, upstream)
    integration_branch = (
        integration_ref[len(fetch_remote) + 1 :]
        if integration_ref and integration_ref.startswith(f"{fetch_remote}/")
        else None
    )

    if current == integration_branch and push_remote == fetch_remote:
        return current, upstream, push_remote, fetch_remote, None, integration_ref, None

    candidate_remotes: list[str] = [push_remote]
    if fetch_remote != push_remote:
        candidate_remotes.append(fetch_remote)

    for remote in candidate_remotes:
        if remote == fetch_remote and current == integration_branch:
            continue
        if _ref_exists(repo_root, f"refs/remotes/{remote}/{current}"):
            return current, upstream, remote, fetch_remote, f"{remote}/{current}", integration_ref, None

    if _remote_exists(repo_root, push_remote):
        ok, remote_sha, probe_proc = _ls_remote_branch_sha(repo_root, push_remote, current)
        if not ok:
            if push_remote != fetch_remote:
                return (
                    current,
                    upstream,
                    push_remote,
                    fetch_remote,
                    f"{push_remote}/{current}",
                    integration_ref,
                    probe_proc,
                )
            return current, upstream, push_remote, fetch_remote, None, integration_ref, None
        if remote_sha is not None:
            return current, upstream, push_remote, fetch_remote, f"{push_remote}/{current}", integration_ref, None

    return current, upstream, push_remote, fetch_remote, None, integration_ref, None


def _ahead_behind(repo_root: Path, target_ref: str) -> tuple[int, int] | None:
    """Return ``(ahead, behind)`` commit counts for ``HEAD`` relative to *target_ref*."""
    count_proc = _git_query(repo_root, "rev-list", "--left-right", "--count", f"HEAD...{target_ref}")
    if count_proc.returncode != 0:
        return None
    parts = count_proc.stdout.strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _commits_behind(repo_root: Path, target_ref: str) -> int | None:
    """Return how many commits ``HEAD`` is behind *target_ref*, or ``None`` if unavailable."""
    count_proc = _git_query(repo_root, "rev-list", "--count", f"HEAD..{target_ref}")
    if count_proc.returncode != 0:
        return None
    try:
        return int(count_proc.stdout.strip())
    except ValueError:
        return None


def sync_worktree_branch(
    repo_root: Path,
    *,
    published_mode: str = "report",
    pull_fn: Callable[[Path], tuple[subprocess.CompletedProcess, str | None]] | None = None,
) -> WorktreeSyncResult:
    """Sync a session worktree without rebasing a published branch onto trunk.

    Checks both locally cached remote-tracking refs and the configured push
    remote (including ``branch.<name>.pushRemote`` and ``remote.pushDefault``)
    before deciding to rebase. When the remote branch tip is an ancestor of
    ``HEAD``, runs ``git fetch`` and either reports distance behind the
    integration branch (default ``"report"``) or merges it (``"merge"``) with
    stranded-autostash detection. When the remote branch exists but is not an
    ancestor of ``HEAD``, skips rebasing unless the configured upstream IS that
    remote branch itself. Otherwise delegates to *pull_fn* (default
    :func:`pull_rebase_autostash`) and records which arm was taken.
    """
    runner = pull_fn if pull_fn is not None else pull_rebase_autostash
    (
        current,
        upstream,
        push_remote,
        integration_remote,
        published_ref,
        integration_ref,
        probe_failure,
    ) = _resolve_published_branch(repo_root)
    rebase_reason = "no published upstream"

    if probe_failure is not None and current and published_ref:
        return WorktreeSyncResult(
            arm="published-fetch-failed",
            process=probe_failure,
            stranded=None,
            summary=(
                f"could not verify push remote for branch '{current}' ('{published_ref}') — "
                f"skipped rebase (exit {probe_failure.returncode})"
            ),
        )

    if current and push_remote and integration_remote and published_ref:
        fetch = _git_query(repo_root, "fetch", push_remote)
        if fetch.returncode != 0:
            return WorktreeSyncResult(
                arm="published-fetch-failed",
                process=fetch,
                stranded=None,
                summary=(
                    f"published branch '{current}' tracks '{published_ref}' — "
                    f"skipped rebase; git fetch failed (exit {fetch.returncode})"
                ),
            )
        published_target = published_ref
        if not _ref_exists(repo_root, f"refs/remotes/{published_ref}"):
            branch_fetch = _git_query(repo_root, "fetch", push_remote, f"refs/heads/{current}")
            if branch_fetch.returncode != 0:
                return WorktreeSyncResult(
                    arm="published-fetch-failed",
                    process=branch_fetch,
                    stranded=None,
                    summary=(
                        f"published branch '{current}' tracks '{published_ref}' — "
                        f"skipped rebase; git fetch failed (exit {branch_fetch.returncode})"
                    ),
                )
            published_target = "FETCH_HEAD"
        if integration_remote != push_remote and _remote_exists(repo_root, integration_remote):
            integration_fetch = _git_query(repo_root, "fetch", integration_remote)
            if integration_fetch.returncode != 0:
                return WorktreeSyncResult(
                    arm="published-fetch-failed",
                    process=integration_fetch,
                    stranded=None,
                    summary=(
                        f"published branch '{current}' tracks '{published_ref}' — "
                        f"skipped rebase; git fetch {integration_remote} failed (exit {integration_fetch.returncode})"
                    ),
                )
        refreshed_integration = integration_ref or _integration_remote_ref(
            repo_root, integration_remote, current, upstream
        )
        ancestor_check = _git_query(repo_root, "merge-base", "--is-ancestor", published_target, "HEAD")
        if ancestor_check.returncode == 0:
            behind = _commits_behind(repo_root, refreshed_integration) if refreshed_integration else None
            if published_mode == "merge" and refreshed_integration and behind:
                b_unmerged, b_stashes, b_head, b_probe = _snapshot_strand_baseline(repo_root)
                merged = _git_query(repo_root, "merge", "--no-edit", refreshed_integration)
                merge_stranded = _detect_strand_after_op(repo_root, b_unmerged, b_stashes, b_head, b_probe, "merge")
                if merged.returncode == 0 or merge_stranded is not None:
                    return WorktreeSyncResult(
                        arm="published-merge",
                        process=merged,
                        stranded=merge_stranded,
                        summary=(
                            f"published branch '{current}' tracks '{published_ref}' — "
                            f"skipped rebase; merged {behind} commit(s) from {refreshed_integration}"
                        ),
                    )
                return WorktreeSyncResult(
                    arm="published-merge-failed",
                    process=merged,
                    stranded=None,
                    summary=(
                        f"published branch '{current}' tracks '{published_ref}' — "
                        f"merge with {refreshed_integration} failed (exit {merged.returncode})"
                    ),
                )
            if refreshed_integration and behind is not None:
                distance = (
                    f"{behind} commit(s) behind {refreshed_integration}"
                    if behind > 0
                    else f"up to date with {refreshed_integration}"
                )
            else:
                distance = "remote-tracking refs updated"
            return WorktreeSyncResult(
                arm="published-report",
                process=fetch,
                stranded=None,
                summary=f"published branch '{current}' tracks '{published_ref}' — skipped rebase; fetched ({distance})",
            )
        if upstream != published_ref:
            counts = _ahead_behind(repo_root, published_target)
            if counts is not None and counts[0] == 0 and counts[1] > 0:
                detail = f"is {counts[1]} commit(s) behind '{published_ref}'"
            elif counts is not None:
                detail = f"diverged from '{published_ref}' ({counts[0]} ahead / {counts[1]} behind)"
            else:
                detail = f"diverged from '{published_ref}'"
            return WorktreeSyncResult(
                arm="published-diverged",
                process=fetch,
                stranded=None,
                summary=f"published branch '{current}' {detail} — skipped rebase",
            )
        rebase_reason = f"'{published_ref}' is not an ancestor of HEAD"

    pull, stranded = runner(repo_root)
    summary = f"synced branch '{current}' by pull --rebase ({rebase_reason})" if current else ""
    return WorktreeSyncResult(arm="rebase", process=pull, stranded=stranded, summary=summary)


def detect_missing_tracked_symlinks(repo_root: Path) -> list[str]:
    """Return tracked symlink paths (mode ``120000`` in HEAD) absent from disk.

    A normal checkout always materializes every tracked path, symlink or not.
    A path that HEAD lists as a symlink but that fails ``lstat`` on disk is
    exactly Shape A's signature: ``git status`` reports it deleted,
    ``git show HEAD:<path>`` and ``origin/main`` both still have it, and
    nothing on this fleet's side ever touched it (verified by full sub-agent
    transcript audit — the checkout itself never materialized these entries).
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "-r", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
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

    Shape C. ``git ls-files --deleted`` lists exactly the index entries
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
        check=False,
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
            check=False,
        )
        fields = entry.stdout.split()
        if entry.returncode != 0 or not fields:
            continue
        if fields[0] == "120000":
            continue
        phantom.append(path)
    return phantom
