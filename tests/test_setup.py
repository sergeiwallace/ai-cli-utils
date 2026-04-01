import subprocess
from pathlib import Path
from unittest.mock import call, patch

from ai_cli.setup import _is_humanware_platform, _repo_root_from, run_setup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with CLAUDE.md and CLAUDE-full.md committed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "CLAUDE.md").write_text("# lean config")
    (tmp_path / "CLAUDE-full.md").write_text("# full standalone config")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def _make_home_with_humanware(tmp_path: Path) -> Path:
    """Create a home directory that looks like a humanware platform install."""
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    (home / "projects" / "CLAUDE.md").write_text("# global humanware config")
    return home


def _make_home_without_humanware(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    return home


# ---------------------------------------------------------------------------
# _is_humanware_platform
# ---------------------------------------------------------------------------


class TestIsHumanwarePlatform:
    def test_when_projects_claude_md_exists_then_returns_true(self, tmp_path):
        home = _make_home_with_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            assert _is_humanware_platform() is True

    def test_when_projects_claude_md_missing_then_returns_false(self, tmp_path):
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            assert _is_humanware_platform() is False

    def test_when_projects_dir_missing_entirely_then_returns_false(self, tmp_path):
        # ~/projects/ doesn't exist at all
        home = tmp_path / "home"
        home.mkdir()
        with patch("ai_cli.setup.Path.home", return_value=home):
            assert _is_humanware_platform() is False


# ---------------------------------------------------------------------------
# _repo_root_from
# ---------------------------------------------------------------------------


class TestRepoRootFrom:
    def test_when_inside_git_repo_then_returns_root_path(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        result = _repo_root_from(tmp_path)
        assert result is not None
        assert result.exists()
        assert (result / ".git").exists()

    def test_when_outside_git_repo_then_returns_none(self, tmp_path):
        result = _repo_root_from(tmp_path)
        assert result is None

    def test_when_called_from_subdirectory_returns_repo_root(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subdir = tmp_path / "deep" / "subdir"
        subdir.mkdir(parents=True)
        result = _repo_root_from(subdir)
        assert result is not None
        # Should resolve to the repo root, not the subdir
        assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# run_setup — humanware platform path
# ---------------------------------------------------------------------------


class TestRunSetupHumanwarePlatform:
    def test_returns_zero(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_with_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            assert run_setup(cwd=repo) == 0

    def test_prints_platform_detected_and_lean_config_confirmation(self, tmp_path, capsys):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_with_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        out = capsys.readouterr().out
        assert "humanware platform detected" in out
        assert "lean CLAUDE.md" in out

    def test_claude_md_content_is_not_modified(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_with_humanware(tmp_path)
        original = (repo / "CLAUDE.md").read_text()
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE.md").read_text() == original

    def test_claude_full_md_is_not_modified(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_with_humanware(tmp_path)
        original_full = (repo / "CLAUDE-full.md").read_text()
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE-full.md").read_text() == original_full

    def test_git_assume_unchanged_is_not_called(self, tmp_path):
        """On humanware platform, git index must not be touched."""
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_with_humanware(tmp_path)
        with (
            patch("ai_cli.setup.Path.home", return_value=home),
            patch("ai_cli.setup.subprocess.run", wraps=subprocess.run) as mock_run,
        ):
            run_setup(cwd=repo)
        assume_unchanged_calls = [c for c in mock_run.call_args_list if "assume-unchanged" in str(c)]
        assert assume_unchanged_calls == []


# ---------------------------------------------------------------------------
# run_setup — external (no humanware) path
# ---------------------------------------------------------------------------


class TestRunSetupExternal:
    def test_returns_zero(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            assert run_setup(cwd=repo) == 0

    def test_claude_md_content_replaced_with_full_config(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE.md").read_text() == "# full standalone config"

    def test_claude_full_md_is_preserved_after_swap(self, tmp_path):
        """CLAUDE-full.md must still exist and be unmodified after setup."""
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE-full.md").exists()
        assert (repo / "CLAUDE-full.md").read_text() == "# full standalone config"

    def test_prints_no_platform_detected_and_switched_message(self, tmp_path, capsys):
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        out = capsys.readouterr().out
        assert "No humanware platform detected" in out
        assert "standalone config" in out
        assert "assume-unchanged" in out

    def test_git_assume_unchanged_called_on_claude_md(self, tmp_path):
        """After swap, git must be told to ignore local changes to CLAUDE.md."""
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with (
            patch("ai_cli.setup.Path.home", return_value=home),
            patch("ai_cli.setup.subprocess.run", wraps=subprocess.run) as mock_run,
        ):
            run_setup(cwd=repo)
        assume_unchanged_calls = [
            c
            for c in mock_run.call_args_list
            if c
            == call(
                ["git", "update-index", "--assume-unchanged", "CLAUDE.md"],
                cwd=repo,
                capture_output=True,
            )
        ]
        assert len(assume_unchanged_calls) == 1

    def test_idempotent_when_run_twice(self, tmp_path):
        """Running setup twice should leave CLAUDE.md with the full config and return 0 both times."""
        repo = _make_repo(tmp_path / "repo")
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result1 = run_setup(cwd=repo)
            result2 = run_setup(cwd=repo)
        assert result1 == 0
        assert result2 == 0
        assert (repo / "CLAUDE.md").read_text() == "# full standalone config"

    def test_when_run_from_subdirectory_still_finds_repo_root(self, tmp_path):
        """Setup must work when cwd is a subdirectory, not the repo root."""
        repo = _make_repo(tmp_path / "repo")
        subdir = repo / "src" / "deep"
        subdir.mkdir(parents=True)
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=subdir)
        assert result == 0
        assert (repo / "CLAUDE.md").read_text() == "# full standalone config"


# ---------------------------------------------------------------------------
# run_setup — error paths
# ---------------------------------------------------------------------------


class TestRunSetupErrors:
    def test_when_not_in_git_repo_returns_error_and_prints_message(self, tmp_path, capsys):
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=tmp_path)
        assert result == 1
        assert "not inside a git repository" in capsys.readouterr().err

    def test_when_claude_full_md_missing_returns_error(self, tmp_path, capsys):
        repo = _make_repo(tmp_path / "repo")
        (repo / "CLAUDE-full.md").unlink()
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 1
        err = capsys.readouterr().err
        assert "CLAUDE-full.md not found" in err
        assert "restore" in err  # hint about recovery

    def test_when_claude_full_md_missing_claude_md_not_modified(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        (repo / "CLAUDE-full.md").unlink()
        original = (repo / "CLAUDE.md").read_text()
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE.md").read_text() == original

    def test_when_claude_md_missing_returns_error(self, tmp_path, capsys):
        repo = _make_repo(tmp_path / "repo")
        (repo / "CLAUDE.md").unlink()
        home = _make_home_without_humanware(tmp_path)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 1
        assert "CLAUDE.md not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI dispatch — `ai setup` in main.py
# ---------------------------------------------------------------------------


class TestCliSetupDispatch:
    def test_ai_setup_calls_run_setup_and_exits_with_its_return_code(self, tmp_path):
        from ai_cli.main import cli

        exit_calls = []

        def fake_exit(code):
            exit_calls.append(code)
            raise SystemExit(code)

        with (
            patch("sys.argv", ["ai", "setup"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.setup.run_setup", return_value=0) as mock_setup,
            patch("sys.exit", side_effect=fake_exit),
        ):
            try:
                cli()
            except SystemExit:
                pass
        mock_setup.assert_called_once()
        assert exit_calls == [0]

    def test_ai_setup_propagates_nonzero_exit_code(self, tmp_path):
        from ai_cli.main import cli

        exit_calls = []

        def fake_exit(code):
            exit_calls.append(code)
            raise SystemExit(code)

        with (
            patch("sys.argv", ["ai", "setup"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.setup.run_setup", return_value=1),
            patch("sys.exit", side_effect=fake_exit),
        ):
            try:
                cli()
            except SystemExit:
                pass
        assert exit_calls == [1]
