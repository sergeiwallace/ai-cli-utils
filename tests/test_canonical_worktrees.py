"""Regression coverage for durable canonical-worktree registration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_cli.canonical_worktrees import (
    CanonicalWorktreeRegistryError,
    get_canonical_worktree_registry_path,
    register_canonical_worktree,
    registered_canonical_worktrees,
    removal_targets_canonical_worktree,
)


@pytest.fixture
def registry_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "persistent-state" / "canonical-session-worktrees.json"
    monkeypatch.setenv("AI_CLI_CANONICAL_WORKTREE_REGISTRY", str(path))
    return path


def test_given_local_user_default_efs_directory_when_resolving_then_xdg_is_used(tmp_path, monkeypatch):
    """AI-CLI-hgna: a directory NAMED user-default-efs is not evidence of durable storage.

    The SageMaker rationale is real -- there `$HOME` is ephemeral and `$HOME/user-default-efs`
    is the EFS mount that survives a restart -- but the test was for the NAME, so any host
    where that directory happens to exist inherited a SageMaker-shaped path.

    Measured on sem-kg-ec2: /home/ubuntu/user-default-efs exists, created by `credo` and
    holding only `.credo`, and `findmnt -t nfs,nfs4` returns nothing because the host has no
    EFS at all. The registry therefore resolved onto the root volume rather than this
    machine's XDG location -- and the durable volume here is /data, which is what
    ~/.claude, ~/.beads and ~/projects actually symlink onto.

    So the predicate must be the storage property the rationale depends on, not the name.
    """
    monkeypatch.delenv("AI_CLI_CANONICAL_WORKTREE_REGISTRY", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    stray = tmp_path / "user-default-efs"
    (stray / ".credo").mkdir(parents=True)  # exactly the shape credo leaves behind

    resolved = get_canonical_worktree_registry_path()

    assert stray.is_dir(), "the fixture must reproduce the stray directory, or it proves nothing"
    assert stray not in resolved.parents, (
        f"a local directory named user-default-efs is not a durable mount, so the registry "
        f"must not resolve beneath it -- got {resolved}"
    )


def test_given_explicit_override_when_resolving_then_override_still_wins(tmp_path, monkeypatch):
    """Canary: the administrator/test override outranks every root-detection rule."""
    chosen = tmp_path / "elsewhere" / "canonical-session-worktrees.json"
    monkeypatch.setenv("AI_CLI_CANONICAL_WORKTREE_REGISTRY", str(chosen))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "user-default-efs").mkdir()

    assert get_canonical_worktree_registry_path() == chosen


def test_given_new_worktree_when_registered_then_persists_absolute_entry(registry_path, tmp_path):
    worktree = tmp_path / "repo" / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)

    action, path = register_canonical_worktree(worktree, engine="c", session_name="c-myproject-1")

    assert action == "created"
    assert path == registry_path
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["worktrees"][0]["path"] == str(worktree.resolve())
    assert payload["worktrees"][0]["engine"] == "c"


def test_given_registered_worktree_when_registered_again_then_validates_existing_entry(registry_path, tmp_path):
    worktree = tmp_path / "repo" / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)
    register_canonical_worktree(worktree, engine="g", session_name="g-myproject-1")

    action, _ = register_canonical_worktree(worktree, engine="g", session_name="g-myproject-1")

    assert action == "validated-already-tracked"
    assert len(json.loads(registry_path.read_text(encoding="utf-8"))["worktrees"]) == 1


def test_given_registered_worktree_when_launch_metadata_changes_then_updates_entry(registry_path, tmp_path):
    worktree = tmp_path / "repo" / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)
    register_canonical_worktree(worktree, engine="c", session_name="c-myproject-1")

    action, _ = register_canonical_worktree(worktree, engine="cx", session_name="cx-myproject-1")

    assert action == "updated"
    assert json.loads(registry_path.read_text(encoding="utf-8"))["worktrees"][0]["engine"] == "cx"


def test_given_registry_missing_when_guard_reads_then_fails_closed(registry_path):
    with pytest.raises(CanonicalWorktreeRegistryError, match="missing"):
        registered_canonical_worktrees()


def test_given_truncated_registry_when_guard_reads_then_fails_closed(registry_path):
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{", encoding="utf-8")

    with pytest.raises(CanonicalWorktreeRegistryError, match="cannot read"):
        registered_canonical_worktrees()


def test_given_registered_canonical_path_when_parent_targeted_then_reports_protection(registry_path, tmp_path):
    worktree = tmp_path / "repo" / ".worktrees" / "session-1"
    worktree.mkdir(parents=True)
    register_canonical_worktree(worktree, engine="p", session_name="p-myproject-1")

    protected, _ = removal_targets_canonical_worktree(worktree.parent)
    unrelated, _ = removal_targets_canonical_worktree(tmp_path / "repo" / ".worktrees" / "session-1-feature")

    assert protected
    assert not unrelated
