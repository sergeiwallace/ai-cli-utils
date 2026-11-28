"""Retire a defunct ``ai`` session's worktree (``ai retire-session NAME``).

A session home ``<repo>/.worktrees/<name>`` is never reaped automatically: its
owner retires it. This is that route, and the one for an exited ``ai c -t`` probe,
whose worktree lives outside the repository; :func:`session_worktree` reads which
worktree a name has from its run-state record. It removes the checkout and its
canonical-registry entry, and nothing else of value:

* the tip of the branch checked out in the slot, and of ``wt-<name>``, is first
  anchored at ``refs/preserved/retire-session/<branch>``; the branches themselves
  stay, so the anchor plus the branch is the recovery path;
* the checkout goes through ``git worktree remove`` without ``--force``, unless it
  passed the dead-checkout proof below;
* the canonical-registry entry for the slot is dropped under the registry's lock;
* this machine's session run-state rows for NAME (every engine and ``-r`` variant)
  are dropped, so ``ai sessions list`` stops showing the retired session;
* one JSON line is appended to ``<state>/session-retirements.jsonl``.

The Claude Code project directory (transcripts, memory) is left in place and
named in the output.

Every refusal is checked before the first write, ``--dry-run`` included: the slot
must be a registered worktree with no worktree nested inside it; the session must
not be running (a Claude Code process named NAME or working in the slot, a tmux
session that hosts it, or a heartbeat fresher than the stale-session reaper's
``stale_after_seconds``); the checkout must have no uncommitted change or
untracked file that is not ignored; and its checked-out commit must be contained
in its branch's upstream or ``origin/main``, or its content must have landed there.

A squash merge never makes a branch's commits ancestors of the target, so ancestry
alone refuses every squash-merged slot. The content proof covers that case, line by
line against the target's current tree: every line the branch adds since its
merge-base must be on the target at the same path, have been there at some commit
since the merge-base (it landed and was rewritten later), or have replaced a base line
the target also rewrote (the target moved on past it). A branch that adds no line at all
proves nothing and still refuses.

A locked checkout takes the dead-checkout proof instead of the content checks. A launch
killed while ``git worktree add`` was still populating the checkout leaves it locked as
``initializing``, with ``index.lock`` and no index, so ``git status`` calls every file
changed and git refuses a plain removal. :func:`_dead_checkout_failures` proves such a
checkout never started: the lock reason is exactly ``initializing``; its admin dir has
been unchanged for :data:`DEAD_CHECKOUT_QUIET_SECONDS`; no live process has its working
directory, an open file, a memory mapping, ``argv[0]`` or its executable inside the
checkout or its admin dir, and none whose details cannot be read (:func:`_out_of_reach`
names the exceptions); HEAD is contained in the branch's upstream or ``origin/main``; and every file on disk
is HEAD's blob or a strict prefix of it, with nothing outside HEAD's tree. The tips are then
anchored as usual and the checkout goes through ``git worktree remove --force --force``. A
locked checkout that fails any check is refused, naming each failed check.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil

from . import config as _config
from . import session_registry as _session_registry
from .branch_rename import anchor_ref
from .canonical_worktrees import CanonicalWorktreeRegistryError, unregister_canonical_worktree
from .cc_migrate import cc_project_dir
from .git_repair import _git_env
from .session_adopt import AdoptionError
from .session_migrate import _names_old_session, _refuse_live, _session_variants, _tmux_names
from .stale_session_reaper import (
    DEFAULT_STALE_AFTER_SECONDS,
    HEARTBEAT_VERSION,
    current_boot_generation,
    encode_ledger_component,
    read_reaper_config,
)

ANCHOR_NAMESPACE = "refs/preserved/retire-session"
LEDGER_FILENAME = "session-retirements.jsonl"
INTEGRATION_REF = "refs/remotes/origin/main"
DEAD_CHECKOUT_LOCK_REASON = "initializing"
DEAD_CHECKOUT_QUIET_SECONDS = 60
OS_EXECUTABLE_DIRS = (Path("/System"), Path("/usr/libexec"))


class RetireError(AdoptionError):
    """A refusal raised before ``retire-session`` writes anything."""


@dataclass
class Anchor:
    """One branch tip preserved before the checkout is removed."""

    branch: str
    ref: str
    tip: str
    exists: bool


@dataclass
class RetireResult:
    """What a retirement did, or would do under ``dry_run``."""

    name: str
    repo: Path
    worktree: Path
    head: str
    branch: str | None
    anchors: list[Anchor] = field(default_factory=list)
    registry_entries: int = 0
    session_rows: int = 0
    project_dir: Path | None = None
    ledger: Path | None = None
    dry_run: bool = False
    dead_checkout: bool = False
    warnings: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(), check=False)


def _resolve(repo: Path, ref: str) -> str | None:
    res = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None


def _fresh_heartbeats(name: str, state: Path, threshold: int, boot: str | None, now: float) -> list[str]:
    """Every heartbeat of a tmux session hosting ``name`` that does not prove it dead.

    The reaper's freshness rule: a record is fresh when it was written in this boot
    no more than ``threshold`` seconds ago on the monotonic clock. An unreadable
    record, an unknown boot or a stamp in the future cannot prove the session dead,
    so each counts as fresh.
    """
    directory = state / "session-heartbeats"
    if not directory.is_dir():
        return []
    variants = set(_session_variants(name))
    prefixes = tuple(f"{encode_ledger_component(v)}-" for v in variants)
    found: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if path.name.startswith(prefixes):
                found.append(f"{path} is unreadable")
            continue
        if not isinstance(record, dict) or record.get("session_name") not in variants:
            if not isinstance(record, dict) and path.name.startswith(prefixes):
                found.append(f"{path} is not a heartbeat record")
            continue
        stamp = record.get("monotonic_recorded_at")
        if record.get("version") != HEARTBEAT_VERSION or boot is None:
            found.append(f"{path} ({record['session_name']}) cannot be dated")
        elif record.get("boot_generation") != boot:
            continue
        elif not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp) or stamp > now:
            found.append(f"{path} ({record['session_name']}) has an invalid stamp")
        elif now - stamp <= threshold:
            found.append(f"{path} ({record['session_name']}) beat {int(now - stamp)}s ago (fresh within {threshold}s)")
    return found


def _u0_hunks(diff: str) -> list[tuple[list[str], list[str]]]:
    """The ``(removed, added)`` stripped lines of each hunk of a ``git diff -U0``."""
    hunks: list[tuple[list[str], list[str]]] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks.append(([], []))
        elif hunks and line[:1] in "-+" and not line.startswith(("---", "+++")):
            hunks[-1][0 if line[0] == "-" else 1].append(line[1:].strip())
    return hunks


def _unlanded_lines(repo: Path, head: str, target: str) -> dict[str, list[str]] | None:
    """Each path's added lines that did not land on ``target``; ``None`` when that cannot be read.

    An added line has landed when the target's copy of the path carries it, when a
    commit on the target since the merge-base carried it there, or when it replaced a
    base line the target also rewrote. The replacement is read positionally
    from a hunk that adds no more lines than it removes; any other hunk explains none of
    its lines, since git cannot say which of its additions is an insertion.

    A binary path has landed only when the target's blob is the branch's. A branch that
    adds nothing is reported under ``""``: no content was compared, so nothing is proved.
    """
    base = _git(repo, "merge-base", head, target).stdout.strip()
    if not base:
        return None
    numstat = _git(repo, "diff", "--no-renames", "--numstat", "-z", base, head)
    if numstat.returncode != 0:
        return None
    unlanded: dict[str, list[str]] = {}
    compared = False
    for entry in filter(None, numstat.stdout.split("\0")):
        added_count, _removed_count, path = entry.split("\t", 2)
        if added_count == "0":
            continue
        compared = True
        if added_count == "-":
            if _resolve_blob(repo, f"{head}:{path}") != _resolve_blob(repo, f"{target}:{path}"):
                unlanded[path] = ["(binary content)"]
            continue
        branch_diff = _git(repo, "diff", "-U0", "--no-renames", base, head, "--", path)
        target_diff = _git(repo, "diff", "-U0", "--no-renames", base, target, "--", path)
        if branch_diff.returncode != 0 or target_diff.returncode != 0:
            return None
        shown = _git(repo, "show", f"{target}:{path}")
        on_target = {line.strip() for line in shown.stdout.splitlines()} if shown.returncode == 0 else set()
        target_removed = {line for removed, _added in _u0_hunks(target_diff.stdout) for line in removed}
        # Each added line's verdict at every position it is added; all must hold.
        explained: dict[str, list[bool]] = {}
        for removed, added in _u0_hunks(branch_diff.stdout):
            pairable = len(added) <= len(removed)
            for index, line in enumerate(added):
                explained.setdefault(line, []).append(pairable and removed[index] in target_removed)
        absent = [line for line, verdicts in explained.items() if line and line not in on_target and not all(verdicts)]
        if absent:
            absent = _never_on_target(repo, base, target, path, absent)
        if absent is None:
            return None
        if absent:
            unlanded[path] = absent
    if not compared:
        unlanded[""] = []
    return unlanded


def _never_on_target(repo: Path, base: str, target: str, path: str, lines: list[str]) -> list[str] | None:
    """The ``lines`` no commit on ``target`` since ``base`` carried at ``path``.

    A squash merge leaves the merge-base where it was, so a line that landed and was
    then rewritten by a later change is absent from the target's tree yet on its history.
    """
    log = _git(repo, "log", "--format=%H", "--no-renames", f"{base}..{target}", "--", path)
    if log.returncode != 0:
        return None
    remaining = set(lines)
    for commit in log.stdout.split():
        shown = _git(repo, "show", f"{commit}:{path}")
        if shown.returncode == 0:
            remaining -= {line.strip() for line in shown.stdout.splitlines()}
        if not remaining:
            break
    return [line for line in lines if line in remaining]


def _resolve_blob(repo: Path, spec: str) -> str | None:
    res = _git(repo, "rev-parse", "--verify", "--quiet", spec)
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None


def _containment_bases(wt: Path, branch: str | None) -> list[str]:
    """The refs a checkout's commit must be contained in: its branch's upstream, then ``origin/main``."""
    bases: list[str] = []
    if branch:
        upstream = _git(wt, "for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}").stdout.strip()
        if upstream and _resolve(wt, upstream):
            bases.append(upstream)
    if _resolve(wt, INTEGRATION_REF):
        bases.append(INTEGRATION_REF)
    return list(dict.fromkeys(bases))


def _admin_dir(wt: Path) -> Path | None:
    res = _git(wt, "rev-parse", "--absolute-git-dir")
    return Path(res.stdout.strip()).resolve() if res.returncode == 0 and res.stdout.strip() else None


def _admin_entries(admin: Path) -> Iterator[os.stat_result]:
    yield admin.lstat()
    for root, dirs, files in os.walk(admin):
        for entry in (*dirs, *files):
            yield Path(root, entry).lstat()


def _inside(path: str | None, roots: tuple[Path, ...]) -> bool:
    if not path or not Path(path).is_absolute():
        return False
    candidate = Path(os.path.normcase(path))
    return any(candidate == root or root in candidate.parents for root in roots)


def _resident_detail(proc: psutil.Process, roots: tuple[Path, ...]) -> str | None:
    """What of ``proc`` lies inside ``roots``, or None; psutil errors propagate.

    psutil has no memory-map API on some platforms (macOS among them); there the open-file,
    working-directory and executable checks stand alone.
    """
    cwd = proc.cwd()
    if _inside(cwd, roots):
        return f"its working directory {cwd}"
    for opened in proc.open_files():
        if _inside(opened.path, roots):
            return f"open file {opened.path}"
    memory_maps = getattr(proc, "memory_maps", None)
    for mapping in memory_maps() if memory_maps is not None else ():
        if _inside(mapping.path, roots):
            return f"memory mapping {mapping.path}"
    argv = proc.cmdline()
    if argv:
        argv0 = Path(argv[0]) if Path(argv[0]).is_absolute() or not cwd else Path(cwd, argv[0])
        if _inside(os.path.normpath(argv0), roots):
            return f"argv[0] {argv[0]}"
    exe = proc.exe()
    if _inside(exe, roots):
        return f"executable {exe}"
    return None


def _out_of_reach(proc: psutil.Process, born: float) -> bool:
    """Whether a process this user cannot read is still provably not the session's.

    It is when it started before the checkout existed; when it runs as another user (on
    POSIX the effective uid, elsewhere the account name), since the checkout's own launch
    runs as this user; or when it runs an executable from :data:`OS_EXECUTABLE_DIRS`, where
    macOS keeps the sandboxed system agents that deny this user their details. Those
    directories exist only on POSIX systems. Any detail that cannot be read leaves it in reach.
    """
    try:
        if proc.create_time() < born:
            return True
    except psutil.Error:
        pass
    try:
        if _inside(proc.exe(), OS_EXECUTABLE_DIRS):
            return True
    except psutil.Error:
        pass
    try:
        if hasattr(os, "geteuid"):
            return proc.uids().effective != os.geteuid()
        return proc.username() != psutil.Process().username()
    except psutil.Error:
        return False


def _resident_processes(roots: tuple[Path, ...], born: float) -> list[str]:
    """Every live process that is, or may be, using a path inside ``roots``."""
    roots = tuple(Path(os.path.normcase(root)) for root in roots)
    found: list[str] = []
    for proc in psutil.process_iter():
        try:
            with proc.oneshot():
                detail = _resident_detail(proc, roots)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            if not _out_of_reach(proc, born):
                found.append(f"process {proc.pid} cannot be read, so nothing proves it is not using the checkout")
            continue
        if detail:
            found.append(f"process {proc.pid} has {detail} inside the checkout or its admin dir")
    return found


class _BlobReader:
    """Reads blobs through one ``git cat-file --batch``, which flushes after each object."""

    def __init__(self, repo: Path) -> None:
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_env(),
        )

    def read(self, sha: str) -> bytes | None:
        stdin, stdout = self._proc.stdin, self._proc.stdout
        if stdin is None or stdout is None:
            return None
        stdin.write(sha.encode("ascii") + b"\n")
        stdin.flush()
        header = stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            return None
        data = stdout.read(int(header[2]))
        stdout.read(1)
        return data

    def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.wait()


def _off_head_files(wt: Path, head: str) -> list[str] | None:
    """Every path on disk that is not HEAD's bytes or a strict prefix of them; None when HEAD cannot be listed."""
    listing = _git(wt, "ls-tree", "-r", "-z", "-l", "--full-tree", head)
    if listing.returncode != 0:
        return None
    blobs: dict[str, tuple[str, str, int]] = {}
    directories: set[str] = set()
    for entry in filter(None, listing.stdout.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, sha, size = meta.split()
        parts = path.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
        if kind == "blob":
            blobs[path] = (mode, sha, int(size))
        else:
            directories.add(path)

    off: list[str] = []
    reader = _BlobReader(wt)
    try:
        for root, dirs, files in os.walk(wt):
            rel_root = Path(root).relative_to(wt).as_posix()
            prefix = "" if rel_root == "." else f"{rel_root}/"
            if not prefix and ".git" in files:
                files.remove(".git")
            for name in list(dirs):
                full, rel = Path(root, name), f"{prefix}{name}"
                if full.is_symlink():
                    dirs.remove(name)
                    files.append(name)
                elif rel not in directories:
                    dirs.remove(name)
                    off.append(f"{rel}/ is not in HEAD's tree")
            for name in files:
                full, rel = Path(root, name), f"{prefix}{name}"
                if rel not in blobs:
                    off.append(f"{rel} is not in HEAD's tree")
                    continue
                mode, sha, size = blobs[rel]
                if full.is_symlink():
                    same = mode == "120000" and os.fsencode(full.readlink()) == reader.read(sha)
                    if not same:
                        off.append(f"{rel} is a symlink HEAD does not hold")
                    continue
                on_disk = full.lstat().st_size
                if on_disk == 0:
                    continue
                if on_disk > size:
                    off.append(f"{rel} is neither HEAD's blob nor a prefix of it")
                    continue
                blob = reader.read(sha)
                if blob is None or not blob.startswith(full.read_bytes()):
                    off.append(f"{rel} is neither HEAD's blob nor a prefix of it")
    finally:
        reader.close()
    return off


def _dead_checkout_failures(wt: Path, admin: Path, head: str, branch: str | None) -> list[str]:
    """Every check a locked checkout fails of the proof that it is an interrupted, never-started one."""
    failures: list[str] = []
    try:
        reason = (admin / "locked").read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        reason = f"unreadable ({exc})"
    if reason != DEAD_CHECKOUT_LOCK_REASON:
        failures.append(f"lock reason is `{reason}`, not `{DEAD_CHECKOUT_LOCK_REASON}`")

    try:
        stamps = [stat.st_mtime for stat in _admin_entries(admin)]
    except OSError as exc:
        failures.append(f"its admin dir {admin} cannot be read: {exc}")
        stamps = []
    if stamps:
        age = time.time() - max(stamps)
        if age < DEAD_CHECKOUT_QUIET_SECONDS:
            failures.append(
                f"its admin dir {admin} changed {max(int(age), 0)}s ago; it must be unchanged for "
                f"{DEAD_CHECKOUT_QUIET_SECONDS}s"
            )
        failures.extend(_resident_processes((wt, admin), min(stamps)))

    bases = _containment_bases(wt, branch)
    if not bases:
        failures.append("there is no upstream and no origin/main to prove HEAD is contained in")
    elif not any(_git(wt, "merge-base", "--is-ancestor", head, base).returncode == 0 for base in bases):
        failures.append(f"HEAD {head[:12]} is not contained in {' or '.join(bases)}")

    try:
        off = _off_head_files(wt, head)
    except OSError as exc:
        off = [f"the checkout cannot be read: {exc}"]
    if off is None:
        failures.append(f"HEAD {head[:12]}'s tree cannot be listed")
    else:
        failures.extend(off)
    return failures


def _content_blockers(wt: Path, head: str, branch: str | None) -> list[str]:
    """Everything in the checkout that removing it could lose."""
    blockers: list[str] = []
    # --no-optional-locks: a plain status refreshes the index, and a preview writes nothing.
    status = _git(wt, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        return [f"git status failed in {wt}: {(status.stderr or '').strip()}"]
    for line in status.stdout.splitlines():
        if line.strip():
            blockers.append(f"{'untracked' if line.startswith('??') else 'uncommitted'}: {line[3:]}")

    bases = _containment_bases(wt, branch)
    label = branch or f"detached HEAD {head[:12]}"
    if not bases:
        blockers.append(f"{label} has no upstream and there is no origin/main to prove its commits are contained in")
        return blockers
    unique = _git(wt, "rev-list", head, "--not", *bases)
    if unique.returncode != 0:
        blockers.append(f"cannot compare {label} with {', '.join(bases)}: {(unique.stderr or '').strip()}")
        return blockers
    if not unique.stdout.split():
        return blockers
    count = len(unique.stdout.split())
    where = f"in neither {' nor '.join(bases)}" if len(bases) > 1 else f"not in {bases[0]}"
    reasons: list[str] = []
    for target in bases:
        unlanded = _unlanded_lines(wt, head, target)
        if unlanded == {}:
            return blockers
        if unlanded is None:
            reasons.append(f"its content could not be compared with {target}")
        elif "" in unlanded:
            reasons.append(f"it adds no line, so no content proves it landed on {target}")
        else:
            path, lines = sorted(unlanded.items())[0]
            total = sum(len(v) for v in unlanded.values())
            reasons.append(
                f"{total} added line(s) in {len(unlanded)} file(s) are not on {target}, e.g. {path} adds `{lines[0]}`"
            )
    blockers.append(f"{label} has {count} commit(s) {where}, and {'; '.join(reasons)}")
    return blockers


def _plan_anchors(repo: Path, name: str, branch: str | None) -> list[Anchor]:
    anchors: list[Anchor] = []
    for candidate in dict.fromkeys(b for b in (branch, f"wt-{name}") if b):
        tip = _resolve(repo, f"refs/heads/{candidate}")
        if tip is None:
            continue
        ref = anchor_ref(candidate, ANCHOR_NAMESPACE)
        existing = _resolve(repo, ref)
        if existing is not None and existing != tip:
            # A reissued name reuses the branch name with a new tip. The plain anchor
            # preserves the earlier holder's tip and is never moved, so this tip gets its
            # own anchor beside it, named by the tip it preserves.
            ref = f"{ref}--{tip[:12]}"
            existing = _resolve(repo, ref)
            if existing is not None and existing != tip:
                raise RetireError(
                    f"anchor {ref} already points at {existing}, not {candidate}'s tip {tip}; it preserves an "
                    f"earlier tip, so refusing to overwrite it"
                )
        anchors.append(Anchor(candidate, ref, tip, exists=existing is not None))
    return anchors


def _drop_session_rows(name: str, *, dry_run: bool) -> int:
    """Drop this machine's run-state rows for ``name`` (every engine and remote variant)."""
    names = set(_session_variants(name))
    if not _session_registry.registry_path().exists():
        return 0
    if dry_run:
        rows = _session_registry.load_registry()["sessions"]
        return sum(1 for r in rows if _names_old_session(r, names) and r.get("kind") == "local")
    dropped: list[dict] = []

    def drop(doc: dict) -> bool:
        kept = []
        for record in doc["sessions"]:
            if _names_old_session(record, names) and record.get("kind") == "local":
                dropped.append(record)
            else:
                kept.append(record)
        doc["sessions"] = kept
        return bool(dropped)

    _session_registry._mutate(drop, timeout=2)
    return len(dropped)


def session_worktree(repo_root: Path, name: str, registered: list[Path]) -> Path:
    """The registered worktree session ``name`` runs in, read from its run-state record.

    A session's record keeps the directory it was launched in (``cwd`` and its boot recipe's
    ``cwd``), which is where its worktree really is: ``<repo>/.worktrees/<name>`` for a
    session home, the probe directory for an ``ai c -t`` probe. A recorded directory counts
    only when it is a linked worktree of ``repo_root``; the repository's own checkout, which a
    ``--no-worktree`` session records, is never one. The slots ``<repo>/.worktrees/<name>``
    and the probe directory for ``name`` count too, since pruning drops an exited probe's
    record. A record and a slot naming different worktrees, or records naming two, refuse
    rather than pick one.
    """
    from .session import _registered_worktree_at, probe_worktree_base

    slot = repo_root / ".worktrees" / name
    variants = set(_session_variants(name))
    recorded: list[Path] = []
    if _session_registry.registry_path().exists():
        try:
            rows = _session_registry.load_registry()["sessions"]
        except _session_registry.RegistryError:
            rows = []
        for record in rows:
            if record.get("kind") != "local" or not _names_old_session(record, variants):
                continue
            recipe = record.get("boot_recipe") if isinstance(record.get("boot_recipe"), dict) else {}
            for cwd in (recipe.get("cwd"), record.get("cwd")):
                found = _registered_worktree_at(Path(cwd), registered) if isinstance(cwd, str) and cwd else None
                if found is not None and found != repo_root and found not in recorded:
                    recorded.append(found)
    worktrees: list[Path] = list(recorded)
    for candidate in (slot, probe_worktree_base(repo_root) / name):
        found = _registered_worktree_at(candidate, registered)
        if found is not None and found not in worktrees:
            worktrees.append(found)
    if not worktrees:
        raise RetireError(f"{slot} is not a registered worktree of {repo_root}, and no session record names one")
    if len(worktrees) > 1:
        raise RetireError(
            f"{name} names more than one worktree ({', '.join(str(w) for w in worktrees)}); retire each by hand"
        )
    return worktrees[0]


def retire_session(
    repo_root: Path,
    name: str,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
    state_dir: Path | None = None,
    proc_dir: Path | None = None,
    tmux_names: Callable[[], list[str]] | None = None,
    config: Mapping[str, object] | None = None,
    boot_generation: Callable[[], str | None] = current_boot_generation,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> RetireResult:
    """Retire session ``name``'s worktree (:func:`session_worktree`); see the module docstring.

    Raises :class:`RetireError` or :class:`~ai_cli.session_adopt.LiveSessionError`
    before writing anything.
    """
    from .session import _current_branch, registered_worktrees

    repo_root = repo_root.resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()

    registered = registered_worktrees(repo_root)
    wt = session_worktree(repo_root, name, registered)
    nested = [w for w in registered if w != wt and wt in w.parents]
    if nested:
        raise RetireError(
            f"{wt} contains other registered worktrees ({', '.join(str(w) for w in nested)}); retire or move each first"
        )

    _refuse_live(name, name, wt, wt, home, proc_dir, (tmux_names or _tmux_names)(), verb="retired")
    settings = read_reaper_config(config if config is not None else _config.load_config())
    threshold = settings.stale_after_seconds if settings else DEFAULT_STALE_AFTER_SECONDS
    fresh = _fresh_heartbeats(name, state, threshold, boot_generation(), monotonic_clock())
    if fresh:
        raise RetireError(f"{name} may still be running: " + "; ".join(fresh))

    head = _resolve(wt, "HEAD")
    if head is None:
        raise RetireError(f"{wt} has no commit checked out; nothing proves its contents are preserved")
    branch = _current_branch(wt)
    admin = _admin_dir(wt)
    dead_checkout = admin is not None and (admin / "locked").exists()
    if dead_checkout and admin is not None:
        failures = _dead_checkout_failures(wt, admin, head, branch)
        if failures:
            raise RetireError(
                f"{wt} is locked, and the proof that it is an interrupted, never-started checkout failed:\n  "
                + "\n  ".join(failures)
            )
    else:
        blockers = _content_blockers(wt, head, branch)
        if blockers:
            raise RetireError(f"{wt} holds work a removal would lose:\n  " + "\n  ".join(blockers))

    anchors = _plan_anchors(repo_root, name, branch)
    try:
        entries = unregister_canonical_worktree(wt, dry_run=True)
    except CanonicalWorktreeRegistryError as exc:
        raise RetireError(f"{exc} -- refusing before any write") from exc

    project = cc_project_dir(wt, home)
    result = RetireResult(
        name=name,
        repo=repo_root,
        worktree=wt,
        head=head,
        branch=branch,
        anchors=anchors,
        registry_entries=entries,
        project_dir=project if project.is_dir() else None,
        ledger=state / LEDGER_FILENAME,
        dry_run=dry_run,
        dead_checkout=dead_checkout,
    )
    try:
        result.session_rows = _drop_session_rows(name, dry_run=True)
    except _session_registry.RegistryError as exc:
        result.warnings.append(f"the session registry cannot be read, so its rows for {name} stay: {exc}")
    if dry_run:
        return result

    for anchor in [a for a in anchors if not a.exists]:
        # The empty old value makes the write fail if the ref appeared meanwhile.
        written = _git(
            repo_root, "update-ref", "-m", f"retire-session: anchor {anchor.branch}", anchor.ref, anchor.tip, ""
        )
        if written.returncode != 0:
            raise RetireError(
                f"could not write anchor {anchor.ref}: {(written.stderr or '').strip()} -- nothing was removed"
            )
    force = ("--force", "--force") if dead_checkout else ()
    removed = _git(repo_root, "worktree", "remove", *force, str(wt))
    if removed.returncode != 0:
        raise RetireError(
            f"git worktree remove refused {wt}, so it is still in place: {(removed.stderr or '').strip()}"
        )
    try:
        result.registry_entries = unregister_canonical_worktree(wt, dry_run=False)
    except CanonicalWorktreeRegistryError as exc:
        result.warnings.append(f"the checkout is removed but its registry entry stays: {exc}")
    if result.session_rows:
        try:
            result.session_rows = _drop_session_rows(name, dry_run=False)
        except (_session_registry.RegistryError, OSError) as exc:
            result.warnings.append(f"the checkout is removed but its session registry rows stay: {exc}")

    state.mkdir(parents=True, exist_ok=True)
    row = {
        "time": datetime.now(UTC).isoformat(),
        "name": name,
        "repo": str(repo_root),
        "worktree": str(wt),
        "head": head,
        "branch": branch,
        "anchors": {a.ref: a.tip for a in anchors},
        "branch_tips": {a.branch: a.tip for a in anchors},
    }
    with (state / LEDGER_FILENAME).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row, sort_keys=True) + "\n")
    return result
