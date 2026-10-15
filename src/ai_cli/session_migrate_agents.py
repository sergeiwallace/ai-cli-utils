"""Carry a migrated session's agent worktrees and branches in every repo.

A session ``<old>`` that delegates work leaves, in any repository it touched, agent
worktrees under ``<repo>/.worktrees/<old>-agents/<leaf>`` and branches named
``wt/<old>/<leaf>``, checked out there or parked. Both keep naming the old session
after ``ai session-migrate`` renames its slot, so this module moves them too:

* every registered worktree under ``<old>-agents/`` moves to ``<new>-agents/<leaf>``
  with ``git worktree move`` (by hand plus ``git worktree repair`` for one holding
  submodules, :mod:`ai_cli.worktree_move`), and the emptied container is removed;
* every local ``wt/<old>/<leaf>`` branch becomes ``wt/<new>/<leaf>`` through
  :func:`ai_cli.branch_rename.rename_branch_anchored`, so its tip is anchored first;
* every ``branch.*.aih-session-home`` marker naming the old slot or a moved worktree
  is rewritten to the new path, under the branch's new name.

Repositories are the git checkouts directly under ``[project] projects_dir`` plus the
slot's own repo. A worktree a live Claude Code process has its cwd in is left where it
is with its branch, so that process keeps a consistent view; that is not a refusal,
because the process is not the session being renamed. A destination that already
exists is reported and left alone. A git refusal on one entry is a warning and the
rest still move.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .branch_rename import anchor_ref, rename_branch_anchored
from .git_repair import _git_env
from .worktree_move import move_worktree

_SESSION_HOME_PATTERN = r"^branch\..+\.aih-session-home$"
_PROVENANCE_FILE = "aih-provenance.json"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(), check=False)


def renamed_key(key: str, renames: dict[str, str]) -> str:
    """``key`` after ``git branch -m`` of each pair in ``renames``, which moves the branch's config section."""
    for old, new in renames.items():
        prefix = f"branch.{old}."
        if key.startswith(prefix):
            return f"branch.{new}.{key[len(prefix) :]}"
    return key


@dataclass
class AgentEntry:
    """One agent worktree, or one parked branch, of the old session in one repo."""

    repo: Path
    worktree: Path | None
    dest: Path | None
    branch: str | None
    new_branch: str | None
    held_by: int | None = None
    conflict: bool = False
    tip: str = ""
    ownership: WorktreeOwnership | None = None

    @property
    def moves(self) -> bool:
        return self.held_by is None and not self.conflict


@dataclass
class AgentMarker:
    repo: Path
    key: str
    value: str


@dataclass
class WorktreeOwnership:
    """Ownership metadata that follows one moved worktree."""

    repo: Path
    worktree: Path
    dest: Path
    old_owner: str
    new_owner: str
    branch: str | None
    new_branch: str | None
    provenance_state: str
    stamp_value: str | None


@dataclass
class AgentPlan:
    entries: list[AgentEntry] = field(default_factory=list)
    markers: list[AgentMarker] = field(default_factory=list)
    containers: list[Path] = field(default_factory=list)

    def renames(self, repo: Path) -> dict[str, str]:
        """The branch renames planned in ``repo``."""
        return {
            e.branch: e.new_branch for e in self.entries if e.repo == repo and e.moves and e.branch and e.new_branch
        }


def _worktrees(repo: Path) -> list[tuple[Path, str | None]]:
    """``(path, checked-out branch or None)`` for each worktree ``git worktree list`` reports."""
    res = _git(repo, "worktree", "list", "--porcelain")
    found: list[tuple[Path, str | None]] = []
    for line in (res.stdout or "").splitlines() if res.returncode == 0 else []:
        if line.startswith("worktree "):
            found.append((Path(line[len("worktree ") :]).resolve(), None))
        elif line.startswith("branch refs/heads/") and found:
            found[-1] = (found[-1][0], line[len("branch refs/heads/") :])
    return found


def _holder(path: Path, live_cwds: list[tuple[int, Path]]) -> int | None:
    for pid, cwd in live_cwds:
        if cwd == path or path in cwd.parents:
            return pid
    return None


def _provenance(worktree: Path) -> tuple[dict | None, str]:
    """Return a provenance object and ``present``, ``missing``, or ``unreadable``."""
    located = _git(worktree, "rev-parse", "--git-path", _PROVENANCE_FILE)
    if located.returncode != 0 or not located.stdout.strip():
        return None, "unreadable"
    path = Path(located.stdout.strip())
    if not path.is_absolute():
        path = worktree / path
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "unreadable"
    if not isinstance(value, dict) or not isinstance(value.get("owner_session"), str):
        return None, "unreadable"
    return value, "present"


def plan_worktree_ownership(
    repo: Path,
    worktree: Path,
    dest: Path,
    old_owner: str,
    new_owner: str,
    branch: str | None,
    new_branch: str | None,
) -> WorktreeOwnership:
    """Inspect the existing ownership records for one planned worktree move."""
    provenance, state = _provenance(worktree)
    if provenance is not None and provenance["owner_session"] != old_owner:
        state = "different-owner"
    stamp_value = None
    if branch:
        stamp = _git(repo, "config", "--get", f"branch.{branch}.aih-agent-worktree")
        if stamp.returncode == 0:
            stamp_value = stamp.stdout.rstrip("\n")
    return WorktreeOwnership(
        repo,
        worktree,
        dest,
        old_owner,
        new_owner,
        branch,
        new_branch,
        state,
        stamp_value,
    )


def describe_worktree_ownership(plan: WorktreeOwnership) -> list[tuple[str, str, str, str]]:
    """Describe provenance and branch-stamp changes without writing either."""
    lines: list[tuple[str, str, str, str]] = []
    if plan.provenance_state == "present":
        lines.append(
            (
                "provenance owner",
                f"{plan.worktree} [{plan.old_owner}]",
                f"{plan.dest} [{plan.new_owner}]",
                "rewrite",
            )
        )
    elif plan.provenance_state in {"missing", "unreadable"}:
        lines.append(("provenance record", str(plan.worktree), "", f"skipped: {plan.provenance_state}"))
    else:
        lines.append(("provenance record", str(plan.worktree), "", "left alone: owner does not match"))
    if plan.stamp_value is not None and plan.branch:
        target_branch = plan.new_branch or plan.branch
        lines.append(
            (
                "agent-worktree marker",
                f"branch.{plan.branch}.aih-agent-worktree = {plan.stamp_value}",
                f"branch.{target_branch}.aih-agent-worktree = {plan.dest}",
                "rewrite",
            )
        )
    return lines


def _replace_provenance_owner(worktree: Path, old_owner: str, new_owner: str) -> str | None:
    """Atomically replace only ``owner_session``; return a skip/failure reason."""
    record, state = _provenance(worktree)
    if record is None:
        return f"record is {state}"
    if record["owner_session"] != old_owner:
        return "owner no longer matches the old session"
    record["owner_session"] = new_owner
    located = _git(worktree, "rev-parse", "--git-path", _PROVENANCE_FILE)
    if located.returncode != 0 or not located.stdout.strip():
        return "record path is unreadable"
    path = Path(located.stdout.strip())
    if not path.is_absolute():
        path = worktree / path
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if path.read_bytes() != payload:
            return "record read-back did not match"
    except OSError as exc:
        return str(exc)
    finally:
        temporary.unlink(missing_ok=True)
    return None


def apply_worktree_ownership(plan: WorktreeOwnership, branch: str | None) -> list[str]:
    """Apply a planned ownership rebind after its worktree move succeeds."""
    warnings: list[str] = []
    if plan.provenance_state == "present":
        reason = _replace_provenance_owner(plan.dest, plan.old_owner, plan.new_owner)
        if reason:
            warnings.append(f"provenance owner for {plan.dest} was NOT rebound: {reason}")
    if plan.stamp_value is not None and branch:
        key = f"branch.{branch}.aih-agent-worktree"
        written = _git(plan.repo, "config", key, str(plan.dest))
        if written.returncode != 0:
            warnings.append(f"agent-worktree marker {key} FAILED to rewrite: {(written.stderr or '').strip()}")
    return warnings


def _repos(projects_dir: Path, slot_repo: Path) -> list[Path]:
    """The slot's repo, then every directory under ``projects_dir`` that has a ``.worktrees/``."""
    found = [slot_repo]
    if projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            resolved = child.resolve()
            if resolved != slot_repo and (resolved / ".worktrees").is_dir():
                found.append(resolved)
    return found


def plan_agents(
    old: str,
    new: str,
    *,
    projects_dir: Path,
    slot_repo: Path,
    old_wt: Path,
    new_wt: Path,
    live_cwds: list[tuple[int, Path]],
) -> AgentPlan:
    """Plan every agent worktree, branch and marker move; writes nothing."""
    plan = AgentPlan()
    prefix, new_prefix = f"wt/{old}/", f"wt/{new}/"
    for repo in _repos(projects_dir, slot_repo):
        listed = _git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}")
        if listed.returncode != 0:
            continue  # not a git repository
        branches = [b for b in listed.stdout.splitlines() if b.startswith(prefix)]
        container = (repo / ".worktrees" / f"{old}-agents").resolve()
        new_container = container.parent / f"{new}-agents"
        worktrees = _worktrees(repo)
        holding = {branch: path for path, branch in worktrees if branch}

        def _new_branch(branch: str | None) -> str | None:
            return new_prefix + branch[len(prefix) :] if branch and branch.startswith(prefix) else None

        def _branch_exists(branch: str | None, repo: Path = repo) -> bool:
            return (
                bool(branch) and _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
            )

        repo_entries: list[AgentEntry] = []
        for path, branch in worktrees:
            if container not in path.parents:
                continue
            dest = new_container / path.relative_to(container)
            entry = AgentEntry(repo, path, dest, branch, _new_branch(branch))
            if entry.new_branch is None:
                entry.branch = None
            entry.held_by = _holder(path, live_cwds)
            entry.conflict = os.path.lexists(dest) or _branch_exists(entry.new_branch)
            if entry.moves:
                entry.ownership = plan_worktree_ownership(
                    repo,
                    path,
                    dest,
                    old,
                    new,
                    entry.branch,
                    entry.new_branch,
                )
            repo_entries.append(entry)
        attached = {e.branch for e in repo_entries}
        for branch in branches:
            if branch in attached:
                continue
            entry = AgentEntry(repo, None, None, branch, _new_branch(branch))
            elsewhere = holding.get(branch)
            entry.held_by = _holder(elsewhere, live_cwds) if elsewhere else None
            entry.conflict = _branch_exists(entry.new_branch)
            repo_entries.append(entry)
        for entry in repo_entries:
            if entry.branch:
                tip = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{entry.branch}")
                entry.tip = tip.stdout.strip()
        plan.entries += repo_entries
        # An emptied container is planned too: its last worktree may have been reaped before
        # the migration, and nothing else removes a directory still named for the old session.
        if any(e.worktree for e in repo_entries) or container.is_dir():
            plan.containers.append(container)

        moved = {e.worktree: e.dest for e in repo_entries if e.worktree and e.moves}
        markers = _git(repo, "config", "--get-regexp", _SESSION_HOME_PATTERN)
        for line in (markers.stdout or "").splitlines():
            key, _, value = line.partition(" ")
            if not value:
                continue
            target = Path(value).resolve(strict=False)
            if target in moved:
                plan.markers.append(AgentMarker(repo, key, str(moved[target])))
            elif target == old_wt and repo != slot_repo:
                plan.markers.append(AgentMarker(repo, key, str(new_wt)))
    return plan


def describe(plan: AgentPlan, renames: dict[Path, dict[str, str]] | None = None) -> list[tuple[str, str, str, str]]:
    """``(kind, source, dest, action)`` for each planned step, in the order they run."""
    lines: list[tuple[str, str, str, str]] = []
    for e in plan.entries:
        if e.held_by is not None:
            action, dest = f"left alone: held by pid {e.held_by}", ""
        else:
            action = "conflict" if e.conflict else "move"
        if e.worktree:
            dest = "" if e.held_by is not None else str(e.dest)
            lines.append(("agent worktree", str(e.worktree), dest, action))
            if e.ownership:
                lines += describe_worktree_ownership(e.ownership)
        if e.branch:
            dest = "" if e.held_by is not None else str(e.new_branch)
            lines.append(("agent branch", f"{e.branch} ({e.repo})", dest, action))
            if e.moves:
                lines.append(("branch anchor", f"{anchor_ref(e.branch)} ({e.repo})", e.tip, "write"))
    for m in plan.markers:
        key = renamed_key(m.key, (renames or {}).get(m.repo, plan.renames(m.repo)))
        lines.append(("session-home marker", f"{m.key} ({m.repo})", f"{key} = {m.value}", "rewrite"))
    for container in plan.containers:
        lines.append(("agent container", str(container), "", "removed once empty"))
    return lines


def _remove_if_empty(container: Path) -> None:
    """Remove ``container`` and its empty subdirectories; keep anything still holding a file."""
    if not container.is_dir():
        return
    for root, _dirs, _files in os.walk(container, topdown=False):
        with contextlib.suppress(OSError):  # still holds a worktree that was left alone
            Path(root).rmdir()


def apply_agents(plan: AgentPlan) -> tuple[dict[Path, dict[str, str]], list[str]]:
    """Perform ``plan``. Returns the renames that happened, per repo, and the warnings."""
    done: dict[Path, dict[str, str]] = {}
    warnings: list[str] = []
    moved: set[Path] = set()
    for e in [e for e in plan.entries if e.moves]:
        worktree_moved = False
        current_branch = e.branch
        if e.worktree and e.dest:
            e.dest.parent.mkdir(parents=True, exist_ok=True)
            ok, why = move_worktree(e.repo, e.worktree, e.dest)
            if not ok:
                warnings.append(f"agent worktree {e.worktree} FAILED to move: {why}; its branch keeps its name")
                continue
            moved.add(e.worktree)
            worktree_moved = True
            if (e.dest / ".venv").is_dir():
                warnings.append(f"{e.dest / '.venv'} names the old path in its scripts -- run `uv sync --reinstall`")
        if e.branch and e.new_branch:
            renamed = rename_branch_anchored(e.repo, e.branch, e.new_branch)
            if renamed.ok:
                done.setdefault(e.repo, {})[e.branch] = e.new_branch
                current_branch = e.new_branch
            else:
                warnings.append(f"agent branch {e.branch} in {e.repo} FAILED to rename: {renamed.error}")
        if worktree_moved and e.ownership:
            warnings += apply_worktree_ownership(e.ownership, current_branch)
    for m in plan.markers:
        key = renamed_key(m.key, done.get(m.repo, {}))
        res = _git(m.repo, "config", key, m.value)
        if res.returncode != 0:
            warnings.append(f"session-home marker {key} in {m.repo} FAILED to rewrite: {(res.stderr or '').strip()}")
    for container in plan.containers:
        _remove_if_empty(container)
    return done, warnings
