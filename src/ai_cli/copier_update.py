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
import tempfile
from pathlib import Path

import yaml

from .git_repair import _git_env, repair_bare_worktree_config

EX_CONFIG = 78
EX_TEMPFAIL = 75
EX_PARTIAL_MUTATION = 3


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
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()
    return []


def _load_answers(answers_file: Path) -> dict[str, object] | None:
    """Load Copier answers, returning None for missing or malformed files."""
    try:
        with answers_file.open() as file:
            data = yaml.safe_load(file)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_copier_source(project_dir: Path, answers: dict[str, object]) -> str | None:
    """Resolve a local Copier source before changing the working directory."""
    source = answers.get("_src_path")
    if not isinstance(source, str) or not source:
        return None
    if source.startswith(("git@", "http://", "https://", "ssh://", "file://")):
        return source
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = project_dir / source_path
    return str(source_path.resolve())


def _restore_answer_source(
    answers_file: Path, source: object, original_answers: dict[str, object], original_text: str
) -> bool:
    """Keep the stored source spelling stable after using an absolute local source."""
    answers = _load_answers(answers_file)
    if answers is None:
        return False
    answers["_src_path"] = source
    if answers == original_answers:
        answers_file.write_text(original_text)
    else:
        answers_file.write_text(yaml.safe_dump(answers, sort_keys=False))
    return True


def _answer_values_preserved(before: dict[str, object], after: dict[str, object]) -> bool:
    """All previously stored project answers must survive an update unchanged."""
    return all(after.get(key) == value for key, value in before.items() if not key.startswith("_"))


def _is_semantic_update_failure(error: str) -> bool:
    """Whether an error means Copier changed files but the result is unsafe to commit."""
    return error == "stored Copier answers were not preserved"


def _path_candidates(path: str) -> set[str]:
    """Return Copier template and rendered destination spellings for a path."""
    candidates = {path}
    if path.endswith(".jinja"):
        candidates.add(path.removesuffix(".jinja"))
    return candidates


def _parse_diff_hunks(diff: str) -> tuple[set[str], set[tuple[str, tuple[str, ...], tuple[str, ...]]]]:
    """Extract changed paths and zero-context hunks from a Git diff."""
    paths: set[str] = set()
    hunks: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    path: str | None = None
    removed: list[str] = []
    added: list[str] = []

    def finish_hunk() -> None:
        if path is not None and (removed or added):
            hunks.add((path, tuple(removed), tuple(added)))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish_hunk()
            removed = []
            added = []
            parts = line.split(" ", 3)
            path = parts[3].removeprefix("b/") if len(parts) == 4 else None
            if path:
                paths.add(path)
        elif line.startswith("@@ "):
            finish_hunk()
            removed = []
            added = []
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    finish_hunk()
    return paths, hunks


def _template_diff(
    source: str, previous_commit: str
) -> tuple[set[str], set[tuple[str, tuple[str, ...], tuple[str, ...]]]] | None:
    """Return the template changes Copier should apply from previous_commit to HEAD."""
    source_path = Path(source)
    with tempfile.TemporaryDirectory(prefix="ai-copier-template-") as temporary_dir:
        if source_path.is_dir():
            template_dir = source_path
        else:
            template_dir = Path(temporary_dir) / "template"
            clone = subprocess.run(
                ["git", "clone", "--quiet", source, str(template_dir)],
                capture_output=True,
                text=True,
                env=_git_env(),
                check=False,
            )
            if clone.returncode != 0:
                return None
        result = subprocess.run(
            ["git", "-C", str(template_dir), "diff", "--no-ext-diff", "--unified=0", previous_commit, "HEAD", "--"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    if result.returncode != 0:
        return None
    return _parse_diff_hunks(result.stdout)


def _verify_update_parity(
    project_dir: Path,
    porcelain: str,
    template_paths: set[str],
    template_hunks: set[tuple[str, tuple[str, ...], tuple[str, ...]]],
) -> str | None:
    """Return a parity error when Copier omitted a template file or static hunk."""
    changed_paths = {
        str(Path(path).relative_to(project_dir))
        for path in _changed_paths(porcelain, project_dir)
        if Path(path).is_relative_to(project_dir)
    }
    expected_paths = {
        path for path in template_paths if path not in {"copier.yml", "copier.yaml"} and not path.startswith(".copier-")
    }
    missing_paths = sorted(path for path in expected_paths if not (_path_candidates(path) & changed_paths))
    if missing_paths:
        return f"template parity failed: missing changed file(s): {', '.join(missing_paths)}"

    actual = subprocess.run(
        ["git", "-C", str(project_dir), "diff", "--no-ext-diff", "--unified=0", "--"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if actual.returncode != 0:
        return "template parity failed: could not inspect applied changes"
    _, actual_hunks = _parse_diff_hunks(actual.stdout)
    untracked_paths = {line[3:].strip().strip('"') for line in porcelain.splitlines() if line.startswith("?? ")}

    def matches_untracked_file(hunk: tuple[str, tuple[str, ...], tuple[str, ...]]) -> bool:
        for path in _path_candidates(hunk[0]) & untracked_paths:
            target = project_dir / path
            if target.is_file():
                contents = target.read_text()
                if all(line in contents.splitlines() for line in hunk[2]):
                    return True
        return False

    expected_static_hunks = {
        hunk
        for hunk in template_hunks
        if "{{" not in "\n".join((*hunk[1], *hunk[2])) and "{%" not in "\n".join((*hunk[1], *hunk[2]))
    }
    missing_hunks = [
        hunk
        for hunk in expected_static_hunks
        if not any(
            _path_candidates(hunk[0]) & _path_candidates(actual_hunk[0]) and hunk[1:] == actual_hunk[1:]
            for actual_hunk in actual_hunks
        )
        and not matches_untracked_file(hunk)
    ]
    if missing_hunks:
        return "template parity failed: one or more static template hunks were not applied"
    return None


def _run_copier_update(
    project_dir: Path, copier_bin: str, resolved_source: str | None = None
) -> tuple[
    str | None, dict[str, object] | None, tuple[set[str], set[tuple[str, tuple[str, ...], tuple[str, ...]]]] | None
]:
    """Run Copier with stored answers and return any safety failure."""
    answers_file = project_dir / ".copier-answers.yml"
    answers_before = _load_answers(answers_file)
    if answers_before is None:
        return "could not read .copier-answers.yml", None, None
    original_answers = answers_before.copy()
    original_text = answers_file.read_text()
    source = resolved_source or _resolve_copier_source(project_dir, answers_before)
    previous_commit = answers_before.get("_commit")
    if source is None or not isinstance(previous_commit, str) or not previous_commit:
        return "could not resolve Copier source and previous template commit", None, None
    template_changes = _template_diff(source, previous_commit)
    if template_changes is None:
        return "could not inspect template diff for parity verification", None, None

    original_source = original_answers["_src_path"]
    update_answers = original_answers.copy()
    update_answers["_src_path"] = source
    answers_file.write_text(yaml.safe_dump(update_answers, sort_keys=False))
    failure: str | None = None
    try:
        result = subprocess.run(
            [
                copier_bin,
                "update",
                "--defaults",
                "--data-file",
                str(answers_file),
                "--trust",
                "--vcs-ref",
                "HEAD",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failure = result.stderr.strip() or "copier update failed"
        else:
            answers_after = _load_answers(answers_file)
            if answers_after is None or not _answer_values_preserved(original_answers, answers_after):
                failure = "stored Copier answers were not preserved"
    finally:
        if not _restore_answer_source(answers_file, original_source, original_answers, original_text):
            failure = "could not restore stored Copier source"
    if failure is not None:
        return failure, None, None
    return None, original_answers, template_changes


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
        check=False,
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
        check=False,
    )
    subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True, env=_git_env(), check=False)
    subprocess.run(["git", "-C", str(root), "branch", "-D", branch], capture_output=True, env=_git_env(), check=False)
    repair_bare_worktree_config(root)


def _do_update_in_worktree(
    wt_dir: Path, root: Path, copier_bin: str, push: bool, resolved_source: str | None = None
) -> tuple[str, str | list[str]]:
    """Run copier + commit/push inside an already-created worktree.

    Returns (status, detail) where status is one of:
      ok        — updated, committed, (pushed) — detail unused
      nochange  — copier produced no changes — detail unused
      conflict  — merge conflicts — detail = list[str] of relative paths
      parityfail — a template file or hunk did not land — detail = str (message)
      pushfail  — committed but push rejected — detail = str (git stderr)
      failed    — copier or commit failed — detail = str (message)
    """
    update_error, _, template_changes = _run_copier_update(wt_dir, copier_bin, resolved_source)
    if update_error is not None:
        if _is_semantic_update_failure(update_error):
            return "parityfail", update_error
        # A copier invocation failure is retried as transient; the isolated target is discarded.
        return "failed", update_error

    status = subprocess.run(
        ["git", "-C", str(wt_dir), "status", "--porcelain"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if not status.stdout.strip():
        return "nochange", ""

    # Only files copier actually touched can hold a real conflict marker — scoping the
    # scan to them avoids false positives from files that merely reference `<<<<<<<`.
    conflicts = _conflict_files(wt_dir, _changed_paths(status.stdout, wt_dir))
    if conflicts:
        rels = [str(Path(c).relative_to(wt_dir)) for c in conflicts]
        return "conflict", rels

    assert template_changes is not None
    parity_error = _verify_update_parity(wt_dir, status.stdout, *template_changes)
    if parity_error is not None:
        return "parityfail", parity_error

    subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True, env=_git_env(), check=False)
    commit = subprocess.run(
        ["git", "-C", str(wt_dir), "commit", "-m", _COMMIT_MSG],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if commit.returncode != 0:
        # A commit failure is retried as transient; the isolated target is discarded.
        return "failed", (commit.stderr.strip() or "commit failed")

    if push:
        pr = subprocess.run(
            ["git", "-C", str(wt_dir), "push", "origin", "HEAD:main"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        if pr.returncode != 0:
            return "pushfail", (pr.stderr.strip() or "push failed")
        # Keep the repo's main working tree in sync with what we just shipped.
        subprocess.run(["git", "-C", str(root), "pull", "--rebase"], capture_output=True, env=_git_env(), check=False)
        repair_bare_worktree_config(root)

    return "ok", ""


def _update_one_isolated(project_dir: Path, copier_bin: str, push: bool = True) -> tuple[str, str | list[str]]:
    """Update one repo in an isolated temp worktree. Returns (status, detail).

    On ok/nochange/failed the temp worktree is removed. On conflict/pushfail it is
    left in place (detail carries the info needed to resolve it manually).
    """
    root = _repo_root(project_dir)
    if root is None:
        # This is a deterministic prerequisite failure: the project is not a Git repository.
        return "failed", "not a git repository"

    project_answers = _load_answers(project_dir / ".copier-answers.yml")
    if project_answers is None:
        return "failed", "could not read .copier-answers.yml"
    resolved_source = _resolve_copier_source(project_dir, project_answers)
    if resolved_source is None:
        return "failed", "could not resolve Copier source before creating worktree"

    wt_dir = root / ".worktrees" / _WT_NAME
    branch = _WT_BRANCH

    # Clear any leftover temp worktree/branch from a prior interrupted run.
    _cleanup_worktree(root, wt_dir, branch)

    # Base the worktree on fresh origin/main so we propagate onto the shipped tip,
    # not whatever the local main tree happens to be at. Fall back to HEAD offline.
    subprocess.run(
        ["git", "-C", str(root), "fetch", "origin", "main"], capture_output=True, env=_git_env(), check=False
    )
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "origin/main"],
        capture_output=True,
        env=_git_env(),
        check=False,
    )
    base = "origin/main" if probe.returncode == 0 else "HEAD"

    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    repair_bare_worktree_config(root)
    add = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", str(wt_dir), "-b", branch, base],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    repair_bare_worktree_config(root)
    if add.returncode != 0:
        _cleanup_worktree(root, wt_dir, branch)
        # The required isolated worktree cannot be created, so retrying without repair will not help.
        return "failed", f"worktree add failed: {add.stderr.strip()}"

    status, detail = _do_update_in_worktree(wt_dir, root, copier_bin, push, resolved_source)

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
        return EX_CONFIG

    copier_bin = shutil.which("copier")
    if copier_bin is None:
        print(
            "Error: copier not found in PATH. Install with: uv tool install copier",
            file=sys.stderr,
        )
        return EX_CONFIG

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
            return EX_CONFIG

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
    has_partial_mutation = False
    has_config_failure = False
    has_transient_failure = False
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
            has_partial_mutation = True
        elif status == "parityfail":
            print("✗ TEMPLATE PARITY FAILED — inspect the temp worktree before continuing")
            print(f"    {detail}")
            failed += 1
            has_partial_mutation = True
        elif status == "pushfail":
            print("✗ PUSH FAILED — commit is ready in the temp worktree; push manually")
            print(f"    {detail}")
            failed += 1
            has_partial_mutation = True
        else:  # failed
            print("✗ FAILED")
            print(f"    {detail}")
            failed += 1
            if detail == "not a git repository" or (
                isinstance(detail, str) and detail.startswith("worktree add failed:")
            ):
                has_config_failure = True
            else:
                has_transient_failure = True

    print()
    if failed:
        print(f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        print(f"All projects up to date ({changed} updated).")
    if has_partial_mutation:
        return EX_PARTIAL_MUTATION
    if has_config_failure:
        return EX_CONFIG
    if has_transient_failure:
        return EX_TEMPFAIL
    return 0


def _run_direct(projects: list[Path], copier_bin: str) -> int:
    """Legacy direct-to-main-tree flow (--no-isolate). Returns exit code."""
    print(f"Updating {len(projects)} project(s) [direct — main tree]:\n")
    failed = 0
    has_partial_mutation = False
    has_transient_failure = False
    for project_dir in projects:
        print(f"  {project_dir.name}... ", end="", flush=True)
        update_error, _, template_changes = _run_copier_update(project_dir, copier_bin)
        if update_error is not None:
            semantic_failure = _is_semantic_update_failure(update_error)
            if semantic_failure:
                print("✗ TEMPLATE PARITY FAILED")
                has_partial_mutation = True
            else:
                print("✗ FAILED")
            if update_error:
                for line in update_error.splitlines():
                    print(f"    {line}", file=sys.stderr)
            failed += 1
            if not semantic_failure:
                has_transient_failure = True
            continue

        porcelain = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
        conflicts = _conflict_files(project_dir, _changed_paths(porcelain.stdout, project_dir))
        if conflicts:
            # In --no-isolate mode, unresolved markers mutate the live main tree, not a temp worktree.
            print(f"✗ CONFLICTS ({len(conflicts)} file(s))")
            for c in conflicts:
                print(f"    conflict: {c}")
            failed += 1
            has_partial_mutation = True
        else:
            assert template_changes is not None
            parity_error = _verify_update_parity(project_dir, porcelain.stdout, *template_changes)
            if parity_error is not None:
                print("✗ TEMPLATE PARITY FAILED")
                print(f"    {parity_error}")
                failed += 1
                has_partial_mutation = True
            else:
                print("✓")

    print()
    if failed:
        print(f"{failed} project(s) had errors or conflicts — resolve before continuing.")
    else:
        print("All projects updated successfully.")
    if has_partial_mutation:
        return EX_PARTIAL_MUTATION
    if has_transient_failure:
        return EX_TEMPFAIL
    return 0
