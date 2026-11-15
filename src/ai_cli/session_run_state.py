"""Durable supervisor state, independent of terminal tracking and hook evidence."""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
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


NOT_RUNNING_AT_REBOOT = "so it was not running when the host went down"


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def not_running_at_reboot(record: dict, *, boot_id: str | None, booted: float | None) -> str | None:
    """Why a record this boot would replay was not running when the host went down, else None.

    Replay brings back what the reboot stopped. A reservation an earlier boot made that
    never started, an exit recorded before this boot began (except a maintenance wind-down,
    which stops a session for the reboot), and a run an earlier boot's relaunch already
    judged and nothing started since were all down before the reboot. ``booted`` is this
    boot's start in epoch seconds; an exit that cannot be placed against it is refused.
    """
    current = run_state(record)
    if current == "relaunched":
        return f"reserved by an earlier boot's relaunch but never started, {NOT_RUNNING_AT_REBOOT}"
    decided = (record.get("boot_decision") or {}).get("boot_id")
    if boot_id and record.get("boot_id") != boot_id and decided and decided not in (boot_id, record.get("boot_id")):
        return f"kept down by an earlier boot's relaunch and not started since, {NOT_RUNNING_AT_REBOOT}"
    settled = cause(record, boot_id)
    if current != "exited" or settled == WIND_DOWN_CAUSE:
        return None
    exited = record.get("supervisor_exit") or record.get("exit") or {}
    ended = _epoch(record.get("ended_at")) or _epoch(exited.get("at"))
    if booted is None or ended is None:
        return "the exit cannot be placed before or after this boot's start"
    if ended < booted:
        return f"exited ({settled}) before this boot, {NOT_RUNNING_AT_REBOOT}"
    return None


def boot_veto(
    record: dict,
    *,
    cfg: dict,
    host: str,
    boot_id: str | None,
    alive: bool | None,
    booted: float | None = None,
) -> str | None:
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
    return not_running_at_reboot(record, boot_id=boot_id, booted=booted) or boot_recipe_refusal(record)


def default_relaunch(record: dict) -> None:
    """Give ``record`` the automatic boot policy unless one was chosen for it.

    ``relaunch_set_by`` records who wrote ``relaunch``: ``owner`` for ``ai sessions policy``,
    ``default`` for an automatic writer (launch, enrolment, recovery, repair).
    The automatic policy is ``never`` for a probe and ``auto`` for every other session. It
    replaces only a missing policy or one an automatic writer set. An owner's value, and an
    unattributed value written before ``relaunch_set_by`` existed, are left as they are;
    ``ai sessions repair-policy`` is the one place that judges an unattributed ``never``.
    """
    if "relaunch" in record and record.get("relaunch_set_by") != "default":
        return
    record.update(relaunch="never" if record.get("probe") else "auto", relaunch_set_by="default")


def tombstone_path(name: str, host: str) -> Path:
    key = hashlib.sha256(f"{host}\0{name}".encode()).hexdigest()
    return registry.registry_path().parent / "session-safety" / f"{key}.json"


def _find(doc: dict, name: str) -> dict:
    for record in doc["sessions"]:
        if record.get("kind") == "local" and record.get("name") == name:
            return record
    raise ValueError(f"session {name!r} is not registered; refusing an untracked transition")


#: The agent family a launcher engine starts, as recorded in a row's ``family`` field.
FAMILIES = {"c": "claude", "cx": "codex", "g": "gemini", "p": "pi"}
_ENGINE_PREFIX = re.compile(r"^(cx|c|g|p)(?:-r)?-")


def family_for(name: str, engine: str | None = None) -> str | None:
    """The family of the agent a session runs, from its engine or its tmux name's prefix."""
    if engine in FAMILIES:
        return FAMILIES[engine]
    match = _ENGINE_PREFIX.match(name)
    return FAMILIES[match.group(1)] if match else None


def canonical_name(name: str) -> str:
    """The session name every client shows: the tmux key without its engine/transport prefix."""
    return _ENGINE_PREFIX.sub("", name, count=1)


def _live_claim(row: dict, pid: int, started_at: float) -> bool:
    """Whether ROW holds a current attestation of the process ``pid`` born at ``started_at``."""
    identity = row.get("agent_identity") or {}
    return (
        row.get("kind") == "local"
        and identity.get("pid") == pid
        and identity.get("started_at") == started_at
        and identity.get("generation") == row.get("generation")
        and row.get("run_state") != "exited"
    )


def record_agent(name: str, pid: int, generation: str | None = None) -> bool:
    """Record the interactive agent process the launcher started on its session row.

    ``agent_identity`` holds:

    * ``pid``: the agent process itself (a shell that ran it exec'd it, so the pid is
      the agent's), never the tmux server or the supervisor shell.
    * ``started_at``: its start time in epoch seconds, as psutil reports it.
    * ``proc_start`` and ``proc_start_backend``: the start marker in the unit the
      platform probe captures and compares (``procfs`` start ticks on Linux, ``psutil``
      epoch seconds elsewhere), so ``process_probe.probe_for().start_time_match`` verifies it.
    * ``boot_id``: the host boot identity (Linux ``boot_id``, macOS
      ``kern.bootsessionuuid``); null on Windows, which exposes no boot id to read.
    * ``interactive``: true, the process is an interactive client in a session pane.
    * ``generation``: the run generation the row held when the agent started.

    Returns False, writing nothing, when the row is missing, belongs to another generation,
    or another session's row holds a live attestation of the same process.
    """
    import psutil

    from .process_probe import probe_for

    started_at = psutil.Process(pid).create_time()
    marker = probe_for().capture_identity(pid)
    boot_id = registry.current_boot_id()
    written = []

    def change(doc):
        row = next((r for r in doc["sessions"] if r.get("kind") == "local" and r.get("name") == name), None)
        if row is None or (generation and row.get("generation") != generation):
            return False
        # One attested agent has exactly one owning session: a second live claim on the
        # same process is refused, never last-write-wins.
        if any(_live_claim(other, pid, started_at) for other in doc["sessions"] if other is not row):
            return False
        row["agent_identity"] = {
            "pid": pid,
            "started_at": started_at,
            "proc_start": marker.marker if marker else None,
            "proc_start_backend": marker.backend if marker else None,
            "boot_id": boot_id,
            "interactive": True,
            "generation": row.get("generation"),
        }
        written.append(True)
        return True

    registry._mutate(change, timeout=2)
    return bool(written)


def register(name: str, *, recipe: dict, host: str, new_start: bool = False, probe: bool = False) -> dict:
    """Upsert the owning-host recipe, preserving lifecycle and policy on reattach.

    A ``probe`` session is marked as one and its default relaunch policy is ``never``, so
    boot replay vetoes it and a prune drops its row once its session is gone. An ordinary
    new start of a probe's record clears the mark and gives it the default ``auto``; the
    launch refuses a probe whose title a canonical record already holds, so a probe never
    reaches one here. A policy its owner set survives both.
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
        if new_start and not probe and existing.pop("probe", None) and existing.get("relaunch_set_by") != "owner":
            # An ordinary launch that takes a probe's title makes the record canonical again.
            existing.update(relaunch="auto", relaunch_set_by="default")
        if new_start:
            existing["boot_recipe"] = recipe
            existing["cwd"] = recipe["cwd"]
            existing["relaunch_argv"] = recipe["argv"]
            existing.update(launcher_pid=os.getpid(), launched_at=registry._now())
        else:
            existing.setdefault("boot_recipe", recipe)
        existing.setdefault("owning_host", host)
        family = family_for(name, recipe.get("engine"))
        if family:
            existing["family"] = family
        existing["canonical_name"] = canonical_name(name)
        if probe:
            existing["probe"] = True
        default_relaunch(existing)
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


def begin(
    name: str,
    *,
    command: list[str] | None = None,
    recovery_recipe: dict | None = None,
    supervisor: dict | None = None,
) -> str:
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
                default_relaunch(record)
        record = _find(doc, name)
        if not record.get("boot_recipe"):
            raise ValueError("session has no boot recipe; refusing an untracked start")
        if command is not None:
            record["boot_recipe"]["command"] = command
        record.update(generation=generation, transition_seq=0)
        record.pop("supervisor_identity", None)
        if supervisor is not None:
            record["supervisor_identity"] = dict(supervisor, generation=generation)
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
        teardown = exit_teardown(record)
        if teardown and teardown.get("generation") == record.get("generation"):
            raise ValueError("manual exit fenced this supervisor generation")
        # The monotonic clock restarts at boot, so an unsequenced (manual) transition is
        # ordered after the stored one: a supervisor that died with the host left a
        # sequence no reading in this boot can exceed.
        stored = record.get("transition_seq", 0)
        seq = sequence if sequence is not None else max(time.monotonic_ns(), stored + 1)
        if seq <= stored:
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


def exit_teardown(record: dict) -> dict:
    """Read cleanup evidence from lifecycle fields preserved across reattachment."""
    return ((record.get("supervisor_exit") or {}).get("evidence") or {}).get("teardown") or {}


def request_manual_exit(name: str, *, host: str, generation: str, targets: dict) -> dict:
    """Commit a manual exit and its cleanup identities, or raise without authorizing signals.

    Unlike ``mark_exited``, a safety-only fallback is a failure for this operation.
    Repeating the request retains the original transition and resumes its cleanup.
    """
    accepted = {}

    def change(doc):
        record = _find(doc, name)
        if not generation or record.get("generation") != generation or record.get("owning_host") != host:
            raise ValueError("session host or generation mismatch")
        previous = exit_teardown(record)
        if previous.get("generation") == generation:
            if any(targets.values()) and previous["targets"] != targets:
                raise ValueError("cleanup identities differ from the committed exit")
            accepted.update(record)
            return False
        now = registry._now()
        record.update(
            run_state="exited",
            changed_at=now,
            ended_at=now,
            transition_seq=record.get("transition_seq", 0) + 1,
        )
        record.pop("wind_down", None)
        registry.record_exit(
            record["id"],
            cause="manual_exit",
            evidence={"teardown": {"generation": generation, "targets": targets, "errors": []}},
            at=now,
            _document=doc,
        )
        record["supervisor_exit"] = dict(record["exit"])
        accepted.update(record)
        return True

    def fallback(_doc):
        path = tombstone_path(name, host)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        registry._write(path, {"name": name, "generation": generation, "deny_boot": True, "record": accepted})
        raise OSError("registry exit commit failed; recovery evidence retained")

    registry._mutate(change, write_failure=fallback, timeout=2)
    return accepted
