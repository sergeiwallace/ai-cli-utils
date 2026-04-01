import subprocess
from pathlib import Path
from unittest.mock import patch

from ai_cli.setup import _is_humanware_platform, _repo_root_from, run_setup


class TestIsHumanwarePlatform:
    def test_when_projects_claude_md_exists_then_returns_true(self, tmp_path):
        projects_claude = tmp_path / "projects" / "CLAUDE.md"
        projects_claude.parent.mkdir(parents=True)
        projects_claude.write_text("# global config")
        with patch("ai_cli.setup.Path.home", return_value=tmp_path):
            assert _is_humanware_platform() is True

    def test_when_projects_claude_md_missing_then_returns_false(self, tmp_path):
        with patch("ai_cli.setup.Path.home", return_value=tmp_path):
            assert _is_humanware_platform() is False


class TestRepoRootFrom:
    def test_when_inside_git_repo_then_returns_path(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        result = _repo_root_from(tmp_path)
        assert result is not None
        assert result.exists()

    def test_when_outside_git_repo_then_returns_none(self, tmp_path):
        result = _repo_root_from(tmp_path)
        assert result is None


class TestRunSetup:
    def _make_repo(self, tmp_path: Path) -> Path:
        tmp_path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        (tmp_path / "CLAUDE.md").write_text("# lean config")
        (tmp_path / "CLAUDE-full.md").write_text("# full config")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        return tmp_path

    def test_when_humanware_platform_detected_then_exits_zero_and_prints_confirmation(self, tmp_path, capsys):
        repo = self._make_repo(tmp_path / "repo")
        home = tmp_path / "home"
        (home / "projects").mkdir(parents=True)
        (home / "projects" / "CLAUDE.md").write_text("# global")
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 0
        out = capsys.readouterr().out
        assert "humanware platform detected" in out
        assert "lean CLAUDE.md" in out

    def test_when_humanware_platform_detected_then_claude_md_unchanged(self, tmp_path):
        repo = self._make_repo(tmp_path / "repo")
        original = (repo / "CLAUDE.md").read_text()
        home = tmp_path / "home"
        (home / "projects").mkdir(parents=True)
        (home / "projects" / "CLAUDE.md").write_text("# global")
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        assert (repo / "CLAUDE.md").read_text() == original

    def test_when_no_humanware_platform_then_claude_md_replaced_with_full(self, tmp_path):
        repo = self._make_repo(tmp_path / "repo")
        home = tmp_path / "home"
        home.mkdir(parents=True)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 0
        assert (repo / "CLAUDE.md").read_text() == "# full config"

    def test_when_no_humanware_platform_then_prints_switched_message(self, tmp_path, capsys):
        repo = self._make_repo(tmp_path / "repo")
        home = tmp_path / "home"
        home.mkdir(parents=True)
        with patch("ai_cli.setup.Path.home", return_value=home):
            run_setup(cwd=repo)
        out = capsys.readouterr().out
        assert "standalone config" in out
        assert "assume-unchanged" in out

    def test_when_claude_full_md_missing_then_returns_error(self, tmp_path, capsys):
        repo = self._make_repo(tmp_path / "repo")
        (repo / "CLAUDE-full.md").unlink()
        home = tmp_path / "home"
        home.mkdir(parents=True)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 1
        assert "CLAUDE-full.md not found" in capsys.readouterr().err

    def test_when_claude_md_missing_then_returns_error(self, tmp_path, capsys):
        repo = self._make_repo(tmp_path / "repo")
        (repo / "CLAUDE.md").unlink()
        home = tmp_path / "home"
        home.mkdir(parents=True)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=repo)
        assert result == 1
        assert "CLAUDE.md not found" in capsys.readouterr().err

    def test_when_not_in_git_repo_then_returns_error(self, tmp_path, capsys):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        with patch("ai_cli.setup.Path.home", return_value=home):
            result = run_setup(cwd=tmp_path)
        assert result == 1
        assert "not inside a git repository" in capsys.readouterr().err
