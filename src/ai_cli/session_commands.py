"""Session lifecycle and publication commands."""

import json
import sys
import time

import click

from . import output, session_publish_timer, session_state_publish
from . import session_registry as registry
from . import session_run_state as state
from .output import Untagged


@click.group("sessions", help="Session run state and publication")
def sessions():
    pass


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
def begin(name):
    try:
        output.raw(Untagged.INTERNAL_REPLY, state.begin(name))
    except Exception as exc:
        raise click.ClickException("cannot acquire a durable supervisor generation") from exc


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
