"""Changelog fragments: one file per change under ``changelog.d/``, folded into CHANGELOG.md at release.

Each test drives a real git repository and the real ``scripts/fold_changelog.py`` as a release runs
it. The point of fragments is that two branches adding one each merge without a conflict, where two
branches editing the top of the same CHANGELOG.md section do not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLD = REPO_ROOT / "scripts" / "fold_changelog.py"

HEADER = """# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

"""

CHANGELOG = (
    HEADER
    + """### Added

- An existing feature.

### Fixed

- An existing fix, whose text
  wraps onto a second line.

## [1.0.0] - 2026-01-01

### Fixed

- A released fix.
"""
)


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env


def _git(repo: Path, *args: str, date: str | None = None) -> subprocess.CompletedProcess[str]:
    env = _env()
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)


def _commit(repo: Path, files: dict[str, str], message: str, date: str | None = None) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert _git(repo, "add", "-A").returncode == 0
    done = _git(repo, "commit", "-q", "-m", message, date=date)
    assert done.returncode == 0, done.stderr


def _fold(repo: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FOLD), "-r", str(repo), *flags], capture_output=True, text=True, env=_env(), check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _commit(root, {"CHANGELOG.md": CHANGELOG, "changelog.d/.gitkeep": ""}, "chore: seed", "2026-01-01T00:00:00+00:00")
    return root


def _branch(repo: Path, name: str, files: dict[str, str]) -> None:
    assert _git(repo, "checkout", "-q", "-b", name, "main").returncode == 0
    _commit(repo, files, f"fix: {name}")
    assert _git(repo, "checkout", "-q", "main").returncode == 0


def test_given_two_branches_each_adding_a_fragment_when_both_merge_then_neither_conflicts(repo: Path) -> None:
    _branch(repo, "one", {"changelog.d/one.fixed.md": "- The first fix.\n"})
    _branch(repo, "two", {"changelog.d/two.fixed.md": "- The second fix.\n"})

    first = _git(repo, "merge", "--no-edit", "one")
    second = _git(repo, "merge", "--no-edit", "two")

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert sorted(p.name for p in (repo / "changelog.d").glob("*.md")) == ["one.fixed.md", "two.fixed.md"]


def test_given_two_branches_each_editing_the_changelog_top_when_both_merge_then_the_second_conflicts(
    repo: Path,
) -> None:
    """The problem fragments remove: both entries land at the top of the same section."""
    top = CHANGELOG.replace("### Fixed\n\n", "### Fixed\n\n- {}\n", 1)
    _branch(repo, "one", {"CHANGELOG.md": top.format("The first fix.")})
    _branch(repo, "two", {"CHANGELOG.md": top.format("The second fix.")})

    assert _git(repo, "merge", "--no-edit", "one").returncode == 0
    second = _git(repo, "merge", "--no-edit", "two")

    assert second.returncode != 0
    assert "CONFLICT" in second.stdout


def test_given_fragments_when_folded_then_the_changelog_reads_as_if_each_entry_was_added_by_hand(repo: Path) -> None:
    _commit(
        repo,
        {"changelog.d/older.fixed.md": "- An older fix.\n", "changelog.d/feature.added.md": "- A new feature.\n"},
        "fix: older",
        "2026-02-01T00:00:00+00:00",
    )
    _commit(
        repo,
        {
            "changelog.d/newer.fixed.md": "- A newer fix, whose text\n  wraps too.\n",
            "changelog.d/rename.changed.md": "- A renamed option.\n",
        },
        "fix: newer",
        "2026-03-01T00:00:00+00:00",
    )

    done = _fold(repo)

    assert done.returncode == 0, done.stdout + done.stderr
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == (
        HEADER
        + """### Added

- A new feature.
- An existing feature.

### Changed

- A renamed option.

### Fixed

- A newer fix, whose text
  wraps too.
- An older fix.
- An existing fix, whose text
  wraps onto a second line.

## [1.0.0] - 2026-01-01

### Fixed

- A released fix.
"""
    )
    assert [p.name for p in (repo / "changelog.d").iterdir()] == [".gitkeep"]


def test_given_a_dry_run_when_folded_then_it_prints_the_result_and_changes_nothing(repo: Path) -> None:
    _commit(repo, {"changelog.d/one.fixed.md": "- The first fix.\n"}, "fix: one")

    done = _fold(repo, "-n")

    assert done.returncode == 0, done.stderr
    assert "- The first fix.\n- An existing fix" in done.stdout
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG
    assert (repo / "changelog.d" / "one.fixed.md").exists()


@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("one.fixes.md", "- A fix.\n", "type"),
        ("one.fixed.txt", "- A fix.\n", "type"),
        ("one.fixed.md", "A fix without a bullet.\n", "list item"),
        ("one.fixed.md", "\n", "empty"),
    ],
)
def test_given_a_malformed_fragment_when_folded_then_it_is_named_and_nothing_changes(
    repo: Path, name: str, text: str, reason: str
) -> None:
    _commit(repo, {f"changelog.d/{name}": text}, "fix: one")

    done = _fold(repo)

    assert done.returncode == 1
    assert name in done.stderr and reason in done.stderr
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG
    assert (repo / "changelog.d" / name).exists()


def test_given_no_unreleased_section_when_folded_then_it_refuses(repo: Path) -> None:
    _commit(
        repo,
        {"CHANGELOG.md": CHANGELOG.replace("## [Unreleased]\n\n", ""), "changelog.d/one.fixed.md": "- A fix.\n"},
        "fix: one",
    )

    done = _fold(repo)

    assert done.returncode == 1
    assert "## [Unreleased]" in done.stderr
    assert (repo / "changelog.d" / "one.fixed.md").exists()


def test_given_no_fragments_when_folded_then_the_changelog_is_unchanged(repo: Path) -> None:
    done = _fold(repo)

    assert done.returncode == 0, done.stderr
    assert "no changelog fragments" in done.stdout
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG
