"""Fresh interactive launches migrate legacy slots and announce the rename once."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD

from ai_cli.session_migrate import MIGRATION_LEDGER

slot = test_session_migrate.slot


def _launch(
    slot, name: str = "3", *, config: dict | None = None, real_aliases: bool = False
) -> tuple[int, str, str, list[dict]]:
    """Drive the real local ``ai c`` naming and migration path up to worktree reuse."""
    launched: list[dict] = []

    def _start(ai_name, **_kwargs):
        prompt = slot["state"] / f"cc-resume-prompt-c-{ai_name}"
        launched.append(
            {
                "ai_name": ai_name,
                "prompt": prompt.read_text(encoding="utf-8") if prompt.is_file() else "",
                "ledger": [
                    json.loads(line)
                    for line in (slot["state"] / MIGRATION_LEDGER).read_text(encoding="utf-8").splitlines()
                ]
                if (slot["state"] / MIGRATION_LEDGER).is_file()
                else [],
            }
        )
        raise SystemExit(0)

    with ExitStack() as stack:
        patches: tuple[tuple[str, dict], ...] = (
            ("ai_cli.session.create_worktree", {"side_effect": _start}),
            ("ai_cli.session.detect_repo_root", {"return_value": Path(slot["repo"])}),
            ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
            ("ai_cli.session._resolve_is_remote", {"return_value": False}),
            ("ai_cli.session._tmux_session_names", {"return_value": []}),
            ("ai_cli.session_naming.resolve_host_alias", {"return_value": "box"}),
            ("ai_cli.config._get_projects_dir", {"return_value": slot["repo"].parent}),
            ("ai_cli.trust.ensure_workspace_trusted", {}),
            ("ai_cli.main._ensure_dolt_server", {}),
            ("ai_cli.tunnel._ensure_nats_tunnel", {}),
        )
        for target, kwargs in patches:
            if real_aliases and target == "ai_cli.session_naming.resolve_host_alias":
                continue
            stack.enter_context(patch(target, **kwargs))
        code, out, err = test_session_migrate.run_cli(
            ["ai", "c", name, "-b", "-D", "--project-prefix", "myrepo"], config=config
        )
    return code, out, err, launched


@pytest.mark.usefixtures("interactive_terminal")
def test_given_a_stopped_legacy_slot_when_launched_interactively_then_it_migrates_and_uses_the_alias_name(slot):
    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [NEW]
    assert not slot["wt"].exists()
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()
    assert f"auto-migrated {OLD} -> {NEW}" in err


@pytest.mark.usefixtures("interactive_terminal")
def test_given_a_migration_when_the_new_session_launches_twice_then_the_change_context_is_queued_once(slot):
    code, out, err = test_session_migrate._migrate(slot["repo"], "-y")
    assert code == 0, out + err

    first = _launch(slot, NEW)
    second = _launch(slot, NEW)

    assert first[0] == second[0] == 0
    prompt = first[3][0]["prompt"]
    assert f"Your session name changed from {OLD} to {NEW}." in prompt
    assert f"Your worktree moved from {slot['wt']} to {slot['repo'] / '.worktrees' / NEW}." in prompt
    assert f"Your branch changed from wt-{OLD} to wt-{NEW}." in prompt
    assert f"Your task namespace changed from {OLD} to {NEW}." in prompt
    assert second[3][0]["prompt"] == prompt
    assert sum(row.get("context_announced") is True for row in second[3][0]["ledger"]) == 1
