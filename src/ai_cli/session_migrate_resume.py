"""Recovery of a moved session home, using current artifacts rather than completion stamps."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import session_migrate as m
from .venv_relocate import _MAX_BYTES, _candidates, _forms, _pattern

# Include absent optional parts so dry and real runs expose the same inventory.
PARTS = (
    "worktree",
    "name registry",
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
    "held output",
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


def _stale_transcript(path: Path, old_wt: Path, new_wt: Path, old: str) -> bool:
    # Match the existing line rewriters: malformed records remain verbatim.
    # Stream transcripts so an already-complete large conversation needs no full copy.
    with path.open(encoding="utf-8") as source:
        for raw in source:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if (
                row.get("customTitle") == old
                or m._rewrite_line(raw, str(old_wt), str(new_wt)) != raw
                or (row.get("type") == "worktree-state" and row.get("worktreeSession") is not None)
                or (row.get("type") == "relocated" and row.get("relocatedCwd") not in (None, str(new_wt)))
            ):
                return True
    return False


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
        if _stale_transcript(path, old_wt, new_wt, old):
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
    parts["name registry"] = "skipped: direct rename"

    def assign(part: str, status: str):
        def rank(value):
            if value.startswith("failed:"):
                return 5
            if value.startswith("skipped:") and not any(
                word in value for word in ("no matching", "informational", "missing", "unconfigured", "unreadable")
            ):
                return 4
            return {"would do": 3, "done now": 2, "done earlier": 1}.get(value, 0)

        if rank(status) >= rank(parts.get(part, "")):
            parts[part] = status

    for move in result.moves:
        if move.kind in {"migration record", "branch anchor"} or move.kind.endswith("warning"):
            continue
        if move.action == "report" and move.kind.startswith("hook "):
            continue  # Hook status is carried by its explicit planned/applying step.
        if move.action == "already done":
            status = "done earlier"
        elif move.action.startswith(("skipped:", "left alone", "kept", "left in place")) or move.action == "conflict":
            status = f"{'' if move.action.startswith('skipped:') else 'skipped: '}{move.action} {move.dest}".strip()
        elif move.action == "report" and not move.kind.startswith("hook "):
            status = "skipped: informational"
        else:
            status = "would do" if result.dry_run else "done now"
        assign(move.kind, status)
    if result.task_moves:
        assign("task namespace", "would do" if result.dry_run else "done now")
    if result.resume_moves:
        assign("resume", "would do" if result.dry_run else "done now")
    for warning in result.warnings:
        if "FAILED" in warning or "NOT " in warning:
            matched = next((part for part in sorted(parts, key=len, reverse=True) if part in warning), "migration")
            parts[matched] = f"failed: {m._one_line(warning)}"
    for kept in result.kept:
        parts["leftover slot"] = f"failed: {kept}"
    result.parts = parts


def run(repo: Path, old: str, new: str, **kwargs) -> m.MigrationResult:
    repo = repo.resolve()
    state = kwargs.get("state_dir") or m._config.get_xdg_state_home()
    old_wt, new_wt = repo / ".worktrees" / old, repo / ".worktrees" / new
    resumed = not old_wt.exists() and new_wt.exists()
    pair_recorded = kwargs.pop("pair_recorded", False)
    if resumed:
        if not pair_recorded and not proven_pair(repo, old, new, state):
            raise m.MigrationError(f"{new_wt} exists but no durable evidence binds {old} to {new}; refusing recovery")
        call = m.finish_session
        kwargs["record_finish"] = False
        kwargs = {key: value for key, value in kwargs.items() if key != "new_is_reserved"}
    else:
        call = m._start_session
    interrupted = False
    remaining = None
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
                raise m.MigrationError(
                    f"migration stopped before the worktree move: {type(exc).__name__}: {exc}"
                ) from exc
            interrupted = True
            # Inspect what remains after the interrupted write. If that preflight itself
            # refuses, report uncertainty instead of claiming every planned write happened.
            with contextlib.suppress(Exception, KeyboardInterrupt):
                remaining = m.finish_session(
                    repo,
                    old,
                    new,
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key not in {"new_is_reserved", "record_finish", "dry_run"}
                    },
                    dry_run=True,
                    record_finish=False,
                )
            result = preview
            result.dry_run = False
            result.warnings.append(f"migration FAILED: {type(exc).__name__}: {exc}; re-run the same command to finish")
    summarize(result, resumed=resumed)
    if interrupted:
        if remaining is not None:
            summarize(remaining, resumed=True)
            for part, status in remaining.parts.items():
                if status == "would do":
                    result.parts[part] = "failed: pending after interruption; re-run the same command"
        else:
            for part, status in result.parts.items():
                if status == "done now":
                    result.parts[part] = "failed: completion could not be verified after interruption"
        result.parts["worktree"] = "done earlier" if resumed else "done now"
    return result
