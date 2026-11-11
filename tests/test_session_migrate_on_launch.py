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
from types import SimpleNamespace
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

FINISH = f"ai migrate-session {OLD} {NEW} --finish -r"


def _ledger(slot) -> list[dict]:
    path = slot["state"] / "session-migrations.jsonl"
    return [
        row
        for line in path.read_text(encoding="utf-8").splitlines()
        if not (row := json.loads(line)).get("context_announced")
    ]


def _launch(slot, tmux: list[str] | None = None, *flags: str) -> tuple[int, str, str, list[dict]]:
    """``ai c 3 [flags]`` resolving to NEW; returns the exit code, output and what each launch saw."""
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
        code, out, err = test_session_migrate.run_cli(
            ["ai", "c", "3", "-b", "-D", "--project-prefix", "myrepo", *flags]
        )
    return code, out, err, launched


def _replay(slot) -> list[dict]:
    """A recorded ``c-NEW`` recipe replayed as ``ai sessions relaunch`` does; returns what each launch saw.

    The launch is stopped at the migration-context queue, the next step after the finish.
    """
    from ai_cli import session_commands

    launched: list[dict] = []
    logs = slot["state"] / "launch-logs"

    def _start(ai_name, *_args, **_kwargs):
        launched.append(
            {
                "ai_name": ai_name,
                "old_logs": sorted(p.name for p in logs.glob(f"{OLD}-*")) if logs.is_dir() else [],
                "ledger": _ledger(slot),
            }
        )
        raise SystemExit(0)

    name = f"c-{NEW}"
    record = {
        "name": name,
        "boot_recipe": {
            "session_name": name,
            "cwd": str(slot["repo"] / ".worktrees" / NEW),
            "project": "myrepo",
            "engine": "c",
            "options": {"no_direnv": True},
            "extra_args": [],
            "shell": "sh",
            "argv": ["ai", "c", name, "--project-prefix", "myrepo"],
        },
    }
    with ExitStack() as stack:
        patches: tuple[tuple[str, dict], ...] = (
            ("ai_cli.main._queue_migration_context", {"side_effect": _start}),
            ("ai_cli.session._tmux_session_names", {"return_value": []}),
            ("ai_cli.tmux_setup.tmux_runs", {"return_value": True}),
            ("ai_cli.tmux_setup.formats_expand", {"return_value": True}),
            ("ai_cli.tmux_setup.probe", {"return_value": SimpleNamespace(versions_disagree=False)}),
            ("ai_cli.tmux_setup.report_lines", {"return_value": []}),
            ("ai_cli.config._get_projects_dir", {"return_value": slot["repo"].parent}),
            ("ai_cli.main._ensure_dolt_server", {}),
            ("ai_cli.tunnel._ensure_nats_tunnel", {}),
        )
        for target, kwargs in patches:
            stack.enter_context(patch(target, **kwargs))
        with pytest.raises(SystemExit) as stopped:
            session_commands._launch_recipe(record, cause="manual")
    if not launched:
        raise stopped.value  # refused before the launch reached the stub
    assert stopped.value.code == 0
    return launched


@pytest.fixture
def migrated(slot):
    """A slot migrated OLD -> NEW and recorded unfinished, then given leftovers only ``--finish`` carries.

    The migration's own verified ``finished`` row is dropped, leaving the ledger a build from
    before the re-plan rule wrote: the migration row alone.
    """
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    ledger = slot["state"] / "session-migrations.jsonl"
    rows = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    ledger.write_text("".join(row for row in rows if not json.loads(row).get("finished")), encoding="utf-8")
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


def test_given_the_finish_is_recorded_when_new_is_launched_again_then_it_is_re_planned_read_only_and_launches(
    migrated, hooks
):
    hooks("10-notes", "notes")
    code, out, err, _launched = _launch(migrated)
    assert code == 0, out + err
    runs = len(hooks.calls())
    rows = _ledger(migrated)

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1
    again = hooks.calls()[runs:]
    assert [c["args"][-1] for c in again] == ["--dry-run"], "one read-only re-plan, no live hook run"
    assert _ledger(migrated) == rows
    assert "refusing" not in err


def test_given_a_recorded_finish_dirtied_later_when_new_is_launched_then_nothing_starts(migrated):
    code, out, err, _launched = _launch(migrated)
    assert code == 0, out + err
    rows = _ledger(migrated)
    assert rows[-1].get("verified") is True
    leftover = _seed_logs_and_evidence(migrated)["bare"]

    code, out, err, launched = _launch(migrated)

    assert code == 1, out + err
    assert launched == [], "a verified pair that re-plans partial still refuses the launch"
    assert leftover.is_file(), "the re-plan writes nothing"
    assert _ledger(migrated) == rows
    assert f"ai migrate-session {OLD} {NEW} --finish -r {migrated['repo']}" in err


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


def _finish_raises(_slot, _hooks):
    return patch("ai_cli.session_migrate.finish_session", side_effect=RuntimeError("example finish failure"))


def _refusal(err: str) -> str:
    lines = [line for line in err.splitlines() if f"refusing to launch {NEW}" in line]
    assert len(lines) == 1, err
    return lines[0]


@pytest.mark.parametrize("failure", [_unreadable_chief, _hook_fails_live, _hook_refuses_plan])
def test_given_the_finish_leaves_a_part_when_new_is_launched_then_nothing_starts_and_the_finish_command_prints(
    migrated, hooks, failure
):
    failure(migrated, hooks)
    rows = _ledger(migrated)

    code, out, err, launched = _launch(migrated)

    assert code == 1, out + err
    assert launched == [], "a partial migration starts nothing"
    assert _ledger(migrated) == rows, "no finished row"
    refusal = _refusal(err)
    assert f"the migration {OLD} -> {NEW} is partial; parts left: " in refusal
    assert f"`{FINISH} {migrated['repo']}`" in refusal
    assert "allow-partial" not in refusal, "the refusal names the finish, not the override"


@pytest.mark.parametrize("failure", [_old_still_running, _finish_raises])
def test_given_the_launch_time_finish_raises_when_new_is_launched_then_the_launch_is_refused_not_continued(
    migrated, hooks, failure
):
    raising = failure(migrated, hooks)
    rows = _ledger(migrated)

    with raising or ExitStack():
        code, out, err, launched = _launch(migrated)

    assert code == 1, out + err
    assert launched == []
    assert _ledger(migrated) == rows
    assert "parts left: migration (failed: " in _refusal(err)
    assert f"`{FINISH} {migrated['repo']}`" in _refusal(err)


@pytest.mark.parametrize("flag", ["-P", "--allow-partial-migration"])
def test_given_the_override_when_a_partial_new_is_launched_then_it_starts_with_one_warning_logged(
    migrated, hooks, flag
):
    hooks("10-notes", "notes", fail="live")
    logs = migrated["state"] / "launch-logs"
    before = set(logs.glob("3-*.log"))

    code, out, err, launched = _launch(migrated, None, flag)

    assert code == 0, out + err
    assert len(launched) == 1 and launched[0]["ai_name"] == NEW
    warnings = [line for line in err.splitlines() if f"with the migration {OLD} -> {NEW} partial" in line]
    assert len(warnings) == 1, err
    assert "parts left: hook notes (failed:" in warnings[0]
    assert "refusing" not in err
    (log,) = set(logs.glob("3-*.log")) - before
    assert "parts left: hook notes (failed:" in log.read_text(encoding="utf-8")
    assert not [r for r in _ledger(migrated) if r.get("finished")], "the pair stays partial on the ledger"


@pytest.mark.parametrize("flags", [(), ("-P",)])
def test_given_a_ledger_row_in_a_newer_format_when_new_is_launched_then_it_refuses_with_the_update_command(
    migrated, flags
):
    from ai_cli.session_migrate import MIGRATION_FORMAT

    ledger = migrated["state"] / "session-migrations.jsonl"
    newer = {"old": OLD, "new": NEW, "repo": str(migrated["repo"]), "format": MIGRATION_FORMAT + 1}
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps(newer) + "\n", encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")

    code, out, err, launched = _launch(migrated, None, *flags)

    assert code == 1, out + err
    assert launched == []
    assert ledger.read_text(encoding="utf-8") == before
    refusal = _refusal(err)
    assert f"format {MIGRATION_FORMAT + 1}" in refusal
    assert "`ai update --force`" in refusal


def test_given_a_hook_when_new_is_launched_then_the_finish_runs_it_like_finish_does(migrated, hooks):
    hooks("10-notes", "notes")
    hooks("20-board", "board")

    code, out, err, _launched = _launch(migrated)

    assert code == 0, out + err
    calls = hooks.calls()
    planned = [c["hook"] for c in calls if "--dry-run" in c["args"]]
    live = [c for c in calls if "--dry-run" not in c["args"]]
    assert planned == ["notes", "board", "notes", "board"], "the plan, then the re-plan that verifies it"
    assert [(c["hook"], c["args"]) for c in live] == [
        ("notes", ["--old", OLD, "--new", NEW]),
        ("board", ["--old", OLD, "--new", NEW]),
    ]
    assert all(NEW in c["slots"] for c in live)


def test_given_a_manual_finish_succeeds_when_new_is_launched_then_it_is_not_run_again(migrated, hooks):
    code, out, err = _finish(migrated["repo"], "-y")
    assert code == 0, out + err
    rows = _ledger(migrated)
    assert [r for r in rows if r.get("finished")]

    code, out, err, launched = _launch(migrated)

    assert code == 0, out + err
    assert len(launched) == 1
    assert _ledger(migrated) == rows
    assert "finished the migration" not in err


def test_given_a_verified_finish_and_a_hook_added_later_when_new_is_launched_then_it_is_refused_unrun(migrated, hooks):
    code, out, err = _finish(migrated["repo"], "-y")
    assert code == 0, out + err
    hooks("10-notes", "notes")

    code, out, err, launched = _launch(migrated)

    assert code == 1, out + err
    assert launched == []
    assert all("--dry-run" in c["args"] for c in hooks.calls()), "the re-plan never runs the hook live"
    assert "parts left: hook notes (would do)" in _refusal(err)


def test_given_a_recorded_unfinished_migration_when_new_is_relaunched_from_its_recipe_then_it_is_finished_first(
    migrated, capfd
):
    launched = _replay(migrated)

    err = capfd.readouterr().err
    assert len(launched) == 1 and launched[0]["ai_name"] == NEW, err
    assert launched[0]["old_logs"] == [], "the finish ran before the relaunch"
    _assert_logs_and_evidence_follow_new(migrated, migrated["seeded"])
    finished = [r for r in launched[0]["ledger"] if r.get("finished")]
    assert [(r["old"], r["new"], r["repo"]) for r in finished] == [(OLD, NEW, str(migrated["repo"]))]


def test_given_the_finish_refuses_when_new_is_relaunched_from_its_recipe_then_it_is_refused(migrated, hooks, capfd):
    from ai_cli.main import PartialMigrationRefusal

    hooks("10-notes", "notes", fail="plan")
    rows = _ledger(migrated)

    with pytest.raises(PartialMigrationRefusal) as refused:
        _replay(migrated)

    err = capfd.readouterr().err
    assert refused.value.code == 1
    assert f"`{FINISH} {migrated['repo']}`" in refused.value.reason
    assert _ledger(migrated) == rows
    assert f"`{FINISH} {migrated['repo']}`" in _refusal(err)


def test_given_the_launch_help_when_printed_then_it_names_the_finish_the_refusal_and_the_override():
    code, out, _err = _run_cli(["ai", "c", "--help"])
    assert code == 0
    text = " ".join(out.split())
    assert "migrate-session OLD NEW --finish -y" in text
    assert "the launch is refused" in text
    assert "-P, --allow-partial-migration" in text
