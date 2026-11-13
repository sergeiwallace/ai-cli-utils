"""Session lifecycle and publication commands."""

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import click
import psutil

from . import (
    output,
    session_boot_unit,
    session_list_view,
    session_publish_timer,
    session_reconcile,
    session_state_publish,
)
from . import session_registry as registry
from . import session_run_state as state
from .cli_help import HelpGroup
from .iterm2_restore import LAUNCH_REASON_VAR
from .output import Untagged
from .process_probe import StartTimeMatch, normalize_proc_start, probe_for
from .session_enrol import enrol, heal_live_sessions


@click.group(
    "sessions",
    cls=HelpGroup,
    invoke_without_command=True,
    help="List sessions and manage their run state, relaunch and publication. Bare `ai sessions` runs "
    "`ai sessions list` with the [sessions_list] defaults: the running sessions of this host and its pair, "
    "grouped by host, active first",
)
@click.pass_context
def sessions(ctx):
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_sessions)


sessions.add_command(enrol)


@sessions.command(
    "mark",
    help="Record a session's run state in the registry: running, or exited with a cause (-c) and "
    "optional JSON evidence (-e)",
)
@click.argument("name")
@click.argument("status", type=click.Choice(["running", "exited"]))
@click.option(
    "-c", "--cause", type=click.Choice(registry.EXIT_CAUSES), help="Why the session exited; required with exited"
)
@click.option("-e", "--evidence", default="{}", help="Exit evidence as a JSON object")
@click.option("--generation", hidden=True)
@click.option("--sequence", type=int, hidden=True)
def mark(name, status, cause, evidence, generation, sequence):
    if status == "exited" and cause is None:
        raise click.UsageError("exited requires -c/--cause")
    try:
        parsed = json.loads(evidence)
    except json.JSONDecodeError as exc:
        raise click.UsageError("-e/--evidence must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise click.UsageError("-e/--evidence must be a JSON object")
    if status == "exited" and generation:
        row = state._find(registry.load_registry(), name)
        if (
            row.get("generation") == generation
            and state.run_state(row) == "exited"
            and state.exit_teardown(row).get("generation") == generation
        ):
            # The exit worker already committed the final state. Preserve its
            # cleanup targets and errors while the supervisor acknowledges it.
            return
    if not state.mark(
        name, status, cause=cause or "unknown", evidence=parsed, generation=generation, sequence=sequence
    ):
        raise click.ClickException("session state persistence refused; retry before returning to the shell")


@sessions.command("_exit-receipt", hidden=True)
@click.argument("name")
@click.argument("generation", required=False)
@click.argument("timeout", type=click.FloatRange(min=0), default=5.0)
def exit_receipt(name, generation, timeout):
    from .session_exit import owning_receipt

    try:
        output.raw(Untagged.JSON, json.dumps(owning_receipt(name, generation, timeout)))
    except (ValueError, OSError, registry.RegistryError) as exc:
        raise click.ClickException(str(exc)) from exc


@sessions.command("_begin", hidden=True)
@click.argument("name")
@click.option("--launch-metadata", hidden=True, help="Generated launcher metadata for registration recovery")
@click.option("--supervisor-pid", type=int, hidden=True)
def begin(name, launch_metadata, supervisor_pid):
    # Acquisition precedes the generated shell's terminal reset. A previous
    # agent may have left extended keys enabled, encoding Ctrl+C as text. Use
    # the controlling terminal: stdout is a captured generation-token reply.
    previous_int = None
    if os.name == "posix" and re.fullmatch(r"(?:cx|c|g|p)-[A-Za-z0-9_-]+", name):
        import termios

        from .transport import _TERMINAL_RESTORE

        def interrupted(_signum, _frame):
            # Old supervisors already check this file before considering a
            # respawn; they otherwise retry a child's ordinary SIGINT status.
            marker = int_exit_marker(name)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            raise KeyboardInterrupt

        previous_int = signal.signal(signal.SIGINT, interrupted)
        try:
            with ExitStack() as stack:
                terminal_fd = 2
                if not os.isatty(terminal_fd):
                    terminal = stack.enter_context(Path("/dev/tty").open("r+b", buffering=0))
                    terminal_fd = terminal.fileno()
                os.write(terminal_fd, _TERMINAL_RESTORE.encode())
                attrs = termios.tcgetattr(terminal_fd)
                attrs[3] |= termios.ISIG | termios.ICANON | termios.ECHO
                attrs[6][termios.VINTR] = b"\x03"
                termios.tcsetattr(terminal_fd, termios.TCSANOW, attrs)
        except (OSError, termios.error):
            pass
    try:
        recipe = None
        if launch_metadata is not None:
            meta = json.loads(launch_metadata)
            engine = meta["engine"]
            host = state.config.load_config().get("machine", {}).get("host_id") or registry.load_registry()["machine"]
            cwd = meta.get("worktree_dir") or str(Path.cwd())
            if meta["session"] != name or name not in (f"{engine}-{meta['ai_name']}", f"{engine}-r-{meta['ai_name']}"):
                raise ValueError("launch metadata does not match the session identity")
            prefix = meta["project_prefix"]
            if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", prefix):
                raise ValueError("invalid launch project prefix")
            options = {key: meta.get(key, False) for key in ("sandbox", "is_remote", "notify")}
            if any(not isinstance(value, bool) for value in options.values()):
                raise ValueError("invalid launch options")
            argv = ["ai", engine, name, "--project-prefix", prefix]
            project = meta.get("project_name", "")
            if project:
                argv += ["-p", project]
            for key in ("sandbox", "is_remote", "notify"):
                if options[key]:
                    argv.append("--" + key.replace("_", "-"))
            recipe = {
                "engine": engine,
                "session_name": name,
                "owning_host": host,
                "cwd": cwd,
                "project": project,
                "argv": argv,
                "options": options,
                "extra_args": [],
            }
            if not state.valid_boot_recipe({"name": name, "owning_host": host, "boot_recipe": recipe}):
                raise ValueError("invalid launch recovery recipe")
        heal_live_sessions()
        import psutil

        supervisor = None
        if supervisor_pid is not None:
            parent = psutil.Process(supervisor_pid)
            supervisor = {"pid": parent.pid, "started_at": parent.create_time()}
        output.raw(
            Untagged.INTERNAL_REPLY,
            state.begin(name, recovery_recipe=recipe, supervisor=supervisor),
        )
    except Exception as exc:
        raise click.ClickException(f"cannot acquire a durable supervisor generation: {exc}") from exc
    finally:
        if previous_int is not None:
            signal.signal(signal.SIGINT, previous_int)


_BIND_TMUX_ATTEMPTS = 4


@sessions.command("_bind-exit-tmux", hidden=True)
@click.argument("name")
@click.argument("generation")
@click.argument("session_id")
@click.argument("token")
def bind_exit_tmux(name, generation, session_id, token):
    from .session_exit import TmuxIdentity, _tmux_server

    if not re.fullmatch(r"\$[0-9]+", session_id) or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise click.ClickException("invalid tmux identity")
    # This runs before the supervisor starts its child, and a failure ends the launch, so a tmux
    # server that is merely slow to answer on a loaded host must not be mistaken for a missing one.
    server = None
    for attempt in range(_BIND_TMUX_ATTEMPTS):
        try:
            server = _tmux_server(TmuxIdentity(session_id, token))
            break
        except ValueError as exc:
            if attempt == _BIND_TMUX_ATTEMPTS - 1:
                raise click.ClickException(str(exc)) from exc
            time.sleep(0.5)
    if server is None:
        raise click.ClickException("tmux ownership is absent")

    def bind(doc):
        row = state._find(doc, name)
        supervisor = row.get("supervisor_identity") or {}
        if row.get("generation") != generation or supervisor.get("generation") != generation:
            raise ValueError("stale supervisor generation")
        supervisor["tmux"] = {"session_id": session_id, "generation": token}
        return True

    try:
        registry._mutate(bind, timeout=2)
    except (ValueError, OSError, registry.RegistryError) as exc:
        raise click.ClickException(str(exc)) from exc


@sessions.command("_sequence", hidden=True)
def sequence():
    output.raw(Untagged.INTERNAL_REPLY, str(time.monotonic_ns()))


@sessions.command(
    "publish",
    help="Publish a snapshot of the session registry to the configured publication repo, when it "
    "has unpublished changes and the debounce window has passed",
)
@click.option("-n", "--now", is_flag=True, help="Ignore the debounce window")
@click.option("-t", "--tick", is_flag=True, help="Run one publication timer tick")
def publish(now, tick):
    result = session_state_publish.publish(now=now, tick=tick)
    output.emit(output.Tag.SESSIONS, "sync", result.reason)


def _local_host(doc: dict) -> str:
    return state.config.load_config(read_only=True).get("machine", {}).get("host_id") or doc["machine"]


def _pair_host(view: dict) -> tuple[str, str]:
    """The paired host id and, when there is none, why."""
    if view["pair_host"]:
        return view["pair_host"], ""
    try:
        alias = state.config.resolve_remote_machine_alias(state.config.load_config(read_only=True))
    except state.config.RemoteMachineError as exc:
        return "", f"no pair host: {exc}"
    if not alias:
        return "", "no pair host: set [sessions_list] pair_host or a [remote.machines] default"
    return alias, ""


def _snapshot_rows(host: str, doc: dict) -> list[session_list_view.Row]:
    rows = []
    for name, fields in doc["sessions"].items():
        if not isinstance(fields, dict):
            continue
        rows.append(
            session_list_view.Row(
                host=host,
                name=str(name),
                status=str(fields.get("run_state") or "unknown"),
                cause=str(fields.get("cause") or ""),
                changed=str(fields.get("changed_at") or ""),
                relaunch=str(fields.get("relaunch") or "auto"),
            )
        )
    return rows


@sessions.command(
    "list",
    help="List sessions as a table grouped by host, active sessions first: this host's running sessions and, "
    "with the default pair scope, its paired host's published ones. -a/--all adds exited and unknown rows; "
    "-S/--scope host|pair|fleet chooses the hosts; -g/--group-by and -s/--sort-by override the [sessions_list] "
    "defaults in config.toml. -j/--json prints this host's registry records unchanged",
)
@click.option("-j", "--json", "as_json", is_flag=True, help="Print this host's registry records as JSON")
@click.option("-a", "--all", "show_all", is_flag=True, default=None, help="Include exited and unknown sessions")
@click.option(
    "-S",
    "--scope",
    type=click.Choice(session_list_view.SCOPES),
    help="Hosts to list: this host, this host and its pair, or every host that publishes its sessions",
)
@click.option("-g", "--group-by", type=click.Choice(session_list_view.GROUP_KEYS), help="Column to group rows by")
@click.option(
    "-s",
    "--sort-by",
    metavar="KEYS",
    help=f"Comma-separated sort keys, first key first: {', '.join(session_list_view.SORT_KEYS)}",
)
def list_sessions(as_json=False, show_all=None, scope=None, group_by=None, sort_by=None):
    if as_json and scope in ("pair", "fleet"):
        raise click.UsageError("-j/--json prints this host's registry only; drop -S/--scope or -j")
    try:
        view = session_list_view.settings()
        if sort_by is not None:
            view["sort_by"] = session_list_view.parse_sort(sort_by, "-s/--sort-by")
    except session_list_view.ViewError as exc:
        raise click.UsageError(str(exc)) from exc
    session_reconcile.reconcile()
    session_reconcile.age_out()
    doc = registry.load_registry()
    records = [
        {
            "name": r["name"],
            "run_state": state.run_state(r),
            "cause": state.cause(r) if state.run_state(r) == "exited" else "",
            "changed_at": r.get("changed_at", ""),
            "relaunch": r.get("relaunch", "auto"),
        }
        for r in doc["sessions"]
    ]
    if as_json:
        output.raw(Untagged.JSON, json.dumps(records))
        return
    local = _local_host(doc)
    rows = [
        session_list_view.Row(
            host=local,
            name=r["name"],
            status=r["run_state"],
            cause=r["cause"],
            changed=r["changed_at"],
            relaunch=r["relaunch"],
        )
        for r in records
    ]
    notes = []
    scope = scope or view["scope"]
    if scope != "host":
        wanted, why = _pair_host(view) if scope == "pair" else (None, "")
        if why:
            notes.append(why)
        if wanted != "":
            snapshots = session_state_publish.read_snapshots()
            if snapshots.problem:
                notes.append(snapshots.problem)
            hosts = sorted(h for h in snapshots.hosts if h != local and (wanted is None or h == wanted))
            if wanted is not None and wanted != local and wanted not in snapshots.hosts and not snapshots.problem:
                notes.append(f"{wanted}: no published snapshot")
            for host in hosts:
                published = snapshots.hosts[host]
                rows += _snapshot_rows(host, published)
                notes.append(f"{host}: published snapshot from {published.get('published_at') or 'an unknown time'}")
    group = group_by or view["group_by"]
    everything = view["show_all"] if show_all is None else show_all
    groups = session_list_view.arrange(
        rows, local_host=local, group_by=group, sort_by=view["sort_by"], show_all=everything
    )
    if groups:
        headers, cells = session_list_view.table(groups, group)
        output.table(headers, cells)
    else:
        shown = "sessions" if everything else "running sessions"
        hint = "" if everything else "; -a/--all shows every row"
        output.emit(output.Tag.SESSIONS, "list", f"no {shown} recorded{hint}")
    for note in notes:
        output.emit(output.Tag.SESSIONS, "list", note)


_SESSION_SLUG = r"[A-Za-z0-9_-]+"


def _session_slug(_ctx, _param, value):
    if value is not None and not re.fullmatch(_SESSION_SLUG, value):
        raise click.BadParameter("invalid session name; use only letters, digits, underscores and hyphens")
    return value


def int_exit_marker(tmux_session: str) -> Path:
    """The file whose presence tells ``tmux_session``'s supervisor to stop instead of relaunching."""
    return state.config.get_xdg_state_home() / f"session-int-exit-{tmux_session}"


STOP_TIMEOUT_SECONDS = 30.0


@dataclass
class StopReport:
    """What :func:`stop_session` did, and what still runs in the slot afterwards."""

    name: str
    #: tmux sessions whose live supervisor was given the stop marker.
    marked: list[str] = field(default_factory=list)
    supervisors: list[int] = field(default_factory=list)
    #: Claude Code pids sent SIGTERM.
    signalled: list[int] = field(default_factory=list)
    #: Each process or tmux session still in the slot when the wait ended.
    remaining: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.marked or self.signalled or self.remaining)

    @property
    def stopped(self) -> bool:
        return not self.remaining


def _pane_pids(tmux_session: str) -> list[int]:
    try:
        listed = subprocess.run(
            ["tmux", "list-panes", "-s", "-t", f"={tmux_session}", "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if listed.returncode != 0:
        return []
    return [int(token) for token in (listed.stdout or "").split() if token.isdigit()]


def _slot_processes(probe, supervisors: dict[int, object]) -> set[int]:
    """The pids running under ``supervisors``, read only while each supervisor is still the captured process."""
    found: set[int] = set()
    for pid, identity in supervisors.items():
        with contextlib.suppress(psutil.Error):
            children = {child.pid for child in psutil.Process(pid).children(recursive=True)}
            if probe.capture_identity(pid) == identity:
                found |= children
    return found


def _agent_identity(probe, session) -> object | None:
    """The process identity of a live Claude Code record, or None when the record cannot prove it."""
    try:
        record = json.loads(session.record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if probe.start_time_match(session.pid, normalize_proc_start(record.get("procStart"))) is not StartTimeMatch.MATCH:
        return None
    return probe.capture_identity(session.pid)


def stop_session(
    name: str,
    *,
    timeout: float = STOP_TIMEOUT_SECONDS,
    claude_home: Path | None = None,
    poll_seconds: float = 0.2,
) -> StopReport:
    """Stop the session ``name`` so its supervisor exits instead of relaunching the agent.

    A supervisor generated by ``session_script.py`` treats an agent exit as a restart
    unless its stop marker exists, and deletes that marker when it starts, so the
    marker is written only for a tmux session whose supervisor is running now. Then
    each Claude Code process recorded under ``name``, or running under one of those
    supervisors whatever name its record carries (as after a rename), is sent SIGTERM,
    by its pid and only when its record's ``procStart`` proves which process holds
    that pid. The call returns once nothing is left in the slot or ``timeout`` elapses.
    """
    from .session_adopt import live_sessions
    from .session_migrate import _session_variants, _tmux_names

    if not re.fullmatch(_SESSION_SLUG, name):
        raise ValueError(f"invalid session name: {name!r}")
    probe = probe_for()
    report = StopReport(name=name)
    variants = set(_session_variants(name))
    tracked: dict[int, object] = {}
    for tmux_session in sorted(set(_tmux_names()) & variants):
        live = {
            pid: identity
            for pid in _pane_pids(tmux_session)
            if not probe.has_ended(pid) and (identity := probe.capture_identity(pid))
        }
        if not live:
            continue
        marker = int_exit_marker(tmux_session)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("stop\n", encoding="utf-8")
        report.marked.append(tmux_session)
        report.supervisors.extend(live)
        tracked.update(live)
    slot = _slot_processes(probe, tracked)

    def in_slot(session) -> bool:
        return session.name == name or session.pid in slot

    for session in live_sessions(claude_home):
        if not in_slot(session):
            continue
        identity = _agent_identity(probe, session)
        if identity is None:
            continue
        # On Windows os.kill with SIGTERM terminates the process outright; there is no handler to run.
        with contextlib.suppress(ProcessLookupError):
            os.kill(session.pid, signal.SIGTERM)
        report.signalled.append(session.pid)
        tracked[session.pid] = identity

    def occupants() -> list[str]:
        found = [
            f"supervisor pid {pid}"
            for pid in report.supervisors
            if not probe.has_ended(pid) and probe.capture_identity(pid) == tracked[pid]
        ]
        for session in live_sessions(claude_home):
            if not in_slot(session):
                continue
            if session.pid in report.signalled or _agent_identity(probe, session) is not None:
                found.append(f"Claude Code pid {session.pid}")
            else:
                found.append(f"Claude Code pid {session.pid} (its record cannot prove the pid; not signalled)")
        found.extend(f"tmux session {tmux}" for tmux in sorted(set(_tmux_names()) & variants))
        return found

    deadline = time.monotonic() + timeout
    while (remaining := occupants()) and time.monotonic() < deadline:
        time.sleep(poll_seconds)
    report.remaining = remaining
    if report.stopped:
        session_reconcile.reconcile(names=variants)
    return report


@sessions.command("stop", help="Stop a managed session so its wrapper does not relaunch the agent")
@click.argument("name", callback=_session_slug)
@click.option(
    "-t",
    "--timeout",
    type=click.FloatRange(min=0),
    default=STOP_TIMEOUT_SECONDS,
    show_default=True,
    help="Seconds to wait for the session to exit",
)
def stop(name, timeout):
    report = stop_session(name, timeout=timeout)
    if not report.found:
        output.emit(output.Tag.SESSIONS, "stop", f"nothing is running for {name}")
        return
    for tmux_session in report.marked:
        output.emit(output.Tag.SESSIONS, "stop", f"{tmux_session}: stop marker written for its supervisor")
    for pid in report.signalled:
        output.emit(output.Tag.SESSIONS, "stop", f"{name}: sent SIGTERM to Claude Code pid {pid}")
    if report.stopped:
        output.emit(output.Tag.SESSIONS, "stop", f"{name}: stopped; nothing is left running in its slot")
        return
    raise click.ClickException(f"{name}: still running after {timeout:g}s: {', '.join(report.remaining)}")


def _owned_record(doc: dict, slug: str) -> dict:
    matches = [record for record in doc["sessions"] if record.get("name") == slug]
    if not matches:
        raise click.ClickException(f"unknown session: {slug}")
    host = state.config.load_config().get("machine", {}).get("host_id") or doc["machine"]
    for record in matches:
        if record.get("kind") == "local" and record.get("owning_host") == host:
            return record
    owners = sorted({str(r.get("owning_host")) for r in matches if r.get("kind") == "local"})
    detail = f"; its owning_host is {', '.join(map(repr, owners))} and this host is {host!r}" if owners else ""
    raise click.ClickException(f"{slug}: foreign or remote session; not owned by this host{detail}")


@sessions.command(
    "wind-down",
    help="Stop a running session for a maintenance window, recording maintenance_wind_down so the "
    "boot relaunch or a named relaunch brings it back",
)
@click.argument("slug", callback=_session_slug)
@click.option(
    "-t",
    "--timeout",
    type=click.FloatRange(min=0),
    default=STOP_TIMEOUT_SECONDS,
    show_default=True,
    help="Seconds to wait for the session to exit",
)
@click.option("-d", "--dry-run", is_flag=True, help="Check the session and print the plan without writing or stopping")
def wind_down(slug, timeout, dry_run):
    """Record the wind-down cause first, then end the session through its wrapper's clean exit."""
    selected = {}

    def change(doc):
        record = _owned_record(doc, slug)
        alive = registry._local_session_alive(slug)
        if alive is None:
            raise click.ClickException(f"{slug}: tmux liveness could not be established")
        if not alive:
            raise click.ClickException(f"{slug}: not running; nothing to wind down")
        refusal = state.boot_recipe_refusal(record)
        if refusal:
            raise click.ClickException(f"{slug}: {refusal}; it could not be relaunched")
        if not dry_run:
            state.request_wind_down(record)
        selected.update(deepcopy(record))
        return True

    if dry_run:
        change(registry.load_registry())
        output.emit(output.Tag.SESSIONS, "session", f"{slug}: would record {state.WIND_DOWN_CAUSE} and stop it")
        return
    registry._mutate(change, timeout=2)
    engine = selected["boot_recipe"]["engine"]
    report = stop_session(slug.removeprefix(f"{engine}-"), timeout=timeout)
    if not report.stopped:
        raise click.ClickException(f"{slug}: still running after {timeout:g}s: {', '.join(report.remaining)}")
    record = _owned_record(registry.load_registry(), slug)
    settled = state.cause(record, registry.current_boot_id())
    if state.run_state(record) != "exited" or settled != state.WIND_DOWN_CAUSE:
        raise click.ClickException(
            f"{slug}: stopped, but its record shows {state.run_state(record)} with cause {settled}"
        )
    output.emit(output.Tag.SESSIONS, "session", f"{slug}: stopped; exit cause {settled} recorded")


@sessions.command("policy")
@click.argument("slug", callback=_session_slug)
@click.argument("policy", type=click.Choice(["auto", "never", "always"]))
def policy(slug, policy):
    """Set a local session's boot relaunch policy as its owner's choice and queue publication.

    No automatic write replaces it afterwards. It records relaunch_changed_at and leaves
    changed_at, the run-state time the list shows, unchanged.
    """

    def change(doc):
        record = _owned_record(doc, slug)
        record.update(relaunch=policy, relaunch_set_by="owner", relaunch_changed_at=registry._now())
        return True

    registry._mutate(change, timeout=2)
    output.emit(output.Tag.SESSIONS, "session", f"{slug}: relaunch policy {policy}")


@sessions.command("repair-policy")
@click.option("-d", "--dry-run", is_flag=True, help="Print what would change without writing")
def repair_policy(dry_run):
    """Give this host's sessions left at an unattributed never the default boot policy auto.

    Enrolment and recovery once wrote never for every session they registered, without
    recording who chose it. Each local record of this host that is not a probe, has policy
    never and has no relaunch_set_by becomes auto with relaunch_set_by default. A policy set
    with `ai sessions policy` records its owner and is left alone. A never set by hand before
    relaunch_set_by existed cannot be told apart and is repaired too, so set it again.
    Running it twice changes nothing the second time.
    """
    found = []

    def change(doc):
        host = _local_host(doc)
        for record in doc["sessions"]:
            if (
                record.get("kind") == "local"
                and record.get("owning_host") == host
                and not record.get("probe")
                and record.get("relaunch") == "never"
                and "relaunch_set_by" not in record
            ):
                found.append(record["name"])
                record.update(relaunch="auto", relaunch_set_by="default")
        return bool(found) and not dry_run

    if dry_run:
        change(registry.load_registry())
    else:
        registry._mutate(change, timeout=2)
    verb = "would set" if dry_run else "set"
    for name in found:
        output.emit(output.Tag.SESSIONS, "session", f"{name}: relaunch policy never {verb} to auto (default)")
    if not found:
        output.emit(output.Tag.SESSIONS, "session", "nothing to repair")


def _launch_recipe(record: dict, *, cause: str | None = None, allow_partial_migration: bool = False) -> None:
    from .launch_logging import create_launch_log
    from .main import _do_session_launch

    recipe = record["boot_recipe"]
    options = recipe["options"]
    argv = recipe["argv"]
    prefix = argv[argv.index("--project-prefix") + 1] if "--project-prefix" in argv else ""
    previous_cwd = Path.cwd()
    previous_reason = os.environ.get(LAUNCH_REASON_VAR)
    # A replay writes a launch log like `ai c` does, so what it reported, such as the parts of
    # a partial migration it was told to start past, can be read after the fact.
    launch_log = create_launch_log(record["name"])
    previous_sink = output.attach_file_sink(launch_log.write_line)
    try:
        os.chdir(recipe["cwd"])
        os.environ[LAUNCH_REASON_VAR] = (
            cause or record.get("boot_decision", {}).get("cause") or state.cause(record, registry.current_boot_id())
        )
        _do_session_launch(
            engine=recipe["engine"],
            name=recipe["session_name"],
            resume=False,
            once=options.get("once", False),
            bare=False,
            notify=options.get("notify", False),
            sandbox=options.get("sandbox", False),
            no_worktree=True,
            remote=False,
            project=recipe["project"],
            is_remote=options.get("is_remote", False),
            project_prefix_override=prefix,
            extra_args=recipe["extra_args"],
            config=state.config.load_config(),
            no_direnv=options.get("no_direnv", False),
            detached=True,
            replay_recipe=recipe,
            allow_partial_migration=allow_partial_migration,
        )
    finally:
        output.attach_file_sink(previous_sink)
        launch_log.close()
        os.chdir(previous_cwd)
        if previous_reason is None:
            os.environ.pop(LAUNCH_REASON_VAR, None)
        else:
            os.environ[LAUNCH_REASON_VAR] = previous_reason


def _launch_failure(exc: BaseException) -> str:
    """The recorded reason a replay did not start: a partial-migration refusal names its finish command."""
    from .main import PartialMigrationRefusal

    if isinstance(exc, PartialMigrationRefusal):
        return f"refused: {exc.reason}"
    return f"launch failed ({type(exc).__name__})"


def _boot_check(record: dict, cfg: dict, host: str, boot_id: str | None) -> str | None:
    alive = False
    if (
        cfg["on_boot"]
        and record.get("kind") == "local"
        and record.get("owning_host") == host
        and not state.tombstone_path(record["name"], host).exists()
    ):
        alive = registry._local_session_alive(record["name"])
    return state.boot_veto(record, cfg=cfg, host=host, boot_id=boot_id, alive=alive)


def _wait_for_boot_recipe(recipe: dict, cfg: dict) -> str | None:
    deadline = time.monotonic() + max(0, float(cfg["network_wait_seconds"]))
    while True:
        ready = Path(recipe["cwd"]).is_dir()
        reason = "session directory is unavailable"
        if ready and cfg["network_probe"]:
            reason = "network probe is unavailable"
            try:
                host, port = cfg["network_probe"].rsplit(":", 1)
                with socket.create_connection(
                    (host, int(port)), timeout=max(0.001, min(2, deadline - time.monotonic()))
                ):
                    pass
            except (OSError, ValueError):
                ready = False
        if ready:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return reason
        time.sleep(min(1, remaining))


def _relaunch_named(slug: str, *, dry_run: bool, allow_partial_migration: bool = False) -> None:
    selected = {}

    def select(doc):
        record = _owned_record(doc, slug)
        alive = registry._local_session_alive(slug)
        if alive is not False:
            reason = "tmux session already exists" if alive else "tmux liveness could not be established"
            raise click.ClickException(f"{slug}: {reason}")
        refusal = state.boot_recipe_refusal(record)
        if refusal:
            raise click.ClickException(f"{slug}: {refusal}")
        if not dry_run:
            now = registry._now()
            record.update(
                changed_at=now,
                relaunch_decision={"cause": "remote_request", "reason": "relaunch requested", "at": now},
            )
        selected.update(deepcopy(record))
        return True

    if dry_run:
        select(registry.load_registry())
        output.emit(output.Tag.SESSIONS, "session", f"{slug}: would relaunch detached: remote_request")
        return
    registry._mutate(select, timeout=2)
    failed = False
    try:
        _launch_recipe(selected, cause="remote_request", allow_partial_migration=allow_partial_migration)
        reason = "relaunched detached"
    except (Exception, SystemExit) as exc:
        reason = _launch_failure(exc)
        failed = True

    def finish(doc):
        record = _owned_record(doc, slug)
        if record.get("relaunch_decision") != selected["relaunch_decision"]:
            return False
        record["relaunch_decision"]["reason"] = reason
        return True

    registry._mutate(finish, timeout=2)
    state.LOGGER.info("%s: %s", slug, reason)
    if failed:
        raise click.ClickException(f"{slug}: {reason}")
    output.emit(output.Tag.SESSIONS, "session", f"{slug}: {reason}")


@sessions.command("relaunch")
@click.argument("slug", required=False, callback=_session_slug)
@click.option("-b", "--boot", is_flag=True, help="Replay eligible owning-host sessions after boot")
@click.option("-d", "--dry-run", is_flag=True, help="Print decisions without writing or launching")
@click.option(
    "-P",
    "--allow-partial-migration",
    is_flag=True,
    help="Relaunch a session even though a recorded migration to its name is partial; the parts left "
    "are written into its launch log",
)
def relaunch(slug, boot, dry_run, allow_partial_migration):
    """Relaunch recorded sessions detached, without a terminal application.

    A session whose recorded migration is partial is refused, with the parts left and the
    command that finishes it; the others still relaunch, and the command exits 1.

    A session whose boot recipe cannot replay is refused as "unsupported or incomplete boot
    recipe: " followed by the first field that failed, in a dry run too. A session recorded
    under another host id is refused naming both host ids.
    """
    if boot == (slug is not None):
        raise click.UsageError("provide either a session name or --boot, never both")
    if sys.platform == "win32":
        raise click.ClickException("boot relaunch is supported on macOS and Linux")
    if slug is not None:
        if not dry_run:
            session_reconcile.reconcile(names={slug})
        _relaunch_named(slug, dry_run=dry_run, allow_partial_migration=allow_partial_migration)
        return
    if not dry_run:
        session_reconcile.reconcile()
    heal_live_sessions(dry_run=dry_run)
    cfg = state.settings()
    doc = registry.load_registry()
    host = state.config.load_config().get("machine", {}).get("host_id") or doc["machine"]
    boot_id = registry.current_boot_id() if cfg["on_boot"] else None
    failed = False
    if not cfg["on_boot"]:
        output.emit(output.Tag.SESSIONS, "session", "session_relaunch.on_boot is off")
        state.LOGGER.info("session_relaunch.on_boot is off")
    for snapshot in doc["sessions"]:
        veto = _boot_check(snapshot, cfg, host, boot_id)
        if dry_run:
            reason = veto or f"would relaunch detached: {state.cause(snapshot, boot_id)}"
            output.emit(output.Tag.SESSIONS, "session", f"{snapshot['name']}: {reason}")
            continue
        readiness = _wait_for_boot_recipe(snapshot["boot_recipe"], cfg) if veto is None else None
        selected = {}

        def reserve(current, snapshot=snapshot, readiness=readiness, selected=selected, initial_veto=veto):
            for record in current["sessions"]:
                if (record.get("kind"), record.get("owning_host"), record.get("name")) != (
                    snapshot.get("kind"),
                    snapshot.get("owning_host"),
                    snapshot.get("name"),
                ):
                    continue
                reason = _boot_check(record, cfg, host, boot_id) or initial_veto or readiness
                # A concurrent explicit launch can change the recipe during the wait.
                if reason is None and record.get("boot_recipe") != snapshot.get("boot_recipe"):
                    reason = "boot recipe changed during readiness wait"
                record["boot_decision"] = {
                    "boot_id": boot_id,
                    "cause": state.cause(record, boot_id),
                    "reason": reason or "relaunch reserved",
                    "at": registry._now(),
                }
                if reason is None:
                    record.update(relaunched_boot_id=boot_id, run_state="relaunched", changed_at=registry._now())
                selected.update(record=deepcopy(record), veto=reason)
                return True
            return False

        registry._mutate(reserve, timeout=2)
        if not selected:
            continue
        record, veto = selected["record"], selected["veto"]
        if veto is None:
            try:
                _launch_recipe(record, allow_partial_migration=allow_partial_migration)
                reason = "relaunched detached"
            except (Exception, SystemExit) as exc:
                reason = f"{_launch_failure(exc)}; boot reservation retained"
                failed = True

            def finish(current, record=record, reason=reason):
                for item in current["sessions"]:
                    if (
                        item.get("kind") == "local"
                        and item.get("name") == record["name"]
                        and item.get("owning_host") == host
                        and item.get("relaunched_boot_id") == boot_id
                    ):
                        item.setdefault("boot_decision", record["boot_decision"])["reason"] = reason
                        return True
                return False

            registry._mutate(finish, timeout=2)
        else:
            reason = veto
        state.LOGGER.info("%s: %s", snapshot["name"], reason)
        output.emit(output.Tag.SESSIONS, "session", f"{snapshot['name']}: {reason}")
    if not dry_run and cfg["publish"]["repo"]:
        session_state_publish.publish(tick=True)
    if failed:
        raise click.ClickException("one or more boot launches failed; inspect the recorded decisions")


@sessions.group("publish-timer", help="Manage the background publication timer")
def publish_timer():
    pass


_TIMER_HELP = {
    "install": "Install and enable the background timer that publishes session state; "
    "removes it instead when no publication repo is configured",
    "uninstall": "Disable and remove the publication timer",
    "status": "Report whether the publication timer is installed, current and active",
}


def _timer_command(action):
    @click.command(action, help=_TIMER_HELP[action])
    def command():
        output.emit(output.Tag.SESSIONS, "configure", session_publish_timer.manage(action))

    return command


for _action in ("install", "uninstall", "status"):
    publish_timer.add_command(_timer_command(_action))


@sessions.group("boot-unit", help="Manage the one-shot boot relaunch service (macOS and Linux)")
def boot_unit():
    pass


_BOOT_UNIT_HELP = {
    "install": "Write the boot relaunch service and enable it, or disable and remove it, as "
    "session_relaunch.on_boot (or -b/--on-boot) asks",
    "uninstall": "Disable and remove the boot relaunch service",
    "status": "Report whether the boot relaunch service is installed, current and enabled",
}


def _boot_unit_command(action):
    @click.command(action, help=_BOOT_UNIT_HELP[action])
    @click.option("-d", "--dry-run", is_flag=True, help="Print the unit, path and actions without making changes")
    def command(dry_run, on_boot=None):
        try:
            result = session_boot_unit.manage(action, on_boot=on_boot, dry_run=dry_run)
        except (OSError, RuntimeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
            raise click.ClickException(f"boot unit failed: {message}") from exc
        if dry_run:
            output.raw(Untagged.SERVICE_UNIT, result)
        else:
            output.emit(output.Tag.SESSIONS, "configure", result)

    if action == "install":
        command = click.option(
            "-b",
            "--on-boot",
            type=bool,
            default=None,
            help="Desired profile state; defaults to session_relaunch.on_boot",
        )(command)
    return command


for _action in ("install", "uninstall", "status"):
    boot_unit.add_command(_boot_unit_command(_action))


def _run_on_this_terminal(command: list[str]) -> int:
    """Run the agent on the terminal this process was given, and return its status.

    The agent inherits stdin, stdout and stderr untouched. An interactive agent decides
    from them whether it is interactive: with stdout captured, Claude Code took the
    launch as ``--print``, refused for want of a prompt and exited at once, so every
    tmux --once session ended as a bare ``[exited]``.

    Ctrl+C reaches the whole foreground process group, so this process ignores it
    while the agent runs: the agent decides what an interrupt means, and this owner
    stays alive to record the exit.
    """
    proc = subprocess.Popen(command)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return proc.wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def _agent_program(command: list[str]) -> str:
    """The agent's own program name, past a ``direnv exec <dir>`` prefix."""
    if command[:2] == ["direnv", "exec"] and len(command) > 3:
        command = command[3:]
    return Path(command[0]).name


def _exit_description(program: str, status: int, failure: OSError | None) -> str:
    if failure is not None:
        return f"{program} could not be started: {failure}"
    if status < 0:
        try:
            signame = signal.Signals(-status).name
        except ValueError:
            signame = f"signal {-status}"
        return f"{program} was ended by {signame}"
    return f"{program} exited with status {status}"


def _record_exit_cause(name: str, launch_log: str, description: str, status: int) -> None:
    """Say why the agent ended, on the terminal and in the launch's log when it names one.

    A detached session's pane closes with its agent, so the log is the only place the
    reason survives.
    """
    from .launch_logging import open_launch_log

    log = open_launch_log(launch_log) if launch_log else None
    previous = output.attach_file_sink(log.write_line if log is not None and log.path is not None else None)
    try:
        level = output.Level.INFO if status == 0 else output.Level.WARNING
        output.emit(output.Tag.SESSION, "exit", f"{name}: {description}", err=True, level=level)
        output.flush_sinks()
    finally:
        output.attach_file_sink(previous)
        if log is not None:
            log.close()


@sessions.command("_run", hidden=True, context_settings={"ignore_unknown_options": True})
@click.option("-l", "--launch-log", default="", help="Launch log that records why the agent ended")
@click.argument("name")
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
def run_once(launch_log, name, command):
    """Keep a local lifecycle owner around bare and once-only agent processes."""
    generation = state.begin(name, command=list(command))
    sequence = time.monotonic_ns()
    if not state.mark_running(name, generation=generation, sequence=sequence):
        raise click.ClickException("startup persistence failed; agent was not started")
    cause = "manual_exit"
    failure: OSError | None = None
    try:
        status = _run_on_this_terminal(list(command))
    except OSError as exc:
        cause, status, failure = "unknown", 1, exc
    _record_exit_cause(name, launch_log, _exit_description(_agent_program(list(command)), status, failure), status)
    while not state.mark_exited(
        name, cause, {"source": "single-run", "status": status}, generation=generation, sequence=time.monotonic_ns()
    ):
        output.error(output.Tag.SESSIONS, "exit", "session exit persistence failed; retrying local safety write")
        time.sleep(1)
    sys.exit(status)
