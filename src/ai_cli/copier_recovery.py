"""Verified local snapshots and issue filing for failed isolated template updates."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .git_repair import _git_env


def _git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env=env if env is not None else _git_env(),
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        # External diagnostics can contain credentials in remote URLs.
        raise RuntimeError(f"backup anchor: git {args[0]} failed")
    return result.stdout.strip()


def _template_tip(source: str) -> tuple[str, str]:
    if Path(source).is_dir():
        commit = _git(Path(source), "rev-parse", "--verify", "HEAD^{commit}")
        short = _git(Path(source), "rev-parse", "--short", commit)
    else:
        try:
            result = subprocess.run(
                ["git", "ls-remote", source, "HEAD"],
                capture_output=True,
                text=True,
                env=_git_env(),
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("backup anchor: could not query template HEAD") from exc
        if result.returncode != 0 or not result.stdout.split():
            raise RuntimeError("backup anchor: could not resolve template HEAD")
        commit = result.stdout.split()[0]
        short = commit[:7]
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) or not re.fullmatch(r"[0-9a-f]{4,64}", short):
        raise RuntimeError("backup anchor: invalid template commit")
    return commit, short


def _anchor_failed_update(wt_dir: Path, repo_name: str, source: str) -> tuple[str, str]:
    """Capture final worktree bytes without writing its index or changing its HEAD.

    A copied index retains staged additions, including tracked ignored files.
    ``add -A`` resolves unmerged stages only in that copy using the files on disk.
    An earlier snapshot at the same ref becomes a second parent, so repeated
    failures on the same day retain every snapshot. The ref update uses a CAS.
    """
    template_commit, short = _template_tip(source)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    ref = f"refs/backup/{date}/copier-update/{repo_name}-{short}"
    _git(wt_dir, "check-ref-format", ref)
    previous = _git(wt_dir, "for-each-ref", "--format=%(objectname)", ref)
    if previous:
        _git(wt_dir, "cat-file", "-e", f"{previous}^{{commit}}")
    head = _git(wt_dir, "rev-parse", "--verify", "HEAD")
    index_path = Path(_git(wt_dir, "rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = wt_dir / index_path
    with tempfile.TemporaryDirectory(prefix="ai-copier-index-") as directory:
        index = Path(directory) / "index"
        shutil.copyfile(index_path, index)
        env = {**_git_env(), "GIT_INDEX_FILE": str(index)}
        _git(wt_dir, "add", "-A", env=env)
        tree = _git(wt_dir, "write-tree", env=env)
    parents = ["-p", head]
    if previous and previous != head:
        parents += ["-p", previous]
    commit = _git(wt_dir, "commit-tree", tree, *parents, "-m", "Backup failed template update")
    _git(wt_dir, "update-ref", ref, commit, previous or "")
    if _git(wt_dir, "rev-parse", "--verify", ref) != commit or _git(wt_dir, "rev-parse", f"{ref}^{{tree}}") != tree:
        raise RuntimeError(f"backup anchor verification failed: {ref}")
    return ref, template_commit


def _push_backup_anchor(root: Path, ref: str) -> str:
    """Copy a verified local backup ref to origin, without bypassing hooks.

    Best effort: the fleet pre-push gate runs on every ref, so a project whose
    gate refused the update usually refuses the anchor too. The local ref is
    the durable copy; the returned line says where the anchor lives.
    """
    local = _git(root, "rev-parse", "--verify", ref)
    try:
        _git(root, "push", "origin", f"{ref}:{ref}")
        remote = _git(root, "ls-remote", "--refs", "origin", ref).split()
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return "Backup anchor location: local only (push to origin was refused)"
    if remote != [local, ref]:
        return "Backup anchor location: local only (origin readback did not match)"
    return "Backup anchor location: local and origin"


def _check_recovery_target(root: Path, wt_dir: Path, branch: str, ref: str) -> None:
    """Confirm the actual linked checkout and its saved HEAD before any cleanup."""
    listing = _git(root, "worktree", "list", "--porcelain", "-z")
    expected_branch = f"branch refs/heads/{branch}"
    if not any(
        f"worktree {wt_dir.resolve().as_posix()}" in entry.split("\0") and expected_branch in entry.split("\0")
        for entry in listing.split("\0\0")
    ):
        raise RuntimeError("backup anchor: recovery target is not the expected linked worktree")
    if _git(wt_dir, "rev-parse", "HEAD") != _git(root, "rev-parse", f"{ref}^1"):
        raise RuntimeError("backup anchor: worktree HEAD changed before cleanup")


def _clean_anchored_pushfail(root: Path, wt_dir: Path, branch: str, ref: str) -> None:
    _check_recovery_target(root, wt_dir, branch, ref)
    # Only this run's verified snapshot permits discarding these local changes.
    _git(wt_dir, "reset", "--hard", "HEAD")
    _git(wt_dir, "clean", "-fd")
    if _git(wt_dir, "status", "--porcelain"):
        raise RuntimeError(f"backup anchor {ref}: rejected-push worktree is still dirty")


def _tracker_failure(summary: str, proc: subprocess.CompletedProcess) -> str:
    """``summary`` plus the tracker's exit status and the tail of its stderr, so the cause is visible."""
    tail = " ".join((proc.stderr or "").split())[-300:]
    return f"{summary} (exit {proc.returncode}: {tail})" if tail else f"{summary} (exit {proc.returncode})"


def _file_recovery_issue(
    root: Path,
    ref: str,
    conflicts: list[str],
    template_range: str,
    failure_output: str | None = None,
) -> str:
    """Create once per repo/job, then comment on the matching non-closed issue."""
    # Reuse the existing local-store helper: it strips store overrides and uses
    # sandbox mode, leaving store publication to the caller.
    import json

    from .session_migrate import _bd

    marker = f"[copier-update:{root.name}]"
    title = f"{marker} Resolve failed template update"
    failure_details = (
        f"Push failure output:\n{failure_output}\n"
        if failure_output
        else f"Conflicted paths: {', '.join(conflicts) or '(semantic failure; inspect snapshot)'}\n"
    )
    body = (
        f"Backup anchor: {ref}\n"
        f"{failure_details}"
        f"Template commit range: {template_range}\n"
        "Restore the backup in a separate worktree to resolve and validate the template update."
    )
    listed = _bd(root, "list", "--all", "--json", "--limit", "0")
    if listed.returncode != 0:
        raise RuntimeError(_tracker_failure("could not search the repository issue store", listed))
    try:
        rows = json.loads(listed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("repository issue search returned invalid JSON") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("repository issue search returned no issue list")
    for row in rows:
        if marker in str(row.get("title", "")) and row.get("status") not in ("closed", "tombstone") and row.get("id"):
            issue = str(row["id"])
            commented = _bd(root, "comments", "add", issue, body)
            if commented.returncode != 0:
                raise RuntimeError(_tracker_failure("could not comment on the existing recovery issue", commented))
            return issue
    # The supported creator is user-installed; pathlib keeps its location portable.
    command = Path.home() / ".claude" / "bin" / "bd-new"
    env = {k: v for k, v in os.environ.items() if k not in ("BEADS_DIR", "BEADS_DB")}
    created = subprocess.run(
        [
            str(command),
            "--repo",
            str(root),
            "--title",
            title,
            "--description",
            body,
            "--type",
            "bug",
            "--priority",
            "1",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    if created.returncode != 0:
        raise RuntimeError(_tracker_failure("could not create the repository recovery issue with bd-new", created))
    return created.stdout.strip()
