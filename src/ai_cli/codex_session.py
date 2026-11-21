"""Codex session identity: a confirmed thread per numbered slot, and its name.

Depends on: background_attach.py (the tmux paste used for a waiting resume prompt),
codex_control.py (the live slot attestation bridge), session_clients.py.

A numbered interactive slot is bound to the native thread its own ``SessionStart`` hook
confirms (:func:`prepare`, :func:`confirm`). Only identity metadata is retained. Process
identity guards launch collisions, not authorization; attaching to a live pane stays
independent.

Codex has no launch-time option or config key that names a thread. A name is set by
``/rename``, which appends ``{"id", "thread_name", "updated_at"}`` to
``$CODEX_HOME/session_index.jsonl``; the newest non-empty entry for an id is its name.
Codex's own picker and its ``codex resume <name>`` lookup skip a thread that has had no
turn, while ``codex resume <id>`` opens it. So the launcher resolves the name to an id
itself (:func:`find_thread`), and types ``/rename <name>`` only when no thread carries the
name yet (:func:`name_session`). A slot with no record adopts the thread that carries its
session's name, once the native store proves that thread is this directory's.

Design: docs/designs/codex-session-naming.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from . import output
from .output import Untagged
from .process_probe import StartTimeMatch, normalize_proc_start, probe_for
from .session_clients import PRESENT, Codex

SCHEMA_VERSION = 2
SUPPORTED_VERSIONS = {"0.169.0", "0.171.0"}
HOOK_COMMAND = "ai internal codex-session-confirm"
HOOK_DECLARATION = 'hooks.SessionStart=[{hooks=[{type="command",command=' + json.dumps(HOOK_COMMAND) + "}]}]"
SLOT_RECORD_ENV = "AI_CLI_CODEX_SLOT_RECORD"
LAUNCH_TOKEN_ENV = "AI_CLI_CODEX_LAUNCH_TOKEN"
#: The session's name, which the session script exports to every agent it runs.
SESSION_NAME_ENV = "AI_SESSION_NAME"
#: A file holding a relaunch's resume prompt, for the launch to hand to Codex itself.
PROMPT_FILE_ENV = "AI_CLI_CODEX_PROMPT_FILE"


def hook_contract() -> dict:
    """Describe the pinned launch boundary; this does not grant hook trust."""
    # Codex 0.169.0 normalizes omitted timeout/async to 600/false and omits
    # absent optional fields before hashing recursively sorted compact JSON.
    group = tomllib.loads(HOOK_DECLARATION)["hooks"]["SessionStart"][0]
    handler = {**group["hooks"][0], "timeout": 600, "async": False}
    identity = {"event_name": "session_start", "hooks": [handler]}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    key = "/<session-flags>/config.toml:session_start:0:0"
    return {
        "schema_version": 1,
        "supported_client_versions": sorted(SUPPORTED_VERSIONS),
        "event": "SessionStart",
        "command": HOOK_COMMAND,
        "declaration": HOOK_DECLARATION,
        "position": {"group": 0, "handler": 0},
        "keys": {"linux": key, "macos": key},
        "expected_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


class IdentityError(ValueError):
    """The slot cannot safely identify a resumable native thread."""


def _thread_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is not None


def _read(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise IdentityError("Codex slot schema is incompatible; identity is unknown")
    if not {"phase", "thread_id", "rollout_path", "result", "resumable", "in_flight"} <= doc.keys():
        raise IdentityError("Codex slot record is incomplete; identity is unknown")
    launch = doc.get("in_flight")
    if (
        not isinstance(doc.get("slot"), str)
        or not isinstance(doc.get("cwd"), str)
        or not Path(doc["cwd"]).is_absolute()
        or doc.get("phase") not in ("confirmed", "unconfirmed")
        or not isinstance(launch, dict)
        or not isinstance(launch.get("token"), str)
        or not launch["token"]
        or not isinstance(launch.get("launched_at"), str)
        or not isinstance(launch.get("client_version"), str)
        or not isinstance(launch.get("route"), str)
        or not isinstance(doc.get("client_version"), str)
        or doc.get("execution_mode") != "interactive"
        or not isinstance(doc.get("route"), str)
    ):
        raise IdentityError("Codex slot record is malformed; identity is unknown")
    try:
        launched_at = datetime.fromisoformat(launch["launched_at"])
    except ValueError:
        raise IdentityError("Codex launch time is malformed") from None
    if launched_at.tzinfo is None:
        raise IdentityError("Codex launch time has no timezone")
    if doc["phase"] == "confirmed":
        if not _thread_id(doc.get("thread_id")) or doc.get("result") not in ("confirmed", "live-only"):
            raise IdentityError("Codex confirmed identity is malformed")
        if (doc["result"] == "confirmed" and not _rollout(doc.get("rollout_path"))) or (
            doc["result"] == "live-only" and doc.get("rollout_path") is not None
        ):
            raise IdentityError("Codex confirmed rollout identity is malformed")
    elif doc.get("thread_id") is not None or doc.get("rollout_path") is not None or doc.get("result") != "unconfirmed":
        raise IdentityError("Codex unconfirmed identity is malformed")
    saved = doc.get("resumable")
    if saved is not None and (
        not isinstance(saved, dict) or not _thread_id(saved.get("thread_id")) or not _rollout(saved.get("rollout_path"))
    ):
        raise IdentityError("Codex saved resumable identity is malformed")
    if (doc["phase"] == "unconfirmed" and saved is not None) or (
        doc["result"] == "confirmed" and saved != {"thread_id": doc["thread_id"], "rollout_path": doc["rollout_path"]}
    ):
        raise IdentityError("Codex committed identity and resumable fallback are inconsistent")
    _recorded_writer(launch)
    return doc


def _rollout(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


def _writer() -> dict:
    probe = probe_for()
    pid = os.getpid()
    identity = probe.capture_identity(pid)
    if identity is None:
        raise IdentityError("Codex writer identity unavailable on this platform")
    return {"pid": pid, "backend": identity.backend, "start_time": identity.marker}


def _recorded_writer(launch: dict) -> tuple[dict, int, int | float]:
    """Validate stored identity without interpreting unreadable values as death."""
    writer = launch.get("writer")
    if not isinstance(writer, dict):
        raise IdentityError("Codex writer identity missing; launch refused")
    pid, start = writer.get("pid"), normalize_proc_start(writer.get("start_time"))
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or not 0 < pid <= 0xFFFFFFFF
        or not isinstance(start, (int, float))
        or isinstance(start, bool)
        or not 0 < start < 2**64
        or not math.isfinite(start)
        or writer.get("backend") not in ("procfs", "psutil")
    ):
        raise IdentityError("Codex writer identity malformed; launch refused")
    return writer, pid, start


def _check_writer(launch: dict) -> None:
    """Return only on proven death/reuse; unreadable evidence never proves death."""
    writer, pid, start = _recorded_writer(launch)
    probe = probe_for()
    if not probe.is_present(pid):
        return
    current = probe.capture_identity(pid)
    if current is None or current.backend != writer["backend"]:
        raise IdentityError("Codex writer identity unreadable or backend changed; launch refused")
    match = probe.start_time_match(pid, start)
    if match is StartTimeMatch.MATCH:
        raise IdentityError("Codex writer is live; slot collision refused")
    if match is not StartTimeMatch.UNPROVEN or current.marker == start:
        raise IdentityError("Codex writer identity cannot be compared; launch refused")
    # capture_identity proves a different birth marker, rather than interpreting
    # UNPROVEN alone (which can also mean access denied) as pid reuse.


def _write(path: Path, doc: dict) -> None:
    staged = path.with_suffix(".pending")
    staged.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def _client() -> Codex:
    # CODEX_HOME relocates the native store, not the launcher's slot records.
    root = os.environ.get("CODEX_HOME")
    if not root:
        return Codex()

    class LocatedCodex(Codex):
        def store_root(self) -> Path:
            return Path(root).expanduser() / "sessions"

    return LocatedCodex()


def prepare(
    path: Path, cwd: Path, slot: str, *, fresh: bool, route: str, client_version: str, name: str | None = None
) -> tuple[str, str, str, str]:
    """Reserve this process's launch while preserving committed identity.

    A slot with no record resumes the thread that carries the session's ``name`` (a
    session named before slots were recorded, or by hand with ``/rename``), but only when
    the native store proves that thread is this directory's. Its own ``SessionStart``
    then confirms it like any other launch.
    """
    path = path.resolve()
    if "\n" in str(path) or "\r" in str(path):
        raise IdentityError("Codex slot path cannot be represented in the launcher reply")
    if client_version not in SUPPORTED_VERSIONS:
        raise IdentityError("Codex client version is unsupported; identity result is unknown")
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path) if path.exists() else None
        target = "new"
        decision = None
        if doc is not None:
            if doc.get("slot") != slot or doc.get("cwd") != str(cwd.resolve()):
                raise IdentityError("Codex slot identity does not match this launch")
            _check_writer(doc["in_flight"])
            if doc["phase"] == "confirmed":
                probe = _client().probe_thread(cwd, doc["thread_id"])
                if probe.state == PRESENT:
                    if doc["result"] == "live-only":
                        doc.update(rollout_path=probe.reason, result="confirmed")
                        doc["resumable"] = {"thread_id": doc["thread_id"], "rollout_path": probe.reason}
                    target = doc["thread_id"]
                elif doc["result"] == "confirmed":
                    raise IdentityError(probe.reason)
                elif doc["resumable"] is not None:
                    saved = doc["resumable"]
                    proof = _client().probe_thread(cwd, saved["thread_id"])
                    if proof.state != PRESENT:
                        raise IdentityError(proof.reason)
                    target = saved["thread_id"]
            decision = "resume-confirmed" if target != "new" else "abandoned-unconfirmed; start-fresh"
        else:
            named = find_thread(name) if name else None
            if named is not None and _client().probe_thread(cwd, named).state == PRESENT:
                target = named
            elif not fresh or _client().records_for(cwd):
                raise IdentityError("Codex slot has no client-confirmed thread; refusing directory-based adoption")
            doc = {
                "schema_version": SCHEMA_VERSION,
                "slot": slot,
                "cwd": str(cwd.resolve()),
                "phase": "unconfirmed",
                "thread_id": None,
                "rollout_path": None,
                "result": "unconfirmed",
                "resumable": None,
                "client_version": client_version,
                "execution_mode": "interactive",
                "route": route,
            }
        doc["in_flight"] = {
            "token": secrets.token_hex(16),
            "writer": _writer(),
            "launched_at": datetime.now(UTC).isoformat(),
            "client_version": client_version,
            "route": route,
        }
        _write(path, doc)
        if decision:
            output.warning(output.Tag.SESSION, "session", f"Codex slot {slot}: abandoned launch; {decision}.")
        return target, HOOK_DECLARATION, str(path), doc["in_flight"]["token"]


def confirm(path: Path, token: str, event: object) -> None:
    """Bind a native SessionStart to the current launch, retaining resume proof."""
    if not token or not path.is_file():
        raise IdentityError("missing Codex launch confirmation identity")
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path)
        if doc["in_flight"]["token"] != token:
            raise IdentityError("stale Codex launch confirmation refused")
        if not isinstance(event, dict) or event.get("hook_event_name") != "SessionStart":
            raise IdentityError("unsupported Codex confirmation event")
        thread = event.get("session_id")
        cwd = event.get("cwd")
        rollout = event.get("transcript_path")
        if (
            not _thread_id(thread)
            or not isinstance(cwd, str)
            or Path(cwd).resolve() != Path(doc["cwd"])
            or not (rollout is None or _rollout(rollout))
            or "transcript_path" not in event
        ):
            raise IdentityError("Codex confirmation identity is incompatible with the launch")
        if doc["in_flight"]["client_version"] not in SUPPORTED_VERSIONS:
            raise IdentityError("unsupported Codex client confirmation")
        if rollout:
            doc["resumable"] = {"thread_id": thread, "rollout_path": rollout}
        doc.update(
            phase="confirmed",
            thread_id=thread,
            rollout_path=rollout,
            result="confirmed" if rollout else "live-only",
            client_version=doc["in_flight"]["client_version"],
            route=doc["in_flight"]["route"],
        )
        _write(path, doc)

    from .codex_control import notify

    notify(path, token)


def confirm_from_environment(args: list[str]) -> int:
    """Read hook identity privately; report refusals without exception contents."""
    try:
        record = os.environ.get(SLOT_RECORD_ENV)
        token = os.environ.get(LAUNCH_TOKEN_ENV)
        if args or not record or not token:
            raise IdentityError("missing Codex launch environment")
        confirm(Path(record), token, json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, portalocker.LockException):
        output.error(output.Tag.SESSION, "session", "Codex identity unknown: launch confirmation refused.")
        return 1
    return 0


INDEX_FILE = "session_index.jsonl"
#: The idle footer Codex draws once its composer takes input. The ``›`` prompt line is
#: drawn earlier, while Codex is still loading, and keys typed then are lost or queued.
READY_MARKER = "? for shortcuts"
READY_WAIT_SECONDS = 30.0
#: A ``/rename`` submitted while Codex is still starting is queued until startup ends, and a
#: first start in a new Codex home installs its app-server first (measured: the name landed
#: 28 s after launch). The namer is detached, so a long wait costs nothing.
CONFIRM_WAIT_SECONDS = 120.0
POLL_SECONDS = 0.5
#: A pause between the typed command and its Enter, so a TUI that treats a fast burst
#: of keys as a paste does not take the Enter as a newline inside it.
SUBMIT_SETTLE_SECONDS = 0.5
#: The bare pty path has no screen to read, so it waits this long before typing.
FIFO_START_SECONDS = 5.0
#: Where :func:`bind_pane` records each thread's pane, under ``$CODEX_HOME``.
BINDINGS_DIR = "pane-bindings"
#: A new session is named only after Codex starts and records the ``/rename``.
BIND_WAIT_SECONDS = READY_WAIT_SECONDS + CONFIRM_WAIT_SECONDS
_TMUX_TIMEOUT_SECONDS = 5.0
_HELP_TIMEOUT_SECONDS = 10.0


def exclusive_process_flags(codex: str = "codex", cache_dir: Path | None = None) -> list[str]:
    """``["--no-daemon"]`` when the installed Codex offers it, else nothing.

    By default Codex 0.169 starts one shared app-server daemon that every TUI on the host
    connects to, so tool processes would descend from that shared server rather than from
    the session's own Codex. ``--no-daemon`` keeps each session in its own process. An
    older Codex without the option would refuse it, so it is passed only when ``codex
    --help`` lists it. The answer is cached per binary (resolved path, size, mtime), so the
    help runs once per Codex install, and the probe is bounded so a Codex that does not
    answer ``--help`` cannot stall a launch.
    """
    path = shutil.which(codex)
    if path is None:
        return []
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
    except OSError:
        return []
    key = f"{resolved}\t{stat.st_size}\t{stat.st_mtime_ns}"
    cache = (cache_dir or _flags_cache_dir()) / "codex-exclusive-flags"
    try:
        cached_key, _, cached_flags = cache.read_text(encoding="utf-8").partition("\n")
        if cached_key == key:
            return cached_flags.split()
    except OSError:
        pass
    try:
        shown = subprocess.run(
            [path, "--help"], capture_output=True, text=True, timeout=_HELP_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    flags = ["--no-daemon"] if "--no-daemon" in shown.stdout else []
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(f"{key}\n{' '.join(flags)}", encoding="utf-8")
    except OSError:
        pass
    return flags


def _flags_cache_dir() -> Path:
    from .config import get_xdg_state_home

    return get_xdg_state_home()


def codex_home() -> Path:
    """Codex's state directory: ``$CODEX_HOME``, else ``~/.codex`` on every OS."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _index_entries(home: Path) -> list[dict]:
    try:
        text = (home / INDEX_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("thread_name"), str):
            entries.append(entry)
    return entries


def _has_rollout(home: Path, thread_id: str) -> bool:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return False
    return any(sessions.rglob(f"rollout-*{thread_id}.jsonl")) or any(sessions.rglob(f"rollout-*{thread_id}.jsonl.zst"))


def find_thread(name: str, home: Path | None = None, *, resumable: bool = True) -> str | None:
    """The id of the newest resumable Codex thread whose current name is ``name``.

    A thread counts only while its newest non-empty index entry still says ``name`` (a
    later ``/rename`` moves it away) and, when ``resumable``, its rollout is still under
    ``sessions/`` (an archived or deleted thread cannot be resumed there). A running
    thread is named before Codex saves its rollout, so :func:`bind_pane` passes
    ``resumable=False``.
    """
    name = name.strip()
    if not name:
        return None
    home = home or codex_home()
    seen: set[str] = set()
    for entry in reversed(_index_entries(home)):
        thread_id, thread_name = entry["id"], entry["thread_name"].strip()
        if not thread_name or thread_id in seen:
            continue
        seen.add(thread_id)
        if thread_name == name and (not resumable or _has_rollout(home, thread_id)):
            return thread_id
    return None


def bind_pane(
    name: str,
    pane: str,
    *,
    home: Path | None = None,
    wait_seconds: float | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], float] | None = None,
) -> str | None:
    """Record which tmux pane runs the Codex thread named ``name``.

    A tool process inside Codex sees its thread id (``CODEX_THREAD_ID``) but inherits
    ``TMUX_PANE`` from whatever first started Codex's long-lived app-server, which can name
    a pane that has since closed. The launcher owns the pane, so it writes
    ``$CODEX_HOME/pane-bindings/<thread id>.json`` holding ``thread_id``, ``pane_id``,
    ``tmux_session`` and ``written_at``, for such a tool to find its own pane. It runs on
    every launch and relaunch in the pane, so the record follows the live pane. A new
    session has no thread id until its name is recorded, so this waits for the name.
    Returns None once written, else the reason it was not.
    """
    if not pane.startswith("%"):
        return "no tmux pane to bind"
    home = home or codex_home()
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    wait_seconds = BIND_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = monotonic() + wait_seconds
    while (thread_id := find_thread(name, home, resumable=False)) is None:
        if monotonic() >= deadline:
            return f"no Codex thread carried the name within {wait_seconds:.0f}s"
        sleep(POLL_SECONDS)
    shown = _tmux("display-message", "-p", "-t", pane, "#{session_name}")
    session = shown.stdout.strip() if shown is not None and shown.returncode == 0 else ""
    if not session:
        return f"tmux does not report pane {pane}"
    record = {"thread_id": thread_id, "pane_id": pane, "tmux_session": session, "written_at": (now or time.time)()}
    directory = home / BINDINGS_DIR
    target = directory / f"{thread_id}.json"
    staging = directory / f".{thread_id}.{os.getpid()}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        staging.write_text(json.dumps(record), encoding="utf-8")
        staging.replace(target)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        return f"could not write {target}: {exc}"
    return None


def _index_size(home: Path) -> int:
    try:
        return (home / INDEX_FILE).stat().st_size
    except OSError:
        return 0


def _named_since(home: Path, name: str, offset: int) -> bool:
    """Whether the index gained an entry naming ``name`` after byte ``offset``."""
    try:
        with (home / INDEX_FILE).open("rb") as handle:
            handle.seek(offset)
            added = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    for line in added.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and str(entry.get("thread_name", "")).strip() == name:
            return True
    return False


def _tmux(*args: str) -> subprocess.CompletedProcess | None:
    tmux = shutil.which("tmux")
    if not tmux:
        return None
    try:
        return subprocess.run([tmux, *args], capture_output=True, text=True, timeout=_TMUX_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ready(tmux_target: str) -> bool:
    captured = _tmux("capture-pane", "-p", "-t", tmux_target)
    return captured is not None and captured.returncode == 0 and READY_MARKER in captured.stdout


def _type_tmux(tmux_target: str, text: str, sleep: Callable[[float], None]) -> bool:
    typed = _tmux("send-keys", "-t", tmux_target, "-l", text)
    if typed is None or typed.returncode != 0:
        return False
    sleep(SUBMIT_SETTLE_SECONDS)
    submitted = _tmux("send-keys", "-t", tmux_target, "Enter")
    return submitted is not None and submitted.returncode == 0


def _type_fifo(fifo: Path, text: str, sleep: Callable[[float], None]) -> bool:
    try:
        # O_NONBLOCK: a FIFO no launcher holds open fails here instead of hanging.
        fd = os.open(fifo, os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    try:
        os.write(fd, text.encode("utf-8"))
        sleep(SUBMIT_SETTLE_SECONDS)
        os.write(fd, b"\r")
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def name_session(
    name: str,
    *,
    tmux_target: str | None = None,
    fifo: Path | None = None,
    prompt_file: Path | None = None,
    home: Path | None = None,
    ready_wait_seconds: float | None = None,
    confirm_wait_seconds: float | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str | None:
    """Type ``/rename <name>`` into a newly started Codex, then confirm it from the index.

    Exactly one of ``tmux_target`` and ``fifo`` names the input path. On tmux it waits for
    Codex's idle footer first; the bare pty path has no screen, so it waits a fixed time.
    A ``prompt_file`` (a relaunch's resume prompt) is pasted only once the name is
    confirmed, so the ``/rename`` is not queued behind a turn; the file is removed either
    way. Returns None once Codex recorded the name, else the reason it did not.
    """
    home = home or codex_home()
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    ready_wait_seconds = READY_WAIT_SECONDS if ready_wait_seconds is None else ready_wait_seconds
    confirm_wait_seconds = CONFIRM_WAIT_SECONDS if confirm_wait_seconds is None else confirm_wait_seconds
    try:
        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file is not None else None
    except OSError:
        prompt = None
    if prompt_file is not None:
        prompt_file.unlink(missing_ok=True)

    if tmux_target is not None:
        deadline = monotonic() + ready_wait_seconds
        while not _ready(tmux_target):
            if monotonic() >= deadline:
                return f"Codex never showed its input prompt within {ready_wait_seconds:.0f}s, so it was not named"
            sleep(POLL_SECONDS)
    else:
        sleep(FIFO_START_SECONDS)

    offset = _index_size(home)
    command = f"/rename {name}"
    if tmux_target is not None:
        typed = _type_tmux(tmux_target, command, sleep)
    elif fifo is not None:
        typed = _type_fifo(fifo, command, sleep)
    else:
        return "no input path to type into"
    if not typed:
        return "could not type the rename command into the session"

    deadline = monotonic() + confirm_wait_seconds
    while not _named_since(home, name, offset):
        if monotonic() >= deadline:
            return f"Codex did not record the name within {confirm_wait_seconds:.0f}s"
        sleep(POLL_SECONDS)

    if prompt and prompt.strip():
        if tmux_target is not None:
            from .background_attach import _paste

            delivered = _paste(tmux_target, prompt, sleep)
        else:
            delivered = fifo is not None and _type_fifo(fifo, prompt.removesuffix("\n"), sleep)
        if not delivered:
            return "the session was named, but the resume prompt could not be typed"
    return None


def _take_prompt() -> str | None:
    """Read and remove the resume prompt the session script left, and keep it from Codex's environment."""
    path = os.environ.pop(PROMPT_FILE_ENV, "")
    if not path:
        return None
    try:
        prompt = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    finally:
        Path(path).unlink(missing_ok=True)
    return prompt if prompt.strip() else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 6 and args[0] in {"prepare", "launch"}:
            if args[0] == "launch" and os.name != "posix":
                raise IdentityError("Codex writer exec identity unsupported on this platform")
            version = subprocess.run(["codex", "--version"], capture_output=True, text=True, check=False, timeout=10)
            match = re.fullmatch(r"codex-cli ([0-9.]+)\s*", version.stdout) if version.returncode == 0 else None
            target, config, record, token = prepare(
                Path(args[1]),
                Path(args[2]),
                args[3],
                fresh=args[4] == "true",
                route=args[5],
                client_version=match[1] if match else "unknown",
                name=os.environ.get(SESSION_NAME_ENV, "").strip() or None,
            )
            if args[0] == "launch":
                os.environ[SLOT_RECORD_ENV] = record
                os.environ[LAUNCH_TOKEN_ENV] = token
                from .codex_control import start

                start(Path(record))
                # Its own Codex process, not the host's shared app-server daemon, when this
                # Codex offers --no-daemon, so tool processes descend from the attested agent.
                own = exclusive_process_flags()
                command = ["codex", "-c", config]
                command.extend(["resume", *own, target] if target != "new" else own)
                prompt = _take_prompt()
                if prompt:
                    command.append(prompt)
                # POSIX exec preserves pid and birth marker, including through
                # executable shims. No child can run before the reservation exists.
                from .main import _exec_handoff

                _exec_handoff(command[0], command)
            output.raw(Untagged.INTERNAL_REPLY, target)
            output.raw(Untagged.INTERNAL_REPLY, config)
            output.raw(Untagged.INTERNAL_REPLY, record)
            output.raw(Untagged.INTERNAL_REPLY, token)
        else:
            raise IdentityError("invalid Codex identity operation")
    except IdentityError as exc:
        output.error(output.Tag.SESSION, "session", f"Codex identity unknown: {exc}")
        return 76 if args[:1] == ["launch"] else 1
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        portalocker.LockException,
        subprocess.TimeoutExpired,
    ):
        # Never echo event payloads, child output, or exception text from JSON parsing.
        output.error(
            output.Tag.SESSION,
            "session",
            "Codex identity unknown: launch/resume refused; keep a live pane attached or inspect the slot record.",
        )
        return 76 if args[:1] == ["launch"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
