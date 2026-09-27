"""Adopt a Claude Code session that was started outside ``ai c`` into a session slot.

Why this exists
---------------
``ai cc-migrate`` moves a *transcript* between project roots. That is necessary
but not sufficient: a Claude Code session started as a plain ``claude`` in a repo
root leaves state in several places, and only some of it is keyed by the session
UUID (which a migration does not change). The rest is keyed by the **project
slug** — the session's working directory with every non-alphanumeric character
replaced by ``-`` — or by a **task namespace** derived from the session UUID
rather than from the session's name. Those are the parts that break when the
conversation should henceforth be resumed by ``ai c <n>`` from a worktree.

Adoption is the whole job, in one pass:

1. **Worktree** — ensure ``<repo>/.worktrees/<ai_name>`` exists (created off
   ``origin/main`` by the ordinary session machinery when absent, reused as-is
   when present; never clobbered).
2. **Duplicate-title collision** — refuse to proceed, unconditionally, when two
   transcripts claim the same title. ``ai c`` resolves by the *current*
   ``customTitle`` in a newest-first scan, so a duplicate makes resume
   nondeterministic: it would silently pick one of two conversations. See
   ``TitleCollision``.

   This once read "resolves by *first* ``customTitle``", which was wrong about
   the code it described and is worth naming because the error was
   self-justifying: while :func:`ai_cli.cc_migrate.transcript_title` really did
   read the first record, the sentence looked like a correct account of
   observable behaviour rather than a bug report. A transcript renamed away from
   a title therefore kept claiming it and blocked the adoption of whoever
   legitimately held that name.
3. **Transcript** — delegated to :func:`ai_cli.cc_migrate.migrate_session`, which
   rewrites recorded cwd fields and verifies the destination before removing the
   source.
3b. **Worktree binding** — :func:`neutralise_worktree_state` clears any stale
   ``worktree-state`` record left by an earlier mid-session worktree entry.
   Without this the transcript does not *stay* adopted: Claude Code restores the
   recorded binding on resume and renames the transcript back to the recorded
   original directory when the worktree is left. Moving the file is necessary but
   not sufficient.
4. **Task namespace** — ``~/.claude/tasks/<namespace>/<n>.json``. Task ids are
   *namespace-scoped* small integers, so merging two namespaces must renumber
   rather than overwrite; references between tasks are remapped with the ids.
5. **Auto-memory** — ``~/.claude/projects/<slug>/memory/``. See
   :func:`adopt_memory` for the rule and why it is a copy.
5b. **Resume hand-off** — ``~/.claude/resume/``, keyed by the session *name*. See
   :func:`migrate_resume_artifacts`, which is a deliberately temporary bridge.
6. **Everything else** — inventoried in ``docs/tools/cc-session-adoption.md``.
   UUID-keyed state (``~/.claude/teams/session-<uuid8>/``,
   ``~/.claude/session-env/<uuid>/``, ``~/.claude/file-history/<uuid>/``) is
   deliberately untouched: the UUID does not change, so it keeps working.

Every write is preceded by three refusals — a live session, a duplicate title,
insufficient free space — because each of them corrupts state if discovered
halfway through instead of up front.

Depends only on the standard library, apart from an optional call into
``ai_cli.session`` to create a missing worktree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .cc_migrate import (
    MigrationResult,
    cc_project_dir,
    find_transcript,
    migrate_session,
    transcript_title,
    transcript_titles,
)

#: Headroom demanded on top of the bytes an adoption actually plans to write.
#: An adoption copies before it verifies and only then removes the source, so a
#: volume that fills mid-write leaves a truncated transcript — the one file whose
#: loss cannot be undone. 16 MiB is far more than the small JSON writes need and
#: is cheap to insist on.
_SPACE_MARGIN_BYTES = 16 * 1024 * 1024

#: ``<prefix>-<index>`` — the ai_name shape ``ai c`` builds and worktrees use.
_AI_NAME_RE = re.compile(r"^(?P<prefix>.+)-(?P<index>\d+)$")


class AdoptionError(Exception):
    """Base for every refusal raised before an adoption writes anything."""


class LiveSessionError(AdoptionError):
    """The session (or its destination slot) is currently running.

    Adopting live state means moving files out from under a process that has them
    open and will keep appending to them. There is no safe merge afterwards, so
    this is a refusal rather than a warning.
    """


class InsufficientSpaceError(AdoptionError):
    """Not enough free space to complete the adoption without risking ENOSPC."""


@dataclass
class TitleCandidate:
    """One transcript claiming a title, described well enough to tell apart."""

    path: Path
    title: str
    size: int
    lines: int
    cwd: str
    mtime: float

    def describe(self) -> str:
        return (
            f"{self.path}\n"
            f"    title={self.title!r} size={self.size / 1_000_000:.2f} MB "
            f"lines={self.lines} mtime={self.mtime:.0f}\n"
            f"    cwd={self.cwd or '<unrecorded>'}"
        )


class TitleCollision(AdoptionError):
    """Two or more transcripts claim the same title — a human must choose.

    Carries every candidate and the lowest index free for the repo, so the CLI
    can print a concrete remedy without recomputing anything. This is never
    auto-resolved: picking by size or mtime would silently discard a
    conversation, and the two candidates in the case that motivated this were
    0.83 MB and 5.90 MB of genuinely different work.
    """

    def __init__(self, title: str, candidates: list[TitleCandidate], free_index: int | None, prefix: str | None):
        self.title = title
        self.candidates = candidates
        self.free_index = free_index
        self.prefix = prefix
        suggestion = f"{prefix}-{free_index}" if prefix and free_index else "a free index"
        super().__init__(
            f"{len(candidates)} transcripts claim the title {title!r} — `ai c` resolves by the "
            f"first matching customTitle, so resume would be nondeterministic. Retitle one of them "
            f"to {suggestion} and adopt that, or pick a different index."
        )


@dataclass
class LiveSession:
    """A Claude Code process this machine currently reports as running."""

    pid: int
    name: str
    cwd: str
    record: Path
    session_id: str = ""


@dataclass
class TaskMove:
    """One task file relocated into the pinned namespace."""

    source: Path
    dest: Path
    renumbered_from: str | None


@dataclass
class AdoptionResult:
    """What an adoption (or dry run) did / would do."""

    ai_name: str
    source_root: Path
    dest_root: Path
    worktree_created: bool
    migration: MigrationResult | None
    source_jsonl: Path | None = None
    source_lines: int = 0
    tasks_moved: list[TaskMove] = field(default_factory=list)
    memory_copied: list[Path] = field(default_factory=list)
    memory_conflicts: list[Path] = field(default_factory=list)
    resume_moves: list[ResumeMove] = field(default_factory=list)
    retitled_from: str | None = None
    worktree_records_cleared: int = 0
    resolved: Path | None = None
    dry_run: bool = False
    already_adopted: bool = False
    warnings: list[str] = field(default_factory=list)


# --- refusals ---------------------------------------------------------------


def _pid_is_live(pid: int, proc_dir: Path | None = None) -> bool:
    """True when ``pid`` names a running process.

    Reads ``/proc/<pid>`` on Linux rather than shelling out: a ``ps`` pipeline
    matching a pattern also matches the ``grep`` in its own pipeline, which has
    produced a backwards live/dead answer on a real machine. Where ``/proc`` does
    not exist (macOS, Windows) ``psutil`` answers the same question. Both live
    behind :func:`ai_cli.process_probe.probe_for`, which is the one place that
    chooses between them for every caller in the package.

    The fail direction is this function's own, and is the opposite of the
    launcher's registry check: an unanswerable probe counts as **live** here,
    because this guards an adoption that would move files out from under a running
    session.
    """
    if pid <= 0:
        return False
    try:
        from .process_probe import probe_for

        return probe_for(proc_dir).is_present(pid)
    except Exception:
        return True


def live_sessions(claude_home: Path | None = None, proc_dir: Path | None = None) -> list[LiveSession]:
    """Return every session in ``~/.claude/sessions/`` whose process still runs.

    Claude Code writes one ``<pid>.json`` per live session and does not always
    remove it on exit, so the file's presence proves nothing — the pid must be
    checked.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    found: list[LiveSession] = []
    session_dir = home / "sessions"
    if not session_dir.is_dir():
        return found
    for record in sorted(session_dir.glob("*.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            pid = int(data.get("pid", 0))
        except (TypeError, ValueError):
            continue
        if not _pid_is_live(pid, proc_dir):
            continue
        found.append(
            LiveSession(
                pid=pid,
                name=str(data.get("name") or ""),
                cwd=str(data.get("cwd") or ""),
                record=record,
                session_id=str(data.get("sessionId") or ""),
            )
        )
    return found


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _free_bytes(path: Path) -> int:
    """Free bytes on the filesystem holding ``path``, or its nearest parent."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def check_free_space(dest_dir: Path, needed: int, margin: int = _SPACE_MARGIN_BYTES) -> int:
    """Raise :class:`InsufficientSpaceError` unless ``needed + margin`` is free.

    Returns the observed free bytes so callers can report them.
    """
    free = _free_bytes(dest_dir)
    if free < needed + margin:
        raise InsufficientSpaceError(
            f"need {(needed + margin) / 1_000_000:.1f} MB free on the filesystem holding {dest_dir} "
            f"({needed / 1_000_000:.1f} MB to write + {margin / 1_000_000:.0f} MB margin) but only "
            f"{free / 1_000_000:.1f} MB is available — refusing to start. An adoption interrupted by "
            f"ENOSPC leaves a truncated transcript."
        )
    return free


# --- titles and indexes -----------------------------------------------------


def split_ai_name(ai_name: str) -> tuple[str, int] | None:
    """Split ``myproject-3`` into ``("myproject", 3)``; None when not that shape."""
    match = _AI_NAME_RE.match(ai_name)
    if not match:
        return None
    return match.group("prefix"), int(match.group("index"))


def _first_cwd(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(record, dict) and record.get("cwd"):
                    return str(record["cwd"])
    except OSError:
        return ""
    return ""


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def describe_candidate(path: Path) -> TitleCandidate:
    """Build a :class:`TitleCandidate` for one transcript, streaming it once each
    for line count and recorded cwd so a 64 MB file never lands in memory."""
    try:
        size = path.stat().st_size
        mtime = path.stat().st_mtime
    except OSError:
        size = 0
        mtime = 0.0
    return TitleCandidate(
        path=path,
        title=transcript_title(path) or "",
        size=size,
        lines=_count_lines(path),
        cwd=_first_cwd(path),
        mtime=mtime,
    )


def _project_dirs_for_repo(repo_root: Path, claude_home: Path | None) -> list[Path]:
    """Project dirs for the repo root and each of its session worktrees."""
    dirs = [cc_project_dir(repo_root, claude_home)]
    worktrees = repo_root / ".worktrees"
    if worktrees.is_dir():
        for child in sorted(worktrees.iterdir()):
            if child.is_dir():
                dirs.append(cc_project_dir(child, claude_home))
    return dirs


def find_title_candidates(
    repo_root: Path, title: str, claude_home: Path | None = None, extra_dirs: list[Path] | None = None
) -> list[TitleCandidate]:
    """Every transcript under this repo (root or any worktree) claiming ``title``."""
    seen: set[Path] = set()
    candidates: list[TitleCandidate] = []
    search = _project_dirs_for_repo(repo_root, claude_home) + list(extra_dirs or [])
    for project_dir in search:
        if not project_dir.is_dir():
            continue
        for path in sorted(project_dir.glob("*.jsonl")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if transcript_title(path) == title:
                candidates.append(describe_candidate(path))
    return candidates


def used_indexes(repo_root: Path, prefix: str, claude_home: Path | None = None) -> set[int]:
    """Indexes already claimed for ``prefix``, from worktrees *and* titles.

    Both sources matter: an index whose worktree was cleaned up may still be
    claimed by a transcript title (so reusing it would recreate the very
    collision this module refuses), and an index whose transcript was deleted may
    still have a worktree holding uncommitted work.
    """
    used: set[int] = set()
    worktrees = repo_root / ".worktrees"
    if worktrees.is_dir():
        for child in worktrees.iterdir():
            split = split_ai_name(child.name)
            if split and split[0] == prefix:
                used.add(split[1])
    for project_dir in _project_dirs_for_repo(repo_root, claude_home):
        if not project_dir.is_dir():
            continue
        for path in project_dir.glob("*.jsonl"):
            found = transcript_title(path)
            split = split_ai_name(found) if found else None
            if split and split[0] == prefix:
                used.add(split[1])
    return used


def next_free_index(repo_root: Path, prefix: str, claude_home: Path | None = None) -> int:
    """Lowest index >= 1 claimed by neither a worktree nor a transcript title."""
    used = used_indexes(repo_root, prefix, claude_home)
    index = 1
    while index in used:
        index += 1
    return index


def retitle_transcript(path: Path, old_title: str, new_title: str) -> int:
    """Rewrite every ``customTitle`` equal to ``old_title`` in ``path``.

    All matching records are rewritten, not just the first. Resume matches the
    *last* titled record, so rewriting only the first would leave the file still
    answering to the old name — and rewriting only the last would leave the old
    name in its history to be resurrected by any future reader that scans
    forwards. Rewriting every match is what makes the rename total.

    Written to a sibling temp file and atomically replaced, so an interrupted retitle
    leaves the original transcript intact rather than a half-rewritten one.
    Returns the number of records changed.
    """
    temp = path.with_name(path.name + ".retitle-tmp")
    changed = 0
    try:
        with path.open("r", encoding="utf-8") as src, temp.open("w", encoding="utf-8") as out:
            for raw in src:
                stripped = raw.strip()
                if stripped:
                    try:
                        record = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError):
                        record = None
                    if isinstance(record, dict) and record.get("customTitle") == old_title:
                        record["customTitle"] = new_title
                        raw = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                        changed += 1
                out.write(raw)
        stat = path.stat()
        os.utime(temp, (stat.st_atime, stat.st_mtime))
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return changed


def neutralise_worktree_state(path: Path, dest_root: Path) -> int:
    """Clear a transcript's stale worktree binding so Claude Code stops relocating it.

    Why this is necessary, and why nothing weaker works
    --------------------------------------------------
    A session that ever entered a worktree mid-conversation records a
    ``worktree-state`` entry holding an *absolute* ``originalCwd`` — the
    directory the session started in, which for an un-adopted session is the
    repo root. Claude Code treats that record as authoritative: on resume it
    restores the binding and moves the session into the recorded
    ``worktreePath``, and when that worktree is later left (explicitly, or by
    exiting the session) it returns the session to ``originalCwd`` **and renames
    the transcript into that directory's project directory**. A transcript's
    location is a function of the session's working directory, so the rename
    carries the file straight back out of the adopted slot.

    Moving the transcript therefore cannot hold on its own: adoption and Claude
    Code are both asserting where the session lives, and Claude Code asserts it
    last. Rewriting the recorded working directories does not help either — the
    binding lives *inside* ``worktreeSession``, which is not a top-level cwd
    field, so the migration's rewrite never reached it.

    So the binding is neutralised rather than fought. Every ``worktree-state``
    record is rewritten to ``worktreeSession: null`` — byte-for-byte what Claude
    Code itself writes on a clean exit, so resume reads it as "no worktree
    session active" and relocates nothing — and every ``relocated`` stamp is
    repointed at ``dest_root`` so the recorded location agrees with where the
    file now sits. Conversation content is untouched: these are session-metadata
    records, and the worktree they referred to is transient anyway.

    Written to a sibling temp file and atomically replaced, so an interrupted call
    leaves the original transcript intact. Returns the number of records changed.
    """
    dest = str(dest_root)
    temp = path.with_name(path.name + ".worktree-tmp")
    changed = 0
    try:
        with path.open("r", encoding="utf-8") as src, temp.open("w", encoding="utf-8") as out:
            for raw in src:
                stripped = raw.strip()
                if stripped:
                    try:
                        record = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError):
                        record = None
                    if isinstance(record, dict):
                        kind = record.get("type")
                        if kind == "worktree-state" and record.get("worktreeSession") is not None:
                            record["worktreeSession"] = None
                            raw = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                            changed += 1
                        elif kind == "relocated" and record.get("relocatedCwd") not in (None, dest):
                            record["relocatedCwd"] = dest
                            raw = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                            changed += 1
                out.write(raw)
        stat = path.stat()
        os.utime(temp, (stat.st_atime, stat.st_mtime))
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return changed


# --- task namespaces --------------------------------------------------------


def task_namespace_candidates(session_uuid: str) -> list[str]:
    """Namespace directory names an unpinned session may have written under.

    A session launched without a pinned task list gets a namespace derived from
    its own UUID, in one of two observed forms: ``session-<first 8 hex>`` and the
    full UUID. Both are checked; the pinned form is the ai_name itself.
    """
    if not session_uuid:
        return []
    return [f"session-{session_uuid[:8]}", session_uuid]


def merge_task_namespace(source_ns: Path, dest_ns: Path, dry_run: bool = False) -> list[TaskMove]:
    """Move ``<n>.json`` task files from ``source_ns`` into ``dest_ns``.

    Task ids are namespace-scoped integers, so the same ``1.json`` routinely
    exists in both namespaces and holds unrelated work. Colliding files are
    renumbered to the next free id instead of overwriting, the ``id`` field
    inside each moved file is rewritten to match its new name, and ``blocks``
    references between moved tasks are remapped through the same mapping so a
    renumbered dependency does not end up pointing at a stranger's task.
    """
    if not source_ns.is_dir() or source_ns.resolve() == dest_ns.resolve():
        return []

    sources = sorted((p for p in source_ns.glob("*.json") if p.stem.isdigit()), key=lambda p: int(p.stem))
    if not sources:
        return []

    occupied = {int(p.stem) for p in dest_ns.glob("*.json") if p.stem.isdigit()} if dest_ns.is_dir() else set()
    # Reserve the ids that non-colliding sources will keep *before* allocating
    # replacements for the colliding ones. Allocating in a single pass makes an
    # id freshly assigned to an earlier source collide with a later source that
    # could have kept its own id, cascading renumbering across the whole
    # namespace instead of only the files that actually clash.
    keepers = {int(p.stem) for p in sources if int(p.stem) not in occupied}
    taken = occupied | keepers
    mapping: dict[str, str] = {}
    moves: list[TaskMove] = []
    next_id = 1
    for source in sources:
        old_id = source.stem
        if int(old_id) in keepers:
            new_id = old_id
        else:
            while next_id in taken:
                next_id += 1
            new_id = str(next_id)
            taken.add(next_id)
        mapping[old_id] = new_id
        moves.append(
            TaskMove(
                source=source, dest=dest_ns / f"{new_id}.json", renumbered_from=old_id if new_id != old_id else None
            )
        )

    if dry_run:
        return moves

    dest_ns.mkdir(mode=0o700, parents=True, exist_ok=True)
    for move in moves:
        try:
            data = json.loads(move.source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            data["id"] = move.dest.stem
            blocks = data.get("blocks")
            if isinstance(blocks, list):
                data["blocks"] = [mapping.get(str(b), b) if isinstance(b, (str, int)) else b for b in blocks]
            move.dest.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            move.source.unlink()
        else:
            shutil.move(str(move.source), str(move.dest))
    return moves


# --- auto-memory ------------------------------------------------------------


def adopt_memory(
    source_project_dir: Path, dest_project_dir: Path, dry_run: bool = False
) -> tuple[list[Path], list[Path]]:
    """Copy auto-memory into the destination project dir. Never move, never overwrite.

    The rule, and why:

    * **Copy, not move.** ``~/.claude/projects/<slug>/memory/`` is keyed by the
      project slug, not by the session — every session that ever ran from that
      directory shares it. Moving it would silently strip memory from sessions
      still using the source root, which is precisely why ``cc_migrate`` refuses
      to touch it at all. The source copy is left byte-identical.
    * **Never overwrite.** A destination worktree may already carry its own
      memory, which is newer and about the work that actually happened there.
      Clobbering it would destroy the only copy. Files already present are
      reported as conflicts and left alone, for a human to merge.

    Returns ``(copied, conflicts)`` as destination paths.
    """
    source_memory = source_project_dir / "memory"
    dest_memory = dest_project_dir / "memory"
    copied: list[Path] = []
    conflicts: list[Path] = []
    if not source_memory.is_dir():
        return copied, conflicts

    for source in sorted(p for p in source_memory.rglob("*") if p.is_file()):
        target = dest_memory / source.relative_to(source_memory)
        if target.exists():
            conflicts.append(target)
            continue
        copied.append(target)
        if not dry_run:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return copied, conflicts


# --- the resume hand-off tree -----------------------------------------------
#
# ``~/.claude/resume/`` holds the hand-off a session writes for itself so its
# next window — the one that comes up after a compaction — starts with its state
# instead of nothing. Every artifact in it is named after the session's **key**,
# which is the session's *name*. A rename therefore orphans the whole set: the
# next window derives the new name, finds no artifact under it, and injects
# nothing. Adoption renames, so adoption has to carry the tree with it.
#
# THIS IS A BRIDGE (AI-CLI-p6tw), not machinery to build on. The key should never
# have been the name: a title is mutable, while the session UUID naming the
# transcript is stable, and keying the tree by UUID would make a rename free and
# delete this bug class outright. That change spans every producer of a key and is
# not this module's to make. So the code below exists to stop losing hand-offs
# today, and is expected to be **deleted** — not extended — once the key becomes
# the UUID.


#: Subdirectories holding a *latch* rather than a record, never migrated.
#:
#: ``disarmed/<key>`` is the one measured case: its presence suppresses arming,
#: and it is age-bounded, so re-keying it would carry a live suppression across
#: the rename and could leave the adopted session's auto-compact disarmed under
#: its new name. Leaving it behind fails in the safe direction — the latch stays
#: under a key nothing derives any more, and arming works.
_RESUME_LATCH_DIRS = frozenset({"disarmed"})

#: Separators observed between a key and the id that follows it. ``pollers/`` uses
#: a double underscore while ``attempts/``, ``confirmed/`` and ``failed/`` use a
#: hyphen, so matching one separator silently misses whole subdirectories.
_RESUME_SEPARATORS = ("__", "-")

#: What may follow a separator and still prove the leading text was a whole key:
#: a long hex id or a unix timestamp, optionally extended by a suffix.
#:
#: The bound matters, and is why this is not a looser match. Two sessions whose
#: names share a prefix coexist in a real tree (measured: ``oldname`` alongside
#: ``oldname-generic-dragon``), so accepting any hyphen-separated remainder would
#: sweep a *different* session's artifacts into this one's rename. Requiring an id
#: shape rejects ``-generic-dragon.log`` while accepting ``-1789956827.md``, and
#: the lengths are floored well above a session index so ``oldname`` does not
#: claim ``oldname-3.log`` from ``oldname-3``. Observed ids are 32 hex characters
#: and observed timestamps 10 digits, in every file of a 14-subdirectory tree.
_RESUME_ID_RE = re.compile(r"(?:[0-9a-f]{16,}|[0-9]{9,})(?:\..*)?$")


@dataclass
class ResumeMove:
    """One resume artifact, and what the adoption decided to do with it.

    ``dest`` is None exactly when nothing is renamed — a lock that is removed, or
    one left alone. Every action other than ``"move"`` mutates nothing.
    """

    source: Path
    dest: Path | None
    action: str

    def describe(self) -> str:
        where = f"{self.source.parent.name}/{self.source.name}"
        # `dest` is None for every action that renames nothing, so it is narrowed here
        # rather than assumed from the action name -- the two are only kept in step by
        # construction, and a type checker cannot see that.
        if self.dest is not None and self.action in ("move", "conflict"):
            renamed = f"{where} -> {self.dest.name}"
            return renamed if self.action == "move" else f"{renamed} SKIPPED (target exists)"
        if self.action == "remove-stale-lock":
            return f"{where} removed (lock, no live holder)"
        return f"{where} left alone ({self.action})"


def _resume_key_match(name: str, key: str) -> bool:
    """True when ``name`` is an artifact of ``key`` rather than of a longer key.

    Accepts the key alone, the key plus a dotted suffix
    (``<key>.log``, ``<key>.delivered.md``), and the key separated from an id or a
    timestamp (``<key>-<ts>.md``, ``<key>__<id>.json``). Rejects a name that
    merely *starts* with the key — see :data:`_RESUME_ID_RE`.
    """
    if not key or not name.startswith(key):
        return False
    rest = name[len(key) :]
    if rest == "" or rest.startswith("."):
        return True
    for separator in _RESUME_SEPARATORS:
        if rest.startswith(separator):
            return bool(_RESUME_ID_RE.fullmatch(rest[len(separator) :]))
    return False


def resume_artifacts(resume_root: Path, key: str) -> list[Path]:
    """Every file under ``resume_root`` that belongs to ``key``.

    The tree is walked and each name tested, rather than globbed for a list of
    known patterns: the patterns are not uniform, and an enumeration written from
    a pattern list has already missed a whole subdirectory once. Walking also
    means a subdirectory added later is covered without changing this code, and
    that hash-keyed subdirectories (``consumed/``, ``sensing/``) and the
    UUID-named logs in ``logs/`` are skipped because their names do not match a
    session key — not because they are on a list.

    Files sitting directly in ``resume_root`` are deliberately not touched. The
    one measured case, ``wake-ready-<key>-<id>.marker``, carries its key in the
    *middle* of the name and is a pending wake owned by the context supervisor,
    not a record of this session's hand-off.
    """
    found: list[Path] = []
    if not resume_root.is_dir():
        return found
    for subdir in sorted(resume_root.iterdir()):
        if not subdir.is_dir() or subdir.name in _RESUME_LATCH_DIRS:
            continue
        try:
            entries = sorted(subdir.iterdir())
        except OSError:
            continue
        found += [path for path in entries if path.is_file() and _resume_key_match(path.name, key)]
    return found


def _lsof_pids(target: str) -> str:
    """Every pid holding ``target`` open, one per line, as ``lsof -t`` prints them."""
    return subprocess.run(
        ["lsof", "-t", "--", target],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout


def _lock_state(path: Path, lsof_fn: Callable[[str], str] | None = None) -> str:
    """Classify a ``pollers/*.lock`` as ``"held"``, ``"stale"`` or ``"unprovable"``.

    A lock is live coordination state, not a record, and the three outcomes exist
    because migrating a lock and deleting one are both wrong in one direction.
    Re-keying a *stale* lock hands the new key a lock nobody will ever release,
    turning a harmless orphan into an active block on the very mechanism the
    migration is repairing. Deleting a *held* one breaks a running poller.

    The files are zero bytes, so there is no pid to read and holders have to be
    probed by open handle. Where that probe cannot run — Windows, or no ``lsof``
    on PATH — the answer is ``"unprovable"`` and the caller leaves the lock where
    it is: not migrated (so it cannot block the new key) and not deleted (because
    nothing proved it dead). ``lsof_fn`` is injectable so tests need no real
    holder.
    """
    probe = lsof_fn
    if probe is None:
        if shutil.which("lsof") is None:
            return "unprovable"
        probe = _lsof_pids

    try:
        output = probe(str(path))
    except Exception:
        return "unprovable"
    return "held" if any(line.strip().isdigit() for line in str(output).splitlines()) else "stale"


def migrate_resume_artifacts(
    old_key: str,
    new_key: str,
    *,
    claude_home: Path | None = None,
    resume_root: Path | None = None,
    dry_run: bool = False,
    lsof_fn: Callable[[str], str] | None = None,
) -> list[ResumeMove]:
    """Re-key ``old_key``'s resume artifacts to ``new_key``. Moves, never copies.

    Moving is the point. ``history/<key>.jsonl`` is append-only, so a copy leaves
    two partial series that both look authoritative and double-count when
    aggregated. The archival subdirectories (``confirmed/``, ``failed/``,
    ``history/``) are re-keyed along with the live ones: the key is a lookup
    index, not a historical assertion, the session's identity never changed —
    only its title — and an audit trail no reader can find has failed its one
    purpose. The old name survives inside the files.

    An existing target is never overwritten. It is reported as a ``"conflict"``
    and left alone, because the destination artifact belongs to whoever already
    holds the new name and is the only copy of it.
    """
    if not old_key or not new_key or old_key == new_key:
        return []
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    root = resume_root if resume_root is not None else home / "resume"

    planned: list[ResumeMove] = []
    for source in resume_artifacts(root, old_key):
        dest = source.with_name(new_key + source.name[len(old_key) :])
        if source.suffix == ".lock":
            state = _lock_state(source, lsof_fn)
            if state == "stale":
                planned.append(ResumeMove(source=source, dest=None, action="remove-stale-lock"))
                continue
            if state == "unprovable":
                planned.append(ResumeMove(source=source, dest=None, action="unprovable-lock"))
                continue
        planned.append(ResumeMove(source=source, dest=dest, action="conflict" if dest.exists() else "move"))

    if dry_run:
        return planned

    # Only ``"move"`` reaches a rename and only ``"remove-stale-lock"`` reaches an
    # unlink. Every other action is inert by construction rather than by a check
    # placed next to the mutation.
    for move in planned:
        if move.action == "move":
            assert move.dest is not None
            move.dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.move(str(move.source), str(move.dest))
        elif move.action == "remove-stale-lock":
            move.source.unlink(missing_ok=True)
    return planned


def resume_source_keys(transcript: Path, target_key: str) -> list[str]:
    """Every key this session's resume artifacts could be filed under, except the target.

    A rename is not the only way artifacts end up under a key the session no
    longer answers to, which is why migrating only ``old_key -> new_key`` was not
    enough. Two other sources, both measured:

    * **Earlier names.** A session renamed more than once leaves a trail. One
      transcript here had artifacts under FOUR keys — 35 files under its original
      generated name, 11 under an intermediate, 1 under its current one. Adopting
      it without this would have carried 1 file and orphaned 46.
    * **The session UUID.** A bare ``claude`` session has no registry name, so its
      key falls back to the session id. Adoption then makes a name authoritative,
      and every UUID-keyed artifact is stranded.

    Ordered oldest-first so the earliest name is migrated first and the most
    recent wins any conflict over a shared filename — the newer artifact is the
    one the next window actually wants.
    """
    keys = [*transcript_titles(transcript), transcript.stem]
    # Preserve order, drop duplicates and the target (nothing to do for itself).
    seen: list[str] = []
    for key in keys:
        if key and key != target_key and key not in seen:
            seen.append(key)
    return seen


# --- the post-adopt probe ---------------------------------------------------


def probe_resolves(dest_root: Path, title: str, claude_home: Path | None = None) -> Path | None:
    """Return the transcript ``ai c`` would resume for ``title`` in ``dest_root``.

    This is the *same* lookup the launcher performs — a newest-first scan of the
    destination project directory for the first transcript whose *current* title
    matches (see :func:`~ai_cli.cc_migrate.transcript_title`: a rename APPENDS a
    ``customTitle`` record rather than rewriting the first one, so the last record
    is the live name and the first is only the original) — so a pass means resume
    genuinely finds the file,
    and a miss returns None. It reports failure whenever adoption did nothing,
    landed the transcript in the wrong project directory, or left it under the
    wrong title.
    """
    return find_transcript(cc_project_dir(dest_root, claude_home), title=title)


# --- the adoption itself ----------------------------------------------------


def _ensure_worktree(repo_root: Path, ai_name: str, dry_run: bool) -> tuple[Path, bool]:
    """Return ``(worktree_path, created)``, creating it from the resolved worktree base if absent.

    An existing destination is only reused when it really is a worktree *of this
    repository*, checked against ``git worktree list``. Existence alone is not
    enough, and the distinction is not academic: ``.worktrees/<name>`` carries two
    incompatible meanings. This launcher wants it to *be* the session's checkout,
    while per-task agent worktrees are nested *inside* it as
    ``<name>/<task>/<leaf>``. A session started from a repository root — precisely
    the population this command migrates — therefore finds that container sitting
    where its own checkout must go.

    Reusing it would adopt a transcript into a directory holding none of the
    repository's content and rewrite every recorded cwd to point there. The
    refusal is raised in the dry run too: the only symptom of the reuse was a line
    that failed to appear in the preview.
    """
    wt_dir = repo_root / ".worktrees" / ai_name
    if dry_run:
        from .session import _registered_worktree_at, registered_worktrees

        registered = _registered_worktree_at(wt_dir, registered_worktrees(repo_root))
        if registered is not None:
            return registered, False
        if wt_dir.exists():
            raise AdoptionError(
                f"{wt_dir} exists but is not a worktree of {repo_root} — refusing to adopt into it. "
                f"This path has two meanings: `ai c {ai_name}` wants it to BE the session's checkout, "
                f"while per-task agent worktrees are nested INSIDE it as `{ai_name}/<task>/<leaf>`. "
                f"Adopting here would rewrite the transcript's recorded directories to a path holding "
                f"none of the repository's content. Move anything nested there to a sibling container "
                f"and re-run — `git worktree move <nested> "
                f"{wt_dir.parent / (ai_name + '-agents')}/<leaf>` for each registered worktree (a plain "
                f"`mv` leaves git's registration pointing at the old path), or remove the directory if "
                f"it only holds debris."
            )
        return wt_dir, True
    from .session import create_worktree

    try:
        result = create_worktree(ai_name, with_status=True, repo_root=repo_root)
    except RuntimeError as exc:
        raise AdoptionError(str(exc)) from exc
    if not isinstance(result, tuple) or not result[0].is_dir():
        raise AdoptionError(
            f"could not create the session worktree {wt_dir} — create it by hand (git worktree add) and re-run"
        )
    return result


def adopt_session(
    repo_root: Path,
    ai_name: str,
    *,
    source_root: Path | None = None,
    source_project_dir: Path | None = None,
    task_namespace: str | None = None,
    new_title: str | None = None,
    on_collision: str = "gate",
    dry_run: bool = False,
    claude_home: Path | None = None,
    proc_dir: Path | None = None,
) -> AdoptionResult:
    """Adopt the session titled ``ai_name`` into ``<repo_root>/.worktrees/<ai_name>``.

    With ``on_collision="retitle"`` and ``new_title`` supplied — the human's
    answer to a previous run's :class:`TitleCollision` — the source transcript is
    retitled and adopted into the worktree for the *new* title instead. There is
    deliberately no mode that resolves a collision without a human-supplied
    title.

    Raises :class:`LiveSessionError`, :class:`TitleCollision` or
    :class:`InsufficientSpaceError` before writing anything.
    """
    repo_root = repo_root.resolve()
    source_root = (source_root or repo_root).resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"

    target_title = ai_name
    retitled_from: str | None = None
    if new_title:
        if on_collision != "retitle":
            raise AdoptionError("a new title is only meaningful with on_collision='retitle'")
        retitled_from, target_title = ai_name, new_title

    source_dir = (source_project_dir or cc_project_dir(source_root, home)).resolve()
    src_jsonl = find_transcript(source_dir, title=ai_name)
    dest_root_guess = repo_root / ".worktrees" / target_title
    already = probe_resolves(dest_root_guess, target_title, home)

    # Locating the transcript first is read-only, and it makes this refusal
    # *precise*: the session being adopted can be recognised by its UUID as well
    # as by its name. The check deliberately does NOT reject every session
    # sharing the source root — memory is copied rather than moved and each
    # transcript is its own file, so a sibling session in that directory is
    # unaffected. Rejecting those too would mask real errors behind a
    # live-session message: an unknown title reported "still running".
    live_uuid = src_jsonl.stem if src_jsonl is not None else None
    for session in live_sessions(home, proc_dir):
        adopting_this_one = bool(session.name) and session.name in {ai_name, target_title}
        same_transcript = bool(live_uuid) and session.session_id == live_uuid
        holds_destination = session.cwd == str(dest_root_guess)
        if adopting_this_one or same_transcript or holds_destination:
            reason = "is running in the destination worktree" if holds_destination else "is the session being adopted"
            raise LiveSessionError(
                f"session {session.name or '<unnamed>'} (pid {session.pid}, cwd {session.cwd}) {reason} "
                f"and is still running — its transcript and task files are open and still being appended "
                f"to. Exit it first, then adopt."
            )

    if src_jsonl is None:
        if already is not None:
            return AdoptionResult(
                ai_name=ai_name,
                source_root=source_root,
                dest_root=dest_root_guess,
                worktree_created=False,
                migration=None,
                resolved=already,
                dry_run=dry_run,
                already_adopted=True,
            )
        raise AdoptionError(
            f"no transcript titled {ai_name!r} in {source_dir} — nothing to adopt "
            f"(check the title with `ai cc-migrate --dry-run`, or pass -s/--source)"
        )

    candidates = find_title_candidates(repo_root, ai_name, home, extra_dirs=[source_dir])
    if len(candidates) > 1:
        split = split_ai_name(ai_name)
        prefix = split[0] if split else None
        free = next_free_index(repo_root, prefix, home) if prefix else None
        if not new_title:
            raise TitleCollision(ai_name, candidates, free, prefix)

    needed = src_jsonl.stat().st_size + _dir_size(source_dir / src_jsonl.stem)
    needed += _dir_size(source_dir / "memory")
    for namespace in [task_namespace] if task_namespace else task_namespace_candidates(src_jsonl.stem):
        needed += _dir_size(home / "tasks" / namespace)
    dest_project_dir = cc_project_dir(dest_root_guess, home)
    check_free_space(dest_project_dir, needed)

    # Read the naming history BEFORE `migrate_session` runs. It MOVES the
    # transcript, so reading titles off `src_jsonl` afterwards finds nothing and
    # the resume migration below would silently do nothing at all — the same
    # class of silent no-op it exists to fix.
    resume_keys = resume_source_keys(src_jsonl, target_title)

    dest_root, worktree_created = _ensure_worktree(repo_root, target_title, dry_run)

    migration: MigrationResult | None
    worktree_records_cleared = 0
    if dry_run:
        # A dry run must not create the worktree, and ``migrate_session`` refuses
        # to plan into a destination root that does not exist yet — so when the
        # worktree is still to be created, report the source instead of the plan.
        migration = (
            migrate_session(
                source_root,
                dest_root,
                title=ai_name,
                source_project_dir=source_dir,
                dry_run=True,
                claude_home=home,
            )
            if dest_root.is_dir()
            else None
        )
    else:
        migration = migrate_session(
            source_root,
            dest_root,
            title=ai_name,
            source_project_dir=source_dir,
            claude_home=home,
        )
        if retitled_from:
            retitle_transcript(migration.dest_jsonl, retitled_from, target_title)
        # Moving the file is not enough on its own: a stale worktree binding makes
        # Claude Code rename it back out of the slot the next time the session is
        # resumed and left. See :func:`neutralise_worktree_state`.
        worktree_records_cleared = neutralise_worktree_state(migration.dest_jsonl, dest_root)

    dest_project_dir = cc_project_dir(dest_root, home)
    tasks_moved: list[TaskMove] = []
    namespaces = [task_namespace] if task_namespace else task_namespace_candidates(src_jsonl.stem)
    for namespace in namespaces:
        tasks_moved += merge_task_namespace(home / "tasks" / namespace, home / "tasks" / target_title, dry_run)

    memory_copied, memory_conflicts = adopt_memory(source_dir, dest_project_dir, dry_run)

    # Keyed by the session *name*, and a rename is NOT the only way artifacts end
    # up under a key the session no longer answers to. Migrating just
    # `ai_name -> target_title` covered the retitle path and silently did nothing
    # for every other case: a session renamed twice, or a bare one keyed by UUID,
    # kept its hand-off stranded. Measured on a real adoption whose title was
    # already correct: 1 artifact under the current key and 46 under three older
    # ones, all of which the retitle-only version would have left behind. So every
    # historical key is migrated, always, whether or not this adoption renames
    # anything.
    resume_moves: list[ResumeMove] = []
    claimed: set[Path] = set()
    for source_key in resume_keys:
        for move in migrate_resume_artifacts(source_key, target_title, claude_home=home, dry_run=dry_run):
            # A dry run moves nothing, so a second key claiming a destination the
            # first already planned would not see it there and would report two
            # moves where a real run performs one move and one conflict. Tracking
            # the claims keeps the preview honest about what the real run will do,
            # which is the only reason to offer a preview at all.
            if dry_run and move.action == "move" and move.dest is not None:
                if move.dest in claimed:
                    move = ResumeMove(source=move.source, dest=move.dest, action="conflict")
                else:
                    claimed.add(move.dest)
            resume_moves.append(move)

    # ``migrate_session`` warns when the transcript's title differs from the
    # destination worktree's name. In the retitle path that mismatch is the
    # intent and is corrected immediately above, so the warning would be false;
    # the post-adopt probe below is the check that still applies.
    warnings: list[str] = [] if (migration is None or retitled_from) else list(migration.warnings)
    for conflict in [m for m in resume_moves if m.action == "conflict"]:
        warnings.append(
            f"resume artifact {conflict.source} was NOT re-keyed: {conflict.dest} already exists and "
            f"is the only copy of whatever wrote it — merge or remove it by hand, then re-run"
        )
    for stranded in [m for m in resume_moves if m.action == "unprovable-lock"]:
        warnings.append(
            f"resume lock {stranded.source} left in place: this platform cannot prove whether a "
            f"process still holds it, and re-keying a stale lock would block the new key"
        )
    resolved = None
    if not dry_run:
        assert migration is not None
        resolved = probe_resolves(dest_root, target_title, home)
        if resolved != migration.dest_jsonl:
            warnings.append(
                f"post-adopt check FAILED: `ai c` in {dest_root} resolves {resolved} for title "
                f"{target_title!r}, not the adopted {migration.dest_jsonl}"
            )
        if retitled_from:
            # Stepping aside from a taken title only makes sense if whoever already
            # holds it still resolves, so this checks that they do. It has to look in
            # BOTH places a holder can live, because the two are not alternatives:
            # an already-adopted session resolves from its own worktree, while a bare
            # session — the population this command exists to migrate — never had one
            # and resolves from the project root.
            #
            # Probing only the worktree path made the warning fire on every
            # successful bare adoption, which is worse than no warning: it trains the
            # operator to ignore the channel meant to carry real failures.
            searched = [repo_root / ".worktrees" / retitled_from, source_root]
            if all(probe_resolves(root, retitled_from, home) is None for root in searched):
                warnings.append(
                    f"post-adopt check: title {retitled_from!r} no longer resolves in any of "
                    f"{', '.join(str(root) for root in searched)} — the transcript that kept the "
                    f"original title may live elsewhere; verify it by hand"
                )

    return AdoptionResult(
        ai_name=ai_name,
        source_root=source_root,
        dest_root=dest_root,
        worktree_created=worktree_created,
        migration=migration,
        source_jsonl=src_jsonl,
        source_lines=_count_lines(src_jsonl) if migration is None else migration.lines,
        tasks_moved=tasks_moved,
        memory_copied=memory_copied,
        memory_conflicts=memory_conflicts,
        resume_moves=resume_moves,
        retitled_from=retitled_from,
        worktree_records_cleared=worktree_records_cleared,
        resolved=resolved,
        dry_run=dry_run,
        warnings=warnings,
    )


def titled_sessions(source_root: Path, claude_home: Path | None = None) -> list[str]:
    """Every distinct ``customTitle`` in ``source_root``'s project directory."""
    project_dir = cc_project_dir(source_root, claude_home)
    if not project_dir.is_dir():
        return []
    titles: list[str] = []
    for path in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        found = transcript_title(path)
        if found and found not in titles:
            titles.append(found)
    return titles


def adopt_all(
    repo_root: Path,
    *,
    source_root: Path | None = None,
    dry_run: bool = False,
    claude_home: Path | None = None,
    proc_dir: Path | None = None,
) -> list[tuple[str, AdoptionResult | AdoptionError]]:
    """Adopt every titled session in ``source_root``, one entry per title.

    A refusal for one session is recorded and the batch continues: a collision or
    a live session is information about *that* conversation, and aborting the run
    would strand the rest for no reason.
    """
    root = (source_root or repo_root).resolve()
    outcomes: list[tuple[str, AdoptionResult | AdoptionError]] = []
    for title in titled_sessions(root, claude_home):
        try:
            outcomes.append(
                (
                    title,
                    adopt_session(
                        repo_root,
                        title,
                        source_root=root,
                        dry_run=dry_run,
                        claude_home=claude_home,
                        proc_dir=proc_dir,
                    ),
                )
            )
        except AdoptionError as exc:
            outcomes.append((title, exc))
    return outcomes
