"""Rewriting a stale link never loses a link: not a neighbour's, not the original, not its type.

The three cases come from review findings on the link rewrite: a fixed temporary name removed an
unrelated link that happened to carry it, a replace that kept failing removed the original link
as well as its replacement, and a directory link whose new target did not exist yet (its
worktree moves later in the run) was recreated as a file link, which Windows treats differently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_cli import session_migrate_links as links


def _symlinks_available(tmp_path: Path) -> bool:
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def _target(link: Path) -> Path:
    """What ``link`` names, as it was written.

    On Windows ``readlink`` returns the stored substitute path, so an absolute target reads back
    with a ``\\\\?\\`` prefix (``os.readlink``, changed in Python 3.8); the link still names the
    same path, and the code under test strips that prefix the same way before comparing.
    """
    return Path(str(link.readlink()).removeprefix("\\\\?\\"))


@pytest.fixture
def tree(tmp_path):
    if not _symlinks_available(tmp_path):
        pytest.skip("this platform cannot create symlinks")
    old = tmp_path / "old" / "data"
    new = tmp_path / "new" / "data"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    link = work / "link"
    link.symlink_to(old, target_is_directory=True)
    return {"tmp": tmp_path, "old": old, "new": new, "work": work, "link": link}


def test_given_an_unrelated_link_named_like_the_temporary_when_rewritten_then_that_link_is_untouched(tree):
    neighbour = tree["work"] / ".link.relink"
    unrelated = tree["tmp"] / "unrelated"
    unrelated.mkdir()
    neighbour.symlink_to(unrelated, target_is_directory=True)

    warnings = links.apply([links.StaleLink(tree["link"], str(tree["old"]), str(tree["new"]))])

    assert warnings == []
    assert _target(tree["link"]) == tree["new"]
    assert neighbour.is_symlink()
    assert _target(neighbour) == unrelated


def test_given_a_replace_that_keeps_failing_when_rewritten_then_the_original_link_survives(tree, monkeypatch):
    def refuse(self, target):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "replace", refuse)

    warnings = links.apply([links.StaleLink(tree["link"], str(tree["old"]), str(tree["new"]))])

    assert len(warnings) == 1
    assert "FAILED" in warnings[0]
    assert tree["link"].is_symlink()
    assert _target(tree["link"]) == tree["old"]
    leftovers = sorted(p.name for p in tree["work"].iterdir() if p != tree["link"])
    assert leftovers == []


def test_given_a_directory_link_whose_new_target_is_not_there_yet_when_rewritten_then_it_stays_a_directory_link(
    tree, monkeypatch
):
    tree["new"].rmdir()  # the worktree it will name moves into place later in the run
    created: list[bool] = []
    real = Path.symlink_to

    def record(self, target, target_is_directory=False):
        created.append(target_is_directory)
        return real(self, target, target_is_directory=target_is_directory)

    found = links.scan([(tree["work"], tree["work"])], [(tree["old"].parent, tree["new"].parent)])
    monkeypatch.setattr(Path, "symlink_to", record)

    warnings = links.apply(found)

    assert warnings == []
    assert len(found) == 1
    assert created == [True]
    assert _target(tree["link"]) == tree["new"]


def test_given_a_rewrite_with_no_link_type_in_its_plan_when_applied_then_the_new_link_keeps_the_old_links_type(
    tree, monkeypatch
):
    """The type comes from the link being replaced: Windows refuses to rename a file link over a
    directory link, so a type taken from anywhere else could leave the old link in place."""
    created: list[bool] = []
    real = Path.symlink_to

    def record(self, target, target_is_directory=False):
        created.append(target_is_directory)
        return real(self, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", record)

    warnings = links.apply([links.StaleLink(tree["link"], str(tree["old"]), str(tree["new"]))])

    assert warnings == []
    assert created == [True]
    assert _target(tree["link"]) == tree["new"]
