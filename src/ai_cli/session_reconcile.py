"""Settle vanished local runs without destroying evidence from an earlier boot."""

import json
import math
import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import psutil

from . import session_registry as registry
from . import session_run_state as state

# Let a launch finish acquiring its supervisor before inferring an unrecorded end.
START_GRACE_SECONDS = 60


def _could_run_session(executable: str | None) -> bool:
    if not executable:
        return True
    executable = executable.lower().removesuffix(".exe")
    return executable.startswith("python") or executable in {
        "claude",
        "codex",
        "gemini",
        "pi",
        "node",
        "nodejs",
        "sh",
        "bash",
        "zsh",
        "fish",
        "dash",
        "ksh",
        "pwsh",
        "powershell",
        "cmd",
    }


def _process_alive(record: dict) -> bool | None:
    """Check agents, exact generated wrappers, and the recorded launcher; fail closed.

    Service helpers inherit session environment too, so an environment marker alone
    cannot establish an agent's liveness. Never log argv or environment contents.
    """
    name = record["name"]
    agent_name = name.partition("-")[2].removeprefix("r-")
    script = str(registry.registry_path().parent / "sessions" / f"{name}.sh")
    try:
        user = psutil.Process().username()
        for process in psutil.process_iter(["pid", "username", "status", "name"]):
            info = process.info
            if info["pid"] == os.getpid() or info["status"] == psutil.STATUS_ZOMBIE:
                continue
            if info["username"] is None:
                return None
            if info["username"] != user:
                continue
            try:
                if info["pid"] == record.get("launcher_pid"):
                    launched = datetime.fromisoformat(record["launched_at"].replace("Z", "+00:00"))
                    if process.create_time() <= launched.timestamp() + 1:
                        return True
                try:
                    argv = process.cmdline()
                except psutil.AccessDenied:
                    argv = None
                if not argv:
                    # Missing arguments on a possible agent or wrapper are unknown;
                    # unrelated processes cannot block this session's absence proof.
                    if _could_run_session(info.get("name")):
                        return None
                    continue
                if "--ai-cli-heartbeat-ticker" in argv:
                    continue
                if script in argv:
                    return True
                executable = Path(argv[0]).name.lower().removesuffix(".exe") if argv else ""
                if any(Path(part).name == "circusd" for part in argv[:2]) or "ai_cli.session_bare_owner" in argv:
                    continue
                cli = "ai_cli.main" in argv or any(Path(part).stem == "ai" for part in argv[:2])
                if cli and (
                    "session-reaper" in argv or "internal" in argv or ("sessions" in argv and "publish" in argv)
                ):
                    continue
                # Interpreters also run unrelated helpers with inherited markers.
                # Require an agent entry point before interpreting those markers.
                agents = {"claude", "codex", "gemini", "pi"}
                entry = argv[1].replace("\\", "/") if len(argv) > 1 else ""
                interpreted_agent = Path(entry).stem in agents or any(
                    package in entry
                    for package in (
                        "/@anthropic-ai/claude-code/",
                        "/@openai/codex/",
                        "/@google/gemini-cli/",
                        "/pi-coding-agent/",
                    )
                )
                if executable not in agents and not (
                    (executable in {"node", "nodejs"} or executable.startswith("python")) and interpreted_agent
                ):
                    continue
                if any(flag == "--name" and value == agent_name for flag, value in pairwise(argv)):
                    return True
                environment = process.environ()
                if (
                    environment.get("AI_TMUX_SESSION") == name
                    or environment.get("CLAUDE_CODE_TASK_LIST_ID") == agent_name
                ):
                    return True
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError, ValueError, KeyError):
                return None
    except (psutil.Error, OSError):
        return None
    return False


def liveness_blocker(record: dict) -> str | None:
    """Why a record that says running may still be live, or None when this host proves it gone.

    A record from an earlier boot of its own host is proved gone once tmux and the process
    table both show nothing, because no process survives a reboot. Reconcile still leaves
    such a record unchanged for the boot relaunch to judge; only the caller stops treating
    it as live.
    """
    if record.get("kind") != "local" or not record.get("boot_id"):
        return "liveness is unprovable on this host: owning-boot evidence is unavailable"
    tmux = registry._local_session_alive(record["name"])
    if tmux is True:
        return f"live tmux session {record['name']}"
    process = None
    try:
        for candidate in psutil.process_iter(["pid", "status"]):
            if candidate.info["pid"] != record.get("launcher_pid") or candidate.info["status"] == psutil.STATUS_ZOMBIE:
                continue
            launched = datetime.fromisoformat(record["launched_at"].replace("Z", "+00:00"))
            if candidate.create_time() <= launched.timestamp() + 1:
                return f"live launcher pid {candidate.info['pid']}"
        process = _process_alive(record)
        if process is True:
            return "live agent or session wrapper process"
    except (psutil.Error, OSError, ValueError, KeyError):
        pass
    host = state.config.load_config().get("machine", {}).get("host_id") or registry.load_registry()["machine"]
    boot = registry.current_boot_id()
    if record.get("owning_host", host) != host:
        return f"liveness is unprovable on this host: the session belongs to host {record['owning_host']}"
    unanswered = [probe for probe, alive in (("tmux", tmux), ("process table", process)) if alive is None]
    if unanswered:
        return f"liveness is unprovable: the {' and '.join(unanswered)} probe could not answer"
    if boot and record["boot_id"] != boot:
        return None
    return "liveness is unprovable; inspect the launcher, tmux and process evidence"


def reconcile(*, names: set[str] | None = None) -> None:
    """Probe without the registry lock, then settle only unchanged records.

    The existing reaper invokes this every minute, including in observe mode.
    Readers also use it so a stopped scheduler cannot make stale state authoritative.
    Different or missing boot identities are never changed, even after power loss.
    """
    if not registry.registry_path().exists():
        return
    try:
        boot = registry.current_boot_id()
        if not boot:
            return
        host = state.config.load_config().get("machine", {}).get("host_id") or registry.load_registry()["machine"]
        settled = []
        candidates = []
        for record in registry.load_registry()["sessions"]:
            if (
                record.get("kind") != "local"
                or record.get("owning_host", host) != host
                or record.get("boot_id") != boot
                or (names is not None and record.get("name") not in names)
                or state.run_state(record) not in {"running", "unknown"}
            ):
                continue
            safety = state.tombstone_path(record["name"], record.get("owning_host", ""))
            if safety.exists():
                saved = json.loads(safety.read_text()).get("record", {})
                if saved.get("generation") == record.get("generation") and saved.get("transition_seq", 0) > record.get(
                    "transition_seq", 0
                ):
                    candidates.append((record, saved))
                # Preserve an explicit final exit instead of replacing its evidence.
                continue
            stamp = record.get("changed_at") or record.get("launched_at")
            if stamp:
                try:
                    age = (datetime.now(UTC) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
                except (ValueError, TypeError):
                    continue
                if 0 <= age < START_GRACE_SECONDS:
                    continue
            if registry._local_session_alive(record["name"]) is False and _process_alive(record) is False:
                candidates.append((record, None))
        if not candidates:
            return

        def change(doc):
            changed = False
            for snapshot, saved in candidates:
                record = next((r for r in doc["sessions"] if r.get("id") == snapshot.get("id")), None)
                # Generation, sequence, recipe and launcher changes all veto a stale
                # observation. Slow probes must never block an exit or new launch.
                if record is None or record != snapshot:
                    continue
                if saved is not None:
                    record.update(saved)
                    changed = True
                    continue
                now = registry._now()
                evidence = {
                    "source": "liveness_reconciliation",
                    "checked_at": now,
                    "boot_id": boot,
                    "tmux_alive": False,
                    "process_alive": False,
                    "checks": ["tmux_exact_session", "local_agent_and_wrapper_processes"],
                }
                for key in ("exit", "supervisor_exit", "changed_at", "ended_at"):
                    if record.get(key) is not None:
                        evidence[f"previous_{key}"] = deepcopy(record[key])
                registry.record_exit(record["id"], cause="unknown", evidence=evidence, at=now, _document=doc)
                record.update(
                    run_state="exited",
                    changed_at=now,
                    ended_at=now,
                    supervisor_exit=dict(record["exit"]),
                    transition_seq=max(time.monotonic_ns(), record.get("transition_seq", 0) + 1),
                )
                settled.append(deepcopy(record))
            return changed or bool(settled)

        def fallback(_doc):
            for record in settled:
                path = state.tombstone_path(record["name"], record.get("owning_host", ""))
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if path.exists():
                    path.read_bytes()
                registry._write(
                    path,
                    {
                        "name": record["name"],
                        "generation": record.get("generation"),
                        "deny_boot": True,
                        "record": record,
                    },
                )

        registry._mutate(change, timeout=2, write_failure=fallback)
        for record in settled:
            state.LOGGER.warning(
                json.dumps(
                    {
                        "event": "session_reconciled",
                        "name": record["name"],
                        "check": "same_boot_liveness",
                        "result": "exited",
                        "checked_at": record["exit"]["evidence"]["checked_at"],
                        "tmux_alive": False,
                        "process_alive": False,
                    },
                    sort_keys=True,
                )
            )
    except Exception as exc:
        # Exception messages can contain process argv or environment values.
        state.LOGGER.warning("session reconciliation unavailable (%s)", type(exc).__name__)


#: Hours a row whose session is proved gone stays listed; ``[session_registry]
#: stale_row_retention_hours`` overrides it on every call, and 0 turns aging out off.
DEFAULT_RETENTION_HOURS = 72


def retention_seconds() -> float | None:
    """The configured retention in seconds, None when aging out is off."""
    table = state.config.load_config().get("session_registry", {})
    hours = table.get("stale_row_retention_hours", DEFAULT_RETENTION_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours < 0:
        raise ValueError("session_registry.stale_row_retention_hours must be a non-negative number")
    return None if hours == 0 else float(hours) * 3600


def boot_time() -> float | None:
    """When this host booted (epoch seconds), None when it cannot be read."""
    try:
        value = psutil.boot_time()
    except (psutil.Error, OSError):
        return None
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _last_seen(record: dict, keys: tuple[str, ...]) -> float | None:
    stamps = [value for key in keys if (value := _epoch(record.get(key))) is not None]
    return max(stamps) if stamps else None


def _launcher_alive(record: dict) -> bool | None:
    """Whether the process that holds a remote session's connection still runs."""
    pid = record.get("launcher_pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    launched = _epoch(record.get("launched_at"))
    try:
        created = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return None
    if launched is None:
        return None
    # A process younger than the launch reused the pid.
    return created <= launched + 1


def _local_gone(record: dict, *, host: str, boot: str | None, booted: float | None, cutoff: float) -> str | None:
    owner = record.get("owning_host")
    if owner not in (None, "", host):
        return None
    if state.tombstone_path(record["name"], owner or "").exists():
        return None
    seen = _last_seen(record, ("changed_at", "ended_at", "refreshed_at", "launched_at"))
    if not boot or record.get("boot_id") != boot:
        # A row from an earlier boot is the boot relaunch's to replay, so its clock
        # starts when this boot did.
        if booted is None:
            return None
        seen = max(seen or booted, booted)
    if seen is None or seen > cutoff:
        return None
    if registry._local_session_alive(record["name"]) is not False or _process_alive(record) is not False:
        return None
    return "no tmux session or agent process"


def age_out(*, probe_remote: bool = False) -> list[tuple[dict, str]]:
    """Remove rows whose session is proved gone and that have not changed for the retention.

    Local: ``tmux has-session`` and the process table both prove absence, no deny-boot
    safety file exists, and the row is older than the retention (a row from an earlier
    boot counts from this boot's start). Remote, only with ``probe_remote``: the
    launcher process is gone, the row is older than the retention, and either a clean
    exit was recorded or the remote host's tmux has no such session. A row the probe
    cannot settle is stamped ``probed_at`` and waits another retention before the next
    dial. A row that changed after this pass read it is never removed.
    """
    if not registry.registry_path().exists():
        return []
    removed: list[tuple[dict, str]] = []
    try:
        retention = retention_seconds()
        if retention is None:
            return []
        cfg = state.config.load_config()
        host = cfg.get("machine", {}).get("host_id") or registry.load_registry()["machine"]
        boot = registry.current_boot_id()
        booted = boot_time()
        cutoff = time.time() - retention
        verdicts: dict[str, tuple[dict, str]] = {}
        probed: dict[str, dict] = {}
        unreachable: set[str] = set()
        for record in registry.load_registry()["sessions"]:
            if not isinstance(record, dict) or not record.get("id") or not isinstance(record.get("name"), str):
                continue
            if record.get("kind") == "local":
                reason = _local_gone(record, host=host, boot=boot, booted=booted, cutoff=cutoff)
                if reason is not None:
                    verdicts[record["id"]] = (deepcopy(record), reason)
                continue
            remote = record.get("remote")
            if not probe_remote or record.get("kind") != "remote" or not isinstance(remote, dict):
                continue
            seen = _last_seen(record, ("refreshed_at", "launched_at", "ended_at", "probed_at"))
            if seen is None or seen > cutoff or _launcher_alive(record) is not False:
                continue
            if record.get("ended_at"):
                verdicts[record["id"]] = (deepcopy(record), "remote session exited cleanly")
                continue
            if not remote.get("session"):
                continue
            alias = str(remote.get("alias") or "")
            alive = None if alias in unreachable else registry._remote_session_alive(record, cfg)
            if alive is False:
                verdicts[record["id"]] = (deepcopy(record), "remote host has no such tmux session")
                continue
            if alive is None:
                # One unreachable host costs one dial per pass.
                unreachable.add(alias)
            probed[record["id"]] = deepcopy(record)
        if not verdicts and not probed:
            return []
        now = registry._now()

        def change(doc):
            kept = []
            changed = False
            for record in doc["sessions"]:
                rid = record.get("id") if isinstance(record, dict) else None
                if rid in verdicts and record == verdicts[rid][0]:
                    removed.append((record, verdicts[rid][1]))
                    changed = True
                    continue
                if rid in probed and record == probed[rid]:
                    record["probed_at"] = now
                    changed = True
                kept.append(record)
            doc["sessions"] = kept
            return changed

        registry._mutate(change, timeout=2)
        for record, reason in removed:
            event = {"event": "session_row_aged_out", "name": record["name"], "kind": record.get("kind")}
            state.LOGGER.warning(json.dumps({**event, "reason": reason}, sort_keys=True))
    except Exception as exc:
        # Exception messages can contain process argv or environment values.
        state.LOGGER.warning("session row age-out unavailable (%s)", type(exc).__name__)
    return removed
