"""Recovery of a moved session home, using current artifacts rather than completion stamps."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import session_migrate as m
from .venv_relocate import _MAX_BYTES, _candidates, _forms, _pattern

# Include absent optional parts so dry and real runs expose the same inventory.
PARTS = (
    "worktree",
    "virtual environment",
    "branch",
    "canonical registry",
    "provenance owner",
    "agent-worktree marker",
    "session-home marker",
    "agent worktree",
    "agent branch",
    "project dir",
    "transcript title",
    "held-output",
    "memory file",
    "memory index",
    "task namespace",
    "resume",
    "session meta",
    "wrapper script",
    "session state",
    "session map",
    "iTerm2 restore record",
    "launch log",
    "chief registration",
    "watcher lock",
    "slot lock",
    "leftover slot",
    "agent container",
    "beads issue",
)


@dataclass
class HomePlan:
    moves: list[m.PlannedMove] = field(default_factory=list)
    actions: list[Callable[[], object]] = field(default_factory=list)


def plan_home(repo: Path, old_wt: Path, new_wt: Path, old: str, new: str, home: Path) -> HomePlan:
    """Plan the steps a worktree move may have preceded, without changing any files."""
    plan = HomePlan()

    def add(kind, source, dest, action):
        plan.moves.append(m.PlannedMove(kind, str(source), str(dest), "rewrite"))
        plan.actions.append(action)

    old_branch, new_branch = f"wt-{old}", f"wt-{new}"
    branch = m.rename_branch_anchored(repo, old_branch, new_branch, dry_run=True)
    if branch.tip:
        if not branch.ok:
            raise m.MigrationError(f"branch recovery refused: {branch.error}")

        def rename():
            done = m.rename_branch_anchored(repo, old_branch, new_branch)
            if not done.ok:
                raise m.MigrationError(f"branch recovery failed: {done.error}")

        add("branch", old_branch, new_branch, rename)
    if m.move_canonical_worktree(old_wt, new_wt, old, new, dry_run=True):
        add(
            "canonical registry",
            old_wt,
            new_wt,
            lambda: m.move_canonical_worktree(old_wt, new_wt, old, new, dry_run=False),
        )
    ownership = m._agents.plan_worktree_ownership(
        repo, new_wt, new_wt, old, new, old_branch if branch.tip else new_branch, new_branch
    )
    if ownership.provenance_state == "present" or (ownership.stamp_value and ownership.stamp_value != str(new_wt)):
        plan.moves += [m.PlannedMove(*line) for line in m._agents.describe_worktree_ownership(ownership)]
        plan.actions.append(lambda: _ownership(ownership, new_branch))
    venv = new_wt / ".venv"
    pattern = _pattern(_forms(old_wt))
    for path in _candidates(venv):
        if path.stat().st_size <= _MAX_BYTES:
            data = path.read_bytes()
            if b"\0" not in data and pattern.search(data):
                add("virtual environment", path, new_wt, lambda: m.relocate_venv(venv, old_wt, new_wt))
                break
    old_pd, new_pd = m.cc_project_dir(old_wt, home), m.cc_project_dir(new_wt, home)
    if old_pd.exists() and new_pd.exists():
        raise m.MigrationError(f"both project directories exist: {old_pd} and {new_pd}; refusing to merge")
    if old_pd.is_dir():
        add("project dir", old_pd, new_pd, lambda: old_pd.rename(new_pd))
    project = old_pd if old_pd.is_dir() else new_pd
    for path in sorted(project.glob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        stale = (
            old in m.transcript_titles(path)
            or any(m._rewrite_line(line, str(old_wt), str(new_wt)) != line for line in text.splitlines(keepends=True))
            or any(row.get("type") == "worktree-state" and row.get("worktreeSession") is not None for row in records)
        )
        if stale:
            dest = new_pd / path.name

            def rewrite(dest=dest):
                m._rewrite_transcript_cwd(dest, str(old_wt), str(new_wt))
                if old in m.transcript_titles(dest):
                    m.retitle_transcript(dest, old, new)
                m.neutralise_worktree_state(dest, new_wt)

            add("transcript title", path, dest, rewrite)
    return plan


def _ownership(plan, branch):
    warnings = m._agents.apply_worktree_ownership(plan, branch)
    if warnings:
        raise m.MigrationError("; ".join(warnings))


def apply_home(plan: HomePlan, result: m.MigrationResult) -> None:
    for action in plan.actions:
        action()


def proven_pair(repo: Path, old: str, new: str, state: Path) -> bool:
    """A recorded pair or OLD's branch checked out at NEW proves this is recovery, not a collision."""
    if any(
        row.get("old") == old and row.get("new") == new and row.get("repo") == str(repo)
        for row in m._ledger_rows(state)
    ):
        return True
    return m._git(repo / ".worktrees" / new, "symbolic-ref", "--short", "HEAD").stdout.strip() == f"wt-{old}"


def summarize(result: m.MigrationResult, *, resumed: bool) -> None:
    parts = dict.fromkeys(PARTS, "done earlier" if resumed else "skipped: no matching artifact")
    for move in result.moves:
        if move.kind == "migration record" or move.kind == "branch anchor":
            continue
        if move.action == "already done":
            status = "done earlier"
        elif move.action.startswith(("skipped:", "left alone", "kept", "left in place")) or move.action == "conflict":
            status = f"skipped: {move.action} {move.dest}".strip()
        elif move.action == "report" and not move.kind.startswith("hook "):
            status = "skipped: informational"
        else:
            status = "would do" if result.dry_run else "done now"
        parts[move.kind] = status
    if result.task_moves:
        parts["task namespace"] = "would do" if result.dry_run else "done now"
    if result.resume_moves:
        parts["resume"] = "would do" if result.dry_run else "done now"
    for warning in result.warnings:
        if "FAILED" in warning or "NOT " in warning:
            matched = next((part for part in parts if part in warning), "migration")
            parts[matched] = f"failed: {m._one_line(warning)}"
    for kept in result.kept:
        parts["leftover slot"] = f"failed: {kept}"
    result.parts = parts


def run(repo: Path, old: str, new: str, **kwargs) -> m.MigrationResult:
    repo = repo.resolve()
    state = kwargs.get("state_dir") or m._config.get_xdg_state_home()
    old_wt, new_wt = repo / ".worktrees" / old, repo / ".worktrees" / new
    resumed = not old_wt.exists() and new_wt.exists()
    if resumed:
        if not proven_pair(repo, old, new, state):
            raise m.MigrationError(f"{new_wt} exists but no durable evidence binds {old} to {new}; refusing recovery")
        call = m.finish_session
        kwargs["record_finish"] = False
        kwargs = {key: value for key, value in kwargs.items() if key != "new_is_reserved"}
    else:
        call = m._start_session
    if kwargs.get("dry_run"):
        result = call(repo, old, new, **kwargs)
    else:
        # Freeze the read-only plan before any writes, including hook preflights.
        preview = call(repo, old, new, **{**kwargs, "dry_run": True})
        try:
            if resumed and not m.migration_recorded(old, new, state):
                m._record_migration(state, old, new, repo, new_wt)
            result = call(repo, old, new, **kwargs)
        except (Exception, KeyboardInterrupt) as exc:
            if not new_wt.exists():
                raise
            result = preview
            result.dry_run = False
            result.warnings.append(f"migration FAILED: {type(exc).__name__}: {exc}; re-run the same command to finish")
    summarize(result, resumed=resumed)
    return result
