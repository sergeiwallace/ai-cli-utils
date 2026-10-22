"""Restart one idle, supervised Claude Code process without replacing its pane.

Only this module's guarded act path signals a PID. Prompt injection and peer
observation are optional argv adapters, never shell strings. Unknown evidence
refuses an action. Windows uses injection because POSIX signals are unsupported.
"""

from __future__ import annotations

import contextlib
import errno
import getpass
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeGuard

import portalocker
import psutil

from . import config, session_run_state
from .process_probe import ProcessIdentity, StartTimeMatch, normalize_proc_start, probe_for

TAIL_LIMIT = 8 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
POLL_SECONDS = 0.2
RESTART_PIN_PROTOCOL = "restart-pin-v1"
# These base windows match the existing statusline model table. Unknown models
# require an explicit window; extended windows need a known tag or override.
MODEL_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-5-5": 200_000,
    "claude-fable-5-1": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-sonnet-5-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-5-5": 200_000,
}


class Refusal(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal("unreadable", path.name) from exc
    if not isinstance(value, dict):
        raise Refusal("unreadable", path.name)
    return value


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        Path(temporary).replace(path)
        # fsync the rename too, so the pin survives a host crash before signalling.
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def state_paths(session: str) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        raise Refusal("target", "invalid tmux session name")
    root = config.get_xdg_state_home() / "restart-session"
    return root / f"{session}.lock", root / f"{session}.json", root / f"{session}.request.json"


@contextlib.contextmanager
def session_lock(session: str):
    """Shared actor protocol: stable OS lock inode, never unlink the lock file."""
    path, _, _ = state_paths(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.LockException as exc:
            raise Refusal("locked") from exc

        def check() -> None:
            try:
                opened = os.fstat(handle.fileno())
                named = path.stat()
                if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                    raise Refusal("locked", "session lock changed")
            except (OSError, ValueError) as exc:
                raise Refusal("locked", "session lock unavailable") from exc

        try:
            yield check
        finally:
            portalocker.unlock(handle)


def _signal_supported() -> bool:
    # Windows has no POSIX SIGTERM/SIGKILL restart contract.
    return sys.platform != "win32"


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value: object) -> float:
    if _number(value):
        return float(value) / 1000  # registry timestamps are epoch milliseconds
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.timestamp()
    raise ValueError("timestamp unavailable")


def _pane(record: dict) -> tuple[str, str]:
    match = re.fullmatch(r"([A-Za-z0-9_-]+):@\d+\.(%\d+)", str(record.get("tmux", "")))
    if not match:
        raise Refusal("not-managed", "registry pane unavailable")
    return match[1], match[2]


def _run(argv: list[str], timeout: float = 15) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("probe-unavailable") from exc
    if result.returncode:
        # External output can contain credentials. Never echo it, including errors.
        raise Refusal("probe-unavailable")
    return result.stdout


def records() -> list[tuple[Path, dict]]:
    found = []
    for path in sorted((Path.home() / ".claude" / "sessions").glob("*.json")):
        try:
            found.append((path, _object(path)))
        except Refusal:
            continue
    return found


def resolve_target(target: str | None) -> tuple[Path, dict]:
    matches = []
    for path, record in records():
        try:
            session, pane = _pane(record)
        except Refusal:
            continue
        if (target and target in (record.get("name"), record.get("sessionId"), session)) or (
            not target and pane == os.environ.get("TMUX_PANE")
        ):
            matches.append((path, record))
    live = [
        (path, record)
        for path, record in matches
        if isinstance(record.get("pid"), int) and record["pid"] > 1 and not probe_for().has_ended(record["pid"])
    ]
    if matches and not live:
        raise Refusal("identity", "no live target process")
    matches = live
    if len(matches) != 1:
        candidates = ", ".join(str(record.get("name", path.stem)) for path, record in matches)
        raise Refusal("target", f"{len(matches)} candidates" + (f": {candidates}" if candidates else ""))
    return matches[0]


def transcript_path(record: dict) -> Path:
    try:
        session_id = str(uuid.UUID(record["sessionId"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise Refusal("background-work", "conversation identity unavailable") from exc
    matches = list((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    if len(matches) != 1:
        raise Refusal("background-work", "transcript unavailable or ambiguous")
    return matches[0].resolve()


def _background_events(row: dict) -> Iterator[tuple[str, bool]]:
    result = row.get("toolUseResult")
    if isinstance(result, dict):
        if result.get("backgroundTaskId"):
            yield str(result["backgroundTaskId"]), False
        if result.get("status") == "async_launched":
            if not result.get("agentId"):
                raise Refusal("background-work", "agent launch has no identity")
            yield str(result["agentId"]), False
    content = (
        row.get("content", [])
        if row.get("type") == "queue-operation" and row.get("operation") == "enqueue"
        else row.get("message", {}).get("content", [])
    )
    text = content if isinstance(content, str) else json.dumps(content)
    if "task-notification" in text and re.search(r"<status>(completed|failed|killed)</status>", text):
        for task_id in re.findall(r"<task-id>([^<]+)</task-id>", text):
            yield task_id, True
    if row.get("type") == "task-notification" and row.get("status") in ("completed", "failed", "killed"):
        yield str(row.get("taskId", "")), True


class _TranscriptTail(list[dict]):
    """Rows whose full history has already passed background verification."""


class _UnreadableTranscript(Refusal):
    """Transcript evidence is unavailable, rather than positive background work."""


class _UnfinishedBackgroundLaunch(Refusal):
    """A transcript launch has no matching completion yet."""


def transcript_tail(path: Path) -> list[dict]:
    """Scan every record, with a bounded tail and a disk-backed background ledger."""
    tail: deque[tuple[dict, int]] = deque()
    tail_size = 0
    seen = False
    try:
        # An on-disk ledger bounds memory even when the history contains many tasks,
        # including notifications recorded before their corresponding launch rows.
        with tempfile.TemporaryDirectory(prefix="restart-evidence-") as directory:
            with contextlib.closing(sqlite3.connect(str(Path(directory) / "tasks.sqlite"))) as tasks:
                tasks.execute("PRAGMA cache_size = -1024")
                tasks.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, launched INTEGER, completed INTEGER)")
                with path.open("rb") as source:
                    before = os.fstat(source.fileno())
                    remaining = before.st_size
                    pending = b""
                    while remaining:
                        chunk = source.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise ValueError("short transcript read")
                        remaining -= len(chunk)
                        lines = (pending + chunk).split(b"\n")
                        pending = lines.pop()
                        if len(pending) > TAIL_LIMIT:
                            raise ValueError("record exceeds evidence buffer")
                        for line in lines:
                            if len(line) > TAIL_LIMIT:
                                raise ValueError("record exceeds evidence buffer")
                            if not line.strip():
                                continue
                            row = json.loads(line)
                            if (
                                not isinstance(row, dict)
                                or not isinstance(row.get("message", {}), dict)
                                or not isinstance(row.get("compactMetadata", {}), dict)
                            ):
                                raise ValueError("invalid transcript evidence shape")
                            seen = True
                            # Sidechains cannot establish a main turn boundary, but
                            # their task launches still count as background evidence.
                            for task_id, completed in _background_events(row):
                                tasks.execute(
                                    "INSERT INTO tasks VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                                    "launched = MAX(launched, excluded.launched), "
                                    "completed = MAX(completed, excluded.completed)",
                                    (task_id, int(not completed), int(completed)),
                                )
                            if not row.get("isSidechain"):
                                length = len(line) + 1
                                tail.append((row, length))
                                tail_size += length
                                while tail_size > TAIL_LIMIT and tail:
                                    tail_size -= tail.popleft()[1]
                    after = os.fstat(source.fileno())
                    if (
                        pending
                        or not seen
                        or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    ):
                        raise ValueError("incomplete or changing transcript")
                if tasks.execute("SELECT 1 FROM tasks WHERE launched = 1 AND completed = 0 LIMIT 1").fetchone():
                    raise _UnfinishedBackgroundLaunch("background-work", "unfinished background launch")
        return _TranscriptTail(row for row, _ in tail)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise _UnreadableTranscript("background-work", "transcript evidence unreadable") from exc


def _completed_compaction(chain: list[dict]) -> bool:
    boundary = next((i for i in range(len(chain) - 1, -1, -1) if chain[i].get("subtype") == "compact_boundary"), None)
    if boundary is None:
        return False
    following = chain[boundary + 1 :]
    if not following or following[0].get("isCompactSummary") is not True:
        return False
    for row in following:
        if row.get("type") != "user" or "toolUseResult" in row:
            return False
        if row.get("isCompactSummary") is True:
            continue
        content = row.get("message", {}).get("content", "")
        if not isinstance(content, str) or not (
            (row.get("isMeta") is True and content.startswith("<local-command-caveat>"))
            or content.startswith(("<command-name>/compact</command-name>", "<local-command-stdout>"))
        ):
            return False
    return True


def idle_gate(record: dict, rows: list[dict], settings: dict) -> None:
    try:
        quiet = time.time() - _timestamp(record.get("statusUpdatedAt"))
    except (ValueError, TypeError, OverflowError):
        quiet = -1
    if record.get("status") != "idle" or record.get("waitingFor") or quiet < settings["settle_seconds"]:
        raise Refusal("busy", "registry is not settled idle")
    # Metadata/notifications can follow a completed turn; user/assistant/tool records cannot.
    chain = [row for row in rows if row.get("type") in ("user", "assistant", "system", "tool")]
    if not chain or (chain[-1].get("subtype") != "turn_duration" and not _completed_compaction(chain)):
        raise Refusal("busy", "transcript has no final turn boundary")
    turn_end = next((row for row in reversed(chain) if row.get("subtype") == "turn_duration"), None)
    if turn_end is None:
        raise Refusal("background-work", "pending agent counter boundary unavailable")
    # Claude Code omits this counter when it is zero, but the boundary is required.
    pending_agents = turn_end.get("pendingBackgroundAgentCount", 0)
    if type(pending_agents) is not int or pending_agents != 0:
        raise Refusal("background-work", "pending agent counter is positive or unavailable")
    launched: set[str] = set()
    completed: set[str] = set()
    for row in rows:
        for task_id, done in _background_events(row):
            (completed if done else launched).add(task_id)
    if not isinstance(rows, _TranscriptTail) and launched - completed:
        raise Refusal("background-work", "unfinished background launch")


def _base_model(model: str) -> str:
    model = model.split("[", 1)[0].rsplit("anthropic.", 1)[-1]
    return re.sub(r"-v\d+:\d+$", "", model)


def context_gate(rows: list[dict], settings: dict) -> bool:
    boundary = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].get("subtype") == "compact_boundary"), None)
    if boundary is not None:
        following = rows[boundary + 1 :]
        turns = {
            row.get("message", {}).get("id", str(i))
            for i, row in enumerate(following)
            if row.get("type") == "assistant"
        }
        if (
            rows[boundary].get("compactMetadata", {}).get("trigger") == "auto"
            and len(turns) <= settings["compaction_fresh_turns"]
        ):
            return True
    else:
        following = rows
    latest = next(
        (
            row
            for row in reversed(following)
            if row.get("type") == "assistant" and row.get("message", {}).get("usage") is not None
        ),
        {},
    )
    message = latest.get("message", {})
    usage = message.get("usage")
    tokens: list[int | float] = []
    if not latest and boundary is not None:
        token = rows[boundary].get("compactMetadata", {}).get("postTokens")
        if not _number(token) or token < 0:
            return False
        tokens.append(token)
    else:
        if not isinstance(usage, dict):
            return False
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            token = usage.get(key)
            if not _number(token) or token < 0:
                return False
            tokens.append(token)
    window = settings["context_window_tokens"]
    model = message.get("model", "")
    if not window:
        base = _base_model(model) if isinstance(model, str) else ""
        requested = latest.get("requestedModel")
        if (
            isinstance(requested, str)
            and re.fullmatch(r"[A-Za-z0-9._:/@-]+(\[1m\])?", requested)
            and _base_model(requested) == base
        ):
            model = requested
        window = MODEL_WINDOWS.get(model, MODEL_WINDOWS.get(base)) if isinstance(model, str) else None
        if isinstance(model, str) and model.endswith("[1m]") and base in MODEL_WINDOWS and "haiku" not in base:
            window = 1_000_000
    return bool(window and 100 * sum(tokens) / window <= settings["context_threshold_pct"])


def context_pre_step(
    record: dict,
    settings: dict,
    commands: dict,
    record_path: Path | None,
    managed: Managed,
    check_lock: Callable[[], None] | None,
) -> str:
    """Submit once, then require new compaction evidence and a fresh idle interval."""
    if not commands.get("inject"):
        return "pre-step unavailable"
    if record_path is None or check_lock is None:
        raise Refusal("locked", "pre-step requires lock ownership")
    path = transcript_path(record)

    def boundaries(rows: list[dict]) -> Counter[str]:
        return Counter(json.dumps(row, sort_keys=True) for row in rows if row.get("subtype") == "compact_boundary")

    def fresh(*, require_idle: bool = True) -> dict:
        check_lock()
        current = _fresh(record_path, record)
        if managed_gate(current, settings, prepared=managed) != managed:
            raise Refusal("identity", "process ownership changed during pre-step")
        current = _fresh(record_path, record)
        if require_idle and (current.get("status") != "idle" or current.get("waitingFor")):
            raise Refusal("busy", "registry became busy during pre-step")
        check_lock()
        return current

    # Capability probes can take time; repeat the idle/background checks after them.
    current = fresh()
    idle_gate(current, transcript_tail(path), settings)
    descendants_gate(current["pid"], Path(current["cwd"]))
    rows = transcript_tail(path)
    idle_gate(_fresh(record_path, record), rows, settings)
    check_lock()
    before = boundaries(rows)
    outcome, queued = _inject_save_state(current, commands)
    deadline = time.monotonic() + settings["compaction_wait_seconds"]
    compacted = False
    idle_since = None
    while True:
        # The injected /save-state and the compaction it triggers keep the session busy. Identity and
        # the lock are still checked each pass; idleness is required only once new compaction
        # evidence exists (the compacted branch below) and again before the final assessment.
        current = fresh(require_idle=False)
        last_pass_detail = ""
        try:
            rows = transcript_tail(path)
        except _UnreadableTranscript:
            rows = None
            idle_since = None
            last_pass_detail = "; transcript was unreadable on the last pass"
        except _UnfinishedBackgroundLaunch:
            rows = None
            idle_since = None
            last_pass_detail = "; an unfinished background launch was seen on the last pass"
        now = time.monotonic()
        if rows is not None and not compacted and boundaries(rows) - before:
            compacted = True
            # Allow the full idle interval even if compaction used its wait budget.
            deadline += settings["settle_seconds"]
        if rows is not None and compacted:
            descendants_gate(current["pid"], Path(current["cwd"]))
            try:
                idle_gate(current, rows, {**settings, "settle_seconds": 0})
            except Refusal as exc:
                if exc.reason != "busy":
                    raise
                idle_since = None
            else:
                if idle_since is None:
                    idle_since = now
                if now - idle_since >= settings["settle_seconds"]:
                    # No recursive pre-step: context or any other failing gate now refuses.
                    assess(fresh(), settings, commands, prepared=managed)
                    check_lock()
                    return ""
        if now >= deadline:
            detail = f"{outcome}; compaction wait timed out"
            if queued and not compacted:
                detail += "; save-state was queued and never observed to compact"
            return detail + last_pass_detail
        time.sleep(min(POLL_SECONDS, deadline - now))


def _settings(cwd: Path) -> list[dict]:
    paths = [
        Path.home() / ".claude" / "settings.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    ]
    settings = []
    for path in paths:
        try:
            path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise Refusal("rc-activation", "settings unreadable") from exc
        settings.append(_object(path))
    return settings


def activation_gate(cwd: Path) -> None:
    enabled = None
    try:
        for settings in _settings(cwd):
            if "remoteControlAtStartup" in settings:
                enabled = settings["remoteControlAtStartup"]
    except Refusal as exc:
        raise Refusal("rc-activation", "settings unreadable") from exc
    if enabled is not True:
        raise Refusal("rc-activation", "remoteControlAtStartup must be true")


def _mcp_commands(cwd: Path) -> list[tuple[str, list[str]]]:
    commands = []
    for path in (Path.home() / ".claude.json", cwd / ".mcp.json"):
        try:
            path.stat()
        except FileNotFoundError:
            continue
        data = _object(path)
        scopes = [data]
        if path.name == ".claude.json":
            projects = data.get("projects", {})
            if not isinstance(projects, dict):
                raise Refusal("background-work", "MCP configuration unreadable")
            scopes.append(projects.get(str(cwd), {}))
        for scope in scopes:
            if not isinstance(scope, dict) or not isinstance(scope.get("mcpServers", {}), dict):
                raise Refusal("background-work", "MCP configuration unreadable")
            for server in scope.get("mcpServers", {}).values():
                if isinstance(server, dict) and isinstance(server.get("command"), str):
                    args = server.get("args", [])
                    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                        raise Refusal("background-work", "MCP arguments unreadable")
                    commands.append((server["command"], args))
    return commands


def _command_path(command: str, cwd: Path) -> Path | None:
    path = Path(command).expanduser()
    if not path.is_absolute():
        if path.parent == Path():
            found = shutil.which(command)
            if not found:
                return None
            path = Path(found)
        else:
            path = cwd / path
    try:
        return path.resolve(strict=True)
    except OSError:
        return None


def _mcp_matches(argv: list[str], command: str, args: list[str], cwd: Path) -> bool:
    configured = _command_path(command, cwd)
    if not argv or configured is None:
        return False
    executable = _command_path(argv[0], cwd)
    if executable == configured:
        return not args or argv[1:] == args
    # A shebang script appears after its interpreter in the process command line.
    # Verify both identities rather than trusting an argument suffix alone.
    with configured.open("rb") as source:
        header = source.readline(256)
    if not header.startswith(b"#!"):
        return False
    interpreter = shlex.split(header[2:].decode("utf-8"))
    index = len(interpreter)
    return bool(
        interpreter
        and len(argv) > index
        and executable == _command_path(interpreter[0], cwd)
        and argv[1:index] == interpreter[1:]
        and _command_path(argv[index], cwd) == configured
        and (not args or argv[index + 1 :] == args)
    )


def _mcp_allowlist() -> list[dict]:
    """Consume installer-verified shapes; never derive trust from live children."""
    path = config.get_xdg_config_home() / "restart-session.d" / "mcp-servers.toml"
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise Refusal("background-work", "MCP allowlist unreadable; regenerate the allowlist") from exc
    name = "mcp-servers.toml"
    try:
        if type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("servers"), list):
            raise ValueError("invalid schema")
        names = set()
        for entry in data["servers"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
                raise ValueError("missing entry name")
            name = entry["name"]
            if name in names:
                raise ValueError("duplicate entry name")
            names.add(name)
            argv = entry.get("argv")
            executable = entry.get("executable")
            stamps = entry.get("identities")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(arg, str) for arg in argv)
                or not argv[0]
                or not isinstance(executable, str)
                or not Path(executable).is_absolute()
                or not isinstance(stamps, list)
                or not stamps
            ):
                raise ValueError("invalid runtime shape")
            paths = set()
            for stamp in stamps:
                if (
                    not isinstance(stamp, dict)
                    or not isinstance(stamp.get("path"), str)
                    or not Path(stamp["path"]).is_absolute()
                    or type(stamp.get("size")) is not int
                    or stamp["size"] < 0
                    or not isinstance(stamp.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", stamp["sha256"])
                ):
                    raise ValueError("invalid identity stamp")
                identity = Path(stamp["path"])
                # Canonical paths prevent silently accepting a retargeted symlink.
                if identity.resolve(strict=True) != identity or identity in paths:
                    raise ValueError("noncanonical or duplicate identity")
                paths.add(identity)
                with identity.open("rb") as source:
                    before = os.fstat(source.fileno())
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                    after = os.fstat(source.fileno())
                if (
                    before.st_size != stamp["size"]
                    or digest != stamp["sha256"]
                    or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                ):
                    raise ValueError("stale identity stamp")
            if Path(executable) not in paths:
                raise ValueError("executable stamp missing")
        return data["servers"]
    except (OSError, ValueError, TypeError) as exc:
        raise Refusal(
            "background-work", f"MCP entry {name}: identity invalid or unreadable; regenerate the allowlist"
        ) from exc


def _mcp_runtime_matches(child: psutil.Process, argv: list[str], entries: list[dict]) -> bool:
    for entry in entries:
        if argv == entry["argv"]:
            executable = Path(child.exe())
            if executable.is_absolute() and executable.resolve(strict=True) == Path(entry["executable"]):
                return True
    return False


def _mcp_npx_descendants(children: list[psutil.Process], entries: list[dict], cwd: Path) -> None:
    """Require one certified installation to account for the entire helper tree."""
    cache = Path.home() / ".npm" / "_npx"
    entries = [
        entry
        for entry in entries
        if any(
            Path(stamp["path"]).name in {"npx", "npx-cli.js", "npx-cli"} or Path(stamp["path"]).is_relative_to(cache)
            for stamp in entry["identities"]
        )
    ]
    if not entries:
        return
    # These POSIX helper shells are permitted only by their resolved system paths.
    shells = {_command_path(shell, cwd) for shell in ("/bin/sh", "/usr/bin/bash", "/usr/bin/sh")}
    for child in children:
        executable = Path(child.exe())
        argv = child.cmdline()
        if not executable.is_absolute() or not argv:
            raise Refusal("background-work", f"unresolvable npx descendant: exe={str(executable)!r} argv={argv!r}")
        executable = executable.resolve(strict=True)
        entries = [entry for entry in entries if executable in shells or executable == Path(entry["executable"])]
        if not entries:
            raise Refusal("background-work", f"unclassified npx descendant: exe={str(executable)!r} argv={argv!r}")
        paths = set()
        for index, argument in enumerate(argv):
            # npm's shell command is one argv item; inspect its individual words too.
            words = (
                shlex.split(argument)
                if index and re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", argv[index - 1])
                else [argument]
            )
            for word in words:
                word = word.split("=", 1)[-1] if word.startswith("-") and "=" in word else word
                if not word or word.startswith("-"):
                    continue
                path = Path(word).expanduser()
                if not path.is_absolute():
                    # A bare argv[0] is resolved via PATH, rather than the process cwd.
                    if index == 0 and "/" not in word and "\\" not in word:
                        continue
                    path = Path(child.cwd()) / path
                    if "/" not in word and "\\" not in word and not path.is_file():
                        continue
                resolved = path.resolve()
                if path.is_relative_to(cache) or resolved.is_relative_to(cache.resolve()):
                    paths.add(resolved)
        entries = [entry for entry in entries if paths <= {Path(stamp["path"]) for stamp in entry["identities"]}]
        if not entries:
            raise Refusal(
                "background-work",
                "uncertified npx installation; regenerate the MCP launcher certificates with the installer that wrote them",
            )


def descendants_gate(pid: int, cwd: Path) -> None:
    """Allow only configured MCP server roots and their own descendants."""
    try:
        commands = _mcp_commands(cwd)
        entries = _mcp_allowlist()
        agent = psutil.Process(pid)
        children = agent.children(recursive=True)
        started = agent.create_time()
        allowed: dict[int, int] = {}
        runtimes: dict[int, list[dict]] = {}
        helpers: dict[int, list[psutil.Process]] = {}
        pending = {child.pid: child for child in children}
        while pending:
            progress = False
            for child_id, child in list(pending.items()):
                parent = child.ppid()
                server = False
                if parent == pid and (commands or entries):
                    born = child.create_time()
                    if _number(started) and _number(born) and 0 <= born - started <= 60:
                        argv = child.cmdline()
                        matching = [entry for entry in entries if _mcp_runtime_matches(child, argv, [entry])]
                        server = bool(matching)
                        if server:
                            runtimes[child_id] = matching
                        if not server:
                            for command, args in commands:
                                if _mcp_matches(argv, command, args, cwd):
                                    server = True
                                    break
                if parent in allowed or server:
                    root = allowed.get(parent, child_id)
                    allowed[child_id] = root
                    if parent in allowed:
                        helpers.setdefault(root, []).append(child)
                    del pending[child_id]
                    progress = True
            if not progress:
                raise Refusal("background-work", "unclassified live descendant")
        for root, matching in runtimes.items():
            _mcp_npx_descendants(helpers.get(root, []), matching, cwd)
    except (OSError, psutil.Error, TypeError, ValueError) as exc:
        raise Refusal("background-work", "process tree unavailable") from exc


def load_commands() -> dict[str, list[str]]:
    commands = {}
    root = config.get_xdg_config_home() / "restart-session.d"
    for path in sorted(root.glob("*.toml")):
        try:
            with path.open("rb") as source:
                data = tomllib.load(source)
            for name in ("inject", "observer"):
                if name not in data:
                    continue
                argv = data[name]["argv"]
                if (
                    name in commands
                    or not isinstance(argv, list)
                    or not argv
                    or not all(isinstance(arg, str) and arg for arg in argv)
                ):
                    raise ValueError("ambiguous or invalid command")
                commands[name] = argv
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Refusal("config", f"invalid command file {path.name}") from exc
    return commands


def _argv(command: list[str], record: dict, slash: str = "") -> list[str]:
    session, pane = _pane(record)
    values = {
        "tmux_session": session,
        "tmux_pane": pane,
        "session_id": record["sessionId"],
        "title": record["name"],
        "command": slash,
    }
    try:
        return [arg.format_map(values) for arg in command]
    except (ValueError, KeyError) as exc:
        raise Refusal("config", "unknown command placeholder") from exc


@dataclass(frozen=True)
class Managed:
    session: str
    pane: str
    generation: str
    supervisor_pid: int
    supervisor_identity: ProcessIdentity
    child_pid: int
    child_identity: ProcessIdentity
    agent_identity: ProcessIdentity
    script: Path
    source: str
    metadata: dict


def _identity(pid: int) -> ProcessIdentity:
    value = probe_for().capture_identity(pid)
    if value is None:
        raise Refusal("identity", "process birth identity unavailable")
    return value


def _script_source(pid: int, script: Path) -> str:
    """Read the shell's open inode, or prove the stable file predates this shell.

    procfs provides the open script on Linux even after an atomic refresh. On
    macOS the unchanged-file route uses filesystem change time and process birth.
    Unknown provenance is deliberately an upgrade refusal.
    """
    if sys.platform.startswith("linux"):
        root = Path("/proc") / str(pid) / "fd"
        for descriptor in root.iterdir():
            with contextlib.suppress(OSError):
                if str(descriptor.readlink()).removesuffix(" (deleted)") == str(script):
                    return descriptor.read_text(encoding="utf-8")
    process = psutil.Process(pid)
    before = script.stat()
    if before.st_ctime > process.create_time():
        raise Refusal("supervisor-upgrade-required", "running script provenance unavailable")
    source = script.read_text(encoding="utf-8")
    if script.stat() != before:
        raise Refusal("supervisor-upgrade-required", "script changed during inspection")
    return source


def _compatible(source: str, expected: str) -> bool:
    # Full program comparison, excluding package stamps. New restart insertions
    # are bracketed so the one supported pre-feature program can be certified too.
    def normalize(text: str) -> str:
        text = re.sub(r"(?ms)^    # BEGIN (PINNED RESTART|RUN STATE)[^\n]*\n.*?^    # END \1\n", "", text)
        text = re.sub(r"(?m)^    _template_(?:version|commit)=.*$", "", text)
        return "\n".join(line for line in text.splitlines() if line.strip())

    return normalize(source) == normalize(expected)


def _tmux_pane_environment(session: str) -> dict[str, str]:
    """The environment tmux hands a new pane in *session*: global, then session overrides."""
    environment: dict[str, str] = {}
    for scope in (["-g"], ["-t", session]):
        for line in _run(["tmux", "show-environment", *scope]).splitlines():
            name, separator, value = line.partition("=")
            if separator:
                environment[name] = value
            elif name.startswith("-"):  # tmux's mark for a variable removed in this scope
                environment.pop(name[1:], None)
    return environment


def _restart_pin_gate(supervisor: int, cwd: Path, session: str) -> None:
    """Probe the helper the replacement child inherits, not the caller's install."""
    try:
        environment = psutil.Process(supervisor).environ()
        if "PATH" not in environment and sys.platform == "darwin":
            # macOS-specific: the kernel withholds the environment of Apple platform
            # binaries, and every real supervisor runs under one (/bin/zsh, /bin/bash),
            # so psutil returns {} instead of raising. The pane's PATH was set by the
            # launcher's `new-session -e PATH=...`, which tmux still holds.
            with contextlib.suppress(Refusal):
                environment = _tmux_pane_environment(session)
        if "PATH" not in environment:
            raise Refusal("supervisor-upgrade-required", "supervisor PATH unavailable")
        # The child changes to its working directory before resolving `ai`.
        # Empty and relative PATH entries must therefore be anchored there too.
        search = os.pathsep.join(str(cwd / entry) for entry in environment["PATH"].split(os.pathsep))
        executable = shutil.which("ai", path=search)
        if executable is None:
            raise Refusal("supervisor-upgrade-required", "supervisor ai executable unavailable")
        result = subprocess.run(
            [executable, "internal", "restart-pin", "--probe"],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=15,
        )
        if result.returncode or result.stdout.strip() != RESTART_PIN_PROTOCOL:
            raise Refusal("supervisor-upgrade-required", "supervisor ai lacks restart-pin support")
    except (OSError, ValueError, subprocess.SubprocessError, psutil.Error) as exc:
        # Neither the pane environment nor executable output is safe to echo.
        raise Refusal("supervisor-upgrade-required", "restart-pin compatibility cannot be established") from exc


def managed_gate(record: dict, settings: dict, *, prepared: Managed | None = None) -> Managed:
    from .main import _engine_script_from_meta

    try:
        session, pane = _pane(record)
        pid = record["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            raise Refusal("identity")
        probe = probe_for()
        if (
            probe.has_ended(pid)
            or probe.start_time_match(pid, normalize_proc_start(record.get("procStart"))) is not StartTimeMatch.MATCH
        ):
            raise Refusal("identity", "dead, reused or unproven agent pid")
        agent = psutil.Process(pid)
        if time.time() - agent.create_time() < settings["min_uptime_seconds"]:
            raise Refusal("uptime")
        shown = (
            _run(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    pane,
                    "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{@ai_cli_session_generation}\t#{pane_start_command}",
                ]
            )
            .strip()
            .split("\t")
        )
        if len(shown) != 5 or shown[0] != session or shown[1] != pane or not shown[3]:
            raise Refusal("not-managed", "pane identity unavailable")
        supervisor = int(shown[2])
        script = config.get_xdg_state_home() / "sessions" / f"{session}.sh"
        command = shlex.split(shown[4])
        if str(script) not in command or command[-1] != str(script):
            raise Refusal("not-managed", "pane start command is not the stable session script")
        parents = agent.parents()
        if supervisor not in [parent.pid for parent in parents]:
            raise Refusal("not-managed", "agent is not owned by pane supervisor")
        child = next((parent for parent in parents if parent.ppid() == supervisor), None)
        if child is None or "--ai-cli-child-body" not in child.cmdline():
            raise Refusal("supervisor-upgrade-required", "replaceable child unavailable")
        metadata = _object(config.get_xdg_state_home() / f"session-meta-{session}.json")
        if (
            metadata.get("engine") != "c"
            or metadata.get("session") != session
            or metadata.get("ai_name") != record.get("name")
        ):
            raise Refusal("supervisor-upgrade-required", "metadata identity differs")
        expected = _engine_script_from_meta(metadata)
        source = script.read_text(encoding="utf-8")  # inspect the target before replacement
        if prepared is not None and sys.platform == "darwin":
            # Atomic replacement preserves the old executing inode. macOS cannot
            # reread it through procfs, so retain this lock owner's pre-refresh
            # program proof only while both loaded processes have the same births.
            if (supervisor, child.pid, _identity(supervisor), _identity(child.pid)) != (
                prepared.supervisor_pid,
                prepared.child_pid,
                prepared.supervisor_identity,
                prepared.child_identity,
            ):
                raise Refusal("identity", "prepared program ownership changed")
            running_sources = [prepared.source]
        else:
            running_sources = [_script_source(supervisor, script), _script_source(child.pid, script)]
        if not all(_compatible(text, expected) for text in (source, *running_sources)):
            raise Refusal("supervisor-upgrade-required", "unknown running supervisor or child program")
        child_cwd = (Path(psutil.Process(supervisor).cwd()) / (metadata.get("worktree_dir") or "")).resolve()
        _restart_pin_gate(supervisor, child_cwd, session)
        return Managed(
            session,
            pane,
            shown[3],
            supervisor,
            _identity(supervisor),
            child.pid,
            _identity(child.pid),
            _identity(pid),
            script,
            source,
            metadata,
        )
    except Refusal:
        raise
    except (OSError, ValueError, KeyError, psutil.Error, StopIteration) as exc:
        raise Refusal("supervisor-upgrade-required", "supervisor compatibility cannot be established") from exc


def boot_recipe_gate(record: dict) -> None:
    session, _ = _pane(record)
    try:
        session_run_state.can_begin(session)
    except (OSError, ValueError, RuntimeError, portalocker.exceptions.LockException) as exc:
        raise Refusal("no-boot-recipe", str(exc)) from exc


def assess(
    record: dict,
    settings: dict,
    commands: dict,
    *,
    pre_step: bool = False,
    prepared: Managed | None = None,
    record_path: Path | None = None,
    check_lock: Callable[[], None] | None = None,
) -> Managed:
    managed = managed_gate(record, settings, prepared=prepared)
    boot_recipe_gate(record)
    cwd = Path(record.get("cwd", "")).resolve()
    if not record.get("cwd") or not cwd.is_dir():
        raise Refusal("not-managed", "working directory unavailable")
    activation_gate(cwd)
    rows = transcript_tail(transcript_path(record))
    idle_gate(record, rows, settings)
    descendants_gate(record["pid"], cwd)
    if not context_gate(rows, settings):
        detail = (
            context_pre_step(record, settings, commands, record_path, managed, check_lock)
            if pre_step and settings["pre_step"]
            else "pre-step unavailable"
        )
        if detail:
            raise Refusal("context", detail)
    return managed


def _digest(script: Path) -> str:
    return hashlib.sha256(script.read_bytes()).hexdigest()


def prepare_request(record: dict, managed: Managed, settings: dict) -> dict:
    from .main import _engine_script_from_meta, _write_launch_script_if_changed

    _, _, path = state_paths(managed.session)
    if path.exists():
        previous = _object(path)
        # A manual retry is recovery only for this exact, currently validated conversation.
        if previous.get("status") != "complete" and (
            previous.get("session_id") != record["sessionId"] or previous.get("title") != record["name"]
        ):
            raise Refusal("restart-fenced", "explicit recovery of the pinned conversation required")
    if managed.script.read_text(encoding="utf-8") != managed.source:
        raise Refusal("supervisor-upgrade-required", "stable script changed")
    refreshed = _engine_script_from_meta(managed.metadata)
    _write_launch_script_if_changed(managed.script, refreshed)
    if managed.script.read_text(encoding="utf-8") != refreshed:
        raise Refusal("supervisor-upgrade-required", "prepared child digest differs")
    request = {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "status": "prepared",
        "session_id": record["sessionId"],
        "transcript": str(transcript_path(record)),
        "title": record["name"],
        "cwd": str(Path(record["cwd"]).resolve()),
        "tmux": record["tmux"],
        "generation": managed.generation,
        "supervisor_pid": managed.supervisor_pid,
        "supervisor_identity": asdict(managed.supervisor_identity),
        "old_child_pid": managed.child_pid,
        "old_child_identity": asdict(managed.child_identity),
        "old_agent_pid": record["pid"],
        "old_agent_identity": asdict(managed.agent_identity),
        "launch_log_path": managed.metadata.get("launch_log_path"),
        "owner_pid": os.getpid(),
        "owner_identity": asdict(_identity(os.getpid())),
        "script_digest": _digest(managed.script),
        "expires_at": time.time() + max(1, settings["term_grace_seconds"] + settings["relaunch_timeout_seconds"]),
    }
    _atomic_json(path, request)
    return request


def _same(pid: int, identity: dict) -> bool:
    current = probe_for().capture_identity(pid)
    return current is not None and asdict(current) == identity


def _clear_proof(request: dict) -> dict:
    """Prove absence or a different birth; unreadable evidence never permits clearing."""
    pid = request.get("old_agent_pid")
    recorded = request.get("old_agent_identity")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise Refusal("identity", "recorded target pid unavailable")
    probe = probe_for()
    if not probe.is_present(pid):
        return {"pid": pid, "state": "gone", "recorded": recorded}
    current = probe.capture_identity(pid)
    if (
        current is None
        or not isinstance(recorded, dict)
        or recorded.get("backend") != current.backend
        or not _number(recorded.get("marker"))
    ):
        raise Refusal("identity", "target birth identity unproven")
    if current.marker == recorded["marker"]:
        raise Refusal("target-live", "recorded target is still alive")
    return {"pid": pid, "state": "reused", "recorded": recorded, "current": asdict(current)}


def clear_request(request_id: str, reason: str) -> None:
    """Audit recovery under the restart actor lock, then unlink only the named request."""
    if not reason.strip():
        raise Refusal("reason", "a nonempty reason is required")
    try:
        request_id = str(uuid.UUID(request_id))
    except ValueError as exc:
        raise Refusal("target", "invalid request id") from exc
    root = config.get_xdg_state_home()
    matches = []
    for path in (root / "restart-session").glob("*.request.json"):
        try:
            if _object(path).get("request_id") == request_id:
                matches.append(path)
        except Refusal:
            continue
    if len(matches) != 1:
        raise Refusal("target", "request id unavailable or ambiguous")
    path = matches[0]
    session = path.name.removesuffix(".request.json")
    with session_lock(session) as check_lock:
        request = _object(path)
        if (
            path.is_symlink()
            or request.get("request_id") != request_id
            or request.get("version") != 1
            or request.get("status") not in ("prepared", "acting", "failed")
            or _pane(request)[0] != session
        ):
            raise Refusal("restart-fenced", "request changed or invalid")
        proof = _clear_proof(request)
        log_path = request.get("launch_log_path")
        if not log_path:
            # Requests written before recovery support use the supervisor metadata.
            log_path = _object(root / f"session-meta-{session}.json").get("launch_log_path")
        if not isinstance(log_path, str) or not log_path:
            raise Refusal("unavailable", "supervisor log unavailable")
        audit = {
            "event": "restart-request-clear",
            "request_id": request_id,
            "reason": reason,
            "actor": {"user": getpass.getuser(), "pid": os.getpid()},
            "proof": proof,
            "time": time.time(),
        }
        check_lock()
        if _object(path) != request:
            raise Refusal("restart-fenced", "request changed")
        with Path(log_path).open("a", encoding="utf-8") as output:
            output.write(json.dumps(audit, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        check_lock()
        if _object(path) != request:
            raise Refusal("restart-fenced", "request changed after audit")
        path.unlink()


def fenced_remedy(session: str) -> str:
    retry = f"after validating the pinned session, retry with: ai --restart-session {session}"
    try:
        request = _object(state_paths(session)[2])
        request_id = str(uuid.UUID(request["request_id"]))
        _clear_proof(request)
    except (Refusal, OSError, KeyError, TypeError, ValueError, psutil.Error):
        return retry
    return f"target gone or pid reused; clear with: ai clear-restart-request {request_id} --reason 'explain recovery'"


def _validate_request(request: dict, session: str, *, child: bool) -> None:
    try:
        _, _, path = state_paths(session)
        if request != _object(path) or request["version"] != 1 or request["status"] not in ("prepared", "acting"):
            raise ValueError("request changed or fenced")
        uuid.UUID(request["request_id"])
        if str(uuid.UUID(request["session_id"])) != request["session_id"]:
            raise ValueError("invalid conversation id")
        if not _number(request["expires_at"]) or time.time() >= request["expires_at"]:
            raise ValueError("expired request")
        transcript = Path(request["transcript"])
        if not transcript.is_file() or transcript.stem != request["session_id"]:
            raise ValueError("missing pinned transcript")
        if not _same(request["owner_pid"], request["owner_identity"]):
            raise ValueError("restart owner exited")
        if not _same(request["supervisor_pid"], request["supervisor_identity"]):
            raise ValueError("supervisor birth changed")
        pane_session, pane = _pane({"tmux": request["tmux"]})
        shown = (
            _run(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    pane,
                    "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{@ai_cli_session_generation}",
                ]
            )
            .strip()
            .split("\t")
        )
        if shown != [session, pane, str(request["supervisor_pid"]), request["generation"]] or pane_session != session:
            raise ValueError("supervisor generation changed")
        script = config.get_xdg_state_home() / "sessions" / f"{session}.sh"
        if _digest(script) != request["script_digest"]:
            raise ValueError("prepared script changed")
        if child:
            if request["status"] != "acting" or str(Path.cwd().resolve()) != request["cwd"]:
                raise ValueError("request not acting or cwd changed")
            parent = psutil.Process(os.getppid())
            if parent.ppid() != request["supervisor_pid"]:
                raise ValueError("replacement child ownership changed")
            for prefix in ("old_child", "old_agent"):
                pid = request[f"{prefix}_pid"]
                if not probe_for().has_ended(pid) and _same(pid, request[f"{prefix}_identity"]):
                    raise ValueError("old child or agent has not exited")
            # The helper must still own the shared lock, not merely exist.
            try:
                with session_lock(session):
                    raise ValueError("restart lock abandoned")
            except Refusal as exc:
                if exc.reason != "locked":
                    raise
        elif not all(
            _same(request[f"{prefix}_pid"], request[f"{prefix}_identity"]) for prefix in ("old_child", "old_agent")
        ):
            raise ValueError("old child or agent birth changed")
    except (OSError, KeyError, TypeError, ValueError, psutil.Error) as exc:
        raise Refusal("restart-fenced", "request identity or lifetime invalid") from exc


def child_pin(session: str, title: str) -> tuple[int, str]:
    """Internal child protocol: 3 ordinary launch, 0 exact transcript, 1 fenced.

    A request is not consumed. A per-request exclusive launch receipt prohibits
    relaunching even the pinned conversation twice after a failed resume.
    """
    _, _, path = state_paths(session)
    if not path.exists():
        return 3, ""
    try:
        request = _object(path)
        if request.get("status") == "complete":
            if (
                request.get("version") != 1
                or request.get("result") != "restarted"
                or request.get("title") != title
                or _pane({"tmux": request["tmux"]})[0] != session
                or str(uuid.UUID(request["session_id"])) != request["session_id"]
            ):
                raise Refusal("restart-fenced")
            return 3, ""
        _validate_request(request, session, child=True)
        if request["title"] != title:
            raise Refusal("restart-fenced")
        receipt = path.with_name(f"{session}.{request['request_id']}.launched")
        with receipt.open("x", encoding="utf-8") as output:
            output.write(request["request_id"])
            output.flush()
            os.fsync(output.fileno())
        return 0, request["transcript"]
    except (Refusal, OSError, KeyError, TypeError, ValueError):
        return 1, ""


def observer_verdict(record: dict, command: list[str] | None) -> str:
    if command is None:
        return "UNVERIFIED"
    started = time.time()
    try:
        raw = _run(_argv(command, record))
        if "sessions beyond the first pages are missing" in raw:
            return "UNKNOWN"
        data = json.loads(raw)
        observed = data.get("observed_at")
        moment = _timestamp(observed) if isinstance(observed, str) else observed
        if not _number(moment) or not started <= moment <= time.time() or data.get("complete") is not True:
            return "UNKNOWN"
        if any(data.get(key) for key in ("truncated", "listTruncated", "bridgeWalkFailed", "localListFailed")):
            return "UNKNOWN"
        rows = data.get("rows")
        if (
            not isinstance(rows, list)
            or any(
                not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key] for key in ("session_id", "title"))
                for row in rows
            )
            or data.get("status") not in ("seen", "absent", "unknown")
        ):
            return "UNKNOWN"
        if data["status"] == "unknown":
            return "UNKNOWN"
        related = [
            row
            for row in rows
            if isinstance(row, dict)
            and (row.get("session_id") == record["sessionId"] or row.get("title") == record["name"])
        ]
        if data["status"] == "absent":
            return "FAILED" if not related else "UNKNOWN"
        if len(related) != 1:
            return "FAILED"
        row = related[0]
        if row.get("session_id") != record["sessionId"] or row.get("title") != record["name"]:
            return "FAILED"
        if row.get("bridge_session_id") is not None and row["bridge_session_id"] != record.get("bridgeSessionId"):
            return "FAILED"
        return "VERIFIED"
    except (Refusal, ValueError, TypeError, AttributeError, OverflowError):
        return "UNKNOWN"


def local_verdict(record: dict, *, details: list[str] | None = None) -> str:
    try:
        pid = record["pid"]
        probe = probe_for()
        if (
            probe.has_ended(pid)
            or probe.start_time_match(pid, normalize_proc_start(record.get("procStart"))) is not StartTimeMatch.MATCH
        ):
            return "UNKNOWN"
        if record.get("nameSource") != "user" or not record.get("bridgeSessionId"):
            return "FAILED"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            try:
                connection.connect(record["messagingSocketPath"])
            except OSError as exc:
                # A refused connection is positive failure evidence. Missing or
                # inaccessible paths can be namespace/access problems: unknown.
                if exc.errno == errno.ECONNREFUSED:
                    return "FAILED"
                raise
        data = json.loads(_run(["claude", "agents", "--json"]))
        if isinstance(data, dict):
            if data.get("complete") is not True or any(
                data.get(key) for key in ("listTruncated", "localListFailed", "bridgeWalkFailed", "truncated")
            ):
                return "UNKNOWN"
            rows = data.get("sessions")
        else:
            rows = data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return "UNKNOWN"
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and (row.get("sessionId") == record["sessionId"] or row.get("name") == record["name"])
        ]
        if not matches and any(
            not any(isinstance(row.get(key), str) and row[key] for key in ("sessionId", "name")) for row in rows
        ):
            # A row with neither identity field could be the target's own row, so absence is not proved.
            return "UNKNOWN"
        for row in matches:
            invalid = [key for key in ("sessionId", "name") if not isinstance(row.get(key), str) or not row[key]]
            if type(row.get("pid")) is not int or row["pid"] <= 1:
                invalid.append("pid")
            if invalid:
                if details is not None:
                    details.append("matching local agent row has invalid " + ", ".join(invalid))
                return "UNKNOWN"
        if len(matches) != 1 or any(matches[0].get(key) != record[key] for key in ("pid", "sessionId", "name")):
            return "FAILED"
        return "VERIFIED"
    except (OSError, ValueError, Refusal, KeyError, TypeError, AttributeError):
        return "UNKNOWN"


def trigger_verdicts(record: dict, commands: dict) -> dict:
    # Passive evidence is shared by check mode and the automatic evaluator.
    installed = Path.home() / ".local" / "bin" / "claude"
    update = "UNKNOWN"
    try:
        target = installed.resolve(strict=True)
        versions = Path.home() / ".local" / "share" / "claude" / "versions"
        current = record.get("version", "")
        if (
            target.parent == versions
            and re.fullmatch(r"\d+(?:\.\d+)+", target.name)
            and re.fullmatch(r"\d+(?:\.\d+)+", current)
        ):
            update = "YES" if tuple(map(int, target.name.split("."))) > tuple(map(int, current.split("."))) else "NO"
    except (OSError, ValueError, TypeError):
        pass
    if update != "YES":
        try:
            _, pane = _pane(record)
            footer = _run(["tmux", "capture-pane", "-p", "-t", pane, "-S", "-12"])
            if "Update installed · Restart to apply" in footer:
                update = "YES"
        except Refusal:
            pass
    local = local_verdict(record)
    peer = observer_verdict(record, commands.get("observer"))
    unreachable = local == "FAILED" or peer == "FAILED"
    # A registry and a peer can agree on a generated/renamed title while
    # disagreeing with the supervisor's configured custom title.
    try:
        session, _ = _pane(record)
        metadata = _object(config.get_xdg_state_home() / f"session-meta-{session}.json")
        expected = metadata.get("ai_name")
        if (
            metadata.get("engine") == "c"
            and metadata.get("session") == session
            and isinstance(expected, str)
            and expected
            and record.get("name") != expected
        ):
            unreachable = True
    except Refusal:
        pass  # Unreadable metadata cannot establish a title mismatch.
    return {
        "update": update,
        "unreachable": "YES" if unreachable else "UNKNOWN" if local == "UNKNOWN" or peer != "VERIFIED" else "NO",
    }


def schedule_auto(session: str, configuration: dict) -> None:
    """Reserve one interval under the shared lock, then spawn without waiting."""
    try:
        settings = config.read_restart_config(configuration)
        if not settings["auto"]:
            return
        lock_path, _, _ = state_paths(session)
        path = lock_path.with_suffix(".auto.json")
        with session_lock(session):
            previous = _object(path) if path.exists() else {}
            last = previous.get("last_evaluation", 0)
            if not _number(last) or time.time() - last < settings["auto_interval_seconds"]:
                return
            # Reserve before releasing ownership so concurrent heartbeats cannot
            # spawn duplicate evaluators. A failed spawn retries next interval.
            _atomic_json(path, {"last_evaluation": time.time()})
        subprocess.Popen(
            [sys.executable, "-m", "ai_cli.main", "internal", "restart-auto", session],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (Refusal, OSError, ValueError, TypeError, subprocess.SubprocessError):
        # Automatic scheduling must never suppress heartbeat publication.
        return


def restart_auto(session: str) -> dict:
    """Evaluate just this pane; only positive evidence enters the guarded path."""
    triggers = {"update": "UNKNOWN", "unreachable": "UNKNOWN"}
    report = _report("unavailable")
    try:
        settings = config.read_restart_config(config.load_config())
        if not settings["auto"]:
            report = _report("disabled")
        else:
            lock_path, _, _ = state_paths(session)
            with session_lock(session):
                _atomic_json(lock_path.with_suffix(".auto.json"), {"last_evaluation": time.time()})
            _, record = resolve_target(session)
            if _pane(record)[0] != session:
                raise Refusal("target")
            triggers = trigger_verdicts(record, load_commands())
            report = (
                restart(record["sessionId"], detached=True, automatic=True, expected_tmux_session=session)
                if any(triggers.get(key) == "YES" for key in ("update", "unreachable"))
                else _report("no-trigger")
            )
    except Refusal as exc:
        report = _report(exc.reason)
    except (OSError, ValueError, TypeError, KeyError):
        report = _report("unavailable")
    # Only protocol verdicts are logged, never adapter output or diagnostics.
    root = config.get_xdg_state_home() / "restart-session"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(root / "auto.log", mode="a", encoding="utf-8", timeout=5) as output:
            output.write(json.dumps({"session": session, "triggers": triggers, "outcome": report["reason"]}) + "\n")
    except (OSError, portalocker.LockException):
        return _report("auto-log-unavailable")
    return report


def _report(reason: str, *, detail: str = "", local: str = "UNVERIFIED", peer: str = "UNVERIFIED", **extra) -> dict:
    return {
        "reason": reason,
        "detail": detail,
        "local": local,
        "peer": peer,
        "mobile": "UNVERIFIED",
        "message_delivery": "UNVERIFIED",
        **extra,
    }


def _reconcile_failed_request(record: dict, check_lock: Callable[[], None]) -> bool:
    """Under the session lock, retire a failed fence only on positive local proof."""
    try:
        session, pane = _pane(record)
        _, result_path, request_path = state_paths(session)
        try:
            request_path.stat()
        except FileNotFoundError:
            return True
        request = _object(request_path)
        if request.get("status") != "failed":
            return True
        result = _object(result_path)
        pid = record["pid"]
        supervisor = request["supervisor_pid"]
        probe = probe_for()
        if (
            any(type(value) is not int or value <= 1 for value in (pid, supervisor, request["old_agent_pid"]))
            or record.get("tmux") != request["tmux"]
            or record.get("sessionId") != request["session_id"]
            or result.get("session_id") != request["session_id"]
            or not _number(result.get("last_restart"))
            or pid == request["old_agent_pid"]
            or probe.has_ended(pid)
            or probe.start_time_match(pid, normalize_proc_start(record.get("procStart"))) is not StartTimeMatch.MATCH
        ):
            return False
        shown = (
            _run(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    pane,
                    "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{@ai_cli_session_generation}",
                ]
            )
            .strip()
            .split("\t")
        )
        if (
            shown != [session, pane, str(supervisor), request["generation"]]
            or not _same(supervisor, request["supervisor_identity"])
            or supervisor not in [parent.pid for parent in psutil.Process(pid).parents()]
            or local_verdict(record) != "VERIFIED"
        ):
            return False
        check_lock()
        if _object(request_path) != request or _object(result_path) != result:
            return False
    except Exception:
        # Probe failures, including unexpected ones, cannot authorize recovery.
        return False
    reconciled = {
        **request,
        "status": "complete",
        "reconciliation": {
            "time": time.time(),
            "replacement_pid": pid,
            "evidence": [
                "pane",
                "session-id",
                "replacement-pid",
                "process-start",
                "supervisor-identity",
                "supervisor-ancestry",
                "local-verdict",
            ],
        },
    }
    # Publish the result first: an interrupted update must retain the request fence.
    try:
        _atomic_json(result_path, {**result, "failed": False, "reason": "reconciled"})
        _atomic_json(request_path, reconciled)
    except Exception as exc:
        # A rename can succeed before fsync raises; restore either changed document.
        if _object(request_path) != request:
            _atomic_json(request_path, request)
        if _object(result_path) != result:
            _atomic_json(result_path, result)
        raise Refusal("restart-fenced", "reconciliation could not be persisted") from exc
    return True


def _check(record: dict, settings: dict, commands: dict) -> dict:
    gates = {}
    cwd = Path(record.get("cwd") or Path.cwd())
    for name, check in (
        ("live-supervisor-uptime", lambda: managed_gate(record, settings)),
        ("boot-recipe", lambda: boot_recipe_gate(record)),
        ("rc-activation", lambda: activation_gate(cwd)),
        ("idle-background-transcript", lambda: idle_gate(record, transcript_tail(transcript_path(record)), settings)),
        ("background-descendants", lambda: descendants_gate(record["pid"], cwd)),
    ):
        try:
            check()
            gates[name] = "PASS"
        except Refusal as exc:
            gates[name] = exc.reason
    try:
        gates["context"] = "PASS" if context_gate(transcript_tail(transcript_path(record)), settings) else "context"
    except Refusal as exc:
        gates["context"] = exc.reason
    session, _ = _pane(record)
    _, result_path, request_path = state_paths(session)
    gates.update({"lock": "UNKNOWN", "cooldown": "UNKNOWN", "failure-fence": "UNKNOWN"})
    try:
        with session_lock(session) as check_lock:
            gates["lock"] = "PASS"
            reconciled = _reconcile_failed_request(record, check_lock)
            result = _object(result_path) if result_path.exists() else {}
            gates["cooldown"] = (
                "cooldown" if time.time() - result.get("last_restart", 0) < settings["cooldown_seconds"] else "PASS"
            )
            gates["failure-fence"] = (
                "restart-fenced"
                if not reconciled or (request_path.exists() and _object(request_path).get("status") != "complete")
                else "PASS"
            )
    except Refusal as exc:
        gates["lock"] = exc.reason
    gates["mechanism"] = (
        "PASS"
        if (settings["mechanism"] == "kill" and _signal_supported()) or commands.get("inject")
        else "no-mechanism"
    )
    passed = all(value == "PASS" for value in gates.values())
    return _report("ready" if passed else "refused", gates=gates, triggers=trigger_verdicts(record, commands))


def _fresh(record_path: Path, original: dict) -> dict:
    current = _object(record_path)
    for key in ("pid", "procStart", "sessionId", "tmux", "name", "cwd"):
        if current.get(key) != original.get(key):
            raise Refusal("identity", "target changed during restart preparation")
    return current


def _verify_replacement(old: dict, settings: dict, commands: dict) -> dict:
    deadline = time.monotonic() + settings["relaunch_timeout_seconds"]
    while True:
        local_details: list[str] = []
        candidates = [row for _, row in records() if row.get("tmux") == old["tmux"] and row.get("pid") != old["pid"]]
        for row in candidates:
            probe = probe_for()
            if not isinstance(row.get("pid"), int) or probe.has_ended(row["pid"]):
                continue
            if (
                probe.start_time_match(row["pid"], normalize_proc_start(row.get("procStart")))
                is not StartTimeMatch.MATCH
            ):
                continue
            if row.get("sessionId") != old["sessionId"]:
                return _report("resume-mismatch", local="FAILED")
            if row.get("name") == old["name"] and local_verdict(row, details=local_details) == "VERIFIED":
                peer = observer_verdict(row, commands.get("observer"))
                session, pane = _pane(old)
                request = _object(state_paths(session)[2])
                shown = (
                    _run(
                        [
                            "tmux",
                            "display-message",
                            "-p",
                            "-t",
                            pane,
                            "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{@ai_cli_session_generation}",
                        ]
                    )
                    .strip()
                    .split("\t")
                )
                if (
                    shown != [session, pane, str(request["supervisor_pid"]), request["generation"]]
                    or not _same(request["supervisor_pid"], request["supervisor_identity"])
                    or request["supervisor_pid"] not in [parent.pid for parent in psutil.Process(row["pid"]).parents()]
                    or local_verdict(row) != "VERIFIED"
                ):
                    return _report(
                        "verification-failed", detail="replacement ownership changed", local="FAILED", peer=peer
                    )
                return _report(
                    "restarted"
                    if peer == "VERIFIED"
                    else "verification-failed"
                    if peer == "FAILED"
                    else "verification-incomplete",
                    local="VERIFIED",
                    peer=peer,
                    replacement_pid=row["pid"],
                )
        if time.monotonic() >= deadline:
            detail = "replacement or local bridge verification timed out"
            if local_details:
                detail += "; " + "; ".join(local_details)
            return _report("verification-failed", detail=detail, local="FAILED")
        time.sleep(POLL_SECONDS)


def _act(record_path: Path, record: dict, managed: Managed, request: dict, settings: dict, commands: dict) -> None:
    session, _ = _pane(record)
    _, _, request_path = state_paths(session)
    request["status"] = "acting"
    _atomic_json(request_path, request)
    # Every dangerous action is below the same final guards, under the lock.
    current = _fresh(record_path, record)
    latest = assess(current, settings, commands, prepared=managed)
    if (latest.supervisor_identity, latest.child_identity, latest.agent_identity, latest.generation) != (
        managed.supervisor_identity,
        managed.child_identity,
        managed.agent_identity,
        managed.generation,
    ):
        raise Refusal("identity", "process ownership changed")
    _validate_request(request, session, child=False)
    mechanism = settings["mechanism"]
    if mechanism == "exit" or not _signal_supported():
        if not commands.get("inject"):
            raise Refusal("no-mechanism")
        rows = transcript_tail(transcript_path(current))
        idle_gate(_fresh(record_path, record), rows, settings)
        _inject_exit(record, commands)
        return
    try:
        if (
            probe_for().start_time_match(record["pid"], normalize_proc_start(current.get("procStart")))
            is not StartTimeMatch.MATCH
        ):
            raise Refusal("identity", "agent birth changed before signal")
        rows = transcript_tail(transcript_path(current))
        idle_gate(_fresh(record_path, record), rows, settings)
        os.kill(record["pid"], signal.SIGTERM)
    except ProcessLookupError:
        raise Refusal("identity", "agent exited before signal") from None
    except OSError:
        # Failed signal access is the fallback, never a group/tree signal.
        current = _fresh(record_path, record)
        assess(current, settings, commands, prepared=managed)
        _validate_request(request, session, child=False)
        if not commands.get("inject"):
            raise Refusal("no-mechanism") from None
        rows = transcript_tail(transcript_path(current))
        idle_gate(_fresh(record_path, record), rows, settings)
        _inject_exit(record, commands)
        return
    deadline = time.monotonic() + settings["term_grace_seconds"]
    probe = probe_for()
    while not probe.has_ended(record["pid"]) and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    if not probe.has_ended(record["pid"]):
        # Escalate only to that same birth identity, after all gates still pass.
        current = _fresh(record_path, record)
        assess(current, settings, commands, prepared=managed)
        _validate_request(request, session, child=False)
        if (
            probe.start_time_match(record["pid"], normalize_proc_start(record.get("procStart")))
            is not StartTimeMatch.MATCH
        ):
            raise Refusal("identity")
        rows = transcript_tail(transcript_path(current))
        idle_gate(_fresh(record_path, record), rows, settings)
        os.kill(record["pid"], signal.SIGKILL)


def _inject_exit(record: dict, commands: dict) -> None:
    try:
        _run(_argv(commands["inject"], record, "/exit"))
    except Refusal as exc:
        raise Refusal("no-mechanism", "exit injection failed") from exc


def _inject_save_state(record: dict, commands: dict) -> tuple[str, bool]:
    try:
        result = subprocess.run(
            _argv(commands["inject"], record, "/save-state"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise Refusal("context", "save-state injection unavailable or timed out") from exc
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {}
    # Only protocol enums are printable. Adapter diagnostics/evidence may contain secrets.
    state = data.get("state") if isinstance(data, dict) else None
    accepted = ("SENT", "STARTED", "COMPLETED")
    known = (*accepted, "QUEUED", "HELD", "REFUSED", "AMBIGUOUS", "BOUND", "CORRUPTED", "DROPPED")
    outcome = f"injection outcome: {state if state in known else 'UNKNOWN'} (exit {result.returncode})"
    # The queued landing certifies a matching transcript enqueue; an attempt id alone does not.
    queued = state == "QUEUED" and result.returncode == 2 and data.get("landing") == "queued"
    delivered = state in accepted and result.returncode == 0
    if not (delivered or queued) or data.get("submitted") is not True:
        raise Refusal("context", outcome)
    return outcome, queued


def restart(
    target: str | None,
    *,
    check: bool = False,
    detached: bool = False,
    automatic: bool = False,
    expected_tmux_session: str | None = None,
) -> dict:
    """One command path; callers never acquire a lock on a helper's behalf."""
    try:
        settings = config.read_restart_config(config.load_config())
        commands = load_commands()
        record_path, record = resolve_target(target)
        session, pane = _pane(record)
        if expected_tmux_session is not None and session != expected_tmux_session:
            raise Refusal("target", "automatic target moved to another tmux session")
        if check:
            return _check(record, settings, commands)
        if not detached and pane == os.environ.get("TMUX_PANE"):
            # Pin the target as the conversation id across detachment, not its mutable title.
            subprocess.Popen(
                [sys.executable, "-m", "ai_cli.main", "internal", "restart-detached", record["sessionId"]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return _report("detached")
        with session_lock(session) as check_lock:
            _, result_path, request_path = state_paths(session)
            _reconcile_failed_request(record, check_lock)
            previous = _object(result_path) if result_path.exists() else {}
            if time.time() - previous.get("last_restart", 0) < settings["cooldown_seconds"]:
                raise Refusal("cooldown")
            if (
                automatic
                and previous.get("session_id") == record["sessionId"]
                and previous.get("failed")
                and (
                    local_verdict(record) != "VERIFIED"
                    or observer_verdict(record, commands.get("observer")) != "VERIFIED"
                )
            ):
                raise Refusal("previous-verification-failed")
            if detached:
                deadline = time.monotonic() + settings["compaction_wait_seconds"]
                while True:
                    record = _fresh(record_path, record)
                    try:
                        idle_gate(record, transcript_tail(transcript_path(record)), settings)
                        break
                    except Refusal as exc:
                        if exc.reason != "busy" or time.monotonic() >= deadline:
                            raise
                    time.sleep(POLL_SECONDS)
            managed = assess(record, settings, commands, pre_step=True, record_path=record_path, check_lock=check_lock)
            if (settings["mechanism"] == "exit" or not _signal_supported()) and not commands.get("inject"):
                raise Refusal("no-mechanism")
            request = prepare_request(record, managed, settings)
            result = {
                "session_id": record["sessionId"],
                "last_restart": time.time(),
                "failed": True,
                "reason": "interrupted",
            }
            # Durable attempt precedes action. A crash never permits an automatic retry.
            _atomic_json(result_path, result)
            try:
                _act(record_path, record, managed, request, settings, commands)
                report = _verify_replacement(record, settings, commands)
            except Refusal as exc:
                report = _report(exc.reason, detail=exc.detail)
            except (OSError, ValueError, KeyError, TypeError, psutil.Error):
                report = _report("verification-failed", detail="replacement evidence became unavailable")
            result.update(reason=report["reason"], failed=report["reason"] != "restarted")
            _atomic_json(result_path, result)
            request["status"] = "complete" if report["reason"] == "restarted" else "failed"
            request["result"] = report["reason"]
            _atomic_json(request_path, request)
            return report
    except Refusal as exc:
        return _report(exc.reason, detail=exc.detail)
    except (OSError, ValueError, TypeError) as exc:
        return _report("unavailable", detail=type(exc).__name__)
