"""No test may write the operator's real canonical-worktree registry.

AI-CLI-u2ox. Measured 2026-09-27: the real registry held 938 entries, 935 of them pytest
temp paths, leaving 3 real ones. The registry is the authoritative answer to "is this path a
canonical session worktree" -- the question a deletion guard asks before removing one -- so
one that is 99.7% test noise cannot answer it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import (
    _REAL_CANONICAL_WORKTREE_REGISTRY,
    _REAL_HOME,
    canonical_registry_breach,
)

from ai_cli.canonical_worktrees import (
    CanonicalWorktreeRegistryError,
    get_canonical_worktree_registry_path,
    prune_missing_worktrees,
    register_canonical_worktree,
    registered_canonical_worktrees,
)

OVERRIDE = "AI_CLI_CANONICAL_WORKTREE_REGISTRY"


def test_given_an_ordinary_test_when_the_registry_resolves_then_it_is_outside_the_real_home():
    """The property the autouse redirect exists for, asserted without opting into anything."""
    resolved = get_canonical_worktree_registry_path()

    assert resolved != _REAL_CANONICAL_WORKTREE_REGISTRY
    assert _REAL_HOME not in resolved.parents
    assert canonical_registry_breach() == ""


def test_given_a_test_that_registers_a_worktree_when_it_runs_then_the_real_registry_is_untouched(tmp_path):
    """The end-to-end property: a real registration lands in the per-test file, not the live one.

    This is the exact call that produced the 935 stray entries, driven deliberately here.
    """
    before = _REAL_CANONICAL_WORKTREE_REGISTRY.read_bytes() if _REAL_CANONICAL_WORKTREE_REGISTRY.exists() else None
    worktree = tmp_path / "myproject" / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)

    action, registry = register_canonical_worktree(worktree, engine="c", session_name="c-myproject-1")

    assert action == "created"
    assert registry != _REAL_CANONICAL_WORKTREE_REGISTRY
    assert json.loads(registry.read_text(encoding="utf-8"))["worktrees"][0]["path"] == str(worktree.resolve())
    after = _REAL_CANONICAL_WORKTREE_REGISTRY.read_bytes() if _REAL_CANONICAL_WORKTREE_REGISTRY.exists() else None
    assert after == before, "the registration reached the operator's real registry"


def test_given_the_override_points_at_the_real_registry_when_the_guard_checks_then_it_reports_a_breach():
    """Negative control: the guard must FAIL when the redirect is undone.

    Applied with ``patch.dict`` rather than ``monkeypatch`` so the environment is restored
    before this test's teardown runs -- the guard is autouse, and a breach still in force at
    teardown would (correctly) fail this test for causing one.
    """
    with patch.dict(os.environ, {OVERRIDE: str(_REAL_CANONICAL_WORKTREE_REGISTRY)}):
        breach = canonical_registry_breach()

    assert str(_REAL_CANONICAL_WORKTREE_REGISTRY) in breach


def test_given_windows_resolution_when_only_home_is_redirected_then_the_guard_still_reports_a_breach():
    """Why the fix is the registry's own override and not the HOME redirect that precedes it.

    ``get_xdg_data_home`` is platform-branched: on Windows it reads ``LOCALAPPDATA`` and
    ignores ``XDG_DATA_HOME`` entirely, and the HOME redirect does not clear ``LOCALAPPDATA``.
    So with HOME redirected and nothing else, a Windows run still resolves a registry inside
    the operator's own profile -- a different file from the POSIX one and just as live.
    """
    with (
        patch.dict(os.environ, {"LOCALAPPDATA": str(_REAL_HOME / "AppData" / "Local")}),
        patch("sys.platform", "win32"),
    ):
        os.environ.pop(OVERRIDE)

        breach = canonical_registry_breach()

    assert "inside the operator's real home" in breach


def test_given_a_dead_entry_when_a_worktree_is_registered_then_the_dead_entry_is_pruned(tmp_path, monkeypatch):
    """The self-heal: the operator's existing pollution goes away on the next real launch."""
    registry = tmp_path / "registry.json"
    monkeypatch.setenv(OVERRIDE, str(registry))
    live = tmp_path / "live-worktree"
    live.mkdir()
    register_canonical_worktree(live, engine="c", session_name="c-myproject-1")
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["worktrees"].append(
        {
            "path": str(tmp_path / "removed-long-ago"),
            "engine": "c",
            "session_name": "c-myproject-2",
            "first_registered_at": "2026-09-19T22:01:08.782013+00:00",
            "last_validated_at": "2026-09-19T22:01:08.782013+00:00",
        }
    )
    registry.write_text(json.dumps(payload), encoding="utf-8")

    register_canonical_worktree(live, engine="c", session_name="c-myproject-1")

    paths = [entry["path"] for entry in json.loads(registry.read_text(encoding="utf-8"))["worktrees"]]
    assert paths == [str(live.resolve())]


def test_given_a_dead_entry_when_the_deletion_guard_reads_then_it_is_still_reported(tmp_path, monkeypatch):
    """Negative control on the prune's boundary: the READ path must stay fail-closed.

    Pruning on read would turn "this path is not visible right now" -- an unmounted network
    volume, a stale handle -- into "this path is not protected", which is the one direction a
    deletion guard must not fail in. So the read path deliberately reports a missing entry.
    """
    registry = tmp_path / "registry.json"
    monkeypatch.setenv(OVERRIDE, str(registry))
    gone = tmp_path / "unmounted" / "worktree"
    gone.mkdir(parents=True)
    register_canonical_worktree(gone, engine="c", session_name="c-myproject-1")
    gone.rmdir()

    registered, _ = registered_canonical_worktrees()

    assert gone.resolve() in registered


def test_given_a_payload_of_dead_entries_when_pruned_then_it_reports_how_many_it_removed():
    """The count is what made the defect visible, so the helper answers it directly."""
    payload = {
        "version": 1,
        "worktrees": [
            {"path": "/nonexistent/pytest-of-user/pytest-1/popen-gw6/test_a0/worktrees/session-1"},
            {"path": "/nonexistent/pytest-of-user/pytest-1/popen-gw7/test_b0/worktrees/session-1"},
        ],
    }

    assert prune_missing_worktrees(payload) == 2
    assert payload["worktrees"] == []


def test_given_a_registry_path_under_the_real_home_when_checked_then_the_breach_names_it(monkeypatch):
    """The breach message has to name the resolved path, or a reader cannot act on it."""
    with patch.dict(os.environ, {OVERRIDE: str(_REAL_HOME / "stray-registry.json")}):
        breach = canonical_registry_breach()

    assert "stray-registry.json" in breach


def test_given_pathlib_refusing_to_answer_when_the_guard_checks_then_it_reports_no_breach():
    """A test that patched ``os.name`` is still under that patch at teardown (see _pathlib_home).

    Raising from the guard there would turn a passing test into a teardown error, which is how
    the same shape first reached CI for the HOME guard.
    """
    with patch("ai_cli.canonical_worktrees.get_canonical_worktree_registry_path", side_effect=NotImplementedError):
        assert canonical_registry_breach() == ""


def test_given_a_missing_registry_when_the_deletion_guard_reads_then_it_fails_closed():
    """Canary: the per-test redirect must not turn a missing registry into an empty one.

    The redirect points at a file that does not exist yet, and "no registry" must stay
    distinguishable from "no protected worktrees" or every deletion guard reads as permission.
    """
    with pytest.raises(CanonicalWorktreeRegistryError, match="missing"):
        registered_canonical_worktrees()


def test_given_the_redirect_when_a_test_asks_for_the_registry_then_it_is_inside_pytest_tmp(tmp_path_factory):
    """Pins the redirect target's shape: per-test temp, not a shared file somewhere else."""
    resolved = get_canonical_worktree_registry_path()

    assert Path(tmp_path_factory.getbasetemp()) in resolved.parents
