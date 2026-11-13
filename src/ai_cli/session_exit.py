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


def resolve_exit(name: str | None) -> tuple[dict, dict, bool]:
    """Resolve only recorded process identities; ancestry proves an implicit name.

    Old supervisors without a birth identity must be relaunched before they can
    use this command. Environment variables and launcher PIDs are not ownership
    evidence: both can outlive or be inherited by another session.
    """
    import json

    from .process_probe import StartTimeMatch, normalize_proc_start, probe_for
    from .session_adopt import live_sessions

    doc = registry.load_registry()
    host = state.config.load_config(read_only=True).get("machine", {}).get("host_id") or doc["machine"]
    ancestors = {p.pid for p in psutil.Process().parents()}
    sessions = live_sessions()
    probe = probe_for()
    verified = []
    for session in sessions:
        names = (session.name, f"c-{session.name}", f"c-r-{session.name}")
        if (name is None and session.pid not in ancestors) or (name is not None and name not in names):
            continue
        data = json.loads(session.record.read_text(encoding="utf-8"))
        before = psutil.Process(session.pid).create_time()
        if probe.start_time_match(session.pid, normalize_proc_start(data.get("procStart"))) is not StartTimeMatch.MATCH:
            raise ValueError("cannot verify session record process start time; nothing signalled")
        target = ProcessIdentity(session.pid, before)
        _process(target)
        verified.append((session, target))
    if name is None:
        current = [(s, target) for s, target in verified if s.pid in ancestors]
        if len(current) != 1:
            raise ValueError(
                "cannot verify current session: expected one ancestor with a matching session record and process start time"
            )
        session, _target = current[0]
        names = {session.name, f"c-{session.name}", f"c-r-{session.name}"}
        matches = [
            r
            for r in doc["sessions"]
            if r.get("kind") == "local" and r.get("name") in names and r.get("owning_host") == host
        ]
        if len(matches) != 1:
            raise ValueError(
                "cannot verify current session: session record does not identify one owning-host registry row"
            )
        name = matches[0]["name"]
    row = state._find(doc, name)
    if row.get("owning_host") != host or not row.get("generation"):
        raise ValueError("session host or generation cannot be verified")
    saved = state.exit_teardown(row)
    if saved.get("generation") == row["generation"]:
        targets = saved["targets"]
    else:
        supervisor = row.get("supervisor_identity") or {}
        if supervisor.get("generation") != row["generation"]:
            raise ValueError("supervisor identity is missing or stale; relaunch this session before using exit-session")
        target = ProcessIdentity(supervisor["pid"], supervisor["started_at"])
        _process(target)
        agents = [(s, t) for s, t in verified if row["name"] in (s.name, f"c-{s.name}", f"c-r-{s.name}")]
        if len(agents) > 1:
            raise ValueError("multiple agent records claim this session; refusing teardown")
        agent = agents[0][1] if agents else None
        if agent and target.pid not in {p.pid for p in psutil.Process(agent.pid).parents()} | {agent.pid}:
            raise ValueError("agent record is not descended from the recorded supervisor")
        targets = {
            "agent": asdict(agent) if agent else None,
            "supervisor": asdict(target),
            "tmux": supervisor.get("tmux"),
        }
    self_exit = any(t and t.get("pid") in ancestors for k, t in targets.items() if k != "tmux")
    return row, targets, self_exit


def detach_cleanup(name: str, *, host: str, generation: str, timeout: float) -> subprocess.Popen:
    """Continue a committed self-exit outside the agent's terminal/process group."""
    from .session_lifecycle import pinned_python

    path = registry.registry_path().as_posix()
    code = (
        "from pathlib import Path; from ai_cli import session_registry as registry; "
        f"registry.registry_path = lambda: Path({path!r}); "
        "from ai_cli.session_exit import exit_session; "
        f"result = exit_session({name!r}, host={host!r}, generation={generation!r}, timeout={timeout!r}); "
        "sys.exit(0 if result.complete else 1)"
    )
    kwargs = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    return subprocess.Popen(
        pinned_python(code), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
    )


def owning_receipt(name: str, generation: str | None = None, timeout: float = 5) -> dict:
    """Read an owning-host generation, optionally tearing down that exact run."""
    local_host = (
        state.config.load_config(read_only=True).get("machine", {}).get("host_id")
        or registry.load_registry()["machine"]
    )
    row = state._find(registry.load_registry(), name)
    if row.get("owning_host") != local_host or not row.get("generation"):
        raise ValueError("owning host or generation cannot be verified")
    if generation:
        if row["generation"] != generation:
            raise ValueError("session generation changed; nothing signalled")
        row, targets, self_exit = resolve_exit(name)
        if self_exit:
            raise ValueError("remote teardown must run outside the target session")
        result = exit_session(
            name,
            host=local_host,
            generation=generation,
            timeout=timeout,
            agent=ProcessIdentity(**targets["agent"]) if targets.get("agent") else None,
            supervisor=ProcessIdentity(**targets["supervisor"]) if targets.get("supervisor") else None,
            tmux=TmuxIdentity(**targets["tmux"]) if targets.get("tmux") else None,
        )
        if not result.complete:
            raise ValueError("remote cleanup unresolved: " + "; ".join(result.errors))
        row = state._find(registry.load_registry(), name)
    return {
        key: row.get(key)
        for key in (
            "name",
            "owning_host",
            "generation",
            "run_state",
            "changed_at",
            "ended_at",
            "transition_seq",
            "exit",
            "supervisor_exit",
        )
    }


def _remote_receipt(row: dict, *, generation: str | None = None, timeout: float = 5) -> dict:
    import json
    import shlex
    from pathlib import Path

    remote = row.get("remote") or {}
    machine = state.config.get_remote_machine(state.config.load_config(read_only=True), remote.get("alias") or "")
    host = machine.get("vpn_host") or machine.get("host")
    if not host or not remote.get("session"):
        raise ValueError("remote host or session is missing")
    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(machine.get("port", 22))]
    if machine.get("identity_file"):
        args += ["-i", str(Path(machine["identity_file"]).expanduser())]
    args.append(f"{machine['user']}@{host}" if machine.get("user") else host)
    command = ["ai", "sessions", "_exit-receipt", remote["session"]]
    if generation:
        command += [generation, str(timeout)]
    done = subprocess.run(
        [*args, 'export PATH="$HOME/.local/bin:$PATH"; ' + shlex.join(command)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15 + 2 * timeout,
    )
    if done.returncode:
        raise ValueError("owning host did not acknowledge the exit receipt")
    receipt = json.loads(done.stdout)
    if (
        not isinstance(receipt, dict)
        or receipt.get("name") != remote["session"]
        or not isinstance(receipt.get("generation"), str)
        or not receipt["generation"]
        or not receipt.get("owning_host")
        or receipt.get("run_state") not in {"running", "exited", "unknown"}
        or (generation and receipt["generation"] != generation)
        or (row.get("owning_host") and row["owning_host"] != receipt["owning_host"])
    ):
        raise ValueError("invalid owning-host generation receipt")
    return receipt


def _accept_receipt(snapshot: dict, receipt: dict) -> bool:
    teardown = state.exit_teardown(receipt)
    if receipt["run_state"] != "exited" or teardown.get("generation") != receipt["generation"]:
        return False
    if not teardown.get("complete") or not receipt.get("ended_at"):
        raise ValueError("owning-host exit committed; cleanup remains unresolved")

    def update(doc):
        row = next((r for r in doc["sessions"] if r.get("id") == snapshot["id"]), None)
        if row != snapshot:
            raise ValueError("client mirror changed while awaiting receipt")
        row.update(receipt)
        return True

    registry._mutate(update, timeout=2)
    return True


def remote_mirror(name: str | None) -> dict | None:
    """An explicit remote mirror is the only authority for selecting a remote host."""
    if name is None:
        return None
    rows = [r for r in registry.load_registry()["sessions"] if r.get("kind") == "remote" and r.get("name") == name]
    if len(rows) > 1:
        raise ValueError("multiple remote mirrors match; remote exit unresolved")
    return rows[0] if rows else None


def request_remote_exit(row: dict, *, timeout: float, dry_run: bool = False) -> None:
    """Fence an owning-host exit and acknowledge it without signalling the client shell."""
    try:
        receipt = _remote_receipt(row, timeout=timeout)
        if dry_run:
            return
        receipt = _remote_receipt(row, generation=receipt["generation"], timeout=timeout)
        if not _accept_receipt(row, receipt):
            raise ValueError("owning host did not acknowledge a completed exit")
    except (ValueError, OSError, subprocess.SubprocessError, registry.RegistryError, LockException) as exc:
        raise ValueError(f"remote exit unresolved: {exc}") from exc


def receive_remote_exit(record_id: str) -> bool:
    """Check for a manual-exit receipt before either transport retries a connection."""
    import click

    row = next((r for r in registry.load_registry()["sessions"] if r.get("id") == record_id), None)
    if row is None or row.get("kind") != "remote":
        return False
    try:
        receipt = _remote_receipt(row)
    except (ValueError, OSError, subprocess.SubprocessError):
        # Unreachable hosts prove nothing; ordinary transport recovery still applies.
        return False
    try:
        return _accept_receipt(row, receipt)
    except (ValueError, OSError, registry.RegistryError, LockException) as exc:
        raise click.ClickException(f"remote exit unresolved: {exc}") from exc
