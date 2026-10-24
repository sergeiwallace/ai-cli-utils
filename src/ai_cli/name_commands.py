"""The ``ai`` commands that manage session names.

Each is a top-level ``{verb}-{noun}`` command whose help line opens with "Session
names:", so the group reads together in ``ai --help`` without a parent command. A
refusal exits 2; a registry that could not be reached or written exits 75.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import click

from . import name_list
from . import name_registry as reg
from . import output as _out
from .output import Tag, Untagged

EXIT_REFUSED = 2
EXIT_UNREADABLE = 3
EXIT_UNREACHABLE = 75


def _host() -> str:
    from . import config
    from .host_env import refresh_host_env
    from .session_naming import host_id

    refresh_host_env(config.load_config())
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


@click.command("show-name-config", help="Session names: print each naming setting, its value and the layer that set it")
def show_name_config_command() -> None:
    from . import session_title

    try:
        discovery = reg.discover()
    except reg.RegistryError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(1)
    registry = discovery.registry
    fleet = isinstance(registry, reg.GitRegistry)
    policy_config = reg.read_policy_file(registry.policy_file) if isinstance(registry, reg.GitRegistry) else None
    source = reg.policy_source(registry) if registry else "config/session-names.toml"
    try:
        settings = session_title.resolve_settings(
            enabled_flag=None, with_label_flag=None, policy_config=policy_config, host=_host(), policy_source=source
        )
    except session_title.TitleError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(EXIT_REFUSED)
    lines = [
        f"registry           {registry.kind}: {registry.location}"
        if registry
        else f"registry           none: {discovery.reason}"
    ]
    for key in ("enabled", "name_with_label"):
        lines.append(f"{key:<18} {settings[key].value!s:<8} ({settings[key].layer})")
    if registry is None:
        _out.emit(Tag.NAMES, "config", "\n".join(lines))
        return
    try:
        snapshot = registry.snapshot()
    except reg.RegistryUnreachableError:
        snapshot = registry.snapshot(fetch=False)
        lines.append("note               the registry is unreachable; showing the last fetched tip")
    try:
        limits = session_title.resolve_limits(registry, snapshot)
        policy = snapshot.policy
    except reg.RegistryError as exc:
        lines.append(f"policy             unavailable: {exc}")
        _out.emit(Tag.NAMES, "config", "\n".join(lines))
        return
    lines += [
        f"min_name_length    {limits.low:<8} ({limits.layer})",
        f"max_name_length    {limits.high:<8} ({limits.layer})",
    ]
    layer = "published policy" if fleet else "built-in"
    for key in ("pick_floor", "low_water", "offline_spares", "publish_timeout_s", "publish_attempts"):
        lines.append(f"{key:<18} {policy.get(key)!s:<8} ({layer})")
    lines.append(f"max_edit_distance  {policy.get('confusable', {}).get('max_edit_distance')!s:<8} ({layer})")
    if isinstance(registry, reg.GitRegistry):
        generation = snapshot.generation or {}
        commit, _committed_at = reg.policy_revision(registry)
        local_digest = name_list.policy_digest(reg.local_policy(registry))
        state = reg.ancestry_state(registry, generation.get("policy_commit"), commit) if generation else "local-newer"
        lines += [
            f"published policy   commit {generation.get('policy_commit')}, digest {generation.get('policy_digest')}",
            f"this checkout      commit {commit}, digest {local_digest}",
            f"ancestry state     {state}",
        ]
    _out.emit(Tag.NAMES, "config", "\n".join(lines))
    if limits.warning:
        _out.emit(Tag.NAMES, "config", limits.warning, err=True, level=_out.Level.WARNING)


def _projects_root() -> Path:
    from .session_naming import _projects_roots

    return _projects_roots()[0]


def _adapters() -> dict[str, object]:
    from . import session_clients

    return {name: cls() for name, cls in session_clients.ADAPTERS.items()}


def _host_alias() -> str:
    from .session_naming import HostAliasError, resolve_host_alias

    try:
        return resolve_host_alias()
    except HostAliasError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(1)


def _recover(registry: reg.Registry) -> list[str]:
    from . import migrate_names, session_clients

    return migrate_names.recover_intents(
        registry, _host(), projects_root=_projects_root(), adapters=session_clients.all_adapters()
    )


@click.command(
    "reconcile-names",
    help="Session names: publish offline activations, release names whose worktree and transcript are gone",
)
@click.option("-t", "--title", default=None, help="Consider only the row for this title")
@click.option("-d", "--dry-run", is_flag=True, help="Report what would be released; write nothing")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the summary as JSON")
def reconcile_names_command(title: str | None, dry_run: bool, as_json: bool) -> None:
    registry = _registry_only()
    try:
        report = reg.reconcile(
            registry,
            _host(),
            projects_root=_projects_root(),
            adapters=_adapters(),
            host_alias=_host_alias(),
            title=title,
            dry_run=dry_run,
            recover=None if dry_run else (lambda: _recover(registry)),
        )
    except reg.RegistryError as exc:
        _fail(exc)
        return
    summary = {
        "dry_run": dry_run,
        "published": report.published,
        "recovered": report.recovered,
        "released": report.released,
        "spares_released": report.spares_released,
        "spares_added": report.spares_added,
        "held": report.held,
    }
    if as_json:
        _out.raw(Untagged.JSON, json.dumps(summary, sort_keys=True))
        return
    verb = "would release" if dry_run else "released"
    lines = [f"{verb}: {', '.join(report.released) or 'none'}"]
    if report.spares_released:
        lines.append(f"{verb} spares outside the limits: {', '.join(report.spares_released)}")
    if report.spares_added:
        lines.append(f"added spares: {', '.join(report.spares_added)}")
    lines += [f"published: {name}" for name in report.published]
    lines += [f"recovered: {item}" for item in report.recovered]
    lines += [f"held: {token}: {why}" for token, why in sorted(report.held.items())]
    _out.emit(Tag.NAMES, "reconcile", "\n".join(lines))


def _registry_only() -> reg.Registry:
    """This machine's registry, without publishing the spool: the commands that do so under the lock themselves."""
    try:
        discovery = reg.discover()
    except reg.RegistryError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(1)
    if discovery.registry is None:
        _out.error(Tag.NAMES, "config", discovery.reason)
        sys.exit(1)
    return discovery.registry


@click.command("release-name", help="Session names: release one name once its worktree and transcript are both gone")
@click.argument("name")
def release_name_command(name: str) -> None:
    registry = open_registry()
    try:
        reg.release_name(registry, name.lower(), _host(), projects_root=_projects_root(), adapters=_adapters())
    except reg.RegistryError as exc:
        _fail(exc)
        return
    _out.emit(Tag.NAMES, "release", f"released {name.lower()}")


def _known_hosts() -> frozenset[str]:
    from .session_naming import _read_registry, alias_sources

    hosts: set[str] = set()
    for source in alias_sources():
        data = _read_registry(source) or {}
        aliases = data.get("aliases", {})
        if isinstance(aliases, dict):
            hosts |= set(aliases)
    return frozenset(hosts)


@click.command(
    "reassign-name",
    help="Session names: move a name's reconciliation to another host id; the name stays held",
)
@click.argument("name")
@click.option("-t", "--to", "to", required=True, help="Host id that will reconcile the row")
@click.option("-r", "--reason", required=True, help="Why, recorded in the registry commit")
def reassign_name_command(name: str, to: str, reason: str) -> None:
    registry = open_registry()
    try:
        reg.reassign_name(registry, name.lower(), to, reason, known_hosts=_known_hosts())
    except reg.RegistryError as exc:
        _fail(exc)
        return
    _out.emit(Tag.NAMES, "reassign", f"{name.lower()} is now reconciled by {to}")


@click.command(
    "check-names",
    help="Session names: report every held name outside the length limits; exit 1 when one is too long",
)
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the report as JSON")
def check_names_command(as_json: bool) -> None:
    registry = _registry_only()
    try:
        snapshot = registry.snapshot()
        report = reg.check_names(snapshot)
        low, high = name_list.limits(snapshot.policy)
    except reg.RegistryError as exc:
        _out.error(Tag.NAMES, "check", f"could not read the registry, so the over-limit check did not run: {exc}")
        sys.exit(EXIT_UNREADABLE)
    if as_json:
        _out.raw(
            Untagged.JSON,
            json.dumps(
                {
                    "limits": [low, high],
                    "over_limit": report.over_limit,
                    "spares_over_limit": report.spares_over_limit,
                    "grandfathered": report.grandfathered,
                    "incomplete": [{"row": row, "intent": intent} for row, intent in report.incomplete],
                    "stale_intents": report.stale_intents,
                    "counts": report.counts,
                },
                sort_keys=True,
            ),
        )
        sys.exit(1 if report.failing else 0)
    lines: list[str] = []
    by_host: dict[str, list[str]] = {}
    for row in report.over_limit:
        by_host.setdefault(row["machine"], []).append(
            f"  {row['token']} ({len(row['token'])} letters): {row['title']}; run on {row['machine']}: "
            "ai migrate-session --all --to-names --over-limit -y"
        )
    for row in report.spares_over_limit:
        by_host.setdefault(row["machine"], []).append(
            f"  spare {row['token']} ({len(row['token'])} letters); run on {row['machine']}: ai reconcile-names"
        )
    for host, host_lines in sorted(by_host.items()):
        lines.append(f"{host}:")
        lines += host_lines
    for row in report.grandfathered:
        lines.append(f"grandfathered: {row['token']} ({row['title']}@{row['machine']}) is shorter than {low} letters")
    for row, intent in report.incomplete:
        survivors = ", ".join(f"{item['kind']} {item['identifier']}" for item in row.get("recovery") or [])
        lines.append(
            f"migration-incomplete: {row['title']}@{row['machine']} (rename of "
            f"{(intent or {}).get('old_title', 'unknown')}); left: {survivors or 'nothing recorded'}"
        )
    for record in report.stale_intents:
        lines.append(f"unfinished rename: {record['old_title']} -> {record['new_title']} on {record['machine']}")
    counts = ", ".join(f"{host} {count}" for host, count in sorted(report.counts.items()))
    lines.append(f"limits {low} to {high} letters; held per host: {counts or 'none'}")
    if report.failing:
        _out.error(Tag.NAMES, "check", "\n".join(["names over max_name_length:", *lines]))
        sys.exit(1)
    _out.emit(Tag.NAMES, "check", "\n".join(lines))


@click.command("list-names", help="Session names: list every held name with its title, host, client and kind")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the rows as JSON")
def list_names_command(as_json: bool) -> None:
    from .session import _tmux_session_names

    registry = open_registry()
    try:
        snapshot = registry.snapshot()
    except reg.RegistryUnreachableError:
        snapshot = registry.snapshot(fetch=False)
        _out.emit(
            Tag.NAMES,
            "list",
            "the registry is unreachable; listing the last fetched tip",
            err=True,
            level=_out.Level.WARNING,
        )
    host = _host()
    live = set(_tmux_session_names())
    rows = sorted(snapshot.rows.values(), key=lambda row: (str(row.get("machine")), row["token"]))

    def running(row: dict) -> bool | None:
        """Whether this machine runs the row's session; None for another machine's row or a spare."""
        if row.get("machine") != host or not row.get("title"):
            return None
        return any(
            f"{engine}{remote}-{row['title']}" in live for engine in ("c", "g", "p", "cx") for remote in ("", "-r")
        )

    if as_json:
        _out.raw(Untagged.JSON, json.dumps([{**row, "running": running(row)} for row in rows], sort_keys=True))
        return
    if not rows:
        _out.emit(Tag.NAMES, "list", "no names are held")
        return
    lines = []
    for row in rows:
        state = {True: " running", False: " stopped", None: ""}[running(row)]
        lines.append(
            f"{row['token']:<6} {row.get('title') or '-'!s:<32} {row.get('machine')!s:<16} "
            f"{row.get('client') or '-'!s:<12} {row['kind']:<8} {row.get('reserved_at')}{state}"
        )
    _out.emit(Tag.NAMES, "list", "\n".join(lines))


@click.command(
    "list-name-candidates",
    help="Session names: count the free names per length and the letter tiers the picker would use",
)
@click.option(
    "-r", "--repo", "repo_alias", default=None, help="Repo alias whose letters rank the names (default: this repo)"
)
@click.option("-L", "--max-length", type=int, default=None, help="Count only names of at most this many letters")
def list_name_candidates_command(repo_alias: str | None, max_length: int | None) -> None:
    from . import session_naming
    from .session import get_project_prefix

    registry = open_registry()
    try:
        snapshot = registry.snapshot()
    except reg.RegistryUnreachableError:
        snapshot = registry.snapshot(fetch=False)
    alias = (repo_alias or get_project_prefix()).lower()
    slot = reg.Slot(repo="", repo_alias=alias, host_alias=_host_alias(), label=None, machine=_host(), client="")
    try:
        request, warnings = reg.pick_request(registry, snapshot, slot, spool=reg.read_spool(), max_length=max_length)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    candidates, removed = session_naming.name_candidates(request)
    first = next((char for char in alias if "a" <= char <= "z"), "")
    lines = [f"letters {''.join(sorted(request.letters))} (repo alias {alias}), pick floor {request.pick_floor}"]
    for length in sorted({len(name) for name in candidates}):
        names = [name for name in candidates if len(name) == length]
        tiers: dict[tuple[int, bool], int] = {}
        for name in names:
            key = (len(set(name) & request.letters), name.startswith(first))
            tiers[key] = tiers.get(key, 0) + 1
        ladder = ", ".join(
            f"{count} with {contained}{' incl. first' if starts else ''}"
            for (contained, starts), count in sorted(tiers.items(), reverse=True)
        )
        lines.append(f"{length} letters: {len(names)} free ({ladder})")
    lines += [f"removed: {reason}: {count}" for reason, count in removed.items() if count]
    lines += [f"warning: {warning}" for warning in warnings]
    _out.emit(Tag.NAMES, "list", "\n".join(lines))


@click.command(
    "locate-session",
    help="Session names: which host holds a session, whether it is active, and which messaging rung reaches it",
)
@click.argument("query")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print one JSON object with row, local, liveness and reach")
def locate_session_command(query: str, as_json: bool) -> None:
    from . import session_locate

    registry = open_registry()
    located = session_locate.locate(registry, query.lower(), _host(), projects_root=_projects_root())
    if as_json:
        _out.raw(Untagged.JSON, json.dumps(located.as_json(), sort_keys=True))
        sys.exit(0 if located.row is not None else 1)
    if located.row is None:
        _out.error(Tag.NAMES, "locate", f"no session holds {query}; `ai list-names` lists every held name")
        sys.exit(1)
    _out.emit(Tag.NAMES, "locate", "\n".join(session_locate.describe(located)))


COMMANDS = (
    sync_names_command,
    add_name_command,
    remove_name_command,
    show_name_config_command,
    reconcile_names_command,
    release_name_command,
    reassign_name_command,
    check_names_command,
    list_names_command,
    list_name_candidates_command,
    locate_session_command,
)
