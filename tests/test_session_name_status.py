"""Each name's general status: ``ai set-name-status``, ``ai list-names``, and what unusable status data does.

Runs on both database registries (``name_fleet``); the git-tree store has no status model.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from click.testing import CliRunner
from name_fleet import HOST, NAMES, OTHER_HOST, REPO, build_fleet

from ai_cli import config as _config
from ai_cli import name_list
from ai_cli import name_registry as reg
from ai_cli import name_schema as schema
from ai_cli.main import _cli_group

pytestmark = [pytest.mark.usefixtures("registry_backend"), pytest.mark.registry_backends("sqlite", "dolt")]


def cli(*args: str):
    return CliRunner().invoke(_cli_group, list(args))


def _slot(machine: str = HOST) -> reg.Slot:
    return reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=machine, client="claude-code")


def _hold(fleet, name: str, machine: str = HOST) -> None:
    registry = fleet.registry if machine == HOST else fleet.other_machine(f"m-{name}")
    reg.reserve_hand_chosen(registry, name, _slot(machine), add=False, via="add-name", added_by="t", words=frozenset())


def _pool(result) -> dict[str, str]:
    return {entry["name"]: entry["status"] for entry in json.loads(result.output.strip().splitlines()[-1])}


def corrupt_status(fleet, name: str) -> None:
    """Give ``name`` a status this package does not know, as a newer package's database would."""
    if fleet.backend == "sqlite":
        connection = sqlite3.connect(fleet.registry.path, isolation_level=None)
        connection.execute("pragma ignore_check_constraints = on")
        connection.execute("update names set status = 'retired' where name = ?", (name,))
        connection.close()
        return
    writer = fleet.other_machine("newer-package")
    writer.snapshot()
    writer.dolt(
        "sql",
        "-q",
        "alter table names modify status enum('unreviewed','rejected','approved','retired') not null "
        f"default 'unreviewed'; update names set status = 'retired' where name = '{name}'; "
        "call dolt_commit('-Am', 'a newer status value')",
    )
    writer.dolt("push", "origin", "main")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch, statuses={})
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    return fleet


def test_given_several_pool_names_when_their_status_is_set_then_one_commit_records_actor_recorder_and_time(fleet):
    tip = fleet.tip()

    result = cli("set-name-status", "-a", "owner", "-r", "liked it", "approved", "cleo", "Hugo")

    assert result.exit_code == 0, result.output
    assert fleet.changed() == {schema.status_path("cleo"), schema.status_path("hugo")}
    assert len(fleet.commits) == 2  # the fixture's publish, then this one write
    assert fleet.tip() != tip
    statuses = fleet.snapshot().statuses
    for name in ("cleo", "hugo"):
        entry = statuses[name]
        assert entry["status"] == "approved" and entry["actor"] == "owner" and entry["reason"] == "liked it"
        assert entry["recorded_by"].endswith(f"@{HOST}")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry["set_at"])
    pool = _pool(cli("list-names", "--pool", "-j"))
    assert set(pool) == set(NAMES)
    assert {name for name, status in pool.items() if status == "approved"} == {"cleo", "hugo"}
    assert {status for name, status in pool.items() if name not in ("cleo", "hugo")} == {"unreviewed"}


def test_given_one_name_outside_the_pool_when_statuses_are_set_then_the_whole_call_refuses_and_writes_nothing(fleet):
    tip = fleet.tip()

    result = cli("set-name-status", "-a", "owner", "approved", "cleo", "zork")

    assert result.exit_code == 2
    assert "zork is not in the name database" in result.output
    assert fleet.tip() == tip
    assert fleet.snapshot().statuses["cleo"]["status"] == "unreviewed"


@pytest.mark.parametrize("value", ["maybe", "excluded", "needs-review"])
def test_given_a_status_outside_the_three_values_when_set_by_command_then_it_is_a_usage_error(fleet, value):
    tip = fleet.tip()

    result = cli("set-name-status", "-a", "owner", value, "cleo")

    assert result.exit_code == 2
    assert all(status in result.output for status in reg.NAME_STATUSES)
    assert fleet.tip() == tip


@pytest.mark.parametrize(("legacy", "stored"), [("excluded", "rejected"), ("needs-review", "unreviewed")])
def test_given_a_legacy_status_during_import_when_set_then_its_canonical_value_is_stored(fleet, legacy, stored):
    reg.set_name_status(fleet.registry, ["cleo"], "approved", actor="owner", recorded_by="t")

    reg.set_name_status(fleet.registry, ["cleo"], legacy, actor="owner", recorded_by="t", legacy=True)

    assert fleet.snapshot().statuses["cleo"]["status"] == stored


def test_given_a_rejected_name_when_the_pool_is_listed_then_it_stays_listed_as_rejected(fleet):
    assert cli("set-name-status", "-a", "owner", "rejected", "cleo").exit_code == 0

    listed = cli("list-names", "--pool")

    assert listed.exit_code == 0, listed.output
    assert re.search(r"\bcleo\s+rejected\s", listed.output)
    assert _pool(cli("list-names", "-p", "-j"))["cleo"] == "rejected"


def test_given_held_names_when_listed_then_each_row_shows_its_general_status(fleet):
    cli("set-name-status", "-a", "owner", "approved", "cleo")
    _hold(fleet, "cleo")
    _hold(fleet, "dora", OTHER_HOST)

    rows = {row["token"]: row for row in json.loads(cli("list-names", "-j").output.strip().splitlines()[-1])}
    text = cli("list-names").output

    assert rows["cleo"]["status"] == "approved" and rows["dora"]["status"] == "unreviewed"
    assert re.search(r"\bcleo\b.*\bapproved\b", text) and re.search(r"\bdora\b.*\bunreviewed\b", text)


def test_given_unreadable_status_data_when_the_pool_is_listed_then_it_exits_3_rather_than_guess(fleet):
    corrupt_status(fleet, "milo")

    result = cli("list-names", "--pool")

    assert result.exit_code == 3
    assert "milo" in result.output


def test_given_unreadable_status_data_when_reconciling_then_top_up_is_skipped_but_releases_still_happen(
    fleet, monkeypatch, tmp_path
):
    reg.set_name_status(fleet.registry, list(NAMES), "approved", actor="owner", recorded_by="t")
    slot = reg.Slot(
        repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code", worktree=False
    )
    reg.reserve_hand_chosen(fleet.registry, "cleo", slot, add=False, via="x", added_by="t", words=frozenset())
    corrupt_status(fleet, "milo")
    absent = reg.Absence("absent", "gone", "absent", "gone")
    monkeypatch.setattr(reg, "probe_absence", lambda row, root, adapters: absent)

    report = reg.reconcile(fleet.registry, HOST, projects_root=fleet.projects, adapters={}, host_alias="box")

    assert report.released == ["cleo"]
    assert report.spares_added == []
    assert "no unverified spare was created" in (report.top_up_skipped or "")
    rows = fleet.rows()
    assert "cleo" not in rows
    assert not [row for row in rows.values() if row["kind"] == "spare"]


def test_given_unreadable_status_data_when_picking_topping_up_or_taking_offline_then_each_refuses(fleet):
    reg.set_name_status(fleet.registry, list(NAMES), "approved", actor="owner", recorded_by="t")
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("lola"): reg.checked_row(reg.spare_row(name="lola", host_alias="box", machine=HOST))}, "spare"
        )
    )
    corrupt_status(fleet, "milo")
    tip = fleet.tip()

    with pytest.raises(reg.NameStatusError):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    with pytest.raises(reg.NameStatusError):
        reg.take_spare(fleet.registry, _slot())

    assert fleet.tip() == tip
    assert reg.read_spool() == {}


def test_given_no_shared_registry_when_the_local_one_is_seeded_then_every_shipped_name_is_approved(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    (projects / "plainrepo").mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(reg, "alias_words", lambda: frozenset())
    shipped = set(name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE))

    pool = _pool(cli("list-names", "-p", "-j"))
    reservation = reg.reserve_picked(reg.discover().registry, _slot(), words=frozenset())

    assert set(pool) == shipped
    assert set(pool.values()) == {"approved"}
    assert reservation.name in shipped
