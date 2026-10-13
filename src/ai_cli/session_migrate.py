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
  and the slot's project directories (filename prefix only);
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
    old: str, new: str, old_wt: Path, new_wt: Path, home: Path, proc_dir: Path | None, tmux: list[str]
) -> None:
    for session in live_sessions(home, proc_dir):
        cwd = Path(session.cwd).resolve(strict=False) if session.cwd else None
        inside = cwd is not None and (cwd in (old_wt, new_wt) or old_wt in cwd.parents)
        if session.name in {old, new} or inside:
            raise LiveSessionError(
                f"session {session.name or '<unnamed>'} (pid {session.pid}, cwd {session.cwd}) is still "
                f"running in the slot being migrated -- its transcript and task files are open. Exit it first."
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


def _plan_venv_store(old_wt: Path, old: str, new: str) -> tuple[Path, Path] | None:
    """``(store dir, renamed store dir)`` when the slot's ``.venv`` links to a dir named for the slot.

    A worktree's venv may live outside it, in a store directory named ``<repo>--<slot>``
    and reached through a ``.venv`` symlink. Left as it is, the store keeps naming the old
    session after the move, and anything that reaps store directories by name would take
    it for an orphan of a session that no longer exists.
    """
    link = old_wt / ".venv"
    if not link.is_symlink():
        return None
    target = Path(os.path.realpath(link))
    suffix = f"--{old}"
    if not target.is_dir() or not target.name.endswith(suffix):
        return None
    return target, target.with_name(target.name[: -len(old)] + new)


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
            moves.append(
                PlannedMove("watcher lock", str(lock), "", "left alone: a lock, and the session is not running")
            )
    return moves


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
        hooks = _hooks.load_hooks()
    except _hooks.HookConfigError as exc:
        raise MigrationError(f"{exc} -- refusing before any write") from exc
    for hook in hooks:
        run = _hooks.run_hook(hook, old, new, dry_run=True, cwd=repo_root)
        if not run.ok:
            raise MigrationError(
                f"hook {hook.name} ({hook.path}) refused the migration (exit {run.returncode}): "
                f"{run.stderr or '(no stderr)'}"
            )
        if report is not None:
            report += [PlannedMove(f"hook {hook.name}", line, "", "report") for line in run.lines]
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
    venv_store = _plan_venv_store(old_wt, old, new)
    if venv_store is not None:
        source, dest = venv_store
        action = "conflict" if os.path.lexists(dest) else "move"
        moves.append(PlannedMove("venv store", str(source), str(dest), action))
        if action == "move":
            result.warnings.append(
                f"{dest} is renamed for the new name, and its console scripts name {source} in their "
                f"shebangs -- recreate it (`uv sync --reinstall` in {new_wt}) before running them"
            )
        venv_store = venv_store if action == "move" else None
    elif (old_wt / ".venv").is_dir() and not (old_wt / ".venv").is_symlink():
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

    held_moves = _plan_held_output(old, new, [(cc_project_dir(repo_root, home), None), (old_pd, new_pd)])
    moves += held_moves

    old_ns, new_ns = home / "tasks" / old, home / "tasks" / new
    result.task_moves, result.resume_moves = rekey_name_artifacts(
        new, task_namespaces=[old], resume_keys=[old], claude_home=home, dry_run=True
    )
    if old_ns.is_dir():
        leftover = _namespace_leftovers(old_ns, {m.source.name for m in result.task_moves})
        if leftover:
            moves.append(PlannedMove("task namespace", str(old_ns), f"holds {', '.join(leftover)}", "left in place"))
        else:
            moves.append(PlannedMove("task namespace", str(old_ns), "", "removed after rekey"))
    for item in moves:
        if item.action == "conflict":
            result.warnings.append(f"{item.kind} {item.source} was NOT moved: {item.dest} already exists")
    for conflict in [m for m in result.resume_moves if m.action == "conflict"]:
        result.warnings.append(f"resume artifact {conflict.source} was NOT re-keyed: {conflict.dest} already exists")
    hooks = _plan_hooks(old, new, repo_root, moves if dry_run else None)
    if dry_run:
        return result

    moved = _git(repo_root, "worktree", "move", str(old_wt), str(new_wt))
    if moved.returncode != 0:
        raise MigrationError(f"git worktree move failed, nothing was migrated: {(moved.stderr or '').strip()}")
    if venv_store is not None:
        # The link is replaced atomically, so the moved worktree never has no .venv at all.
        source, dest = venv_store
        source.rename(dest)
        link = new_wt / ".venv"
        temp = link.with_name(".venv.migrate-tmp")
        temp.symlink_to(dest, target_is_directory=True)
        temp.replace(link)
    if new_branch:
        renamed = rename_branch_anchored(repo_root, old_branch, new_branch)
        if not renamed.ok:
            anchored = _git(repo_root, "rev-parse", "--verify", "--quiet", renamed.anchor).returncode == 0
            kept = f"; the tip stays anchored at {renamed.anchor}" if anchored else ""
            result.warnings.append(f"branch rename to {new_branch} failed: {renamed.error}{kept}")
            new_branch = None
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

    if old_pd.is_dir():
        old_pd.rename(new_pd)
        for path in sorted(new_pd.glob("*.jsonl")):
            _rewrite_transcript_cwd(path, str(old_wt), str(new_wt))
            if path.name in {t.name for t in titled}:
                retitle_transcript(path, old, new)
            neutralise_worktree_state(path, new_wt)

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

    _apply_state(state_moves, old, new, old_wt, new_wt, regenerate_script, result)
    _apply_session_maps(map_moves, old, new, home, state)
    if iterm2_moves:
        _apply_iterm2(old, new)

    for hook in hooks:
        run = _hooks.run_hook(hook, old, new, dry_run=False, cwd=repo_root)
        moves += [PlannedMove(f"hook {hook.name}", line, "", "report") for line in run.lines]
        if not run.ok:
            result.warnings.append(
                f"hook {hook.name} ({hook.path}) FAILED (exit {run.returncode}): {run.stderr or '(no stderr)'} "
                f"-- the core moves are done; re-run the hook alone to finish"
            )

    if titled:
        result.resolved = probe_resolves(new_wt, new, home)
        if result.resolved is None:
            result.warnings.append(
                f"post-migrate check FAILED: `ai c` in {new_wt} resolves no transcript titled {new!r}"
            )
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
