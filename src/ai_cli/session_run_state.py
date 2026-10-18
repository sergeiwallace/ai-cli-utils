"""Durable supervisor state, independent of terminal tracking and hook evidence."""

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from . import config
from . import session_registry as registry

LOGGER = logging.getLogger(__name__)
DEFAULTS = {
    "on_boot": False,
    "causes": ["host_reboot", "terminal_quit_or_crash"],
    "network_wait_seconds": 120,
    "network_probe": "api.anthropic.com:443",
}
PUBLISH_DEFAULTS = {
    "repo": "",
    "remote": "origin",
    "ref_prefix": "refs/heads/fleet-sessions/",
    "debounce_seconds": 60,
    "host": "",
}


def settings() -> dict:
    """Read configuration on every operation; unsafe boot causes are refused."""
    cfg = config.load_config()
    result = DEFAULTS | cfg.get("session_relaunch", {})
    if not isinstance(result["causes"], list) or any(c not in DEFAULTS["causes"] for c in result["causes"]):
        raise ValueError("session_relaunch.causes permits only host_reboot and terminal_quit_or_crash")
    result["publish"] = PUBLISH_DEFAULTS | result.get("publish", {})
    result["publish"]["host"] = result["publish"]["host"] or cfg.get("machine", {}).get("host_id", "")
    return result


def run_state(record: dict) -> str:
    return record.get("run_state") or ("exited" if record.get("ended_at") else "running")


def cause(record: dict, boot_id: str | None = None) -> str:
    """Settle boot evidence without allowing hooks to override supervisor state."""
    if run_state(record) == "exited":
        return (record.get("supervisor_exit") or record.get("exit") or {}).get("cause", "unknown")
    stored = record.get("boot_id")
    if not stored or not boot_id:
        return "unknown"
    return "host_reboot" if stored != boot_id else "terminal_quit_or_crash"


def tombstone_path(name: str, host: str) -> Path:
    key = hashlib.sha256(f"{host}\0{name}".encode()).hexdigest()
    return registry.registry_path().parent / "session-safety" / f"{key}.json"


def _find(doc: dict, name: str) -> dict:
    for record in doc["sessions"]:
        if record.get("kind") == "local" and record.get("name") == name:
            return record
    raise ValueError(f"session {name!r} is not registered; refusing an untracked transition")


def register(name: str, *, recipe: dict, host: str, new_start: bool = False) -> dict:
    """Upsert the owning-host recipe, preserving lifecycle and policy on reattach."""
    result = {}

    def upsert(doc):
        existing = next((r for r in doc["sessions"] if r.get("kind") == "local" and r.get("name") == name), None)
        if existing is None:
            existing = registry._new_record(
                kind="local",
                name=name,
                relaunch_argv=recipe["argv"],
                cwd=recipe["cwd"],
                remote=None,
                iterm2=None,
                launcher_pid=os.getpid(),
            )
            doc["sessions"].append(existing)
        if new_start:
            existing["boot_recipe"] = recipe
            existing["cwd"] = recipe["cwd"]
            existing["relaunch_argv"] = recipe["argv"]
        else:
            existing.setdefault("boot_recipe", recipe)
        existing.setdefault("owning_host", host)
        existing.setdefault("relaunch", "auto")
        result.update(existing)
        return True

    registry._mutate(upsert, timeout=2)
    return result


def begin(name: str, *, command: list[str] | None = None) -> str:
    """Acquire a fresh supervisor generation before starting any agent."""
    generation = str(uuid.uuid4())

    def change(doc):
        record = _find(doc, name)
        if not record.get("boot_recipe"):
            raise ValueError("session has no boot recipe; refusing an untracked start")
        if command is not None:
            record["boot_recipe"]["command"] = command
        record.update(generation=generation, transition_seq=0)
        return True

    registry._mutate(change, timeout=2)
    return generation


def mark(
    name: str,
    state: str,
    *,
    cause: str = "unknown",
    evidence: dict | None = None,
    generation: str | None = None,
    sequence: int | None = None,
) -> bool:
    """Apply an ordered transition. Final exits fall back to a durable deny-boot file."""
    if state not in ("running", "exited") or cause not in registry.EXIT_CAUSES:
        LOGGER.error("invalid session transition")
        return False
    accepted = {}

    def change(doc):
        record = _find(doc, name)
        if generation is not None and generation != record.get("generation"):
            raise ValueError("stale supervisor generation")
        seq = sequence if sequence is not None else time.monotonic_ns()
        if seq <= record.get("transition_seq", 0):
            raise ValueError("stale supervisor transition sequence")
        safety = tombstone_path(name, record.get("owning_host", ""))
        if (
            state == "running"
            and safety.exists()
            and json.loads(safety.read_text()).get("generation") == record.get("generation")
        ):
            raise ValueError("final exit safety veto requires a new explicit launch")
        now = registry._now()
        record.update(run_state=state, changed_at=now, transition_seq=seq)
        if state == "running":
            record.update(exit=None, ended_at=None, supervisor_exit=None, boot_id=registry.current_boot_id())
        else:
            registry.record_exit(record["id"], cause=cause, evidence=evidence or {}, at=now, _document=doc)
            record["supervisor_exit"] = dict(record["exit"])
            record["ended_at"] = now
        accepted.update(record)
        return True

    def fallback(_doc):
        path = tombstone_path(name, accepted.get("owning_host", ""))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        registry._write(
            path, {"name": name, "generation": accepted.get("generation"), "deny_boot": True, "record": accepted}
        )
        LOGGER.error("session exit stored in deny-boot safety file after registry persistence failed")

    def clear_safety():
        if state != "running":
            return
        path = tombstone_path(name, accepted.get("owning_host", ""))
        if path.exists():
            path.read_bytes()
            path.unlink()
            if os.name != "nt":
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

    try:
        registry._mutate(
            change, write_failure=fallback if state == "exited" else None, timeout=2, after_write=clear_safety
        )
        return True
    except Exception as exc:
        LOGGER.error("session state persistence refused: %s", exc)
        return False


def mark_running(name: str, **kwargs) -> bool:
    return mark(name, "running", **kwargs)


def mark_exited(name: str, cause: str, evidence: dict, **kwargs) -> bool:
    return mark(name, "exited", cause=cause, evidence=evidence, **kwargs)


def reconcile_safety() -> None:
    """Recover final exits from the safety store once registry persistence works again."""

    def recover(doc):
        changed = False
        for record in doc["sessions"]:
            if record.get("kind") != "local":
                continue
            path = tombstone_path(record["name"], record.get("owning_host", ""))
            if not path.exists():
                continue
            saved = json.loads(path.read_text()).get("record", {})
            if saved.get("generation") == record.get("generation") and saved.get("transition_seq", 0) > record.get(
                "transition_seq", 0
            ):
                record.update(saved)
                changed = True
        return changed

    registry._mutate(recover, timeout=2)
