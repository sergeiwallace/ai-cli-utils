"""Durable, generation-fenced teardown for an owning-host session."""

import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

import psutil
from portalocker.exceptions import LockException

from . import session_registry as registry
from . import session_run_state as state


@dataclass(frozen=True)
class ProcessIdentity:
    """A PID and its exact ``psutil.Process.create_time()`` timestamp."""

    pid: int
    started_at: float


@dataclass(frozen=True)
class TmuxIdentity:
    """An opaque session ID and its ``@ai_cli_session_generation`` token."""

    session_id: str
    generation: str


@dataclass(frozen=True)
class ExitResult:
    registry_committed: bool
    complete: bool
    errors: tuple[str, ...] = ()


def _process(target: ProcessIdentity) -> psutil.Process | None:
    try:
        proc = psutil.Process(target.pid)
        if proc.create_time() != target.started_at:
            raise ValueError(f"process identity mismatch for pid {target.pid}")
        # A server can be shared by unrelated sessions. This is a refusal check,
        # never a way to discover or select signal targets by process name.
        if proc.name().split(":", 1)[0] in ("tmux", "tmux.exe"):
            raise ValueError("refusing to signal a tmux server process")
        if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return None
        return proc
    except psutil.NoSuchProcess:
        return None


def _tmux(args: list[str]) -> str | None:
    result = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=2, check=False)
    if result.returncode:
        # An already removed session/server is successful idempotent cleanup.
        if any(message in result.stderr for message in ("can't find session:", "no server running on")) or (
            "error connecting to" in result.stderr and "(No such file or directory)" in result.stderr
        ):
            return None
        raise ValueError("tmux ownership or removal could not be established")
    return result.stdout.strip()


def _tmux_server(target: TmuxIdentity) -> int | None:
    answer = _tmux(
        ["display-message", "-p", "-t", target.session_id, "#{session_id}|#{@ai_cli_session_generation}|#{pid}"]
    )
    if answer is None:
        return None
    fields = answer.split("|")
    if len(fields) != 3 or fields[:2] != [target.session_id, target.generation] or not fields[2].isdigit():
        raise ValueError("tmux session identity mismatch")
    return int(fields[2])


def _cleanup(targets: dict, timeout: float) -> list[str]:
    processes = [ProcessIdentity(**targets[key]) for key in ("agent", "supervisor") if targets.get(key)]
    tmux = TmuxIdentity(**targets["tmux"]) if targets.get("tmux") else None
    # Inspect the exact session before signalling anything: a supplied server pid
    # must never turn a session teardown into teardown of a shared server.
    server = _tmux_server(tmux) if tmux else None
    if server in {target.pid for target in processes}:
        raise ValueError("refusing to signal the shared tmux server pid")
    for target in processes:
        _process(target)
    errors = []

    def send(target, *, force=False):
        proc = _process(target)
        if proc is None:
            return
        if force:
            proc.kill()  # psutil rechecks identity and uses the native Windows API.
        else:
            # Native Windows has no POSIX stop/continue signals.
            if sys.platform != "win32" and proc.status() == psutil.STATUS_STOPPED:
                proc.send_signal(signal.SIGCONT)
                proc = _process(target)
                if proc is None:
                    return
            proc.send_signal(signal.SIGTERM)

    pending = list(dict.fromkeys(processes))
    for force in (False, True):
        survivors = []
        for target in pending:
            try:
                send(target, force=force)
                survivors.append(target)
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, ValueError, OSError) as exc:
                errors.append(str(exc))
        deadline = time.monotonic() + timeout
        pending = survivors
        while pending:
            survivors = []
            for target in pending:
                try:
                    if _process(target) is not None:
                        survivors.append(target)
                except (psutil.Error, ValueError, OSError) as exc:
                    errors.append(str(exc))
            pending = survivors
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    errors.extend(f"pid {target.pid} survived teardown" for target in pending)
    # A refused identity must also prevent indirect termination through tmux.
    if tmux and not errors and server is not None:
        answer = _tmux(
            [
                "if-shell",
                "-F",
                "-t",
                tmux.session_id,
                "#{==:#{session_id}|#{@ai_cli_session_generation}," + tmux.session_id + "|" + tmux.generation + "}",
                f"kill-session -t '{tmux.session_id}'",
                "display-message -p __ownership_mismatch__",
            ]
        )
        if answer is not None and "__ownership_mismatch__" in answer:
            errors.append("tmux session identity mismatch")
    return errors


def exit_session(
    name: str,
    *,
    host: str,
    generation: str,
    agent: ProcessIdentity | None = None,
    supervisor: ProcessIdentity | None = None,
    tmux: TmuxIdentity | None = None,
    timeout: float = 5,
) -> ExitResult:
    """Persist exit before bounded cleanup; execute only on the owning host.

    Callers supply verified run identities, never discovered process names. A retry
    may omit all targets to reuse the identities stored with the committed exit.
    ``host`` must be the caller's locally established host identity. Each wait is
    bounded by ``timeout``; each tmux operation has a separate two-second bound.
    """
    committed = False
    try:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        for target in (agent, supervisor):
            if target and (
                type(target.pid) is not int
                or target.pid <= 1
                or target.pid == os.getpid()
                or not math.isfinite(target.started_at)
                or target.started_at <= 0
            ):
                raise ValueError("invalid process identity or caller pid")
        if tmux and (
            not re.fullmatch(r"\$[0-9]+", tmux.session_id) or not re.fullmatch(r"[A-Za-z0-9_-]+", tmux.generation)
        ):
            raise ValueError("invalid tmux identity")
        targets = {
            "agent": asdict(agent) if agent else None,
            "supervisor": asdict(supervisor) if supervisor else None,
            "tmux": asdict(tmux) if tmux else None,
        }
        saved = state.request_manual_exit(name, host=host, generation=generation, targets=targets)
        committed = True
        errors = []

        def clean(doc):
            row = state._find(doc, name)
            if row.get("generation") != generation or row.get("owning_host") != host:
                raise ValueError("session host or generation changed before cleanup")
            try:
                errors.extend(_cleanup(state.exit_teardown(saved)["targets"], timeout))
            except (ValueError, OSError, subprocess.SubprocessError, psutil.Error) as exc:
                errors.append(str(exc))
            state.exit_teardown(row).update(errors=errors, complete=not errors)
            row["exit"] = dict(row["supervisor_exit"])
            return True

        # Keep begin()/other transitions out until all signals and tmux fencing
        # finish. The durable exit was committed in the preceding transaction.
        registry._mutate(clean, timeout=2)
        return ExitResult(True, not errors, tuple(errors))
    except (
        ValueError,
        OSError,
        subprocess.SubprocessError,
        psutil.Error,
        registry.RegistryError,
        LockException,
    ) as exc:
        return ExitResult(committed, False, (str(exc),))
