"""An issue update and its audit comment form one resumable migration part."""

from __future__ import annotations

import json
import subprocess

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, _assignees, _migrate

from ai_cli import session_migrate as migration

slot = test_session_migrate.slot
stores = test_session_migrate.stores


@pytest.mark.parametrize("boundary", ["before comment", "after comment"])
def test_given_an_issue_updated_before_comment_completion_when_retried_then_exactly_one_comment_finishes(
    stores, monkeypatch, boundary
):
    real = migration._bd
    stopped = False

    def interrupt(store, *args):
        nonlocal stopped
        if args[:2] == ("comments", "add") and not stopped:
            stopped = True
            if boundary == "before comment":
                return subprocess.CompletedProcess([], 1, "", "temporary comment lock")
            real(store, *args)
            raise KeyboardInterrupt("comment already committed")
        return real(store, *args)

    with monkeypatch.context() as changes:
        changes.setattr(migration, "_bd", interrupt)
        code, out, err = _migrate(stores["repo"], "-y")
        assert code == 1, out + err
    code, out, err = _migrate(stores["repo"], "-y")
    assert code == 0, out + err
    data = json.loads(stores["db"].read_text())
    for issues in data["stores"].values():
        for issue in issues:
            if issue["id"] in {"TST-1", "TST-2", "WS-1"}:
                assert issue["comments"] == [f"Re-keyed from {OLD} to {NEW} by `ai migrate-session`."]
    before = stores["db"].read_text()
    assert _migrate(stores["repo"], "-y")[0] == 0
    assert stores["db"].read_text() == before
    assert _assignees(stores)["TST-5"] == "other-1"
