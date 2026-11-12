"""Save issue intent before changing its assignee; verify comments from the store on retry."""

from __future__ import annotations

import json
import os
from pathlib import Path

JOURNAL = "session-migration-issues.jsonl"


def _rows(state: Path, old: str, new: str) -> list[dict]:
    """The pair's journal rows that name a store, in the order they were written."""
    rows: list[dict] = []
    path = state / JOURNAL
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(row, dict)
                and row.get("old") == old
                and row.get("new") == new
                and isinstance(row.get("store"), str)
            ):
                rows.append(row)
    return rows


def intents(state: Path, old: str, new: str) -> dict[Path, set[str]]:
    found: dict[Path, set[str]] = {}
    for row in _rows(state, old, new):
        if isinstance(row.get("issues"), list):
            found.setdefault(Path(row["store"]), set()).update(i for i in row["issues"] if isinstance(i, str))
    return found


def unpublished(state: Path, old: str, new: str) -> set[Path]:
    """Each store whose last re-key intent has no later ``published`` row."""
    pending: dict[Path, bool] = {}
    for row in _rows(state, old, new):
        if isinstance(row.get("issues"), list):
            pending[Path(row["store"])] = True
        elif row.get("published") is True:
            pending[Path(row["store"])] = False
    return {store for store, waiting in pending.items() if waiting}


def _append(state: Path, row: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with (state / JOURNAL).open("a", encoding="utf-8") as out:
        out.write(json.dumps(row) + "\n")
        out.flush()
        os.fsync(out.fileno())


def remember(state: Path, store: Path, old: str, new: str, issues: list[str]) -> None:
    missing = set(issues) - intents(state, old, new).get(store, set())
    if missing:
        _append(state, {"old": old, "new": new, "store": str(store), "issues": sorted(missing)})


def remember_published(state: Path, store: Path, old: str, new: str) -> None:
    _append(state, {"old": old, "new": new, "store": str(store), "published": True})


def note(old: str, new: str) -> str:
    return f"Re-keyed from {old} to {new} by `ai migrate-session`."


def inspect_comments(store, old: str, new: str, intended: set[str]) -> None:
    """Only a recorded issue still held by NEW may have its missing comment finished."""
    from .session_migrate import _bd

    missing = intended - set(store.issues)
    if not missing:
        return
    listed = _bd(store.path, "list", "--assignee", new, "--all", "--brief", "--json", "--limit", "0")
    if listed.returncode:
        raise ValueError(listed.stderr or "cannot verify migrated issue assignees")
    rows = json.loads(listed.stdout)
    if not isinstance(rows, list):
        raise ValueError("bd list printed no issue list")
    held = {str(row["id"]) for row in rows if isinstance(row, dict) and row.get("assignee") == new and row.get("id")}
    for issue in sorted(missing):
        if issue not in held:
            raise ValueError(f"recorded issue {issue} is no longer assigned to {old} or {new}; completion unverified")
        comments = _bd(store.path, "comments", issue, "--json")
        if comments.returncode:
            raise ValueError(comments.stderr or f"cannot verify comments for {issue}")
        values = json.loads(comments.stdout)
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ValueError(f"bd comments printed no comment list for {issue}")
        if any(value.get("text") == note(old, new) for value in values):
            store.completed.append(issue)
        else:
            store.comments.append(issue)
