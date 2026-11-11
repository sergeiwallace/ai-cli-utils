"""Read-only hook plans expose completion and checks that can expire before apply."""

from __future__ import annotations

import pytest
import test_session_migrate
import test_session_migrate_hooks
from test_session_migrate import _everything, _migrate

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks


def test_given_an_agent_mail_preflight_when_snapshot_age_can_change_then_preview_names_that_warning(slot, hooks):
    hooks("10-mail", "agent-mail")
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-n")
    assert code == 0, out + err
    assert "agent-mail snapshot warning" in out
    assert "snapshot freshness can expire before apply" in out
    assert _everything(slot) == before


@pytest.mark.parametrize("statuses", [["unknown"], ["done", "done"]])
def test_given_invalid_hook_status_when_planned_then_refused_before_any_moves(slot, hooks, statuses):
    hook = hooks("10-notes", "notes")
    hook.write_text(hook.read_text() + "\n".join(f"print('migrate-status: {value}')" for value in statuses))
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-n")
    assert code == 2, out + err
    assert "invalid migrate-status" in out + err
    assert _everything(slot) == before
