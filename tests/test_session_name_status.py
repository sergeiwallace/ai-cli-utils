"""Each name's approval status: setting it with ``ai set-name-status`` and reading it with ``ai list-names``.

The registry is a bare-remote fixture (``name_fleet``); a status lives in its
``available/status.json``, and a name absent from that record needs review.
"""

from __future__ import annotations

import json
import re

import pytest
from click.testing import CliRunner
from name_fleet import HOST, NAMES, OTHER_HOST, REPO, STATUS_JSON, build_fleet, write_status

from ai_cli import config as _config
from ai_cli import name_list
from ai_cli import name_registry as reg
from ai_cli.main import _cli_group


def cli(*args: str):
    return CliRunner().invoke(_cli_group, list(args))


def _hold(fleet, name: str, machine: str) -> None:
    registry = fleet.registry if machine == HOST else fleet.other_machine(f"m-{name}")
    slot = reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=machine, client="claude-code")
    reg.reserve_hand_chosen(registry, name, slot, add=False, via="add-name", added_by="t@box", words=frozenset())


def _pool(result) -> dict[str, str]:
    return {entry["name"]: entry["status"] for entry in json.loads(result.output.strip().splitlines()[-1])}


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch, statuses={})
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    return fleet


def test_given_pool_names_when_some_are_approved_then_the_record_holds_them_and_the_rest_need_review(fleet):
    result = cli("set-name-status", "approved", "cleo", "Hugo")

    assert result.exit_code == 0, result.output
    record = json.loads(fleet.file(STATUS_JSON))
    assert {name: entry["status"] for name, entry in record["names"].items()} == {
        "cleo": "approved",
        "hugo": "approved",
    }
    assert record["names"]["cleo"]["set_by"].endswith(f"@{HOST}")
    pool = _pool(cli("list-names", "--pool", "-j"))
    assert set(pool) == set(NAMES)
    assert {name for name, status in pool.items() if status == "approved"} == {"cleo", "hugo"}
    assert {status for name, status in pool.items() if name not in ("cleo", "hugo")} == {"needs-review"}


def test_given_an_unknown_name_when_its_status_is_set_then_it_refuses_and_writes_nothing(fleet):
    tip = fleet.tip()

    result = cli("set-name-status", "approved", "cleo", "zork")

    assert result.exit_code == 2
    assert "zork is not in the name database" in result.output
    assert fleet.tip() == tip


def test_given_a_status_outside_the_values_when_set_then_it_is_a_usage_error(fleet):
    tip = fleet.tip()

    result = cli("set-name-status", "maybe", "cleo")

    assert result.exit_code == 2
    assert "approved" in result.output and "needs-review" in result.output and "excluded" in result.output
    assert fleet.tip() == tip


def test_given_an_excluded_name_when_the_pool_is_listed_then_it_stays_listed_as_excluded(fleet):
    assert cli("set-name-status", "excluded", "cleo").exit_code == 0

    listed = cli("list-names", "--pool")

    assert listed.exit_code == 0, listed.output
    assert re.search(r"\bcleo\s+excluded\s", listed.output)
    assert _pool(cli("list-names", "-p", "-j"))["cleo"] == "excluded"


def test_given_held_names_when_listed_then_each_row_shows_its_status(fleet):
    cli("set-name-status", "approved", "cleo")
    _hold(fleet, "cleo", HOST)
    _hold(fleet, "dora", OTHER_HOST)

    rows = {row["token"]: row for row in json.loads(cli("list-names", "-j").output.strip().splitlines()[-1])}
    text = cli("list-names").output

    assert rows["cleo"]["status"] == "approved" and rows["dora"]["status"] == "needs-review"
    assert "approved" in text and "needs-review" in text


def test_given_no_status_record_when_a_status_is_set_then_the_record_is_created_with_only_that_name(fleet):
    write_status(fleet.registry, None)

    result = cli("set-name-status", "approved", "milo")

    assert result.exit_code == 0, result.output
    assert set(json.loads(fleet.file(STATUS_JSON))["names"]) == {"milo"}


def test_given_an_unreadable_status_record_when_a_status_is_set_then_it_refuses_and_leaves_it(fleet):
    write_status(fleet.registry, b"{not json")

    result = cli("set-name-status", "approved", "milo")

    assert result.exit_code == 2
    assert "available/status.json" in result.output
    assert fleet.file(STATUS_JSON) == "{not json"


def test_given_no_status_record_when_the_pool_is_listed_then_it_says_so_rather_than_guess(fleet):
    write_status(fleet.registry, None)

    result = cli("list-names", "--pool")

    assert result.exit_code == 3
    assert "available/status.json" in result.output


def test_given_no_shared_registry_when_the_local_one_is_seeded_then_only_shipped_approvals_are_approved(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    (projects / "plainrepo").mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(reg, "alias_words", lambda: frozenset())
    shipped = json.loads((name_list.DATA_DIR / "status.json").read_text())["names"]
    approved = {name for name, entry in shipped.items() if entry["status"] == "approved"}

    pool = _pool(cli("list-names", "-p", "-j"))
    reservation = reg.reserve_picked(
        reg.discover().registry,
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code"),
        words=frozenset(),
    )

    assert {name for name, status in pool.items() if status == "approved"} == approved & set(pool)
    assert reservation.name in approved
