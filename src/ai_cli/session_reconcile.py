"""Settle vanished local runs without destroying evidence from an earlier boot."""

import json
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
        for process in psutil.process_iter(["pid", "username", "status"]):
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
                argv = process.cmdline()
                if not argv:
                    return None
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
                # Only agent executables use an inherited marker as identity.
                if not (
                    executable in {"claude", "codex", "gemini", "pi", "node", "nodejs"}
                    or executable.startswith("python")
                ):
                    continue
                environment = process.environ()
                if (
                    environment.get("AI_TMUX_SESSION") == name
                    or environment.get("CLAUDE_CODE_TASK_LIST_ID") == agent_name
                    or any(flag == "--name" and value == agent_name for flag, value in pairwise(argv))
                ):
                    return True
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError, ValueError, KeyError):
                return None
    except (psutil.Error, OSError):
        return None
    return False


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
