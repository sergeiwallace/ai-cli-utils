"""The Dolt registry backend: its schema, its claim protocol and its access path (T-1.1 to T-1.3).

Every test here runs the real ``dolt`` program against a bare git remote under
``tmp_path``. The schema tests also apply the same DDL to SQLite and compare.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import portalocker
import pytest
from name_fleet import DOLT_REF, HOST, OTHER_HOST, POLICY_TOML, REPO, build_fleet, git, publish_list

from ai_cli import name_dolt
from ai_cli import name_registry as reg
from ai_cli import name_schema as schema
from ai_cli.name_dolt import DoltRegistry
from ai_cli.name_sqlite import SqliteRegistry


@pytest.fixture(autouse=True)
def _dolt_installed():
    if shutil.which("dolt") is None:
        if os.environ.get("CI"):
            pytest.fail("the dolt program is not installed, and CI must run every Dolt registry test")
        pytest.skip("the dolt program is not installed: the Dolt registry tests were not run")


def _slot(machine: str = HOST, host_alias: str = "box") -> reg.Slot:
    return reg.Slot(
        repo=REPO, repo_alias=REPO, host_alias=host_alias, label=None, machine=machine, client="claude-code"
    )


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    return build_fleet(tmp_path, monkeypatch, backend="dolt")


def _dolt_calls(monkeypatch) -> list[tuple[list[str], dict, float | None]]:
    """Every ``dolt`` command the package runs, with its environment and timeout."""
    calls: list[tuple[list[str], dict, float | None]] = []
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if isinstance(command, list) and command and command[0] == "dolt":
            env = dict(kwargs.get("env") or {})
            config_file = Path(env.get("DOLT_ROOT_PATH", "/nonexistent")) / ".dolt" / "config_global.json"
            env["__config__"] = json.loads(config_file.read_text()) if config_file.is_file() else {}
            calls.append((list(command), env, kwargs.get("timeout")))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _remote_sql(fleet, query: str) -> list[dict]:
    """``query`` against a fresh clone of the remote's registry database."""
    observer = DoltRegistry(
        fleet.repo,
        dolt_ref=DOLT_REF,
        remote="origin",
        policy_file=fleet.repo / reg.POLICY_MARKER,
        timeout_s=15,
        attempts=1,
        path=fleet.tmp / f"sql-{time.monotonic_ns()}.dolt",
    )
    observer.snapshot()
    return observer.sql_rows(query + ";\n")[0]


# --- T-1.1: one schema, two dialects ---------------------------------------------------------


class Databases:
    """An empty Dolt database and an empty SQLite file, each with the schema applied."""

    def __init__(self, tmp: Path) -> None:
        self.dolt_dir = tmp / "schema.dolt"
        self.dolt_dir.mkdir()
        self.root = tmp / "schema-root"
        (self.root / ".dolt").mkdir(parents=True)
        (self.root / ".dolt" / "config_global.json").write_text(
            json.dumps({**name_dolt.ROOT_CONFIG, "user.name": "t", "user.email": "t@example.com"})
        )
        self._dolt("init", "-b", "main")
        self._dolt("sql", input=";\n".join(schema.ddl("dolt")) + ";\n")
        self.sqlite = sqlite3.connect(tmp / "schema.sqlite3", isolation_level=None)
        self.sqlite.execute("pragma foreign_keys = on")
        for statement in schema.ddl("sqlite"):
            self.sqlite.execute(statement)

    def _dolt(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "DOLT_REMOTE_INFO_BRANCH": "", "DOLT_ROOT_PATH": str(self.root)}
        return subprocess.run(
            ["dolt", *args], cwd=self.dolt_dir, input=input, capture_output=True, text=True, env=env, check=False
        )

    def insert_dolt(self, table: str, row: dict) -> bool:
        columns = ", ".join(row)
        values = ", ".join(name_dolt.literal(value) for value in row.values())
        result = self._dolt("sql", "-q", f"insert into {table} ({columns}) values ({values})")
        if result.returncode == 0:
            self._dolt("sql", "-q", f"delete from {table}")
        return result.returncode == 0

    def insert_sqlite(self, table: str, row: dict) -> bool:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        try:
            self.sqlite.execute(f"insert into {table} ({columns}) values ({marks})", tuple(row.values()))
        except sqlite3.IntegrityError:
            return False
        self.sqlite.execute(f"delete from {table}")
        return True

    def both(self, table: str, row: dict) -> tuple[bool, bool]:
        return self.insert_dolt(table, row), self.insert_sqlite(table, row)

    def seed_name(self, name: str) -> None:
        self._dolt("sql", "-q", f"insert into names (name, origin) values ('{name}', 'generated')")
        self.sqlite.execute("insert into names (name, origin) values (?, 'generated')", (name,))


@pytest.fixture
def dbs(tmp_path):
    return Databases(tmp_path)


def _hold(kind: str, held: str, **changes) -> dict:
    if kind == "spare":
        row = reg.spare_row(name=held, host_alias="box", machine=HOST, now="2026-01-01T00:00:00Z")
    elif kind == "label":
        row = reg.label_row(
            token=held,
            title=f"myrepo-box-review-{held}",
            repo=REPO,
            repo_alias=REPO,
            host_alias="box",
            label=f"review-{held}",
            machine=HOST,
            client="claude-code",
            worktree=None,
            now="2026-01-01T00:00:00Z",
        )
    else:
        row = reg.session_row(
            name=held,
            title=f"myrepo-box-{held}",
            repo=REPO,
            repo_alias=REPO,
            host_alias="box",
            label=None,
            machine=HOST,
            client="claude-code",
            source="picked",
            tier=None,
            worktree=None,
            now="2026-01-01T00:00:00Z",
        )
    db = schema.hold_to_db(row, f"{HOST}:fixture:nonce")
    db.update(changes)
    return db


def test_given_empty_databases_when_the_schema_is_applied_then_both_hold_the_six_tables(dbs):
    dolt_tables = {row["Tables_in_schema.dolt"] if "Tables_in_schema.dolt" in row else next(iter(row.values()))
                   for row in json.loads(dbs._dolt("sql", "-r", "json", "-q", "show tables").stdout)["rows"]}  # fmt: skip
    sqlite_tables = {row[0] for row in dbs.sqlite.execute("select name from sqlite_master where type = 'table'")}

    expected = {"meta", "policy_snapshot", "names", "verdicts", "holds", "migrations"}
    assert dolt_tables == expected
    assert sqlite_tables == expected


@pytest.mark.parametrize("token", ["Abc", "abc2", "ab-c", ""])
def test_given_a_name_or_session_token_outside_letters_when_inserted_then_both_backends_refuse_it(dbs, token):
    assert dbs.both("names", {"name": token, "origin": "generated"}) == (False, False)
    assert dbs.both("holds", _hold("session", "ada", token=token)) == (False, False)
    assert dbs.both("holds", _hold("spare", "ada", token=token)) == (False, False)


@pytest.mark.parametrize("token", ["42", "Abc", "abc-2", ""])
def test_given_a_label_token_outside_its_language_when_inserted_then_both_backends_refuse_it(dbs, token):
    assert dbs.both("holds", _hold("label", "ada", token=token)) == (False, False)


@pytest.mark.parametrize("token", ["abc2", "2abc"])
def test_given_a_label_token_with_digits_that_is_not_digits_only_when_inserted_then_both_backends_accept_it(dbs, token):
    assert dbs.both("holds", _hold("label", token)) == (True, True)


def test_given_a_verdict_for_a_name_not_in_the_pool_when_inserted_then_both_backends_refuse_it(dbs, tmp_path):
    verdict = {
        "name": "zed",
        "scope_kind": "host",
        "scope_value": HOST,
        "verdict": "rejected",
        "actor": "owner",
        "recorded_by": "cli@box",
        "set_at": "2026-01-01T00:00:00Z",
    }
    assert dbs.both("verdicts", verdict) == (False, False)
    dbs.seed_name("zed")
    assert dbs.both("verdicts", verdict) == (True, True)


def test_given_the_local_registry_when_it_opens_connections_then_every_one_enforces_foreign_keys(tmp_path, monkeypatch):
    opened: list[sqlite3.Connection] = []
    real_connect = SqliteRegistry.connect

    def connect(self):
        connection = real_connect(self)
        opened.append(connection)
        assert connection.execute("pragma foreign_keys").fetchone()[0] == 1
        return connection

    monkeypatch.setattr(SqliteRegistry, "connect", connect)
    registry = SqliteRegistry(tmp_path / "names.sqlite3")
    reg.reserve_picked(registry, _slot(), words=frozenset())
    registry.snapshot()

    assert len(opened) >= 2


def test_given_malformed_json_when_a_policy_body_or_a_recovery_is_inserted_then_both_backends_refuse_it(dbs):
    good_policy = {"revision": "r1", "generated_at": "2026-01-01T00:00:00Z", "body": '{"schema": 1}'}
    assert dbs.both("policy_snapshot", good_policy) == (True, True)
    assert dbs.both("policy_snapshot", {**good_policy, "body": "{not json"}) == (False, False)
    incomplete = _hold("session", "ada", state="migration-incomplete", recovery='[{"path": "x"}]')
    assert dbs.both("holds", incomplete) == (True, True)
    assert dbs.both("holds", {**incomplete, "recovery": "[not json"}) == (False, False)


def _matrix() -> list[tuple[str, str, dict]]:
    """Valid and invalid rows for every enum, nullability, JSON and per-kind token rule."""
    cases: list[tuple[str, str, dict]] = []
    name = {"name": "ada", "origin": "generated"}
    cases += [("names", "valid", name), ("names", "origin enum", {**name, "origin": "invented"})]
    cases += [
        ("names", "status enum", {**name, "status": "maybe"}),
        ("names", "origin null", {"name": "ada", "origin": None}),
    ]
    for kind in ("session", "spare", "label"):
        base = _hold(kind, "ada")
        cases.append(("holds", f"{kind} valid", base))
        cases.append(("holds", f"{kind} kind enum", {**base, "kind": "other"}))
        cases.append(("holds", f"{kind} state enum", {**base, "state": "gone"}))
        cases.append(("holds", f"{kind} machine null", {**base, "machine": None}))
        cases.append(("holds", f"{kind} claimant null", {**base, "claimant": None}))
        cases.append(("holds", f"{kind} absolute worktree", {**base, "worktree": "/abs/path"}))
        cases.append(("holds", f"{kind} recovery while active", {**base, "recovery": "[]"}))
        for token in ("ada", "abc2", "2abc", "42", "Ada", "a-b", ""):
            cases.append(("holds", f"{kind} token {token!r}", {**base, "token": token}))
    session = _hold("session", "ada")
    cases.append(("holds", "session without client", {**session, "client": None}))
    cases.append(("holds", "spare with a title", {**_hold("spare", "ada"), "title": "myrepo-box-ada"}))
    verdict = {
        "name": "ada",
        "scope_kind": "repo",
        "scope_value": REPO,
        "verdict": "approved",
        "actor": "owner",
        "recorded_by": "cli@box",
        "set_at": "2026-01-01T00:00:00Z",
    }
    cases += [("verdicts", "valid", verdict), ("verdicts", "scope enum", {**verdict, "scope_kind": "planet"})]
    cases += [("verdicts", "verdict enum", {**verdict, "verdict": "maybe"})]
    cases += [("verdicts", "actor null", {**verdict, "actor": None})]
    migration = {
        "old_title": "myrepo-3",
        "new_name": "ada",
        "new_title": "myrepo-box-ada",
        "state": "intent",
        "machine": HOST,
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    cases += [("migrations", "valid", migration), ("migrations", "state enum", {**migration, "state": "done"})]
    return cases


def test_given_the_constraint_matrix_when_applied_to_both_backends_then_they_agree_and_tokens_match_the_validator(
    dbs,
):
    dbs.seed_name("ada")
    disagreements, token_mismatches, valid_results = [], [], {}
    for table, label, row in _matrix():
        if table == "names":
            dbs._dolt("sql", "-q", "delete from verdicts; delete from names")
            dbs.sqlite.execute("delete from verdicts")
            dbs.sqlite.execute("delete from names")
        dolt_ok, sqlite_ok = dbs.both(table, row)
        if label.endswith("valid"):
            valid_results[(table, label)] = (dolt_ok, sqlite_ok)
        if dolt_ok != sqlite_ok:
            disagreements.append((table, label, dolt_ok, sqlite_ok))
        if table == "holds" and " token " in label and row.get("kind") in reg.ROW_KINDS:
            validator_ok = reg.row_problem(schema.hold_from_db({**row, "recovery": None})) is None
            if validator_ok != dolt_ok:
                token_mismatches.append((label, validator_ok, dolt_ok))
        if table == "names":
            dbs.seed_name("ada")

    assert not disagreements
    assert not token_mismatches
    # The valid rows are accepted, so an all-refusing schema cannot pass the comparison above.
    assert valid_results and all(result == (True, True) for result in valid_results.values()), valid_results


# --- T-1.2: the claim protocol ------------------------------------------------------------------


def _race_names(count: int) -> list[str]:
    letters = "bcdfghjklmnp"
    return [f"qa{letters[i // len(letters)]}{letters[i % len(letters)]}" for i in range(count)]


def _claim_race(fleet, name: str, slots: tuple[reg.Slot, reg.Slot], registries) -> list[object]:
    barrier = threading.Barrier(2)
    outcomes: list[object] = [None, None]

    def claim(index: int) -> None:
        barrier.wait()
        try:
            outcomes[index] = reg.reserve_hand_chosen(
                registries[index], name, slots[index], add=False, via="add-name", added_by="t", words=frozenset()
            )
        except reg.RegistryError as exc:
            outcomes[index] = exc

    threads = [threading.Thread(target=claim, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return outcomes


def test_given_two_hosts_claiming_one_free_name_together_when_repeated_twenty_times_then_each_trial_has_one_winner(
    tmp_path, monkeypatch
):
    names = _race_names(20)
    policy = POLICY_TOML.replace("max_edit_distance = 1", "max_edit_distance = 0")
    fleet = build_fleet(tmp_path, monkeypatch, names=names, policy_toml=policy, backend="dolt")
    registries = (fleet.other_machine("host-a"), fleet.other_machine("host-b"))
    slots = (_slot(HOST), _slot(OTHER_HOST, "two"))

    for name in names:
        outcomes = _claim_race(fleet, name, slots, registries)
        winners = [index for index, outcome in enumerate(outcomes) if isinstance(outcome, reg.Reservation)]
        assert len(winners) == 1, (name, outcomes)
        [winner] = winners
        loser = 1 - winner
        assert isinstance(outcomes[loser], reg.HandChosenRefusedError), outcomes[loser]
        claimant = registries[winner].last_claimants[reg.row_path(name)]
        assert registries[loser].snapshot(fetch=False).claimants[name] == claimant
        assert fleet.snapshot().claimants[name] == claimant


def test_given_two_claims_with_identical_row_values_when_they_race_then_one_wins_by_its_unique_claimant(fleet):
    registries = (fleet.other_machine("same-a"), fleet.other_machine("same-b"))
    slots = (_slot(HOST), _slot(HOST))

    outcomes = _claim_race(fleet, "cleo", slots, registries)

    winners = [outcome for outcome in outcomes if isinstance(outcome, reg.Reservation)]
    assert len(winners) == 1, outcomes
    assert winners[0].row == fleet.rows()["cleo"]
    claimants = {registry.last_claimants.get(reg.row_path("cleo")) for registry in registries} - {None}
    assert claimants == {fleet.snapshot().claimants["cleo"]}


def _local_head(registry: DoltRegistry) -> str:
    return registry.sql_rows("select hashof('main') as h;\n")[0][0]["h"]


def test_given_a_claim_whose_not_exists_guard_turned_false_when_written_then_no_row_no_commit_and_no_push(fleet):
    registry = fleet.registry
    tip = fleet.tip()

    def build(snapshot):
        # The row appears between the read and the insert, as if the guard had been a prior SELECT.
        row = schema.hold_to_db(json.loads(reg.checked_row(_spare("cleo"))), "someone:else:1")
        values = ", ".join(name_dolt.literal(v) for v in row.values())
        registry.dolt("sql", "-q", f"insert into holds ({', '.join(row)}) values ({values})")
        return reg.Change({reg.row_path("cleo"): reg.checked_row(_spare("cleo"))}, "claim cleo")

    before = None
    with registry.locked():
        registry._fetch()
        before = _local_head(registry)
    with pytest.raises(reg.WriteRefusedError, match="matched 0 rows"):
        registry.transact(build)

    assert fleet.tip() == tip
    assert _local_head(registry) == before
    assert "cleo" not in fleet.rows()


def _spare(name: str, machine: str = HOST) -> dict:
    return reg.spare_row(name=name, host_alias="box", machine=machine)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_given_an_update_or_delete_whose_predicate_turned_false_when_written_then_nothing_is_committed_or_pushed(
    fleet, operation
):
    registry = fleet.registry
    registry.transact(lambda s: reg.Change({reg.row_path("cleo"): reg.checked_row(_spare("cleo"))}, "spare cleo"))
    tip = fleet.tip()

    def build(snapshot):
        registry.dolt("sql", "-q", "update holds set host_alias = 'moved' where token = 'cleo'")
        if operation == "delete":
            return reg.Change({reg.row_path("cleo"): None}, "release cleo")
        return reg.Change(
            {reg.row_path("cleo"): reg.checked_row({**snapshot.rows["cleo"], "machine": OTHER_HOST})}, "reassign cleo"
        )

    with pytest.raises(reg.WriteRefusedError, match="matched 0 rows"):
        registry.transact(build)

    assert fleet.tip() == tip
    assert fleet.rows()["cleo"]["machine"] == HOST
    assert fleet.rows()["cleo"]["host_alias"] == "box"


def test_given_a_compiled_change_when_inspected_then_every_mutating_statement_carries_its_whole_guard(fleet):
    snapshot_tables = schema.Tables.from_rows(
        {"holds": [_hold("session", "ada")], "names": [{"name": "ada", "origin": "generated", "status": "unreviewed"}]}
    )
    old_row = schema.hold_from_db(_hold("session", "ada"))
    change = reg.Change(
        {
            reg.row_path("ada"): reg.checked_row({**old_row, "client": "codex"}),
            reg.row_path("bob"): reg.checked_row(_spare("bob")),
            reg.migration_path("myrepo-3"): None,
            schema.status_path("ada"): json.dumps({"status": "approved", "actor": "owner"}).encode(),
        },
        "mixed",
    )

    compiled = schema.compile_change(snapshot_tables, change, "dolt")

    by_key = {statement.key: statement.sql for statement in compiled.statements}
    assert "where not exists (select 1 from holds where token" in by_key[reg.row_path("bob")]
    update = by_key[reg.row_path("ada")]
    assert update.startswith("update holds set client")
    assert all(
        f"{column} <=>" in update or f"{column} <=> cast" in update for column in schema.TABLE["holds"].column_names
    )
    assert "status <=> ?" in by_key[schema.status_path("ada")]
    assert reg.migration_path("myrepo-3") not in by_key  # deleting an absent rename writes nothing


def _always_lose(monkeypatch, fleet, rival: reg.Registry, *, times: int | None = None) -> list[bool]:
    cls = type(fleet.registry)
    real_push = cls._push
    results: list[bool] = []

    def push(self):
        if self is fleet.registry and (times is None or len(results) < times):
            rival.transact(lambda s: reg.Change(
                {schema.meta_path("rival"): str(time.monotonic_ns()).encode()}, "rival write"))  # fmt: skip
        result = real_push(self)
        if self is fleet.registry:
            results.append(result)
        return result

    monkeypatch.setattr(cls, "_push", push)
    return results


def test_given_a_rejected_push_when_retried_then_it_resets_and_reruns_the_guard_without_pull_or_merge(
    fleet, monkeypatch
):
    rival = fleet.other_machine("rival")
    results = _always_lose(monkeypatch, fleet, rival, times=1)
    calls = _dolt_calls(monkeypatch)
    builds: list[str | None] = []
    real_reserve_build = reg.pick_request

    def counting_pick_request(registry, snapshot, *args, **kwargs):
        builds.append(snapshot.tip)
        return real_reserve_build(registry, snapshot, *args, **kwargs)

    monkeypatch.setattr(reg, "pick_request", counting_pick_request)

    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert results == [False, True]
    assert len(builds) == 2 and builds[0] != builds[1]
    commands = [command[1] for command, _env, _timeout in calls]
    assert "pull" not in commands and "merge" not in commands
    assert ["dolt", "reset", "--hard", "origin/main"] in [command for command, _env, _t in calls]
    assert not any("--force" in command or "-f" in command[2:] for command, _e, _t in calls if command[1] == "push")
    assert reservation.name in fleet.rows()


def test_given_every_attempt_rejected_when_writing_then_it_raises_contended_and_writes_nothing_of_its_own(
    fleet, monkeypatch
):
    rival = fleet.other_machine("rival")
    results = _always_lose(monkeypatch, fleet, rival)

    with pytest.raises(reg.RegistryContendedError):
        reg.reserve_hand_chosen(
            fleet.registry, "cleo", _slot(), add=False, via="add-name", added_by="t", words=frozenset()
        )

    assert results == [False] * fleet.registry.attempts
    assert "cleo" not in fleet.rows()


def test_given_a_write_that_made_no_commit_when_published_then_nothing_is_pushed_and_it_raises(fleet, monkeypatch):
    registry = fleet.registry
    pushes: list[str] = []
    monkeypatch.setattr(DoltRegistry, "_push", lambda self: pushes.append("push") or True)
    real_sql_rows = DoltRegistry.sql_rows

    def sql_rows(self, script):
        if "dolt_commit" in script:
            # A read-only open: the commit call returns nothing and the log head does not move.
            return [[{"status": 0}], [], real_sql_rows(self, "select commit_hash from dolt_log limit 1;\n")[0]]
        return real_sql_rows(self, script)

    monkeypatch.setattr(DoltRegistry, "sql_rows", sql_rows)
    tip = fleet.tip()

    with pytest.raises(reg.RegistryError, match="made no commit"):
        reg.reserve_picked(registry, _slot(), words=frozenset())

    assert pushes == []
    assert fleet.tip() == tip


def test_given_a_policy_write_overtaken_right_after_its_push_when_read_back_then_it_refuses(fleet, monkeypatch):
    rival = fleet.other_machine("rival")
    real_push = DoltRegistry._push
    policy = fleet.registry.snapshot().policy

    def push(self):
        landed = real_push(self)
        if self is fleet.registry and landed:
            publish_list(rival, ["ada", "bob"], policy, "rival-commit")
        return landed

    monkeypatch.setattr(DoltRegistry, "_push", push)

    with pytest.raises(reg.RegistryContendedError, match="policy snapshot differs"):
        publish_list(fleet.registry, ["ada", "bob", "cyd"], policy, "mine")


def test_given_a_release_whose_token_is_claimed_again_right_after_its_push_when_read_back_then_it_succeeds(
    fleet, monkeypatch
):
    fleet.registry.transact(lambda s: reg.Change({reg.row_path("cleo"): reg.checked_row(_spare("cleo"))}, "spare"))
    rival = fleet.other_machine("rival")
    real_push = DoltRegistry._push

    def push(self):
        landed = real_push(self)
        if self is fleet.registry and landed:
            reg.reserve_hand_chosen(
                rival, "cleo", _slot(OTHER_HOST, "two"), add=False, via="x", added_by="t", words=frozenset()
            )
        return landed

    monkeypatch.setattr(DoltRegistry, "_push", push)

    fleet.registry.transact(lambda s: reg.Change({reg.row_path("cleo"): None}, "release cleo"))

    assert fleet.rows()["cleo"]["machine"] == OTHER_HOST


def test_given_status_verdict_and_rename_writes_when_published_then_each_reads_back_without_a_claimant(fleet):
    stamp = "2026-01-01T00:00:00Z"
    change = reg.Change(
        {
            schema.status_path("cleo"): json.dumps({"status": "approved", "actor": "owner", "set_at": stamp}).encode(),
            schema.verdict_path("cleo", "host", HOST): json.dumps(
                {"verdict": "rejected", "actor": "owner", "recorded_by": "cli@box", "set_at": stamp}
            ).encode(),
            reg.migration_path("myrepo-3"): reg.encode_json(
                reg.migration_record("myrepo-3", "myrepo-box-cleo", HOST, "x")
            ),
        },
        "status, verdict and rename",
    )

    assert fleet.registry.transact(lambda s: change) is change

    after = fleet.snapshot()
    assert after.statuses["cleo"]["status"] == "approved"
    assert after.verdicts[("cleo", "host", HOST)]["verdict"] == "rejected"
    assert after.migrations["myrepo-3"]["status"] == "intent"
    assert fleet.registry.last_claimants == {}


def test_given_any_registry_operation_when_dolt_runs_then_every_process_has_no_info_branch_and_metrics_off(
    fleet, monkeypatch
):
    calls = _dolt_calls(monkeypatch)

    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    fleet.other_machine("fresh").snapshot()

    assert calls
    for command, env, _timeout in calls:
        assert env.get("DOLT_REMOTE_INFO_BRANCH") == "", command
        assert env["__config__"].get("metrics.disabled") == "true", command
    refs = git(fleet.remote, "for-each-ref", "--format=%(refname)")
    assert "__dolt_remote_info__" not in refs
    assert DOLT_REF in refs


def test_given_a_reservation_with_a_spare_top_up_when_published_then_both_rows_are_one_dolt_commit(fleet):
    commits_before = len(_remote_sql(fleet, "select commit_hash from dolt_log"))

    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    log = _remote_sql(fleet, "select commit_hash from dolt_log")
    assert len(log) == commits_before + 1
    head = log[0]["commit_hash"]
    changed = _remote_sql(fleet, f"select to_token from dolt_diff_holds where to_commit = '{head}'")
    assert {row["to_token"] for row in changed} == {reservation.name, *reservation.spares_added}
    assert reservation.spares_added


def test_given_writes_turned_off_when_an_ordinary_write_runs_then_it_refuses_but_an_exempt_write_lands(fleet):
    fleet.registry.transact(lambda s: reg.Change({schema.meta_path("writes_enabled"): b"0"}, "freeze"))
    tip = fleet.tip()

    with pytest.raises(reg.WriteRefusedError, match="writes turned off"):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert fleet.tip() == tip
    fleet.registry.transact(lambda s: reg.Change({schema.meta_path("note"): b"import"}, "import"), exempt=True)
    assert fleet.tip() != tip


# --- T-1.3: the access path ---------------------------------------------------------------------

_WRITER_CHILD = textwrap.dedent(
    """
    import sys
    from ai_cli import name_registry as reg
    registry = reg.discover().registry
    slot = reg.Slot(repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine="box-one",
                    client="claude-code")
    reg.reserve_hand_chosen(registry, sys.argv[1], slot, add=False, via="x", added_by="t", words=frozenset())
    print("done", flush=True)
    """
)


def test_given_two_processes_on_one_host_when_both_write_then_the_host_lock_serializes_them_and_both_land(fleet):
    env = dict(os.environ)
    lock_file = fleet.registry.path.parent / f"{fleet.registry.path.name}.lock"
    fleet.registry.snapshot()
    with portalocker.Lock(str(lock_file), mode="a", timeout=5):
        children = [
            subprocess.Popen([sys.executable, "-c", _WRITER_CHILD, name], env=env, stdout=subprocess.PIPE, text=True)
            for name in ("cleo", "dora")
        ]
        time.sleep(2.0)
        assert all(child.poll() is None for child in children), "a writer ran while the host lock was held"
    outputs = [child.communicate(timeout=120)[0] for child in children]

    assert all("done" in output for output in outputs), outputs
    assert {"cleo", "dora"} <= set(fleet.rows())


def test_given_no_dolt_program_when_the_registry_is_used_then_it_is_unreachable_naming_the_install(
    fleet, monkeypatch, tmp_path
):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    fresh = DoltRegistry(
        fleet.repo,
        dolt_ref=DOLT_REF,
        remote="origin",
        policy_file=fleet.repo / reg.POLICY_MARKER,
        timeout_s=15,
        attempts=1,
        path=tmp_path / "no-dolt.dolt",
    )
    monkeypatch.setattr(DoltRegistry, "_git", lambda self, *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    monkeypatch.setattr(DoltRegistry, "remote_url", lambda self: "git+file:///nowhere")

    with pytest.raises(reg.RegistryUnreachableError, match="install Dolt"):
        fresh.snapshot()


def test_given_a_dolt_older_than_the_floor_when_the_registry_is_used_then_it_refuses_naming_the_upgrade(
    fleet, monkeypatch, tmp_path
):
    bin_dir = tmp_path / "old-bin"
    bin_dir.mkdir()
    fake = bin_dir / "dolt"
    fake.write_text("#!/bin/sh\necho 'dolt version 2.3.3'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    fresh = fleet.other_machine("old-dolt")

    with pytest.raises(reg.RegistryError, match="upgrade Dolt"):
        fresh.snapshot()
    with pytest.raises(reg.RegistryError, match="upgrade Dolt"):
        reg.reserve_picked(fresh, _slot(), words=frozenset())


def _raw_remote_write(fleet, sql: str) -> None:
    """Commit ``sql`` and push it as a newer package would, bypassing this client's checks."""
    writer = fleet.other_machine(f"raw-{time.monotonic_ns()}")
    writer.snapshot()
    writer.dolt("sql", "-q", f"{sql}; call dolt_commit('-Am', 'raw write')")
    writer.dolt("push", "origin", "main")


def test_given_a_database_needing_a_newer_reader_when_read_or_written_then_it_refuses_naming_the_upgrade(fleet):
    _raw_remote_write(fleet, "update meta set v = '99' where k = 'min_reader_version'")
    other = fleet.other_machine("reader")

    with pytest.raises(reg.RegistryError, match="update ai-cli-utils"):
        other.snapshot()
    with pytest.raises(reg.RegistryError, match="update ai-cli-utils"):
        reg.reserve_picked(other, _slot(), words=frozenset())


def _user_config(fleet, monkeypatch, text: str) -> None:
    base = fleet.tmp / "user-config"
    (base / "ai-cli-utils").mkdir(parents=True, exist_ok=True)
    (base / "ai-cli-utils" / "config.toml").write_text(text)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    monkeypatch.setenv("APPDATA", str(base))


def test_given_distinct_fetch_and_push_timeouts_when_writing_then_each_dolt_call_gets_only_its_own(fleet, monkeypatch):
    _user_config(
        fleet, monkeypatch, "[session_names]\nregistry_fetch_timeout_seconds = 31\nregistry_push_timeout_seconds = 37\n"
    )
    fleet.registry.timeout_s = 4
    calls = _dolt_calls(monkeypatch)
    real_run = subprocess.run

    def slow_network(command, *args, **kwargs):
        if isinstance(command, list) and command[:2] in (["dolt", "fetch"], ["dolt", "push"]):
            time.sleep(5)  # longer than the publish budget, inside the configured one
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", slow_network)

    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert {timeout for command, _env, timeout in calls if command[1] == "fetch"} == {31}
    assert {timeout for command, _env, timeout in calls if command[1] == "push"} == {37}
    assert not reservation.offline
    assert reg.read_spool() == {}


@pytest.mark.parametrize("key", ["registry_fetch_timeout_seconds", "registry_push_timeout_seconds"])
def test_given_an_invalid_timeout_setting_when_writing_then_it_refuses_naming_the_setting(fleet, monkeypatch, key):
    _user_config(fleet, monkeypatch, f"[session_names]\n{key} = -1\n")
    tip = fleet.tip()

    with pytest.raises(reg.RegistryError, match=key):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert fleet.tip() == tip


def test_given_a_host_with_no_clone_yet_when_the_last_known_snapshot_is_read_then_it_clones_first(fleet):
    fresh = fleet.other_machine("never-cloned")
    assert not fresh.has_clone()

    snapshot = reg.last_known_snapshot(fresh)

    assert fresh.has_clone()
    assert snapshot.tip is not None
    assert snapshot.names == sorted(fleet.snapshot().names)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:owner/repo.git", "git+ssh://git@github.com/owner/repo.git"),
        ("https://example.com/owner/repo.git", "git+https://example.com/owner/repo.git"),
        ("git+ssh://example.com/repo.git", "git+ssh://example.com/repo.git"),
    ],
)
def test_given_a_fleet_remote_url_when_converted_then_dolt_gets_its_git_remote_form(url, expected):
    assert name_dolt.dolt_remote_url(url) == expected


def test_given_a_local_path_remote_when_converted_then_dolt_gets_a_file_url(tmp_path):
    assert name_dolt.dolt_remote_url(str(tmp_path / "remote.git")) == f"git+{(tmp_path / 'remote.git').as_uri()}"
