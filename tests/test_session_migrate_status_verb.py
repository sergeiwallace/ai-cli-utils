"""``ai migrate-session -s/--status`` lists each recorded migration on this host as complete or partial.

Every test records real migrations on the fixture slot, then runs the status verb through the
CLI. It re-plans each pair read-only, so the listing must leave every tree, the migration ledger
included, byte for byte as it was.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import test_session_migrate
import test_session_migrate_hooks
from conftest import snapshot_tree
from test_session_migrate import NEW, OLD, _everything, _leftovers, _migrate, run_cli

from ai_cli.config import get_xdg_config_home

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks

HEADER = "old | new | repo | state | parts left | finish with"


def _status(slot, *argv: str, tmux: list[str] | None = None, config: dict | None = None):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux or [])),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        return run_cli(["ai", "migrate-session", *argv], config=config)


def _rows(out: str) -> list[str]:
    lines = [line.partition("migrate: ")[2] for line in out.splitlines()]
    return [line for line in lines if " | " in line and line != HEADER]


def _ledger(slot) -> Path:
    return slot["state"] / "session-migrations.jsonl"


def _append(slot, **row: object) -> None:
    with _ledger(slot).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row) + "\n")


def _drop_finished(slot) -> None:
    """Leave only the migration row: the ledger a build from before the re-plan rule wrote."""
    rows = [json.loads(line) for line in _ledger(slot).read_text(encoding="utf-8").splitlines()]
    _ledger(slot).write_text("".join(json.dumps(r) + "\n" for r in rows if not r.get("finished")), encoding="utf-8")


@pytest.fixture
def recorded(slot):
    """OLD migrated to NEW and recorded unfinished, with old-name leftovers recreated after it."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _drop_finished(slot)
    slot["left"] = _leftovers(slot)
    return slot


def _everything_and_ledger(slot) -> dict:
    return {**_everything(slot), "ledger": _ledger(slot).read_bytes()}


def test_given_a_pair_with_leftovers_when_status_runs_then_it_reads_partial_names_the_parts_and_exits_1(recorded):
    code, out, err = _status(recorded, "--status")

    assert code == 1, out + err
    assert HEADER in out
    [row] = _rows(out)
    old, new, repo, state, parts, command = row.split(" | ")
    assert (old, new, repo, state) == (OLD, NEW, str(recorded["repo"]), "partial")
    assert "slot lock (would do)" in parts and "memory file (would do)" in parts
    assert command == f"ai migrate-session {OLD} {NEW} --finish -r {recorded['repo']}"


def test_given_an_unverified_pair_with_nothing_left_when_status_runs_then_it_reads_complete_and_exits_0(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _drop_finished(slot)
    _append(slot, old=OLD, new=NEW, repo=str(slot["repo"]), finished=True)

    code, out, err = _status(slot, "-s")

    assert code == 0, out + err
    assert _rows(out) == [f"{OLD} | {NEW} | {slot['repo']} | complete | - | -"]


def test_given_a_verified_pair_when_status_runs_then_it_is_not_listed_until_all_is_given(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _leftovers(slot)

    code, out, err = _status(slot, "--status")
    assert code == 0, out + err
    assert _rows(out) == []
    assert "each verified finished; -a/--all re-plans them too" in out

    code, out, err = _status(slot, "--status", "-a")
    assert code == 1, out + err
    [row] = _rows(out)
    assert f"{OLD} | {NEW} | {slot['repo']} | partial | " in row


def test_given_a_verified_pair_with_a_later_transcripts_row_when_status_runs_then_it_still_reads_verified(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _append(slot, old=OLD, new=NEW, repo=str(slot["repo"]), transcripts=["example-id"], transcripts_recorded=True)

    code, out, err = _status(slot, "--status")

    assert code == 0, out + err
    assert _rows(out) == [], "a transcripts row records a resume hint, not a new migration"


def test_given_new_is_live_when_status_runs_then_it_still_re_plans_the_pair(recorded):
    before = _everything_and_ledger(recorded)

    code, out, err = _status(recorded, "--status", tmux=[f"c-{NEW}"])

    assert code == 1, out + err
    [row] = _rows(out)
    assert "| partial |" in row and "slot lock (would do)" in row
    assert _everything_and_ledger(recorded) == before


def test_given_recorded_pairs_when_status_runs_then_nothing_is_written_ledger_included(recorded, hooks):
    hooks("10-notes", "notes")
    before = _everything_and_ledger(recorded)

    for argv in (["--status"], ["--status", "-a"], ["-s", NEW]):
        code, out, err = _status(recorded, *argv)
        assert code == 1, out + err

    assert _everything_and_ledger(recorded) == before
    assert all("--dry-run" in call["args"] for call in hooks.calls())


def test_given_a_pair_whose_slot_is_gone_when_status_runs_then_it_reads_partial_slot_missing(recorded, tmp_path):
    gone = tmp_path / "gone-repo"
    _append(recorded, old="gone-1", new="gone-box-1", repo=str(gone))

    code, out, err = _status(recorded, "--status")

    assert code == 1, out + err
    row = next(row for row in _rows(out) if row.startswith("gone-1 | "))
    assert f"| {gone} | partial | slot (missing: {gone / '.worktrees' / 'gone-box-1'} does not exist) |" in row
    assert row.endswith(f"ai migrate-session gone-1 gone-box-1 --finish -r {gone}")


@pytest.mark.parametrize("name", [OLD, NEW])
def test_given_two_recorded_pairs_when_status_names_one_then_only_that_pair_is_listed(recorded, tmp_path, name):
    _append(recorded, old="other-1", new="other-box-1", repo=str(tmp_path / "other"))

    code, out, err = _status(recorded, "--status", name)

    assert code == 1, out + err
    assert [row.split(" | ")[:2] for row in _rows(out)] == [[OLD, NEW]]


def test_given_a_name_no_pair_matches_when_status_runs_then_it_exits_2_naming_what_it_searched(recorded):
    code, out, err = _status(recorded, "--status", "oak")

    assert code == 2
    assert "no migration this host records matches oak" in err
    assert _rows(out) == []


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--status", "-y"], id="status-yes"),
        pytest.param(["--status", "--finish"], id="status-finish"),
        pytest.param(["--status", "-n"], id="status-dry-run"),
        pytest.param(["--status", "-a", "-T"], id="status-to-names"),
        pytest.param(["-R"], id="remote-alone"),
    ],
)
def test_given_a_flag_status_does_not_take_when_run_then_it_exits_2(recorded, argv):
    before = _everything_and_ledger(recorded)

    code, _out, err = _status(recorded, *argv)

    assert code == 2
    assert "-s/--status takes NAME, -a/--all and -R/--remote only" in err
    assert _everything_and_ledger(recorded) == before


def test_given_all_with_to_names_when_run_then_the_sweep_still_requires_both(recorded):
    """``-a`` keeps its meaning beside ``-T``: without ``-T`` and ``-s`` it is a usage error."""
    code, _out, err = _status(recorded, "-a", "-r", str(recorded["repo"]))

    assert code != 0
    assert "-a/--all and -T/--to-names go together" in err


REMOTE = {"remote": {"host": "192.0.2.10", "user": "user", "port": 2222}}


def test_given_a_remote_machine_when_status_runs_with_remote_then_it_runs_there_and_prints_its_rows(recorded):
    code, local_out, err = _status(recorded, "--status")
    assert code == 1, local_out + err
    calls: list[list[str]] = []
    inner = subprocess.run

    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "ssh":
            calls.append(cmd)
            if "command -v zsh" in cmd[-1]:
                return subprocess.CompletedProcess(cmd, 0, "/bin/bash\n", "")
            return subprocess.CompletedProcess(cmd, 1, local_out, "")
        return inner(cmd, *args, **kwargs)

    before = _everything_and_ledger(recorded)
    with patch("ai_cli.main.subprocess.run", side_effect=run):
        code, out, err = _status(recorded, "--status", NEW, "-a", "-R", config=REMOTE)

    assert code == 1, out + err
    assert _rows(out) == _rows(local_out)
    status_call = calls[-1]
    assert "user@192.0.2.10" in status_call and "BatchMode=yes" in status_call and "2222" in status_call
    assert status_call[-1].startswith("/bin/bash -l -c ")
    assert f"ai migrate-session --status {NEW} --all" in status_call[-1]
    assert _everything_and_ledger(recorded) == before


def test_given_no_remote_machine_when_status_runs_with_remote_then_it_exits_2(recorded):
    code, _out, err = _status(recorded, "--status", "-R", config={"remote": {"host": ""}})

    assert code == 2
    assert "-R/--remote needs a remote machine" in err


def test_given_the_remote_cannot_be_reached_when_status_runs_with_remote_then_it_exits_2_saying_so(recorded):
    def run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 255, "", "ssh: connect to host 192.0.2.10 port 2222: timed out\n")

    with patch("ai_cli.main.subprocess.run", side_effect=run):
        code, _out, err = _status(recorded, "--status", "-R", config=REMOTE)

    assert code == 2
    assert "exited 255; no status read" in err
    assert "timed out" in err


def test_given_the_help_when_printed_then_it_names_status_all_and_remote():
    code, out, _err = run_cli(["ai", "migrate-session", "--help"])

    assert code == 0
    assert "-s, --status" in out and "-R, --remote" in out and "-a, --all" in out


def test_given_a_hook_dir_with_no_hooks_when_status_runs_then_the_config_dir_is_untouched(recorded):
    config = get_xdg_config_home()
    config.mkdir(parents=True, exist_ok=True)
    before = snapshot_tree(config)

    code, out, err = _status(recorded, "--status")

    assert code == 1, out + err
    assert snapshot_tree(config) == before
