"""Each AI client's session store, behind one contract: find, probe, rename, roll back.

A session name is released only when its worktree and its transcript are both gone,
and only the client that wrote the transcript knows where it is. Each adapter here
reads the store its client's own resume reads, as measured in
docs/plans/session-name-identifiers-phase-3-notes.md:

========== ===================================================== ===================================
Client     Store                                                 Working directory, id, title
========== ===================================================== ===================================
claude     ``~/.claude/projects/<slug>/*.jsonl``                 the slug; ``sessionId``; ``customTitle``
codex      ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``      first record ``payload.cwd``, ``payload.id``
pi         ``~/.pi/agent/sessions/--<cwd>--/*.jsonl``            first record ``cwd``, ``id``; ``session_info.name``
gemini     ``~/.gemini/tmp/<project id>/chats/*``                ``~/.gemini/projects.json`` maps path to id
========== ===================================================== ===================================

``probe_transcript`` answers ``present``, ``absent`` or ``unknown``. ``absent`` is
only ever an answer from an accessible, recognised store root and a valid mapping
from the session to where its record would be. A missing or unreadable root, or a
mapping that cannot be made, is ``unknown``, which holds the name.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .cc_migrate import cc_project_dir

PRESENT, ABSENT, UNKNOWN = "present", "absent", "unknown"


@dataclass(frozen=True)
class Probe:
    state: str
    reason: str = ""


@dataclass
class Record:
    """One resumable session in a client's store."""

    client: str
    path: Path
    cwd: str | None
    session_id: str | None
    title: str | None
    problem: str | None = None


@dataclass
class Changed:
    """One artifact a rename changed, with what it held before, so it can be put back."""

    path: Path
    before: bytes | None
    moved_from: Path | None = None
    note: str = ""


@dataclass
class RenameResult:
    changed: list[Changed] = field(default_factory=list)


def _read_first_json(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            first = handle.readline()
        value = json.loads(first)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_atomic(path: Path, data: bytes) -> None:
    staged = path.with_name(f".{path.name}.ai-cli-rename")
    staged.write_bytes(data)
    staged.replace(path)


def _rewrite_lines(path: Path, rewrite) -> bytes | None:
    """Rewrite each JSON line of ``path`` through ``rewrite``; return the old bytes when anything changed."""
    before = path.read_bytes()
    out: list[bytes] = []
    changed = False
    for raw in before.splitlines(keepends=True):
        stripped = raw.strip()
        if not stripped:
            out.append(raw)
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            out.append(raw)
            continue
        new = rewrite(record)
        if new is None:
            out.append(raw)
            continue
        changed = True
        ending = raw[len(raw.rstrip(b"\r\n")) :]
        out.append(json.dumps(new, ensure_ascii=False).encode("utf-8") + (ending or b"\n"))
    if not changed:
        return None
    _write_atomic(path, b"".join(out))
    return before


def rollback(changes: Iterable[Changed]) -> list[str]:
    """Put every changed artifact back; return the ones that could not be restored."""
    failed: list[str] = []
    for change in reversed(list(changes)):
        try:
            if change.moved_from is not None:
                change.moved_from.parent.mkdir(parents=True, exist_ok=True)
                change.path.replace(change.moved_from)
                target = change.moved_from
            else:
                target = change.path
            if change.before is not None:
                _write_atomic(target, change.before)
        except OSError as exc:
            failed.append(f"{change.path}: {exc}")
    return failed


class Adapter:
    client = ""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home if home is not None else Path.home()

    def store_root(self) -> Path:
        raise NotImplementedError

    def _root_problem(self) -> str | None:
        root = self.store_root()
        if not root.is_dir():
            return f"{self.client} store root {root} is missing"
        if not os.access(root, os.R_OK | os.X_OK):
            return f"{self.client} store root {root} is not readable"
        return None

    def all_records(self) -> list[Record]:
        raise NotImplementedError

    def records_for(self, worktree: Path) -> list[Record]:
        """The records whose working directory is ``worktree``, plus any unreadable one that could be.

        An unreadable record has no working directory to compare, so it cannot be ruled
        out: it comes back with its problem set, and the probe turns that into ``unknown``.
        """
        target = str(worktree)
        return [record for record in self.all_records() if record.cwd == target or self._could_be(record, worktree)]

    def _could_be(self, record: Record, worktree: Path) -> bool:
        return record.problem is not None

    def records_titled(self, title: str) -> list[Record]:
        return [record for record in self.all_records() if record.title == title]

    def probe_transcript(self, worktree: Path | None, title: str) -> Probe:
        problem = self._root_problem()
        if problem:
            return Probe(UNKNOWN, problem)
        try:
            records = self.records_for(worktree) if worktree is not None else self.records_titled(title)
        except OSError as exc:
            return Probe(UNKNOWN, f"{self.client} store could not be read: {exc}")
        broken = [record for record in records if record.problem]
        if broken:
            return Probe(UNKNOWN, f"{self.client} record {broken[0].path} is not readable: {broken[0].problem}")
        if records:
            return Probe(PRESENT, f"{self.client} record {records[0].path}")
        return Probe(ABSENT, f"no {self.client} record for it under {self.store_root()}")

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        raise NotImplementedError


class ClaudeCode(Adapter):
    """Claude Code: one project directory per working directory, a transcript per session."""

    client = "claude-code"

    def store_root(self) -> Path:
        return self.home / ".claude" / "projects"

    def all_records(self) -> list[Record]:
        records: list[Record] = []
        root = self.store_root()
        for path in sorted(root.glob("*/*.jsonl")):
            title, session_id, cwd, problem = None, path.stem, None, None
            try:
                for raw in path.read_bytes().splitlines():
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    cwd = cwd or entry.get("cwd")
                    if entry.get("type") == "custom-title" or "customTitle" in entry:
                        title = entry.get("customTitle") or title
            except OSError as exc:
                problem = str(exc)
            records.append(Record(self.client, path, cwd, session_id, title, problem))
        return records

    def records_for(self, worktree: Path) -> list[Record]:
        project = cc_project_dir(worktree, self.home / ".claude")
        if not project.is_dir():
            return []
        return [record for record in self.all_records() if record.path.parent == project] or [
            Record(self.client, project, str(worktree), None, None)
        ]

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        result = RenameResult()

        def retitle(entry: dict) -> dict | None:
            if entry.get("customTitle") == old_title:
                return {**entry, "customTitle": new_title}
            return None

        if record.path.is_file():
            before = _rewrite_lines(record.path, retitle)
            if before is not None:
                result.changed.append(Changed(record.path, before, note="customTitle"))
        return result


class Codex(Adapter):
    """Codex: one rollout file per session; the first record holds the working directory."""

    client = "codex"

    def store_root(self) -> Path:
        return self.home / ".codex" / "sessions"

    def all_records(self) -> list[Record]:
        records: list[Record] = []
        for path in sorted(self.store_root().glob("*/*/*/rollout-*.jsonl")):
            first = _read_first_json(path)
            if first is None or first.get("type") != "session_meta" or not isinstance(first.get("payload"), dict):
                records.append(Record(self.client, path, None, None, None, "first record is not session_meta"))
                continue
            payload = first["payload"]
            records.append(Record(self.client, path, payload.get("cwd"), payload.get("id"), None))
        return records

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        result = RenameResult()
        if new_worktree is None or record.cwd is None:
            return result
        old_cwd = record.cwd

        def repoint(entry: dict) -> dict | None:
            payload = entry.get("payload")
            if entry.get("type") == "session_meta" and isinstance(payload, dict) and payload.get("cwd") == old_cwd:
                return {**entry, "payload": {**payload, "cwd": str(new_worktree)}}
            return None

        before = _rewrite_lines(record.path, repoint)
        if before is not None:
            result.changed.append(Changed(record.path, before, note="payload.cwd"))
        return result


def pi_directory(cwd: str) -> str:
    """Pi's session directory name for ``cwd``: separators and colons become hyphens, wrapped in ``--``."""
    stripped = cwd[1:] if cwd[:1] in ("/", "\\") else cwd
    return "--" + stripped.replace("/", "-").replace("\\", "-").replace(":", "-") + "--"


class Pi(Adapter):
    """Pi: one directory per working directory; the first record is the session header."""

    client = "pi"

    def _could_be(self, record: Record, worktree: Path) -> bool:
        # The directory is derived from the working directory, so only an unreadable record
        # in this session's own directory can be its record.
        return record.problem is not None and record.path.parent.name == pi_directory(str(worktree))

    def store_root(self) -> Path:
        return self.home / ".pi" / "agent" / "sessions"

    def all_records(self) -> list[Record]:
        records: list[Record] = []
        for path in sorted(self.store_root().glob("*/*.jsonl")):
            first = _read_first_json(path)
            if first is None or first.get("type") != "session":
                records.append(Record(self.client, path, None, None, None, "first record is not a session header"))
                continue
            title = None
            try:
                for raw in path.read_bytes().splitlines():
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("type") == "session_info":
                        title = entry.get("name", title)
            except OSError as exc:
                records.append(Record(self.client, path, first.get("cwd"), first.get("id"), None, str(exc)))
                continue
            records.append(Record(self.client, path, first.get("cwd"), first.get("id"), title))
        return records

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        result = RenameResult()
        old_cwd = record.cwd

        def rewrite(entry: dict) -> dict | None:
            if entry.get("type") == "session" and new_worktree is not None and entry.get("cwd") == old_cwd:
                return {**entry, "cwd": str(new_worktree)}
            if entry.get("type") == "session_info" and entry.get("name") == old_title:
                return {**entry, "name": new_title}
            return None

        before = _rewrite_lines(record.path, rewrite)
        if before is not None:
            result.changed.append(Changed(record.path, before, note="cwd and session_info.name"))
        if new_worktree is not None and old_cwd is not None:
            target_dir = self.store_root() / pi_directory(str(new_worktree))
            if target_dir != record.path.parent:
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / record.path.name
                record.path.replace(target)
                result.changed.append(Changed(target, None, moved_from=record.path, note="session directory"))
        return result


class Gemini(Adapter):
    """Gemini CLI: ``projects.json`` maps a project path to its id; chats live under that id."""

    client = "gemini"

    def store_root(self) -> Path:
        return self.home / ".gemini" / "tmp"

    def registry(self) -> Path:
        return self.home / ".gemini" / "projects.json"

    def _projects(self) -> dict[str, str] | None:
        try:
            data = json.loads(self.registry().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return None
        projects = data.get("projects") if isinstance(data, dict) else None
        return projects if isinstance(projects, dict) else None

    def _root_problem(self) -> str | None:
        problem = super()._root_problem()
        if problem:
            return problem
        if self._projects() is None:
            return f"gemini project registry {self.registry()} is not readable"
        return None

    def all_records(self) -> list[Record]:
        projects = self._projects() or {}
        by_id = {project_id: path for path, project_id in projects.items()}
        records: list[Record] = []
        for chat in sorted(self.store_root().glob("*/chats/*")):
            if chat.suffix not in (".json", ".jsonl"):
                continue
            project_id = chat.parent.parent.name
            first = _read_first_json(chat)
            session_id = first.get("sessionId") if first else None
            records.append(Record(self.client, chat, by_id.get(project_id), session_id, None))
        return records

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        result = RenameResult()
        if new_worktree is None or record.cwd is None:
            return result
        registry = self.registry()
        before = registry.read_bytes()
        data = json.loads(before)
        projects = data["projects"]
        project_id = projects.pop(record.cwd, None)
        if project_id is None:
            return result
        projects[str(new_worktree)] = project_id
        _write_atomic(registry, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
        result.changed.append(Changed(registry, before, note="projects.json path"))
        marker = self.store_root() / project_id / ".project_root"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == record.cwd:
            marker_before = marker.read_bytes()
            _write_atomic(marker, f"{new_worktree}\n".encode())
            result.changed.append(Changed(marker, marker_before, note=".project_root"))
        return result


ADAPTERS: dict[str, type[Adapter]] = {
    "claude-code": ClaudeCode,
    "codex": Codex,
    "pi": Pi,
    "gemini": Gemini,
}


def adapter(client: str, home: Path | None = None) -> Adapter:
    try:
        return ADAPTERS[client](home)
    except KeyError:
        raise KeyError(f"no session-store adapter for client {client!r}") from None


def all_adapters(home: Path | None = None) -> list[Adapter]:
    return [cls(home) for cls in ADAPTERS.values()]
