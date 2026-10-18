"""Restart one idle, supervised Claude Code process without replacing its pane.

Only this module's guarded act path signals a PID. Prompt injection and peer
observation are optional argv adapters, never shell strings. Unknown evidence
refuses an action. Windows uses injection because POSIX signals are unsupported.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeGuard

import portalocker
import psutil

from . import config
from .process_probe import ProcessIdentity, StartTimeMatch, probe_for

TAIL_LIMIT = 8 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
POLL_SECONDS = 0.2
# Keep unknown models unknown. These base windows match the existing statusline
# model table; extended windows require an explicit tag or configured size.
MODEL_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-haiku-4-5": 200_000,
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
        try:
            yield
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


def transcript_tail(path: Path) -> list[dict]:
    """Read backwards in bounded chunks. A truncated history cannot exclude old shells."""
    try:
        with path.open("rb") as source:
            size = source.seek(0, os.SEEK_END)
            if size > TAIL_LIMIT:
                raise Refusal("background-work", "background history exceeds bounded evidence window")
            chunks = []
            offset = size
            while offset:
                length = min(offset, CHUNK_SIZE)
                offset -= length
                source.seek(offset)
                chunks.append(source.read(length))
            raw = b"".join(reversed(chunks))
        if not raw.endswith(b"\n"):
            raise ValueError("incomplete record")
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError("invalid transcript")
        if any(
            not isinstance(row.get("message", {}), dict) or not isinstance(row.get("compactMetadata", {}), dict)
            for row in rows
        ):
            raise ValueError("invalid transcript evidence shape")
        return [row for row in rows if not row.get("isSidechain")]
    except (OSError, ValueError) as exc:
        raise Refusal("background-work", "transcript evidence unreadable") from exc


def idle_gate(record: dict, rows: list[dict], settings: dict) -> None:
    try:
        quiet = time.time() - _timestamp(record.get("statusUpdatedAt"))
    except (ValueError, TypeError, OverflowError):
        quiet = -1
    if record.get("status") != "idle" or record.get("waitingFor") or quiet < settings["settle_seconds"]:
        raise Refusal("busy", "registry is not settled idle")
    # Metadata/notifications can follow a completed turn; user/assistant/tool records cannot.
    chain = [row for row in rows if row.get("type") in ("user", "assistant", "system")]
    if not chain or chain[-1].get("subtype") != "turn_duration":
        raise Refusal("busy", "transcript has no final turn boundary")
    pending_agents = chain[-1].get("pendingBackgroundAgentCount")
    if type(pending_agents) is not int or pending_agents != 0:
        raise Refusal("background-work", "pending agent counter is positive or unavailable")
    launched: set[str] = set()
    completed: set[str] = set()
    for row in rows:
        result = row.get("toolUseResult")
        if isinstance(result, dict):
            if result.get("backgroundTaskId"):
                launched.add(str(result["backgroundTaskId"]))
            if result.get("status") == "async_launched":
                if not result.get("agentId"):
                    raise Refusal("background-work", "agent launch has no identity")
                launched.add(str(result["agentId"]))
        # Notifications occur as user content in current transcripts, or as structured records.
        content = row.get("message", {}).get("content", [])
        text = content if isinstance(content, str) else json.dumps(content)
        if "task-notification" in text and re.search(r"<status>(completed|failed|killed)</status>", text):
            for task_id in re.findall(r"<task-id>([^<]+)</task-id>", text):
                completed.add(task_id)
        if row.get("type") == "task-notification" and row.get("status") in ("completed", "failed", "killed"):
            completed.add(str(row.get("taskId", "")))
    if launched - completed:
        raise Refusal("background-work", "unfinished background launch")


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
    latest = next((row for row in reversed(following) if row.get("type") == "assistant"), {})
    message = latest.get("message", {})
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return False
    tokens: list[int | float] = []
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        token = usage.get(key)
        if not _number(token) or token < 0:
            return False
        tokens.append(token)
    window = settings["context_window_tokens"]
    model = message.get("model", "")
    if not window:
        window = MODEL_WINDOWS.get(model, 0)
        if str(model).endswith("[1m]") and str(model).removesuffix("[1m]") in MODEL_WINDOWS and "haiku" not in model:
            window = 1_000_000
    return bool(window and 100 * sum(tokens) / window <= settings["context_threshold_pct"])


def context_pre_step(record: dict, settings: dict, commands: dict) -> str:
    """Integration seam for the separately implemented save-state pre-step."""
    return "pre-step unavailable"


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


def descendants_gate(pid: int, cwd: Path) -> None:
    """Allow only configured MCP server roots and their own descendants."""
    try:
        commands = []
        paths = [Path.home() / ".claude.json", cwd / ".mcp.json"]
        for path in paths:
            if path.exists():
                data = _object(path)
                servers = data.get("mcpServers", {})
                if not isinstance(servers, dict):
                    raise Refusal("background-work", "MCP configuration unreadable")
                for server in servers.values():
                    if isinstance(server, dict) and isinstance(server.get("command"), str):
                        commands.append([server["command"], *server.get("args", [])])
        children = psutil.Process(pid).children(recursive=True)
        allowed = set()
        pending = {child.pid: child for child in children}
        while pending:
            progress = False
            for child_id, child in list(pending.items()):
                if child.ppid() in allowed or child.cmdline() in commands:
                    allowed.add(child_id)
                    del pending[child_id]
                    progress = True
            if not progress:
                raise Refusal("background-work", "unclassified live descendant")
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
        text = re.sub(r"(?ms)^    # BEGIN PINNED RESTART.*?^    # END PINNED RESTART\n", "", text)
        text = re.sub(r"(?m)^    _template_(?:version|commit)=.*$", "", text)
        return "\n".join(line for line in text.splitlines() if line.strip())

    return normalize(source) == normalize(expected)


def managed_gate(record: dict, settings: dict, *, prepared: Managed | None = None) -> Managed:
    from .main import _engine_script_from_meta

    try:
        session, pane = _pane(record)
        pid = record["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            raise Refusal("identity")
        probe = probe_for()
        if probe.has_ended(pid) or probe.start_time_match(pid, record.get("procStart")) is not StartTimeMatch.MATCH:
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


def assess(
    record: dict, settings: dict, commands: dict, *, pre_step: bool = False, prepared: Managed | None = None
) -> Managed:
    managed = managed_gate(record, settings, prepared=prepared)
    cwd = Path(record.get("cwd", "")).resolve()
    if not record.get("cwd") or not cwd.is_dir():
        raise Refusal("not-managed", "working directory unavailable")
    activation_gate(cwd)
    rows = transcript_tail(transcript_path(record))
    idle_gate(record, rows, settings)
    descendants_gate(record["pid"], cwd)
    if not context_gate(rows, settings):
        detail = (
            context_pre_step(record, settings, commands)
            if pre_step and settings["pre_step"]
            else "pre-step unavailable"
        )
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
        data = json.loads(_run(_argv(command, record)))
        observed = data.get("observed_at")
        moment = _timestamp(observed) if isinstance(observed, str) else observed
        if not _number(moment) or not started <= moment <= time.time() or data.get("complete") is not True:
            return "UNKNOWN"
        if any(data.get(key) for key in ("truncated", "listTruncated", "bridgeWalkFailed", "localListFailed")):
            return "UNKNOWN"
        rows = data.get("rows")
        if not isinstance(rows, list) or data.get("status") not in ("seen", "absent", "unknown"):
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


def local_verdict(record: dict) -> str:
    try:
        pid = record["pid"]
        probe = probe_for()
        if probe.has_ended(pid) or probe.start_time_match(pid, record.get("procStart")) is not StartTimeMatch.MATCH:
            return "UNKNOWN"
        if record.get("nameSource") != "user" or not record.get("bridgeSessionId"):
            return "FAILED"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(record["messagingSocketPath"])
        data = json.loads(_run(["claude", "agents", "--json"]))
        if isinstance(data, dict):
            if data.get("complete") is not True or any(
                data.get(key) for key in ("listTruncated", "localListFailed", "bridgeWalkFailed", "truncated")
            ):
                return "UNKNOWN"
            rows = data.get("sessions")
        else:
            rows = data
        if not isinstance(rows, list):
            return "UNKNOWN"
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and (row.get("sessionId") == record["sessionId"] or row.get("name") == record["name"])
        ]
        if len(matches) != 1 or any(matches[0].get(key) != record[key] for key in ("pid", "sessionId", "name")):
            return "FAILED"
        return "VERIFIED"
    except (OSError, ValueError, Refusal, KeyError, TypeError, AttributeError):
        return "UNKNOWN"


def trigger_verdicts(record: dict, commands: dict) -> dict:
    # Passive evidence for check mode; scheduling and automatic actions are separate.
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
    local = local_verdict(record)
    peer = observer_verdict(record, commands.get("observer"))
    unreachable = local == "FAILED" or peer == "FAILED"
    return {
        "update": update,
        "unreachable": "YES" if unreachable else "UNKNOWN" if local == "UNKNOWN" or peer != "VERIFIED" else "NO",
    }


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


def _check(record: dict, settings: dict, commands: dict) -> dict:
    gates = {}
    cwd = Path(record.get("cwd") or Path.cwd())
    for name, check in (
        ("live-supervisor-uptime", lambda: managed_gate(record, settings)),
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
        with session_lock(session):
            gates["lock"] = "PASS"
            result = _object(result_path) if result_path.exists() else {}
            gates["cooldown"] = (
                "cooldown" if time.time() - result.get("last_restart", 0) < settings["cooldown_seconds"] else "PASS"
            )
            gates["failure-fence"] = (
                "restart-fenced"
                if request_path.exists() and _object(request_path).get("status") != "complete"
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
        candidates = [row for _, row in records() if row.get("tmux") == old["tmux"] and row.get("pid") != old["pid"]]
        for row in candidates:
            probe = probe_for()
            if not isinstance(row.get("pid"), int) or probe.has_ended(row["pid"]):
                continue
            if probe.start_time_match(row["pid"], row.get("procStart")) is not StartTimeMatch.MATCH:
                continue
            if row.get("sessionId") != old["sessionId"]:
                return _report("resume-mismatch", local="FAILED")
            if row.get("name") == old["name"] and local_verdict(row) == "VERIFIED":
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
            return _report(
                "verification-failed", detail="replacement or local bridge verification timed out", local="FAILED"
            )
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
        _inject_exit(record, commands)
        return
    try:
        if probe_for().start_time_match(record["pid"], current.get("procStart")) is not StartTimeMatch.MATCH:
            raise Refusal("identity", "agent birth changed before signal")
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
        if probe.start_time_match(record["pid"], record.get("procStart")) is not StartTimeMatch.MATCH:
            raise Refusal("identity")
        os.kill(record["pid"], signal.SIGKILL)


def _inject_exit(record: dict, commands: dict) -> None:
    try:
        _run(_argv(commands["inject"], record, "/exit"))
    except Refusal as exc:
        raise Refusal("no-mechanism", "exit injection failed") from exc


def restart(target: str | None, *, check: bool = False, detached: bool = False, automatic: bool = False) -> dict:
    """One command path; callers never acquire a lock on a helper's behalf."""
    try:
        settings = config.read_restart_config(config.load_config())
        commands = load_commands()
        record_path, record = resolve_target(target)
        session, pane = _pane(record)
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
        with session_lock(session):
            _, result_path, request_path = state_paths(session)
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
            managed = assess(record, settings, commands, pre_step=True)
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
