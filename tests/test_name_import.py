"""Moving a fleet onto Dolt: ``ai import-names`` (T-3.1) and ``ai seed-name-verdicts`` (T-3.2, T-2.5).

The importer reads the git-tree store of a bare-remote fleet and writes the Dolt
database on the same remote. The seed runs against a Dolt fleet whose environment
checkout pins a fixture oracle; every name and session here is invented.
"""

from __future__ import annotations

import json
import shutil
import textwrap

import pytest
from click.testing import CliRunner
from name_fleet import DOLT_REF, HOST, NAMES, POLICY_TOML, REPO, build_fleet, git

from ai_cli import name_import, session_naming
from ai_cli import name_registry as reg
from ai_cli.main import _cli_group
from ai_cli.name_dolt import DoltRegistry


@pytest.fixture(autouse=True)
def _dolt_installed():
    if shutil.which("dolt") is None:
        pytest.skip("the dolt program is not installed: the Dolt import and seed tests were not run")


def cli(*args: str):
    return CliRunner().invoke(_cli_group, list(args))


def _slot(machine: str = HOST, label: str | None = None) -> reg.Slot:
    return reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=label, machine=machine, client="claude-code")


def _database(fleet, name: str = "observer") -> DoltRegistry:
    return DoltRegistry(
        fleet.repo,
        dolt_ref=DOLT_REF,
        remote="origin",
        policy_file=fleet.repo / reg.POLICY_MARKER,
        timeout_s=15,
        attempts=5,
        path=fleet.tmp / f"{name}.dolt",
    )


# --- T-3.1: import-names ---------------------------------------------------------------------


@pytest.fixture
def old_fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch, backend="git")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    registry = fleet.registry
    reg.reserve_hand_chosen(registry, "cleo", _slot(), add=False, via="x", added_by="t", words=frozenset())
    reg.reserve_hand_chosen(registry, "zara", _slot(), add=True, via="add-name", added_by="t@box", words=frozenset())
    reg.reserve_label(registry, _slot(), "review2")
    registry.transact(
        lambda s: reg.Change(
            {
                reg.row_path("dora"): reg.checked_row(reg.spare_row(name="dora", host_alias="box", machine=HOST)),
                reg.migration_path("myrepo-7"): reg.encode_json(
                    {**reg.migration_record("myrepo-7", "myrepo-box-cleo", HOST, "claude-code"), "status": "complete"}
                ),
                reg.migration_path("myrepo-8"): reg.encode_json(
                    reg.migration_record("myrepo-8", "myrepo-box-zara", HOST, "claude-code")
                ),
            },
            "spare and renames (fixture)",
        )
    )
    return fleet


def test_given_an_old_registry_tree_when_imported_then_one_dolt_commit_holds_it_all_and_a_rerun_finds_nothing(
    old_fleet,
):
    tree = old_fleet.registry.snapshot()

    result = cli("import-names")

    assert result.exit_code == 0, result.output
    assert "imported" in result.output
    after = _database(old_fleet).snapshot()
    assert after.rows == tree.rows
    assert after.manual == tree.manual
    assert after.migrations == tree.migrations
    assert sorted(after.names) == sorted(tree.names)
    assert after.generation == tree.generation
    assert after.meta["writes_enabled"] == "0"  # import runs with ordinary Dolt writes still off
    reader = _database(old_fleet, "log")
    reader.snapshot()
    log = reader.sql_rows("select message from dolt_log;\n")[0]
    assert [row["message"] for row in log if "import" in row["message"]] == [f"session-names: import {tree.tip}"]
    remote_tip = git(old_fleet.remote, "rev-parse", DOLT_REF)

    again = cli("import-names")

    assert again.exit_code == 0, again.output
    assert "nothing to import" in again.output
    assert git(old_fleet.remote, "rev-parse", DOLT_REF) == remote_tip


def test_given_an_old_registry_tree_when_imported_then_the_printed_counts_match_the_tree(old_fleet):
    result = cli("import-names", "-d")

    assert result.exit_code == 0, result.output
    assert f"names: {len(NAMES)}" in result.output
    assert "manual names: 1" in result.output
    assert "held rows: label 1, session 2, spare 1" in result.output
    assert "migrations: complete 1, intent 1" in result.output
    assert git(old_fleet.remote, "for-each-ref", DOLT_REF) == ""  # a dry run creates nothing


def test_given_a_tree_row_failing_the_validator_when_imported_then_the_whole_import_refuses_naming_it(old_fleet):
    bad = {**old_fleet.registry.snapshot().rows["cleo"], "client": None}
    old_fleet.registry.transact(lambda s: reg.Change({reg.row_path("cleo"): reg.encode_json(bad)}, "bad row"))

    result = cli("import-names")

    assert result.exit_code == 2
    assert "'cleo'" in result.output and "needs client" in result.output
    rows = _database(old_fleet).snapshot().rows
    assert rows == {}


# --- T-3.2: the seed -------------------------------------------------------------------------

POOL = [*NAMES, "dee", "fay", "gus"]
TARGET, OTHER_SESSION = "myrepo-1", "myrepo-5"

ORACLE = textwrap.dedent(
    """\
    preapproved = ["ada", "bob", "cleo", "jude"]
    addition = ["zora"]
    original_table = [
      {session = "myrepo-1", name = "kurt"},
      {session = "myrepo-2", name = "lola"},
      {session = "myrepo-3", name = "milo"},
      {session = "myrepo-4", name = "cleo"},
    ]
    corrected_table = [
      {session = "myrepo-1", name = "nora"},
      {session = "myrepo-2", name = "lola"},
      {session = "myrepo-3", name = "milo"},
      {session = "myrepo-4", name = "cleo"},
    ]
    superseded = "kurt"
    replacement = "nora"
    exclusions = ["dee", "jude"]
    answers = [{name = "jude", verdict = "approved", scope_kind = "general", scope_value = ""}]
    verdicts = [{name = "hugo", scope_kind = "host", scope_value = "box-one", verdict = "rejected"}]
    """
)


def _manifest(oracle: str = ORACLE, revision: str = "seed-1", reason: str = "the owner's approvals") -> str:
    return f'revision = "{revision}"\nactor = "owner"\nreason = "{reason}"\n' + oracle


@pytest.fixture
def seed_fleet(tmp_path, monkeypatch):
    policy = POLICY_TOML.replace("denylist = []", 'denylist = ["fay"]')
    fleet = build_fleet(tmp_path, monkeypatch, names=POOL, policy_toml=policy, backend="dolt", statuses={})
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    return fleet


def _pin_oracle(fleet, oracle: str = ORACLE, *, digest: str | None = None, commit: str | None = None) -> None:
    (fleet.repo / name_import.ORACLE_PATH).write_text(oracle)
    git(fleet.repo, "add", name_import.ORACLE_PATH)
    git(fleet.repo, "commit", "-q", "-m", "the owner-approved oracle")
    pinned = commit or git(fleet.repo, "rev-parse", "HEAD")
    (fleet.repo / name_import.CUTOVER_RECORD).write_text(
        f'oracle_commit = "{pinned}"\noracle_sha256 = "{digest or name_import.oracle_digest_of(oracle)}"\n'
    )


def _seed(fleet, manifest: str):
    path = fleet.tmp / "seed.toml"
    path.write_text(manifest)
    return cli("seed-name-verdicts", "-f", str(path))


def test_given_the_exact_oracle_candidate_when_seeded_then_every_approval_lands_at_its_scope_in_one_commit(
    seed_fleet,
):
    reg.reserve_hand_chosen(seed_fleet.registry, "fay", _slot(), add=False, via="x", added_by="t", words=frozenset())
    _pin_oracle(seed_fleet)
    commits = len(seed_fleet.commits)

    result = _seed(seed_fleet, _manifest())

    assert result.exit_code == 0, result.output
    assert len(seed_fleet.commits) == commits + 1
    after = seed_fleet.snapshot()
    status = {name: entry["status"] for name, entry in after.statuses.items()}
    assert {name for name, value in status.items() if value == "approved"} == {"ada", "bob", "cleo", "jude", "zora"}
    assert status["nora"] == status["lola"] == status["milo"] == "unreviewed"  # table names: no general approval
    assert status["kurt"] == "unreviewed"  # the superseded entry gets nothing
    assert status["dee"] == "rejected" and status["fay"] == "rejected"
    sessions = {(name, value) for (name, kind, value), v in after.verdicts.items() if kind == "session"}
    assert {("nora", "myrepo-1"), ("lola", "myrepo-2"), ("milo", "myrepo-3"), ("cleo", "myrepo-4")} <= sessions
    assert not [key for key in after.verdicts if key[0] == "kurt"]
    assert after.verdicts[("hugo", "host", HOST)]["verdict"] == "rejected"
    assert all(entry["actor"] == "owner" for entry in after.verdicts.values())
    assert after.statuses["ada"]["actor"] == "owner" and after.statuses["ada"]["recorded_by"].endswith(f"@{HOST}")


def test_given_a_name_only_in_the_corrected_table_when_seeded_then_only_its_session_takes_it_and_no_pick_offers_it(
    seed_fleet,
):
    _pin_oracle(seed_fleet)
    assert _seed(seed_fleet, _manifest()).exit_code == 0
    registry = seed_fleet.registry
    reg.set_name_status(registry, ["nora"], "rejected", actor="owner", recorded_by="t")
    snapshot = registry.snapshot()

    for session in (TARGET, OTHER_SESSION, None):
        request, _w = reg.pick_request(registry, snapshot, _slot(), words=frozenset(), session=session)
        assert "nora" not in session_naming.name_candidates(request)[0]
    with pytest.raises(reg.HandChosenRefusedError, match="rejected"):
        reg.reserve_for_migration(registry, _slot(), OTHER_SESSION, name="nora", max_length=None, words=frozenset())
    reservation = reg.reserve_for_migration(registry, _slot(), TARGET, name="nora", max_length=None, words=frozenset())

    assert reservation.name == "nora"  # R6: rejected in general, approved for its own session's migration
    request, _w = reg.pick_request(registry, registry.snapshot(), _slot(), words=frozenset(), session=TARGET)
    assert "cleo" in session_naming.name_candidates(request)[0]  # in the table and pre-approved


def test_given_an_unreviewed_table_name_when_another_session_chooses_it_then_it_is_decided_as_unreviewed(seed_fleet):
    _pin_oracle(seed_fleet)
    assert _seed(seed_fleet, _manifest()).exit_code == 0

    verdict, _reason = reg.check_hand_chosen(
        seed_fleet.registry.snapshot(), "lola", _slot(), frozenset(), spool={}, session=OTHER_SESSION
    )

    assert verdict == "ok"  # R3, as if the myrepo-2 approval were absent


def test_given_an_approved_entry_outside_the_pool_when_seeded_then_it_is_added_as_manual_in_the_same_commit(
    seed_fleet,
):
    _pin_oracle(seed_fleet)

    assert _seed(seed_fleet, _manifest()).exit_code == 0

    seeded = seed_fleet.commits[-1].writes
    assert reg.manual_path("zora") in seeded and "status/zora.json" in seeded
    manual = seed_fleet.snapshot().manual["zora"]
    assert manual["added_by"] == "owner" and manual["reason"] == "the owner's approvals" and manual["via"] == "seed"


def test_given_a_held_denylisted_name_when_seeded_then_the_hold_stays_with_a_session_approval_for_its_holder(
    seed_fleet,
):
    taken = reg.reserve_hand_chosen(
        seed_fleet.registry, "fay", _slot(), add=False, via="x", added_by="t", words=frozenset()
    )
    _pin_oracle(seed_fleet)

    assert _seed(seed_fleet, _manifest()).exit_code == 0

    after = seed_fleet.snapshot()
    assert after.rows["fay"] == taken.row
    assert after.verdicts[("fay", "session", taken.title)]["verdict"] == "approved"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda o: o.replace('name = "hugo"', 'name = "zed"'), "zed"),
        (lambda o: o.replace('exclusions = ["dee", "jude"]', 'exclusions = ["dee", "jude", "zed"]'), "zed"),
    ],
    ids=["scoped-verdict", "rejection"],
)
def test_given_a_seed_naming_a_name_outside_the_pool_when_seeded_then_the_whole_seed_refuses(seed_fleet, edit, message):
    oracle = edit(ORACLE)
    _pin_oracle(seed_fleet, oracle)
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest(oracle))

    assert result.exit_code == 2
    assert message in result.output and "pool does not hold" in result.output
    assert seed_fleet.tip() == tip


def _in_corrected(oracle: str, old: str, new: str) -> str:
    """``oracle`` with ``old`` replaced inside the corrected table only."""
    before, after = oracle.split("corrected_table", 1)
    return before + "corrected_table" + after.replace(old, new, 1)


SUBSTITUTION_DEFECTS = {
    "superseded-not-in-original": lambda o: o.replace('superseded = "kurt"', 'superseded = "gus"'),
    "superseded-still-corrected": lambda o: o.replace('{session = "myrepo-1", name = "nora"}', '{session = "myrepo-1", name = "nora"},\n  {session = "myrepo-6", name = "kurt"}'),
    "superseded-pre-approved": lambda o: o.replace('preapproved = ["ada", "bob", "cleo", "jude"]', 'preapproved = ["ada", "bob", "cleo", "jude", "kurt"]'),
    "replacement-absent": lambda o: o.replace('replacement = "nora"', 'replacement = "gus"'),
    "replacement-other-session": lambda o: o.replace('{session = "myrepo-1", name = "nora"}', '{session = "myrepo-9", name = "nora"}'),
    "entry-without-session": lambda o: _in_corrected(o, '{session = "myrepo-2", name = "lola"}', '{session = "", name = "lola"}'),
}  # fmt: skip


@pytest.mark.parametrize("defect", sorted(SUBSTITUTION_DEFECTS))
def test_given_a_substitution_that_cannot_apply_when_validated_then_it_fails_before_any_seed_row(seed_fleet, defect):
    oracle = SUBSTITUTION_DEFECTS[defect](ORACLE)
    assert oracle != ORACLE
    _pin_oracle(seed_fleet, oracle)
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest(oracle))

    assert result.exit_code == 2
    assert "cannot be applied as recorded" in result.output
    assert seed_fleet.tip() == tip


def test_given_an_approved_name_also_excluded_with_no_owner_answer_when_validated_then_it_fails_naming_it(seed_fleet):
    oracle = ORACLE.replace(
        'answers = [{name = "jude", verdict = "approved", scope_kind = "general", scope_value = ""}]', "answers = []"
    )
    _pin_oracle(seed_fleet, oracle)
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest(oracle))

    assert result.exit_code == 2
    assert "jude is approved but also excluded or denylisted" in result.output
    assert seed_fleet.tip() == tip
    assert seed_fleet.snapshot().statuses["jude"]["status"] == "unreviewed"


def test_given_an_approved_name_on_the_fleet_denylist_with_no_answer_when_validated_then_it_fails(seed_fleet):
    oracle = ORACLE.replace('addition = ["zora"]', 'addition = ["fay"]')
    _pin_oracle(seed_fleet, oracle)
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest(oracle))

    assert result.exit_code == 2
    assert "fay is approved but also excluded or denylisted" in result.output
    assert seed_fleet.tip() == tip


@pytest.mark.parametrize("missing", ["record", "commit", "digest", "oracle-at-commit"])
def test_given_the_oracle_or_its_pin_missing_when_validated_then_it_fails_before_any_seed_row(seed_fleet, missing):
    _pin_oracle(seed_fleet)
    record = seed_fleet.repo / name_import.CUTOVER_RECORD
    lines = record.read_text().splitlines()
    if missing == "record":
        record.unlink()
    elif missing == "commit":
        record.write_text(lines[1] + "\n")
    elif missing == "digest":
        record.write_text(lines[0] + "\n")
    else:
        record.write_text(f'oracle_commit = "{"0" * 40}"\n{lines[1]}\n')
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest())

    assert result.exit_code == 2
    assert seed_fleet.tip() == tip


def test_given_an_oracle_whose_digest_differs_from_the_stored_one_when_validated_then_it_fails(seed_fleet):
    _pin_oracle(seed_fleet, digest="0" * 64)
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, _manifest())

    assert result.exit_code == 2
    assert "not the " + "0" * 64 in result.output
    assert seed_fleet.tip() == tip


PART_EDITS = {
    "preapproved": lambda o: o.replace('"ada", "bob"', '"ada", "bert"'),
    "addition": lambda o: o.replace('addition = ["zora"]', 'addition = ["iris"]'),
    "original_table": lambda o: o.replace('{session = "myrepo-3", name = "milo"},\n  {session = "myrepo-4", name = "cleo"},\n]\ncorrected', '{session = "myrepo-3", name = "milo"},\n  {session = "myrepo-7", name = "cleo"},\n]\ncorrected'),
    "corrected_table_session": lambda o: o.replace('{session = "myrepo-3", name = "milo"},\n  {session = "myrepo-4", name = "cleo"},\n]\nsuperseded', '{session = "myrepo-8", name = "milo"},\n  {session = "myrepo-4", name = "cleo"},\n]\nsuperseded'),
    "exclusions": lambda o: o.replace('exclusions = ["dee", "jude"]', 'exclusions = ["gus", "jude"]'),
    "answers": lambda o: o.replace('scope_kind = "general", scope_value = ""', 'scope_kind = "host", scope_value = "box-one"'),
    "verdicts": lambda o: o.replace('scope_value = "box-one", verdict = "rejected"', 'scope_value = "box-two", verdict = "rejected"'),
}  # fmt: skip


@pytest.mark.parametrize("part", sorted(PART_EDITS))
def test_given_a_self_consistent_candidate_differing_from_the_pinned_oracle_when_seeded_then_it_writes_nothing(
    seed_fleet, part
):
    _pin_oracle(seed_fleet)
    candidate = PART_EDITS[part](ORACLE)
    assert candidate != ORACLE
    # The candidate even carries its own matching digest, which proves nothing.
    manifest = _manifest(candidate) + f'\nexpected_sha256 = "{name_import.oracle_digest_of(candidate)}"\n'
    tip = seed_fleet.tip()

    result = _seed(seed_fleet, manifest)

    assert result.exit_code == 2
    assert "differs from the owner-approved oracle" in result.output
    assert seed_fleet.tip() == tip


def test_given_a_seed_revision_already_applied_when_seeded_again_then_it_makes_no_commit_or_refuses_on_new_content(
    seed_fleet,
):
    _pin_oracle(seed_fleet)
    assert _seed(seed_fleet, _manifest()).exit_code == 0
    tip = seed_fleet.tip()

    again = _seed(seed_fleet, _manifest())
    changed = _seed(seed_fleet, _manifest(reason="something else"))

    assert again.exit_code == 0 and "already applied" in again.output
    assert changed.exit_code == 2 and "already applied with different content" in changed.output
    assert seed_fleet.tip() == tip
    assert json.loads(json.dumps(seed_fleet.snapshot().meta))["seed_revision"] == "seed-1"
