"""Tests for the iTerm2 Dynamic Profile isolation installed in conftest (AI-CLI-tevy).

Two separable things are covered, because they fail independently:

* :func:`snapshot_tree` / :func:`describe_tree_change` -- the guard's *detector*. A
  guard that has never been observed to fire enforces nothing, so its failure modes
  are asserted directly here rather than only exercised in passing by 3000 clean tests.
* The HOME redirect -- the guard's *prevention*. These assert the property step 1 of
  the task asked for: a test that knows nothing about profile writes still cannot
  reach the operator's real directory.
"""

from pathlib import Path

from conftest import _REAL_HOME, _REAL_ITERM2_PROFILE_DIR, describe_tree_change, snapshot_tree

# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


class TestSnapshotTree:
    def test_given_a_missing_directory_when_snapshotted_then_returns_none(self, tmp_path):
        assert snapshot_tree(tmp_path / "absent") is None

    def test_given_an_empty_directory_when_snapshotted_then_returns_empty_mapping_not_none(self, tmp_path):
        # "absent" and "present but empty" must stay distinguishable: a test that
        # merely creates the real profile directory has already polluted the machine.
        assert snapshot_tree(tmp_path) == {}

    def test_given_a_file_when_snapshotted_then_records_its_relative_posix_name(self, tmp_path):
        (tmp_path / "ai-cli-session-session-1.json").write_text("{}")
        assert set(snapshot_tree(tmp_path) or {}) == {"ai-cli-session-session-1.json"}

    def test_given_a_nested_file_when_snapshotted_then_records_both_dir_and_file(self, tmp_path):
        # layout.py writes into a SUBDIRECTORY of the watched dir, so the walk must
        # not stop at depth one.
        nested = tmp_path / "ai-cli-generated"
        nested.mkdir()
        (nested / "layout-myproject-editor.json").write_text("{}")
        assert set(snapshot_tree(tmp_path) or {}) == {
            "ai-cli-generated",
            "ai-cli-generated/layout-myproject-editor.json",
        }

    def test_given_a_path_that_is_a_file_when_snapshotted_then_returns_none(self, tmp_path):
        plain = tmp_path / "not-a-dir"
        plain.write_text("x")
        assert snapshot_tree(plain) is None


class TestDescribeTreeChange:
    def test_given_identical_snapshots_when_described_then_reports_no_change(self):
        snapshot = {"a.json": (1, 2)}
        assert describe_tree_change(snapshot, dict(snapshot)) == ""

    def test_given_two_absent_directories_when_described_then_reports_no_change(self):
        assert describe_tree_change(None, None) == ""

    def test_given_a_directory_created_during_the_test_when_described_then_reports_creation(self):
        assert describe_tree_change(None, {}) == "directory did not exist before the test and does now"

    def test_given_a_directory_removed_during_the_test_when_described_then_reports_removal(self):
        assert describe_tree_change({}, None) == "directory existed before the test and was removed"

    def test_given_an_added_file_when_described_then_names_it(self):
        change = describe_tree_change({}, {"ai-cli-session-session-1.json": (9, 9)})
        assert change == "added ['ai-cli-session-session-1.json']"

    def test_given_a_removed_file_when_described_then_names_it(self):
        # session._cleanup_stale_profiles unlinks profiles, so deletion is a real
        # failure mode here and not just an added-file problem.
        change = describe_tree_change({"ai-cli-session-session-1.json": (9, 9)}, {})
        assert change == "removed ['ai-cli-session-session-1.json']"

    def test_given_a_rewritten_file_of_the_same_size_when_described_then_still_reports_it(self):
        # Same size, different mtime: an overwrite of an existing profile. iTerm2
        # reloads on the filesystem event regardless of whether the size changed.
        change = describe_tree_change({"a.json": (9, 100)}, {"a.json": (9, 200)})
        assert change == "modified ['a.json']"

    def test_given_added_removed_and_modified_together_when_described_then_reports_all_three(self):
        before = {"keep.json": (1, 1), "gone.json": (1, 1), "changed.json": (1, 1)}
        after = {"keep.json": (1, 1), "changed.json": (1, 2), "new.json": (1, 1)}
        assert (
            describe_tree_change(before, after)
            == "added ['new.json']; removed ['gone.json']; modified ['changed.json']"
        )


# ---------------------------------------------------------------------------
# The prevention
# ---------------------------------------------------------------------------


class TestHomeRedirect:
    def test_given_the_autouse_fixture_when_a_test_runs_then_home_is_not_the_real_home(self):
        assert Path.home() != _REAL_HOME

    def test_given_the_autouse_fixture_when_a_test_runs_then_home_is_writable_and_empty_of_profiles(self):
        # The redirect must yield a usable home, not merely a different string --
        # production code calls mkdir(parents=True) under it.
        home = Path.home()
        assert home.is_dir()
        assert not (home / "Library").exists()

    def test_given_no_call_site_patching_when_resolving_the_profile_dir_then_it_is_under_the_redirected_home(self):
        from ai_cli.icon_generator import _dynamic_profile_dir

        resolved = _dynamic_profile_dir()
        assert resolved.is_relative_to(Path.home())
        assert not resolved.is_relative_to(_REAL_HOME)

    def test_given_no_call_site_patching_when_resolving_the_layout_profile_dir_then_it_is_under_the_redirected_home(
        self,
    ):
        # layout.py defines its OWN _dynamic_profile_dir, so patching the
        # icon_generator one would have left this path leaking.
        from ai_cli.layout import _dynamic_profile_dir as layout_profile_dir

        resolved = layout_profile_dir()
        assert resolved.is_relative_to(Path.home())
        assert not resolved.is_relative_to(_REAL_HOME)

    def test_given_an_unpatched_generate_call_when_it_writes_then_the_real_directory_is_untouched(self):
        """The exact shape that leaked: generate a profile with nothing mocked.

        This is the regression test for AI-CLI-tevy. Before the redirect this call
        wrote into the operator's live iTerm2 directory; it must now land under the
        redirected home and leave the real directory byte-identical. The real-directory
        assertion is deliberately explicit here as well as in the autouse guard, so
        this test still states its own contract if the guard is ever changed.
        """
        from ai_cli.icon_generator import generate_dynamic_profile

        before = snapshot_tree(_REAL_ITERM2_PROFILE_DIR)

        written = generate_dynamic_profile("session-1", "#5e35b1", "cc")

        assert written.exists()
        assert written.is_relative_to(Path.home())
        assert not written.is_relative_to(_REAL_HOME)
        assert describe_tree_change(before, snapshot_tree(_REAL_ITERM2_PROFILE_DIR)) == ""

    def test_given_an_unpatched_stale_sweep_when_it_runs_then_it_deletes_nothing_real(self):
        """``_sweep_stale_iterm2_profiles`` unlinks, so isolation failure here DESTROYS state.

        With an empty active-session set every ``ai-cli-session-*.json`` in the
        resolved directory qualifies for deletion, which against the real directory
        would remove the operator's live profiles.
        """
        from unittest.mock import patch

        # Seed a profile in the REDIRECTED directory and prove the sweep removes that
        # one, so the test fails if the call silently no-ops instead of isolating.
        from ai_cli.icon_generator import generate_dynamic_profile
        from ai_cli.session import _sweep_stale_iterm2_profiles

        seeded = generate_dynamic_profile("session-1", "#5e35b1", "cc")
        before = snapshot_tree(_REAL_ITERM2_PROFILE_DIR)

        with patch("ai_cli.session.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            _sweep_stale_iterm2_profiles()

        assert not seeded.exists()
        assert describe_tree_change(before, snapshot_tree(_REAL_ITERM2_PROFILE_DIR)) == ""
