"""Session lifecycle and publication commands."""

import json
import os
import re
import socket
import sys
import time
from copy import deepcopy
from pathlib import Path

import click

from . import output, session_boot_unit, session_publish_timer, session_state_publish
from . import session_registry as registry
from . import session_run_state as state
from .iterm2_restore import LAUNCH_REASON_VAR
from .output import Untagged
from .session_enrol import enrol, heal_live_sessions


@click.group("sessions", help="Session run state and publication")
def sessions():
    pass


sessions.add_command(enrol)


@sessions.command("mark")
@click.argument("name")
@click.argument("status", type=click.Choice(["running", "exited"]))
@click.option("-c", "--cause", type=click.Choice(registry.EXIT_CAUSES))
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
    if not state.mark(
        name, status, cause=cause or "unknown", evidence=parsed, generation=generation, sequence=sequence
    ):
        raise click.ClickException("session state persistence refused; retry before returning to the shell")


@sessions.command("_begin", hidden=True)
@click.argument("name")
@click.option("--launch-metadata", hidden=True, help="Generated launcher metadata for registration recovery")
def begin(name, launch_metadata):
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
        output.raw(Untagged.INTERNAL_REPLY, state.begin(name, recovery_recipe=recipe))
    except Exception as exc:
        raise click.ClickException(f"cannot acquire a durable supervisor generation: {exc}") from exc


@sessions.command("_sequence", hidden=True)
def sequence():
    output.raw(Untagged.INTERNAL_REPLY, str(time.monotonic_ns()))


@sessions.command("publish")
@click.option("-n", "--now", is_flag=True, help="Ignore the debounce window")
@click.option("-t", "--tick", is_flag=True, help="Run one publication timer tick")
def publish(now, tick):
    result = session_state_publish.publish(now=now, tick=tick)
    output.emit(output.Tag.SESSIONS, "sync", result.reason)


@sessions.command("list")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print JSON")
def list_sessions(as_json):
    records = [
        {
            "name": r["name"],
            "run_state": state.run_state(r),
            "cause": state.cause(r) if state.run_state(r) == "exited" else "",
            "changed_at": r.get("changed_at", ""),
            "relaunch": r.get("relaunch", "auto"),
        }
        for r in registry.load_registry()["sessions"]
    ]
    if as_json:
        output.raw(Untagged.JSON, json.dumps(records))
    else:
        for record in records:
            output.emit(output.Tag.SESSIONS, "list", "\t".join(record.values()))


def _session_slug(_ctx, _param, value):
    if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise click.BadParameter("invalid session name; use only letters, digits, underscores and hyphens")
    return value


def _owned_record(doc: dict, slug: str) -> dict:
    matches = [record for record in doc["sessions"] if record.get("name") == slug]
    if not matches:
        raise click.ClickException(f"unknown session: {slug}")
    host = state.config.load_config().get("machine", {}).get("host_id") or doc["machine"]
    for record in matches:
        if record.get("kind") == "local" and record.get("owning_host") == host:
            return record
    raise click.ClickException(f"{slug}: foreign or remote session; not owned by this host")


@sessions.command("policy")
@click.argument("slug", callback=_session_slug)
@click.argument("policy", type=click.Choice(["auto", "never", "always"]))
def policy(slug, policy):
    """Set a local session's boot relaunch policy and queue publication."""

    def change(doc):
        record = _owned_record(doc, slug)
        record.update(relaunch=policy, changed_at=registry._now())
        return True

    registry._mutate(change, timeout=2)
    output.emit(output.Tag.SESSIONS, "session", f"{slug}: relaunch policy {policy}")


def _launch_recipe(record: dict, *, cause: str | None = None) -> None:
    from .main import _do_session_launch

    recipe = record["boot_recipe"]
    options = recipe["options"]
    argv = recipe["argv"]
    prefix = argv[argv.index("--project-prefix") + 1] if "--project-prefix" in argv else ""
    previous_cwd = Path.cwd()
    previous_reason = os.environ.get(LAUNCH_REASON_VAR)
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
        )
    finally:
        os.chdir(previous_cwd)
        if previous_reason is None:
            os.environ.pop(LAUNCH_REASON_VAR, None)
        else:
            os.environ[LAUNCH_REASON_VAR] = previous_reason


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


def _relaunch_named(slug: str, *, dry_run: bool) -> None:
    selected = {}

    def select(doc):
        record = _owned_record(doc, slug)
        alive = registry._local_session_alive(slug)
        if alive is not False:
            reason = "tmux session already exists" if alive else "tmux liveness could not be established"
            raise click.ClickException(f"{slug}: {reason}")
        if not state.valid_boot_recipe(record):
            raise click.ClickException(f"{slug}: unsupported or incomplete boot recipe")
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
        _launch_recipe(selected, cause="remote_request")
        reason = "relaunched detached"
    except (Exception, SystemExit) as exc:
        reason = f"launch failed ({type(exc).__name__})"
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
def relaunch(slug, boot, dry_run):
    """Relaunch recorded sessions detached, without a terminal application."""
    if boot == (slug is not None):
        raise click.UsageError("provide either a session name or --boot, never both")
    if sys.platform == "win32":
        raise click.ClickException("boot relaunch is supported on macOS and Linux")
    if slug is not None:
        _relaunch_named(slug, dry_run=dry_run)
        return
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
                _launch_recipe(record)
                reason = "relaunched detached"
            except (Exception, SystemExit) as exc:
                reason = f"launch failed ({type(exc).__name__}); boot reservation retained"
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


def _timer_command(action):
    @click.command(action)
    def command():
        output.emit(output.Tag.SESSIONS, "configure", session_publish_timer.manage(action))

    return command


for _action in ("install", "uninstall", "status"):
    publish_timer.add_command(_timer_command(_action))


@sessions.group("boot-unit", help="Manage the one-shot boot relaunch service (macOS and Linux)")
def boot_unit():
    pass


def _boot_unit_command(action):
    @click.command(action)
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


@sessions.command("_run", hidden=True, context_settings={"ignore_unknown_options": True})
@click.argument("name")
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
def run_once(name, command):
    """Keep a local lifecycle owner around bare and once-only agent processes."""
    generation = state.begin(name, command=list(command))
    sequence = time.monotonic_ns()
    if not state.mark_running(name, generation=generation, sequence=sequence):
        raise click.ClickException("startup persistence failed; agent was not started")
    cause = "manual_exit"
    try:
        result = output.run_relayed(output.Tag.SESSION, "session", list(command), check=False)
        status = result.returncode
    except OSError:
        cause, status = "unknown", 1
    except KeyboardInterrupt:
        status = 130
    while not state.mark_exited(
        name, cause, {"source": "single-run", "status": status}, generation=generation, sequence=time.monotonic_ns()
    ):
        output.error(output.Tag.SESSIONS, "exit", "session exit persistence failed; retrying local safety write")
        time.sleep(1)
    sys.exit(status)
