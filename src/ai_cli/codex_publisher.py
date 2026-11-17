"""The launcher side of the Codex notification publisher.

A supervised ``ai cx`` session calls an external publisher, configured as
``[codex.publisher] command``, through synchronous phases so another tool can deliver
notices into the session's Codex thread. The session script runs three steps from here:
``begin`` (before the app-server starts), ``running`` (once it has), and ``monitor`` (in the
background while the TUI runs: it enrolls the thread, refreshes it, and calls finish).

Design: docs/designs/codex-notification-publisher.md.

Depends on: config.py, output.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config as _config
from . import output as _out
from .output import Tag, Untagged

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
RESPONSE_KEYS = frozenset({"schemaVersion", "phase", "status", "reason", "possiblyPersisted", "recordId", "socketPath"})
# A record id is a canonical (lowercase, hyphenated) UUID string.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# The session script's heartbeat ticker sleeps this long between beats; the monitor ticks at
# the same cadence, or at refresh_seconds when that is shorter.
HEARTBEAT_SECONDS = 30
NOTICE = "Codex notifications are unavailable for this session"
_POLL_SECONDS = 0.2
_STATE_RETENTION_SECONDS = 7 * 86400


@dataclass(frozen=True)
class Settings:
    command: tuple[str, ...]
    timeout_ms: int
    refresh_seconds: int
    enroll_window_seconds: int
    remote_flag: str


@dataclass(frozen=True)
class PhaseResult:
    """``outcome``: accepted, pending, unavailable, invalid or uncertain."""

    outcome: str
    reason: str
    response: dict | None = None


def load_settings(config: dict, *, report: bool) -> Settings | None:
    """The publisher settings, or ``None`` when it is off or its config is invalid."""
    try:
        section = _config.read_codex_publisher_config(config)
    except ValueError as exc:
        if report:
            _out.warning(Tag.CONFIG, "load", f"{exc}; Codex notifications are off for this launch")
        return None
    if not section["command"]:
        return None
    return Settings(
        tuple(section["command"]),
        section["timeout_ms"],
        section["refresh_seconds"],
        section["enroll_window_seconds"],
        section["remote_flag"],
    )


# --- one phase ------------------------------------------------------------------------


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON the publisher may send")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _parse_response(raw: bytes, phase: str) -> dict | None:
    """The one response object on stdout when it has exactly the fixed keys, else ``None``."""
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != RESPONSE_KEYS:
        return None
    version = value["schemaVersion"]
    if type(version) is not int or version != SCHEMA_VERSION or value["phase"] != phase:
        return None
    statuses = {"accepted", "unavailable", "pending"} if phase == "enroll" else {"accepted", "unavailable"}
    if value["status"] not in statuses or not isinstance(value["reason"], str):
        return None
    if not isinstance(value["possiblyPersisted"], bool):
        return None
    record_id, socket_path = value["recordId"], value["socketPath"]
    if record_id is not None and not (isinstance(record_id, str) and _UUID.fullmatch(record_id)):
        return None
    if socket_path is not None and not (isinstance(socket_path, str) and Path(socket_path).is_absolute()):
        return None
    return value


def _kill_group(child: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()


def _record_diagnostics(phase: str, stderr: bytes) -> None:
    lines = stderr.decode("utf-8", "replace").splitlines()[:20]
    for line in lines:
        if line.strip():
            _out.emit(Tag.SESSION, "notify", f"publisher {phase}: {line}", err=True, terminal=False)


def run_phase(settings: Settings, phase: str, request: dict) -> PhaseResult:
    """Run ``<command> <phase>`` with ``request`` on stdin, bounded by ``timeout_ms``.

    The child leads its own process group, so a timeout kills it and anything it started,
    and the launcher reaps it. A timeout, or stdout that does not parse against the fixed
    keys, is handled as exit 4: the outcome is uncertain.
    """
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        return PhaseResult("invalid", f"the {phase} request is over {MAX_REQUEST_BYTES} bytes")
    try:
        child = subprocess.Popen(
            [*settings.command, phase],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return PhaseResult("unavailable", f"the publisher could not be started ({exc.strerror or exc})")
    try:
        stdout, stderr = child.communicate(payload, timeout=settings.timeout_ms / 1000)
    except subprocess.TimeoutExpired:
        _kill_group(child)
        return PhaseResult("uncertain", f"the publisher's {phase} did not answer within {settings.timeout_ms} ms")
    _record_diagnostics(phase, stderr)
    response = _parse_response(stdout, phase)
    if response is None:
        return PhaseResult("uncertain", f"the publisher's {phase} answer could not be read")
    status, reason, code = response["status"], response["reason"], child.returncode
    if code == 0 and status == "accepted":
        return PhaseResult("accepted", reason, response)
    if code == 2:
        return PhaseResult("invalid", reason, response)
    if code == 3 and status == "pending" and reason == "thread-not-loaded":
        return PhaseResult("pending", reason, response)
    if code == 3 and status == "unavailable":
        return PhaseResult("unavailable", reason, response)
    return PhaseResult("uncertain", reason or f"exit {code}", response)


# --- per-generation state -------------------------------------------------------------


def _state_dir() -> Path:
    return _config.get_xdg_state_home() / "codex-publisher"


def _state_path(tmux_session: str, generation: str) -> Path:
    digest = hashlib.sha256(f"{tmux_session}\0{generation}".encode()).hexdigest()[:32]
    return _state_dir() / f"{digest}.json"


def _load_state(path: Path) -> dict:
    state = {"notice": False, "closed": False, "record": None}
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state |= {key: loaded[key] for key in state if key in loaded}
    return state


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{os.getpid()}.tmp")
    scratch.write_text(json.dumps(state), encoding="utf-8")
    scratch.replace(path)


def _prune_states(keep: Path) -> None:
    cutoff = time.time() - _STATE_RETENTION_SECONDS
    with contextlib.suppress(OSError):
        for entry in _state_dir().glob("*.json"):
            with contextlib.suppress(OSError):
                if entry != keep and entry.stat().st_mtime < cutoff:
                    entry.unlink()


def _notice(path: Path, state: dict, detail: str) -> None:
    """Say once per run generation that notifications are unavailable."""
    if state["notice"]:
        return
    state["notice"] = True
    _save_state(path, state)
    _out.warning(Tag.SESSION, "notify", f"{NOTICE}: {detail}. The session keeps running.")


def _defect(phase: str) -> str:
    return f"the publisher refused the launcher's {phase} request as invalid, a launcher defect; please report it"


def _finish(settings: Settings, path: Path, state: dict) -> None:
    """Call finish once for the live record, if there is one."""
    record = state.get("record")
    if not record:
        return
    state["record"] = None
    _save_state(path, state)
    result = run_phase(settings, "finish", {"schemaVersion": SCHEMA_VERSION, "recordId": record["recordId"]})
    if result.outcome != "accepted":
        _out.emit(Tag.SESSION, "notify", f"publisher finish: {result.reason}", err=True, terminal=False)


# --- the steps the session script runs ----------------------------------------------


def _git_roots(cwd: str) -> tuple[str, str]:
    def ask(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", cwd, "rev-parse", *args], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    worktree_root = ask("--show-toplevel") or cwd
    common = ask("--path-format=absolute", "--git-common-dir")
    repo_root = str(Path(common).parent) if common else worktree_root
    return worktree_root, repo_root


def begin(config: dict, tmux_session: str, ai_name: str, wrapper_pid: int) -> int:
    """Ask the publisher to begin; print the socket path and return 0 only when it accepts."""
    if sys.platform == "win32":
        # Windows: the publisher's transport is a Unix socket, which this launcher does not use there.
        return 1
    settings = load_settings(config, report=True)
    generation = os.environ.get("AI_CLI_RUN_GENERATION", "")
    if settings is None or not generation:
        return 1
    path = _state_path(tmux_session, generation)
    state = _load_state(path)
    if state["closed"]:
        return 1
    _prune_states(path)
    cwd = str(Path.cwd())
    worktree_root, repo_root = _git_roots(cwd)
    in_tmux = bool(os.environ.get("TMUX"))
    request = {
        "schemaVersion": SCHEMA_VERSION,
        "sessionName": ai_name,
        "launcherRunGeneration": generation,
        "wrapperPid": wrapper_pid,
        "worktreeRoot": worktree_root,
        "cwd": cwd,
        "repoRoot": repo_root,
        "tmuxSession": tmux_session if in_tmux else "",
        "paneId": os.environ.get("TMUX_PANE", "") if in_tmux else "",
    }
    result = run_phase(settings, "begin", request)
    if result.outcome == "accepted" and result.response:
        record_id, socket_path = result.response["recordId"], result.response["socketPath"]
        if record_id and socket_path and Path(socket_path).is_absolute():
            state["record"] = {"recordId": record_id, "socketPath": socket_path}
            _save_state(path, state)
            _out.raw(Untagged.INTERNAL_REPLY, socket_path)
            return 0
        result = PhaseResult("uncertain", "the publisher accepted begin without a record or socket")
    if result.outcome == "unavailable" and (
        result.response is None or result.response["possiblyPersisted"] or result.response["recordId"] is not None
    ):
        # A refusal that may have left a record behind is not a clean no: reconcile it.
        result = PhaseResult("uncertain", result.reason)
    if result.outcome == "invalid":
        state["closed"] = True
        _save_state(path, state)
        detail = result.reason if result.response is None else _defect("begin")
        _notice(path, state, detail)
        return 1
    if result.outcome == "uncertain":
        # Never retry begin for this generation: reconcile the record it may have made, once.
        state["closed"] = True
        _save_state(path, state)
        reconcile = run_phase(
            settings,
            "reconcile",
            {"schemaVersion": SCHEMA_VERSION, "launcherRunGeneration": generation, "wrapperPid": wrapper_pid},
        )
        if reconcile.outcome != "accepted":
            _out.emit(Tag.SESSION, "notify", f"publisher reconcile: {reconcile.reason}", err=True, terminal=False)
    _notice(path, state, result.reason)
    return 1


def _process_token(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def _same_process(pid: int, token: float | None) -> bool:
    return token is not None and _process_token(pid) == token


def _socket_answers(socket_path: str, engine_pid: int, timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    engine = _process_token(engine_pid)
    while time.monotonic() < deadline:
        if not _same_process(engine_pid, engine):
            return False
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(socket_path)
        except OSError:
            time.sleep(0.05)
        else:
            return True
        finally:
            probe.close()
    return False


def _record_or_none(tmux_session: str) -> tuple[Path, dict, dict | None]:
    path = _state_path(tmux_session, os.environ.get("AI_CLI_RUN_GENERATION", ""))
    state = _load_state(path)
    return path, state, state.get("record")


def running(config: dict, tmux_session: str, engine_pid: int) -> int:
    """Prove the engine to the publisher; print the TUI's attach arguments on acceptance."""
    settings = load_settings(config, report=False)
    path, state, record = _record_or_none(tmux_session)
    if settings is None or not record:
        return 1
    if not _socket_answers(record["socketPath"], engine_pid, settings.timeout_ms):
        _finish(settings, path, state)
        _notice(path, state, f"the Codex app-server did not answer on its socket within {settings.timeout_ms} ms")
        return 1
    result = run_phase(
        settings, "running", {"schemaVersion": SCHEMA_VERSION, "recordId": record["recordId"], "enginePid": engine_pid}
    )
    if result.outcome == "accepted":
        _out.raw(Untagged.INTERNAL_REPLY, f"{settings.remote_flag}\nunix://{record['socketPath']}")
        return 0
    _finish(settings, path, state)
    _notice(path, state, _defect("running") if result.outcome == "invalid" and result.response else result.reason)
    return 1


def monitor(config: dict, tmux_session: str, wrapper_pid: int, engine_pid: int) -> int:
    """Enroll the session's thread, then refresh it, until the session script stops this.

    SIGTERM (the TUI has exited) calls finish; the session script then stops the engine. If
    the session script itself is gone, this stops the engine and does not call finish: no
    caller is left that descends from the wrapper the record names.
    """
    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(requested=True))
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    settings = load_settings(config, report=False)
    path, state, record = _record_or_none(tmux_session)
    if settings is None or not record:
        return 1
    wrapper, engine = _process_token(wrapper_pid), _process_token(engine_pid)
    request = {"schemaVersion": SCHEMA_VERSION, "recordId": record["recordId"], "enginePid": engine_pid}
    tick = min(HEARTBEAT_SECONDS, settings.refresh_seconds)
    started = time.monotonic()
    next_tick = started + tick
    enrolled = False
    last_refresh = started
    active = True
    while True:
        if stop["requested"]:
            _finish(settings, path, state)
            return 0
        if not _same_process(wrapper_pid, wrapper):
            # The session shell is gone, so no caller can prove it descends from it: stop the
            # engine and leave the record to the publisher's own stale-record cleanup.
            if _same_process(engine_pid, engine):
                with contextlib.suppress(OSError):
                    os.kill(engine_pid, signal.SIGTERM)
            _notice(path, state, "the session shell exited before the session ended")
            return 0
        now = time.monotonic()
        if active and now >= next_tick:
            next_tick = now + tick
            if not enrolled:
                if now - started >= settings.enroll_window_seconds:
                    _finish(settings, path, state)
                    _notice(
                        path,
                        state,
                        f"no Codex thread was loaded within enroll_window_seconds ({settings.enroll_window_seconds})",
                    )
                    active = False
                else:
                    result = run_phase(settings, "enroll", request)
                    if result.outcome == "accepted":
                        enrolled, last_refresh = True, time.monotonic()
                    elif result.outcome != "pending":
                        active = _give_up(settings, path, state, "enroll", result)
            elif now - last_refresh >= settings.refresh_seconds:
                result = run_phase(settings, "refresh", request)
                if result.outcome == "accepted":
                    last_refresh = time.monotonic()
                else:
                    active = _give_up(settings, path, state, "refresh", result)
        time.sleep(_POLL_SECONDS)


def _give_up(settings: Settings, path: Path, state: dict, phase: str, result: PhaseResult) -> bool:
    _finish(settings, path, state)
    _notice(path, state, _defect(phase) if result.outcome == "invalid" and result.response else result.reason)
    return False


USAGE = (
    "Usage: ai internal codex-publisher begin <tmux_session> <ai_name> <wrapper_pid>\n"
    "       ai internal codex-publisher running <tmux_session> <wrapper_pid> <engine_pid>\n"
    "       ai internal codex-publisher monitor <tmux_session> <wrapper_pid> <engine_pid>"
)


def main(config: dict, argv: list[str]) -> int:
    """``ai internal codex-publisher <step> ...``, run by the session script."""
    if len(argv) == 4 and argv[0] == "begin" and argv[3].isdigit():
        return begin(config, argv[1], argv[2], int(argv[3]))
    if len(argv) == 4 and argv[0] in ("running", "monitor") and argv[2].isdigit() and argv[3].isdigit():
        if argv[0] == "running":
            return running(config, argv[1], int(argv[3]))
        return monitor(config, argv[1], int(argv[2]), int(argv[3]))
    _out.emit(Tag.INTERNAL, "usage", USAGE, err=True)
    return 1
