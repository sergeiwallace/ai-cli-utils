"""The registry's query commands: ``ai list-migrations`` (T-3.3) and ``ai find-names`` (T-3.4)."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner
from name_fleet import HOST, OTHER_HOST, REPO, build_fleet

from ai_cli import name_registry as reg
from ai_cli.main import _cli_group

pytestmark = pytest.mark.usefixtures("registry_backend")


def cli(*args: str):
    return CliRunner().invoke(_cli_group, list(args))


def _last_json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _slot(machine: str = HOST) -> reg.Slot:
    return reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=machine, client="claude-code")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch, statuses={"cleo": "approved", "dora": "approved", "hugo": "rejected"})
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    return fleet


def test_given_renames_in_both_states_when_listed_then_every_one_shows_with_its_state(fleet):
    fleet.registry.transact(
        lambda s: reg.Change(
            {
                reg.migration_path("myrepo-1"): reg.encode_json(
                    {**reg.migration_record("myrepo-1", "myrepo-box-cleo", HOST, "claude-code"), "status": "complete"}
                ),
                reg.migration_path("myrepo-2"): reg.encode_json(
                    reg.migration_record("myrepo-2", "myrepo-box-dora", HOST, "claude-code")
                ),
            },
            "renames (fixture)",
        )
    )

    rows = _last_json(cli("list-migrations", "-j"))
    text = cli("list-migrations").output

    assert {(row["old_title"], row["status"]) for row in rows} == {("myrepo-1", "complete"), ("myrepo-2", "intent")}
    assert "myrepo-1" in text and "complete" in text and "intent" in text


@pytest.mark.registry_backends("sqlite", "dolt")
def test_given_filters_when_names_are_found_then_only_names_matching_all_of_them_come_back_from_one_query(
    fleet, monkeypatch
):
    reg.set_name_verdict(
        fleet.registry,
        ["cleo", "dora"],
        "rejected",
        scope_kind="host",
        scope_value=OTHER_HOST,
        actor="o",
        recorded_by="t",
    )
    reg.reserve_hand_chosen(fleet.registry, "cleo", _slot(), add=False, via="x", added_by="t", words=frozenset())
    queries: list[str] = []
    if fleet.backend == "sqlite":
        from ai_cli.name_sqlite import SqliteRegistry

        real_connect = SqliteRegistry.connect

        def connect(self):
            connection = real_connect(self)
            connection.set_trace_callback(queries.append)
            return connection

        monkeypatch.setattr(SqliteRegistry, "connect", connect)
    else:
        real_run = subprocess.run

        def run(command, *args, **kwargs):
            if command[:2] == ["dolt", "sql"]:
                queries.append(kwargs.get("input") or "")
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)

    found = _last_json(
        cli("find-names", "-s", "approved", "-k", "host", "-v", OTHER_HOST, "-m", HOST, "-t", "active", "-j")
    )

    assert [entry["name"] for entry in found] == ["cleo"]
    assert found[0]["hold"]["state"] == "active" and found[0]["verdicts"][0]["scope_value"] == OTHER_HOST
    assert len([query for query in queries if "from names n" in query]) == 1
    assert {entry["name"] for entry in _last_json(cli("find-names", "-k", "host", "-j"))} == {"cleo", "dora"}
    assert {entry["name"] for entry in _last_json(cli("find-names", "-s", "rejected", "-j"))} == {"hugo"}


@pytest.mark.registry_backends("dolt")
def test_given_a_name_changed_over_time_when_its_history_is_asked_for_then_every_past_value_is_listed(fleet):
    reg.set_name_status(fleet.registry, ["milo"], "approved", actor="owner", recorded_by="t")
    reg.set_name_verdict(
        fleet.registry, ["milo"], "rejected", scope_kind="repo", scope_value=REPO, actor="owner", recorded_by="t"
    )
    reg.set_name_status(fleet.registry, ["milo"], "rejected", actor="owner", recorded_by="t")
    reg.reserve_hand_chosen(
        fleet.registry, "milo", _slot(OTHER_HOST), add=False, via="x", added_by="t", words=frozenset()
    )

    rows = _last_json(cli("find-names", "-H", "milo", "-j"))

    statuses = [row["value"] for row in rows if row["what"] == "status"]
    assert statuses == ["unreviewed", "approved", "rejected"]
    assert [row["value"] for row in rows if row["what"] == "verdict"] == ["rejected"]
    assert [row["value"] for row in rows if row["what"] == "hold"] == [f"session active {OTHER_HOST}"]
    assert all(row["committer"] and row["commit_date"] for row in rows)


@pytest.mark.registry_backends("sqlite")
def test_given_the_local_registry_when_history_is_asked_for_then_it_refuses_naming_the_fleet_registry(fleet):
    result = cli("find-names", "-H", "milo")

    assert result.exit_code == 2
    assert "history needs the fleet registry" in result.output
