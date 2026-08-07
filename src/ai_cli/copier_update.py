"""ai copier-update — propagate project-template changes to downstream projects.

Two modes:

* **isolated** (default, AI-CLI-91) — for each target repo, create a temporary git
  worktree off fresh ``origin/main``, run ``copier update`` there, commit, push
  ``HEAD:main``, sync the main tree, and remove the worktree. All churn is isolated
  from the repo's main working tree, so this is safe to run while other CC sessions
  are actively working in a repo. The temp worktree is left in place only when there
  are merge conflicts or the push fails (never clobbering a session's main tree).

* **direct** (``--no-isolate``) — legacy behaviour: run ``copier update`` straight in
  each repo's main working tree and leave the changes uncommitted for a manual
  commit+push. Fast, but races any active session in that repo. Use only when you
  know no session is working in the target(s).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from .git_repair import _git_env, repair_bare_worktree_config


def _find_copier_projects(projects_dir: Path) -> list[Path]:
    """Return project dirs under projects_dir that use project-template via copier."""
    result = []
    for answers_file in sorted(projects_dir.glob("*/.copier-answers.yml")):
        try:
            with answers_file.open() as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue
        if "project-template" in str(data.get("_src_path", "")):
            result.append(answers_file.parent)
    return result


_EXCLUDE_DIRS = (".venv", "node_modules", "target", ".worktrees", ".git", "__pycache__")


def _changed_paths(porcelain: str, base: Path) -> list[str]:
    """Absolute paths of files reported changed by `git status --porcelain`.

    Handles rename entries (`R  old -> new` keeps the new path) and quoted paths.
    """
    paths = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        rest = line[3:]  # strip the 2-char status field + separating space
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        rest = rest.strip().strip('"')
        if rest:
            paths.append(str(base / rest))
    return paths


def _conflict_files(project_dir: Path, paths: list[str] | None = None) -> list[str]:
    """Return files containing git conflict markers.

    When `paths` is given, only those files are scanned — this is the correct scope
    after a copier run, because a *real* conflict marker only ever appears in a file
    copier itself modified. Scanning the whole tree (paths=None) also matches files
    that merely reference `<<<<<<<` as content (test fixtures, conflict-handling code,
    docs about merges) and reports them as false-positive conflicts.
    """
    if paths is not None:
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            return []
        cmd = ["grep", "-l", "--binary-files=without-match", "<<<<<<<", *existing]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
        return []

    cmd = [
        "grep",
        "-rl",
        "--binary-files=without-match",
        "<<<<<<<",
        str(project_dir),
    ]
    for d in _EXCLUDE_DIRS:
        cmd += ["--exclude-dir", d]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()
    return []


# ---------------------------------------------------------------------------
# Isolated worktree flow (AI-CLI-91)
# ---------------------------------------------------------------------------

# Temp worktree lives under the repo's own (globally-gitignored) .worktrees/ dir so
# it can never collide with a session worktree (`sw-N`) or leak into git status.
_WT_NAME = "copier-update"
_WT_BRANCH = "copier-update-tmp"
_COMMIT_MSG = "chore: copier update from project-template"


def _repo_root(path: Path) -> Path | None:
    """Return the git top-level dir for path, or None if path is not in a git repo."""
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip())


def _cleanup_worktree(root: Path, wt_dir: Path, branch: str) -> None:
    """Remove the temp worktree and its branch (best-effort, idempotent)."""
    # Repair backstop (AI-CLI-99) before AND after the worktree teardown — root
    # is the repo's normal main working tree and must never end up bare / with
    # a stale core.worktree, regardless of what a leaked GIT_* env would do.
    repair_bare_worktree_config(root)
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
        capture_output=True,
        env=_git_env(),
    )
    subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True, env=_git_env())
    subprocess.run(["git", "-C", str(root), "branch", "-D", branch], capture_output=True, env=_git_env())
    repair_bare_worktree_config(root)


def _do_update_in_worktree(wt_dir: Path, root: Path, copier_bin: str, push: bool) -> tuple[str, object]:
    """Run copier + commit/push inside an already-created worktree.

    Returns (status, detail) where status is one of:
      ok        — updated, committed, (pushed) — detail unused
      nochange  — copier produced no changes — detail unused
      conflict  — merge conflicts — detail = list[str] of relative paths
      pushfail  — committed but push rejected — detail = str (git stderr)
      failed    — copier or commit failed — detail = str (message)
    """
    cu = subprocess.run(
        [copier_bin, "update", "--defaults", "--trust", "--vcs-ref", "HEAD"],
        cwd=wt_dir,
        capture_output=True,
        text=True,
    )
    if cu.returncode != 0:
        return "failed", (cu.stderr.strip() or "copier update failed")

    status = subprocess.run(
        ["git", "-C", str(wt_dir), "status", "--porcelain"],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if not status.stdout.strip():
        return "nochange", ""

    # Only files copier actually touched can hold a real conflict marker — scoping the
    # scan to them avoids false positives from files that merely reference `<<<<<<<`.
    conflicts = _conflict_files(wt_dir, _changed_paths(status.stdout, wt_dir))
    if conflicts:
        rels = [str(Path(c).relative_to(wt_dir)) for c in conflicts]
        return "conflict", rels

    subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True, env=_git_env())
    commit = subprocess.run(
        ["git", "-C", str(wt_dir), "commit", "-m", _COMMIT_MSG],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if commit.returncode != 0:
        return "failed", (commit.stderr.strip() or "commit failed")

    if push:
        pr = subprocess.run(
            ["git", "-C", str(wt_dir), "push", "origin", "HEAD:main"],
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        if pr.returncode != 0:
            return "pushfail", (pr.stderr.strip() or "push failed")
        # Keep the repo's main working tree in sync with what we just shipped.
        subprocess.run(["git", "-C", str(root), "pull", "--rebase"], capture_output=True, env=_git_env())
        repair_bare_worktree_config(root)

    return "ok", ""


def _update_one_isolated(project_dir: Path, copier_bin: str, push: bool = True) -> tuple[str, object]:
    """Update one repo in an isolated temp worktree. Returns (status, detail).

    On ok/nochange/failed the temp worktree is removed. On conflict/pushfail it is
    left in place (detail carries the info needed to resolve it manually).
    """
    root = _repo_root(project_dir)
    if root is None:
        return "failed", "not a git repository"

    wt_dir = root / ".worktrees" / _WT_NAME
    branch = _WT_BRANCH

    # Clear any leftover temp worktree/branch from a prior interrupted run.
    _cleanup_worktree(root, wt_dir, branch)

    # Base the worktree on fresh origin/main so we propagate onto the shipped tip,
    # not whatever the local main tree happens to be at. Fall back to HEAD offline.
    subprocess.run(["git", "-C", str(root), "fetch", "origin", "main"], capture_output=True, env=_git_env())
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "origin/main"],
        capture_output=True,
        env=_git_env(),
    )
    base = "origin/main" if probe.returncode == 0 else "HEAD"

    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    repair_bare_worktree_config(root)
    add = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", str(wt_dir), "-b", branch, base],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    repair_bare_worktree_config(root)
    if add.returncode != 0:
        _cleanup_worktree(root, wt_dir, branch)
        return "failed", f"worktree add failed: {add.stderr.strip()}"

    status, detail = _do_update_in_worktree(wt_dir, root, copier_bin, push)

    # Only tear down the temp worktree when there is nothing left to hand off.
    if status in ("ok", "nochange", "failed"):
        _cleanup_worktree(root, wt_dir, branch)

    return status, detail


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_copier_update(
    projects_dir: Path | None = None,
    project_filter: str | None = None,
    dry_run: bool = False,
    isolate: bool = True,
    push: bool = True,
) -> int:
    """Run copier update across all matching projects. Returns exit code (0 = success).

    isolate=True (default) runs each repo in a throwaway worktree and ships to main;
    isolate=False runs copier directly in each repo's main tree (legacy, unsafe while
    sessions are active).
    """
    if projects_dir is None:
        projects_dir = Path.home() / "projects"

    if not projects_dir.exists():
        print(f"Error: projects directory not found: {projects_dir}", file=sys.stderr)
        return 1

    copier_bin = shutil.which("copier")
    if copier_bin is None:
        print(
            "Error: copier not found in PATH. Install with: uv tool install copier",
            file=sys.stderr,
        )
        return 1

    projects = _find_copier_projects(projects_dir)
    if not projects:
        print("No project-template-based projects found.")
        return 0

    if project_filter:
        projects = [p for p in projects if p.name == project_filter]
        if not projects:
            print(
                f"Error: project '{project_filter}' not found or not copier-managed.",
                file=sys.stderr,
            )
            return 1

    if dry_run:
        mode = "isolated worktree → main" if isolate else "direct (main tree)"
        print(f"Would update {len(projects)} project(s) [{mode}]:")
        for p in projects:
            print(f"  {p.name}")
        print("\n(dry-run: no changes made)")
        return 0

    if isolate:
        return _run_isolated(projects, copier_bin, push)
    return _run_direct(projects, copier_bin)


def _run_isolated(projects: list[Path], copier_bin: str, push: bool) -> int:
    """Isolated-worktree flow (AI-CLI-91). Returns exit code."""
    print(f"Updating {len(projects)} project(s) [isolated worktree → main]:\n")
    failed = 0
    changed = 0
    for project_dir in projects:
        print(f"  {project_dir.name}... ", end="", flush=True)
        status, detail = _update_one_isolated(project_dir, copier_bin, push=push)
        if status == "ok":
            print("✓ updated + pushed" if push else "✓ updated (committed, not pushed)")
            changed += 1
        elif status == "nochange":
            print("· no changes")
        elif status == "conflict":
            print(f"✗ CONFLICTS ({len(detail)} file(s)) — resolve in temp worktree, then merge:")
            for rel in detail:
                print(f"    conflict: {rel}")
            failed += 1
        elif status == "pushfail":
            print("✗ PUSH FAILED — commit is ready in the temp worktree; push manually")
            print(f"    {detail}")
            failed += 1
        else:  # failed
            print("✗ FAILED")
            print(f"    {detail}")
            failed += 1

    print()
    if failed:
        print(f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        print(f"All projects up to date ({changed} updated).")
    return 1 if failed else 0


def _run_direct(projects: list[Path], copier_bin: str) -> int:
    """Legacy direct-to-main-tree flow (--no-isolate). Returns exit code."""
    print(f"Updating {len(projects)} project(s) [direct — main tree]:\n")
    failed = 0
    for project_dir in projects:
        print(f"  {project_dir.name}... ", end="", flush=True)
        result = subprocess.run(
            # --vcs-ref HEAD: use latest commit, not latest tag. Without this,
            # copier compares _commit in .copier-answers.yml against the latest
            # git tag and sees nothing to update if template has untagged commits.
            [copier_bin, "update", "--defaults", "--trust", "--vcs-ref", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("✗ FAILED")
            if result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    print(f"    {line}", file=sys.stderr)
            failed += 1
            continue

        porcelain = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        conflicts = _conflict_files(project_dir, _changed_paths(porcelain.stdout, project_dir))
        if conflicts:
            print(f"✗ CONFLICTS ({len(conflicts)} file(s))")
            for c in conflicts:
                print(f"    conflict: {c}")
            failed += 1
        else:
            print("✓")

    print()
    if failed:
        print(f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        print("All projects updated successfully.")
    return 1 if failed else 0
