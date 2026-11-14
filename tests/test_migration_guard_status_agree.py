"""The launch guard of ``ai c NEW`` and ``ai migrate-session --status`` judge a recorded pair alike.

The case: a chain A -> B finished and verified, B's slot then moved on to C, and B -> C left
partial. ``--status`` must list B -> C as partial and A -> B as complete with its slot moved on,
and a launch of C must refuse on the same B -> C pair. Each test records real migrations on the
fixture slot and drives both entry points through the CLI.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from unittest.mock import patch

import test_session_migrate
from test_session_migrate import NEW, OLD, _migrate, run_cli
from test_session_migrate_finished_rule import _stuck_hook

slot = test_session_migrate.slot

THIRD = "myrepo-box-4"


def _rows(out: str) -> list[list[str]]:
    lines = [line.partition("migrate: ")[2] for line in out.splitlines()]
    return [
        [cell.strip() for cell in line.split(" | ")] for line in lines if " | " in line and not line.startswith("old |")
    ]


def _status(slot, *argv: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        return run_cli(["ai", "migrate-session", "--status", *argv])


def _launch_third(slot) -> tuple[int, str, str, list[str]]:
    """``ai c 4`` resolving to THIRD; the session start is stubbed and ends the run."""
    started: list[str] = []

    def _start(ai_name, **_kwargs):
        started.append(ai_name)
        raise SystemExit(0)

    with ExitStack() as stack:
        for target, kwargs in (
            ("ai_cli.session.create_worktree", {"side_effect": _start}),
            ("ai_cli.session.build_session_name", {"return_value": (f"c-{THIRD}", THIRD)}),
            ("ai_cli.session.detect_repo_root", {"return_value": str(slot["repo"])}),
            ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
            ("ai_cli.session._resolve_is_remote", {"return_value": False}),
            ("ai_cli.session._tmux_session_names", {"return_value": []}),
            ("ai_cli.config._get_projects_dir", {"return_value": slot["repo"].parent}),
            ("ai_cli.trust.ensure_workspace_trusted", {}),
            ("ai_cli.main._ensure_dolt_server", {}),
            ("ai_cli.tunnel._ensure_nats_tunnel", {}),
        ):
            stack.enter_context(patch(target, **kwargs))
        stack.enter_context(patch.dict(os.environ))
        os.environ.pop("TMUX", None)
        code, out, err = run_cli(["ai", "c", "4", "-b", "-D", "--project-prefix", "myrepo"])
    return code, out, err, started


def _chain_with_second_hop_partial(slot, tmp_path) -> None:
    """A -> B verified, B -> C migrated, then B -> C left partial by a hook that never reads done."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        code, out, err = run_cli(["ai", "migrate-session", NEW, THIRD, "-r", str(slot["repo"]), "-y"])
    assert code == 0, out + err
    ledger = slot["state"] / "session-migrations.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    kept = [row for row in rows if not ((row.get("old"), row.get("new")) == (NEW, THIRD) and row.get("finished"))]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in kept), encoding="utf-8")
    _stuck_hook(tmp_path)


def test_given_a_chain_whose_second_hop_is_partial_when_status_and_launch_judge_it_then_both_call_it_partial(
    slot, tmp_path
):
    _chain_with_second_hop_partial(slot, tmp_path)

    status_code, status_out, status_err = _status(slot, "-a")
    launch_code, launch_out, launch_err, started = _launch_third(slot)

    rows = {(row[0], row[1]): row for row in _rows(status_out + status_err)}
    assert rows[(NEW, THIRD)][3] == "partial", status_out + status_err
    assert "hook board" in rows[(NEW, THIRD)][4]
    assert rows[(OLD, NEW)][3] == "complete"
    assert f"slot moved on to {THIRD}" in rows[(OLD, NEW)][4]
    assert status_code == 1

    assert launch_code == 1, launch_out + launch_err
    assert started == []
    assert f"the migration {NEW} -> {THIRD} is partial" in launch_err
    assert "hook board" in launch_err


def test_given_a_chain_whose_second_hop_is_partial_when_status_runs_without_all_then_it_still_lists_that_hop(
    slot, tmp_path
):
    """The pair the launch guard refuses on is the one ``--status`` shows by default."""
    _chain_with_second_hop_partial(slot, tmp_path)

    code, out, err = _status(slot)

    rows = {(row[0], row[1]): row[3] for row in _rows(out + err)}
    assert rows.get((NEW, THIRD)) == "partial", out + err
    assert code == 1
