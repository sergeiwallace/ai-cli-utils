"""Workspace-wide git pull/rebase for all repos and worktrees."""

import json
import re
import subprocess
from pathlib import Path


def _parse_workspace_folders(workspace_path: Path) -> list[Path]:
    """Return absolute paths of all folders in a .code-workspace file."""
    if not workspace_path.exists():
        raise FileNotFoundError(f"Workspace file not found: {workspace_path}")
    text = workspace_path.read_text()
    # Strip // comments and trailing commas (JSON5 subset used by VS Code)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    data = json.loads(text)
    base = workspace_path.parent
    return [(base / f["path"]).resolve() for f in data.get("folders", [])]


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout, result.stderr


def _is_git_repo(path: Path) -> bool:
    rc, _, _ = _run(["git", "-C", str(path), "rev-parse", "--git-dir"])
    return rc == 0


def _is_dirty(path: Path) -> bool:
    _, stdout, _ = _run(["git", "-C", str(path), "status", "--porcelain"])
    return bool(stdout.strip())


def _pull_rebase(path: Path) -> tuple[int, str]:
    rc, stdout, stderr = _run(["git", "-C", str(path), "pull", "--rebase"])
    return rc, (stdout + stderr).strip()


def _upstream_drift(wt: Path) -> str | None:
    """Return a warning string if a canonical worktree branch isn't tracking its
    repository's integration branch, else None (AI-CLI-128).

    A worktree branch left without upstream — or tracking a branch the repository
    does not integrate through — is the precursor state that let one session sit
    46 commits behind for months while reporting "0 ahead, 0 behind" against its
    own dead upstream. This surfaces drift instead of letting it accumulate
    silently; it does not fail the pull.

    The expected upstream is resolved per repository rather than fixed at
    ``origin/main`` (AI-CLI-193): in a repository whose work integrates through a
    workspace branch, ``origin/main`` is itself the drifted state, so hardcoding it
    here would report every correctly-tracking worktree as drifted and stay silent
    about the one case that matters.
    """
    rc, branch, _ = _run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    if rc != 0 or not branch.startswith("wt-"):
        return None  # not a canonical wt-* worktree branch, or detached HEAD

    rc, upstream, _ = _run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", f"{branch}@{{u}}"])
    upstream = upstream.strip()
    if rc != 0:
        return f"{branch}: no upstream configured"

    expected = _expected_upstream(wt)
    if expected is None:
        return None  # cannot resolve the repository's integration branch — do not guess
    if upstream != expected:
        return f"{branch}: upstream is {upstream}, not {expected}"
    return None


def _expected_upstream(wt: Path) -> str | None:
    """Return the ``origin/<branch>`` a worktree under ``wt`` should track, or None.

    Resolves the owning repository's integration branch through the same path
    ``create_worktree`` uses, so the check and the creation cannot disagree.
    """
    from .config import WORKTREE_DIR
    from .session import _resolve_worktree_target

    resolved = wt.resolve()
    if WORKTREE_DIR not in resolved.parts:
        return None
    repo_root = Path(*resolved.parts[: resolved.parts.index(WORKTREE_DIR)])
    try:
        _, upstream_branch = _resolve_worktree_target(repo_root)
    except RuntimeError:
        return None
    return None if upstream_branch is None else f"origin/{upstream_branch}"


def _get_worktrees(path: Path) -> list[Path]:
    """Return paths of all linked worktrees (excludes main worktree and bare)."""
    _, stdout, _ = _run(["git", "-C", str(path), "worktree", "list", "--porcelain"])
    worktrees: list[Path] = []
    blocks = re.split(r"\n\n+", stdout.strip())
    for i, block in enumerate(blocks):
        if i == 0:
            continue  # first block is always the main worktree
        lines = block.strip().splitlines()
        if not lines:
            continue
        if any(line.strip() == "bare" for line in lines):
            continue
        wt_line = next((line for line in lines if line.startswith("worktree ")), None)
        if wt_line:
            worktrees.append(Path(wt_line.split(" ", 1)[1]))
    return worktrees


def ws_pull(
    workspace_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Pull all repos and their worktrees listed in a VS Code workspace file."""
    folders = _parse_workspace_folders(workspace_path)

    try:
        display_path = Path("~") / workspace_path.resolve().relative_to(Path.home())
    except ValueError:
        display_path = workspace_path

    print(f"Workspace: {display_path} ({len(folders)} repos)\n")

    total_pulled = 0
    total_stashed = 0
    total_dirty = 0
    total_drifted = 0

    for folder in folders:
        name = folder.name

        if not folder.exists():
            continue

        if not _is_git_repo(folder):
            print(f"  ⚠  {name}  (not a git repo, skipped)")
            continue

        # Main tree
        main_was_dirty = _is_dirty(folder)
        if main_was_dirty:
            if not dry_run:
                _run(["git", "-C", str(folder), "stash", "push", "-m", "ai-ws auto-stash"])
                _, out = _pull_rebase(folder)
                _run(["git", "-C", str(folder), "stash", "pop"])
                if verbose and out:
                    print(f"     {out}")
            total_stashed += 1
        else:
            if not dry_run:
                _, out = _pull_rebase(folder)
                if verbose and out:
                    print(f"     {out}")
            total_pulled += 1

        # Worktrees
        worktrees = _get_worktrees(folder)
        clean_wts: list[str] = []
        dirty_wts: list[str] = []
        drifted: list[str] = []

        for wt in worktrees:
            if not wt.exists():
                continue
            drift = _upstream_drift(wt)
            if drift:
                drifted.append(f"{name}/{wt.name}: {drift}")
            if _is_dirty(wt):
                dirty_wts.append(wt.name)
                total_dirty += 1
            else:
                if not dry_run:
                    _pull_rebase(wt)
                clean_wts.append(wt.name)
                total_pulled += 1

        # Output
        if main_was_dirty:
            suffix = "(would stash+pull+pop)" if dry_run else "(stashed+pulled)"
            line = f"  ⚠  {name}  main  {suffix}"
        else:
            line = f"  ✓  {name}  main"
            if clean_wts:
                line += "   +  " + "  ".join(clean_wts)
            if dry_run:
                line += "  (would pull)"
        print(line)

        for wt_name in dirty_wts:
            print(f"  ↷  {name}/{wt_name}  (dirty, skipped)")

        for msg in drifted:
            print(f"  ✗  {msg}  (not tracking origin/main — AI-CLI-128)")
            total_drifted += 1

    print(f"\nDone: {total_pulled} pulled, {total_stashed} stashed+pulled, {total_dirty} skipped (dirty)")
    if total_drifted:
        print(f"⚠  {total_drifted} worktree branch(es) not tracking origin/main — see above")
    return 0
