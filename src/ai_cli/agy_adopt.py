"""Adopt an exact Antigravity UUID without modifying its native store."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import portalocker
import psutil

from . import agy_session, config, name_registry, session_naming, session_registry, tmux_binary
from .canonical_worktrees import register_canonical_worktree
from .session_adopt import AdoptionError, _ensure_worktree
from .session_clients import PRESENT, Antigravity


def _native_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise AdoptionError("the Antigravity conversation ID must be a UUID") from None


def _process_arguments(process: psutil.Process) -> tuple[str | None, Path | None]:
    args = iter(process.cmdline()[1:])
    for arg in args:
        if arg == "--conversation":
            return _native_id(next(args, "")), None
        if arg.startswith("--conversation="):
            return _native_id(arg.split("=", 1)[1]), None
    return None, Path(process.cwd()).resolve()


def _agy_process(process: psutil.Process) -> bool:
    return process.name().lower() in {"agy", "agy.exe"} and process.status() not in {
        psutil.STATUS_ZOMBIE,
        psutil.STATUS_DEAD,
    }


def _discovery_processes(pid: int | None, tmux: str | None) -> list[psutil.Process]:
    try:
        if pid is not None:
            process = psutil.Process(pid)
            return [process] if _agy_process(process) else []
        if tmux is None:
            return []
        panes = subprocess.run(
            tmux_binary.argv("list-panes", "-s", "-t", f"={tmux}:", "-F", "#{pane_pid}"),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        processes = {}
        for line in panes.stdout.splitlines():
            parent = psutil.Process(int(line))
            for process in (parent, *parent.children(recursive=True)):
                if _agy_process(process):
                    processes[process.pid] = process
        return list(processes.values())
    except (OSError, psutil.Error, subprocess.SubprocessError) as exc:
        raise AdoptionError("could not inspect the Antigravity process; supply its exact UUID") from exc


def _discover(client: Antigravity, pid: int | None, tmux: str | None) -> str:
    matches = set()
    try:
        for process in _discovery_processes(pid, tmux):
            conversation, cwd = _process_arguments(process)
            if conversation is not None:
                matches.add(conversation)
            elif cwd is not None:
                matches.update(record.session_id for record in client.records_for(cwd) if record.session_id is not None)
    except psutil.Error as exc:
        raise AdoptionError("could not inspect the Antigravity process; supply its exact UUID") from exc
    if len(matches) != 1:
        raise AdoptionError(f"Antigravity discovery matched {len(matches)} conversations; supply the exact UUID")
    return matches.pop()


@contextmanager
def _presence_guard(client: Antigravity, conversation: str, stopped: bool) -> Iterator[None]:
    path = client.store_root() / "presence" / f"{conversation}.lock"
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        yield
        return
    except OSError as exc:
        if not stopped:
            raise AdoptionError("could not read the Antigravity presence lock; verify it is stopped first") from exc
        yield
        return
    with handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.AlreadyLocked as exc:
            raise AdoptionError(f"Antigravity conversation {conversation} still holds its presence lock") from exc
        except portalocker.exceptions.LockException as exc:
            if not stopped:
                raise AdoptionError(
                    "could not check the Antigravity presence lock; verify it is stopped first"
                ) from exc
        yield


def _require_stopped(client: Antigravity, conversation: str, targets: set[str], stopped: bool) -> None:
    for target in sorted(targets):
        live = session_registry._local_session_alive(target)
        if live is True:
            raise AdoptionError(f"stop tmux session {target} before adopting conversation {conversation}")
        if live is None and not stopped:
            raise AdoptionError(f"could not check tmux session {target}; confirm it is stopped with --stopped")
    workspaces = {
        Path(record.cwd).resolve()
        for record in client.all_records()
        if record.session_id == conversation and record.cwd is not None
    }
    for process in psutil.process_iter():
        try:
            if not _agy_process(process):
                continue
            exact, cwd = _process_arguments(process)
            if exact == conversation or cwd in workspaces:
                raise AdoptionError(
                    f"stop Antigravity process {process.pid} before adopting conversation {conversation}"
                )
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            if not stopped:
                raise AdoptionError(
                    "could not check for a live Antigravity process; use --stopped after verifying"
                ) from exc


@dataclass(frozen=True)
class Adoption:
    repo: Path
    title: str
    target: str
    route: str
    conversation: str
    workspace: Path
    worktree: Path
    create_worktree: bool
    record: Path
    already_adopted: bool
    targets: set[str]


def plan(
    repo: Path,
    title: str,
    *,
    conversation: str | None,
    source: Path | None,
    pid: int | None,
    tmux: str | None,
    paired_host: bool,
    stopped: bool,
) -> Adoption:
    session_naming.validate_session_text(title)
    client = Antigravity()
    selected = _native_id(conversation) if conversation is not None else _discover(client, pid, tmux)
    targets = {f"g-{title}", f"g-r-{title}"}
    if tmux is not None:
        targets.add(tmux)
    with _presence_guard(client, selected, stopped):
        _require_stopped(client, selected, targets, stopped)
    if (
        not (client.conversations_dir() / f"{selected}.db").is_file()
        and not (client.store_root() / "brain" / selected).is_dir()
    ):
        raise AdoptionError(f"no native Antigravity conversation data exists for {selected}")
    worktree, create = _ensure_worktree(repo, title, True, reserve_name=True)
    target = f"g-{'r-' if paired_host else ''}{title}"
    route = "paired-host-tmux" if paired_host else "local-tmux"
    record = config.get_xdg_state_home() / "sessions" / f"{target}.agy.json"
    agy_session._ensure_conversation_unowned(record, selected)
    current = agy_session.bound_slot_conversation(record, worktree, target, client)
    if record.exists() and current != selected:
        raise AdoptionError(f"slot already has an identity record at {record}; use owner-approved slot recovery")
    if source is None:
        if client.probe_conversation(worktree, selected).state == PRESENT:
            source = worktree
        else:
            candidates = {
                Path(item.cwd).resolve()
                for item in client.all_records()
                if item.session_id == selected and item.cwd is not None
            }
            proven = {path for path in candidates if client.probe_conversation(path, selected).state == PRESENT}
            if len(proven) != 1:
                raise AdoptionError(f"found {len(proven)} proven workspaces for {selected}; supply --source")
            source = proven.pop()
    source = source.resolve()
    proof = client.probe_conversation(source, selected)
    if proof.state != PRESENT:
        raise AdoptionError(proof.reason)
    return Adoption(
        repo, title, target, route, selected, source, worktree, create, record, current == selected, targets
    )


def register_name(adoption: Adoption, *, dry_run: bool) -> None:
    parsed = session_naming.validate_artifact_name(
        adoption.title, adoption.repo, allow_legacy=not adoption.create_worktree, reserved_name=True
    )
    if parsed.suffix.isdigit():
        return
    from . import name_commands, session_title
    from .name_sqlite import SqliteRegistry

    registry = name_commands.open_registry(read_only=True)
    if dry_run and isinstance(registry, SqliteRegistry):
        snapshot, _ = name_registry.dry_run_snapshot(registry, registry.inspect())
    else:
        snapshot = registry.snapshot(fetch=not dry_run)
    machine = session_naming.host_id()
    row = snapshot.rows.get(parsed.suffix) or {}
    if row.get("title") == adoption.title and session_naming.machine_identity(machine).holds(row):
        return
    slot = name_registry.Slot(
        repo=adoption.repo.name,
        repo_alias=session_naming.registered_repo_alias(adoption.repo),
        host_alias=session_naming.resolve_host_alias(),
        label=parsed.label,
        machine=machine,
        client="agy",
    )
    if slot.title_for(parsed.suffix) != adoption.title:
        raise AdoptionError("the title does not match this repository and host's naming policy")
    if parsed.suffix in session_title.resolve_reserved_names():
        raise AdoptionError(f"{parsed.suffix} is a reserved name")
    effective_policy = snapshot.policy
    words = name_registry.common_words(effective_policy, read_only=dry_run)
    verdict, reason = name_registry.check_hand_chosen(snapshot, parsed.suffix, slot, words)
    if verdict == "unknown":
        if reason := name_registry.limits_reason(parsed.suffix, effective_policy):
            raise AdoptionError(reason)
    elif verdict not in {"ok", "spare"}:
        raise AdoptionError(reason)
    if not dry_run:
        name_registry.reserve_hand_chosen(
            registry,
            parsed.suffix,
            slot,
            add=True,
            via="adopt",
            added_by=f"adopt@{machine}",
            words=words,
            reserved_names=session_title.resolve_reserved_names(),
        )


def apply(adoption: Adoption, *, stopped: bool, command: str) -> None:
    client = Antigravity()
    with _presence_guard(client, adoption.conversation, stopped):
        _require_stopped(client, adoption.conversation, adoption.targets, stopped)
        if adoption.already_adopted:
            return
        argv = shlex.split(command)
        if not argv:
            raise AdoptionError("configured Antigravity command is empty")
        version = agy_session._detect_version(argv[0])
        register_name(adoption, dry_run=False)
        if adoption.create_worktree:
            worktree, _ = _ensure_worktree(adoption.repo, adoption.title, False)
            if worktree.resolve() != adoption.worktree.resolve():
                raise AdoptionError("the worktree changed after planning")
        register_canonical_worktree(adoption.worktree, engine="g", session_name=adoption.target)
        agy_session.prepare(
            adoption.record,
            adoption.worktree,
            adoption.target,
            fresh=False,
            route=adoption.route,
            client_version=version,
            adopt_conversation=(adoption.conversation, adoption.target),
            adopted_from=adoption.workspace,
        )
