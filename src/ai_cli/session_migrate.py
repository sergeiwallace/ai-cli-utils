"""Migrate a registered ``ai`` session to a new name (``ai session-migrate OLD NEW``).

``ai session-adopt`` brings in a session that was started *outside* ``ai c``. This
module moves one that already lives in a slot -- ``<repo>/.worktrees/<old>`` -- to
``<repo>/.worktrees/<new>``, carrying everything keyed by the old name or by the
old worktree path, so ``ai c <new>`` resumes the same conversation with the same
tasks, memory and resume hand-off:

* the worktree (``git worktree move``), its ``wt-<old>`` branch whether checked
  out or parked behind a topic branch (anchored first, see
  :mod:`ai_cli.branch_rename`), and every ``branch.*.<prefix>-session-home`` marker
  naming the old path;
* the canonical-session registry entry for the old path;
* the Claude Code project directory (transcripts, sidecars, worktree-slug
  memory), with each transcript's recorded cwd repointed, its ``customTitle``
  renamed and its stale worktree binding cleared;
* the ``memory/.held-output-<old>-NN-<slug>.md`` replay files in the main tree's
  and the slot's project directories (filename prefix only), and the memory files
  named for the session there (``active-work-<old>.md``, ``.resume-<old>-N.md``) with
  the links to them in ``MEMORY.md``;
* the old name's locks, ``.worktrees/.<old>.lock`` and ``ai-watcher-lock-*-<old>``,
  removed once no process holds them, and an emptied ``<old>-agents`` container;
* the task namespace and the resume tree, through
  :func:`ai_cli.session_adopt.rekey_name_artifacts`, the executor ``session-adopt``
  uses for the same two; the old namespace directory is removed once only its
  bookkeeping remains;
* the launcher's own state: session metadata, the wrapper script (regenerated, so
  ``CLAUDE_CODE_TASK_LIST_ID`` and ``--name`` follow), the resume prompt, the
  session maps, the iTerm2 restore registry and the ``launch-logs/`` files of a launch
  named for the session;
* the chief-of-staff registration ``ai cos`` wrote, when it names the old session
  (``state/chief-session.json`` and ``registry.json``'s chief fields), unless its tmux
  target is live;
* the session's agent worktrees under ``<repo>/.worktrees/<old>-agents/`` and its
  ``wt/<old>/*`` branches in every repo under ``projects_dir``
  (:mod:`ai_cli.session_migrate_agents`), except one a live process is working in;
* every slot still named ``<repo>/.worktrees/<old>`` in another repo, retired through
  :func:`ai_cli.session_retire.retire_session` (tips anchored, checkout removed) once
  that proves it clean and landed, and kept and named, failing the run, otherwise; and
  every ``<old>-agents`` container left holding a file is named;
* every Beads issue assigned to exactly ``<old>`` and not closed, in the slot's repo and
  every directory under ``projects_dir`` with a ``.beads/`` store, re-keyed to ``<new>``
  through ``bd update --if-assignee`` and given a comment; bd runs with ``--sandbox``, so
  each changed store is named as needing its publish step, and a store bd cannot read is
  named and fails the run. Each completed migration is appended to
  :data:`MIGRATION_LEDGER`, the record a later ``--finish`` needs before it re-keys;
  a ``--finish`` that completes appends a ``finished`` row for the pair, and ``ai c
  <new>`` runs the finish of any recorded pair without one (:func:`pending_finishes`);
* whatever the external hooks in ``session-migrate.d/`` declare
  (:mod:`ai_cli.session_migrate_hooks`), each planned with ``--dry-run`` before
  the first write and run after the core moves.

Every refusal is checked before the first write, in ``--dry-run`` too: the slot
must be a registered worktree, nothing may already exist at the new slot or its
project directory, no nested worktree may live inside the old slot, and the
session must not be running -- neither a Claude Code process nor the tmux session
whose wrapper would restart it.

A slot that is a migrated session's leftover is cleaned up instead of migrated: its
branch already carries the ``wt/<new>/`` prefix, it has no Claude Code project
directory, and another repo holds the registered ``.worktrees/<new>``. That run, with
or without ``--finish``, touches only the old-name slots and empty ``<old>-agents``
containers in every repo (:func:`clean_leftovers`), so it proceeds while ``<new>`` is
live elsewhere. It still refuses while a process named ``<old>`` runs or any process
works inside the slot.

Nothing is overwritten. A destination that already exists is reported and left
alone, and a lock is never carried to the new name (the session is not running,
so it has no holder).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import config as _config
from . import session_migrate_agents as _agents
from . import session_migrate_hooks as _hooks
from . import session_registry as _session_registry
from .branch_rename import rename_branch_anchored
from .canonical_worktrees import (
    CanonicalWorktreeRegistryError,
    get_canonical_worktree_registry_path,
    move_canonical_worktree,
)
from .cc_migrate import _rewrite_line, cc_project_dir, transcript_titles
from .git_repair import _git_env
from .session_adopt import (
    AdoptionError,
    LiveSessionError,
    ResumeMove,
    TaskMove,
    live_sessions,
    neutralise_worktree_state,
    probe_resolves,
    rekey_name_artifacts,
    retitle_transcript,
)
from .worktree_move import move_worktree

ENGINES = ("c", "g", "p", "cx")

#: A session name: lowercase hyphen-separated segments ending in ``-<N>``.
_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-\d+")

#: State files named ``<stem><tmux session>``, each a record of that session.
_STATE_FILE_STEMS = (
    "cc-resume-prompt-",
    "gg-resume-prompt-",
    "gg-reload-",
    "gg-restart-",
    "session-agent-exits-",
)


class MigrationError(AdoptionError):
    """A refusal raised before ``session-migrate`` writes anything."""


@dataclass
class PlannedMove:
    """One artifact the migration moves or rewrites, or deliberately leaves."""

    kind: str
    source: str
    dest: str
    action: str = "move"

    def describe(self) -> str:
        if self.action == "move":
            return f"{self.kind}: {self.source} -> {self.dest}"
        if self.action == "conflict":
            return f"{self.kind}: {self.source} -> {self.dest} SKIPPED (target exists)"
        if self.action == "report":
            return f"{self.kind}: {self.source}"
        return f"{self.kind}: {self.source} ({self.action}{': ' + self.dest if self.dest else ''})"


@dataclass
class MigrationResult:
    """What a migration did, or would do under ``dry_run``."""

    old: str
    new: str
    old_worktree: Path
    new_worktree: Path
    moves: list[PlannedMove] = field(default_factory=list)
    task_moves: list[TaskMove] = field(default_factory=list)
    resume_moves: list[ResumeMove] = field(default_factory=list)
    resolved: Path | None = None
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    #: Each old-name slot in another repo left in place, as ``<path>: <reason>``; any makes the run fail.
    kept: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """Whether the run left work undone: a kept slot or a ``FAILED`` warning."""
        return bool(self.kept) or any("FAILED" in w for w in self.warnings)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(), check=False)


def _tmux_names() -> list[str]:
    try:
        from .session import _tmux_session_names

        return _tmux_session_names()
    except OSError:
        return []


def _session_variants(name: str) -> list[str]:
    """Every tmux session name ``ai`` gives the session ``name``: ``c-<name>``, ``c-r-<name>``, ..."""
    return [f"{engine}{remote}-{name}" for engine in ENGINES for remote in ("", "-r")]


def _refuse_live(
    old: str,
    new: str,
    old_wt: Path,
    new_wt: Path,
    home: Path,
    proc_dir: Path | None,
    tmux: list[str],
    *,
    verb: str = "migrated",
) -> None:
    for session in live_sessions(home, proc_dir):
        cwd = Path(session.cwd).resolve(strict=False) if session.cwd else None
        inside = cwd is not None and (cwd in (old_wt, new_wt) or old_wt in cwd.parents)
        if session.name in {old, new} or inside:
            raise LiveSessionError(
                f"session {session.name or '<unnamed>'} (pid {session.pid}, cwd {session.cwd}) is still "
                f"running in the slot being {verb} -- its transcript and task files are open. Exit it first."
            )
    hosting = sorted(set(tmux) & set(_session_variants(old)))
    if hosting:
        raise LiveSessionError(
            f"tmux session {', '.join(hosting)} still hosts {old}, and its wrapper restarts the agent on "
            f"exit. Exit the session and end the tmux session (`tmux kill-session -t {hosting[0]}`) first."
        )


def _session_home_markers(repo: Path, old_wt: Path) -> list[tuple[str, str]]:
    """``(key, value)`` for every session-home marker naming ``old_wt``.

    The environment's session-start tooling records a session's home as
    ``branch.<branch>.<prefix>-session-home = <worktree path>``, ``<prefix>`` being
    ``[worktree] marker_prefix``; unset, there is no marker to find.
    """
    pattern = _agents.session_home_pattern()
    if pattern is None:
        return []
    res = _git(repo, "config", "--get-regexp", pattern)
    found: list[tuple[str, str]] = []
    for line in (res.stdout or "").splitlines():
        key, _, value = line.partition(" ")
        if value and Path(value).resolve(strict=False) == old_wt:
            found.append((key, value))
    return found


def _rewrite_transcript_cwd(path: Path, old_root: str, new_root: str) -> None:
    """Repoint ``path``'s recorded cwd fields from ``old_root`` to ``new_root``, in place.

    Uses the line rewriter ``cc-migrate`` uses, so only top-level cwd fields change and
    conversation text keeps the paths it was written with. Atomic, mtime preserved.
    """
    temp = path.with_name(path.name + ".migrate-tmp")
    try:
        with path.open("r", encoding="utf-8") as src, temp.open("w", encoding="utf-8") as out:
            for raw in src:
                out.write(_rewrite_line(raw, old_root, new_root))
        stat = path.stat()
        os.utime(temp, (stat.st_atime, stat.st_mtime))
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _highwatermark(ns: Path) -> int:
    try:
        return int((ns / ".highwatermark").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _plan_state(old: str, new: str, state: Path) -> list[PlannedMove]:
    moves: list[PlannedMove] = []
    for old_session, new_session in zip(_session_variants(old), _session_variants(new), strict=True):
        pairs = [
            ("session meta", state / f"session-meta-{old_session}.json", state / f"session-meta-{new_session}.json"),
            ("wrapper script", state / "sessions" / f"{old_session}.sh", state / "sessions" / f"{new_session}.sh"),
            *(
                ("session state", state / f"{stem}{old_session}", state / f"{stem}{new_session}")
                for stem in _STATE_FILE_STEMS
            ),
        ]
        for kind, source, dest in pairs:
            if source.exists():
                moves.append(PlannedMove(kind, str(source), str(dest), "conflict" if dest.exists() else "move"))
        lock = state / f"ai-watcher-lock-{old_session}"
        if lock.exists():
            holder = _watcher_holder(lock)
            if holder is None:
                moves.append(PlannedMove("watcher lock", str(lock), "", _STALE_LOCK))
            else:
                moves.append(PlannedMove("watcher lock", str(lock), "", f"left alone: {holder}"))
    return moves


#: The action of a stale lock the migration removes; never carried to the new name.
_STALE_LOCK = "removed: no live holder"


def _watcher_holder(lock: Path) -> str | None:
    """Why ``lock`` may still be held, or ``None`` when it provably is not.

    The wrapper's watcher writes its shell's pid into the file and removes the file on exit,
    so a file naming a pid that no longer runs is a leftover. Anything else, a live pid or a
    file that names no pid, is kept: a pid that answers may be the watcher itself.
    """
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "it names no pid, so its holder cannot be ruled out"
    if pid <= 0:
        return f"it names pid {pid}"
    return f"its pid {pid} is running" if _config._pid_alive(pid) else None


def _plan_session_maps(old: str, new: str, home: Path, state: Path) -> list[PlannedMove]:
    moves: list[PlannedMove] = []
    for path in (home / "cc-sessions.json", state / "gemini_sessions.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and old in data:
            action = "conflict" if new in data else "move"
            moves.append(PlannedMove("session map", f"{path} [{old}]", f"[{new}]", action))
    return moves


def _plan_held_output(old: str, new: str, dirs: list[tuple[Path, Path | None]]) -> list[PlannedMove]:
    """Rename ``memory/.held-output-<old>-NN-<slug>.md`` replay files in each project dir.

    Each entry of ``dirs`` is ``(project dir, where it will live after the migration)``,
    ``None`` for a directory that stays put. Only the filename prefix changes: a
    replay file is replayed byte for byte, so its body is never touched.
    """
    pattern = re.compile(rf"\.held-output-{re.escape(old)}(-\d{{2,}}-.+\.md)")
    moves: list[PlannedMove] = []
    for project, after in dirs:
        memory = project / "memory"
        if not memory.is_dir():
            continue
        for source in sorted(memory.iterdir()):
            match = pattern.fullmatch(source.name)
            if not match or not source.is_file():
                continue
            name = f".held-output-{new}{match.group(1)}"
            action = "conflict" if (memory / name).exists() else "move"
            moves.append(PlannedMove("held output", str(source), str((after or project) / "memory" / name), action))
    return moves


def _memory_pattern(old: str) -> re.Pattern[str]:
    """``<prefix>-<old>.md`` or ``<prefix>-<old>-<N>.md``: a memory file keyed by the session name.

    The prefix is lowercase words (``active-work-``, ``.resume-``), so a ``.held-output-``
    replay file, whose suffix carries a slug, is never matched here.
    """
    return re.compile(rf"(?P<prefix>\.?[a-z]+(?:-[a-z]+)*-){re.escape(old)}(?P<suffix>(?:-\d+)?\.md)")


def _names_another_session(prefix: str, old: str, others: set[str]) -> bool:
    """Whether ``<prefix><old>`` ends in a different session's name, e.g. ``kg-myproject-3`` for ``myproject-3``."""
    words = prefix.lstrip(".").rstrip("-").split("-")
    return any("-".join([*words[i:], old]) in others for i in range(len(words)))


def _plan_memory(
    old: str, new: str, dirs: list[tuple[Path, Path | None]], others: set[str]
) -> tuple[list[PlannedMove], list[PlannedMove]]:
    """``(renames, index rewrites)`` for the memory files named for the session in each project dir.

    A memory directory is shared by every session of a repo, so only a file whose name ends in
    exactly the old session's name is renamed, never one that ends in a longer session name
    that happens to contain it (``others``). Each ``MEMORY.md`` that names a renamed file has
    that file name rewritten, and nothing else in it.
    """
    pattern = _memory_pattern(old)
    renames: list[PlannedMove] = []
    rewrites: list[PlannedMove] = []
    for project, after in dirs:
        memory = project / "memory"
        if not memory.is_dir():
            continue
        here: list[PlannedMove] = []
        for source in sorted(memory.iterdir()):
            match = pattern.fullmatch(source.name)
            if not match or not source.is_file() or _names_another_session(match["prefix"], old, others):
                continue
            name = f"{match['prefix']}{new}{match['suffix']}"
            action = "conflict" if (memory / name).exists() else "move"
            here.append(PlannedMove("memory file", str(source), str((after or project) / "memory" / name), action))
        renames += here
        index = memory / "MEMORY.md"
        moved = {Path(m.source).name: Path(m.dest).name for m in here if m.action == "move"}
        if moved and index.is_file():
            text = index.read_text(encoding="utf-8")
            named = sorted(name for name in moved if _rewrite_index(text, {name: moved[name]})[1])
            if named:
                rewrites.append(PlannedMove("memory index", str(index), ", ".join(named), "rewrite"))
    return renames, rewrites


def _rewrite_index(text: str, renames: dict[str, str]) -> tuple[str, int]:
    """``text`` with each renamed file name, and its ``[[stem]]`` link, naming the new file."""
    count = 0
    for old_name, new_name in renames.items():
        for before, after in ((old_name, new_name), (f"[[{old_name[:-3]}]]", f"[[{new_name[:-3]}]]")):
            text, hits = re.subn(rf"(?<![\w.-]){re.escape(before)}(?![\w-])", after, text)
            count += hits
    return text, count


def _apply_memory(renames: list[PlannedMove], rewrites: list[PlannedMove], result: MigrationResult) -> None:
    """Rename the planned memory files, then rewrite each index that names them, in place."""
    for item in [m for m in renames if m.action == "move"]:
        source = Path(item.source)
        target = source.with_name(Path(item.dest).name)
        if target.exists():
            result.warnings.append(f"memory file {source} was NOT renamed: {target} appeared meanwhile")
            continue
        source.rename(target)
    for item in rewrites:
        index = Path(item.source)
        moved = {
            Path(m.source).name: Path(m.dest).name
            for m in renames
            if m.action == "move" and Path(m.source).parent == index.parent
        }
        before = index.read_text(encoding="utf-8")
        text, _hits = _rewrite_index(before, moved)
        temp = index.with_name(index.name + ".migrate-tmp")
        temp.write_text(text, encoding="utf-8")
        # Another live session may edit the shared index; replace it only if it is unchanged.
        if index.read_text(encoding="utf-8") != before:
            temp.unlink()
            result.warnings.append(f"memory index {index} changed during the migration; re-run with --finish")
            continue
        temp.replace(index)


def _session_names(home: Path, repo_root: Path, *exclude: str) -> set[str]:
    """Every other session name this host knows: task namespaces and slot directories."""
    names: set[str] = set()
    for parent in (home / "tasks", repo_root / ".worktrees"):
        if parent.is_dir():
            names.update(p.name for p in parent.iterdir() if p.is_dir())
    return names - set(exclude)


def _plan_slot_lock(repo_root: Path, old: str) -> list[PlannedMove]:
    """The launcher's ``.worktrees/.<old>.lock``: removed when no process holds it."""
    lock = repo_root / ".worktrees" / f".{old}.lock"
    if not lock.is_file():
        return []
    action = _STALE_LOCK if _slot_lock_free(lock) else "left alone: a process holds it"
    return [PlannedMove("slot lock", str(lock), "", action)]


def _slot_lock_free(lock: Path) -> bool:
    """Whether a non-blocking exclusive lock on ``lock`` succeeds, i.e. no launcher holds it."""
    import portalocker

    try:
        with portalocker.Lock(str(lock), mode="a", timeout=0, fail_when_locked=True):
            return True
    except (portalocker.exceptions.LockException, OSError):
        return False


def _remove_stale_locks(moves: list[PlannedMove]) -> None:
    """Unlink each lock planned as stale, re-proving it free right before the unlink."""
    for item in [m for m in moves if m.action == _STALE_LOCK]:
        lock = Path(item.source)
        free = _slot_lock_free(lock) if item.kind == "slot lock" else _watcher_holder(lock) is None
        if free:
            lock.unlink(missing_ok=True)


#: Task-namespace bookkeeping that means nothing once the namespace's tasks have moved.
_NAMESPACE_BOOKKEEPING = (".highwatermark", ".lock")


def _namespace_leftovers(ns: Path, moving: set[str]) -> list[str]:
    """Entries of ``ns`` that are neither bookkeeping nor among the task files in ``moving``."""
    return sorted(p.name for p in ns.iterdir() if p.name not in _NAMESPACE_BOOKKEEPING and p.name not in moving)


def _plan_hooks(old: str, new: str, repo_root: Path, report: list[PlannedMove] | None) -> list[_hooks.Hook]:
    """Run every external hook with ``--dry-run``; any refusal refuses the migration.

    The hooks' planned lines are appended to ``report`` when one is given.
    """
    try:
        hooks, runs = _hooks.plan_hooks(old, new, cwd=repo_root)
    except _hooks.HookRefusal as exc:
        raise MigrationError(str(exc)) from exc
    if report is not None:
        report += [PlannedMove(f"hook {run.hook.name}", line, "", "report") for run in runs for line in run.lines]
    return hooks


def _plan_iterm2(old: str) -> list[PlannedMove]:
    try:
        doc = _session_registry.load_registry()
    except _session_registry.RegistryError:
        return []
    names = set(_session_variants(old))
    hits = [r for r in doc["sessions"] if isinstance(r, dict) and r.get("name") in names]
    if not hits:
        return []
    return [PlannedMove("iTerm2 restore record", f"{_session_registry.registry_path()} ({len(hits)})", "", "rewrite")]


@dataclass
class _Leftover:
    """A slot ``<repo>/.worktrees/<old>`` outside the session's home, and whether it may go."""

    repo: Path
    slot: Path
    kept: str | None = None


def _one_line(reason: str) -> str:
    reason = re.sub(r":\s*\n\s*", ": ", reason.strip())
    return "; ".join(part.strip() for part in reason.splitlines() if part.strip())


def _registered_slot(repo: Path, name: str) -> Path | None:
    from .session import _registered_worktree_at, registered_worktrees

    return _registered_worktree_at(repo / ".worktrees" / name, registered_worktrees(repo))


def _migrated_home(repo_root: Path, old_wt: Path, new: str, home: Path, projects_dir: Path) -> Path | None:
    """The registered ``.worktrees/<new>`` of another repo when ``old_wt`` is a migrated session's leftover.

    It is one when its checked-out branch already carries the new session's ``wt/<new>/``
    prefix, so the migration has reached it. A slot with a Claude Code project directory is a
    session home in its own right, so it is never a leftover: its transcripts move only with
    a full migration.
    """
    from .session import _current_branch

    if not (_current_branch(old_wt) or "").startswith(f"wt/{new}/") or cc_project_dir(old_wt, home).is_dir():
        return None
    for repo in _agents._repos(projects_dir, repo_root)[1:]:
        found = _registered_slot(repo, new)
        if found is not None:
            return found
    return None


def _plan_leftovers(
    old: str,
    new: str,
    repos: list[Path],
    *,
    home: Path,
    state: Path,
    proc_dir: Path | None,
    tmux: list[str],
    strict: Path | None = None,
) -> list[_Leftover]:
    """Every ``<repo>/.worktrees/<old>`` in ``repos``, each proved removable or kept with its reason.

    The proof is ``ai session-retire``'s own, run as a preview: no live owner, nothing
    uncommitted or untracked, and the checked-out commit contained in, or landed on, its
    upstream or ``origin/main``. A live refusal for ``strict`` is raised instead of kept.
    """
    from .session_retire import RetireError, retire_session

    found: list[_Leftover] = []
    for repo in repos:
        slot = _registered_slot(repo, old)
        if slot is None:
            continue
        item = _Leftover(repo, slot)
        found.append(item)
        if cc_project_dir(slot, home).is_dir():
            item.kept = (
                f"it has a Claude Code project directory, so it is a session home; carry it with "
                f"`ai session-migrate {old} {new} -r {repo}`"
            )
            continue
        try:
            retire_session(
                repo, old, dry_run=True, claude_home=home, state_dir=state, proc_dir=proc_dir, tmux_names=lambda: tmux
            )
        except LiveSessionError as exc:
            if slot == strict:
                raise
            item.kept = _one_line(str(exc))
        except RetireError as exc:
            item.kept = _one_line(str(exc))
    return found


def _describe_leftovers(leftovers: list[_Leftover], dry_run: bool) -> list[PlannedMove]:
    verb = "would be removed" if dry_run else "removed"
    return [
        PlannedMove(
            "leftover slot",
            str(item.slot),
            item.kept or "tips anchored under refs/preserved/session-retire/",
            "kept" if item.kept else verb,
        )
        for item in leftovers
    ]


def _apply_leftovers(
    old: str, leftovers: list[_Leftover], *, home: Path, state: Path, proc_dir: Path | None, tmux: list[str]
) -> list[str]:
    """Retire each removable leftover; one whose retirement now refuses is kept with that reason."""
    from .session_retire import RetireError, retire_session

    warnings: list[str] = []
    for item in [i for i in leftovers if i.kept is None]:
        try:
            done = retire_session(
                item.repo,
                old,
                claude_home=home,
                state_dir=state,
                proc_dir=proc_dir,
                tmux_names=lambda: tmux,
            )
        except (RetireError, LiveSessionError) as exc:
            item.kept = _one_line(str(exc))
            continue
        warnings += done.warnings
    return warnings


def _record_kept(leftovers: list[_Leftover], result: MigrationResult) -> None:
    for item in leftovers:
        if item.kept:
            result.kept.append(f"{item.slot}: {item.kept}")
            result.warnings.append(f"leftover slot {item.slot} was NOT removed: {item.kept}")


def _containers(old: str, repos: list[Path]) -> list[Path]:
    return [c for c in ((r / ".worktrees" / f"{old}-agents").resolve() for r in repos) if c.is_dir()]


def _holds_a_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return True
    return any(_holds_a_file(child) for child in path.iterdir())


def _sweep_containers(containers: list[Path], live_cwds: list[tuple[int, Path]], *, dry_run: bool) -> list[PlannedMove]:
    """Remove each ``<old>-agents`` container that holds no file, and name each one that is kept.

    A container already gone is not listed: the agent plan's own line covers it.
    """
    moves: list[PlannedMove] = []
    for container in [c for c in containers if c.is_dir()]:
        holder = _agents._holder(container, live_cwds)
        held = sorted(child.name for child in container.iterdir() if _holds_a_file(child))
        if holder is not None:
            moves.append(PlannedMove("agent container", str(container), f"pid {holder} works in it", "kept"))
        elif held:
            moves.append(PlannedMove("agent container", str(container), f"holds {', '.join(held)}", "kept"))
        elif dry_run:
            moves.append(PlannedMove("agent container", str(container), "empty", "would be removed"))
        else:
            _agents._remove_if_empty(container)
            moves.append(PlannedMove("agent container", str(container), "empty", "removed"))
    return moves


def clean_leftovers(
    repo_root: Path,
    old: str,
    new: str,
    old_wt: Path,
    home_slot: Path,
    *,
    dry_run: bool,
    home: Path,
    state: Path,
    proc_dir: Path | None,
    tmux: list[str],
    projects_dir: Path,
) -> MigrationResult:
    """Clean up after a session already migrated to ``home_slot``: ``old_wt`` is one of its leftovers.

    Touches only what a live ``new`` session elsewhere cannot be relying on: every old-name slot
    proved landed (retired, tips anchored) and every empty ``<old>-agents`` container. A live
    process named ``old`` or working in ``old_wt`` refuses the run, as for a full migration.
    """
    _refuse_live(old, old, old_wt, old_wt, home, proc_dir, tmux, verb="cleaned up")
    repos = _agents._repos(projects_dir, repo_root)
    leftovers = _plan_leftovers(old, new, repos, home=home, state=state, proc_dir=proc_dir, tmux=tmux, strict=old_wt)
    live_cwds = [(s.pid, Path(s.cwd).resolve(strict=False)) for s in live_sessions(home, proc_dir) if s.cwd]
    result = MigrationResult(old=old, new=new, old_worktree=old_wt, new_worktree=home_slot, dry_run=dry_run)
    result.moves.append(PlannedMove("session already migrated", str(home_slot), "", "report"))
    beads = _plan_beads(old, _stores(projects_dir, repo_root))
    _describe_beads(old, new, beads, result)
    if not dry_run:
        result.warnings += _apply_leftovers(old, leftovers, home=home, state=state, proc_dir=proc_dir, tmux=tmux)
    result.moves += _describe_leftovers(leftovers, dry_run)
    result.moves += _sweep_containers(_containers(old, repos), live_cwds, dry_run=dry_run)
    _record_kept(leftovers, result)
    if not dry_run:
        _apply_beads(old, new, beads, result)
    return result


def _clean_other_repos(
    result: MigrationResult,
    leftovers: list[_Leftover],
    containers: list[Path],
    live_cwds: list[tuple[int, Path]],
    *,
    home: Path,
    state: Path,
    proc_dir: Path | None,
    tmux: list[str],
) -> None:
    """After a home migration: retire the other repos' old-name slots and report every container's fate."""
    if not result.dry_run:
        result.warnings += _apply_leftovers(result.old, leftovers, home=home, state=state, proc_dir=proc_dir, tmux=tmux)
    result.moves += _describe_leftovers(leftovers, result.dry_run)
    if not result.dry_run:
        result.moves += _sweep_containers(containers, live_cwds, dry_run=False)
    _record_kept(leftovers, result)


#: Every completed migration on this host, one ``{"old", "new", ...}`` line each, beside
#: ``session-retirements.jsonl``: the record a later ``--finish`` reads before re-keying issues.
MIGRATION_LEDGER = "session-migrations.jsonl"

#: Seconds one ``bd`` call may take before its store counts as unreadable.
_BD_TIMEOUT = 120

#: The argv prefix that runs bd.
BD_COMMAND: tuple[str, ...] = ("bd",)


def _bd(store: Path, *args: str) -> subprocess.CompletedProcess:
    """``bd --sandbox <args>`` in ``store``, so bd opens that store and never pushes it.

    ``BEADS_DIR`` and ``BEADS_DB`` would point bd at another store whatever its cwd, so they
    are dropped. Publishing a store is a separate, serial step for whoever runs the migration.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("BEADS_DIR", "BEADS_DB")}
    return subprocess.run(
        [*BD_COMMAND, "--sandbox", *args],
        cwd=store,
        capture_output=True,
        text=True,
        env=env,
        timeout=_BD_TIMEOUT,
        check=False,
    )


@dataclass
class _Store:
    """One Beads store's non-closed issues assigned to exactly the old name, or why it could not be read."""

    path: Path
    issues: list[str] = field(default_factory=list)
    error: str | None = None


def _stores(projects_dir: Path, repo_root: Path) -> list[Path]:
    """The slot's repo, then every directory under ``projects_dir``, that holds a ``.beads/`` store."""
    candidates = [repo_root, *(sorted(c.resolve() for c in projects_dir.iterdir()) if projects_dir.is_dir() else [])]
    found: list[Path] = []
    for path in candidates:
        if path not in found and (path / ".beads").is_dir():
            found.append(path)
    return found


def _plan_beads(old: str, stores: list[Path]) -> list[_Store]:
    """Read each store's issues held by ``old``: exactly that name, any status but closed."""
    planned: list[_Store] = []
    for path in stores:
        store = _Store(path)
        planned.append(store)
        try:
            listed = _bd(path, "list", "--assignee", old, "--all", "--brief", "--json", "--limit", "0")
        except (OSError, subprocess.TimeoutExpired) as exc:
            store.error = _one_line(str(exc))
            continue
        if listed.returncode != 0:
            store.error = _one_line(listed.stderr or f"bd exited {listed.returncode}")
            continue
        try:
            rows = json.loads(listed.stdout or "null")
        except json.JSONDecodeError as exc:
            store.error = f"bd list printed no JSON ({exc})"
            continue
        if not isinstance(rows, list):
            store.error = "bd list printed no issue list"
            continue
        store.issues = [
            str(row["id"])
            for row in rows
            if isinstance(row, dict) and row.get("id") and row.get("assignee") == old and row.get("status") != "closed"
        ]
    return planned


def _describe_beads(old: str, new: str, stores: list[_Store], result: MigrationResult, *, held: str = "") -> None:
    """Name every issue the run re-keys (or, with ``held``, leaves and why) and every unreadable store."""
    for store in stores:
        if store.error is not None:
            result.moves.append(PlannedMove("beads store", str(store.path), store.error, "unreadable"))
            result.warnings.append(
                f"beads store {store.path} FAILED to read, so its issues on {old} are not known: {store.error}"
            )
        for issue in store.issues:
            action = f"left alone: {held}" if held else "move"
            result.moves.append(PlannedMove("beads issue", f"{issue} [{old}] ({store.path})", new, action))
    if held and any(store.issues for store in stores):
        result.warnings.append(f"beads issues on {old} were NOT re-keyed to {new}: {held}")


def _apply_beads(old: str, new: str, stores: list[_Store], result: MigrationResult) -> None:
    """Re-key each planned issue through ``bd update``, guarded on its assignee still being ``old``.

    bd runs as ``--actor <old>``: the holder hands its own claim over, so an in-progress issue
    moves without ``--force`` (which bd refuses beside ``--if-assignee`` anyway), and
    ``--if-assignee`` keeps it from touching an issue anyone else picked up meanwhile.
    """
    for store in [s for s in stores if s.error is None and s.issues]:
        changed = 0
        for issue in store.issues:
            where = f"beads issue {issue} in {store.path}"
            try:
                done = _bd(store.path, "--actor", old, "update", issue, "--assignee", new, "--if-assignee", old)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.warnings.append(f"{where} FAILED to re-key: {_one_line(str(exc))}")
                continue
            if done.returncode == 13:
                result.warnings.append(f"{where} was left alone: its assignee is no longer {old}")
                continue
            if done.returncode != 0:
                result.warnings.append(f"{where} FAILED to re-key: {_one_line(done.stderr or str(done.returncode))}")
                continue
            changed += 1
            note = f"Re-keyed from {old} to {new} by `ai session-migrate`."
            try:
                noted = _bd(store.path, "comments", "add", issue, note)
            except (OSError, subprocess.TimeoutExpired) as exc:
                noted = subprocess.CompletedProcess([], 1, "", str(exc))
            if noted.returncode != 0:
                result.warnings.append(f"{where} was re-keyed but its comment FAILED: {_one_line(noted.stderr)}")
        if changed:
            result.moves.append(
                PlannedMove(
                    "beads store",
                    str(store.path),
                    f"{changed} re-keyed, local until the store is published",
                    "publish needed",
                )
            )


def _append_ledger(state: Path, old: str, new: str, repo_root: Path, **fields: object) -> None:
    state.mkdir(parents=True, exist_ok=True)
    row = {"time": datetime.now(UTC).isoformat(), "old": old, "new": new, "repo": str(repo_root), **fields}
    with (state / MIGRATION_LEDGER).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row, sort_keys=True) + "\n")


def _record_migration(state: Path, old: str, new: str, repo_root: Path, new_wt: Path) -> None:
    _append_ledger(state, old, new, repo_root, worktree=str(new_wt))


def _ledger_rows(state: Path) -> list[dict]:
    rows: list[dict] = []
    with contextlib.suppress(OSError):
        for line in (state / MIGRATION_LEDGER).read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def pending_finishes(new: str, state_dir: Path | None = None) -> list[tuple[str, Path]]:
    """``(old, repo)`` for each migration to ``new`` this host records with no later ``finished`` row.

    The ledger is append-only: a completed ``--finish`` appends ``"finished": true`` for its
    pair rather than rewriting the migration's row, and a later migration of the same pair
    makes it pending again. A row without a ``repo`` names no slot to finish and is skipped.
    """
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()
    pending: dict[str, Path] = {}
    for row in _ledger_rows(state):
        old, repo = row.get("old"), row.get("repo")
        if row.get("new") != new or not isinstance(old, str) or not isinstance(repo, str):
            continue
        if row.get("finished") is True:
            pending.pop(old, None)
        else:
            pending[old] = Path(repo)
    return list(pending.items())


def _migration_evidence(old: str, new: str, state: Path) -> str | None:
    """This host's migration-ledger row naming exactly the pair ``(old, new)``, or ``None``.

    A Beads assignee carries no host, and other machines' sessions share the stores, so a
    ``--finish`` re-keys only on a record that names both names. Session metadata or a
    registry entry binding ``new`` to its slot never names ``old``, so neither counts: a
    migration run before the ledger existed gets its row written by hand, from the
    migrating session's own record.
    """
    if any((row.get("old"), row.get("new")) == (old, new) for row in _ledger_rows(state)):
        return f"{state / MIGRATION_LEDGER} records it"
    return None


def rekey_beads(
    repo_root: Path,
    old: str,
    new: str,
    *,
    dry_run: bool = False,
    state_dir: Path | None = None,
    projects_dir: Path | None = None,
) -> MigrationResult:
    """Re-key only the Beads issues still on ``old``, for a migration this host records.

    The rest of :func:`finish_session` refuses while either session runs, because it moves
    their transcripts and task files. Re-keying touches neither, and ``--if-assignee`` keeps
    it from taking an issue someone picked up meanwhile, so this runs with ``new`` live.
    Without this host's ledger row for the pair it lists the issues and changes nothing.
    """
    repo_root = repo_root.resolve()
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()
    projects = (projects_dir if projects_dir is not None else _config._get_projects_dir()).resolve()
    result = MigrationResult(
        old=old,
        new=new,
        old_worktree=repo_root / ".worktrees" / old,
        new_worktree=repo_root / ".worktrees" / new,
        dry_run=dry_run,
    )
    evidence = _migration_evidence(old, new, state)
    if evidence:
        result.moves.append(PlannedMove("migration record", evidence, "", "report"))
    beads = _plan_beads(old, _stores(projects, repo_root))
    held = "" if evidence else f"this host holds no record that {old} was migrated to {new} here"
    _describe_beads(old, new, beads, result, held=held)
    if evidence and not dry_run:
        _apply_beads(old, new, beads, result)
    return result


def migrate_session(
    repo_root: Path,
    old: str,
    new: str,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
    state_dir: Path | None = None,
    proc_dir: Path | None = None,
    tmux_names: Callable[[], list[str]] | None = None,
    regenerate_script: Callable[[dict], str] | None = None,
    projects_dir: Path | None = None,
) -> MigrationResult:
    """Move the session in slot ``old`` to slot ``new``; see the module docstring.

    ``regenerate_script`` turns a session-metadata dict into its wrapper script
    (the launcher's ``_engine_script_from_meta``); without it the old script is
    moved as-is and the next ``ai c`` rewrites it. Raises :class:`MigrationError`
    or :class:`LiveSessionError` before writing anything.
    """
    from .session import _current_branch, _registered_worktree_at, registered_worktrees

    repo_root = repo_root.resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()

    if old == new:
        raise MigrationError(f"{old!r} is already the session's name")
    if not _NAME_RE.fullmatch(new):
        raise MigrationError(
            f"{new!r} is not a session name: lowercase letters and digits in hyphen-separated "
            f"segments, ending in -<N> (e.g. myrepo-box-1)"
        )
    registered = registered_worktrees(repo_root)
    old_wt = _registered_worktree_at(repo_root / ".worktrees" / old, registered)
    if old_wt is None:
        raise MigrationError(
            f"{repo_root / '.worktrees' / old} is not a registered worktree of {repo_root} -- nothing to "
            f"migrate. A session started outside `ai c` is brought into a slot with `ai session-adopt`."
        )
    new_wt = old_wt.parent / new
    if os.path.lexists(new_wt):
        raise MigrationError(f"{new_wt} already exists -- refusing to migrate onto it")
    nested = [w for w in registered if w != old_wt and old_wt in w.parents]
    if nested:
        raise MigrationError(
            f"{old_wt} contains other registered worktrees ({', '.join(str(w) for w in nested)}); moving it "
            f"would orphan their registrations. Move each out with `git worktree move` first."
        )
    projects = (projects_dir if projects_dir is not None else _config._get_projects_dir()).resolve()
    tmux = (tmux_names or _tmux_names)()
    home_slot = _migrated_home(repo_root, old_wt, new, home, projects)
    if home_slot is not None:
        return clean_leftovers(
            repo_root,
            old,
            new,
            old_wt,
            home_slot,
            dry_run=dry_run,
            home=home,
            state=state,
            proc_dir=proc_dir,
            tmux=tmux,
            projects_dir=projects,
        )
    _refuse_live(old, new, old_wt, new_wt, home, proc_dir, tmux)
    live_cwds = [(s.pid, Path(s.cwd).resolve(strict=False)) for s in live_sessions(home, proc_dir) if s.cwd]
    leftovers = _plan_leftovers(
        old, new, _agents._repos(projects, repo_root)[1:], home=home, state=state, proc_dir=proc_dir, tmux=tmux
    )
    agent_plan = _agents.plan_agents(
        old,
        new,
        projects_dir=projects,
        slot_repo=repo_root,
        old_wt=old_wt,
        new_wt=new_wt,
        live_cwds=live_cwds,
    )
    old_pd, new_pd = cc_project_dir(old_wt, home), cc_project_dir(new_wt, home)
    if new_pd.exists():
        raise MigrationError(f"Claude Code project directory {new_pd} already exists -- refusing to merge into it")

    result = MigrationResult(old=old, new=new, old_worktree=old_wt, new_worktree=new_wt, dry_run=dry_run)
    moves = result.moves
    moves.append(PlannedMove("worktree", str(old_wt), str(new_wt)))
    if (old_wt / ".venv").is_dir():
        result.warnings.append(
            f"{new_wt / '.venv'} moves with the worktree, and its console scripts name the old path in "
            f"their shebangs -- recreate it (`uv sync --reinstall`) before running them"
        )

    # The canonical branch is renamed whether the slot has it checked out or parks it
    # while a topic branch is checked out: either way it still names the old session.
    old_branch, new_branch = f"wt-{old}", f"wt-{new}"
    branch_plan = rename_branch_anchored(repo_root, old_branch, new_branch, dry_run=True)
    if not branch_plan.tip:
        new_branch = None
    elif not branch_plan.ok:
        keeper = "the worktree keeps branch" if _current_branch(old_wt) == old_branch else "it keeps the name"
        result.warnings.append(f"{branch_plan.error}, so {keeper} {old_branch}")
        new_branch = None
    else:
        moves.append(PlannedMove("branch", old_branch, new_branch))
        moves.append(PlannedMove("branch anchor", branch_plan.anchor, branch_plan.tip, "write"))
    home_ownership = _agents.plan_worktree_ownership(
        repo_root,
        old_wt,
        new_wt,
        old,
        new,
        old_branch,
        new_branch,
    )
    moves += [PlannedMove(*line) for line in _agents.describe_worktree_ownership(home_ownership)]
    slot_renames = {**agent_plan.renames(repo_root), **({old_branch: new_branch} if new_branch else {})}
    markers = _session_home_markers(repo_root, old_wt)
    for key, _value in markers:
        moves.append(
            PlannedMove("session-home marker", key, f"{_agents.renamed_key(key, slot_renames)} = {new_wt}", "rewrite")
        )
    moves += [PlannedMove(*line) for line in _agents.describe(agent_plan)]

    try:
        registry_entries = move_canonical_worktree(old_wt, new_wt, old, new, dry_run=True)
    except CanonicalWorktreeRegistryError as exc:
        raise MigrationError(f"{exc} -- the moved worktree would lose its protection, so refusing") from exc
    if registry_entries:
        moves.append(
            PlannedMove("canonical registry", str(get_canonical_worktree_registry_path()), str(new_wt), "rewrite")
        )

    transcripts = sorted(old_pd.glob("*.jsonl")) if old_pd.is_dir() else []
    titled = [path for path in transcripts if old in transcript_titles(path)]
    if old_pd.is_dir():
        moves.append(PlannedMove("project dir", str(old_pd), str(new_pd)))
        for path in titled:
            moves.append(PlannedMove("transcript title", path.name, f"{old} -> {new}", "rewrite"))
    else:
        result.warnings.append(f"no Claude Code project directory at {old_pd}; there is no transcript to carry")

    state_moves = _plan_state(old, new, state)
    moves += state_moves
    map_moves = _plan_session_maps(old, new, home, state)
    moves += map_moves
    iterm2_moves = _plan_iterm2(old)
    moves += iterm2_moves
    launcher_moves = _plan_launch_logs(old, new, state) + _plan_chief(old, new, tmux, result)
    moves += launcher_moves

    project_dirs = [(cc_project_dir(repo_root, home), None), (old_pd, new_pd)]
    held_moves = _plan_held_output(old, new, project_dirs)
    moves += held_moves
    memory_moves, index_moves = _plan_memory(old, new, project_dirs, _session_names(home, repo_root, old, new))
    moves += memory_moves + index_moves
    lock_moves = _plan_slot_lock(repo_root, old)
    moves += lock_moves

    _plan_task_rekey(old, new, home, result)
    hooks = _plan_hooks(old, new, repo_root, moves if dry_run else None)
    beads = _plan_beads(old, _stores(projects, repo_root))
    _describe_beads(old, new, beads, result)
    others = {"home": home, "state": state, "proc_dir": proc_dir, "tmux": tmux}
    if dry_run:
        _clean_other_repos(result, leftovers, agent_plan.containers, live_cwds, **others)
        return result

    moved, why = move_worktree(repo_root, old_wt, new_wt)
    if not moved:
        if not new_wt.exists():
            raise MigrationError(f"git worktree move failed, nothing was migrated: {why}")
        raise MigrationError(f"the worktree moved but is not usable yet, so nothing else was migrated: {why}")
    _record_migration(state, old, new, repo_root, new_wt)
    if new_branch:
        renamed = rename_branch_anchored(repo_root, old_branch, new_branch)
        if not renamed.ok:
            anchored = _git(repo_root, "rev-parse", "--verify", "--quiet", renamed.anchor).returncode == 0
            kept = f"; the tip stays anchored at {renamed.anchor}" if anchored else ""
            result.warnings.append(f"branch rename to {new_branch} failed: {renamed.error}{kept}")
            new_branch = None
    result.warnings += _agents.apply_worktree_ownership(home_ownership, new_branch or old_branch)
    agent_renames, agent_warnings = _agents.apply_agents(agent_plan)
    result.warnings += agent_warnings
    slot_renames = {**agent_renames.get(repo_root, {}), **({old_branch: new_branch} if new_branch else {})}
    for key, _value in markers:
        _git(repo_root, "config", _agents.renamed_key(key, slot_renames), str(new_wt))
    move_canonical_worktree(old_wt, new_wt, old, new, dry_run=False)

    # Renamed inside the old project dir, before it moves, so a held-output file of the
    # slot travels under its new name with the directory.
    for item in [m for m in held_moves if m.action == "move"]:
        source = Path(item.source)
        source.rename(source.with_name(Path(item.dest).name))
    _apply_memory(memory_moves, index_moves, result)
    _remove_stale_locks(lock_moves)

    if old_pd.is_dir():
        old_pd.rename(new_pd)
        for path in sorted(new_pd.glob("*.jsonl")):
            _rewrite_transcript_cwd(path, str(old_wt), str(new_wt))
            if path.name in {t.name for t in titled}:
                retitle_transcript(path, old, new)
            neutralise_worktree_state(path, new_wt)

    _apply_task_rekey(old, new, home, result)
    _apply_state(state_moves, old, new, old_wt, new_wt, regenerate_script, result)
    _remove_stale_locks(state_moves)
    _apply_session_maps(map_moves, old, new, home, state)
    if iterm2_moves:
        _apply_iterm2(old, new)
    _apply_launch_logs(launcher_moves, result)
    _apply_chief(launcher_moves, old, new)
    _run_live_hooks(hooks, old, new, repo_root, result)
    _clean_other_repos(result, leftovers, agent_plan.containers, live_cwds, **others)
    _apply_beads(old, new, beads, result)

    if titled:
        result.resolved = probe_resolves(new_wt, new, home)
        if result.resolved is None:
            result.warnings.append(
                f"post-migrate check FAILED: `ai c` in {new_wt} resolves no transcript titled {new!r}"
            )
    return result


def _plan_task_rekey(old: str, new: str, home: Path, result: MigrationResult) -> None:
    """Plan the task-namespace and resume-tree rekey, and turn every planned conflict into a warning."""
    old_ns = home / "tasks" / old
    result.task_moves, result.resume_moves = rekey_name_artifacts(
        new, task_namespaces=[old], resume_keys=[old], claude_home=home, dry_run=True
    )
    if old_ns.is_dir():
        leftover = _namespace_leftovers(old_ns, {m.source.name for m in result.task_moves})
        if leftover:
            result.moves.append(
                PlannedMove("task namespace", str(old_ns), f"holds {', '.join(leftover)}", "left in place")
            )
        else:
            result.moves.append(PlannedMove("task namespace", str(old_ns), "", "removed after rekey"))
    for item in result.moves:
        if item.action == "conflict":
            result.warnings.append(f"{item.kind} {item.source} was NOT moved: {item.dest} already exists")
    for conflict in [m for m in result.resume_moves if m.action == "conflict"]:
        result.warnings.append(f"resume artifact {conflict.source} was NOT re-keyed: {conflict.dest} already exists")


def _apply_task_rekey(old: str, new: str, home: Path, result: MigrationResult) -> None:
    old_ns, new_ns = home / "tasks" / old, home / "tasks" / new
    hwm = _highwatermark(old_ns)
    result.task_moves, result.resume_moves = rekey_name_artifacts(
        new, task_namespaces=[old], resume_keys=[old], claude_home=home, dry_run=False
    )
    if result.task_moves or hwm:
        top = max([hwm, _highwatermark(new_ns), *(int(m.dest.stem) for m in result.task_moves)])
        new_ns.mkdir(mode=0o700, parents=True, exist_ok=True)
        (new_ns / ".highwatermark").write_text(str(top), encoding="utf-8")
    if old_ns.is_dir():
        leftover = _namespace_leftovers(old_ns, set())
        if leftover:
            result.warnings.append(f"task namespace {old_ns} left in place: it still holds {', '.join(leftover)}")
        else:
            for name in _NAMESPACE_BOOKKEEPING:
                (old_ns / name).unlink(missing_ok=True)
            old_ns.rmdir()


def _run_live_hooks(hooks: list[_hooks.Hook], old: str, new: str, repo_root: Path, result: MigrationResult) -> None:
    for run in _hooks.run_hooks(hooks, old, new, cwd=repo_root):
        result.moves += [PlannedMove(f"hook {run.hook.name}", line, "", "report") for line in run.lines]
        if not run.ok:
            result.warnings.append(_hooks.failure_warning(run))


def finish_session(
    repo_root: Path,
    old: str,
    new: str,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
    state_dir: Path | None = None,
    proc_dir: Path | None = None,
    tmux_names: Callable[[], list[str]] | None = None,
    projects_dir: Path | None = None,
) -> MigrationResult:
    """Finish a migration whose slot already moved to ``new``: every leftover still named ``old``.

    For a session migrated before a step existed, or one whose agent worktree a live process
    held at the time. Runs every planner of :func:`migrate_session` that is keyed by the old
    name and is safe to repeat: agent worktrees, branches, containers and markers; the
    launcher's state, maps and locks; held-output and memory files in the repo's and the
    slot's project directories; the task namespace and resume tree; every external hook; and
    the Beads issues still on ``old``, only when :func:`_migration_evidence` finds this host's
    record of the migration. Neither session may be running. Raises :class:`MigrationError` before writing anything.
    A run that re-keyed on that record and left nothing undone appends the pair's ``finished`` row.
    """
    from .session import _registered_worktree_at, registered_worktrees

    repo_root = repo_root.resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()
    registered = registered_worktrees(repo_root)
    projects = (projects_dir if projects_dir is not None else _config._get_projects_dir()).resolve()
    tmux = (tmux_names or _tmux_names)()
    old_wt = repo_root / ".worktrees" / old
    still = _registered_worktree_at(old_wt, registered)
    if still is not None:
        home_slot = _migrated_home(repo_root, still, new, home, projects)
        if home_slot is None:
            raise MigrationError(f"{old_wt} is still a registered slot -- migrate it without --finish first")
        return clean_leftovers(
            repo_root,
            old,
            new,
            still,
            home_slot,
            dry_run=dry_run,
            home=home,
            state=state,
            proc_dir=proc_dir,
            tmux=tmux,
            projects_dir=projects,
        )
    new_wt = _registered_worktree_at(repo_root / ".worktrees" / new, registered)
    if new_wt is None:
        raise MigrationError(f"{repo_root / '.worktrees' / new} is not a registered slot -- nothing to finish")
    _refuse_live(old, new, old_wt.resolve(strict=False), new_wt, home, proc_dir, tmux, verb="finished")
    hosting = sorted(set(tmux) & set(_session_variants(new)))
    if hosting:
        raise LiveSessionError(f"tmux session {', '.join(hosting)} hosts {new}; exit it before finishing")
    live_cwds = [(s.pid, Path(s.cwd).resolve(strict=False)) for s in live_sessions(home, proc_dir) if s.cwd]
    leftovers = _plan_leftovers(
        old, new, _agents._repos(projects, repo_root)[1:], home=home, state=state, proc_dir=proc_dir, tmux=tmux
    )
    agent_plan = _agents.plan_agents(
        old,
        new,
        projects_dir=projects,
        slot_repo=repo_root,
        old_wt=old_wt.resolve(strict=False),
        new_wt=new_wt,
        live_cwds=live_cwds,
    )

    result = MigrationResult(old=old, new=new, old_worktree=old_wt, new_worktree=new_wt, dry_run=dry_run)
    moves = result.moves
    markers = _session_home_markers(repo_root, old_wt.resolve(strict=False))
    slot_renames = agent_plan.renames(repo_root)
    for key, _value in markers:
        moves.append(
            PlannedMove("session-home marker", key, f"{_agents.renamed_key(key, slot_renames)} = {new_wt}", "rewrite")
        )
    moves += [PlannedMove(*line) for line in _agents.describe(agent_plan)]
    state_moves = _plan_state(old, new, state)
    map_moves = _plan_session_maps(old, new, home, state)
    project_dirs: list[tuple[Path, Path | None]] = [
        (cc_project_dir(repo_root, home), None),
        (cc_project_dir(new_wt, home), None),
    ]
    held_moves = _plan_held_output(old, new, project_dirs)
    memory_moves, index_moves = _plan_memory(old, new, project_dirs, _session_names(home, repo_root, old, new))
    lock_moves = _plan_slot_lock(repo_root, old)
    iterm2_moves = _plan_iterm2(old)
    launcher_moves = _plan_launch_logs(old, new, state) + _plan_chief(old, new, tmux, result)
    moves += state_moves + map_moves + iterm2_moves + launcher_moves + held_moves + memory_moves + index_moves
    moves += lock_moves
    _plan_task_rekey(old, new, home, result)
    hooks = _plan_hooks(old, new, repo_root, moves if dry_run else None)
    evidence = _migration_evidence(old, new, state)
    if evidence:
        moves.append(PlannedMove("migration record", evidence, "", "report"))
    beads = _plan_beads(old, _stores(projects, repo_root))
    held = "" if evidence else f"this host holds no record that {old} was migrated to {new} here"
    _describe_beads(old, new, beads, result, held=held)
    others = {"home": home, "state": state, "proc_dir": proc_dir, "tmux": tmux}
    if dry_run:
        _clean_other_repos(result, leftovers, agent_plan.containers, live_cwds, **others)
        return result

    agent_renames, agent_warnings = _agents.apply_agents(agent_plan)
    result.warnings += agent_warnings
    for key, _value in markers:
        _git(repo_root, "config", _agents.renamed_key(key, agent_renames.get(repo_root, {})), str(new_wt))
    for item in [m for m in held_moves if m.action == "move"]:
        source = Path(item.source)
        source.rename(source.with_name(Path(item.dest).name))
    _apply_memory(memory_moves, index_moves, result)
    _remove_stale_locks(lock_moves)
    _apply_task_rekey(old, new, home, result)
    _apply_state(state_moves, old, new, old_wt, new_wt, None, result)
    _remove_stale_locks(state_moves)
    _apply_session_maps(map_moves, old, new, home, state)
    if iterm2_moves:
        _apply_iterm2(old, new)
    _apply_launch_logs(launcher_moves, result)
    _apply_chief(launcher_moves, old, new)
    _run_live_hooks(hooks, old, new, repo_root, result)
    _clean_other_repos(result, leftovers, agent_plan.containers, live_cwds, **others)
    if evidence:
        _apply_beads(old, new, beads, result)
        if not result.failed:
            _append_ledger(state, old, new, repo_root, finished=True)
    return result


def _apply_state(
    moves: list[PlannedMove],
    old: str,
    new: str,
    old_wt: Path,
    new_wt: Path,
    regenerate_script: Callable[[dict], str] | None,
    result: MigrationResult,
) -> None:
    metas: dict[str, dict] = {}
    for item in [m for m in moves if m.kind == "session meta" and m.action == "move"]:
        source, dest = Path(item.source), Path(item.dest)
        meta = json.loads(source.read_text(encoding="utf-8"))
        old_session = source.name[len("session-meta-") : -len(".json")]
        new_session = old_session[: -len(old)] + new
        meta["ai_name"] = new
        meta["session"] = new_session
        meta["prefix"] = new_session.rsplit("-", 1)[0] + "-"
        if meta.get("worktree_dir") and Path(meta["worktree_dir"]).resolve(strict=False) == old_wt:
            meta["worktree_dir"] = str(new_wt)
        dest.write_text(json.dumps(meta), encoding="utf-8")
        source.unlink()
        metas[new_session] = meta
    for item in [m for m in moves if m.action == "move" and m.kind != "session meta"]:
        source, dest = Path(item.source), Path(item.dest)
        meta = metas.get(dest.stem) if item.kind == "wrapper script" else None
        if meta is not None and regenerate_script is not None:
            try:
                dest.write_text(regenerate_script(meta), encoding="utf-8")
                dest.chmod(0o700)
                source.unlink()
                continue
            except Exception as exc:
                result.warnings.append(f"could not regenerate {dest} ({exc}); moved the old one, `ai c` rewrites it")
        shutil.move(str(source), str(dest))


def _apply_session_maps(moves: list[PlannedMove], old: str, new: str, home: Path, state: Path) -> None:
    for item in [m for m in moves if m.action == "move"]:
        path = Path(item.source.rsplit(" [", 1)[0])
        data = json.loads(path.read_text(encoding="utf-8"))
        data[new] = data.pop(old)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _apply_iterm2(old: str, new: str) -> None:
    renames = dict(zip(_session_variants(old), _session_variants(new), strict=True))

    def change(doc: dict) -> bool:
        changed = False
        for record in doc["sessions"]:
            if not isinstance(record, dict) or record.get("name") not in renames:
                continue
            record["name"] = renames[record["name"]]
            argv = record.get("relaunch_argv")
            if isinstance(argv, list) and argv:
                # The relaunch line ends with the session's slot or full name, and a full
                # name maps back to its own slot, so naming the new session is exact.
                argv[-1] = new
            changed = True
        return changed

    _session_registry._mutate(change)


#: ``<name>-<UTC stamp>-<pid>.log``, the name :func:`ai_cli.launch_logging.create_launch_log` gives.
_LAUNCH_LOG_TAIL = re.compile(r"-(\d{8}T\d{6}\.\d+Z-\d+\.log)")


def _plan_launch_logs(old: str, new: str, state: Path) -> list[PlannedMove]:
    """Each ``launch-logs/<name>-<stamp>-<pid>.log`` of a launch named for OLD, renamed for NEW.

    Kept per name and pruned only when that name launches again, so a log left under OLD is
    never pruned; under NEW it joins NEW's retention. A name only the slot number gives
    (``3-<stamp>``) names no session and stays.
    """
    from .launch_logging import _LOG_DIRECTORY, _safe_session_name

    logs = state / _LOG_DIRECTORY
    if not logs.is_dir():
        return []
    names = {
        _safe_session_name(o): _safe_session_name(n)
        for o, n in ((old, new), *zip(_session_variants(old), _session_variants(new), strict=True))
    }
    moves: list[PlannedMove] = []
    for source in sorted(logs.iterdir()):
        for old_name, new_name in names.items():
            tail = (
                _LAUNCH_LOG_TAIL.fullmatch(source.name[len(old_name) :]) if source.name.startswith(old_name) else None
            )
            if tail and source.is_file():
                dest = logs / f"{new_name}-{tail.group(1)}"
                moves.append(PlannedMove("launch log", str(source), str(dest), "conflict" if dest.exists() else "move"))
                break
    return moves


def _apply_launch_logs(moves: list[PlannedMove], result: MigrationResult) -> None:
    for item in [m for m in moves if m.kind == "launch log" and m.action == "move"]:
        try:
            Path(item.source).rename(item.dest)
        except OSError as exc:
            result.warnings.append(f"launch log {item.source} FAILED to move to {item.dest}: {exc}")


def _plan_chief(old: str, new: str, tmux: list[str], result: MigrationResult) -> list[PlannedMove]:
    """The chief-of-staff registration ``ai cos`` wrote naming OLD, and its registry's chief fields.

    Rewritten for NEW unless its tmux target is live, which leaves it named for ``--finish``
    and fails the run. A registration or registry that cannot be read refuses the migration.
    """
    from . import chief_of_staff as _cos

    root = _cos.chief_homes_root()
    moves: list[PlannedMove] = []
    for home in sorted(p for p in root.iterdir() if p.is_dir()) if root is not None and root.is_dir() else []:
        try:
            registration = _cos.read_registration(home)
            _cos._read_json_object(_cos.registry_path(home), "chief registry")
        except _cos.ChiefOfStaffError as exc:
            raise MigrationError(f"{exc}; refusing before any write") from exc
        if not registration or registration.get("native_agent_name") != old:
            continue
        path = str(_cos.registration_path(home))
        target = registration.get("tmux_target")
        if isinstance(target, str) and target in tmux:
            reason = f"its tmux target {target} is live, so the chief may be running"
            moves.append(PlannedMove("chief registration", path, reason, "kept"))
            result.kept.append(f"{path}: {reason}")
            result.warnings.append(f"chief registration {path} was NOT re-keyed: {reason}; re-run with --finish")
            continue
        moves.append(PlannedMove("chief registration", path, f"{old} -> {new}", "rewrite"))
    return moves


def _apply_chief(moves: list[PlannedMove], old: str, new: str) -> None:
    from . import chief_of_staff as _cos

    tmux = dict(zip(_session_variants(old), _session_variants(new), strict=True))

    def renamed(session: object) -> object:
        return new + session[len(old) :] if isinstance(session, str) and session.startswith(f"{old}@") else session

    for item in [m for m in moves if m.kind == "chief registration" and m.action == "rewrite"]:
        home = Path(item.source).parent.parent
        registration = _cos.read_registration(home) or {}
        target = registration.get("tmux_target")
        registration.update(
            native_agent_name=new,
            session=renamed(registration.get("session")),
            tmux_target=tmux.get(target, target) if isinstance(target, str) else target,
        )
        _cos._write_private_json(_cos.registration_path(home), registration)
        registry = _cos._read_json_object(_cos.registry_path(home), "chief registry")
        if registry is None:
            continue
        registry["chief_of_staff_session"] = renamed(registry.get("chief_of_staff_session"))
        routes = registry.get("chief_routes")
        if isinstance(routes, dict) and routes.get("native_agent_name") == old:
            routes["native_agent_name"] = new
        _cos._write_private_json(_cos.registry_path(home), registry)
