"""Tests for the advisory roadmap ID allocator (AIH-89 T-01).

Each test builds a real git repo with an ``origin`` remote whose ``main`` carries a
roadmap fixture, so ``next_id`` exercises the actual fetch + ``git show origin/main``
path rather than a mock.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ai_cli.roadmap import NextIdError, _max_id, next_id

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="requires git")

ROADMAP = "docs/roadmap/master-roadmap.md"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_roadmap(repo: Path, body: str) -> None:
    p = repo / ROADMAP
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture()
def repo_with_origin(tmp_path: Path):
    """A clone whose ``origin/main`` holds a roadmap; returns a writer to set its body.

    Returns (clone_path, set_origin_roadmap) where set_origin_roadmap(body) commits
    ``body`` onto the origin's main and leaves the clone's ``origin/main`` fetchable.
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _write_roadmap(work, "# Roadmap\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("clone", "--bare", str(work), str(origin), cwd=tmp_path)

    clone = tmp_path / "clone"
    _git("clone", str(origin), str(clone), cwd=tmp_path)

    def set_origin_roadmap(body: str) -> None:
        _write_roadmap(work, body)
        _git("add", "-A", cwd=work)
        _git("commit", "-m", "update roadmap", cwd=work)
        _git("push", str(origin), "main", "--force", cwd=work)

    return clone, set_origin_roadmap


def test_next_id_returns_max_plus_one(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A**\n- [x] `[P2]` `[AIH-3]` **B**\n")
    assert next_id("AIH", repo_root=clone) == "AIH-6"


def test_next_id_reflects_fetched_origin_not_stale_local(repo_with_origin):
    """F-04: with the local roadmap ahead, the fetched origin/main max wins."""
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A**\n")
    # local worktree jumps ahead, but origin only knows AIH-5 -> AIH-6 (not AIH-10)
    _write_roadmap(clone, "# Roadmap\n- [ ] `[P1]` `[AIH-9]` **local-only**\n")
    assert next_id("AIH", repo_root=clone) == "AIH-6"
    # now origin advances to AIH-7; a fetching call must see it
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-7]` **A**\n")
    assert next_id("AIH", repo_root=clone) == "AIH-8"


def test_per_prefix_isolation(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-9]` **A**\n- [ ] `[P2]` `[SW-3]` **B**\n")
    assert next_id("SW", repo_root=clone) == "SW-4"
    assert next_id("AIH", repo_root=clone) == "AIH-10"


def test_hyphenated_prefix(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AI-CLI-90]` **A**\n")
    assert next_id("AI-CLI", repo_root=clone) == "AI-CLI-91"


def test_prose_mentions_do_not_count(repo_with_origin):
    """A plain `Related: AIH-9` mention must not inflate the max."""
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A** — Related: AIH-9, AIH-42 (prose)\n")
    assert next_id("AIH", repo_root=clone) == "AIH-6"


def test_unknown_prefix_with_no_entries_starts_at_one(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A**\n")
    assert next_id("ZZ", repo_root=clone) == "ZZ-1"


def test_print_only_does_not_modify_roadmap(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A**\n")
    before = (clone / ROADMAP).read_bytes()
    next_id("AIH", repo_root=clone)
    assert (clone / ROADMAP).read_bytes() == before


def test_invalid_prefix_raises(repo_with_origin):
    clone, _ = repo_with_origin
    for bad in ("", "  ", "aih!", "9AB", "-X"):
        with pytest.raises(NextIdError):
            next_id(bad, repo_root=clone, fetch=False)


def test_prefix_is_normalized_to_upper(repo_with_origin):
    clone, set_roadmap = repo_with_origin
    set_roadmap("# Roadmap\n- [ ] `[P1]` `[AIH-5]` **A**\n")
    assert next_id("aih", repo_root=clone) == "AIH-6"


def test_not_a_git_repo_raises(tmp_path: Path):
    with pytest.raises(NextIdError, match="not a git repository"):
        next_id("AIH", repo_root=tmp_path, fetch=False)


def test_roadmap_missing_on_origin_raises(tmp_path: Path):
    """origin/main exists but has no roadmap file -> clear error, no garbage id."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("no roadmap here\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("clone", "--bare", str(work), str(origin), cwd=tmp_path)
    clone = tmp_path / "clone"
    _git("clone", str(origin), str(clone), cwd=tmp_path)
    with pytest.raises(NextIdError, match="cannot read"):
        next_id("AIH", repo_root=clone)


def test_max_id_helper_ignores_plain_mentions():
    text = "- [ ] `[P1]` `[AIH-5]` **A** blah AIH-99 Related: AIH-42\n"
    assert _max_id(text, "AIH") == 5
