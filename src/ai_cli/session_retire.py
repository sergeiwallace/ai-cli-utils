"""Retire a defunct ``ai`` session's home slot (``ai session-retire NAME``).

A session home ``<repo>/.worktrees/<name>`` is never reaped automatically: its
owner retires it. This is that route. It removes the checkout and its
canonical-registry entry, and nothing else of value:

* the tip of the branch checked out in the slot, and of ``wt-<name>``, is first
  anchored at ``refs/preserved/session-retire/<branch>``; the branches themselves
  stay, so the anchor plus the branch is the recovery path;
* the checkout goes through ``git worktree remove`` without ``--force``;
* the canonical-registry entry for the slot is dropped under the registry's lock;
* one JSON line is appended to ``<state>/session-retirements.jsonl``.

The Claude Code project directory (transcripts, memory) is left in place and
named in the output.

Every refusal is checked before the first write, ``--dry-run`` included: the slot
must be a registered worktree with no worktree nested inside it; the session must
not be running (a Claude Code process named NAME or working in the slot, a tmux
session that hosts it, or a heartbeat fresher than the stale-session reaper's
``stale_after_seconds``); the checkout must have no uncommitted change or
untracked file that is not ignored; and its checked-out commit must be contained
in its branch's upstream or ``origin/main``.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import config as _config
from .branch_rename import anchor_ref
from .canonical_worktrees import CanonicalWorktreeRegistryError, unregister_canonical_worktree
from .cc_migrate import cc_project_dir
from .git_repair import _git_env
from .session_adopt import AdoptionError
from .session_migrate import _refuse_live, _session_variants, _tmux_names
from .stale_session_reaper import (
    DEFAULT_STALE_AFTER_SECONDS,
    HEARTBEAT_VERSION,
    current_boot_generation,
    encode_ledger_component,
    read_reaper_config,
)

ANCHOR_NAMESPACE = "refs/preserved/session-retire"
LEDGER_FILENAME = "session-retirements.jsonl"
INTEGRATION_REF = "refs/remotes/origin/main"


class RetireError(AdoptionError):
    """A refusal raised before ``session-retire`` writes anything."""


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
    project_dir: Path | None = None
    ledger: Path | None = None
    dry_run: bool = False
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

    bases: list[str] = []
    if branch:
        upstream = _git(wt, "for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}").stdout.strip()
        if upstream and _resolve(wt, upstream):
            bases.append(upstream)
    if _resolve(wt, INTEGRATION_REF):
        bases.append(INTEGRATION_REF)
    label = branch or f"detached HEAD {head[:12]}"
    if not bases:
        blockers.append(f"{label} has no upstream and there is no origin/main to prove its commits are contained in")
        return blockers
    unique = _git(wt, "rev-list", head, "--not", *bases)
    if unique.returncode != 0:
        blockers.append(f"cannot compare {label} with {', '.join(bases)}: {(unique.stderr or '').strip()}")
    elif unique.stdout.split():
        count = len(unique.stdout.split())
        blockers.append(f"{label} has {count} commit(s) in neither {' nor '.join(bases)}")
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
            raise RetireError(
                f"anchor {ref} already points at {existing}, not {candidate}'s tip {tip}; it preserves an "
                f"earlier tip, so refusing to overwrite it"
            )
        anchors.append(Anchor(candidate, ref, tip, exists=existing is not None))
    return anchors


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
    """Retire the session home ``<repo_root>/.worktrees/<name>``; see the module docstring.

    Raises :class:`RetireError` or :class:`~ai_cli.session_adopt.LiveSessionError`
    before writing anything.
    """
    from .session import _current_branch, _registered_worktree_at, registered_worktrees

    repo_root = repo_root.resolve()
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    state = state_dir if state_dir is not None else _config.get_xdg_state_home()

    registered = registered_worktrees(repo_root)
    wt = _registered_worktree_at(repo_root / ".worktrees" / name, registered)
    if wt is None:
        raise RetireError(f"{repo_root / '.worktrees' / name} is not a registered worktree of {repo_root}")
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
    )
    if dry_run:
        return result

    for anchor in [a for a in anchors if not a.exists]:
        # The empty old value makes the write fail if the ref appeared meanwhile.
        written = _git(
            repo_root, "update-ref", "-m", f"session-retire: anchor {anchor.branch}", anchor.ref, anchor.tip, ""
        )
        if written.returncode != 0:
            raise RetireError(
                f"could not write anchor {anchor.ref}: {(written.stderr or '').strip()} -- nothing was removed"
            )
    removed = _git(repo_root, "worktree", "remove", str(wt))
    if removed.returncode != 0:
        raise RetireError(
            f"git worktree remove refused {wt}, so it is still in place: {(removed.stderr or '').strip()}"
        )
    try:
        result.registry_entries = unregister_canonical_worktree(wt, dry_run=False)
    except CanonicalWorktreeRegistryError as exc:
        result.warnings.append(f"the checkout is removed but its registry entry stays: {exc}")

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
