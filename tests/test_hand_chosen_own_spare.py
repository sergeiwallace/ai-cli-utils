"""A hand-chosen name that is one of this machine's own unactivated spares is taken by activating it.

The migration runs for real against the migrate suite's bare-remote registry; the
``--name`` launch runs ``_do_session_launch`` up to the point where it would create
anything. A spare of another machine, or one an offline launch already took into the
local spool, stays refused as held.
"""

from __future__ import annotations

import json

import pytest
import test_migrate_to_name
import test_session_migrate
import test_session_name_launch
from name_fleet import HOST, OTHER_HOST, REF, build_fleet, git
from test_migrate_to_name import STEM, migrate
from test_session_migrate import OLD

from ai_cli import main
from ai_cli import name_registry as reg

fleet = test_migrate_to_name.fleet
slot = test_session_migrate.slot
launch = test_session_name_launch.launch


def _seed_spare(registry: reg.Registry, name: str, machine: str) -> dict:
    row = reg.spare_row(name=name, host_alias="box", machine=machine)
    registry.transact(lambda snapshot: reg.Change({reg.row_path(name): reg.checked_row(row)}, f"seed spare {name}"))
    return row


def _rows_at(remote, commit: str) -> dict[str, dict]:
    listing = git(remote, "ls-tree", "--name-only", f"{commit}:names")
    return {
        path.removesuffix(".json"): json.loads(git(remote, "show", f"{commit}:names/{path}"))
        for path in listing.splitlines()
        if path
    }


def _spares_of(rows: dict[str, dict], machine: str) -> list[str]:
    return sorted(token for token, row in rows.items() if row["kind"] == "spare" and row["machine"] == machine)


def _take_offline(registry: reg.Registry) -> reg.Reservation:
    offline_slot = reg.Slot(
        repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine=HOST, client="claude-code"
    )
    return reg.take_spare(registry, offline_slot)


def test_given_this_machines_own_spare_when_migrated_to_it_then_the_spare_is_activated_and_topped_up_in_one_commit(
    fleet,
):
    spare = _seed_spare(fleet.registry, "cleo", HOST)

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code == 0, out + err
    new = f"{STEM}-cleo"
    history = git(fleet.remote, "log", "--format=%H", REF).splitlines()
    reserving = next(commit for commit in reversed(history) if f"migrations/{OLD}.json" in fleet.changed(commit))
    assert "names/cleo.json" in fleet.changed(reserving)
    rows = _rows_at(fleet.remote, reserving)
    taken = rows["cleo"]
    assert taken["kind"] == "session" and taken["title"] == new
    changed = {key for key in reg.ROW_FIELDS if taken[key] != spare[key]}
    assert changed <= reg.SPARE_ACTIVATION_FIELDS, changed
    assert len(_spares_of(rows, HOST)) == 1  # offline_spares in the fixture policy
    assert "cleo" not in _spares_of(rows, HOST)
    assert json.loads(git(fleet.remote, "show", f"{reserving}:migrations/{OLD}.json"))["status"] == "intent"
    assert json.loads(fleet.file(f"migrations/{OLD}.json"))["status"] == "complete"


def test_given_this_machines_own_spare_when_a_migration_to_it_is_dry_run_then_it_plans_and_reserves_nothing(fleet):
    _seed_spare(fleet.registry, "cleo", HOST)
    before = fleet.tip()

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo", "-n")

    assert code == 0, out + err
    assert f"would become {STEM}-cleo" in out + err
    assert fleet.tip() == before


def test_given_another_machines_spare_when_migrated_to_it_then_it_is_refused_as_held_naming_the_holder(fleet):
    _seed_spare(fleet.registry, "cleo", OTHER_HOST)
    before = fleet.tip()

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code != 0
    assert f"cleo is held by a spare@{OTHER_HOST}" in out + err
    assert fleet.tip() == before


def test_given_an_own_spare_already_in_the_offline_spool_when_migrated_to_it_then_it_is_refused_as_held(fleet):
    _seed_spare(fleet.registry, "cleo", HOST)
    assert _take_offline(fleet.registry).name == "cleo"
    before = fleet.tip()

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code != 0
    assert f"cleo is held by a spare@{HOST}" in out + err
    assert fleet.tip() == before


def test_given_this_machines_own_spare_when_a_name_launch_is_dry_run_then_it_reports_the_spare_and_reserves_nothing(
    tmp_path, monkeypatch, launch, capsys
):
    built = build_fleet(tmp_path, monkeypatch)
    _seed_spare(built.registry, "cleo", HOST)
    before = built.tip()

    launch(dry_run=True, naming=main._NamingRequest(custom="cleo"))

    text = "".join(capsys.readouterr())
    assert "this machine's spare" in text
    assert "reserves nothing" in text
    assert built.tip() == before


def test_given_this_machines_own_spare_when_a_name_launch_chooses_it_then_the_spare_becomes_the_session(
    tmp_path, monkeypatch, launch
):
    built = build_fleet(tmp_path, monkeypatch)
    spare = _seed_spare(built.registry, "cleo", HOST)

    ai_name = test_session_name_launch.named(launch, naming=main._NamingRequest(custom="cleo"))

    rows = built.rows()
    taken = rows["cleo"]
    assert ai_name.endswith("-cleo") and taken["title"] == ai_name and taken["kind"] == "session"
    assert {key for key in reg.ROW_FIELDS if taken[key] != spare[key]} <= reg.SPARE_ACTIVATION_FIELDS
    assert len(_spares_of(rows, HOST)) == 1
    assert "names/cleo.json" in built.changed()


def test_given_another_machines_spare_when_a_name_launch_chooses_it_then_it_is_refused_as_held(
    tmp_path, monkeypatch, launch, capsys
):
    built = build_fleet(tmp_path, monkeypatch)
    _seed_spare(built.registry, "cleo", OTHER_HOST)
    before = built.tip()

    with pytest.raises(SystemExit):
        launch(naming=main._NamingRequest(custom="cleo"))

    assert f"cleo is held by a spare@{OTHER_HOST}" in "".join(capsys.readouterr())
    assert built.tip() == before
