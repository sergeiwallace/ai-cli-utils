"""Migrate a registered ``ai`` session to a new name (``ai session-migrate OLD NEW``).

``ai session-adopt`` brings in a session that was started *outside* ``ai c``. This
module moves one that already lives in a slot -- ``<repo>/.worktrees/<old>`` -- to
``<repo>/.worktrees/<new>``, carrying everything keyed by the old name or by the
old worktree path, so ``ai c <new>`` resumes the same conversation with the same
tasks, memory and resume hand-off:

* the worktree (``git worktree move``), its ``wt-<old>`` branch whether checked
  out or parked behind a topic branch (anchored first, see
  :mod:`ai_cli.branch_rename`), and every ``branch.*.aih-session-home`` marker
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
  session maps and the iTerm2 restore registry;
* the session's agent worktrees under ``<repo>/.worktrees/<old>-agents/`` and its
  ``wt/<old>/*`` branches in every repo under ``projects_dir``
  (:mod:`ai_cli.session_migrate_agents`), except one a live process is working in;
* whatever the external hooks in ``session-migrate.d/`` declare
  (:mod:`ai_cli.session_migrate_hooks`), each planned with ``--dry-run`` before
  the first write and run after the core moves.

Every refusal is checked before the first write, in ``--dry-run`` too: the slot
must be a registered worktree, nothing may already exist at the new slot or its
project directory, no nested worktree may live inside the old slot, and the
session must not be running -- neither a Claude Code process nor the tmux session
whose wrapper would restart it.

Nothing is overwritten. A destination that already exists is reported and left
alone, and a lock is never carried to the new name (the session is not running,
so it has no holder).
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

#: The fleet harness's SessionStart hook records a session's home as
#: ``branch.<branch>.aih-session-home = <worktree path>``.
_SESSION_HOME_KEY = re.compile(r"^branch\..+\.aih-session-home$")


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
    """``(key, value)`` for every session-home marker naming ``old_wt``."""
    res = _git(repo, "config", "--get-regexp", _SESSION_HOME_KEY.pattern)
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
    """Whether ``<prefix><old>`` ends in a different session's name, e.g. ``kg-aih-3`` for ``aih-3``."""
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
        if moved and index.is_file() and _rewrite_index(index.read_text(encoding="utf-8"), moved)[1]:
            rewrites.append(PlannedMove("memory index", str(index), ", ".join(sorted(moved)), "rewrite"))
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
    _refuse_live(old, new, old_wt, new_wt, home, proc_dir, (tmux_names or _tmux_names)())
    live_cwds = [(s.pid, Path(s.cwd).resolve(strict=False)) for s in live_sessions(home, proc_dir) if s.cwd]
    agent_plan = _agents.plan_agents(
        old,
        new,
        projects_dir=(projects_dir if projects_dir is not None else _config._get_projects_dir()).resolve(),
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

    project_dirs = [(cc_project_dir(repo_root, home), None), (old_pd, new_pd)]
    held_moves = _plan_held_output(old, new, project_dirs)
    moves += held_moves
    memory_moves, index_moves = _plan_memory(old, new, project_dirs, _session_names(home, repo_root, old, new))
    moves += memory_moves + index_moves
    lock_moves = _plan_slot_lock(repo_root, old)
    moves += lock_moves

    _plan_task_rekey(old, new, home, result)
    hooks = _plan_hooks(old, new, repo_root, moves if dry_run else None)
    if dry_run:
        return result

    moved, why = move_worktree(repo_root, old_wt, new_wt)
    if not moved:
        if not new_wt.exists():
            raise MigrationError(f"git worktree move failed, nothing was migrated: {why}")
        raise MigrationError(f"the worktree moved but is not usable yet, so nothing else was migrated: {why}")
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
    _run_live_hooks(hooks, old, new, repo_root, result)

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
    slot's project directories; the task namespace and resume tree; and every external hook.
    Neither session may be running. Raises :class:`MigrationError` before writing anything.
    """
    from .session import _registered_worktree_at, registered_worktrees

    repo_root = repo_root.resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()
    registered = registered_worktrees(repo_root)
    old_wt = repo_root / ".worktrees" / old
    if _registered_worktree_at(old_wt, registered) is not None:
        raise MigrationError(f"{old_wt} is still a registered slot -- migrate it without --finish first")
    new_wt = _registered_worktree_at(repo_root / ".worktrees" / new, registered)
    if new_wt is None:
        raise MigrationError(f"{repo_root / '.worktrees' / new} is not a registered slot -- nothing to finish")
    tmux = (tmux_names or _tmux_names)()
    _refuse_live(old, new, old_wt.resolve(strict=False), new_wt, home, proc_dir, tmux, verb="finished")
    hosting = sorted(set(tmux) & set(_session_variants(new)))
    if hosting:
        raise LiveSessionError(f"tmux session {', '.join(hosting)} hosts {new}; exit it before finishing")
    live_cwds = [(s.pid, Path(s.cwd).resolve(strict=False)) for s in live_sessions(home, proc_dir) if s.cwd]
    agent_plan = _agents.plan_agents(
        old,
        new,
        projects_dir=(projects_dir if projects_dir is not None else _config._get_projects_dir()).resolve(),
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
    project_dirs = [(cc_project_dir(repo_root, home), None), (cc_project_dir(new_wt, home), None)]
    held_moves = _plan_held_output(old, new, project_dirs)
    memory_moves, index_moves = _plan_memory(old, new, project_dirs, _session_names(home, repo_root, old, new))
    lock_moves = _plan_slot_lock(repo_root, old)
    iterm2_moves = _plan_iterm2(old)
    moves += state_moves + map_moves + iterm2_moves + held_moves + memory_moves + index_moves + lock_moves
    _plan_task_rekey(old, new, home, result)
    hooks = _plan_hooks(old, new, repo_root, moves if dry_run else None)
    if dry_run:
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
    _run_live_hooks(hooks, old, new, repo_root, result)
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
