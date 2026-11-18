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


def open_registry(*, read_only: bool = False) -> reg.Registry:
    """This machine's registry, after publishing its spool; exits when there is none to use."""
    try:
        discovery = reg.discover()
    except reg.RegistryError as exc:
        _out.error(Tag.NAMES, "config", str(exc))
        sys.exit(1)
    if discovery.registry is None:
        _out.error(Tag.NAMES, "config", discovery.reason)
        sys.exit(1)
    if not read_only:
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
    fleet = isinstance(registry, reg.FleetRegistry)
    policy_config = reg.read_policy_file(registry.policy_file) if isinstance(registry, reg.FleetRegistry) else None
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
    if isinstance(registry, reg.FleetRegistry):
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
        "top_up_skipped": report.top_up_skipped,
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
    if report.top_up_skipped:
        lines.append(f"spare top-up skipped: {report.top_up_skipped}")
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


@click.command(
    "set-name-status",
    help=(
        "Session names: set the general status of one or more names in one commit; only approved names are "
        "picked, unreviewed is every name nobody has ruled on, rejected is never picked or taken by hand"
    ),
)
@click.option("-a", "--actor", required=True, help="Who decided, for example the owner whose verdict this records")
@click.option("-r", "--reason", default=None, help="Why, recorded with each name's status")
@click.argument("status", type=click.Choice(reg.NAME_STATUSES))
@click.argument("names", nargs=-1, required=True)
def set_name_status_command(actor: str, reason: str | None, status: str, names: tuple[str, ...]) -> None:
    registry = open_registry()
    values = [name.lower() for name in names]
    try:
        changed = reg.set_name_status(registry, values, status, actor=actor, recorded_by=_added_by(), reason=reason)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    unchanged = sorted(set(values) - set(changed))
    lines = [f"{status}: {', '.join(changed)}"] if changed else []
    if unchanged:
        lines.append(f"already {status}, nothing written for: {', '.join(unchanged)}")
    _out.emit(Tag.NAMES, "set", "\n".join(lines))


@click.command(
    "set-name-verdict",
    help=(
        "Session names: approve or reject names for one repository, host or session only; a scoped rejection "
        "keeps the name out of picks there, a session approval lets that session take it by hand"
    ),
)
@click.option(
    "-s", "--scope", "scope_kind", required=True, type=click.Choice(reg.SCOPE_KINDS), help="What the verdict applies to"
)
@click.option("-v", "--value", "scope_value", required=True, help="The repository name, host id or session title")
@click.option("-a", "--actor", required=True, help="Who decided")
@click.option("-r", "--reason", default=None, help="Why, recorded with each verdict")
@click.option("-d", "--delete", is_flag=True, help="Remove the verdict instead of setting it")
@click.argument("verdict", type=click.Choice(reg.VERDICT_VALUES))
@click.argument("names", nargs=-1, required=True)
def set_name_verdict_command(
    scope_kind: str,
    scope_value: str,
    actor: str,
    reason: str | None,
    delete: bool,
    verdict: str,
    names: tuple[str, ...],
) -> None:
    registry = open_registry()
    values = [name.lower() for name in names]
    try:
        changed = reg.set_name_verdict(
            registry,
            values,
            verdict,
            scope_kind=scope_kind,
            scope_value=scope_value,
            actor=actor,
            recorded_by=_added_by(),
            reason=reason,
            delete=delete,
        )
    except reg.RegistryError as exc:
        _fail(exc)
        return
    action = "removed" if delete else verdict
    if changed:
        _out.emit(Tag.NAMES, "set", f"{action} for {scope_kind} {scope_value}: {', '.join(changed)}")
    else:
        _out.emit(Tag.NAMES, "set", f"nothing to change for {scope_kind} {scope_value}")


def _fleet_database(registry: reg.Registry, *, create: bool = True) -> reg.Registry | None:
    """The fleet's Dolt database, before or after the policy switches to it; refuses with no fleet.

    A database the remote does not hold yet is created with writes off, or, without
    ``create``, None is returned.
    """
    from .name_dolt import DEFAULT_DOLT_REF, DoltRegistry

    if isinstance(registry, DoltRegistry):
        return registry
    if not isinstance(registry, reg.FleetRegistry):
        raise reg.WriteRefusedError("this needs the fleet registry; an install with no fleet keeps a local one")
    policy = reg.read_policy_file(registry.policy_file)
    database = DoltRegistry(
        registry.repo,
        dolt_ref=str(policy.get("registry_dolt_ref", DEFAULT_DOLT_REF)),
        remote=registry.remote,
        policy_file=registry.policy_file,
        timeout_s=registry.timeout_s,
        attempts=registry.attempts,
    )
    exists = database._git(
        "ls-remote", registry.remote, database.dolt_ref, timeout=database.fetch_timeout_s(), check=False
    )
    if exists.returncode == 0 and not exists.stdout.strip() and not database.has_clone():
        if not create:
            return None
        database.initialize(writes_enabled=False)
    return database


@click.command(
    "import-names",
    help=(
        "Session names: copy the old git-tree registry (pool, manual names, held rows, renames) into the fleet's "
        "Dolt database in one commit; a second run finds nothing to import"
    ),
)
@click.option("-f", "--from-ref", "from_ref", default=None, help="The old registry ref (default: registry_ref)")
@click.option("-d", "--dry-run", is_flag=True, help="Count what would be imported and write nothing")
def import_names_command(from_ref: str | None, dry_run: bool) -> None:
    from . import name_import

    registry = open_registry(read_only=True)
    try:
        if not isinstance(registry, reg.FleetRegistry):
            raise reg.WriteRefusedError("import-names needs the fleet registry; an install with no fleet has none")
        policy = reg.read_policy_file(registry.policy_file)
        ref = from_ref or policy.get("registry_ref")
        if not isinstance(ref, str) or not ref:
            raise reg.WriteRefusedError("the policy names no registry_ref; pass the old ref with -f/--from-ref")
        source = reg.GitRegistry(
            registry.repo,
            ref=ref,
            remote=registry.remote,
            policy_file=registry.policy_file,
            timeout_s=registry.timeout_s,
            attempts=registry.attempts,
        )
        tree = source.snapshot()
        target = _fleet_database(registry, create=not dry_run)
        if target is None:
            report = name_import.tree_counts(tree)
            report.written = True
        else:
            report = name_import.import_tree(target, tree, dry_run=dry_run)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    if dry_run:
        lines = ["dry run: would import" if report.written else "dry run: nothing to import", *report.lines()]
    else:
        lines = ["imported" if report.written else "nothing to import", *report.lines()]
    _out.emit(Tag.NAMES, "load", "\n".join(lines))


@click.command(
    "seed-name-verdicts",
    help=(
        "Session names: apply the owner's approvals from a seed file, after checking it part by part against "
        "the owner-approved oracle the cutover record pins"
    ),
)
@click.option(
    "-f", "--file", "path", required=True, type=click.Path(dir_okay=False, path_type=Path), help="The seed file (TOML)"
)
def seed_name_verdicts_command(path: Path) -> None:
    from . import name_import

    registry = open_registry(read_only=True)
    try:
        if not isinstance(registry, reg.FleetRegistry):
            raise reg.WriteRefusedError("seeding needs the fleet registry; an install with no fleet has none")
        manifest = path.read_text(encoding="utf-8")
        oracle = name_import.load_oracle(registry.repo)
        denylist = frozenset(str(name) for name in reg.read_policy_file(registry.policy_file).get("denylist", []))
        database = _fleet_database(registry)
        assert database is not None
        report = name_import.seed_verdicts(
            database, manifest, oracle=oracle, denylist=denylist, recorded_by=_added_by()
        )
    except OSError as exc:
        _fail(reg.WriteRefusedError(f"cannot read {path}: {exc}"))
        return
    except reg.RegistryError as exc:
        _fail(exc)
        return
    head = "seeded" if report.written else "this seed revision was already applied; nothing was written"
    _out.emit(Tag.NAMES, "apply", "\n".join([head, *name_import.describe(report)]))


@click.command("list-migrations", help="Session names: list every recorded rename with its state")
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the rows as JSON")
def list_migrations_command(as_json: bool) -> None:
    registry = open_registry(read_only=True)
    try:
        snapshot = registry.snapshot()
    except reg.RegistryError as exc:
        _fail(exc)
        return
    records = [snapshot.migrations[title] for title in sorted(snapshot.migrations)]
    if as_json:
        _out.raw(Untagged.JSON, json.dumps(records, sort_keys=True))
        return
    if not records:
        _out.emit(Tag.NAMES, "list", "no renames are recorded")
        return
    lines = [
        f"{record['old_title']!s:<32} {record['new_title']!s:<32} {record['status']!s:<9} {record['machine']}"
        for record in records
    ]
    _out.emit(Tag.NAMES, "list", "\n".join(lines))


@click.command(
    "find-names",
    help="Session names: find pool names by status, verdict scope, holder and hold state, or show a name's history",
)
@click.option("-s", "--status", type=click.Choice(reg.NAME_STATUSES), default=None, help="General status")
@click.option(
    "-k", "--scope", "scope_kind", type=click.Choice(reg.SCOPE_KINDS), default=None, help="Verdict scope kind"
)
@click.option("-v", "--value", "scope_value", default=None, help="Verdict scope value")
@click.option("-m", "--machine", default=None, help="The host holding the name")
@click.option(
    "-t",
    "--state",
    type=click.Choice(("reserved", "active", "migration-incomplete", "cutover-quarantined")),
    default=None,
    help="Hold state",
)
@click.option(
    "-H", "--history", "history_of", default=None, metavar="NAME", help="Every past value of NAME (Dolt only)"
)
@click.option("-j", "--json", "as_json", is_flag=True, help="Print JSON")
def find_names_command(
    status: str | None,
    scope_kind: str | None,
    scope_value: str | None,
    machine: str | None,
    state: str | None,
    history_of: str | None,
    as_json: bool,
) -> None:
    registry = open_registry(read_only=True)
    try:
        if history_of:
            rows = registry.history(history_of.lower())
            if as_json:
                _out.raw(Untagged.JSON, json.dumps(rows, sort_keys=True, default=str))
                return
            lines = [
                f"{row['commit_date']!s:<24} {row['committer']!s:<20} {row['what']:<8} {row['scope'] or '-'!s:<28} "
                f"{row['value']}"
                for row in rows
            ]
            _out.emit(Tag.NAMES, "list", "\n".join(lines) or f"no history for {history_of}")
            return
        filters = {
            "status": status,
            "scope_kind": scope_kind,
            "scope_value": scope_value,
            "machine": machine,
            "state": state,
        }
        found = registry.find_names(filters)
    except reg.RegistryError as exc:
        _fail(exc)
        return
    if as_json:
        _out.raw(Untagged.JSON, json.dumps(found, sort_keys=True))
        return
    lines = []
    for entry in found:
        verdicts = ", ".join(f"{v['verdict']} for {v['scope_kind']} {v['scope_value']}" for v in entry["verdicts"])
        hold = entry["hold"]
        held = f"{hold['kind']} {hold['state']} on {hold['machine']}" if hold else "free"
        lines.append(f"{entry['name']:<6} {entry['status']:<11} {held:<36} {verdicts or '-'}")
    _out.emit(Tag.NAMES, "list", "\n".join(lines) or "no name matches")


def _list_pool(snapshot: reg.Snapshot, as_json: bool) -> None:
    """Every name in the database with its general status and holder; exit 3 when status is unusable."""
    try:
        statuses = {name: snapshot.status_of(name) for name in sorted(snapshot.available)}
    except reg.NameStatusError as exc:
        _out.error(Tag.NAMES, "list", str(exc))
        sys.exit(EXIT_UNREADABLE)
    if as_json:
        entries = [
            {"name": name, "status": status, "held_by": snapshot.holder(name)} for name, status in statuses.items()
        ]
        _out.raw(Untagged.JSON, json.dumps(entries, sort_keys=True))
        return
    counts = ", ".join(f"{value} {list(statuses.values()).count(value)}" for value in reg.NAME_STATUSES)
    lines = [f"{name:<6} {status:<11} {snapshot.holder(name) or '-'}" for name, status in statuses.items()]
    _out.emit(Tag.NAMES, "list", "\n".join([*lines, f"{len(statuses)} names: {counts}"]))


@click.command(
    "list-names", help="Session names: list every held name with its title, host, client, kind and general status"
)
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the rows as JSON")
@click.option(
    "-p", "--pool", is_flag=True, help="List every name in the database instead, with its general status and holder"
)
def list_names_command(as_json: bool, pool: bool) -> None:
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
    if pool:
        if snapshot.statuses is None:
            _out.error(Tag.NAMES, "list", "this registry keeps no name status; it arrives with the database registry")
            sys.exit(EXIT_UNREADABLE)
        _list_pool(snapshot, as_json)
        return
    host = _host()
    live = set(_tmux_session_names())
    rows = sorted(snapshot.rows.values(), key=lambda row: (str(row.get("machine")), row["token"]))
    try:
        statuses = (
            {row["token"]: snapshot.status_of(row["token"]) for row in rows} if snapshot.statuses is not None else {}
        )
    except reg.NameStatusError as exc:
        _out.emit(Tag.NAMES, "list", f"{exc}; statuses show as -", err=True, level=_out.Level.WARNING)
        statuses = {}

    def running(row: dict) -> bool | None:
        """Whether this machine runs the row's session; None for another machine's row or a spare."""
        if row.get("machine") != host or not row.get("title"):
            return None
        return any(
            f"{engine}{remote}-{row['title']}" in live for engine in ("c", "g", "p", "cx") for remote in ("", "-r")
        )

    if as_json:
        _out.raw(
            Untagged.JSON,
            json.dumps(
                [{**row, "running": running(row), "status": statuses.get(row["token"])} for row in rows],
                sort_keys=True,
            ),
        )
        return
    from .session_locate import unregistered_sessions

    held = {str(row.get("title")) for row in rows if row.get("title")}
    unregistered = unregistered_sessions(_projects_root(), held)
    if not rows and not unregistered:
        _out.emit(Tag.NAMES, "list", "no names are held")
        return
    lines = []
    for row in rows:
        state = {True: " running", False: " stopped", None: ""}[running(row)]
        lines.append(
            f"{row['token']:<6} {row.get('title') or '-'!s:<32} {row.get('machine')!s:<16} "
            f"{row.get('client') or '-'!s:<12} {row['kind']:<8} {statuses.get(row['token'], '-'):<11} "
            f"{row.get('reserved_at')}{state}"
        )
    # Sessions named before the registry hold no row, so they are listed from this
    # host's tmux and worktrees, each with the command that resumes it.
    for session in unregistered:
        state = "running" if session["tmux"] else "stopped"
        lines.append(
            f"{'-':<6} {session['title']:<32} {host:<16} {'-':<12} {'no name':<8} {state}; {session['resume']}"
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
        sys.exit(0 if located.row is not None or located.local is not None else 1)
    if located.row is None and located.local is None:
        _out.error(Tag.NAMES, "locate", f"no session holds {query}; `ai list-names` lists every held name")
        sys.exit(1)
    _out.emit(Tag.NAMES, "locate", "\n".join(session_locate.describe(located)))


CLIENT_EXECUTABLES = {"claude-code": "claude", "codex": "codex", "pi": "pi", "gemini": "gemini"}


def _most_recent_record(client):
    """The newest readable record whose working directory is known, or None."""
    candidates = [record for record in client.all_records() if not record.problem and record.cwd]
    return max(candidates, key=lambda record: record.path.stat().st_mtime, default=None)


def _probe_cases(client, scratch: Path) -> list[dict]:
    from . import session_clients as sc

    def case(name: str, expected: str, probe) -> dict:
        return {
            "case": name,
            "expected": expected,
            "actual": probe.state,
            "reason": probe.reason,
            "skipped": False,
            "ok": probe.state == expected,
        }

    cases = []
    root_problem = client._root_problem()
    record = _most_recent_record(client) if root_problem is None else None
    if root_problem is not None:
        # A store root that exists but cannot be read is a defect to see, not an empty store.
        cases.append(
            {
                "case": sc.PRESENT,
                "expected": sc.PRESENT,
                "actual": None,
                "reason": root_problem,
                "skipped": False,
                "ok": False,
            }
        )
    elif record is None:
        # A readable store with no record owns no row on this host, so it cannot cause a wrong release.
        cases.append(
            {
                "case": sc.PRESENT,
                "expected": sc.PRESENT,
                "actual": None,
                "reason": f"no {client.client} record on this host to test present",
                "skipped": True,
                "ok": True,
            }
        )
    else:
        cases.append(case(sc.PRESENT, sc.PRESENT, client.probe_transcript(Path(record.cwd), "")))
    worktree = scratch / "worktree"
    worktree.mkdir()
    cases.append(case(sc.ABSENT, sc.ABSENT, client.probe_transcript(worktree, "")))
    missing_home = scratch / "missing-home"
    cases.append(case(sc.UNKNOWN, sc.UNKNOWN, type(client)(missing_home).probe_transcript(worktree, "")))
    return cases


@click.command(
    "probe-transcripts",
    help="Session names: prove each installed client's transcript probe answers present, absent and unknown",
)
@click.option("-j", "--json", "as_json", is_flag=True, help="Print the results as one JSON object")
def probe_transcripts_command(as_json: bool) -> None:
    import shutil
    import tempfile

    from . import session_clients

    clients = []
    for client in session_clients.all_adapters():
        executable = CLIENT_EXECUTABLES.get(client.client, client.client)
        installed = shutil.which(executable) is not None or client.store_root().exists()
        cases = []
        skipped = None
        if not installed:
            skipped = "not installed"
        elif not client.store_root().exists():
            # A missing store root only ever probes unknown, which holds every row, so it cannot release a name.
            skipped = f"no {client.client} store on this host; its rows are held as unknown until one exists"
        else:
            with tempfile.TemporaryDirectory(prefix="ai-probe-transcripts-") as scratch:
                cases = _probe_cases(client, Path(scratch))
        clients.append({"client": client.client, "installed": installed, "skipped": skipped, "cases": cases})
    ok = all(case["ok"] for entry in clients for case in entry["cases"])
    if as_json:
        _out.raw(Untagged.JSON, json.dumps({"clients": clients, "ok": ok}, sort_keys=True))
        sys.exit(0 if ok else 1)
    lines = []
    for entry in clients:
        if entry["skipped"]:
            lines.append(f"{entry['client']} skipped: {entry['skipped']}")
            continue
        for case in entry["cases"]:
            if case["skipped"]:
                lines.append(f"{entry['client']} {case['case']} skipped: {case['reason']}")
                continue
            verdict = "ok" if case["ok"] else "FAIL"
            if case["actual"] is None:
                lines.append(f"{entry['client']} {case['case']}: {case['reason']} ({verdict})")
                continue
            lines.append(
                f"{entry['client']} {case['case']}: expected {case['expected']}, got {case['actual']} "
                f"({verdict}): {case['reason']}"
            )
    _out.emit(Tag.NAMES, "probe", "\n".join(lines))
    sys.exit(0 if ok else 1)


COMMANDS = (
    sync_names_command,
    add_name_command,
    remove_name_command,
    show_name_config_command,
    set_name_status_command,
    set_name_verdict_command,
    import_names_command,
    seed_name_verdicts_command,
    list_migrations_command,
    find_names_command,
    reconcile_names_command,
    release_name_command,
    reassign_name_command,
    check_names_command,
    list_names_command,
    list_name_candidates_command,
    locate_session_command,
    probe_transcripts_command,
)
