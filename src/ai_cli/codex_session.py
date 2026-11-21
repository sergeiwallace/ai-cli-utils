"""Versioned, client-confirmed identity for numbered interactive Codex slots.

Only SessionStart identity metadata is retained. This is a launcher boundary,
not a liveness or authorization authority; attaching to a live pane stays independent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tomllib
from pathlib import Path

import portalocker

from . import output
from .output import Untagged
from .session_clients import PRESENT, Codex

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = {"0.169.0"}
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
    return doc


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
    """Return target, stable hook, record path and token; leave launch pending."""
    path = path.resolve()
    if "\n" in str(path) or "\r" in str(path):
        raise IdentityError("Codex slot path cannot be represented in the launcher reply")
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path) if path.exists() else None
        target = "new"
        if doc is not None:
            if doc.get("slot") != slot or doc.get("cwd") != str(cwd.resolve()):
                raise IdentityError("Codex slot identity does not match this launch")
            if doc.get("phase") != "confirmed" or not _thread_id(doc.get("thread_id")):
                raise IdentityError("Codex slot is pending client confirmation; refusing to guess a thread")
            if not doc.get("rollout_path"):
                raise IdentityError("Codex thread is live-only or ephemeral; no persisted resume is proved")
            probe = _client().probe_thread(cwd, doc["thread_id"])
            if probe.state != PRESENT:
                raise IdentityError(probe.reason)
            target = doc["thread_id"]
        elif not fresh or _client().records_for(cwd):
            raise IdentityError("Codex slot has no client-confirmed thread; refusing directory-based adoption")
        doc = {
            "schema_version": SCHEMA_VERSION,
            "slot": slot,
            "cwd": str(cwd.resolve()),
            "phase": "pending",
            "thread_id": None if target == "new" else target,
            "rollout_path": None,
            "client_version": client_version,
            "execution_mode": "interactive",
            "route": route,
            "result": "pending" if client_version in SUPPORTED_VERSIONS else "unknown",
            "launch_token": secrets.token_hex(16),
        }
        _write(path, doc)
        if client_version not in SUPPORTED_VERSIONS:
            raise IdentityError("Codex client version is unsupported; identity result is unknown")
        return target, HOOK_DECLARATION, str(path), doc["launch_token"]


def confirm(path: Path, token: str, event: object) -> None:
    """Consume a native SessionStart, binding it to this pending launch only."""
    if not token or not path.is_file():
        raise IdentityError("missing Codex launch confirmation identity")
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path)
        if doc.get("launch_token") != token:
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
            or (doc.get("thread_id") is not None and doc["thread_id"] != thread)
            or not (rollout is None or (isinstance(rollout, str) and bool(rollout)))
            or "transcript_path" not in event
        ):
            raise IdentityError("Codex confirmation identity is incompatible with the launch")
        if doc.get("client_version") not in SUPPORTED_VERSIONS:
            raise IdentityError("unsupported Codex client confirmation")
        doc.update(
            phase="confirmed", thread_id=thread, rollout_path=rollout, result="confirmed" if rollout else "live-only"
        )
        _write(path, doc)


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
        if len(args) == 6 and args[0] == "prepare":
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
            output.raw(Untagged.INTERNAL_REPLY, target)
            output.raw(Untagged.INTERNAL_REPLY, config)
            output.raw(Untagged.INTERNAL_REPLY, record)
            output.raw(Untagged.INTERNAL_REPLY, token)
        else:
            raise IdentityError("invalid Codex identity operation")
    except IdentityError as exc:
        output.error(output.Tag.SESSION, "session", f"Codex identity unknown: {exc}")
        return 1
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
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
