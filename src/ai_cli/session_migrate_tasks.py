"""Preserve a task-id mapping across interrupted copies without trusting completion stamps."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .session_adopt import TaskMove, merge_task_namespace

JOURNAL = "session-migration-tasks.jsonl"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload(source: Path, target: str, mapping: dict[str, str]) -> bytes:
    raw = source.read_bytes()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        return raw
    if not isinstance(data, dict):
        return raw
    data["id"] = target
    if isinstance(data.get("blocks"), list):
        data["blocks"] = [
            mapping.get(str(item), item) if isinstance(item, (str, int)) else item for item in data["blocks"]
        ]
    return json.dumps(data, ensure_ascii=False, indent=1).encode()


def plan(old: str, new: str, home: Path, state: Path, *, resuming: bool) -> tuple[list[TaskMove], dict]:
    """Read saved intent, validating each remaining source and each already-written destination."""
    from .session_migrate import MigrationError

    source_ns, dest_ns = home / "tasks" / old, home / "tasks" / new
    saved = None
    path = state / JOURNAL
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # An interrupted append contains no usable intent; artifacts still decide.
            if not isinstance(row, dict):
                continue
            if row.get("old") == old and row.get("new") == new and row.get("home") == str(home):
                saved = row
    if saved is None:
        if resuming and any(source_ns.glob("*.json")) and any(dest_ns.glob("*.json")):
            raise MigrationError(
                f"both task namespaces exist without task-id intent: {source_ns} and {dest_ns}; refusing to duplicate tasks"
            )
        moves = merge_task_namespace(source_ns, dest_ns, dry_run=True)
        mapping = {move.source.stem: move.dest.stem for move in moves}
        saved = {
            "old": old,
            "new": new,
            "home": str(home),
            "tasks": [
                {
                    "source": move.source.stem,
                    "dest": move.dest.stem,
                    "before": _digest(move.source.read_bytes()),
                    "after": _digest(_payload(move.source, move.dest.stem, mapping)),
                }
                for move in moves
            ],
        }
        saved["recorded"] = False
    else:
        saved = {**saved, "recorded": True}
    mapping = {item["source"]: item["dest"] for item in saved["tasks"]}
    remaining = []
    known = set(mapping)
    if any(path.stem not in known for path in source_ns.glob("*.json") if path.stem.isdigit()):
        raise MigrationError(f"task namespace {source_ns} changed after its migration intent; refusing to remap ids")
    for item in saved["tasks"]:
        if not item["source"].isdigit() or not item["dest"].isdigit():
            raise MigrationError(f"invalid task migration intent in {path}")
        source, dest = source_ns / f"{item['source']}.json", dest_ns / f"{item['dest']}.json"
        if source.exists():
            if dest.exists() and _digest(dest.read_bytes()) != item["after"]:
                raise MigrationError(f"task destination {dest} differs from the recorded intent; refusing to overwrite")
            if _digest(source.read_bytes()) != item["before"]:
                raise MigrationError(f"task source {source} changed after its migration intent")
            remaining.append(TaskMove(source, dest, source.stem if source.stem != dest.stem else None))
        elif not dest.exists():
            raise MigrationError(f"task migration has neither {source} nor {dest}; restore the missing artifact")
    return remaining, saved


def remember(intent: dict, state: Path) -> None:
    """Save the mapping before any earlier step can leave the migration resumable."""
    if not intent.get("tasks"):
        return
    if not intent["recorded"]:
        state.mkdir(parents=True, exist_ok=True)
        with (state / JOURNAL).open("a", encoding="utf-8") as out:
            out.write(json.dumps({key: value for key, value in intent.items() if key != "recorded"}) + "\n")
            out.flush()
            os.fsync(out.fileno())
        intent["recorded"] = True


def apply(moves: list[TaskMove], intent: dict, state: Path) -> None:
    """An exact destination match finishes only source removal, using the saved ID mapping."""
    if not moves:
        return
    remember(intent, state)
    mapping = {item["source"]: item["dest"] for item in intent["tasks"]}
    for move in moves:
        payload = _payload(move.source, move.dest.stem, mapping)
        move.dest.parent.mkdir(parents=True, exist_ok=True)
        if not move.dest.exists():
            # Exclusive creation never replaces an unrelated destination. An incomplete file
            # is refused on retry, with its path named, rather than overwritten speculatively.
            from .session_migrate import _write_migration_file

            _write_migration_file(move.dest, payload)
        if move.dest.read_bytes() != payload:
            from .session_migrate import MigrationError

            raise MigrationError(f"task destination {move.dest} changed during migration")
        move.source.unlink()
