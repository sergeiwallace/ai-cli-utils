"""Whether a recorded migration is finished: a read-only re-plan of the pair finds nothing left.

The planner that does the work also judges it. :func:`replan` runs ``--finish`` as a dry run
for the pair and :func:`parts_left` reads its parts table, so a writing run, ``ai
migrate-session --status``, the ``--finish --all`` sweep and the launch guard all ask the same
question of the disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import session_migrate as m
from .session_migrate_resume import summarize

#: Every status a part of a re-plan may read and still count as done: finished earlier, or a
#: skip that says there is nothing of that kind to carry. A hook that reports
#: ``migrate-status: done`` reads ``done earlier``. Anything else, an unreadable store or a
#: hook with no status line included, is work left.
DONE_STATUSES = frozenset(
    {
        "done earlier",
        "skipped: no matching artifact",
        "skipped: informational",
        "skipped: unconfigured",
        "skipped: missing",
        "skipped: direct rename",
    }
)

#: The part a re-plan that could not run is reported under.
UNPLANNED = "migration"

_REPLAN_KWARGS = frozenset({"claude_home", "state_dir", "proc_dir", "tmux_names", "projects_dir", "regenerate_script"})


def parts_left(result: m.MigrationResult) -> dict[str, str]:
    """``{part: status}`` for each part of the dry-run ``result`` that is not done."""
    if not result.parts:
        summarize(result, resumed=True)
    return {part: status for part, status in result.parts.items() if status.strip() not in DONE_STATUSES}


def replan(repo_root: Path, old: str, new: str, **kwargs: Any) -> dict[str, str]:
    """The parts a read-only ``--finish`` plan of ``old -> new`` in ``repo_root`` still finds.

    A plan that refuses or raises is one part left, :data:`UNPLANNED`, carrying the reason:
    a migration is never called finished on a part nobody could read.
    """
    return replan_live(repo_root, old, new, **kwargs)[0]


def replan_live(repo_root: Path, old: str, new: str, **kwargs: Any) -> tuple[dict[str, str], str]:
    """:func:`replan`, and the live-session refusal a writing run would raise, or ``""``."""
    passed = {key: value for key, value in kwargs.items() if key in _REPLAN_KWARGS}
    try:
        plan = m.finish_session(repo_root, old, new, dry_run=True, record_finish=False, **passed)
    except Exception as exc:  # any failure to plan leaves the pair unverified
        return {UNPLANNED: f"failed: could not re-plan: {m._one_line(str(exc)) or type(exc).__name__}"}, ""
    return parts_left(plan), plan.live


def _bookkeeping(row: dict) -> bool:
    """Whether a ledger row only notes a delivered context or the transcripts a run recorded.

    Neither is a migration, so neither makes a pair pending, as
    :func:`ai_cli.session_migrate.pending_finishes` also reads them.
    """
    return row.get("context_announced") is True or row.get("transcripts_recorded") is True


def finish_state(old: str, new: str, state: Path) -> str | None:
    """The ledger's word on the pair: ``verified``, ``unverified``, ``pending``, or ``None`` if unrecorded.

    The latest row decides: a migration row makes the pair ``pending`` again, and a
    ``finished`` row is ``verified`` only when it carries ``verified: true``, which rows
    written before the re-plan rule never do.
    """
    found: str | None = None
    for row in m._ledger_rows(state):
        if (row.get("old"), row.get("new")) != (old, new) or _bookkeeping(row):
            continue
        if row.get("finished") is True:
            found = "verified" if row.get("verified") is True else "unverified"
        else:
            found = "pending"
    return found


def recorded_pairs(new: str, state: Path) -> list[tuple[str, Path]]:
    """``(old, repo)`` for every migration to ``new`` the ledger records, whatever its latest row says.

    The repo is the latest one a row of the pair names. A row without a ``repo`` names no
    slot to judge and is skipped, as :func:`ai_cli.session_migrate.pending_finishes` skips it.
    """
    pairs: dict[str, Path] = {}
    for row in m._ledger_rows(state):
        old, repo = row.get("old"), row.get("repo")
        if row.get("new") != new or _bookkeeping(row):
            continue
        if isinstance(old, str) and isinstance(repo, str):
            pairs[old] = Path(repo)
    return list(pairs.items())


def newest_format(new: str, state: Path) -> int:
    """The highest migration record format any ledger row naming ``new`` carries; 0 when none does."""
    found = 0
    for row in m._ledger_rows(state):
        value = row.get("format")
        if new in (row.get("old"), row.get("new")) and isinstance(value, int) and not isinstance(value, bool):
            found = max(found, value)
    return found


def left_after(result: m.MigrationResult) -> dict[str, str]:
    """``{part: status}`` for each part a writing run left undone; empty when it did not fail."""
    if not result.failed:
        return {}
    if not result.parts:
        summarize(result, resumed=True)
    left = {part: status for part, status in result.parts.items() if status.startswith("failed:")}
    return left or {UNPLANNED: f"failed: {m._one_line('; '.join([*result.kept, *result.warnings])) or 'not finished'}"}


def finish_command(old: str, new: str, repo: Path) -> str:
    return f"ai migrate-session {old} {new} --finish -r {repo}"


def verify(result: m.MigrationResult, repo_root: Path, state: Path, **kwargs: Any) -> None:
    """After a writing run: re-plan the pair, and record it finished only when nothing is left.

    Nothing left and a ledger row naming the pair: append ``finished`` with ``verified: true``,
    unless the latest row already says so. Anything left: mark each such part failed and add a
    ``NOT finished`` warning naming them, so ``result.failed`` holds and nothing is recorded. A
    leftover cleanup in another repo is not judged here.
    """
    if any(move.kind == m.ALREADY_MIGRATED for move in result.moves):
        return  # a leftover cleanup: the pair's home is in another repo, and its own finish judges it
    kwargs = {key: value for key, value in kwargs.items() if key != "state_dir"}
    left = replan(repo_root, result.old, result.new, state_dir=state, **kwargs)
    if not result.parts:
        summarize(result, resumed=True)
    if left:
        for part, status in left.items():
            if status.startswith("failed:"):
                result.parts[part] = status
            elif not result.parts.get(part, "").startswith("failed:"):
                result.parts[part] = f"failed: still left after this run ({status})"
        named = "; ".join(f"{part} ({status})" for part, status in left.items())
        result.warnings.append(
            f"migration {result.old} -> {result.new} NOT finished: a re-plan still finds {named}; "
            f"run `{finish_command(result.old, result.new, repo_root)}` once they are resolved"
        )
        return
    if m._migration_evidence(result.old, result.new, state) and finish_state(result.old, result.new, state) != (
        "verified"
    ):
        m._append_ledger(state, result.old, result.new, repo_root, finished=True, verified=True)
