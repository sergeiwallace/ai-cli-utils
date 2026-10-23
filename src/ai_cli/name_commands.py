"""The ``ai`` commands that manage session names.

Each is a top-level ``{verb}-{noun}`` command whose help line opens with "Session
names:", so the group reads together in ``ai --help`` without a parent command. A
refusal exits 2; a registry that could not be reached or written exits 75.
"""

from __future__ import annotations

import contextlib
import os
import sys

import click

from . import name_list
from . import name_registry as reg
from . import output as _out
from .output import Tag

EXIT_REFUSED = 2
EXIT_UNREACHABLE = 75


def _host() -> str:
    from .session_naming import host_id

    return host_id()


def _added_by() -> str:
    return f"{os.environ.get('AI_TMUX_SESSION') or 'cli'}@{_host()}"


def open_registry() -> reg.Registry:
    """This machine's registry, after publishing its spool; exits when there is none to use."""
    try:
        discovery = reg.discover()
    except reg.RegistryError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(1)
    if discovery.registry is None:
        _out.error(Tag.NAMES, "config", discovery.reason)
        sys.exit(1)
    publish_pending(discovery.registry)
    return discovery.registry


def publish_pending(registry: reg.Registry) -> None:
    """Publish this machine's offline activations; an unreachable registry leaves them for later."""
    if not reg.read_spool():
        return
    with contextlib.suppress(reg.SparesExhaustedError), reg.spool_lock(reg.lock_wait(registry)):
        try:
            published = reg.publish_spool(registry, _host())
        except reg.RegistryUnreachableError:
            return
        except reg.WriteRefusedError as exc:
            _out.emit(Tag.NAMES, "spare", str(exc), err=True, level=_out.Level.WARNING)
            return
        if published:
            _out.emit(Tag.NAMES, "spare", f"published offline activations: {', '.join(published)}", err=True)


def _fail(exc: Exception) -> None:
    if isinstance(exc, reg.RegistryUnreachableError | reg.RegistryContendedError):
        _out.error(Tag.NAMES, "push", f"{exc}")
        sys.exit(EXIT_UNREACHABLE)
    if isinstance(exc, reg.WriteRefusedError):
        _out.error(Tag.NAMES, "check", f"{exc}")
        sys.exit(EXIT_REFUSED)
    _out.error(Tag.NAMES, "check", f"{exc}")
    sys.exit(1)


@click.command(
    "sync-names",
    help="Session names: regenerate and publish the available-name list when this checkout's naming policy is newer",
)
@click.option(
    "-d", "--dry-run", is_flag=True, help="Report the policy state and what would be published; write nothing"
)
def sync_names_command(dry_run: bool) -> None:
    registry = open_registry()
    try:
        result = reg.sync_names(registry, local_policy=reg.local_policy(registry), dry_run=dry_run)
    except name_list.NameListError as exc:
        _out.error(Tag.NAMES, "sync", f"regeneration failed, nothing was published: {exc}")
        sys.exit(1)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    if result.state == "incomparable":
        _out.error(Tag.NAMES, "sync", result.message)
        sys.exit(1)
    _out.emit(Tag.NAMES, "sync", result.message)


@click.command("add-name", help="Session names: add a name to the name database without a prompt")
@click.argument("name")
@click.option("-r", "--reason", default=None, help="Why the name is added, recorded with it")
def add_name_command(name: str, reason: str | None) -> None:
    registry = open_registry()
    value = name.lower()
    try:
        added = reg.add_name(registry, value, added_by=_added_by(), reason=reason)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    if added:
        _out.emit(Tag.NAMES, "add", f"added {value} to the name database")
    else:
        _out.emit(Tag.NAMES, "add", f"{value} is already in the name database; nothing was written")


@click.command("remove-name", help="Session names: remove a name that was added by hand")
@click.argument("name")
def remove_name_command(name: str) -> None:
    registry = open_registry()
    value = name.lower()
    try:
        reg.remove_name(registry, value)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    _out.emit(Tag.NAMES, "remove", f"removed {value} from the name database")


COMMANDS = (sync_names_command, add_name_command, remove_name_command)
