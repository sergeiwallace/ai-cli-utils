"""``ai c NEW`` finishes a migration this host recorded for NEW before the session starts.

Each test migrates the fixture slot for real (which writes the ledger row), seeds an
OLD-keyed artifact that only ``--finish`` carries, then drives ``ai c`` through the CLI
with the session launch itself stubbed at worktree creation: the stub records what the
launch would have found and ends the run.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import pytest
import test_session_migrate
import test_session_migrate_hooks
from conftest import run_cli as _run_cli
from test_session_migrate import (
    NEW,
    OLD,
    UUID,
    _assert_logs_and_evidence_follow_new,
    _finish,
    _migrate,
    _seed_chief,
    _seed_logs_and_evidence,
)

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks

REMEDY = f"ai session-migrate {OLD} {NEW} -r"


def _ledger(slot) -> list[dict]:
    path = slot["state"] / "session-migrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _launch(slot, tmux: list[str] | None = None) -> tuple[int, str, str, list[dict]]:
    """``ai c 3`` resolving to NEW; returns the exit code, output and what each launch saw."""
    launched: list[dict] = []
    logs = slot["state"] / "launch-logs"

    def _start(ai_name, **_kwargs):
        launched.append(
            {
                "ai_name": ai_name,
                "old_logs": sorted(p.name for p in logs.glob(f"{OLD}-*")) if logs.is_dir() else [],
                "ledger": _ledger(slot),
            }
        )
        raise SystemExit(0)

    with ExitStack() as stack:
        patches: tuple[tuple[str, dict], ...] = (
            ("ai_cli.session.create_worktree", {"side_effect": _start}),
            ("ai_cli.session.build_session_name", {"return_value": (f"c-{NEW}", NEW)}),
            ("ai_cli.session.detect_repo_root", {"return_value": str(slot["repo"])}),
            ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
            ("ai_cli.session._resolve_is_remote", {"return_value": False}),
            ("ai_cli.session._tmux_session_names", {"return_value": list(tmux or [])}),
            ("ai_cli.config._get_projects_dir", {"return_value": slot["repo"].parent}),
            ("ai_cli.trust.ensure_workspace_trusted", {}),
            ("ai_cli.main._ensure_dolt_server", {}),
            ("ai_cli.tunnel._ensure_nats_tunnel", {}),
        )
        for target, kwargs in patches:
            stack.enter_context(patch(target, **kwargs))
        code, out, err = test_session_migrate.run_cli(["ai", "c", "3", "-b", "-D", "--project-prefix", "myrepo"])
    return code, out, err, launched


@pytest.fixture
def migrated(slot):
    """A slot migrated OLD -> NEW, then given leftovers only ``--finish`` carries."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert [(r["old"], r["new"]) for r in _ledger(slot)] == [(OLD, NEW)]
    slot["seeded"] = _seed_logs_and_evidence(slot)
    return slot


def test_given_a_recorded_unfinished_migration_when_new_is_launched_then_it_is_finished_first(migrated):
    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1 and launched[0]["ai_name"] == NEW
    assert launched[0]["old_logs"] == [], "the finish ran before the launch"
    _assert_logs_and_evidence_follow_new(migrated, migrated["seeded"])
    finished = [r for r in launched[0]["ledger"] if r.get("finished")]
    assert [(r["old"], r["new"], r["repo"]) for r in finished] == [(OLD, NEW, str(migrated["repo"]))]
    assert f"finished the migration {OLD} -> {NEW}" in err


def test_given_the_finish_is_recorded_when_new_is_launched_again_then_it_does_not_run_again(migrated, hooks):
    hooks("10-notes", "notes")
    code, out, err, _launched = _launch(migrated)
    assert code == 0, out + err
    runs = len(hooks.calls())
    assert runs
    rows = _ledger(migrated)
    leftover = migrated["state"] / "launch-logs" / f"{OLD}-again.log"
    leftover.write_text("x\n")

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1
    assert len(hooks.calls()) == runs
    assert leftover.is_file()
    assert _ledger(migrated) == rows
    assert "migration" not in err


def test_given_no_ledger_row_names_new_when_launched_then_nothing_is_finished_or_printed(migrated):
    ledger = migrated["state"] / "session-migrations.jsonl"
    ledger.write_text(json.dumps({"old": OLD, "new": f"{NEW}9", "repo": str(migrated["repo"])}) + "\n")
    before = ledger.read_text(encoding="utf-8")

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1
    assert launched[0]["old_logs"], "the OLD-keyed launch logs stay where they were"
    assert ledger.read_text(encoding="utf-8") == before
    assert "migrat" not in out + err


def _old_still_running(slot, _hooks) -> None:
    sessions = slot["claude"] / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": OLD, "cwd": str(slot["wt"]), "sessionId": UUID})
    )


def _unreadable_chief(_slot, _hooks) -> None:
    _seed_chief()["registration"].write_text("{not json")


def _hook_fails_live(_slot, hooks) -> None:
    hooks("10-notes", "notes", fail="live")


def _hook_refuses_plan(_slot, hooks) -> None:
    hooks("10-notes", "notes", fail="plan")


@pytest.mark.parametrize("failure", [_old_still_running, _unreadable_chief, _hook_fails_live, _hook_refuses_plan])
def test_given_the_finish_fails_when_new_is_launched_then_it_says_so_records_nothing_and_still_launches(
    migrated, hooks, failure
):
    failure(migrated, hooks)
    rows = _ledger(migrated)

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1 and launched[0]["ai_name"] == NEW, "a failed finish never blocks the launch"
    assert _ledger(migrated) == rows
    notice = [line for line in err.splitlines() if f"{OLD} -> {NEW}" in line]
    assert len(notice) == 1, err
    assert f"{REMEDY} {migrated['repo']} --finish -y" in notice[0]


def test_given_a_hook_when_new_is_launched_then_the_finish_runs_it_like_finish_does(migrated, hooks):
    hooks("10-notes", "notes")
    hooks("20-board", "board")

    code, out, err, _launched = _launch(migrated)

    assert code == 0, out + err
    calls = hooks.calls()
    planned = [c["hook"] for c in calls if "--dry-run" in c["args"]]
    live = [c for c in calls if "--dry-run" not in c["args"]]
    assert planned == ["notes", "board"]
    assert [(c["hook"], c["args"]) for c in live] == [
        ("notes", ["--old", OLD, "--new", NEW]),
        ("board", ["--old", OLD, "--new", NEW]),
    ]
    assert all(NEW in c["slots"] for c in live)


def test_given_a_manual_finish_succeeds_when_new_is_launched_then_it_is_not_run_again(migrated, hooks):
    code, out, err = _finish(migrated["repo"], "-y")
    assert code == 0, out + err
    assert [r for r in _ledger(migrated) if r.get("finished")]
    hooks("10-notes", "notes")

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1
    assert hooks.calls() == []


def test_given_the_launch_help_when_printed_then_it_names_the_pending_finish():
    code, out, _err = _run_cli(["ai", "c", "--help"])
    assert code == 0
    assert "session-migrate OLD NEW --finish -y" in " ".join(out.split())
