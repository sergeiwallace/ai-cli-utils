"""Scoped verdicts and the eligibility table AC-DT-ELIG: what the picker offers and what a hand choice may take.

Each rule is checked through the picker's own candidate filter and through the
hand-choice check every launch and migration runs, on both database registries.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from name_fleet import HOST, REPO, build_fleet, status_writes

from ai_cli import name_registry as reg
from ai_cli import name_schema as schema
from ai_cli import session_naming
from ai_cli.main import _cli_group

pytestmark = [pytest.mark.usefixtures("registry_backend"), pytest.mark.registry_backends("sqlite", "dolt")]

SLOT = reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code")
SESSION = "myrepo-3"  # the session a migration renames
NAMES = ["cleo", "dora", "hugo", "iris"]


def cli(*args: str):
    return CliRunner().invoke(_cli_group, list(args))


def _verdict(name: str, kind: str, value: str, verdict: str) -> dict[str, bytes | None]:
    entry = {"verdict": verdict, "actor": "owner", "recorded_by": "t", "set_at": "2026-01-01T00:00:00Z", "reason": None}
    return {schema.verdict_path(name, kind, value): schema.canonical(entry).encode()}


def _offered(fleet, name: str) -> bool:
    snapshot = fleet.registry.snapshot()
    request, _warnings = reg.pick_request(fleet.registry, snapshot, SLOT, words=frozenset(), session=SESSION)
    candidates, _removed = session_naming.name_candidates(request)
    return name in candidates


def _hand_choice(fleet, name: str) -> tuple[str, str]:
    return reg.check_hand_chosen(fleet.registry.snapshot(), name, SLOT, frozenset(), spool={}, session=SESSION)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch, names=NAMES, statuses={})
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    return fleet


# Rule, general status, the verdicts written, then: offered by the picker, hand-choice verdict.
RULES = [
    ("R1-repo", "approved", [("repo", REPO, "rejected")], False, "rejected"),
    ("R1-host", "approved", [("host", HOST, "rejected")], False, "rejected"),
    (
        "R1-session",
        "rejected",
        [("session", SESSION, "rejected"), ("session", SESSION + "x", "approved")],
        False,
        "rejected",
    ),
    ("R2", "approved", [], True, "ok"),
    ("R2-session-approved", "approved", [("session", SESSION, "approved")], True, "ok"),
    ("R3", "unreviewed", [], False, "ok"),
    ("R4", "unreviewed", [("session", SESSION, "approved")], False, "ok"),
    ("R5", "rejected", [], False, "rejected"),
    ("R6", "rejected", [("session", SESSION, "approved")], False, "ok"),
]


@pytest.mark.parametrize(("rule", "status", "verdicts", "offered", "hand"), RULES, ids=[rule[0] for rule in RULES])
def test_given_each_eligibility_rule_when_picking_and_choosing_by_hand_then_both_results_match_the_table(
    fleet, rule, status, verdicts, offered, hand
):
    writes = status_writes({"cleo": status}) if status != "unreviewed" else {}
    for kind, value, verdict in verdicts:
        writes.update(_verdict("cleo", kind, value, verdict))
    if writes:
        fleet.registry.transact(lambda s: reg.Change(writes, f"{rule} (fixture)"))

    assert _offered(fleet, "cleo") is offered
    verdict, reason = _hand_choice(fleet, "cleo")
    assert verdict == hand, reason
    if rule.startswith("R1"):
        scope = verdicts[0]
        assert f"{scope[0]} {scope[1]}" in reason


def test_given_a_scoped_rejection_for_another_repository_host_or_session_when_picking_then_it_does_not_apply(fleet):
    writes = status_writes({"cleo": "approved"})
    writes.update(_verdict("cleo", "repo", "otherrepo", "rejected"))
    writes.update(_verdict("cleo", "host", "host-elsewhere", "rejected"))
    writes.update(_verdict("cleo", "session", "otherrepo-1", "rejected"))
    fleet.registry.transact(lambda s: reg.Change(writes, "verdicts elsewhere (fixture)"))

    assert _offered(fleet, "cleo")
    assert _hand_choice(fleet, "cleo")[0] == "ok"


def test_given_a_verdict_for_several_names_when_set_then_one_row_per_name_is_keyed_by_name_and_scope(fleet):
    tip = fleet.tip()

    result = cli(
        "set-name-verdict", "-s", "host", "-v", HOST, "-a", "owner", "-r", "too close", "rejected", "cleo", "dora"
    )

    assert result.exit_code == 0, result.output
    assert fleet.tip() != tip
    verdicts = fleet.snapshot().verdicts
    assert set(verdicts) == {("cleo", "host", HOST), ("dora", "host", HOST)}
    for key in verdicts:
        assert verdicts[key]["verdict"] == "rejected" and verdicts[key]["actor"] == "owner"
        assert verdicts[key]["reason"] == "too close" and verdicts[key]["recorded_by"].endswith(f"@{HOST}")
    cli("set-name-verdict", "-s", "repo", "-v", REPO, "-a", "owner", "approved", "cleo")
    assert set(fleet.snapshot().verdicts) == {("cleo", "host", HOST), ("dora", "host", HOST), ("cleo", "repo", REPO)}

    removed = cli("set-name-verdict", "-s", "host", "-v", HOST, "-a", "owner", "-d", "rejected", "cleo")

    assert removed.exit_code == 0, removed.output
    assert set(fleet.snapshot().verdicts) == {("dora", "host", HOST), ("cleo", "repo", REPO)}


def test_given_a_scope_kind_outside_repo_host_and_session_when_a_verdict_is_set_then_it_exits_2(fleet):
    tip = fleet.tip()

    result = cli("set-name-verdict", "-s", "planet", "-v", "earth", "-a", "owner", "rejected", "cleo")

    assert result.exit_code == 2
    assert fleet.tip() == tip
    with pytest.raises(reg.WriteRefusedError, match="not a verdict scope"):
        reg.set_name_verdict(
            fleet.registry, ["cleo"], "rejected", scope_kind="planet", scope_value="earth", actor="o", recorded_by="t"
        )


def test_given_a_verdict_for_a_name_outside_the_pool_when_set_then_the_whole_call_refuses(fleet):
    tip = fleet.tip()

    result = cli("set-name-verdict", "-s", "host", "-v", HOST, "-a", "owner", "rejected", "cleo", "zork")

    assert result.exit_code == 2
    assert "zork is not in the name database" in result.output
    assert fleet.tip() == tip
