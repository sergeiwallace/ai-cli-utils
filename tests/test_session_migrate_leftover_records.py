"""``ai migrate-session`` leaves nothing pointing at the old session's moved worktrees.

A re-plan of the pair must find none of these left once a ``--finish`` has run:

* an entry of the old ``<old>-agents`` container that no ``git worktree list`` names (its
  registration pruned, or never made) is archived into the session-migration archive, so the
  emptied container is removed and the finish converges.

Every test drives the real command on the shared fixture slot.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import test_session_migrate
from test_session_migrate import NEW, OLD, _finish, _git, _migrate, run_cli

from ai_cli import session_migrate as m

slot = test_session_migrate.slot


def _status(slot, *argv: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        return run_cli(["ai", "migrate-session", "--status", "-a", *argv])


def _parts(out: str) -> dict[str, str]:
    lines = [line.partition("migrate: ")[2] for line in out.splitlines()]
    rows = [line.split(" | ") for line in lines if line.count(" | ") == 1 and line != "part | status"]
    return dict(rows)


def _ledger_archived(slot) -> list[dict]:
    rows = [json.loads(line) for line in (slot["state"] / m.MIGRATION_LEDGER).read_text().splitlines()]
    return [entry for row in rows for entry in row.get("archived", [])]


def _new_wt(slot) -> Path:
    return slot["repo"] / ".worktrees" / NEW


# --- AI-CLI-ofp7.13: unregistered entries of the old agent container ----------------------


def _pruned_entries(slot) -> Path:
    """Two entries of the old container no ``git worktree list`` names, as a real host left them."""
    container = slot["repo"] / ".worktrees" / f"{OLD}-agents"
    pruned = container / "pruned-leaf"
    pruned.mkdir(parents=True)
    (pruned / ".git").write_text(f"gitdir: {slot['repo'] / '.git' / 'worktrees' / 'pruned-leaf'}\n")
    (pruned / "work.txt").write_text("unlanded work\n")
    bare = container / "bare-leaf" / "sub"
    bare.mkdir(parents=True)
    (bare / "notes.md").write_text("notes\n")
    return container


def _archived_entry(slot, name: str) -> Path:
    [found] = list((slot["state"] / m.MIGRATION_ARCHIVE).glob(f"*/{OLD}-to-{NEW}/agent-container/{name}"))
    return found


def test_given_pruned_entries_in_the_old_agent_container_when_finished_then_they_are_archived_and_a_replan_is_empty(
    slot,
):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    container = _pruned_entries(slot)

    code, out, err = _status(slot)
    assert code == 1, out + err
    assert "agent container entry (would do)" in out

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert not container.exists()
    assert (_archived_entry(slot, "pruned-leaf") / "work.txt").read_text() == "unlanded work\n"
    assert (_archived_entry(slot, "bare-leaf") / "sub" / "notes.md").read_text() == "notes\n"
    assert {Path(row["source"]).name for row in _ledger_archived(slot)} == {"pruned-leaf", "bare-leaf"}
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert "agent container entry:" not in out and "agent container:" not in out
    assert _parts(out)["agent container entry"] == "done earlier"


def test_given_pruned_entries_when_finish_dry_runs_then_each_archive_is_planned_and_nothing_moves(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    container = _pruned_entries(slot)

    code, out, err = _finish(slot["repo"], "-n")

    assert code == 0, out + err
    assert f"agent container entry: {container / 'pruned-leaf'} (archive: " in out
    assert f"agent container: {container.resolve()} (would be removed: empty)" in out
    assert (container / "pruned-leaf" / "work.txt").is_file()
    assert not (slot["state"] / m.MIGRATION_ARCHIVE).exists()


def test_given_a_process_working_in_a_pruned_entry_when_finished_then_the_container_is_kept_and_nothing_archived(
    slot,
):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    container = _pruned_entries(slot)
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=container / "pruned-leaf")
    try:
        code, out, err = _finish(slot["repo"], "-y")
    finally:
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait()

    assert code == 1, out + err
    assert f"pid {worker.pid} works in pruned-leaf" in out
    assert (container / "pruned-leaf" / "work.txt").is_file()
    assert (container / "bare-leaf" / "sub" / "notes.md").is_file()


def test_given_a_registered_agent_worktree_beside_a_pruned_entry_when_migrated_then_only_the_pruned_one_is_archived(
    slot,
):
    container = _pruned_entries(slot)
    _git("worktree", "add", "-q", str(container / "leaf"), "-b", f"wt/{OLD}/leaf", cwd=slot["repo"])

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert (slot["repo"] / ".worktrees" / f"{NEW}-agents" / "leaf" / "README.md").is_file()
    assert {Path(row["source"]).name for row in _ledger_archived(slot)} == {"pruned-leaf", "bare-leaf"}
    assert not container.exists()


def test_given_the_archive_on_another_filesystem_when_finished_then_entries_go_to_the_containers_own_disk(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    container = _pruned_entries(slot)

    with patch("ai_cli.session_migrate._same_filesystem", return_value=False):
        code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    [archived] = list(
        (slot["repo"] / ".worktrees" / m.MIGRATION_ARCHIVE).glob(f"*/{OLD}-to-{NEW}/agent-container/pruned-leaf")
    )
    assert (archived / "work.txt").read_text() == "unlanded work\n"
    assert not container.exists()
