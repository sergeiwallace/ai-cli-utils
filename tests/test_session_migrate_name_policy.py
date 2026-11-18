"""Named migration previews check the current registry's name policy."""

from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
import test_session_migrate
from test_session_migrate import OLD, _everything, run_cli

from ai_cli import name_registry as reg

slot = test_session_migrate.slot


@pytest.fixture
def name_registry(monkeypatch):
    current = reg.Snapshot(
        tip="current",
        names=["cleo"],
        generation={
            "policy": {
                "schema": 1,
                "limits": {"min_name_length": 3, "max_name_length": 4},
                "confusable": {"max_edit_distance": 1},
            }
        },
        rows={"clea": {"title": "myapp-box-clea", "machine": "other-host"}},
    )
    cached = replace(current, tip="cached", rows={}, fetched=False)
    registry = Mock(spec=reg.Registry)
    registry.snapshot.side_effect = lambda *, fetch=True: current if fetch else cached
    monkeypatch.setattr("ai_cli.main._names_registry_or_exit", lambda **kwargs: registry)
    monkeypatch.setattr("ai_cli.main._migration_stem", lambda repo: "myrepo-box")
    monkeypatch.setattr("ai_cli.session_naming.host_id", lambda: "test-host")
    monkeypatch.setattr(reg, "common_words", lambda policy: frozenset())
    return registry, current, cached


@pytest.mark.parametrize("flag", ["-n", "--dry-run", "-y", "--yes"])
@pytest.mark.parametrize("stale", [False, True], ids=["cached-hold", "uncached-hold"])
def test_given_a_confusable_held_name_when_migration_is_preflighted_then_preview_and_apply_refuse_without_writes(
    slot, name_registry, flag, stale
):
    registry, current, cached = name_registry
    if not stale:
        cached.rows = current.rows.copy()
    before = _everything(slot)
    snapshots = repr((current, cached))

    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        code, out, err = run_cli(["ai", "migrate-session", OLD, "-t", "cleo", "-r", str(slot["repo"]), flag])

    assert code == 2, out + err
    assert "cleo is one edit from the held name clea (myapp-box-clea@other-host)" in out + err
    assert "skipped: preflight refused" in out
    assert "| would do" not in out
    assert _everything(slot) == before
    assert repr((current, cached)) == snapshots
    registry.transact.assert_not_called()
