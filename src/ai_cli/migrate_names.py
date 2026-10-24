"""Migrating sessions to human names: ``ai migrate-session OLD --to-name [NAME]`` and the host sweep.

The rename itself is :func:`ai_cli.session_migrate.migrate_session`. This module puts
the name registry around it: every refusal check runs first, then the new name is
reserved together with an ``intent`` record of the rename, then the session is
renamed, then the record is marked ``complete`` in the commit that also releases a
human-named old row.

A rename that fails after its reservation is probed for anything left under the new
title. When nothing is, the new row and its intent record are released together; when
something is, the row is held as ``migration-incomplete`` with the survivors listed,
and running the same command again resumes it. The same recovery step runs first in
every migration and every reconcile, so an ``intent`` record whose rename finished but
whose completion was never published completes itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import name_list, session_clients
from . import name_registry as reg
from .session_migrate import MigrationError, finish_session, migrate_session

ENGINES = ("c", "g", "p", "cx")


@dataclass(frozen=True)
class TitleParts:
    stem: str
    label: str | None
    tail: str


def split_title(title: str, stem: str) -> TitleParts | None:
    """``myrepo-box-review-3`` under ``myrepo-box``: label ``review``, tail ``3``.

    A title named before the host alias joined names (``myrepo-review-3``) is read under
    the repo alias alone, the part of ``stem`` before its last hyphen.
    """
    legacy = stem.rsplit("-", 1)[0]
    for prefix in (stem, legacy):
        if title.startswith(f"{prefix}-"):
            head, _, tail = title[len(prefix) + 1 :].rpartition("-")
            return TitleParts(prefix, head or None, tail)
    return None


def bounded_max_length(max_length: int | None, policy: dict) -> int | None:
    """``--max-length N`` checked against both fleet limits; outside them it is refused."""
    if max_length is None:
        return None
    low, high = name_list.limits(policy)
    if not low <= max_length <= high:
        raise ValueError(
            f"--max-length {max_length} is outside the configured limits of {low} to {high} letters "
            "(config/session-names.toml [limits]); a longer name would fail the next install's check, "
            "so raise max_name_length there first"
        )
    return max_length


def _tmux_names() -> list[str]:
    from .session import _tmux_session_names

    return _tmux_session_names()


def artifacts_under(title: str, repo_root: Path, adapters: list[session_clients.Adapter]) -> list[dict]:
    """Every artifact on this host still carrying ``title``: worktree, tmux session, client records."""
    found: list[dict] = []
    worktree = repo_root / ".worktrees" / title
    if worktree.exists():
        found.append({"kind": "worktree", "identifier": str(worktree)})
    live = set(_tmux_names())
    for engine in ENGINES:
        for name in (f"{engine}-{title}", f"{engine}-r-{title}"):
            if name in live:
                found.append({"kind": "tmux", "identifier": name})
    for adapter in adapters:
        try:
            records = adapter.records_for(worktree) + adapter.records_titled(title)
        except OSError:
            continue
        for record in {str(record.path): record for record in records}.values():
            found.append({"kind": adapter.client, "identifier": str(record.path)})
    return found


@dataclass
class NamedMigration:
    old: str
    new: str
    released_old: bool = False
    result: object = None
    notes: list[str] = field(default_factory=list)


def migrate_to_name(
    registry: reg.Registry,
    repo_root: Path,
    old: str,
    slot: reg.Slot,
    *,
    name: str | None,
    max_length: int | None,
    words: frozenset[str],
    adapters: list[session_clients.Adapter],
    regenerate_script: Callable[[dict], str] | None = None,
    migrate: Callable[..., object] = migrate_session,
    complete: Callable[[reg.Registry, str, str], bool] = reg.complete_migration,
) -> NamedMigration:
    """Reserve a name for ``old``'s session and rename it, releasing what a failure leaves unused."""
    preview = (name or "zz").lower()
    migrate(
        repo_root, old, slot.title_for(preview), dry_run=True, regenerate_script=regenerate_script, new_is_reserved=True
    )
    reservation = reg.reserve_for_migration(
        registry, slot, old, name=name.lower() if name else None, max_length=max_length, words=words
    )
    try:
        result = migrate(
            repo_root, old, reservation.title, dry_run=False, regenerate_script=regenerate_script, new_is_reserved=True
        )
    except Exception as exc:
        survivors = artifacts_under(reservation.title, repo_root, adapters)
        if not survivors:
            reg.release_failed_migration(registry, old, slot.machine)
            raise MigrationError(
                f"the rename of {old} to {reservation.title} failed ({exc}); nothing was left under the new name, "
                f"so {reservation.name} was released"
            ) from exc
        reg.mark_migration_incomplete(registry, old, slot.machine, survivors)
        listed = ", ".join(f"{item['kind']} {item['identifier']}" for item in survivors)
        raise MigrationError(
            f"the rename of {old} to {reservation.title} failed ({exc}) and left {listed} under the new name; "
            f"{reservation.name} stays held as migration-incomplete. Run the same command again to resume it."
        ) from exc
    try:
        complete(registry, old, slot.machine)
    except reg.RegistryError as exc:
        raise MigrationError(
            f"{old} was renamed to {reservation.title}, but the completion could not be published ({exc}); "
            "the next `ai reconcile-names` or `ai migrate-session` publishes it"
        ) from exc
    return NamedMigration(old=old, new=reservation.title, released_old=old.rsplit("-", 1)[-1].isalpha(), result=result)


def recover_intents(
    registry: reg.Registry,
    machine: str,
    *,
    projects_root: Path,
    adapters: list[session_clients.Adapter],
    resume: Callable[[Path, str, str], object] | None = None,
) -> list[str]:
    """Settle this machine's ``intent`` records from what is on disk; changes nothing when run twice.

    Every artifact under the new title and none under the old: publish the completion.
    Every artifact under the old title: resume the rename when ``resume`` is given (a
    migration), else leave it listed by ``ai check-names`` (a reconcile). Artifacts under
    both: hold the row as ``migration-incomplete``. The new row is never released here.
    """
    snapshot = registry.snapshot()
    settled: list[str] = []
    for old, record in snapshot.migrations.items():
        if record.get("status") != "intent" or record.get("machine") != machine:
            continue
        new = str(record["new_title"])
        token = new.rsplit("-", 1)[-1]
        row = snapshot.rows.get(token)
        repo_root = projects_root / str((row or {}).get("repo") or "")
        under_new = artifacts_under(new, repo_root, adapters)
        under_old = artifacts_under(old, repo_root, adapters)
        if under_new and not under_old:
            reg.complete_migration(registry, old, machine)
            settled.append(f"{old} -> {new}: completed")
        elif under_old and not under_new:
            if resume is not None:
                resume(repo_root, old, new)
                reg.complete_migration(registry, old, machine)
                settled.append(f"{old} -> {new}: resumed and completed")
        elif under_old and under_new:
            reg.mark_migration_incomplete(registry, old, machine, under_new)
            settled.append(f"{old} -> {new}: migration-incomplete")
    return settled


def resume_rename(repo_root: Path, old: str, new: str, regenerate_script=None) -> object:
    """Carry on a rename from wherever it stopped: the full move, or the finish of one that moved.

    A rename that stopped after the worktree moved has its Claude Code project directory
    carried here too, as the full move would have, before the old-name leftovers are finished.
    """
    if not (repo_root / ".worktrees" / new).exists():
        return migrate_session(repo_root, old, new, regenerate_script=regenerate_script, new_is_reserved=True)
    carry_project_dir(repo_root / ".worktrees" / old, repo_root / ".worktrees" / new, old, new)
    return finish_session(repo_root, old, new)


def carry_project_dir(old_wt: Path, new_wt: Path, old: str, new: str, home: Path | None = None) -> bool:
    """Move the Claude Code project directory of ``old_wt`` to ``new_wt``'s, retitled; False when there is none."""
    from .cc_migrate import cc_project_dir
    from .session_adopt import retitle_transcript
    from .session_migrate import _rewrite_transcript_cwd, neutralise_worktree_state

    claude = (home or Path.home()) / ".claude"
    old_pd, new_pd = (
        cc_project_dir(old_wt.resolve(strict=False), claude),
        cc_project_dir(new_wt.resolve(strict=False), claude),
    )
    if not old_pd.is_dir() or new_pd.exists():
        return False
    old_pd.rename(new_pd)
    for path in sorted(new_pd.glob("*.jsonl")):
        _rewrite_transcript_cwd(path, str(old_wt.resolve(strict=False)), str(new_wt.resolve(strict=False)))
        retitle_transcript(path, old, new)
        neutralise_worktree_state(path, new_wt)
    return True


# --- the host inventory ------------------------------------------------------------------------


@dataclass
class InventoryItem:
    title: str
    kinds: list[str]
    running: bool
    worktree: Path | None
    records: list[session_clients.Record] = field(default_factory=list)
    would_be: str | None = None


@dataclass
class Inventory:
    items: list[InventoryItem]
    blocking: list[str]


def _is_index_title(title: str, stem: str) -> bool:
    parts = split_title(title, stem)
    return parts is not None and parts.tail.isdigit()


def inventory(
    repo_root: Path,
    stem: str,
    *,
    adapters: list[session_clients.Adapter],
    select: Callable[[str], bool] | None = None,
) -> Inventory:
    """Every index-named session of this repo on this host: worktrees, tmux sessions and client records.

    ``select`` picks the titles to list; by default every index-named title under ``stem``.
    A record a client store holds but this package cannot read, or a client with no
    adapter, is a blocking entry: an inventory that skipped it would claim to be complete.
    """
    wanted = select or (lambda title: _is_index_title(title, stem))
    items: dict[str, InventoryItem] = {}
    blocking: list[str] = []

    def item(title: str) -> InventoryItem:
        if title not in items:
            items[title] = InventoryItem(title=title, kinds=[], running=False, worktree=None)
        return items[title]

    worktrees_dir = repo_root / ".worktrees"
    listing = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"], capture_output=True, text=True, check=False
    )
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
            if path.parent.resolve(strict=False) == worktrees_dir.resolve(strict=False) and wanted(path.name):
                entry = item(path.name)
                entry.kinds.append("worktree")
                entry.worktree = path
    for name in _tmux_names():
        for engine in ENGINES:
            for prefix in (f"{engine}-r-", f"{engine}-"):
                if name.startswith(prefix) and wanted(name[len(prefix) :]):
                    entry = item(name[len(prefix) :])
                    entry.kinds.append(f"tmux {name}")
                    entry.running = True
                    break
            else:
                continue
            break
    for adapter in adapters:
        try:
            records = adapter.all_records()
        except OSError as exc:
            blocking.append(f"{adapter.client} store {adapter.store_root()} could not be read: {exc}")
            continue
        for record in records:
            if record.problem:
                blocking.append(f"{adapter.client} record {record.path} cannot be read: {record.problem}")
                continue
            title = record.title
            if record.cwd and Path(record.cwd).parent.resolve(strict=False) == worktrees_dir.resolve(strict=False):
                title = title or Path(record.cwd).name
            if title and wanted(title):
                entry = item(title)
                entry.kinds.append(f"{adapter.client} record")
                entry.records.append(record)
    return Inventory(items=sorted(items.values(), key=lambda entry: entry.title), blocking=blocking)


def slot_for(
    title: str, stem: str, repo: str, repo_alias: str, host_alias: str, machine: str, client: str, *, worktree: bool
) -> reg.Slot:
    parts = split_title(title, stem)
    return reg.Slot(
        repo=repo,
        repo_alias=repo_alias,
        host_alias=host_alias,
        label=parts.label if parts else None,
        machine=machine,
        client=client,
        worktree=worktree,
    )


def migrate_transcript_only(
    registry: reg.Registry,
    item: InventoryItem,
    slot: reg.Slot,
    *,
    max_length: int | None,
    words: frozenset[str],
) -> NamedMigration:
    """Name a session whose worktree is gone: reserve, retitle its client records, complete. No worktree is made."""
    reservation = reg.reserve_for_migration(registry, slot, item.title, name=None, max_length=max_length, words=words)
    changes: list[session_clients.Changed] = []
    try:
        for record in item.records:
            adapter = session_clients.adapter(record.client)
            changes += adapter.rename(record, item.title, reservation.title, None).changed
    except Exception as exc:
        failed = session_clients.rollback(changes)
        if failed:
            survivors = [{"kind": "record", "identifier": entry} for entry in failed]
            reg.mark_migration_incomplete(registry, item.title, slot.machine, survivors)
            raise MigrationError(
                f"renaming {item.title}'s records failed ({exc}) and could not be undone: {failed}"
            ) from exc
        reg.release_failed_migration(registry, item.title, slot.machine)
        raise MigrationError(f"renaming {item.title}'s records failed ({exc}); the change was rolled back") from exc
    reg.complete_migration(registry, item.title, slot.machine)
    return NamedMigration(old=item.title, new=reservation.title)
