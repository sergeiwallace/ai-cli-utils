"""Registry behaviours of T-1.4 that the git-tree suite pins, checked on both database registries."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner
from name_fleet import HOST, NAMES, REPO, build_fleet, publish_list

from ai_cli import name_list
from ai_cli import name_registry as reg
from ai_cli.main import _cli_group

pytestmark = [pytest.mark.usefixtures("registry_backend"), pytest.mark.registry_backends("sqlite", "dolt")]


def _slot(label: str | None = None) -> reg.Slot:
    return reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=label, machine=HOST, client="claude-code")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    return fleet


def test_given_a_label_whose_last_token_carries_digits_when_reserved_then_it_is_a_label_row_every_path_refuses(fleet):
    reservation = reg.reserve_label(fleet.registry, _slot(), "review-abc2")

    row = fleet.rows()["abc2"]
    assert row["kind"] == "label" and reservation.title == "myrepo-box-review-abc2"
    verdict, reason = reg.check_hand_chosen(fleet.registry.snapshot(), "abc2", _slot(), frozenset(), spool={})
    assert verdict == "held" and "abc2" in reason
    with pytest.raises(reg.HandChosenRefusedError, match="held"):
        reg.reserve_label(fleet.registry, _slot(), "other-abc2")


def test_given_a_label_whose_last_token_is_digits_only_when_reserved_then_it_refuses_writing_nothing(fleet):
    tip = fleet.tip()

    with pytest.raises(reg.WriteRefusedError):
        reg.reserve_label(fleet.registry, _slot(), "review-42")

    assert fleet.tip() == tip


@pytest.mark.parametrize("change", [{"schema": 3}, {"future_key": True}], ids=["schema", "key"])
def test_given_a_policy_snapshot_this_version_cannot_apply_when_allocating_then_it_refuses_to_update(fleet, change):
    policy = {**fleet.registry.snapshot().policy, **change}
    publish_list(fleet.registry, list(NAMES), policy, "newer")
    tip = fleet.tip()

    with pytest.raises(reg.PolicyUnknownError, match="update ai-cli-utils"):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert fleet.tip() == tip


@pytest.mark.parametrize("field", ["worktree", "repo", "title"])
def test_given_a_row_holding_an_absolute_path_when_written_then_the_database_registry_refuses_it(fleet, field):
    row = reg.session_row(
        name="cleo",
        title="myrepo-box-cleo",
        repo=REPO,
        repo_alias=REPO,
        host_alias="box",
        label=None,
        machine=HOST,
        client="claude-code",
        source="picked",
        tier=None,
        worktree=".worktrees/myrepo-box-cleo",
    )
    row[field] = "/home/user/projects/myrepo"
    tip = fleet.tip()

    with pytest.raises(reg.WriteRefusedError, match="absolute path"):
        fleet.registry.transact(lambda s: reg.Change({reg.row_path("cleo"): reg.encode_json(row)}, "absolute"))

    assert fleet.tip() == tip


@pytest.mark.registry_backends("dolt")  # a local SQLite registry has no remote to lose
def test_given_the_registry_unavailable_when_known_names_are_read_then_the_shipped_seed_comes_back_offline(
    fleet, monkeypatch
):
    reg.reserve_hand_chosen(fleet.registry, "cleo", _slot(), add=False, via="x", added_by="t", words=frozenset())
    fleet.offline()
    real_run = subprocess.run
    network: list[list[str]] = []

    def run(command, *args, **kwargs):
        if isinstance(command, list) and command[:2] in (["dolt", "fetch"], ["dolt", "push"], ["dolt", "clone"]):
            network.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)

    known = reg.known_names(fleet.registry)

    assert set(name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE)) <= known
    assert "cleo" in known
    assert network == []


def test_given_an_over_limit_row_when_names_are_checked_then_it_reports_it_and_exits_1(fleet):
    row = reg.session_row(
        name="hannah",
        title="myrepo-box-hannah",
        repo=REPO,
        repo_alias=REPO,
        host_alias="box",
        label=None,
        machine=HOST,
        client="claude-code",
        source="custom",
        tier=None,
        worktree=None,
    )
    fleet.registry.transact(lambda s: reg.Change({reg.row_path("hannah"): reg.checked_row(row)}, "long (fixture)"))

    result = CliRunner().invoke(_cli_group, ["check-names", "-j"])

    assert result.exit_code == 1, result.output
    report = json.loads(result.output.strip().splitlines()[-1])
    assert [held["token"] for held in report["over_limit"]] == ["hannah"]


def test_given_an_unreadable_registry_when_names_are_checked_then_it_exits_3(fleet, monkeypatch):
    def unreadable(self, *, fetch=True):
        raise reg.RegistryError("the database cannot be read")

    monkeypatch.setattr(type(fleet.registry), "snapshot", unreadable)

    result = CliRunner().invoke(_cli_group, ["check-names"])

    assert result.exit_code == 3
    assert "the over-limit check did not run" in result.output
