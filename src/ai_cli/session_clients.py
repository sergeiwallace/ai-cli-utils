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
agy        ``~/.gemini/antigravity-cli/``                        ``conversation_summaries.db``, ``last_conversations.json``
gemini     ``~/.gemini/tmp/<project id>/chats/*``                ``~/.gemini/projects.json`` maps path to id
========== ===================================================== ===================================

``probe_transcript`` answers ``present``, ``absent`` or ``unknown``. ``absent`` is
only ever an answer from an accessible, recognised store root and a valid mapping
from the session to where its record would be. A missing or unreadable root, or a
mapping that cannot be made, is ``unknown``, which holds the name.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import UUID

from .cc_migrate import RECORD_NAME_KEYS, cc_project_dir

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
    can_probe_absence = True

    def __init__(self, home: Path | None = None) -> None:
        self.home = home if home is not None else Path.home()
        self._read: dict[Path, tuple[tuple[int, int, int, int], Record]] = {}

    def _read_once(self, path: Path, read) -> Record:
        """``read()`` of ``path``, kept and handed back until the file changes.

        One reconcile probes every row it holds against the same store, so a transcript
        is read in full once per adapter, not once per row. A file is unchanged while its
        device, inode, size and modification time are; a rewrite through ``_write_atomic``
        replaces the inode, so a renamed record is read again.
        """
        try:
            stat = path.stat()
        except OSError:
            return read()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        kept = self._read.get(path)
        if kept is not None and kept[0] == key:
            return kept[1]
        record = read()
        if record.problem is None:
            self._read[path] = (key, record)
        return record

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

    def _probe_records(self, worktree: Path) -> list[Record]:
        """What ``probe_transcript`` reads for ``worktree``: enough to decide, not every record."""
        return self.records_for(worktree)

    def probe_transcript(self, worktree: Path | None, title: str) -> Probe:
        problem = self._root_problem()
        if problem:
            return Probe(UNKNOWN, problem)
        try:
            records = self._probe_records(worktree) if worktree is not None else self.records_titled(title)
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

    def _record(self, path: Path) -> Record:
        return self._read_once(path, lambda: self._read_record(path))

    def _read_record(self, path: Path) -> Record:
        """One transcript: the first working directory it names and its last custom title.

        Only a line read while no working directory is known yet, or one that contains
        ``customTitle``, can change either, so every other line is skipped unparsed. The
        transcript is read a line at a time: one can be larger than a gigabyte.
        """
        title, cwd = None, None
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    if cwd and b"customTitle" not in raw:
                        continue
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
            return Record(self.client, path, cwd, path.stem, title, str(exc))
        return Record(self.client, path, cwd, path.stem, title)

    def all_records(self) -> list[Record]:
        return [self._record(path) for path in sorted(self.store_root().glob("*/*.jsonl"))]

    def records_for(self, worktree: Path) -> list[Record]:
        """The transcripts in ``worktree``'s own project directory, never the rest of the store."""
        project = cc_project_dir(worktree, self.home / ".claude")
        if not project.is_dir():
            return []
        return [self._record(path) for path in sorted(project.glob("*.jsonl"))] or [
            Record(self.client, project, str(worktree), None, None)
        ]

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        result = RenameResult()

        def retitle(entry: dict) -> dict | None:
            keys = [key for key in RECORD_NAME_KEYS if entry.get(key) == old_title]
            return {**entry, **dict.fromkeys(keys, new_title)} if keys else None

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

    def probe_thread(self, worktree: Path, thread_id: str) -> Probe:
        """Corroborate one native ID, never infer it from cwd, filename or recency.

        This is persisted evidence only. A caller must obtain the ID from the client;
        a matching rollout does not prove that a process or pane is alive.
        """
        problem = self._root_problem()
        if problem:
            return Probe(UNKNOWN, problem)
        try:
            matches = [record for record in self.all_records() if record.session_id == thread_id]
            if len(matches) != 1:
                return Probe(UNKNOWN, "recorded Codex thread has missing or ambiguous rollouts")
            record = matches[0]
            if record.problem or not isinstance(record.cwd, str) or Path(record.cwd).resolve() != worktree.resolve():
                return Probe(UNKNOWN, "recorded Codex thread working directory cannot be corroborated")
            return Probe(PRESENT, str(record.path))
        except (OSError, ValueError, RuntimeError):
            return Probe(UNKNOWN, "recorded Codex thread store is unreadable")

    def _record(self, path: Path) -> Record:
        first = _read_first_json(path)
        if first is None or first.get("type") != "session_meta" or not isinstance(first.get("payload"), dict):
            return Record(self.client, path, None, None, None, "first record is not session_meta")
        payload = first["payload"]
        return Record(self.client, path, payload.get("cwd"), payload.get("id"), None)

    def all_records(self) -> list[Record]:
        return [self._record(path) for path in sorted(self.store_root().glob("*/*/*/rollout-*.jsonl"))]

    def records_titled(self, title: str) -> list[Record]:
        # A rollout carries no title, so no record can match one.
        return []

    def _probe_records(self, worktree: Path) -> list[Record]:
        """Rollouts are filed by date, so every first record is read, but only until one matches.

        A match is ``present`` whatever else the store holds. An unreadable rollout seen on
        the way is kept, so without a match the probe is ``unknown``, never ``absent``.
        """
        target = str(worktree)
        broken: list[Record] = []
        for path in sorted(self.store_root().glob("*/*/*/rollout-*.jsonl")):
            record = self._record(path)
            if record.cwd == target:
                return [record]
            if record.problem is not None:
                broken.append(record)
        return broken

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

    def _header(self, path: Path) -> dict | Record:
        """The session header, or the unreadable record standing in for it."""
        first = _read_first_json(path)
        if first is None or first.get("type") != "session":
            return Record(self.client, path, None, None, None, "first record is not a session header")
        return first

    def _record(self, path: Path, first: dict) -> Record:
        return self._read_once(path, lambda: self._read_record(path, first))

    def _read_record(self, path: Path, first: dict) -> Record:
        """The session under ``first``, named by its last ``session_info`` line, read a line at a time."""
        title = None
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    if b"session_info" not in raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("type") == "session_info":
                        title = entry.get("name", title)
        except OSError as exc:
            return Record(self.client, path, first.get("cwd"), first.get("id"), None, str(exc))
        return Record(self.client, path, first.get("cwd"), first.get("id"), title)

    def all_records(self) -> list[Record]:
        records: list[Record] = []
        for path in sorted(self.store_root().glob("*/*.jsonl")):
            header = self._header(path)
            records.append(header if isinstance(header, Record) else self._record(path, header))
        return records

    def records_for(self, worktree: Path) -> list[Record]:
        """Every header is read, and only a header whose ``cwd`` matches is read in full.

        The lookup is not narrowed to the ``pi_directory(worktree)`` directory: that is
        this module's rendering of Pi's directory name, and a record Pi filed under a
        different rendering would then read as ``absent``.
        """
        target = str(worktree)
        records: list[Record] = []
        for path in sorted(self.store_root().glob("*/*.jsonl")):
            header = self._header(path)
            if isinstance(header, Record):
                if self._could_be(header, worktree):
                    records.append(header)
            elif header.get("cwd") == target:
                records.append(self._record(path, header))
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

    def _chats(self, pattern: str) -> list[Record]:
        projects = self._projects() or {}
        by_id = {project_id: path for path, project_id in projects.items()}
        records: list[Record] = []
        for chat in sorted(self.store_root().glob(pattern)):
            if chat.suffix not in (".json", ".jsonl"):
                continue
            project_id = chat.parent.parent.name
            first = _read_first_json(chat)
            session_id = first.get("sessionId") if first else None
            records.append(Record(self.client, chat, by_id.get(project_id), session_id, None))
        return records

    def all_records(self) -> list[Record]:
        return self._chats("*/chats/*")

    def records_for(self, worktree: Path) -> list[Record]:
        """Only the chats under the project ids ``projects.json`` maps to ``worktree``."""
        target = str(worktree)
        projects = self._projects() or {}
        by_id = {project_id: path for path, project_id in projects.items()}
        records: list[Record] = []
        for project_id in sorted(pid for pid, path in by_id.items() if path == target):
            # A chat's project id is its directory's name, so an id that is not one name has no chats.
            if (
                isinstance(project_id, str)
                and project_id not in ("", ".", "..")
                and Path(project_id).name == project_id
            ):
                records.extend(self._chats(f"{glob.escape(project_id)}/chats/*"))
        return records

    def records_titled(self, title: str) -> list[Record]:
        # A chat carries no title, so no record can match one.
        return []

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


def _file_uri_to_path(uri: str) -> str | None:
    if not isinstance(uri, str) or not uri:
        return None
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        raw = url2pathname(parsed.path)
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            raw = f"//{parsed.netloc}{raw}"
        return raw or None
    return uri


class Antigravity(Adapter):
    """Antigravity CLI (``agy``): SQLite conversation index plus workspace last-conversation cache."""

    client = "agy"
    can_probe_absence = False

    def store_root(self) -> Path:
        return self.home / ".gemini" / "antigravity-cli"

    def summary_db(self) -> Path:
        return self.store_root() / "conversation_summaries.db"

    def conversation_cache(self) -> Path:
        return self.store_root() / "cache" / "last_conversations.json"

    def conversations_dir(self) -> Path:
        return self.store_root() / "conversations"

    def _read_cache(self) -> dict[str, str]:
        cache = self.conversation_cache()
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise OSError(f"Antigravity conversation cache {cache} is unavailable") from exc
        if not isinstance(data, dict):
            raise OSError(f"Antigravity conversation cache {cache} is malformed")
        out: dict[str, str] = {}
        for workspace, value in data.items():
            if isinstance(workspace, str) and isinstance(value, str):
                try:
                    out[workspace] = str(UUID(value))
                except ValueError:
                    continue
        return out

    def _read_db_rows(self) -> list[tuple[str, list[str], str | None]]:
        db = self.summary_db()
        if not db.is_file():
            return []
        try:
            conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2.0)
            try:
                rows = conn.execute(
                    "SELECT conversation_id, workspace_uris, title FROM conversation_summaries"
                ).fetchall()
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            raise OSError(f"Antigravity summary database {db} is unavailable: {exc}") from exc
        parsed: list[tuple[str, list[str], str | None]] = []
        for conv_id, raw_uris, title in rows:
            if not isinstance(conv_id, str):
                continue
            try:
                norm_id = str(UUID(conv_id))
            except ValueError:
                continue
            paths: list[str] = []
            if isinstance(raw_uris, str) and raw_uris:
                try:
                    uris = json.loads(raw_uris)
                except ValueError:
                    uris = []
                if isinstance(uris, list):
                    for uri in uris:
                        path = _file_uri_to_path(uri)
                        if path:
                            paths.append(path)
            parsed.append((norm_id, paths, title if isinstance(title, str) and title else None))
        return parsed

    def _root_problem(self) -> str | None:
        problem = super()._root_problem()
        if problem:
            return problem
        try:
            self._read_cache()
            self._read_db_rows()
        except OSError as exc:
            return str(exc)
        return None

    def all_records(self) -> list[Record]:
        records: list[Record] = []
        seen: set[tuple[str, str]] = set()
        db_path = self.summary_db()
        for conv_id, paths, title in self._read_db_rows():
            if not paths:
                records.append(Record(self.client, db_path, None, conv_id, title))
                continue
            for path in paths:
                key = (conv_id, path)
                if key not in seen:
                    seen.add(key)
                    records.append(Record(self.client, db_path, path, conv_id, title))
        cache_path = self.conversation_cache()
        for cwd, conv_id in self._read_cache().items():
            key = (conv_id, cwd)
            if key not in seen:
                seen.add(key)
                records.append(Record(self.client, cache_path, cwd, conv_id, None))
        return records

    def records_for(self, worktree: Path) -> list[Record]:
        target = worktree.resolve()
        matched: list[Record] = []
        for record in self.all_records():
            if record.cwd is not None:
                try:
                    if Path(record.cwd).resolve() == target:
                        matched.append(record)
                        continue
                except OSError:
                    if record.cwd == str(worktree):
                        matched.append(record)
                        continue
            if self._could_be(record, worktree):
                matched.append(record)
        return matched

    def probe_conversation(self, worktree: Path, conversation_id: str) -> Probe:
        """Corroborate one exact native conversation ID against persisted Antigravity state."""
        try:
            norm_id = str(UUID(conversation_id))
        except ValueError:
            return Probe(UNKNOWN, "recorded Antigravity conversation ID is malformed")
        problem = self._root_problem()
        if problem:
            return Probe(UNKNOWN, problem)
        resolved = worktree.resolve()
        try:
            db_rows = self._read_db_rows()
            matching_db = [row for row in db_rows if row[0] == norm_id]
            if matching_db:
                for _, paths, _ in matching_db:
                    for path in paths:
                        try:
                            if Path(path).resolve() == resolved:
                                return Probe(PRESENT, str(self.summary_db()))
                        except OSError:
                            continue
                return Probe(UNKNOWN, "recorded Antigravity conversation working directory cannot be corroborated")
            cache = self._read_cache()
            conv_file = self.conversations_dir() / f"{norm_id}.db"
            brain_dir = self.store_root() / "brain" / norm_id
            for cwd, cached_id in cache.items():
                if cached_id != norm_id:
                    continue
                try:
                    if Path(cwd).resolve() == resolved and (conv_file.is_file() or brain_dir.is_dir()):
                        return Probe(PRESENT, str(conv_file if conv_file.is_file() else brain_dir))
                except OSError:
                    continue
            return Probe(UNKNOWN, "recorded Antigravity conversation has missing or uncorroborated state")
        except (OSError, ValueError, RuntimeError):
            return Probe(UNKNOWN, "recorded Antigravity conversation store is unreadable")

    def probe_transcript(self, worktree: Path | None, title: str) -> Probe:
        if worktree is None:
            return Probe(UNKNOWN, "Antigravity identifies conversations by worktree path")
        probe = super().probe_transcript(worktree, title)
        if probe.state == ABSENT:
            return Probe(UNKNOWN, "Antigravity store cannot prove conversation absence")
        return probe

    def rename(self, record: Record, old_title: str, new_title: str, new_worktree: Path | None) -> RenameResult:
        raise ValueError("rename the Antigravity conversation from Antigravity")


ADAPTERS: dict[str, type[Adapter]] = {
    "claude-code": ClaudeCode,
    "codex": Codex,
    "pi": Pi,
    "agy": Antigravity,
}

# Historical rows created before `ai g` switched from Gemini CLI to Antigravity
# still resolve for reconciliation and migration without advertising Gemini CLI
# as an active launch client.
LEGACY_ADAPTERS: dict[str, type[Adapter]] = {
    "gemini": Gemini,
}


def adapter(client: str, home: Path | None = None) -> Adapter:
    cls = ADAPTERS.get(client) or LEGACY_ADAPTERS.get(client)
    if cls is not None:
        return cls(home)
    raise KeyError(f"no session-store adapter for client {client!r}")


def all_adapters(home: Path | None = None) -> list[Adapter]:
    return [cls(home) for cls in ADAPTERS.values()]
