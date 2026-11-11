"""Adoption protects the same canonical homes as launching a session."""

import pytest
from test_session_adopt import _add_worktree
from test_session_adopt import adopt as adopt
from test_session_adopt import world as world

from ai_cli import canonical_worktrees, session_adopt
from ai_cli.cc_migrate import transcript_title


def test_given_adoptable_session_when_adopted_then_home_is_registered(world, adopt):
    result = adopt()
    registry = canonical_worktrees.get_canonical_worktree_registry_path()
    entries = canonical_worktrees._load_registry(registry)["worktrees"]
    assert any(row["path"] == str(result.dest_root) and row["session_name"] == "c-myrepo-tst-2" for row in entries)


@pytest.mark.parametrize("dry_run", [False, True])
def test_given_wrong_alias_in_existing_home_when_adopting_then_transcript_is_preserved(world, adopt, dry_run):
    _add_worktree(world["repo"], "wrong-tst-1")
    with pytest.raises(session_adopt.AdoptionError, match="repo alias rule"):
        adopt(on_collision="retitle", new_title="wrong-tst-1", dry_run=dry_run)
    assert transcript_title(world["src_dir"] / "11111111-2222-4333-8444-555555555555.jsonl") == "myrepo-tst-2"


def test_given_adoptable_session_when_previewing_then_no_registry_is_written(world, adopt):
    adopt(dry_run=True)
    assert not canonical_worktrees.get_canonical_worktree_registry_path().exists()


def test_given_registry_failure_when_adopting_then_source_transcript_is_preserved(world, adopt, monkeypatch):
    def refuse(*args, **kwargs):
        raise canonical_worktrees.CanonicalWorktreeRegistryError("registry unavailable")

    monkeypatch.setattr(canonical_worktrees, "register_canonical_worktree", refuse)
    with pytest.raises(session_adopt.AdoptionError, match="registry unavailable"):
        adopt()
    source = world["src_dir"] / "11111111-2222-4333-8444-555555555555.jsonl"
    assert transcript_title(source) == "myrepo-tst-2"
