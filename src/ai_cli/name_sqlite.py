"""The session-name registry of an install with no shared one: a local SQLite database.

It holds the same tables and constraints as the fleet's Dolt database
(:mod:`ai_cli.name_schema`), guarded by a host file lock so one process writes at a
time, and seeded on first use with the name list this package ships, every name
approved, so picks work out of the box. It keeps no row history.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import portalocker

from . import name_list
from . import name_registry as reg
from . import name_schema as schema


class SqliteRegistry(reg.Registry):
    """The local registry: one SQLite file under the package's data directory."""

    kind = "local"
    policy_schemas = (reg.POLICY_SCHEMA,)

    def __init__(
        self,
        path: Path,
        *,
        timeout_s: float = reg.DEFAULT_TIMEOUT_S,
        attempts: int = reg.DEFAULT_ATTEMPTS,
        seed: bool = True,
    ) -> None:
        super().__init__(timeout_s=timeout_s, attempts=attempts)
        self.path = path
        self.location = str(path)
        self.seed = seed

    # -- connection and lock
    def connect(self) -> sqlite3.Connection:
        """A connection with foreign keys enforced, refused when SQLite does not enforce them."""
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=self.timeout_s)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        if connection.execute("pragma foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise reg.RegistryError(f"SQLite at {self.path} does not enforce foreign keys; nothing was read")
        return connection

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with portalocker.Lock(
                str(self.path.with_suffix(".lock")), mode="a", timeout=self.timeout_s * self.attempts
            ):
                yield
        except portalocker.exceptions.LockException as exc:
            raise reg.RegistryContendedError(
                f"the local registry {self.path} stayed locked; nothing was written"
            ) from exc

    def _create(self, connection: sqlite3.Connection) -> None:
        exists = connection.execute("select 1 from sqlite_master where type = 'table' and name = 'meta'").fetchone()
        if exists:
            return
        connection.execute("begin immediate")
        try:
            for statement in schema.ddl("sqlite"):
                connection.execute(statement)
            for key, value in schema.initial_meta(writes_enabled=True).items():
                connection.execute("insert into meta (k, v) values (?, ?)", (key, value))
            if not self.seed:
                connection.execute("commit")
                return
            generation = json.loads((name_list.DATA_DIR / name_list.GENERATION_FILE).read_text(encoding="utf-8"))
            connection.execute(
                "insert into policy_snapshot (revision, generated_at, body) values (?, ?, ?)",
                (schema.generation_revision(generation), generation["generated_at"], schema.canonical(generation)),
            )
            stamp = reg.utc_now()
            for name in name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE):
                connection.execute(
                    "insert into names (name, origin, status, status_actor, status_recorded_by, status_set_at, "
                    "status_reason) values (?, 'shipped', 'approved', 'ai-cli-utils', ?, ?, ?)",
                    (name, reg.writer(), stamp, "the list this package ships"),
                )
            connection.execute("commit")
        except BaseException:
            connection.execute("rollback")
            raise

    def _tables(self, connection: sqlite3.Connection) -> schema.Tables:
        self._check_reader(connection)
        rows = {
            table.name: [dict(row) for row in connection.execute(f"select * from {table.name}")]
            for table in schema.TABLES
        }
        return schema.Tables.from_rows(rows)

    def _check_reader(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("select v from meta where k = 'min_reader_version'").fetchone()
        if row is not None and int(row[0]) > schema.READER_VERSION:
            raise reg.RegistryError(
                f"the local registry {self.path} needs reader version {row[0]}, above this package's "
                f"{schema.READER_VERSION}: update ai-cli-utils"
            )

    # -- the registry interface
    def snapshot(self, *, fetch: bool = True) -> reg.Snapshot:
        del fetch
        with self._locked(), contextlib.closing(self.connect()) as connection:
            self._create(connection)
            return self._tables(connection).snapshot(None, fetched=True, policy_schemas=self.policy_schemas)

    def transact(self, build: reg.Build, *, exempt: bool = False) -> reg.Change | None:
        del exempt
        with self._locked(), contextlib.closing(self.connect()) as connection:
            self._create(connection)
            tables = self._tables(connection)
            change = build(tables.snapshot(None, fetched=True, policy_schemas=self.policy_schemas))
            if change is None or not change.writes:
                return None
            compiled = schema.compile_change(tables, change, "sqlite")
            connection.execute("begin immediate")
            try:
                for statement in compiled.statements:
                    count = connection.execute(statement.sql, statement.params).rowcount
                    if count != 1:
                        raise reg.WriteRefusedError(
                            f"the registry changed under this write ({statement.key} matched {count} rows, "
                            "expected 1); nothing was written"
                        )
                connection.execute("commit")
            except sqlite3.IntegrityError as exc:
                connection.execute("rollback")
                raise reg.WriteRefusedError(f"the registry refused the write: {exc}; nothing was written") from exc
            except BaseException:
                connection.execute("rollback")
                raise
            self.last_claimants = compiled.claimants
            problems = schema.postcondition_problems(
                compiled, self._tables(connection).snapshot(None, fetched=True, policy_schemas=self.policy_schemas)
            )
            if problems:
                raise reg.RegistryError(f"the write committed but reads back differently: {'; '.join(problems)}")
            return change
