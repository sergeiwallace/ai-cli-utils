"""Version 1 live Antigravity (agy) slot attestation bridge; delivery remains on the TUI's pane.

The bridge is a child of the exec-preserving launch trampoline. For fresh launches it
learns the conversation only from the existing SessionStart callback, with a current
launch token and a kernel-proved descendant of that owner. For corroborated
``--conversation <id>`` resumes (where ``agy`` does not re-fire ``SessionStart``), it
pre-binds the already-confirmed conversation from the slot record and invalidates that
binding immediately if the record changes. No native goal RPC is emulated.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil

from . import output
from .output import Untagged


def contract() -> dict:
    return {
        "schema_version": 1,
        "record_glob": "*.agy.*.control-v1.json",
        "directory": "ai-cli-utils/sessions",
        "transport": "unix-json-line",
        "methods": ["get", "confirm"],
        "max_bytes": 65536,
        "delivery": "tmux-pane",
        "slot_schema_version": 1,
    }


def identity(pid: int) -> dict:
    process = psutil.Process(pid)
    if process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
        raise ValueError("dead process")
    return {"pid": pid, "procStart": f"{process.create_time():.6f}"}


def live(value: dict) -> bool:
    try:
        return identity(value["pid"]) == value
    except (psutil.Error, OSError, ValueError, KeyError, TypeError):
        return False


def _peer(connection: socket.socket) -> tuple[int, int]:
    # Platform-specific peer credential extraction on POSIX Unix-domain sockets.
    if sys.platform.startswith("linux"):
        pid, uid, _gid = struct.unpack("iII", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return pid, uid
    if sys.platform == "darwin":
        import ctypes

        (pid,) = struct.unpack("i", connection.getsockopt(0, 2, 4))
        uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
        if ctypes.CDLL(None).getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise OSError("peer credentials unavailable")
        return pid, uid.value
    raise OSError("peer credentials unavailable")


def request(path: str, message: dict) -> dict:
    # Unix-domain sockets are POSIX-only; Windows does not start a live bridge.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(path)
        connection.sendall(json.dumps(message).encode() + b"\n")
        with connection.makefile("rb") as reader:
            raw = reader.readline(contract()["max_bytes"] + 1)
        if len(raw) > contract()["max_bytes"] or not raw.endswith(b"\n"):
            raise ValueError("invalid control reply")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("invalid control reply")
        return result


def notify(record: Path, token: str) -> None:
    """Notify a live bridge of confirmation; a missing bridge preserves diagnostic prepare behavior."""
    if os.name != "posix":
        return
    generation = hashlib.sha256(token.encode()).hexdigest()
    path = record.with_name(f"{record.stem}.{generation}.control-v1.json")
    if not path.exists():
        return
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if request(metadata["path"], {"method": "confirm", "token": token}) != {"confirmed": True}:
        raise ValueError("live control confirmation refused")


def start(record: Path) -> bool:
    """Start the listener before exec and report whether it pre-bound the resume target."""
    if os.name != "posix" or not os.environ.get("TMUX_PANE"):
        return False
    child = subprocess.Popen(
        [sys.executable, "-m", "ai_cli.agy_control", str(record)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        reply = child.stdout.readline(32) if ready else b""
        if reply not in {b"ready\n", b"ready bound\n"}:
            raise ValueError("live control bridge unavailable")
        return reply == b"ready bound\n"
    except BaseException:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        raise
    finally:
        if child.stdout is not None:
            child.stdout.close()


def _binding_from(doc: dict) -> dict | None:
    if doc.get("phase") == "confirmed" and isinstance(doc.get("conversation_id"), str) and doc["conversation_id"]:
        return {key: doc.get(key) for key in ("conversation_id", "transcript_path", "cwd")}
    return None


def serve(record: Path) -> None:
    from .agy_session import _read

    doc = _read(record)
    token = doc["in_flight"]["token"]
    owner = identity(os.getppid())
    if owner["pid"] != doc["in_flight"]["writer"]["pid"]:
        raise ValueError("bridge owner mismatch")
    generation = hashlib.sha256(token.encode()).hexdigest()
    published = record.with_name(f"{record.stem}.{generation}.control-v1.json")
    # Corroborated --conversation resumes do not re-fire SessionStart; pre-bind their verified ID.
    binding = _binding_from(doc)
    # Own short private directory: macOS Unix sockets have a 104-byte limit.
    with tempfile.TemporaryDirectory(prefix="ai-agy-control-", dir="/tmp") as root:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            path = str(Path(root) / "s")
            listener.bind(path)
            listener.listen(8)
            listener.settimeout(0.2)
            metadata = {
                "schema_version": 1,
                "generation": generation,
                "slot_record": str(record),
                "path": path,
                "owner": owner,
                "listener": identity(os.getpid()),
                "pane_id": os.environ["TMUX_PANE"],
            }
            encoded = json.dumps(metadata) + "\n"
            with published.open("x", encoding="utf-8") as handle:
                published.chmod(0o600)
                handle.write(encoded)
            prebound = (
                binding is not None and binding["conversation_id"] == doc["in_flight"]["expected_conversation_id"]
            )
            output.raw(Untagged.INTERNAL_REPLY, "ready bound" if prebound else "ready")
            try:
                while live(owner) and os.getppid() == owner["pid"]:
                    try:
                        connection, _ = listener.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        connection.settimeout(1)
                        try:
                            pid, uid = _peer(connection)
                            if uid != os.geteuid():
                                continue
                            with connection.makefile("rb") as reader:
                                raw = reader.readline(contract()["max_bytes"] + 1)
                            if len(raw) > contract()["max_bytes"] or not raw.endswith(b"\n"):
                                continue
                            query = json.loads(raw)
                            if not isinstance(query, dict):
                                continue
                            current = _read(record)
                            if current["in_flight"]["token"] != token or not live(owner):
                                break
                            if query.get("method") == "confirm":
                                ancestors = [p.pid for p in psutil.Process(pid).parents()]
                                if (
                                    query.get("token") != token
                                    or owner["pid"] not in ancestors
                                    or current["phase"] != "confirmed"
                                ):
                                    reply = {"confirmed": False}
                                else:
                                    binding = _binding_from(current)
                                    reply = {"confirmed": binding is not None}
                            elif query.get("method") == "get":
                                if binding is not None and any(
                                    current.get(key) != value for key, value in binding.items()
                                ):
                                    binding = None
                                reply = {
                                    **metadata,
                                    "phase": "waiting" if binding else "unconfirmed",
                                    "binding": binding,
                                }
                            else:
                                reply = {"refused": True}
                            connection.sendall(json.dumps(reply).encode() + b"\n")
                        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
                            continue
            finally:
                # Delete only this generation's unchanged publication.
                if published.is_file() and published.read_text(encoding="utf-8") == encoded:
                    published.unlink()


if __name__ == "__main__":
    try:
        serve(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        raise SystemExit(1) from None
