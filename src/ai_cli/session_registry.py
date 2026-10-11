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

from . import config as _config
from . import iterm2 as _iterm2
from . import session as _session
from .config import get_remote_machine, get_xdg_state_home

LOGGER = logging.getLogger(__name__)

#: Schema 2 added each record's ``exit`` and ``boot_id``. A schema-1 file is read with both
#: null and is written back as schema 2 by the next change, never by a read alone.
SCHEMA_VERSION = 2
_READABLE_SCHEMAS = (1, 2)
REGISTRY_FILENAME = "iterm2-sessions.json"
#: Why a session's previous run ended.
EXIT_CAUSES = ("manual_exit", "terminal_quit_or_crash", "host_reboot", "unknown")
LINUX_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
#: Bound on the one read-only ``sysctl`` call that reads the macOS boot identity.
BOOT_ID_TIMEOUT_SECONDS = 5
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
    if doc.get("schema") not in _READABLE_SCHEMAS:
        readable = " or ".join(str(s) for s in _READABLE_SCHEMAS)
        raise RegistryError(f"{path} has schema {doc.get('schema')!r}; this version reads schema {readable}")
    doc["schema"] = SCHEMA_VERSION
    for record in doc["sessions"]:
        if isinstance(record, dict):
            record.setdefault("exit", None)
    return doc


def current_boot_id() -> str | None:
    """This host's boot identity, or None when it cannot be read (and on Windows).

    Linux: the kernel's per-boot ``boot_id``. macOS: ``kern.bootsessionuuid``, read with
    one read-only ``sysctl`` call that only ever runs on macOS.
    """
    if sys.platform.startswith("linux"):
        try:
            return LINUX_BOOT_ID_PATH.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                capture_output=True,
                text=True,
                check=False,
                timeout=BOOT_ID_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None
    return None


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


def _new_record(
    *,
    kind: str,
    name: str,
    relaunch_argv: list[str],
    cwd: str | None,
    remote: dict | None,
    iterm2: dict | None,
    launcher_pid: int | None,
) -> dict:
    now = _now()
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "name": name,
        "relaunch_argv": list(relaunch_argv),
        "cwd": cwd,
        "remote": remote,
        "iterm2": iterm2,
        "launched_at": now,
        "refreshed_at": now,
        "launcher_pid": launcher_pid,
        "ended_at": None,
        "exit": None,
        "boot_id": current_boot_id(),
    }


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
    record = _new_record(
        kind=kind,
        name=name,
        relaunch_argv=relaunch_argv,
        cwd=cwd,
        remote=remote,
        iterm2=None if position is None else {**position, "tty": tty or None},
        launcher_pid=launcher_pid,
    )

    def upsert(doc: dict) -> bool:
        doc["sessions"] = [r for r in doc["sessions"] if not _same_session(r, record)] + [record]
        return True

    _mutate(upsert)
    return record


def mark_ended(record_id: str) -> None:
    """Mark a record as cleanly ended (a manual exit) so the next prune removes it."""
    if not registry_path().exists():
        return

    def mark(doc: dict) -> bool:
        for record in doc["sessions"]:
            if record.get("id") == record_id:
                now = _now()
                record["ended_at"] = now
                if record.get("exit") is None:
                    record["exit"] = {"cause": "manual_exit", "at": now, "evidence": {"source": "on_clean_exit"}}
                return True
        return False

    _mutate(mark)


def record_exit(name_or_id: str, *, cause: str, evidence: dict, at: str | None = None) -> list[dict]:
    """Set the ``exit`` of every record whose id or name is ``name_or_id``; return those records.

    Also sets ``ended_at`` when it is unset. An empty list means nothing matched and
    nothing was written.
    """
    if cause not in EXIT_CAUSES:
        raise ValueError(f"cause must be one of {', '.join(EXIT_CAUSES)}, got {cause!r}")
    if not registry_path().exists():
        return []
    stamp = at or _now()
    marked: list[dict] = []

    def mark(doc: dict) -> bool:
        for record in doc["sessions"]:
            if name_or_id in (record.get("id"), record.get("name")):
                record["exit"] = {"cause": cause, "at": stamp, "evidence": dict(evidence)}
                if not record.get("ended_at"):
                    record["ended_at"] = stamp
                marked.append(record)
        return bool(marked)

    _mutate(mark)
    return marked


def remove(record_ids: Iterable[str]) -> None:
    """Remove the records with these ids (ones a caller has already proved gone)."""
    ids = set(record_ids)
    if not ids or not registry_path().exists():
        return

    def drop(doc: dict) -> bool:
        kept = [r for r in doc["sessions"] if r.get("id") not in ids]
        changed = len(kept) != len(doc["sessions"])
        doc["sessions"] = kept
        return changed

    _mutate(drop)


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


# --- Adopting sessions that predate the registry (D-10) ------------------------------

#: Bound on the one ``tmux list-sessions`` call adoption makes.
ADOPT_TMUX_TIMEOUT_SECONDS = 5
# A short-flag cluster carrying -R (``-R``, ``-Rr``); ``--remote`` is matched exactly.
_REMOTE_SHORT_FLAG = re.compile(r"^-[A-Za-z]*R[A-Za-z]*$")


class AdoptRefused(RuntimeError):
    """Configuration turns tracking off, so nothing may be adopted; the message names the key."""


@dataclass
class AdoptLine:
    """One candidate session and what adoption did (or, in a dry run, would do) with it."""

    verdict: str  # "adopted" | "already recorded" | "skipped"
    label: str
    reason: str = ""
    record: dict | None = None
    note: str = ""
    tty: str = ""
    started: float | None = None  # the launcher process's start time (remote)


@dataclass(frozen=True)
class AdoptOutcome:
    lines: list[AdoptLine]
    notes: list[str]

    def count(self, verdict: str) -> int:
        return sum(1 for line in self.lines if line.verdict == verdict)


def _is_slot(value: str) -> bool:
    """A slot ``build_session_name`` maps back to one existing session: ``N`` or ``<name>-N``."""
    return bool(value) and (value.isdigit() or value.rsplit("-", 1)[-1].isdigit())


def _local_candidate(name: str, projects: dict[str, str]) -> AdoptLine:
    """Derive the ``ai c -p <project> <slot>`` that re-attaches tmux session ``name``.

    The prefix and the slot are split against the registered prefixes, never by
    position: prefixes and slot names both contain hyphens, so only a registry match
    tells them apart, and anything but exactly one match is skipped.
    """
    if name.casefold().startswith("c-r-"):
        return AdoptLine(
            "skipped", name, "c-r- names a session another machine launched here; that machine re-attaches it"
        )
    body = name[2:]
    matches = [
        (prefix, project, body[len(prefix) + 1 :])
        for prefix, project in sorted(projects.items())
        if body.casefold().startswith(f"{prefix}-") and _is_slot(body[len(prefix) + 1 :])
    ]
    if not matches:
        return AdoptLine("skipped", name, "no registered project prefix splits it into c-<prefix>-<n>")
    if len(matches) > 1:
        return AdoptLine("skipped", name, f"ambiguous: prefixes {', '.join(m[0] for m in matches)} all match")
    prefix, project, slot = matches[0]
    # The relaunch resolves -p exactly this way; proving it lands on the same prefix is
    # what makes `ai c -p <project> <slot>` re-attach this session rather than mint one.
    try:
        resolved = _config.resolve_project_prefix_by_name(_config.get_project_aliases().get(project, project))
    except _config.ProjectPrefixError as exc:
        return AdoptLine("skipped", name, f"-p {project} does not resolve ({exc})")
    if resolved.casefold() != prefix:
        return AdoptLine("skipped", name, f"-p {project} resolves to prefix {resolved!r}, not {prefix!r}")
    record = _new_record(
        kind="local",
        name=name,
        relaunch_argv=["ai", "c", "-p", project, slot],
        cwd=None,
        remote=None,
        iterm2=None,
        launcher_pid=None,
    )
    return AdoptLine("adopted", name, record=record)


def _local_candidates() -> tuple[list[AdoptLine], list[str]]:
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=ADOPT_TMUX_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], [f"local sessions not scanned (tmux could not be run: {exc})"]
    # tmux exits 1 with "no server running" when there are no sessions at all.
    listed = (result.stdout or "").splitlines() if result.returncode == 0 else []
    names = [n for n in listed if n.startswith("c-")]
    if not names:
        return [], []
    try:
        projects = _config.registered_project_names()
    except _config.ProjectPrefixError as exc:
        return [AdoptLine("skipped", n, f"project registry unreadable ({exc})") for n in names], []
    return [_local_candidate(n, projects) for n in names], []


def _ai_args(cmdline: list[str] | None) -> list[str] | None:
    """The arguments after ``ai`` when ``cmdline`` runs the ``ai`` entry point, else None."""
    if not cmdline:
        return None
    if Path(cmdline[0]).stem.lower() == "ai":
        return list(cmdline[1:])
    if len(cmdline) > 1 and Path(cmdline[0]).stem.lower().startswith("python") and Path(cmdline[1]).stem == "ai":
        return list(cmdline[2:])
    return None


def _is_remote_launch(args: list[str]) -> bool:
    return bool(args) and args[0] == "c" and any(a == "--remote" or _REMOTE_SHORT_FLAG.match(a) for a in args[1:])


def _remote_session(project: str, slot: str) -> tuple[str | None, str]:
    """The remote tmux name the launcher derives for an explicit slot, or (None, why not)."""
    if not project:
        return None, "no -p in the argv, so the remote prefix came from the launcher's directory"
    if not slot.isdigit():
        return None, "no numeric slot in the argv, so the remote host allocated it"
    try:
        prefix = _config.resolve_project_prefix_by_name(_config.get_project_aliases().get(project, project))
    except _config.ProjectPrefixError as exc:
        return None, f"-p {project} does not resolve ({exc})"
    return _session._new_session_display_name("c", prefix, slot, True), ""


def _remote_candidate(
    proc, args: list[str], parse_launch: Callable[[list[str]], dict], remote_config: dict
) -> AdoptLine:
    pid = proc.info["pid"]
    label = f"pid {pid}"
    try:
        params = parse_launch(args[1:])
    except ValueError as exc:
        return AdoptLine("skipped", label, f"argv does not parse as ai c -R ({exc})")
    if not params.get("remote"):
        return AdoptLine("skipped", label, "argv does not parse as an ai c -R invocation")
    extra = params.get("extra_args") or []
    slot = str(params.get("name") or (extra[0] if extra else ""))
    try:
        alias = _config.resolve_remote_machine_alias(remote_config, str(params.get("remote_machine") or ""))
    except _config.RemoteMachineError as exc:
        return AdoptLine("skipped", label, f"remote machine does not resolve ({exc})")
    session, why = _remote_session(str(params.get("project") or ""), slot)
    import psutil

    relaunch_argv = ["ai", *args]
    try:
        cwd = proc.cwd()
    except (psutil.Error, OSError):
        cwd = None
    record = _new_record(
        kind="remote",
        name=session or shlex.join(relaunch_argv),
        relaunch_argv=relaunch_argv,
        cwd=cwd,
        remote={"alias": alias, "session": session},
        iterm2=None,
        launcher_pid=pid,
    )
    note = "" if session else f"remote session unknown: {why}"
    return AdoptLine(
        "adopted",
        session or label,
        record=record,
        note=note,
        tty=proc.info.get("terminal") or "",
        started=proc.info.get("create_time"),
    )


def _remote_candidates(parse_launch: Callable[[list[str]], dict], remote_config: dict) -> list[AdoptLine]:
    """One line per live ``ai c -R`` launcher (never its ssh child), from one process pass."""
    import psutil

    me = os.getpid()
    try:
        user = psutil.Process(me).username()
    except psutil.Error:
        user = None
    lines: list[AdoptLine] = []
    procs = psutil.process_iter(["pid", "name", "cmdline", "terminal", "username", "create_time"])
    for proc in sorted(procs, key=lambda p: p.info["pid"]):
        info = proc.info
        if info["pid"] == me:
            continue
        cmdline = info.get("cmdline")
        if cmdline is None:
            # Unreadable argv. Reported only where it could be a launcher (this user's
            # python or ai process on a terminal), and never guessed at.
            name = str(info.get("name") or "").lower()
            if info.get("terminal") and info.get("username") == user and ("python" in name or name == "ai"):
                lines.append(AdoptLine("skipped", f"pid {info['pid']}", "argv cannot be read"))
            continue
        args = _ai_args(cmdline)
        if args is not None and _is_remote_launch(args):
            lines.append(_remote_candidate(proc, args, parse_launch, remote_config))
    return lines


def _already_recorded(existing: dict, line: AdoptLine) -> bool:
    """Same kind and name, or (remote) the same launcher process a launch already recorded.

    A pid match counts only when the record was written after the process started, so a
    record left by an earlier process that held the same pid is not mistaken for it.
    """
    record = line.record or {}
    if _same_session(existing, record):
        return True
    if record.get("kind") != "remote" or existing.get("kind") != "remote" or line.started is None:
        return False
    if existing.get("launcher_pid") != record.get("launcher_pid"):
        return False
    try:
        written = datetime.strptime(str(existing.get("launched_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return written.timestamp() + 1 >= line.started


def _fill_positions(lines: list[AdoptLine]) -> list[str]:
    """Set each new record's iTerm2 position from its tty, in one AppleScript pass (macOS)."""
    for line in lines:
        if line.record is not None and line.record["kind"] == "local":
            try:
                line.tty = _iterm2._iterm_pane_tty_for_tmux_session(line.record["name"])
            except OSError:
                line.tty = ""
    wanted = [line for line in lines if line.record is not None and line.tty]
    if not wanted or sys.platform != "darwin":
        return []
    try:
        panes = _iterm2._iterm2_panes_by_tty(REFRESH_TIMEOUT_SECONDS)
    except _iterm2.Iterm2PaneLookupError as exc:
        return [f"positions not read ({exc}); run `ai iterm2 sessions --refresh` once iTerm2 answers"]
    for line in wanted:
        if line.record is not None and line.tty in panes:
            line.record["iterm2"] = {**panes[line.tty], "tty": line.tty}
    return []


def adopt(*, parse_launch: Callable[[list[str]], dict], remote_config: dict, dry_run: bool = False) -> AdoptOutcome:
    """Record the live ``ai c`` sessions that have no record yet (they predate the registry).

    Local: every tmux session named ``c-<prefix>-<n>``. Remote: every live process whose
    argv is an ``ai c -R`` launch, read with ``parse_launch`` (the ``ai c`` option
    parser). A session already recorded is left unchanged; a candidate whose relaunch
    command cannot be derived is skipped with the reason. With ``dry_run`` the same
    verdicts are returned and nothing is written or created.
    """
    config = _iterm2.load_persistence_config()
    if not config["enabled"]:
        raise AdoptRefused("[iterm2.persistence] enabled = false")
    if not config["tracking"]["enabled"]:
        raise AdoptRefused("[iterm2.persistence.tracking] enabled = false")
    lines, notes = _local_candidates()
    lines += _remote_candidates(parse_launch, remote_config)
    for line in lines:
        if line.record is not None and (rule := _not_tracked_because(line.record["kind"], line.record["name"], config)):
            line.verdict, line.reason, line.record = "skipped", rule, None
    notes += _fill_positions(lines)

    def classify(doc: dict) -> bool:
        added = False
        for line in lines:
            if line.record is None:
                continue
            if any(_already_recorded(existing, line) for existing in doc["sessions"]):
                line.verdict = "already recorded"
            else:
                doc["sessions"].append(line.record)
                added = True
        return added

    if dry_run:
        classify(load_registry())
    else:
        _mutate(classify)
    return AdoptOutcome(lines, notes)
