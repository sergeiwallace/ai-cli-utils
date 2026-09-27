"""Session-prefix resolution in the CC session cleanup script (AI-CLI-2jwm).

The script decides whether a named session transcript is sitting in the wrong project
directory, and archives it if so. It used to answer that from a hardcoded table of
prefix-to-repository pairs -- which was a published roster of one operator's private
projects in a public repository, and was also duplicating what the directory names on
disk already state.

The replacement learns the pairing from the directories present. These tests pin the two
properties that matter: it resolves a multi-segment prefix correctly, and it declines to
answer rather than guessing -- because the caller ARCHIVES a file on a mismatch, so a
wrong answer moves a session that was exactly where it belonged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cleanup_cc_sessions import (
    build_prefix_repo_map,
    get_expected_staging_dir,
)


def _staging_tree(root: Path, *dir_names: str) -> Path:
    """A staging base directory whose subdirectories carry the short encoded names."""
    base = root / "staging"
    base.mkdir()
    for name in dir_names:
        (base / name).mkdir()
    return base


class TestThePrefixMapIsLearnedFromTheDirectoriesPresent:
    def test_given_a_worktree_directory_when_the_map_is_built_then_its_prefix_maps_to_its_repository(self, tmp_path):
        base = _staging_tree(tmp_path, "myproject--worktrees-myproject-1")

        assert build_prefix_repo_map(base, is_staging=True) == {"myproject": "myproject"}

    def test_given_a_multi_segment_prefix_when_the_map_is_built_then_the_whole_prefix_is_captured(self, tmp_path):
        """The case a greedy or a naive split gets wrong, and the one this repo's own
        session names take: the prefix is everything before the trailing number."""
        base = _staging_tree(tmp_path, "myapp-utils--worktrees-my-app-3")

        assert build_prefix_repo_map(base, is_staging=True) == {"my-app": "myapp-utils"}

    def test_given_a_prefix_already_established_when_another_directory_claims_it_then_the_first_wins(self, tmp_path):
        """A single contaminated directory must not be able to redefine a prefix.

        If it could, the contaminated directory would teach the script that it is the
        correct home for its own sessions, and the contamination would become invisible.
        """
        base = _staging_tree(
            tmp_path,
            "correct-repo--worktrees-shared-1",
            "wrong-repo--worktrees-shared-2",
        )

        assert build_prefix_repo_map(base, is_staging=True) == {"shared": "correct-repo"}

    def test_given_a_local_encoded_directory_when_the_map_is_built_then_the_path_prefix_is_stripped(self, tmp_path):
        """Local CC directories carry a full encoded path; staging directories do not."""
        base = _staging_tree(tmp_path, "-Users-user-projects-myproject--worktrees-myproject-1")

        assert build_prefix_repo_map(base, is_staging=False) == {"myproject": "myproject"}

    def test_given_a_directory_that_is_not_a_worktree_when_the_map_is_built_then_it_contributes_nothing(self, tmp_path):
        base = _staging_tree(tmp_path, "myproject", "some-other-directory")

        assert build_prefix_repo_map(base, is_staging=True) == {}

    def test_given_a_missing_base_directory_when_the_map_is_built_then_it_is_empty_rather_than_raising(self, tmp_path):
        """The script scans two bases and either may be absent on a given host."""
        assert build_prefix_repo_map(tmp_path / "nope", is_staging=True) == {}


class TestAnUnknownTitleDeclinesToAnswer:
    """``None`` is the fail-safe: the caller leaves the file in place on it.

    Each of these previously fell through the hardcoded table to ``None`` as well, so the
    fail-safe behaviour is preserved rather than newly introduced -- but it is now the only
    thing standing between an unrecognised session and being archived, so it is pinned.
    """

    def test_given_a_title_with_no_trailing_number_when_resolved_then_nothing_is_expected(self):
        assert get_expected_staging_dir("not-a-session", {"myproject": "myproject"}) is None

    def test_given_a_prefix_no_directory_established_when_resolved_then_nothing_is_expected(self):
        """The replacement for "the prefix is not in the table"."""
        assert get_expected_staging_dir("unheard-of-7", {"myproject": "myproject"}) is None

    def test_given_an_empty_map_when_resolved_then_nothing_is_expected(self):
        assert get_expected_staging_dir("myproject-1", {}) is None


class TestAKnownTitleResolvesToItsDirectory:
    def test_given_a_known_prefix_when_resolved_then_the_expected_directory_is_built(self):
        assert get_expected_staging_dir("myproject-2", {"myproject": "myproject"}) == "myproject--worktrees-myproject-2"

    def test_given_a_multi_segment_prefix_when_resolved_then_the_number_is_not_eaten_by_the_prefix(self):
        assert get_expected_staging_dir("my-app-3", {"my-app": "myapp-utils"}) == "myapp-utils--worktrees-my-app-3"

    def test_given_a_title_with_a_trailing_segment_when_resolved_then_it_resolves_to_the_numbered_session(self):
        """``<prefix>-<n>-<something>`` was explicitly handled by the old table's ordering
        comment, so dropping it would be a silent behaviour change."""
        assert (
            get_expected_staging_dir("myproject-1-suffix", {"myproject": "myproject"})
            == "myproject--worktrees-myproject-1"
        )

    def test_given_mixed_case_in_the_title_when_resolved_then_the_prefix_matches_case_insensitively(self):
        """The old table compiled every pattern with ``re.IGNORECASE``."""
        assert get_expected_staging_dir("MyProject-1", {"myproject": "myproject"}) == "myproject--worktrees-myproject-1"
