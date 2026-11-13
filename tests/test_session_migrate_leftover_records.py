"""``ai migrate-session`` leaves nothing pointing at the old session's moved worktrees.

Three stores keep a path a migration moves, and a re-plan of the pair must find none of them
left once a ``--finish`` has run:

* an entry of the old ``<old>-agents`` container that no ``git worktree list`` names (its
  registration pruned, or never made) is archived into the session-migration archive, so the
  emptied container is removed and the finish converges;
* each Agent-tool sub-agent record under the session's transcript, ``<session id>/subagents/
  agent-<id>.meta.json`` (``worktreePath``, ``inheritedWorktreePath``) and its transcript
  ``agent-<id>.jsonl`` (``cwd``), is repointed at the moved worktree;
* each Claude Code background-job record, ``jobs/<short id>/state.json``, of the session's
  transcripts or with its cwd in a moved worktree has ``cwd`` and ``linkScanPath`` repointed.

Every test drives the real command on the shared fixture slot.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, UUID, _finish, _git, _jsonl, _migrate, run_cli

from ai_cli import session_migrate as m
from ai_cli.cc_migrate import cc_project_dir

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
    # The worker reports its own pid: on Windows a venv's python.exe is a launcher that
    # starts the real interpreter as a child, so Popen's pid is not the process whose
    # working directory is the entry.
    pid_file = slot["state"] / "worker.pid"
    script = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    worker = subprocess.Popen([sys.executable, "-c", script], cwd=container / "pruned-leaf")
    try:
        deadline = time.monotonic() + 30
        while not (pid_file.is_file() and pid_file.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        worker_pid = int(pid_file.read_text())
        code, out, err = _finish(slot["repo"], "-y")
    finally:
        try:
            children = psutil.Process(worker.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for process in children:
            process.kill()
        worker.kill()
        worker.wait()

    assert code == 1, out + err
    # Either process of a launcher pair works in the entry, so either may be the one named.
    named = {int(pid) for pid in re.findall(r"pid (\d+) works in pruned-leaf", out)}
    assert named and named <= {worker.pid, worker_pid}, out
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


# --- AI-CLI-ofp7.12: Agent-tool sub-agent records ------------------------------------------


def _subagents(slot) -> Path:
    return slot["project"] / UUID / "subagents"


def _plant_subagents(slot) -> dict[str, Path]:
    """A sub-agent in a moving agent worktree, one in the slot itself, and one whose worktree was reaped."""
    repo, wt = slot["repo"], slot["wt"]
    container = repo / ".worktrees" / f"{OLD}-agents"
    leaf = container / "leaf"
    _git("worktree", "add", "-q", str(leaf), "-b", f"wt/{OLD}/leaf", cwd=repo)
    reaped = container / "reaped"
    subagents = _subagents(slot)
    subagents.mkdir(parents=True, exist_ok=True)
    meta = {"agentType": "engineer", "spawnedWithWorktree": True, "description": "leaf"}
    (subagents / "agent-amoved.meta.json").write_text(json.dumps({**meta, "worktreePath": str(leaf)}))
    _jsonl(
        subagents / "agent-amoved.jsonl", [{"type": "user", "cwd": str(leaf)}, {"type": "x", "cwd": str(leaf / "src")}]
    )
    (subagents / "agent-ainherit.meta.json").write_text(json.dumps({**meta, "inheritedWorktreePath": str(leaf)}))
    _jsonl(subagents / "agent-ahome.jsonl", [{"type": "user", "cwd": str(wt)}])
    (subagents / "agent-areaped.meta.json").write_text(json.dumps({**meta, "worktreePath": str(reaped)}))
    _jsonl(subagents / "agent-areaped.jsonl", [{"type": "user", "cwd": str(reaped)}])
    return {"leaf": leaf, "reaped": reaped}


def _new_subagents(slot) -> Path:
    return cc_project_dir(_new_wt(slot), slot["claude"]) / UUID / "subagents"


def _cwds(path: Path) -> list[str]:
    return [json.loads(line)["cwd"] for line in path.read_text().splitlines()]


def test_given_sub_agent_records_of_moved_worktrees_when_migrated_then_each_names_the_moved_path(slot):
    planted = _plant_subagents(slot)
    reaped_meta = (_subagents(slot) / "agent-areaped.meta.json").read_bytes()
    reaped_log = (_subagents(slot) / "agent-areaped.jsonl").read_bytes()

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    moved = slot["repo"] / ".worktrees" / f"{NEW}-agents" / "leaf"
    records = _new_subagents(slot)
    assert json.loads((records / "agent-amoved.meta.json").read_text())["worktreePath"] == str(moved)
    assert json.loads((records / "agent-ainherit.meta.json").read_text())["inheritedWorktreePath"] == str(moved)
    assert _cwds(records / "agent-amoved.jsonl") == [str(moved), str(moved / "src")]
    assert _cwds(records / "agent-ahome.jsonl") == [str(_new_wt(slot))]
    assert (records / "agent-areaped.meta.json").read_bytes() == reaped_meta
    assert (records / "agent-areaped.jsonl").read_bytes() == reaped_log
    assert not planted["leaf"].exists()
    assert "sub-agent record:" in out


def test_given_sub_agent_records_left_by_an_earlier_migration_when_finished_then_they_are_repointed(slot):
    _plant_subagents(slot)
    with patch("ai_cli.session_migrate._plan_subagent_records", return_value=[]):
        code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    records = _new_subagents(slot)
    assert Path(json.loads((records / "agent-amoved.meta.json").read_text())["worktreePath"]).parts[-2:] == (
        f"{OLD}-agents",
        "leaf",
    )

    code, out, err = _status(slot)
    assert code == 1, out + err
    assert "sub-agent record (would do)" in out

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    moved = slot["repo"] / ".worktrees" / f"{NEW}-agents" / "leaf"
    assert json.loads((records / "agent-amoved.meta.json").read_text())["worktreePath"] == str(moved)
    assert _cwds(records / "agent-ahome.jsonl") == [str(_new_wt(slot))]
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert _parts(out)["sub-agent record"] == "done earlier"


# --- AI-CLI-gqpm: Claude Code background-job records ---------------------------------------


def _job(slot, short: str, **fields: object) -> Path:
    record = slot["claude"] / "jobs" / short / "state.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"state": "done", "name": "job", "nameSource": "user", **fields}, indent=2))
    return record


def _plant_jobs(slot) -> dict[str, Path]:
    wt, project = slot["wt"], slot["project"]
    other = "4b4b4b4b-0000-4000-8000-000000000004"
    return {
        "own": _job(
            slot,
            UUID[:8],
            sessionId=UUID,
            resumeSessionId=UUID,
            cwd=str(wt),
            linkScanPath=str(project / f"{UUID}.jsonl"),
            respawnFlags=["--model", "opus"],
        ),
        "by_cwd": _job(
            slot,
            "5c5c5c5c",
            state="running",
            sessionId="5c5c5c5c-0000-4000-8000-000000000005",
            cwd=str(wt / "src"),
            linkScanPath=str(slot["repo"] / "elsewhere.jsonl"),
        ),
        "foreign": _job(
            slot, other[:8], sessionId=other, cwd=str(slot["repo"]), linkScanPath=str(slot["repo"] / "x.jsonl")
        ),
    }


def _fields(record: Path) -> dict:
    return json.loads(record.read_text())


def test_given_job_records_of_the_session_when_migrated_then_cwd_and_link_scan_path_name_the_moved_paths(slot):
    jobs = _plant_jobs(slot)
    foreign = jobs["foreign"].read_bytes()

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    new_wt = _new_wt(slot)
    own = _fields(jobs["own"])
    assert own["cwd"] == str(new_wt)
    assert own["linkScanPath"] == str(cc_project_dir(new_wt, slot["claude"]) / f"{UUID}.jsonl")
    assert own["respawnFlags"] == ["--model", "opus"]
    by_cwd = _fields(jobs["by_cwd"])
    assert (by_cwd["cwd"], by_cwd["linkScanPath"]) == (str(new_wt / "src"), str(slot["repo"] / "elsewhere.jsonl"))
    assert jobs["foreign"].read_bytes() == foreign
    assert f"job record: {jobs['own']}" in out


def test_given_job_records_left_by_an_earlier_migration_when_finished_then_they_are_repointed_and_replan_is_empty(
    slot,
):
    jobs = _plant_jobs(slot)
    with patch("ai_cli.session_migrate._plan_job_records", return_value=[]):
        code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert _fields(jobs["own"])["cwd"] == str(slot["wt"])

    code, out, err = _status(slot)
    assert code == 1, out + err
    assert "job record (would do)" in out

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    new_wt = _new_wt(slot)
    assert _fields(jobs["own"])["cwd"] == str(new_wt)
    assert _fields(jobs["own"])["linkScanPath"] == str(cc_project_dir(new_wt, slot["claude"]) / f"{UUID}.jsonl")
    assert _fields(jobs["by_cwd"])["cwd"] == str(new_wt / "src")
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert _parts(out)["job record"] == "done earlier"


@pytest.mark.parametrize("flags", [("-n",), ("--dry-run",)])
def test_given_job_and_sub_agent_records_when_dry_run_then_each_rewrite_is_planned_and_nothing_changes(slot, flags):
    jobs = _plant_jobs(slot)
    _plant_subagents(slot)
    before = test_session_migrate._everything(slot)

    code, out, err = _migrate(slot["repo"], *flags)

    assert code == 0, out + err
    assert test_session_migrate._everything(slot) == before
    assert f"job record: {jobs['own']} (rewrite: " in out
    assert f"sub-agent record: {_subagents(slot) / 'agent-amoved.meta.json'} (rewrite: " in out
