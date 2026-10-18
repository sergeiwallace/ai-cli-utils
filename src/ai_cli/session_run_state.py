"""Durable supervisor state, independent of terminal tracking and hook evidence."""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import portalocker

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


def valid_boot_recipe(record: dict) -> bool:
    """Accept structured owning-host recipes, never terminal history or shell text."""
    recipe = record.get("boot_recipe")
    if not isinstance(recipe, dict):
        return False
    engine = recipe.get("engine")
    name = recipe.get("session_name")
    options = recipe.get("options")
    if (
        engine not in ("c", "g", "p", "cx")
        or not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
        or name != record.get("name")
        or not name.startswith(f"{engine}-")
        or recipe.get("owning_host") != record.get("owning_host")
        or not recipe.get("owning_host")
        or not isinstance(recipe.get("cwd"), str)
        or not Path(recipe["cwd"]).is_absolute()
        or not isinstance(recipe.get("project"), str)
        or not isinstance(options, dict)
        or options.get("remote")
    ):
        return False
    for key in ("argv", "extra_args"):
        value = recipe.get(key)
        if not isinstance(value, list) or not all(isinstance(arg, str) and "\0" not in arg for arg in value):
            return False
    if recipe["argv"][:2] != ["ai", engine]:
        return False
    if options.get("bare"):
        command = recipe.get("command")
        return (
            isinstance(recipe.get("shell"), str)
            and bool(recipe["shell"])
            and isinstance(command, list)
            and bool(command)
            and all(isinstance(arg, str) and "\0" not in arg for arg in command)
        )
    return True


def boot_veto(record: dict, *, cfg: dict, host: str, boot_id: str | None, alive: bool | None) -> str | None:
    """Return the first winning boot veto; every policy obeys the same safety order."""
    if not cfg["on_boot"]:
        return "session_relaunch.on_boot is off"
    if record.get("kind") != "local" or record.get("owning_host") != host:
        return "foreign or remote session"
    if tombstone_path(record["name"], host).exists():
        return "deny-boot safety file exists"
    if alive is True:
        return "tmux session already exists"
    if alive is None:
        return "tmux liveness could not be established"
    if boot_id and record.get("relaunched_boot_id") == boot_id:
        return "already attempted in this boot"
    settled = cause(record, boot_id)
    if settled not in DEFAULTS["causes"]:
        return f"cause {settled} forbids boot relaunch"
    policy = record.get("relaunch", "auto")
    if policy not in ("auto", "always"):
        return f"policy {policy} forbids boot relaunch"
    if settled not in cfg["causes"]:
        return f"cause {settled} is disabled by profile"
    if not valid_boot_recipe(record):
        return "unsupported or incomplete boot recipe"
    return None


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


def _begin_record(doc: dict, name: str) -> dict:
    record = _find(doc, name)
    if not record.get("boot_recipe"):
        raise ValueError("session has no boot recipe; refusing an untracked start")
    if not valid_boot_recipe(record):
        raise ValueError("session has an invalid boot recipe; refusing an untracked start")
    return record


def can_begin(name: str) -> None:
    """Check generation prerequisites without creating a file or changing a record.

    This is a snapshot, not a reservation: callers must repeat it before action.
    The existing lock is opened read-only; a missing lock refuses rather than
    creating it through the registry's mutating lock context manager.
    """
    path = registry.registry_path()
    lock_path = path.with_name(f"{path.name}.lock")
    for target, mode in (
        (path.parent, os.W_OK | os.X_OK),
        (path, os.R_OK | os.W_OK),
        (lock_path, os.R_OK | os.W_OK),
    ):
        if not os.access(target, mode):
            raise OSError(f"registry generation storage unavailable: {target}")
    with portalocker.Lock(lock_path, mode="r", timeout=2):
        _begin_record(registry.load_registry(), name)


def begin(name: str, *, command: list[str] | None = None) -> str:
    """Acquire a fresh supervisor generation before starting any agent."""
    generation = str(uuid.uuid4())

    def change(doc):
        record = _find(doc, name)
        if not record.get("boot_recipe"):
            raise ValueError("session has no boot recipe; refusing an untracked start")
        if command is not None:
            record["boot_recipe"]["command"] = command
        _begin_record(doc, name)
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
