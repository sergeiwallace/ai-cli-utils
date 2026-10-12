"""Migrate a registered ``ai`` session to a new name (``ai session-migrate OLD NEW``).

``ai session-adopt`` brings in a session that was started *outside* ``ai c``. This
module moves one that already lives in a slot -- ``<repo>/.worktrees/<old>`` -- to
``<repo>/.worktrees/<new>``, carrying everything keyed by the old name or by the
old worktree path, so ``ai c <new>`` resumes the same conversation with the same
tasks, memory and resume hand-off:

* the worktree (``git worktree move``), its ``wt-<old>`` branch, and every
  ``branch.*.aih-session-home`` marker naming the old path;
* the canonical-session registry entry for the old path;
* the Claude Code project directory (transcripts, sidecars, worktree-slug
  memory), with each transcript's recorded cwd repointed, its ``customTitle``
  renamed and its stale worktree binding cleared;
* the task namespace and the resume tree, through
  :func:`ai_cli.session_adopt.rekey_name_artifacts`, the executor ``session-adopt``
  uses for the same two;
* the launcher's own state: session metadata, the wrapper script (regenerated, so
  ``CLAUDE_CODE_TASK_LIST_ID`` and ``--name`` follow), the resume prompt, the
  session maps and the iTerm2 restore registry.

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
from . import session_registry as _session_registry
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

    branch = _current_branch(old_wt)
    new_branch: str | None = None
    if branch == f"wt-{old}":
        if _git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/wt-{new}").returncode == 0:
            result.warnings.append(f"branch wt-{new} already exists, so the worktree keeps branch {branch}")
        else:
            new_branch = f"wt-{new}"
            moves.append(PlannedMove("branch", branch, new_branch))
    markers = _session_home_markers(repo_root, old_wt)
    for key, _value in markers:
        renamed = key.replace(f"branch.{branch}.", f"branch.{new_branch}.", 1) if new_branch else key
        moves.append(PlannedMove("session-home marker", key, f"{renamed} = {new_wt}", "rewrite"))

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

    old_ns, new_ns = home / "tasks" / old, home / "tasks" / new
    result.task_moves, result.resume_moves = rekey_name_artifacts(
        new, task_namespaces=[old], resume_keys=[old], claude_home=home, dry_run=True
    )
    for item in moves:
        if item.action == "conflict":
            result.warnings.append(f"{item.kind} {item.source} was NOT moved: {item.dest} already exists")
    for conflict in [m for m in result.resume_moves if m.action == "conflict"]:
        result.warnings.append(f"resume artifact {conflict.source} was NOT re-keyed: {conflict.dest} already exists")
    if dry_run:
        return result

    moved = _git(repo_root, "worktree", "move", str(old_wt), str(new_wt))
    if moved.returncode != 0:
        raise MigrationError(f"git worktree move failed, nothing was migrated: {(moved.stderr or '').strip()}")
    if new_branch:
        renamed = _git(repo_root, "branch", "-m", f"wt-{old}", new_branch)
        if renamed.returncode != 0:
            result.warnings.append(f"branch rename to {new_branch} failed: {(renamed.stderr or '').strip()}")
            new_branch = None
    for key, _value in markers:
        target_key = key.replace(f"branch.{branch}.", f"branch.{new_branch}.", 1) if new_branch else key
        _git(repo_root, "config", target_key, str(new_wt))
    move_canonical_worktree(old_wt, new_wt, old, new, dry_run=False)

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

    _apply_state(state_moves, old, new, old_wt, new_wt, regenerate_script, result)
    _apply_session_maps(map_moves, old, new, home, state)
    if iterm2_moves:
        _apply_iterm2(old, new)

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
