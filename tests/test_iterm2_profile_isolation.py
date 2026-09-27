"""Tests for the iTerm2 Dynamic Profile isolation installed in conftest (AI-CLI-tevy).

Separable things are covered, because they fail independently:

* :func:`home_redirect_breach` -- the guard's *enforced* check. This is what fails the
  run when the redirect stops holding, so its failure modes are asserted directly; a
  guard that has never been observed to fire enforces nothing.
* :func:`snapshot_tree` / :func:`describe_tree_change` -- the reporting detector behind
  the guard's warning.
* The HOME redirect itself -- the *prevention*. These assert the property step 1 of the
  task asked for: a test that knows nothing about profile writes still cannot reach the
  operator's real directory.
* A static check that no test hardcodes the real profile path, which is the one route
  that would bypass the redirect without the enforced check noticing.
"""

import os
from pathlib import Path
from unittest.mock import patch

from conftest import (
    _REAL_HOME,
    _REAL_ITERM2_PROFILE_DIR,
    describe_tree_change,
    home_redirect_breach,
    snapshot_tree,
)

_TESTS_DIR = Path(__file__).resolve().parent

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
        # session._sweep_stale_iterm2_profiles unlinks profiles, so deletion is a real
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
# The enforced check
# ---------------------------------------------------------------------------


class TestHomeRedirectBreach:
    """Drives the detector by breaching the redirect on purpose.

    Each breach is confined to a ``patch.dict`` block rather than applied with
    ``monkeypatch``, because the autouse guard shares the test's ``monkeypatch``
    instance and so runs its own check BEFORE monkeypatch's undo -- a breach left
    standing past the test body correctly trips the guard on the test asserting it.
    """

    def test_given_the_conftest_redirect_when_checked_then_reports_no_breach(self):
        assert home_redirect_breach() == ""

    def test_given_os_name_patched_to_nt_when_checked_then_it_answers_instead_of_raising(self, monkeypatch):
        """``pathlib`` must not be the guard's only route to the resolved home.

        ``test_runaway_loop_guards.py`` patches ``os.name`` to ``"nt"`` to exercise a
        Windows branch, and the guard's teardown runs BEFORE monkeypatch's undo, so the
        patch is still live there. ``pathlib`` then builds a ``WindowsPath``, which
        refuses to instantiate on POSIX below Python 3.14, and the raise converted a
        PASSING test into a teardown ERROR -- red on three Linux jobs and on macOS while
        a local 3.14 run, where the instantiation is allowed, was clean.

        Patching it here also exercises the real thing: this test's own teardown runs the
        autouse guard under ``os.name == "nt"``, so a regression fails twice over.
        """
        monkeypatch.setattr(os, "name", "nt")
        assert home_redirect_breach() == ""

    def test_given_pathlib_refuses_to_resolve_home_when_checked_then_it_still_answers(self):
        """Version-independent control for the test above.

        The ``os.name`` route only raises below Python 3.14, so on a newer interpreter
        that test cannot fail and therefore cannot guard anything. Forcing the refusal
        directly makes the regression detectable on every version.
        """
        with patch.object(Path, "home", side_effect=NotImplementedError("cannot instantiate 'WindowsPath'")):
            assert home_redirect_breach() == ""

    def test_given_home_repointed_at_another_tmp_dir_when_checked_then_reports_no_breach(self, tmp_path):
        # The property is "not the real home", not "exactly this fixture's directory".
        # A test that isolates HOME its own way is doing the right thing and must pass.
        with patch.dict(os.environ, {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}):
            assert home_redirect_breach() == ""

    def test_given_home_restored_to_the_real_home_when_checked_then_reports_the_breach(self):
        with patch.dict(os.environ, {"HOME": str(_REAL_HOME), "USERPROFILE": str(_REAL_HOME)}):
            breach = home_redirect_breach()
        assert "resolved to the operator's real home" in breach
        assert str(_REAL_HOME) in breach

    def test_given_only_userprofile_pointed_at_the_real_home_when_checked_then_still_reports_it(self):
        # On POSIX, Path.home() ignores USERPROFILE, so this breach is invisible to a
        # Path.home() comparison alone and would leak on Windows only. Checking the raw
        # variables is what makes the guard OS-portable rather than POSIX-only.
        with patch.dict(os.environ, {"USERPROFILE": str(_REAL_HOME)}):
            assert "USERPROFILE points at the real home" in home_redirect_breach()

    def test_given_homedrive_and_homepath_reconstructing_the_real_home_when_checked_then_reports_it(self):
        # ntpath.expanduser falls back to HOMEDRIVE+HOMEPATH when USERPROFILE is absent,
        # so this pair is a third route to the real home on Windows.
        #
        # The pair is built by splitting the real home at its last separator rather than
        # with os.path.splitdrive, which returns an EMPTY drive on POSIX and so would
        # exercise nothing here. What matters to the guard is that the two values
        # concatenate to the real home, and this split reproduces that on either OS.
        home = str(_REAL_HOME)
        cut = home.rindex(os.sep)
        with patch.dict(os.environ, {"HOMEDRIVE": home[:cut], "HOMEPATH": home[cut:]}):
            assert "HOMEDRIVE + HOMEPATH reconstruct the real home" in home_redirect_breach()

    def test_given_homedrive_and_homepath_pointing_elsewhere_when_checked_then_reports_no_breach(self):
        # Checked as a pair against the real home rather than merely for presence: on
        # Windows these are set for every process, so flagging their existence would
        # fail the whole suite there.
        with patch.dict(os.environ, {"HOMEDRIVE": "C:", "HOMEPATH": r"\Users\someone-else"}):
            assert home_redirect_breach() == ""


# ---------------------------------------------------------------------------
# The prevention
# ---------------------------------------------------------------------------


class TestNoTestHardcodesTheRealProfilePath:
    """The one route that bypasses the redirect without the enforced check noticing.

    ``home_redirect_breach`` observes environment variables, so a test that builds the
    profile path as a literal string never trips it. Closing that statically is
    deterministic, where watching the directory for writes is not -- that directory has
    other legitimate writers on a developer machine.
    """

    def test_given_the_test_suite_when_scanned_then_no_module_hardcodes_the_iterm2_profile_path(self):
        # Both separators, because a Windows-flavoured literal leaks just as well as a
        # POSIX one, and the quoted "Application Support" is the part no other path has.
        literals = ("Application Support/iTerm2", "Application Support\\iTerm2")
        offenders = []
        for module in sorted(_TESTS_DIR.rglob("*.py")):
            if module.name in {"conftest.py", Path(__file__).name}:
                continue  # both legitimately name the real path in order to guard it
            text = module.read_text(encoding="utf-8")
            if any(literal in text for literal in literals):
                offenders.append(module.relative_to(_TESTS_DIR).as_posix())
        assert offenders == [], (
            f"these test modules name the real iTerm2 profile path directly: {offenders}. "
            "Build the path from Path.home() so the conftest HOME redirect covers it, or "
            "use a tmp_path, rather than hardcoding a literal the redirect cannot reach."
        )


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

        This is the regression test for AI-CLI-tevy. Before the redirect this call wrote
        into the operator's live iTerm2 directory; it must now land under the redirected
        home instead.

        The assertion is on WHERE the write landed rather than on the real directory
        being unchanged. Those are equivalent for this test's own write but not for the
        machine: that directory has other legitimate writers, so asserting it unchanged
        makes this test fail for someone else's write. Proving the write went under the
        redirect proves this call cannot have been the writer.
        """
        from ai_cli.icon_generator import generate_dynamic_profile

        written = generate_dynamic_profile("session-1", "#5e35b1", "cc")

        assert written.exists()
        assert written.is_relative_to(Path.home())
        assert not written.is_relative_to(_REAL_HOME)
        assert not written.is_relative_to(_REAL_ITERM2_PROFILE_DIR)

    def test_given_an_unpatched_stale_sweep_when_it_runs_then_it_deletes_nothing_real(self):
        """``_sweep_stale_iterm2_profiles`` unlinks, so isolation failure here DESTROYS state.

        With an empty active-session set every ``ai-cli-session-*.json`` in the resolved
        directory qualifies for deletion, which against the real directory would remove
        the operator's live profiles. Measured live against a throwaway home with the
        redirect disabled, this sweep did exactly that.
        """
        from unittest.mock import patch

        # Seed a profile in the REDIRECTED directory and require the sweep to delete
        # THAT one: a sweep that silently no-ops would otherwise pass this test without
        # proving anything about where it looked.
        from ai_cli.icon_generator import generate_dynamic_profile
        from ai_cli.session import _sweep_stale_iterm2_profiles

        seeded = generate_dynamic_profile("session-1", "#5e35b1", "cc")
        assert seeded.is_relative_to(Path.home())

        with patch("ai_cli.session.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            _sweep_stale_iterm2_profiles()

        assert not seeded.exists()
        assert home_redirect_breach() == ""
