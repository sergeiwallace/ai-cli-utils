"""Versioned, client-confirmed identity for numbered interactive Codex slots.

Only identity metadata is retained. Process identity guards launch collisions,
not authorization; attaching to a live pane stays independent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tomllib
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
    path: Path, cwd: Path, slot: str, *, fresh: bool, route: str, client_version: str
) -> tuple[str, str, str, str]:
    """Reserve this process's launch while preserving committed identity."""
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
        elif not fresh or _client().records_for(cwd):
            raise IdentityError("Codex slot has no client-confirmed thread; refusing directory-based adoption")
        else:
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
            )
            if args[0] == "launch":
                os.environ[SLOT_RECORD_ENV] = record
                os.environ[LAUNCH_TOKEN_ENV] = token
                from .codex_control import start

                start(Path(record))
                command = ["codex", "-c", config]
                if target != "new":
                    command.extend(["resume", target])
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
