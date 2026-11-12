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
WIND_DOWN_CAUSE = "maintenance_wind_down"
DEFAULTS = {
    "on_boot": False,
    "causes": ["host_reboot", "terminal_quit_or_crash", "maintenance_wind_down"],
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
        raise ValueError(f"session_relaunch.causes permits only {', '.join(DEFAULTS['causes'])}")
    result["publish"] = PUBLISH_DEFAULTS | result.get("publish", {})
    result["publish"]["host"] = result["publish"]["host"] or cfg.get("machine", {}).get("host_id", "")
    return result


def run_state(record: dict) -> str:
    return record.get("run_state") or ("exited" if record.get("ended_at") else "unknown")


def cause(record: dict, boot_id: str | None = None) -> str:
    """Settle boot evidence without allowing hooks to override supervisor state."""
    if run_state(record) == "exited":
        return (record.get("supervisor_exit") or record.get("exit") or {}).get("cause", "unknown")
    stored = record.get("boot_id")
    if not stored or not boot_id:
        return "unknown"
    return "host_reboot" if stored != boot_id else "terminal_quit_or_crash"


BOOT_RECIPE_REFUSAL = "unsupported or incomplete boot recipe"
_ENGINES = ("c", "g", "p", "cx")


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(arg, str) and "\0" not in arg for arg in value)


def boot_recipe_problem(record: dict) -> str | None:
    """The first field that keeps a record's boot recipe from replaying, None when it replays.

    Only structured owning-host recipes are accepted, never terminal history or shell text.
    """
    recipe = record.get("boot_recipe")
    if not isinstance(recipe, dict):
        return "the record has no boot_recipe"
    engine = recipe.get("engine")
    name = recipe.get("session_name")
    options = recipe.get("options")
    host = recipe.get("owning_host")
    cwd = recipe.get("cwd")
    if engine not in _ENGINES:
        return f"engine {engine!r} is not one of {', '.join(_ENGINES)}"
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return f"session_name {name!r} is not a valid session name"
    if name != record.get("name"):
        return f"session_name {name!r} differs from the record name {record.get('name')!r}"
    if not name.startswith(f"{engine}-"):
        return f"session_name {name!r} does not start with the engine prefix {engine + '-'!r}"
    if not host:
        return "owning_host is missing"
    if host != record.get("owning_host"):
        return f"owning_host {host!r} differs from the record's owning_host {record.get('owning_host')!r}"
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return f"cwd {cwd!r} is not an absolute path"
    if not isinstance(recipe.get("project"), str):
        return "project is not a string"
    if not isinstance(options, dict):
        return "options is not a mapping"
    if options.get("remote"):
        return "options.remote is set"
    for key in ("argv", "extra_args"):
        if not _string_list(recipe.get(key)):
            return f"{key} is not a list of strings"
    if recipe["argv"][:2] != ["ai", engine]:
        return f"argv does not start with 'ai {engine}'"
    if options.get("bare"):
        shell, command = recipe.get("shell"), recipe.get("command")
        if not (isinstance(shell, str) and shell and _string_list(command) and command):
            return "a bare recipe needs a non-empty shell and command list"
    return None


def valid_boot_recipe(record: dict) -> bool:
    return boot_recipe_problem(record) is None


def boot_recipe_refusal(record: dict) -> str | None:
    """``unsupported or incomplete boot recipe: <field reason>``, None when the recipe replays."""
    problem = boot_recipe_problem(record)
    return None if problem is None else f"{BOOT_RECIPE_REFUSAL}: {problem}"


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
    return boot_recipe_refusal(record)


def tombstone_path(name: str, host: str) -> Path:
    key = hashlib.sha256(f"{host}\0{name}".encode()).hexdigest()
    return registry.registry_path().parent / "session-safety" / f"{key}.json"


def _find(doc: dict, name: str) -> dict:
    for record in doc["sessions"]:
        if record.get("kind") == "local" and record.get("name") == name:
            return record
    raise ValueError(f"session {name!r} is not registered; refusing an untracked transition")


def register(name: str, *, recipe: dict, host: str, new_start: bool = False, probe: bool = False) -> dict:
    """Upsert the owning-host recipe, preserving lifecycle and policy on reattach.

    A ``probe`` session is marked as one and its relaunch policy is ``never``, so boot
    replay vetoes it and a prune drops its row once its session is gone.
    """
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
            existing.update(launcher_pid=os.getpid(), launched_at=registry._now())
        else:
            existing.setdefault("boot_recipe", recipe)
        existing.setdefault("owning_host", host)
        if probe:
            existing.update(probe=True, relaunch="never")
        existing.setdefault("relaunch", "auto")
        result.update(existing)
        return True

    registry._mutate(upsert, timeout=2)
    return result


def can_begin(name: str) -> None:
    """Check generation prerequisites without creating a file or changing a record.

    This is a snapshot, not a reservation: callers must repeat it before action.
    The existing lock is opened read/write without creating or truncating it
    (Windows refuses an exclusive lock on a read-only handle); a missing lock
    refuses rather than creating it through the registry's mutating lock
    context manager.
    """
    path = registry.registry_path()
    lock_path = path.with_name(f"{path.name}.lock")
    # os.access is not a reliable write test on Windows (it ignores ACLs and refuses
    # some writable directories); there the lock open and the registry read decide.
    for target, mode in (
        (path.parent, os.W_OK | os.X_OK),
        (path, os.R_OK | os.W_OK),
        (lock_path, os.R_OK | os.W_OK),
    ):
        if os.name != "nt" and not os.access(target, mode):
            raise OSError(f"registry generation storage unavailable: {target}")
    with portalocker.Lock(lock_path, mode="r+", timeout=2):
        record = _find(registry.load_registry(), name)
        if not record.get("boot_recipe"):
            raise ValueError("session has no boot recipe; refusing an untracked start")
        if not valid_boot_recipe(record):
            raise ValueError("session has an invalid boot recipe; refusing an untracked start")


def begin(name: str, *, command: list[str] | None = None, recovery_recipe: dict | None = None) -> str:
    """Acquire a fresh supervisor generation before starting any agent."""
    generation = str(uuid.uuid4())

    def change(doc):
        if recovery_recipe is not None:
            host = recovery_recipe["owning_host"]
            matches = [r for r in doc["sessions"] if r.get("kind") == "local" and r.get("name") == name]
            # Existing recipes retain the normal begin contract; ownership checks
            # constrain reconstruction, never a re-labeling of registered sessions.
            if (
                matches
                and not matches[0].get("boot_recipe")
                and any(r.get("owning_host", host) != host for r in matches)
            ):
                raise ValueError("session belongs to another host; recovery refused")
            if not matches:
                record = registry._new_record(
                    kind="local",
                    name=name,
                    relaunch_argv=recovery_recipe["argv"],
                    cwd=recovery_recipe["cwd"],
                    remote=None,
                    iterm2=None,
                    launcher_pid=None,
                )
                doc["sessions"].append(record)
            else:
                record = matches[0]
            if not record.get("boot_recipe"):
                record.update(boot_recipe=recovery_recipe, owning_host=host)
                # Recover tracking without opting a pre-feature session into boot replay.
                record.setdefault("relaunch", "never")
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
            settled, details = cause, dict(evidence or {})
            # The wrapper's clean exit says manual_exit; a wind-down of this run asked for it first.
            request = record.pop("wind_down", None) or {}
            if cause == "manual_exit" and request.get("generation") == record.get("generation"):
                settled = WIND_DOWN_CAUSE
                details["wind_down_requested_at"] = request.get("at")
            registry.record_exit(record["id"], cause=settled, evidence=details, at=now, _document=doc)
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


def request_wind_down(record: dict) -> None:
    """Mark this run so its next clean exit is recorded as a maintenance wind-down."""
    now = registry._now()
    record.update(
        wind_down={"cause": WIND_DOWN_CAUSE, "generation": record.get("generation"), "at": now}, changed_at=now
    )


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
