"""``ai migrate-session --finish -a/--all`` finishes every stopped partial migration this host records.

Each test records real migrations on the fixture slot, and a second fixture repo where a test
needs a second pair, then runs the sweep through the CLI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import test_session_migrate
import test_session_migrate_hooks
from test_session_migrate import NEW, OLD, _assert_leftovers_carried, _everything, _leftovers, _migrate, run_cli

from ai_cli import main
from ai_cli import session_migrate as m

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks

HEADER = "old | new | repo | state | parts left | finish with"
OTHER_OLD, OTHER_NEW = "other-1", "other-box-1"


def _sweep(slot, *argv: str, tmux: list[str] | None = None):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux or [])),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        return run_cli(["ai", "migrate-session", *argv])


def _rows(out: str) -> dict[str, list[str]]:
    lines = [line.partition("migrate: ")[2] for line in out.splitlines()]
    rows = [line.split(" | ") for line in lines if " | " in line and line != HEADER]
    return {row[0]: row for row in rows if len(row) == 6}


def _ledger(slot) -> Path:
    return slot["state"] / "session-migrations.jsonl"


def _ledger_rows(slot) -> list[dict]:
    return [json.loads(line) for line in _ledger(slot).read_text(encoding="utf-8").splitlines()]


def _append(slot, **row: object) -> None:
    with _ledger(slot).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row) + "\n")


def _drop_finished(slot) -> None:
    """Leave only the migration row: the ledger a build from before the re-plan rule wrote."""
    rows = [row for row in _ledger_rows(slot) if not row.get("finished")]
    _ledger(slot).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _verified(slot, old: str = OLD, new: str = NEW) -> list[dict]:
    return [r for r in _ledger_rows(slot) if (r.get("old"), r.get("new")) == (old, new) and r.get("verified")]


def _snapshot(slot) -> dict:
    return {**_everything(slot), "ledger": _ledger(slot).read_bytes()}


@pytest.fixture
def recorded(slot):
    """OLD migrated to NEW and recorded unfinished, with old-name leftovers recreated after it."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _drop_finished(slot)
    slot["left"] = _leftovers(slot)
    return slot


@pytest.fixture
def other(recorded):
    """A second recorded pair in its own repo, partial: its old slot lock is still on disk."""
    repo = recorded["repo"].parent / "otherrepo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["commit", "-q", "--allow-empty", "-m", "init"],
        ["worktree", "add", "-q", str(repo / ".worktrees" / OTHER_NEW), "-b", f"wt-{OTHER_NEW}"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    lock = repo / ".worktrees" / f".{OTHER_OLD}.lock"
    lock.write_text("")
    _append(recorded, old=OTHER_OLD, new=OTHER_NEW, repo=str(repo.resolve()))
    return {"repo": repo.resolve(), "lock": lock}


def test_given_a_stopped_partial_pair_when_the_sweep_runs_then_it_is_finished_reads_complete_and_exits_0(recorded):
    code, out, err = _sweep(recorded, "--finish", "--all", "-y")

    assert code == 0, out + err
    _assert_leftovers_carried(recorded, recorded["left"])
    assert _rows(out)[OLD] == [OLD, NEW, str(recorded["repo"]), "complete", "-", "-"]
    assert len(_verified(recorded)) == 1


def test_given_a_pre_fix_finished_row_without_verified_when_the_sweep_runs_then_it_is_re_planned_and_finished(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _drop_finished(slot)
    _append(slot, old=OLD, new=NEW, repo=str(slot["repo"]), finished=True)
    left = _leftovers(slot)

    code, out, err = _sweep(slot, "-f", "-a", "-y")

    assert code == 0, out + err
    _assert_leftovers_carried(slot, left)
    assert _rows(out)[OLD][3] == "complete"
    assert len(_verified(slot)) == 1


def test_given_a_live_pair_and_a_stopped_one_when_the_sweep_runs_then_it_skips_and_names_the_live_one_and_exits_1(
    recorded, other
):
    code, out, err = _sweep(recorded, "-f", "-a", "-y", tmux=[f"c-{OTHER_NEW}"])

    assert code == 1, out + err
    rows = _rows(out)
    assert rows[OLD][3] == "complete"
    _, _, repo, state, parts, command = rows[OTHER_OLD]
    assert repo == str(other["repo"])
    assert state.startswith("skipped: live (") and f"c-{OTHER_NEW}" in state
    assert "slot lock (would do)" in parts
    assert command == f"ai migrate-session {OTHER_OLD} {OTHER_NEW} --finish -r {other['repo']}"
    assert other["lock"].exists()
    assert _verified(recorded, OTHER_OLD, OTHER_NEW) == []


def test_given_a_pair_that_goes_live_after_the_plan_when_the_sweep_runs_then_it_is_skipped_and_the_rest_finished(
    recorded, other
):
    real = m.finish_session

    def finish(repo_root, old, new, **kwargs):
        if old == OTHER_OLD and not kwargs.get("dry_run"):
            raise m.LiveSessionError(f"tmux session c-{OTHER_NEW} hosts {OTHER_NEW}")
        return real(repo_root, old, new, **kwargs)

    with patch("ai_cli.session_migrate.finish_session", side_effect=finish):
        code, out, err = _sweep(recorded, "-f", "-a", "-y")

    assert code == 1, out + err
    rows = _rows(out)
    assert rows[OLD][3] == "complete"
    assert rows[OTHER_OLD][3] == f"skipped: live (tmux session c-{OTHER_NEW} hosts {OTHER_NEW})"
    assert other["lock"].exists()


def test_given_only_complete_pairs_when_the_sweep_runs_then_nothing_is_written_and_it_exits_0(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _drop_finished(slot)
    before = _snapshot(slot)

    code, out, err = _sweep(slot, "-f", "-a", "-y")

    assert code == 0, out + err
    assert _rows(out)[OLD] == [OLD, NEW, str(slot["repo"]), "complete", "-", "-"]
    assert _snapshot(slot) == before


def test_given_a_pair_whose_slot_is_gone_when_the_sweep_runs_then_it_is_skipped_named_and_exits_1(recorded, tmp_path):
    gone = tmp_path / "gone-repo"
    _append(recorded, old="gone-1", new="gone-box-1", repo=str(gone))

    code, out, err = _sweep(recorded, "-f", "-a", "-y")

    assert code == 1, out + err
    rows = _rows(out)
    assert rows[OLD][3] == "complete"
    assert rows["gone-1"][3] == "skipped: could not plan"
    assert rows["gone-1"][4] == f"slot (missing: {gone / '.worktrees' / 'gone-box-1'} does not exist)"


def test_given_a_dry_run_when_the_sweep_runs_then_it_lists_what_it_would_finish_writes_nothing_and_exits_1(
    recorded, other
):
    before = _snapshot(recorded)

    code, out, err = _sweep(recorded, "-f", "-a", "-n", tmux=[f"c-{OTHER_NEW}"])

    assert code == 1, out + err
    rows = _rows(out)
    assert rows[OLD][3] == "would finish" and "slot lock (would do)" in rows[OLD][4]
    assert rows[OTHER_OLD][3].startswith("skipped: live (")
    assert "dry run: 1 migration(s) would be finished; nothing written" in out
    assert _snapshot(recorded) == before
    assert other["lock"].exists()


def test_given_no_yes_when_the_sweep_is_declined_then_nothing_is_written_and_it_exits_1(recorded):
    before = _snapshot(recorded)

    with patch.object(main._out, "confirm", return_value=False) as confirm:
        code, out, err = _sweep(recorded, "-f", "-a")

    assert code == 1, out + err
    assert confirm.call_args.args[2] == "Finish 1 migration(s)?"
    assert _rows(out)[OLD][3] == "would finish"
    assert "Aborted." in err
    assert _snapshot(recorded) == before


def test_given_no_yes_when_the_sweep_is_confirmed_then_it_finishes(recorded):
    with patch.object(main._out, "confirm", return_value=True):
        code, out, err = _sweep(recorded, "-f", "-a")

    assert code == 0, out + err
    _assert_leftovers_carried(recorded, recorded["left"])


def test_given_two_partial_pairs_when_the_sweep_runs_then_the_installed_build_is_checked_once(recorded, other):
    with patch.object(main, "_installed_migration_format", return_value=("ai", m.MIGRATION_FORMAT)) as probe:
        code, out, err = _sweep(recorded, "-f", "-a", "-y")

    assert code == 0, out + err
    assert probe.call_count == 1
    assert not other["lock"].exists()
    assert {row[3] for row in _rows(out).values()} == {"complete"}


def test_given_a_hook_that_refused_when_its_fix_lands_then_the_next_sweep_retries_and_finishes(recorded, hooks):
    hooks("10-board", "board", fail="plan", code=4)

    code, out, err = _sweep(recorded, "-f", "-a", "-y")
    assert code == 1, out + err
    assert _rows(out)[OLD][3] == "partial" and "hook board" in _rows(out)[OLD][4]
    assert _verified(recorded) == []

    hooks("10-board", "board")
    code, out, err = _sweep(recorded, "-f", "-a", "-y")

    assert code == 0, out + err
    assert _rows(out)[OLD][3] == "complete"
    assert len(_verified(recorded)) == 1


def test_given_no_recorded_migration_when_the_sweep_runs_then_it_says_so_and_exits_0(slot):
    code, out, err = _sweep(slot, "-f", "-a", "-y")

    assert code == 0, out + err
    assert "no migration recorded in" in out


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([OLD], id="name"),
        pytest.param(["-r", "."], id="repo"),
        pytest.param(["-b"], id="beads-only"),
        pytest.param(["-L", "4"], id="max-length"),
    ],
)
def test_given_a_flag_the_sweep_does_not_take_when_run_then_it_exits_2_and_writes_nothing(recorded, argv):
    before = _snapshot(recorded)

    code, _out, err = _sweep(recorded, "-f", "-a", *argv)

    assert code == 2
    assert "-f/--finish -a/--all takes -n/--dry-run and -y/--yes only" in err
    assert _snapshot(recorded) == before


def test_given_the_help_when_printed_then_it_names_the_finish_all_sweep():
    code, out, _err = run_cli(["ai", "migrate-session", "--help"])

    assert code == 0
    assert "-f/--finish -a/--all re-plans every recorded migration" in " ".join(out.split())
