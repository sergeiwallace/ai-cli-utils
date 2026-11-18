"""``ai migrate-session --finish`` given one name finds the pair and its repo in this host's ledger.

Every test runs from a directory outside the repo and without ``-r``, so the only way the
finish can reach the slot is through the migration record.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import test_migrate_to_name
from test_session_migrate import OLD, run_cli, slot  # noqa: F401  (slot is a fixture)

fleet = test_migrate_to_name.fleet
NAMED = f"{test_migrate_to_name.STEM}-cleo"


def _ai(projects: Path, *argv: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=projects),
    ):
        return run_cli(["ai", "migrate-session", *argv])


def _ledger(migrated) -> list[dict]:
    path = migrated["state"] / "session-migrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def migrated(fleet, tmp_path, monkeypatch):
    """OLD migrated to NAMED, a stale slot lock left under OLD, and the cwd outside the repo."""
    home = fleet.slot
    code, out, err = _ai(fleet.projects, OLD, "--to-name", "cleo", "-r", str(home["repo"]), "-y")
    assert code == 0, out + err
    lock = home["repo"] / ".worktrees" / f".{OLD}.lock"
    lock.write_text("")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    return {**home, "lock": lock, "projects": fleet.projects}


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([NAMED, "--finish"], id="new"),
        pytest.param(["cleo", "--finish"], id="first-name"),
        pytest.param(["--finish", "-t", "cleo"], id="to-name"),
        pytest.param([OLD, NAMED, "--finish"], id="both-names"),
    ],
)
def test_given_a_recorded_migration_when_finished_by_one_name_from_outside_the_repo_then_the_recorded_pair_finishes(
    migrated, argv
):
    code, out, err = _ai(migrated["projects"], *argv, "-y")

    assert code == 0, out + err
    assert not migrated["lock"].exists()
    finished = [row for row in _ledger(migrated) if row.get("finished")]
    assert [(row["old"], row["new"], row["repo"]) for row in finished] == [(OLD, NAMED, str(migrated["repo"]))]


def test_given_no_recorded_migration_matches_when_finished_by_one_name_then_it_exits_2_naming_what_it_searched(
    migrated,
):
    before = _ledger(migrated)

    code, _out, err = _ai(migrated["projects"], "oak", "--finish", "-y")

    assert code == 2
    assert "no migration this host records matches oak" in err
    assert "session-migrations.jsonl" in err
    assert migrated["lock"].exists()
    assert _ledger(migrated) == before


def test_given_two_recorded_migrations_match_when_finished_by_one_name_then_it_exits_2_listing_each_command(
    migrated, tmp_path
):
    other = tmp_path / "otherrepo"
    with (migrated["state"] / "session-migrations.jsonl").open("a", encoding="utf-8") as rows:
        rows.write(json.dumps({"old": "otherrepo-2", "new": NAMED, "repo": str(other)}) + "\n")
    before = _ledger(migrated)

    code, _out, err = _ai(migrated["projects"], NAMED, "--finish", "-y")

    assert code == 2
    assert "matches 2 recorded migrations" in err
    assert f"ai migrate-session {OLD} {NAMED} --finish -r {migrated['repo']}" in err
    assert f"ai migrate-session otherrepo-2 {NAMED} --finish -r {other}" in err
    assert migrated["lock"].exists()
    assert _ledger(migrated) == before


def test_given_two_matches_when_finished_with_old_and_a_first_name_then_the_one_pair_naming_both_finishes(
    migrated, tmp_path
):
    with (migrated["state"] / "session-migrations.jsonl").open("a", encoding="utf-8") as rows:
        rows.write(json.dumps({"old": "otherrepo-2", "new": NAMED, "repo": str(tmp_path / "otherrepo")}) + "\n")

    code, out, err = _ai(migrated["projects"], OLD, "--finish", "-t", "cleo", "-y")

    assert code == 0, out + err
    assert not migrated["lock"].exists()
