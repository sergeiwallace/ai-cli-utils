"""Machine-local registry of live ``ai`` sessions and the iTerm2 pane each one occupies.

A record is written when ``ai c`` hands a session off (before the local exec into tmux,
before the remote dial) and is removed only when the session it describes is proved
gone. An iTerm2 quit hangs up the ssh client and detaches the tmux client, and neither
is evidence that the session itself ended, so neither removes a record.

Depends on: config.py, iterm2.py
"""

import contextlib
import fnmatch
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from . import iterm2 as _iterm2
from .config import get_remote_machine, get_xdg_state_home

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REGISTRY_FILENAME = "iterm2-sessions.json"
#: Bound on the one AppleScript pass a refresh makes; iTerm2 that does not answer in
#: this long is skipped rather than waited on.
REFRESH_TIMEOUT_SECONDS = 5
#: Bound on one ``--probe-remote`` dial.
PROBE_TIMEOUT_SECONDS = 20

# ``ITERM_SESSION_ID`` is ``w<W>t<T>p<P>:<UUID>``.
_ITERM_SESSION_ID = re.compile(r"^w(\d+)t(\d+)p(\d+):([0-9A-Fa-f-]{8,})$")


class RegistryError(RuntimeError):
    """The registry file exists but cannot be used as a registry."""


def registry_path() -> Path:
    """Return the registry file path (``<XDG state>/iterm2-sessions.json``)."""
    return get_xdg_state_home() / REGISTRY_FILENAME


def parse_iterm_session_id(value: str | None) -> dict | None:
    """Parse ``ITERM_SESSION_ID`` into a position, or return None when absent or malformed."""
    match = _ITERM_SESSION_ID.match(value or "")
    if match is None:
        return None
    window, tab, pane, session_uuid = match.groups()
    return {"session_uuid": session_uuid, "window": int(window), "tab": int(tab), "pane": int(pane)}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_registry() -> dict:
    return {"schema": SCHEMA_VERSION, "machine": socket.gethostname(), "sessions": []}


def load_registry() -> dict:
    """Return the registry document; an absent file reads as an empty registry and is not created."""
    path = registry_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_registry()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{path} is not valid JSON ({exc.msg}); move it aside to start a new registry") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), list):
        raise RegistryError(f"{path} is not a session registry")
    if doc.get("schema") != SCHEMA_VERSION:
        raise RegistryError(f"{path} has schema {doc.get('schema')!r}; this version reads schema {SCHEMA_VERSION}")
    return doc


@contextlib.contextmanager
def _locked() -> Iterator[Path]:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a") as lock_fd:
        portalocker.lock(lock_fd, portalocker.LOCK_EX)
        try:
            yield path
        finally:
            portalocker.unlock(lock_fd)


def _write(path: Path, doc: dict) -> None:
    """Replace the registry atomically; mkstemp creates the temp file owner-only (0600)."""
    doc["machine"] = socket.gethostname()
    fd, temp_name = tempfile.mkstemp(prefix=".iterm2-sessions-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _mutate(change: Callable[[dict], bool]) -> None:
    """Apply ``change`` to the registry under the lock; it returns whether to write."""
    with _locked() as path:
        doc = load_registry()
        if change(doc):
            _write(path, doc)


def _remote_alias(record: dict) -> str | None:
    remote = record.get("remote")
    return remote.get("alias") if isinstance(remote, dict) else None


def _same_session(a: dict, b: dict) -> bool:
    return a.get("kind") == b.get("kind") and a.get("name") == b.get("name") and _remote_alias(a) == _remote_alias(b)


def _not_tracked_because(kind: str, name: str, config: dict) -> str | None:
    tracking = config["tracking"]
    if not config["enabled"]:
        return "[iterm2.persistence] enabled = false"
    if not tracking["enabled"]:
        return "[iterm2.persistence.tracking] enabled = false"
    if kind == "local" and not tracking["include_local"]:
        return "[iterm2.persistence.tracking] include_local = false"
    if kind == "remote" and not tracking["include_remote"]:
        return "[iterm2.persistence.tracking] include_remote = false"
    if tracking["include"] and not any(fnmatch.fnmatchcase(name, glob) for glob in tracking["include"]):
        return f"[iterm2.persistence.tracking] include {tracking['include']!r} has no matching glob"
    for glob in tracking["exclude"]:
        if fnmatch.fnmatchcase(name, glob):
            return f"[iterm2.persistence.tracking] exclude glob {glob!r} matches"
    return None


def record_launch(
    *,
    kind: str,
    name: str,
    relaunch_argv: list[str],
    cwd: str | None,
    remote: dict | None,
    tty: str,
    iterm_session_id: str | None,
    launcher_pid: int,
    config: dict,
) -> dict | None:
    """Write one record for a launch and return it, or return None when config filters it out.

    A later launch of the same session replaces its earlier record, so one session has
    one record however often it is re-attached.
    """
    reason = _not_tracked_because(kind, name, config)
    if reason is not None:
        LOGGER.debug("session %s not recorded in the iTerm2 session registry: %s", name, reason)
        return None
    position = parse_iterm_session_id(iterm_session_id)
    now = _now()
    record = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "name": name,
        "relaunch_argv": list(relaunch_argv),
        "cwd": cwd,
        "remote": remote,
        "iterm2": None if position is None else {**position, "tty": tty or None},
        "launched_at": now,
        "refreshed_at": now,
        "launcher_pid": launcher_pid,
        "ended_at": None,
    }

    def upsert(doc: dict) -> bool:
        doc["sessions"] = [r for r in doc["sessions"] if not _same_session(r, record)] + [record]
        return True

    _mutate(upsert)
    return record


def mark_ended(record_id: str) -> None:
    """Mark a record as cleanly ended so the next prune removes it."""
    if not registry_path().exists():
        return

    def mark(doc: dict) -> bool:
        for record in doc["sessions"]:
            if record.get("id") == record_id:
                record["ended_at"] = _now()
                return True
        return False

    _mutate(mark)


@dataclass(frozen=True)
class TrackOutcome:
    """What recording a launch produced: the record id (None when not recorded) and any warning."""

    record_id: str | None
    warning: str | None = None


def track_launch(*, kind: str, name: str, relaunch_argv: list[str], remote: dict | None = None) -> TrackOutcome:
    """Record the launch running in this process, then refresh other records' positions if configured.

    Raises on any failure; the launcher turns that into one warning and launches anyway.
    """
    config = _iterm2.load_persistence_config()
    tty = _iterm2._current_pane_tty()
    record = record_launch(
        kind=kind,
        name=name,
        relaunch_argv=relaunch_argv,
        cwd=str(Path.cwd()),
        remote=remote,
        tty=tty,
        iterm_session_id=os.environ.get("ITERM_SESSION_ID"),
        launcher_pid=os.getpid(),
        config=config,
    )
    if record is None:
        return TrackOutcome(None)
    # Only from a launch running in an iTerm2 pane: elsewhere there is no pane to map and
    # the AppleScript call would ask for an Automation grant the user never gave.
    if config["tracking"]["refresh_on_launch"] and sys.platform == "darwin" and _iterm2._is_iterm2() and tty:
        # The new record is excluded: its session has no client yet, or (on a re-attach)
        # a client in the pane it is about to leave, so ITERM_SESSION_ID is the truth.
        outcome = refresh(skip_ids={record["id"]})
        return TrackOutcome(record["id"], outcome.skipped)
    return TrackOutcome(record["id"])


def _local_session_alive(name: str) -> bool | None:
    """True/False from ``tmux has-session`` (exact match), None when tmux could not be asked."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", f"={name}"], capture_output=True, check=False, timeout=PROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def _remote_session_alive(record: dict, config: dict) -> bool | None:
    """Ask the remote host's tmux for the session; None when the host could not be asked.

    tmux answers 1 for "no such session" and for "no server running", and either means
    the session is gone. ssh's own failures are 255 and prove nothing about the session.
    """
    remote = record.get("remote") or {}
    try:
        machine = get_remote_machine(config, remote.get("alias") or "")
    except ValueError:
        return None
    host = machine.get("vpn_host") or machine.get("host")
    if not host:
        return None
    ssh_args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(machine.get("port", 22))]
    if machine.get("identity_file"):
        ssh_args += ["-i", str(Path(machine["identity_file"]).expanduser())]
    ssh_args += [
        f"{machine.get('user', 'ubuntu')}@{host}",
        f"tmux has-session -t {shlex.quote('=' + remote['session'])}",
    ]
    try:
        result = subprocess.run(ssh_args, capture_output=True, check=False, timeout=PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _death_reason(record: dict, probe_remote: bool, remote_config: dict) -> str | None:
    if record.get("kind") == "local":
        alive = _local_session_alive(str(record.get("name", "")))
        return "tmux session no longer exists" if alive is False else None
    if record.get("ended_at"):
        return "remote session exited cleanly"
    if probe_remote and isinstance(record.get("remote"), dict) and record["remote"].get("session"):
        alive = _remote_session_alive(record, remote_config)
        return "remote probe found no such tmux session" if alive is False else None
    return None


def prune(
    *, probe_remote: bool = False, remote_config: dict | None = None, dry_run: bool = False
) -> list[tuple[dict, str]]:
    """Remove records whose session is proved gone; return each removed record with its reason.

    Local: ``tmux has-session`` fails (a detached session is alive). Remote: a clean exit
    was recorded, or ``probe_remote`` asked the remote tmux and it has no such session.
    With ``dry_run`` the same verdicts are returned and the registry is not written.
    """
    if not registry_path().exists():
        return []
    snapshot = load_registry()["sessions"]
    verdicts = {}
    for record in snapshot:
        reason = _death_reason(record, probe_remote, remote_config or {})
        if reason is not None:
            verdicts[record.get("id")] = reason
    removed: list[tuple[dict, str]] = []
    if not verdicts or dry_run:
        return [(record, verdicts[record.get("id")]) for record in snapshot if record.get("id") in verdicts]

    def drop(doc: dict) -> bool:
        kept = []
        for record in doc["sessions"]:
            if record.get("id") in verdicts:
                removed.append((record, verdicts[record.get("id")]))
            else:
                kept.append(record)
        doc["sessions"] = kept
        return bool(removed)

    _mutate(drop)
    return removed


def _launcher_tty(record: dict) -> str:
    """The controlling tty of a still-running launcher (the live ssh client's pane), or ""."""
    import psutil

    pid = record.get("launcher_pid")
    if not isinstance(pid, int):
        return ""
    try:
        proc = psutil.Process(pid)
        launched = datetime.strptime(str(record.get("launched_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        # A process younger than the launch is an unrelated one that reused the pid.
        if proc.create_time() > launched.timestamp() + 1:
            return ""
        return proc.terminal() or ""
    except (psutil.Error, AttributeError, ValueError):
        return ""


def _current_tty(record: dict) -> str:
    if record.get("kind") == "local":
        try:
            return _iterm2._iterm_pane_tty_for_tmux_session(str(record.get("name", "")))
        except OSError:
            return ""
    return _launcher_tty(record)


@dataclass(frozen=True)
class RefreshOutcome:
    """Result of one position refresh: how many records moved, or why the pass was skipped."""

    updated: int
    total: int
    skipped: str | None = None

    def summary(self) -> str:
        return self.skipped or f"refreshed {self.updated} of {self.total} recorded position(s)"


def refresh(skip_ids: Iterable[str] = ()) -> RefreshOutcome:
    """Update each record's window/tab/pane from the pane its session occupies now.

    One AppleScript pass maps every iTerm2 pane's tty to its position. A record whose
    current tty is not found keeps its last known position. When iTerm2 cannot be asked
    the registry is left exactly as it was.
    """
    if not registry_path().exists():
        return RefreshOutcome(0, 0)
    skip = set(skip_ids)
    records = [r for r in load_registry()["sessions"] if r.get("id") not in skip]
    if not records:
        return RefreshOutcome(0, 0)
    if sys.platform != "darwin":
        return RefreshOutcome(0, len(records), "refresh skipped (iTerm2 is macOS-only)")
    current = {r["id"]: tty for r in records if (tty := _current_tty(r))}
    try:
        panes = _iterm2._iterm2_panes_by_tty(REFRESH_TIMEOUT_SECONDS)
    except _iterm2.Iterm2PaneLookupError as exc:
        return RefreshOutcome(0, len(records), f"refresh skipped ({exc})")
    positions = {record_id: {**panes[tty], "tty": tty} for record_id, tty in current.items() if tty in panes}
    if not positions:
        return RefreshOutcome(0, len(records))
    now = _now()
    updated: list[str] = []

    def apply(doc: dict) -> bool:
        for record in doc["sessions"]:
            position = positions.get(record.get("id"))
            if position is not None:
                record["iterm2"] = position
                record["refreshed_at"] = now
                updated.append(record["id"])
        return bool(updated)

    _mutate(apply)
    return RefreshOutcome(len(updated), len(records))
