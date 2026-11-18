"""Bound lifecycle CLI processes and retain a final-exit veto independently of them."""

import contextlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import output
from . import session_registry as registry

EXIT_PERSISTENCE_DEFAULTS = {
    "deadline_seconds": 30.0,
    "command_timeout_seconds": 10.0,
    "backoff_seconds": 0.25,
    "max_backoff_seconds": 2.0,
}
POLL_SECONDS = 0.05
_PYTHON = Path(sys.executable).absolute()
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def persistence_settings(cfg: dict) -> dict:
    """Resolve the existing configuration table before the project environment loads."""
    result = EXIT_PERSISTENCE_DEFAULTS | cfg.get("session_relaunch", {}).get("exit_persistence", {})
    for key in EXIT_PERSISTENCE_DEFAULTS:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"session_relaunch.exit_persistence.{key} must be a positive finite number")
    if result["backoff_seconds"] > result["max_backoff_seconds"]:
        raise ValueError("session_relaunch.exit_persistence.backoff_seconds exceeds max_backoff_seconds")
    return result


def pinned_python(code: str) -> list[str]:
    """Use the loaded installation, ignoring PATH, PYTHONPATH and the project directory."""
    if not _PYTHON.is_file() or not (_PACKAGE_ROOT / "ai_cli" / "main.py").is_file():
        raise ValueError("the lifecycle Python installation is unavailable")
    # Forward slashes: a POSIX shell layer on Windows halves doubled backslashes in arguments.
    root = _PACKAGE_ROOT.as_posix()
    return [_PYTHON.as_posix(), "-I", "-c", f"import sys; sys.path.insert(0, {root!r}); {code}"]


def _stop(process: subprocess.Popen) -> None:
    # Each CLI owns a fresh process group on POSIX, including any helpers it spawns.
    # macOS answers EPERM, not ESRCH, when every process left in the group is a zombie:
    # a CLI that exited after the last poll must not abort the deny-boot write.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    process.communicate()


def _call(command: list[str], deadline: float, interrupted: list[bool]) -> str | None:
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=os.name != "nt"
        )
    except OSError:
        return None
    while not interrupted[0] and time.monotonic() < deadline:
        try:
            stdout, _ = process.communicate(timeout=min(POLL_SECONDS, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            continue
        return stdout.decode("utf-8", errors="replace").strip() if process.returncode == 0 else None
    _stop(process)
    return None


def store_safety(context: dict, name: str, generation: str, cause: str, evidence: dict) -> None:
    """Write the existing safety format without invoking the CLI or taking its registry lock."""
    # A superseded supervisor must never overwrite its replacement's safety record.
    try:
        doc = json.loads(Path(context["registry_path"]).read_text(encoding="utf-8"))
        current = next(r for r in doc["sessions"] if r.get("kind") == "local" and r.get("name") == name)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
        current = {}
    if current.get("generation", generation) != generation:
        return
    now = registry._now()
    exit_record = {"cause": cause, "evidence": evidence, "at": now}
    record = {
        "generation": generation,
        "transition_seq": time.monotonic_ns(),
        "run_state": "exited",
        "changed_at": now,
        "ended_at": now,
        "exit": exit_record,
        "supervisor_exit": exit_record,
    }
    path = Path(context["safety_path"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    registry._write(path, {"name": name, "generation": generation, "deny_boot": True, "record": record})


def transition(context: dict, name: str, generation: str, state: str, cause: str, evidence: str) -> bool:
    """Apply ordered CLI transitions within one monotonic budget, including backoff."""
    interrupted = [False]

    def request_stop(_signum, _frame):
        interrupted[0] = True

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    policy = context["policy"]
    deadline = time.monotonic() + policy["deadline_seconds"]
    delay = policy["backoff_seconds"]
    command = context["command"]

    def call(args):
        return _call(command + args, min(deadline, time.monotonic() + policy["command_timeout_seconds"]), interrupted)

    try:
        while not interrupted[0] and time.monotonic() < deadline:
            sequence = call(["sessions", "_sequence"])
            if sequence and sequence.isdecimal() and not interrupted[0]:
                result = call(
                    [
                        "sessions",
                        "mark",
                        name,
                        state,
                        "--cause",
                        cause,
                        "--evidence",
                        evidence,
                        "--generation",
                        generation,
                        "--sequence",
                        sequence,
                    ]
                )
                if result is not None:
                    return True
            if state != "exited":
                return False
            if not interrupted[0] and time.monotonic() < deadline:
                output.emit(
                    output.Tag.SESSION,
                    "exit",
                    "Session exit persistence failed; retrying. The agent will remain stopped.",
                    err=True,
                )
            retry_at = min(deadline, time.monotonic() + delay)
            while not interrupted[0] and time.monotonic() < retry_at:
                time.sleep(min(POLL_SECONDS, max(0.001, retry_at - time.monotonic())))
            delay = min(delay * 2, policy["max_backoff_seconds"])
        if state == "exited":
            output.error(output.Tag.SESSION, "exit", "Session exit persistence failed; stopping without respawning.")
            try:
                store_safety(context, name, generation, cause, json.loads(evidence))
            except Exception:
                output.error(
                    output.Tag.SESSION,
                    "exit",
                    "Session deny-boot safety storage failed; boot protection could not be persisted.",
                )
            else:
                output.emit(output.Tag.SESSION, "exit", "Session exit recorded in deny-boot safety storage.", err=True)
        return False
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    context, name, generation, state, cause, evidence = sys.argv[1:]
    raise SystemExit(0 if transition(json.loads(context), name, generation, state, cause, evidence) else 1)
