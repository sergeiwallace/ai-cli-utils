#!/usr/bin/env python3
"""Reject a commit that deletes a tracked file still present upstream (AI-CLI-9sy).

A staged deletion of a file that still exists on the upstream default branch is
almost always a stale-tree accident, not an intentional removal -- and committing
it silently reverts work that is already published. The deletions this guard was
written for arrive with no error anywhere: `git status` reports the path as
deleted, every exit code reads fine, and the loss is only visible later as
someone else's work missing from the default branch.

WHY THE OUTCOME AND NOT THE CAUSE
An intentional `rm` and an accidental one are structurally indistinguishable:
`rm` + `git add -A` and `git rm` produce byte-identical index states, so no
amount of investigation into how the file went missing can separate them. That
is why this guard does not try. A missing file is harmless and recoverable from
HEAD; the harm is committing the deletion. Guarding the outcome holds regardless
of how many ways a file can go missing, and none of them need to be enumerated.

WHAT IT DOES NOT DO
It does not patch, wrap or work around the pre-commit framework. pre-commit's
`staged_files_only` correctly saves unstaged changes, runs hooks, and restores
them afterwards -- and a deleted file is a legitimate unstaged change that
someone who ran `rm` on purpose needs preserved. That behaviour is right, it is
third-party, and changing it would break real workflows. This guard sits at the
commit boundary instead, where the irreversible step actually happens.

It also never touches the network. The comparison is against the last-fetched
remote-tracking ref, which makes the answer a LOWER bound on what is live
upstream: a stale ref can miss a deletion (a file added upstream since the last
fetch), never invent one. A network call on every commit would be both slow and
wrong offline, and a false positive is the failure mode that gets a guard
switched off.

Renames are not deletions here: rename detection is on (`-M`), so moving a file
reports as R and never reaches the check.

EXIT CODES
    0 -- nothing staged for deletion, or every such deletion is acknowledged
    1 -- at least one unacknowledged deletion of a file still live upstream, or
         deletions are staged and no comparison ref could be resolved

ACKNOWLEDGING AN INTENTIONAL DELETION
    ALLOW_DELETIONS=path/a,path/b git commit ...   # per-path, preferred
    ALLOW_DELETIONS=1 git commit ...               # every staged deletion
    SKIP=live-file-deletion-guard git commit ...   # pre-commit's own bypass

The per-path form exists because the all-or-nothing form is the dangerous one:
an intentional deletion committed together with an accidental one is exactly the
case this guard has to catch, and a blanket bypass would wave both through.

DISTRIBUTION
Standard library and `git` only -- no import from this package, no venv, no
third-party dependency -- so the file is copyable into any repo that wants the
same protection, and the `python3 scripts/check_live_file_deletions.py` hook
entry works there unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HOOK_ID = "live-file-deletion-guard"
ACK_ENV = "ALLOW_DELETIONS"
REF_ENV = "DELETION_GUARD_REF"

#: Comparison refs tried in order. `origin/main` first rather than
#: `origin/HEAD`: HEAD is a local guess at the remote's default branch that only
#: `git clone` and an explicit `git remote set-head` ever write, so it can name a
#: branch that has since been renamed. `origin/HEAD` still comes before
#: `origin/master` so a repo whose default branch is neither `main` nor `master`
#: is covered without configuration.
DEFAULT_REF_CANDIDATES = (
    "refs/remotes/origin/main",
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/master",
)

#: Env vars that point a git subprocess at a DIFFERENT repository than the one
#: named by `-C`. git exports these into every hook process and the pre-commit
#: framework forwards them to a hook's `entry` command unchanged, so a hook that
#: inherits them can silently measure the wrong repo -- which for this guard
#: would mean reading another tree's index and reaching a confident wrong
#: verdict. Stripped so `-C` is the only thing that selects the repository.
_GIT_TARGETING_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_TARGETING_VARS}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=60,
        check=False,
    )


def repo_root(start: Path) -> Path | None:
    """Return the working tree root, or None when `start` is not in a repo."""
    result = _git(start, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def staged_deletions(root: Path) -> list[str]:
    """Return paths this commit would delete, as repo-relative slash paths.

    HEAD versus the index, so only what the commit itself removes is reported --
    not a file an earlier commit on the same branch deleted on purpose, which
    would otherwise make every later commit on that branch fail.
    """
    if _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        return []  # no commits yet, so nothing can be deleted by this one
    result = _git(root, "diff", "--cached", "-M", "--diff-filter=D", "--name-only", "-z")
    if result.returncode != 0:
        return []
    return [path for path in result.stdout.split("\0") if path]


def resolve_ref(root: Path) -> tuple[str | None, list[str]]:
    """Return (resolvable comparison ref, refs tried).

    An explicitly configured ref is never silently replaced by a fallback: a
    typo in it must surface as "cannot check" rather than as a check quietly
    measuring some other branch.
    """
    override = os.environ.get(REF_ENV, "").strip()
    tried = [override] if override else list(DEFAULT_REF_CANDIDATES)
    for ref in tried:
        if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
            return ref, tried
    return None, tried


def paths_in_ref(root: Path, ref: str) -> set[str]:
    result = _git(root, "ls-tree", "-r", "--name-only", "-z", ref)
    if result.returncode != 0:
        return set()
    return {path for path in result.stdout.split("\0") if path}


def acknowledged() -> tuple[bool, set[str]]:
    """Return (acknowledge everything, specific acknowledged paths)."""
    raw = os.environ.get(ACK_ENV, "").strip()
    if not raw:
        return False, set()
    if raw in {"1", "all", "ALL"}:
        return True, set()
    parts = {part.strip() for chunk in raw.split(",") for part in chunk.splitlines()}
    return False, {part.replace("\\", "/") for part in parts if part}


def _quote_paths(paths: list[str]) -> str:
    """Join paths for display in a copy-pasteable command.

    Double quotes rather than `shlex.quote`: they are understood by POSIX shells
    and by `cmd.exe`, and this message is read on all three platforms.
    """
    return " ".join(f'"{path}"' if " " in path else path for path in paths)


def check(root: Path) -> list[str]:
    """Return the failure message lines; empty means the commit may proceed."""
    deletions = staged_deletions(root)
    if not deletions:
        return []

    ack_all, ack_paths = acknowledged()
    if ack_all:
        return []
    deletions = [path for path in deletions if path not in ack_paths]
    if not deletions:
        return []

    ref, tried = resolve_ref(root)
    if ref is None:
        return [
            f"[{HOOK_ID}] REJECTED: this commit stages {len(deletions)} deletion(s), and no",
            "  upstream comparison ref could be resolved -- so whether they revert work that",
            "  is already published cannot be determined.",
            f"    tried: {', '.join(tried)}",
            "",
            "  This fails CLOSED deliberately. An unresolvable probe treated as 'no problem'",
            "  is how a guard comes to enforce nothing while every exit code still reads fine,",
            "  which is the failure this guard exists to prevent.",
            "",
            "  Remedies:",
            "    git fetch origin                      # populate the remote-tracking ref",
            f"    {REF_ENV}=<ref> git commit ...   # name the comparison ref yourself",
            f"    {ACK_ENV}=1 git commit ...           # the deletions are intentional",
        ]

    live = paths_in_ref(root, ref)
    offenders = [path for path in deletions if path in live]
    if not offenders:
        return []

    short_ref = ref.removeprefix("refs/remotes/")
    return [
        f"[{HOOK_ID}] REJECTED: this commit deletes {len(offenders)} file(s) that still exist",
        f"  on {short_ref}. Committing that reverts published work, silently.",
        "",
        *(f"    {path}" for path in offenders),
        "",
        "  If you did not mean to delete these, restore them and re-stage:",
        f"    git restore --source=HEAD --staged --worktree -- {_quote_paths(offenders)}",
        "",
        "  If the deletion is intentional, acknowledge these exact paths:",
        f"    {ACK_ENV}={','.join(offenders)} git commit ...",
        f"  or bypass this hook for one commit:  SKIP={HOOK_ID} git commit ...",
    ]


def main() -> int:
    root = repo_root(Path.cwd())
    if root is None:
        print(f"[{HOOK_ID}] not a git working tree -- nothing to check", file=sys.stderr)
        return 0
    errors = check(root)
    if not errors:
        return 0
    print("", file=sys.stderr)
    for line in errors:
        print(line, file=sys.stderr)
    print("", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
