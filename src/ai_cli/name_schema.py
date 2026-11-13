"""The session-name registry's table schema, written once for Dolt and for SQLite.

Both database backends hold the same six tables (:data:`TABLES`). This module renders
their DDL for each dialect, maps the registry's records to and from table rows, and
compiles one :class:`~ai_cli.name_registry.Change` into guarded statements.

A change names records by their logical path, the same paths the git-tree store uses
(``names/<token>.json`` for a hold, ``migrations/<old-title>.json`` for a rename, and
so on), so every operation in :mod:`ai_cli.name_registry` builds the same change
whatever the backend. :func:`compile_change` turns each path into one statement that
carries its whole guard in its own ``WHERE`` clause: an insert only where no row has
the key, an update or delete only where every column still equals the value the
change was built on. Each statement must affect exactly one row; a backend that sees
any other count refuses the write and commits nothing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

from . import name_registry as reg

SCHEMA_VERSION = 1
# A database whose meta.min_reader_version is above this was written by a newer package.
READER_VERSION = 1
MIN_DOLT_VERSION = (2, 3, 4)

STATUSES = ("unreviewed", "rejected", "approved")
SCOPE_KINDS = ("repo", "host", "session")
VERDICTS = ("approved", "rejected")
ORIGINS = ("generated", "manual", "shipped")
HOLD_STATES = ("reserved", "active", "migration-incomplete", "cutover-quarantined")
MIGRATION_STATES = ("intent", "complete")

STATUS_DIR = "status"
VERDICTS_DIR = "verdicts"
META_DIR = "meta"


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # "str", "enum" or "json"
    size: int = 0
    values: tuple[str, ...] = ()
    nullable: bool = True
    default: str | None = None


def _s(name: str, size: int, *, nullable: bool = True) -> Column:
    return Column(name, "str", size=size, nullable=nullable)


def _e(name: str, values: tuple[str, ...], *, nullable: bool = False, default: str | None = None) -> Column:
    return Column(name, "enum", values=values, nullable=nullable, default=default)


def _j(name: str, *, nullable: bool = True) -> Column:
    return Column(name, "json", nullable=nullable)


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    foreign: tuple[tuple[str, str, str], ...] = ()  # (column, table, column)
    checks: tuple[str, ...] = ()  # names of logical checks rendered by :func:`_check_sql`

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


TABLES: tuple[Table, ...] = (
    Table("meta", (_s("k", 64, nullable=False), _s("v", 256, nullable=False)), ("k",)),
    Table(
        "policy_snapshot",
        (_s("revision", 64, nullable=False), _s("generated_at", 20, nullable=False), _j("body", nullable=False)),
        ("revision",),
    ),
    Table(
        "names",
        (
            _s("name", 16, nullable=False),
            _e("origin", ORIGINS),
            _e("status", STATUSES, default="unreviewed"),
            _s("status_actor", 128),
            _s("status_recorded_by", 128),
            _s("status_set_at", 20),
            _s("status_reason", 256),
            _s("added_by", 128),
            _s("added_at", 20),
            _s("added_reason", 256),
            _s("added_via", 32),
        ),
        ("name",),
        checks=("name_token",),
    ),
    Table(
        "verdicts",
        (
            _s("name", 16, nullable=False),
            _e("scope_kind", SCOPE_KINDS),
            _s("scope_value", 128, nullable=False),
            _e("verdict", VERDICTS),
            _s("actor", 128, nullable=False),
            _s("recorded_by", 128, nullable=False),
            _s("set_at", 20, nullable=False),
            _s("reason", 256),
        ),
        ("name", "scope_kind", "scope_value"),
        foreign=(("name", "names", "name"),),
    ),
    Table(
        "holds",
        (
            _s("token", 16, nullable=False),
            _e("kind", reg.ROW_KINDS),
            _e("state", HOLD_STATES),
            _s("machine", 64, nullable=False),
            _s("host_alias", 64),
            _s("repo", 128),
            _s("repo_alias", 64),
            _s("worktree", 256),
            _s("title", 128),
            _s("client", 32),
            _s("label", 128),
            _s("tier", 32),
            _s("source", 32, nullable=False),
            _j("recovery"),
            _s("reserved_at", 20, nullable=False),
            _s("activated_at", 20),
            _s("claimant", 160, nullable=False),
            _s("writer", 64, nullable=False),
        ),
        ("token",),
        checks=("hold_token", "hold_recovery", "hold_kind_fields", "hold_relative_paths"),
    ),
    Table(
        "migrations",
        (
            _s("old_title", 128, nullable=False),
            _s("new_name", 16, nullable=False),
            _s("new_title", 128, nullable=False),
            _e("state", MIGRATION_STATES),
            _s("machine", 64, nullable=False),
            _s("client", 32),
            _s("recorded_at", 20, nullable=False),
            _s("completed_at", 20),
            _s("writer", 64),
        ),
        ("old_title",),
    ),
)
TABLE = {table.name: table for table in TABLES}

# The fields each hold kind must carry, and must leave empty, as the row validator has them.
_KIND_NOT_NULL = {
    "session": ("title", "repo", "repo_alias", "host_alias", "client", "activated_at"),
    "spare": ("host_alias",),
    "label": ("title", "repo", "repo_alias", "host_alias", "label", "client", "activated_at"),
}
_KIND_NULL = {
    "session": (),
    "spare": ("title", "repo", "repo_alias", "label", "client", "tier", "activated_at", "worktree", "recovery"),
    "label": ("tier",),
}
_KIND_STATES = {
    "session": ("active", "migration-incomplete"),
    "spare": ("reserved", "cutover-quarantined"),
    "label": ("active", "migration-incomplete"),
}
_PATH_COLUMNS = ("machine", "host_alias", "repo", "repo_alias", "worktree", "title", "label")


# --- DDL -------------------------------------------------------------------------------


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(_quote(v) for v in values)})"


def _letters_only(column: str, dialect: str) -> str:
    if dialect == "dolt":
        return f"{column} regexp '^[a-z]+$'"
    return f"({column} glob '[a-z]*' and {column} not glob '*[^a-z]*')"


def _label_token(column: str, dialect: str) -> str:
    if dialect == "dolt":
        return f"{column} regexp '^[a-z0-9]*[a-z][a-z0-9]*$'"
    return f"({column} glob '*[a-z]*' and {column} not glob '*[^a-z0-9]*')"


def _relative(column: str, dialect: str) -> str:
    backslash = "'\\\\'" if dialect == "dolt" else "'\\'"
    return (
        f"({column} is null or not (substr({column}, 1, 1) in ('/', '~', {backslash}) "
        f"or (substr({column}, 2, 1) = ':' and substr({column}, 3, 1) in ('/', {backslash}))))"
    )


def _check_sql(check: str, dialect: str) -> str:
    if check == "name_token":
        return _letters_only("name", dialect)
    if check == "hold_token":
        return (
            f"((kind in ('session', 'spare') and {_letters_only('token', dialect)}) "
            f"or (kind = 'label' and {_label_token('token', dialect)}))"
        )
    if check == "hold_recovery":
        return "((state = 'migration-incomplete' and recovery is not null) or (state <> 'migration-incomplete' and recovery is null))"
    if check == "hold_kind_fields":
        clauses = []
        for kind in reg.ROW_KINDS:
            parts = [f"kind = {_quote(kind)}", _in("state", _KIND_STATES[kind])]
            parts += [f"{column} is not null" for column in _KIND_NOT_NULL[kind]]
            parts += [f"{column} is null" for column in _KIND_NULL[kind]]
            if kind == "spare":
                parts.append("source = 'spare'")
            clauses.append("(" + " and ".join(parts) + ")")
        return "(" + " or ".join(clauses) + ")"
    if check == "hold_relative_paths":
        return " and ".join(_relative(column, dialect) for column in _PATH_COLUMNS)
    raise ValueError(check)


def _column_sql(column: Column, dialect: str) -> tuple[str, list[str]]:
    """The column definition, and any check it needs in ``dialect``."""
    checks: list[str] = []
    if dialect == "dolt":
        if column.kind == "str":
            sql_type = f"varchar({column.size})"
        elif column.kind == "enum":
            sql_type = f"enum({', '.join(_quote(v) for v in column.values)})"
        else:
            sql_type = "json"
    else:
        sql_type = "text"
        if column.kind == "str":
            checks.append(f"{column.name} is null or length({column.name}) <= {column.size}")
        elif column.kind == "enum":
            checks.append(f"{column.name} is null or {_in(column.name, column.values)}")
        else:
            checks.append(f"{column.name} is null or json_valid({column.name})")
    parts = [column.name, sql_type]
    if not column.nullable:
        parts.append("not null")
    if column.default is not None:
        parts.append(f"default {_quote(column.default)}")
    return " ".join(parts), checks


def ddl(dialect: str) -> list[str]:
    """``create table`` statements for every table in ``dialect`` (``dolt`` or ``sqlite``)."""
    if dialect not in ("dolt", "sqlite"):
        raise ValueError(f"unknown dialect {dialect!r}")
    statements = []
    for table in TABLES:
        lines: list[str] = []
        checks: list[str] = []
        for column in table.columns:
            line, column_checks = _column_sql(column, dialect)
            lines.append(line)
            checks += column_checks
        lines.append(f"primary key ({', '.join(table.key)})")
        for column, other, other_column in table.foreign:
            lines.append(f"foreign key ({column}) references {other}({other_column})")
        checks += [_check_sql(check, dialect) for check in table.checks]
        lines += [f"check ({check})" for check in checks]
        statements.append(f"create table {table.name} (\n  " + ",\n  ".join(lines) + "\n)")
    return statements


def initial_meta(*, writes_enabled: bool) -> dict[str, str]:
    return {
        "schema_version": str(SCHEMA_VERSION),
        "min_reader_version": str(READER_VERSION),
        "min_dolt_version": ".".join(str(part) for part in MIN_DOLT_VERSION),
        "writes_enabled": "1" if writes_enabled else "0",
    }


# --- records and rows ------------------------------------------------------------------


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def new_claimant(row: dict) -> str:
    """A claim value unique to this attempt: ``host:session:nonce``."""
    session = row.get("title") or row.get("kind") or "hold"
    return f"{row.get('machine')}:{session}:{uuid.uuid4().hex}"[:160]


def hold_to_db(row: dict, claimant: str) -> dict:
    state = row["state"]
    if row["kind"] == "spare" and state == "active":
        state = "reserved"
    db = {name: row.get(name) for name in TABLE["holds"].column_names}
    db.update({"state": state, "claimant": claimant})
    db["recovery"] = None if row.get("recovery") is None else canonical(row["recovery"])
    return db


def hold_from_db(db: dict) -> dict:
    row: dict = dict.fromkeys(reg.ROW_FIELDS)
    for name in reg.ROW_FIELDS:
        if name in db:
            row[name] = db[name]
    row["schema"] = reg.ROW_SCHEMA
    row["name"] = db["token"] if db["kind"] in ("session", "spare") else None
    if db["kind"] == "spare" and db["state"] == "reserved":
        row["state"] = "active"
    recovery = db.get("recovery")
    row["recovery"] = json.loads(recovery) if isinstance(recovery, str) else recovery
    return row


def migration_to_db(record: dict) -> dict:
    return {
        "old_title": record["old_title"],
        "new_name": str(record["new_title"]).rsplit("-", 1)[-1],
        "new_title": record["new_title"],
        "state": record["status"],
        "machine": record["machine"],
        "client": record.get("client"),
        "recorded_at": record.get("started_at") or reg.utc_now(),
        "completed_at": record.get("completed_at"),
        "writer": record.get("writer"),
    }


def migration_from_db(db: dict) -> dict:
    return {
        "schema": 1,
        "old_title": db["old_title"],
        "new_title": db["new_title"],
        "machine": db["machine"],
        "client": db.get("client"),
        "status": db["state"],
        "started_at": db["recorded_at"],
        "completed_at": db.get("completed_at"),
        "writer": db.get("writer"),
    }


def manual_from_db(db: dict) -> dict:
    return {
        "schema": 1,
        "name": db["name"],
        "added_by": db.get("added_by"),
        "added_at": db.get("added_at"),
        "via": db.get("added_via"),
        "reason": db.get("added_reason"),
    }


def status_from_db(db: dict) -> dict:
    return {
        "status": db["status"],
        "actor": db.get("status_actor"),
        "recorded_by": db.get("status_recorded_by"),
        "set_at": db.get("status_set_at"),
        "reason": db.get("status_reason"),
    }


def verdict_from_db(db: dict) -> dict:
    return {key: db.get(key) for key in ("verdict", "actor", "recorded_by", "set_at", "reason")}


def generation_revision(generation: dict) -> str:
    return str(generation.get("policy_commit") or generation.get("policy_digest") or "unversioned")[:64]


def row_version(db: dict) -> str:
    return hashlib.sha256(canonical(db).encode("utf-8")).hexdigest()


def status_path(name: str) -> str:
    return f"{STATUS_DIR}/{name}.json"


def verdict_path(name: str, scope_kind: str, scope_value: str) -> str:
    return f"{VERDICTS_DIR}/{name}/{scope_kind}/{quote(scope_value, safe='')}.json"


def meta_path(key: str) -> str:
    return f"{META_DIR}/{key}"


def parse_verdict_path(path: str) -> tuple[str, str, str]:
    _root, name, scope_kind, value = path.split("/", 3)
    return name, scope_kind, unquote(value.removesuffix(".json"))


@dataclass
class Tables:
    """Every table's rows as read from one database state."""

    meta: dict[str, str] = field(default_factory=dict)
    policy: list[dict] = field(default_factory=list)
    names: dict[str, dict] = field(default_factory=dict)
    verdicts: dict[tuple[str, str, str], dict] = field(default_factory=dict)
    holds: dict[str, dict] = field(default_factory=dict)
    migrations: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: dict[str, list[dict]]) -> Tables:
        tables = cls()
        tables.meta = {row["k"]: row["v"] for row in rows.get("meta", [])}
        tables.policy = list(rows.get("policy_snapshot", []))
        tables.names = {row["name"]: row for row in rows.get("names", [])}
        tables.verdicts = {(r["name"], r["scope_kind"], r["scope_value"]): r for r in rows.get("verdicts", [])}
        tables.holds = {row["token"]: row for row in rows.get("holds", [])}
        tables.migrations = {row["old_title"]: row for row in rows.get("migrations", [])}
        return tables

    def snapshot(self, tip: str | None, *, fetched: bool, policy_schemas: tuple[int, ...]) -> reg.Snapshot:
        snapshot = reg.Snapshot(tip=tip, fetched=fetched, policy_schemas=policy_schemas)
        for token, db in self.holds.items():
            snapshot.rows[token] = hold_from_db(db)
            snapshot.blobs[token] = row_version(db)
            snapshot.claimants[token] = db["claimant"]
        generated = sorted(name for name, db in self.names.items() if db["origin"] in ("generated", "shipped"))
        snapshot.names = generated or None
        snapshot.manual = {name: manual_from_db(db) for name, db in self.names.items() if db["origin"] == "manual"}
        snapshot.migrations = {title: migration_from_db(db) for title, db in self.migrations.items()}
        if self.policy:
            body = self.policy[0]["body"]
            snapshot.generation = json.loads(body) if isinstance(body, str) else body
        snapshot.statuses = {name: status_from_db(db) for name, db in self.names.items()}
        snapshot.verdicts = {key: verdict_from_db(db) for key, db in self.verdicts.items()}
        snapshot.meta = dict(self.meta)
        return snapshot


# --- compiling a change ----------------------------------------------------------------


@dataclass(frozen=True)
class Statement:
    """One guarded statement: ``?`` placeholders, their values, and the change key it serves."""

    sql: str
    params: tuple
    key: str


@dataclass
class Compiled:
    statements: list[Statement]
    claimants: dict[str, str]
    # The full intended value of every key after the write; None where it must be absent.
    intended: dict[str, object]
    # The claimant a deleted hold carried, so a read-back can tell it from a new claim.
    deleted_claimants: dict[str, str]


def _equals(columns: list[str], db: dict, dialect: str) -> tuple[str, tuple]:
    """A null-safe predicate that ``columns`` still equal ``db``'s values."""
    operator = "<=>" if dialect == "dolt" else "is"
    parts, params = [], []
    for column in columns:
        value = db.get(column)
        if TABLE_COLUMN_KIND.get(column) == "json" and value is not None and dialect == "dolt":
            parts.append(f"{column} <=> cast(? as json)")
            params.append(value if isinstance(value, str) else canonical(value))
        else:
            parts.append(f"{column} {operator} ?")
            params.append(value if not isinstance(value, dict | list) else canonical(value))
    return " and ".join(parts), tuple(params)


TABLE_COLUMN_KIND = {column.name: column.kind for table in TABLES for column in table.columns}


def _value_sql(column: str, dialect: str) -> str:
    return "cast(? as json)" if dialect == "dolt" and TABLE_COLUMN_KIND.get(column) == "json" else "?"


def _insert(table: str, db: dict, dialect: str, key: str) -> Statement:
    columns = [column for column in TABLE[table].column_names if column in db]
    values = ", ".join(_value_sql(column, dialect) for column in columns)
    where, where_params = _equals(list(TABLE[table].key), db, dialect)
    sql = (
        f"insert into {table} ({', '.join(columns)}) select {values} "
        f"where not exists (select 1 from {table} where {where})"
    )
    return Statement(sql, tuple(db[column] for column in columns) + where_params, key)


def _comparable(column: str, value: object) -> object:
    if TABLE_COLUMN_KIND.get(column) == "json" and isinstance(value, str):
        return json.loads(value)
    return value


def _update(table: str, old: dict, new: dict, dialect: str, key: str) -> Statement | None:
    """The guarded update, or None when ``new`` changes nothing: an update that changes no
    column affects no row, and the read-back after the write still checks the value."""
    changed = [
        column
        for column in TABLE[table].column_names
        if column in new and _comparable(column, new[column]) != _comparable(column, old.get(column))
    ]
    if not changed:
        return None
    assignments = ", ".join(f"{column} = {_value_sql(column, dialect)}" for column in changed)
    where, where_params = _equals(list(TABLE[table].column_names), old, dialect)
    return Statement(
        f"update {table} set {assignments} where {where}", tuple(new[c] for c in changed) + where_params, key
    )


def _delete(table: str, old: dict, dialect: str, key: str) -> Statement:
    where, params = _equals(list(TABLE[table].column_names), old, dialect)
    return Statement(f"delete from {table} where {where}", params, key)


def _stage(statements: list[Statement], statement: Statement | None) -> None:
    if statement is not None:
        statements.append(statement)


def _name_db(name: str, origin: str, **fields: object) -> dict:
    db: dict = dict.fromkeys(TABLE["names"].column_names)
    db.update({"name": name, "origin": origin, "status": "unreviewed"})
    db.update(fields)
    return db


# Statement order inside one write: a name must exist before its status or verdicts.
_ORDER = ("meta", "policy", "names", "manual", "status", "verdicts", "holds", "migrations")


def compile_change(tables: Tables, change: reg.Change, dialect: str) -> Compiled:
    """The guarded statements that apply ``change`` to ``tables``, the state it was built on."""
    staged: dict[str, list[Statement]] = {group: [] for group in _ORDER}
    claimants: dict[str, str] = {}
    intended: dict[str, object] = {}
    deleted_claimants: dict[str, str] = {}
    names_after = {name: dict(db) for name, db in tables.names.items()}

    for path, data in sorted(change.writes.items()):
        if path.startswith(f"{reg.NAMES_DIR}/"):
            token = path[len(reg.NAMES_DIR) + 1 :].removesuffix(".json")
            old = tables.holds.get(token)
            if data is None:
                if old is not None:
                    staged["holds"].append(_delete("holds", old, dialect, path))
                    deleted_claimants[path] = old["claimant"]
                intended[path] = None
                continue
            row = json.loads(data)
            problem = reg.row_problem(row)
            if problem:
                raise reg.WriteRefusedError(f"refusing to write the row for {token!r}: {problem}")
            if old is None:
                claimant = new_claimant(row)
                claimants[path] = claimant
                staged["holds"].append(_insert("holds", hold_to_db(row, claimant), dialect, path))
            else:
                _stage(staged["holds"], _update("holds", old, hold_to_db(row, old["claimant"]), dialect, path))
            intended[path] = row
        elif path.startswith(f"{reg.MIGRATIONS_DIR}/"):
            title = path[len(reg.MIGRATIONS_DIR) + 1 :].removesuffix(".json")
            old = tables.migrations.get(title)
            if data is None:
                if old is not None:
                    staged["migrations"].append(_delete("migrations", old, dialect, path))
                intended[path] = None
                continue
            record = json.loads(data)
            new = migration_to_db(record)
            intended[path] = migration_from_db(new)
            if old is None:
                staged["migrations"].append(_insert("migrations", new, dialect, path))
            elif old != new:
                _stage(staged["migrations"], _update("migrations", old, new, dialect, path))
        elif path.startswith(f"{reg.MANUAL_DIR}/"):
            name = path[len(reg.MANUAL_DIR) + 1 :].removesuffix(".json")
            old = tables.names.get(name)
            if data is None:
                if old is not None and old["origin"] == "manual":
                    staged["manual"].append(_delete("names", old, dialect, path))
                    names_after.pop(name, None)
                intended[path] = None
                continue
            record = json.loads(data)
            fields = {
                "added_by": record.get("added_by"),
                "added_at": record.get("added_at"),
                "added_reason": record.get("reason"),
                "added_via": record.get("via"),
            }
            if old is None:
                db = _name_db(name, "manual", **fields)
                staged["manual"].append(_insert("names", db, dialect, path))
            else:
                db = {**old, "origin": "manual", **fields}
                _stage(staged["manual"], _update("names", old, db, dialect, path))
            names_after[name] = db
            intended[path] = manual_from_db(db)
        elif path == reg.NAMES_TXT:
            listed = [line.strip() for line in (data or b"").decode("utf-8").splitlines() if line.strip()]
            for name in sorted(set(listed)):
                if name not in tables.names:
                    db = _name_db(name, "generated")
                    staged["names"].append(_insert("names", db, dialect, path))
                    names_after[name] = db
            ruled = {name for name, _kind, _value in tables.verdicts}
            for name, old in sorted(tables.names.items()):
                # A name the owner ruled on stays, so no verdict is lost when the list changes.
                dropped = old["origin"] in ("generated", "shipped") and name not in listed
                if dropped and old["status"] == "unreviewed" and name not in ruled:
                    staged["names"].append(_delete("names", old, dialect, path))
                    names_after.pop(name)
            intended[path] = sorted(n for n, db in names_after.items() if db["origin"] in ("generated", "shipped"))
        elif path == reg.GENERATION_JSON:
            for old in tables.policy:
                staged["policy"].append(_delete("policy_snapshot", old, dialect, path))
            if data is not None:
                record = json.loads(data)
                db = {
                    "revision": generation_revision(record),
                    "generated_at": str(record.get("generated_at") or reg.utc_now())[:20],
                    "body": canonical(record),
                }
                staged["policy"].append(_insert("policy_snapshot", db, dialect, path))
                intended[path] = record
            else:
                intended[path] = None
        elif path.startswith(f"{STATUS_DIR}/"):
            name = path[len(STATUS_DIR) + 1 :].removesuffix(".json")
            entry = json.loads(data or b"null")
            if entry is None:
                raise reg.WriteRefusedError(f"a status cannot be deleted, only changed: {name}")
            if entry.get("status") not in STATUSES:
                raise reg.WriteRefusedError(f"{entry.get('status')!r} is not one of {', '.join(STATUSES)}")
            old = names_after.get(name)
            if old is None:
                raise reg.WriteRefusedError(f"{name} is not in the name database")
            new = {
                **old,
                "status": entry["status"],
                "status_actor": entry.get("actor"),
                "status_recorded_by": entry.get("recorded_by"),
                "status_set_at": entry.get("set_at"),
                "status_reason": entry.get("reason"),
            }
            _stage(staged["status"], _update("names", old, new, dialect, path))
            names_after[name] = new
            intended[path] = status_from_db(new)
        elif path.startswith(f"{VERDICTS_DIR}/"):
            name, scope_kind, scope_value = parse_verdict_path(path)
            old = tables.verdicts.get((name, scope_kind, scope_value))
            if data is None:
                if old is not None:
                    staged["verdicts"].append(_delete("verdicts", old, dialect, path))
                intended[path] = None
                continue
            entry = json.loads(data)
            new = {"name": name, "scope_kind": scope_kind, "scope_value": scope_value}
            new.update({key: entry.get(key) for key in ("verdict", "actor", "recorded_by", "set_at", "reason")})
            if old is None:
                staged["verdicts"].append(_insert("verdicts", new, dialect, path))
            elif old != new:
                _stage(staged["verdicts"], _update("verdicts", old, new, dialect, path))
            intended[path] = verdict_from_db(new)
        elif path.startswith(f"{META_DIR}/"):
            key = path[len(META_DIR) + 1 :]
            value = (data or b"").decode("utf-8")
            old_value = tables.meta.get(key)
            if old_value is None:
                staged["meta"].append(_insert("meta", {"k": key, "v": value}, dialect, path))
            elif old_value != value:
                _stage(
                    staged["meta"], _update("meta", {"k": key, "v": old_value}, {"k": key, "v": value}, dialect, path)
                )
            intended[path] = value
        else:
            raise reg.WriteRefusedError(f"no table holds the record {path!r}")
    statements = [statement for group in _ORDER for statement in staged[group]]
    return Compiled(statements, claimants, intended, deleted_claimants)


def postcondition_problems(compiled: Compiled, after: reg.Snapshot) -> list[str]:
    """What the state read back after a publish lacks of ``compiled``'s intended result.

    A claim counts only when its row carries this attempt's claimant; an update must
    equal every intended value; a delete must find the row gone, or held by a newer
    claim. Every other record must equal its intended value.
    """
    problems = []
    for path, wanted in compiled.intended.items():
        if path.startswith(f"{reg.NAMES_DIR}/"):
            token = path[len(reg.NAMES_DIR) + 1 :].removesuffix(".json")
            row = after.rows.get(token)
            if wanted is None:
                if row is not None and after.claimants.get(token) == compiled.deleted_claimants.get(path):
                    problems.append(f"{token} is still held")
                continue
            if path in compiled.claimants and after.claimants.get(token) != compiled.claimants[path]:
                problems.append(f"{token} is held by another claim ({after.claimants.get(token)})")
            elif row != wanted:
                problems.append(f"the row for {token} differs from what was written")
        elif path.startswith(f"{reg.MIGRATIONS_DIR}/"):
            title = path[len(reg.MIGRATIONS_DIR) + 1 :].removesuffix(".json")
            if after.migrations.get(title) != wanted:
                problems.append(f"the rename record for {title} differs from what was written")
        elif path.startswith(f"{reg.MANUAL_DIR}/"):
            name = path[len(reg.MANUAL_DIR) + 1 :].removesuffix(".json")
            if after.manual.get(name) != wanted:
                problems.append(f"the manual record for {name} differs from what was written")
        elif path == reg.NAMES_TXT:
            if sorted(after.names or []) != wanted:
                problems.append("the name list differs from what was written")
        elif path == reg.GENERATION_JSON:
            if after.generation != wanted:
                problems.append("the policy snapshot differs from what was written")
        elif path.startswith(f"{STATUS_DIR}/"):
            name = path[len(STATUS_DIR) + 1 :].removesuffix(".json")
            if (after.statuses or {}).get(name) != wanted:
                problems.append(f"the status of {name} differs from what was written")
        elif path.startswith(f"{VERDICTS_DIR}/"):
            if (after.verdicts or {}).get(parse_verdict_path(path)) != wanted:
                problems.append(f"the verdict {path} differs from what was written")
        elif path.startswith(f"{META_DIR}/"):
            if (after.meta or {}).get(path[len(META_DIR) + 1 :]) != wanted:
                problems.append(f"the setting {path} differs from what was written")
    return problems


# --- queries -----------------------------------------------------------------------------

FIND_FILTERS = ("status", "scope_kind", "scope_value", "machine", "state")
_FIND_COLUMNS = {
    "status": "n.status",
    "scope_kind": "v.scope_kind",
    "scope_value": "v.scope_value",
    "machine": "h.machine",
    "state": "h.state",
}


def find_query(filters: dict[str, str | None]) -> Statement:
    """One query for the pool names matching every given filter, with their verdicts and hold."""
    where, params = [], []
    for key in FIND_FILTERS:
        value = filters.get(key)
        if value is not None:
            where.append(f"{_FIND_COLUMNS[key]} = ?")
            params.append(value)
    sql = (
        "select n.name, n.status, v.scope_kind, v.scope_value, v.verdict, "
        "h.kind as hold_kind, h.machine, h.state, h.title "
        "from names n left join verdicts v on v.name = n.name left join holds h on h.token = n.name"
        + (f" where {' and '.join(where)}" if where else "")
        + " order by n.name, v.scope_kind, v.scope_value"
    )
    return Statement(sql, tuple(params), "find")


def found_names(rows: list[dict]) -> list[dict]:
    """The query's joined rows folded into one entry per name."""
    entries: dict[str, dict] = {}
    for row in rows:
        entry = entries.setdefault(
            row["name"], {"name": row["name"], "status": row["status"], "verdicts": [], "hold": None}
        )
        if row.get("scope_kind"):
            verdict = {"scope_kind": row["scope_kind"], "scope_value": row["scope_value"], "verdict": row["verdict"]}
            if verdict not in entry["verdicts"]:
                entry["verdicts"].append(verdict)
        if row.get("hold_kind"):
            entry["hold"] = {
                "kind": row["hold_kind"],
                "machine": row["machine"],
                "state": row["state"],
                "title": row.get("title"),
            }
    return list(entries.values())


def history_query(name: str) -> Statement:
    """Every recorded value of ``name``'s status, verdicts and hold, with its commit, committer and time."""
    sql = (
        "select 'status' as what, '' as scope, status as value, commit_hash, committer, commit_date "
        "from dolt_history_names where name = ? "
        "union all select 'verdict', concat(scope_kind, ' ', scope_value), verdict, commit_hash, committer, commit_date "
        "from dolt_history_verdicts where name = ? "
        "union all select 'hold', '', concat(kind, ' ', state, ' ', machine), commit_hash, committer, commit_date "
        "from dolt_history_holds where token = ? "
        "order by commit_date, what, scope"
    )
    return Statement(sql, (name, name, name), "history")


def changes_only(rows: list[dict]) -> list[dict]:
    """``rows`` without the entries that repeat the previous value of the same series."""
    last: dict[tuple[str, str], str] = {}
    kept = []
    for row in rows:
        series = (row["what"], row["scope"])
        if last.get(series) != row["value"]:
            kept.append(row)
            last[series] = row["value"]
    return kept
