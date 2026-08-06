"""Survey every titled Claude Code session on this machine, and optionally adopt them.

Why this exists
---------------
``ai session-adopt`` fixes *one* session whose name and location you already
know. Answering "which titled sessions exist, where do they really live, and
which ones will ``ai c <n>`` fail to resume" was, until now, a set of throwaway
one-liners — and the two defects that motivated this command are both cases where
the answer a human *believed* was wrong:

1. A session recorded as living at a repo root actually lived in a Claude Code
   *agent* worktree under ``<repo>/.claude/worktrees/<id>``. Adopting it by title
   from the repo root failed with "no transcript titled X" until the real source
   directory was supplied by hand. Nothing had to be supplied: the transcript
   records its own cwd, so the survey can find it and hand it to the adopter.
2. Two transcripts claimed the same title. The adoption gate refused, correctly,
   but only at the moment of writing — nothing surfaced the collision in advance.

Discovery direction matters
---------------------------
This module scans **outward from ``~/.claude/projects/``**, not inward from a list
of repositories. That is deliberate. Walking known repos means enumerating every
place a session may sit (repo root, ``<repo>/.worktrees/<name>``,
``<repo>/.claude/worktrees/<id>``, and whatever convention appears next), and a
place nobody thought to enumerate is silently missing from the report — the exact
shape of defect 1. Every session, wherever it ran, has a project directory under
``~/.claude/projects/``, and each transcript records the working directory it ran
in. So the transcripts are the census, and the repo is *derived* from the cwd
rather than assumed.

The slug is a one-way function
------------------------------
``cc_project_dir`` maps a cwd to a directory name by replacing every
non-alphanumeric character with ``-``, so ``/home/user/my_proj`` and
``/home/user/my-proj`` slugify identically and the mapping cannot be inverted.
The recorded cwd is therefore the only reliable source of a session's real path.
It is *checked* against the containing directory by re-slugifying it: a match
proves the pair is consistent, and a mismatch is reported rather than hidden,
because it means the transcript was moved without its cwd fields being rewritten.

Refusals are reported, never routed around
------------------------------------------
The live-session and duplicate-title refusals belong to
:mod:`ai_cli.session_adopt` and stay there. This module classifies a session as
un-adoptable for those reasons *before* calling the adopter, so a bulk run reports
them as expected skips and keeps going; the adopter's own gates remain the
authority and still fire. Nothing here weakens them, and ``--yes`` covers only the
interactive confirmation.

Depends only on the standard library and the shipped adoption module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .cc_migrate import cc_project_dir
from .session_adopt import (
    AdoptionError,
    AdoptionResult,
    adopt_session,
    describe_candidate,
    live_sessions,
    probe_resolves,
    split_ai_name,
)

#: Directory holding a repo's ``ai c`` session worktrees.
WORKTREES = ".worktrees"

#: Directory holding Claude Code's own agent worktrees, relative to a repo root.
AGENT_WORKTREES = (".claude", "worktrees")

#: How a session's working directory relates to the repo that owns it.
REPO_ROOT = "repo-root"
SESSION_WORKTREE = "session-worktree"
AGENT_WORKTREE = "agent-worktree"
REPO_SUBDIR = "repo-subdir"
UNKNOWN = "unknown"


@dataclass
class SessionRecord:
    """One titled transcript, described well enough to decide what to do with it.

    ``in_correct_slot`` is a fact about **where the transcript file sits** — whether
    its project directory is the one the worktree slot slugifies to — and never
    about what any record's ``cwd`` says. That distinction is the whole bug this
    field once had: an adoption moves the transcript (which is what makes ``ai c``
    resolve it) and rewrites the cwd fields it needs to, but a long transcript
    legitimately keeps thousands of historical cwds pointing at the old location,
    including sub-agent paths. Deciding residency from the recorded cwd therefore
    reported fully-adopted sessions as still needing adoption. ``slug_matches``
    keeps that cwd-vs-directory comparison, as information only.
    """

    title: str
    transcript: Path
    project_dir: Path
    lines: int
    size: int
    cwd: str
    repo_root: Path | None
    location: str
    in_correct_slot: bool
    slug_matches: bool
    live_pid: int | None
    resolves: Path | None
    index: int | None
    claimants: int = 1

    @property
    def live(self) -> bool:
        return self.live_pid is not None

    @property
    def collides(self) -> bool:
        return self.claimants > 1

    @property
    def slot(self) -> Path | None:
        """Where ``ai c <index>`` would look for this session, if a repo is known."""
        if self.repo_root is None:
            return None
        return self.repo_root / WORKTREES / self.title

    def describe(self) -> str:
        flags = [self.location]
        if self.live:
            flags.append(f"LIVE pid={self.live_pid}")
        if self.collides:
            flags.append(f"COLLIDES x{self.claimants}")
        if not self.slug_matches:
            flags.append("slug-mismatch")
        flags.append("in-slot" if self.in_correct_slot else "not-in-slot")
        flags.append("resolvable" if self.resolves else "NOT-resolvable")
        return (
            f"{self.title}\n"
            f"    transcript={self.transcript} lines={self.lines} size={self.size / 1_000_000:.2f} MB\n"
            f"    cwd={self.cwd or '<unrecorded>'}\n"
            f"    repo={self.repo_root or '<none>'} [{', '.join(flags)}]"
        )


@dataclass
class AuditReport:
    """Every titled session found, plus the titles more than one of them claims."""

    sessions: list[SessionRecord] = field(default_factory=list)
    collisions: dict[str, list[SessionRecord]] = field(default_factory=dict)
    scanned_project_dirs: int = 0
    scanned_transcripts: int = 0

    @property
    def repos(self) -> list[Path]:
        seen: list[Path] = []
        for record in self.sessions:
            if record.repo_root is not None and record.repo_root not in seen:
                seen.append(record.repo_root)
        return seen


def _is_repo_root(path: Path) -> bool:
    """True when ``path`` looks like the top of a repository.

    ``.git`` may be a directory (ordinary clone) or a file (a git worktree), so
    ``exists`` rather than ``is_dir``. A directory holding ``.worktrees`` also
    counts: that is the structure this tool is about, and it lets a repo whose
    ``.git`` has not been created yet still be attributed rather than dropped.
    """
    return (path / ".git").exists() or (path / WORKTREES).is_dir()


def owning_repo(cwd: Path) -> tuple[Path | None, str]:
    """Map a session's working directory to the repo that owns it, and how.

    Path *shape* is decisive for the two worktree conventions: anything under
    ``<repo>/.worktrees/`` or ``<repo>/.claude/worktrees/`` belongs to ``<repo>``
    by construction, whether or not that directory still exists on disk — a
    cleaned-up worktree must still be attributed, since its transcript survives it.
    Only when neither convention appears does this fall back to walking up for a
    repository marker.
    """
    parts = cwd.parts
    for index in range(len(parts) - 1, 0, -1):
        if parts[index] == WORKTREES:
            return Path(*parts[:index]), SESSION_WORKTREE
        if parts[index - 1 : index + 1] == AGENT_WORKTREES:
            return Path(*parts[: index - 1]), AGENT_WORKTREE

    probe = cwd
    while True:
        if _is_repo_root(probe):
            return probe, REPO_ROOT if probe == cwd else REPO_SUBDIR
        if probe == probe.parent:
            return None, UNKNOWN
        probe = probe.parent


def _live_pid_for(transcript: Path, title: str, cwd: str, live: list) -> int | None:
    """The pid of the running session this transcript belongs to, if any.

    Matched by session UUID first (the transcript's filename — exact), then by
    name, then by working directory. All three are needed: a session record may
    omit the UUID, a session may have been renamed, and a session started without
    a name is identifiable only by where it runs.
    """
    uuid = transcript.stem
    for session in live:
        if (
            (session.session_id and session.session_id == uuid)
            or (session.name and session.name == title)
            or (cwd and session.cwd == cwd)
        ):
            return session.pid
    return None


def survey(
    *,
    claude_home: Path | None = None,
    proc_dir: Path | None = None,
    repo: Path | None = None,
    title: str | None = None,
) -> AuditReport:
    """Build an :class:`AuditReport` for every titled session under ``~/.claude``.

    ``repo`` and ``title`` narrow what is *reported*, but collisions are always
    computed over the whole census first: a title claimed by a transcript outside
    the filter is still a title claimed twice, and hiding it would make a narrowed
    run look safe when it is not.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    projects = home / "projects"
    live = live_sessions(home, proc_dir)

    records: list[SessionRecord] = []
    scanned_dirs = 0
    scanned_transcripts = 0
    if projects.is_dir():
        for project_dir in sorted(projects.iterdir()):
            if not project_dir.is_dir():
                continue
            scanned_dirs += 1
            for path in sorted(project_dir.glob("*.jsonl")):
                scanned_transcripts += 1
                candidate = describe_candidate(path)
                if not candidate.title:
                    continue
                records.append(_build_record(candidate, project_dir, home, live))

    groups: dict[str, list[SessionRecord]] = {}
    for record in records:
        groups.setdefault(record.title, []).append(record)
    for group in groups.values():
        for record in group:
            record.claimants = len(group)

    kept = [
        record
        for record in records
        if (title is None or record.title == title) and (repo is None or record.repo_root == repo.resolve())
    ]
    collisions = {
        name: group for name, group in sorted(groups.items()) if len(group) > 1 and any(r in kept for r in group)
    }
    kept.sort(key=lambda r: (str(r.repo_root or ""), r.title))
    return AuditReport(
        sessions=kept,
        collisions=collisions,
        scanned_project_dirs=scanned_dirs,
        scanned_transcripts=scanned_transcripts,
    )


def _build_record(candidate, project_dir: Path, home: Path, live: list) -> SessionRecord:
    """Turn one titled transcript into a :class:`SessionRecord`."""
    cwd = candidate.cwd
    repo_root: Path | None = None
    location = UNKNOWN
    slug_matches = False
    if cwd:
        repo_root, location = owning_repo(Path(cwd))
        slug_matches = cc_project_dir(Path(cwd), home) == project_dir

    split = split_ai_name(candidate.title)
    slot = repo_root / WORKTREES / candidate.title if repo_root is not None else None
    return SessionRecord(
        title=candidate.title,
        transcript=candidate.path,
        project_dir=project_dir,
        lines=candidate.lines,
        size=candidate.size,
        cwd=cwd,
        repo_root=repo_root,
        location=location,
        in_correct_slot=slot is not None and project_dir == cc_project_dir(slot, home),
        slug_matches=slug_matches,
        live_pid=_live_pid_for(candidate.path, candidate.title, cwd, live),
        resolves=probe_resolves(slot, candidate.title, home) if slot is not None else None,
        index=split[1] if split else None,
    )


def triage(report: AuditReport) -> tuple[list[SessionRecord], list[tuple[SessionRecord, str]]]:
    """Split the survey into what is safe to adopt and what must be skipped, with reasons.

    A duplicate title is skipped wherever it occurs, not only within one repo.
    Titles double as worktree directory names and as the argument to ``ai c``, so
    a duplicate anywhere is a human decision about which conversation keeps the
    name — and auto-adopting one of the two would settle that decision silently.
    The adopter's own unconditional gate would refuse a same-repo duplicate
    regardless; this makes the cross-repo case a reported skip rather than a
    surprise.
    """
    ready: list[SessionRecord] = []
    skipped: list[tuple[SessionRecord, str]] = []
    for record in report.sessions:
        if record.collides:
            skipped.append(
                (
                    record,
                    f"title collision — {record.claimants} transcripts claim {record.title!r}; a human must choose",
                )
            )
        elif record.live:
            skipped.append(
                (
                    record,
                    f"live session (pid {record.live_pid}) — adopting it would move its transcript "
                    f"out from under a running process",
                )
            )
        elif not record.cwd:
            skipped.append((record, "transcript records no cwd — cannot tell which directory it ran in"))
        elif record.repo_root is None:
            skipped.append((record, f"no owning repo found above {record.cwd} — nothing to adopt it into"))
        elif record.index is None:
            skipped.append((record, f"title {record.title!r} is not <prefix>-<index>, so `ai c <n>` cannot address it"))
        elif record.in_correct_slot and record.resolves is not None:
            skipped.append((record, "already adopted — it is in its worktree slot and `ai c` resolves it"))
        else:
            ready.append(record)
    return ready, skipped


def adopt_ready(
    report: AuditReport,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
    proc_dir: Path | None = None,
) -> tuple[list[tuple[SessionRecord, AdoptionResult | AdoptionError]], list[tuple[SessionRecord, str]]]:
    """Adopt everything :func:`triage` cleared, delegating entirely to ``session_adopt``.

    ``source_root`` is the working directory the transcript itself recorded — this
    is what makes a session living in an agent worktree adoptable without anyone
    supplying its path. A refusal is recorded against its session and the batch
    continues.
    """
    ready, skipped = triage(report)
    outcomes: list[tuple[SessionRecord, AdoptionResult | AdoptionError]] = []
    for record in ready:
        try:
            outcomes.append(
                (
                    record,
                    adopt_session(
                        record.repo_root,
                        record.title,
                        source_root=Path(record.cwd),
                        dry_run=dry_run,
                        claude_home=claude_home,
                        proc_dir=proc_dir,
                    ),
                )
            )
        except AdoptionError as exc:
            outcomes.append((record, exc))
    return outcomes, skipped
