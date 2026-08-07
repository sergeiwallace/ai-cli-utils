"""Tests for ai_cli.workspace — workspace file parser and ws_pull."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.workspace import _parse_workspace_folders, _upstream_drift, ws_pull

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path, folders: list[Path]) -> Path:
    ws_file = tmp_path / "test.code-workspace"
    ws_file.write_text(json.dumps({"folders": [{"path": str(f)} for f in folders]}))
    return ws_file


def _git_run_factory(
    *,
    is_repo: bool = True,
    main_dirty: bool = False,
    worktree_output: str = "",
    pull_output: str = "Already up to date.",
):
    """Return a side_effect function that simulates git subprocesses."""

    def _run(cmd: list[str]) -> tuple[int, str, str]:
        joined = " ".join(cmd)
        if "rev-parse" in joined:
            return (0, ".git\n", "") if is_repo else (128, "", "not a git repo")
        if "status" in joined and "--porcelain" in joined:
            return (0, "M file.py\n", "") if main_dirty else (0, "", "")
        if "worktree" in joined and "list" in joined:
            return (0, worktree_output, "")
        if "pull" in joined:
            return (0, pull_output, "")
        if "stash" in joined:
            return (0, "", "")
        return (0, "", "")

    return _run


def _worktree_porcelain(main_path: Path, linked: list[tuple[Path, str]]) -> str:
    """Build a git worktree list --porcelain string."""
    blocks = [f"worktree {main_path}\nHEAD abc123\nbranch refs/heads/main"]
    for wt_path, branch in linked:
        blocks.append(f"worktree {wt_path}\nHEAD def456\nbranch refs/heads/{branch}")
    return "\n\n".join(blocks) + "\n\n"


# ---------------------------------------------------------------------------
# _parse_workspace_folders
# ---------------------------------------------------------------------------


class TestParseWorkspaceFolders:
    def test_valid_workspace_returns_absolute_paths(self, tmp_path):
        proj1 = tmp_path / "proj1"
        proj2 = tmp_path / "proj2"
        ws = _make_workspace(tmp_path, [proj1, proj2])
        result = _parse_workspace_folders(ws)
        assert result == [proj1.resolve(), proj2.resolve()]

    def test_json5_comments_stripped(self, tmp_path):
        ws = tmp_path / "test.code-workspace"
        ws.write_text(f'// workspace comment\n{{"folders": [{{"path": "{tmp_path / "p"}"}}]}}')
        result = _parse_workspace_folders(ws)
        assert result == [(tmp_path / "p").resolve()]

    def test_json5_trailing_commas_stripped(self, tmp_path):
        ws = tmp_path / "test.code-workspace"
        ws.write_text(f'{{"folders": [{{"path": "{tmp_path / "p"}",}},]}}')
        result = _parse_workspace_folders(ws)
        assert result == [(tmp_path / "p").resolve()]

    def test_json5_comments_and_trailing_commas_together(self, tmp_path):
        p1 = tmp_path / "proj1"
        p2 = tmp_path / "proj2"
        content = f"""// My workspace
{{
    "folders": [
        {{"path": "{p1}"}}, // first
        {{"path": "{p2}"}}, // second
    ],
}}"""
        ws = tmp_path / "test.code-workspace"
        ws.write_text(content)
        result = _parse_workspace_folders(ws)
        assert result == [p1.resolve(), p2.resolve()]

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            _parse_workspace_folders(tmp_path / "missing.code-workspace")

    def test_empty_folders_returns_empty_list(self, tmp_path):
        ws = tmp_path / "test.code-workspace"
        ws.write_text('{"folders": []}')
        assert _parse_workspace_folders(ws) == []

    def test_relative_paths_resolved_relative_to_workspace_dir(self, tmp_path):
        ws = tmp_path / "test.code-workspace"
        ws.write_text('{"folders": [{"path": "subdir"}]}')
        result = _parse_workspace_folders(ws)
        assert result == [(tmp_path / "subdir").resolve()]


# ---------------------------------------------------------------------------
# ws_pull
# ---------------------------------------------------------------------------


class TestWsPull:
    def test_clean_main_tree_pulls(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        run = _git_run_factory(worktree_output=_worktree_porcelain(repo, []))

        with patch("ai_cli.workspace._run", side_effect=run) as mock_run:
            result = ws_pull(ws)

        assert result == 0
        pull_calls = [c for c in mock_run.call_args_list if "pull" in c.args[0]]
        assert len(pull_calls) == 1

    def test_dirty_main_tree_stash_pull_pop_in_order(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = _make_workspace(tmp_path, [repo])

        call_log: list[str] = []

        def run(cmd: list[str]) -> tuple[int, str, str]:
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return (0, ".git\n", "")
            if "status" in joined and "--porcelain" in joined:
                return (0, "M file.py\n", "")
            if "worktree" in joined:
                return (0, _worktree_porcelain(repo, []), "")
            if "stash" in joined and "push" in joined:
                call_log.append("stash_push")
                return (0, "", "")
            if "pull" in joined:
                call_log.append("pull")
                return (0, "", "")
            if "stash" in joined and "pop" in joined:
                call_log.append("stash_pop")
                return (0, "", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            ws_pull(ws)

        assert call_log == ["stash_push", "pull", "stash_pop"]

    def test_dirty_main_tree_warning_logged(self, tmp_path, capsys):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        run = _git_run_factory(main_dirty=True, worktree_output=_worktree_porcelain(repo, []))

        with patch("ai_cli.workspace._run", side_effect=run):
            ws_pull(ws)

        out = capsys.readouterr().out
        assert "stashed+pulled" in out

    def test_clean_worktree_pulled(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])

        with patch("ai_cli.workspace._run", side_effect=_git_run_factory(worktree_output=wt_output)) as mock_run:
            ws_pull(ws)

        pull_cmds = [c for c in mock_run.call_args_list if "pull" in c.args[0]]
        # main pull + worktree pull
        assert len(pull_cmds) == 2

    def test_dirty_worktree_skipped_and_logged(self, tmp_path, capsys):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])

        def run(cmd: list[str]) -> tuple[int, str, str]:
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return (0, ".git\n", "")
            if "status" in joined and "--porcelain" in joined:
                # main is clean, worktree is dirty — distinguish by path
                if str(wt) in joined:
                    return (0, "M file.py\n", "")
                return (0, "", "")
            if "worktree" in joined and "list" in joined:
                return (0, wt_output, "")
            if "pull" in joined:
                return (0, "", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run) as mock_run:
            ws_pull(ws)

        out = capsys.readouterr().out
        assert "dirty, skipped" in out
        # Worktree should NOT be pulled
        pull_cmds = [c for c in mock_run.call_args_list if "pull" in c.args[0]]
        assert not any(str(wt) in str(c.args[0]) for c in pull_cmds)

    def test_non_existent_folder_silently_skipped(self, tmp_path, capsys):
        missing = tmp_path / "ghost"
        ws = _make_workspace(tmp_path, [missing])

        with patch("ai_cli.workspace._run") as mock_run:
            result = ws_pull(ws)

        assert result == 0
        mock_run.assert_not_called()

    def test_non_git_folder_skipped_with_warning(self, tmp_path, capsys):
        notgit = tmp_path / "notgit"
        notgit.mkdir()
        ws = _make_workspace(tmp_path, [notgit])
        run = _git_run_factory(is_repo=False)

        with patch("ai_cli.workspace._run", side_effect=run):
            ws_pull(ws)

        out = capsys.readouterr().out
        assert "not a git repo" in out

    def test_dry_run_no_git_pull_calls(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])
        run = _git_run_factory(worktree_output=wt_output)

        with patch("ai_cli.workspace._run", side_effect=run) as mock_run:
            ws_pull(ws, dry_run=True)

        pull_cmds = [c for c in mock_run.call_args_list if "pull" in c.args[0]]
        assert len(pull_cmds) == 0

    def test_dry_run_no_stash_calls(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        run = _git_run_factory(main_dirty=True, worktree_output=_worktree_porcelain(repo, []))

        with patch("ai_cli.workspace._run", side_effect=run) as mock_run:
            ws_pull(ws, dry_run=True)

        stash_cmds = [c for c in mock_run.call_args_list if "stash" in c.args[0]]
        assert len(stash_cmds) == 0

    def test_summary_line_counts_correctly(self, tmp_path, capsys):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])
        run = _git_run_factory(worktree_output=wt_output)

        with patch("ai_cli.workspace._run", side_effect=run):
            ws_pull(ws)

        out = capsys.readouterr().out
        assert "2 pulled" in out
        assert "0 stashed" in out
        assert "0 skipped" in out

    def test_returns_zero_on_success(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        run = _git_run_factory(worktree_output=_worktree_porcelain(repo, []))

        with patch("ai_cli.workspace._run", side_effect=run):
            result = ws_pull(ws)

        assert result == 0


# ---------------------------------------------------------------------------
# _upstream_drift (AI-CLI-128)
# ---------------------------------------------------------------------------


class TestUpstreamDrift:
    def test_non_wt_branch_returns_none(self, tmp_path):
        def run(cmd):
            if "--abbrev-ref" in cmd and "HEAD" in cmd:
                return (0, "main\n", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            assert _upstream_drift(tmp_path) is None

    def test_head_rev_parse_fails_returns_none(self, tmp_path):
        def run(cmd):
            if "--abbrev-ref" in cmd and "HEAD" in cmd:
                return (128, "", "fatal: not a git repository")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            assert _upstream_drift(tmp_path) is None

    def test_wt_branch_no_upstream_returns_warning(self, tmp_path):
        def run(cmd):
            joined = " ".join(cmd)
            if "--abbrev-ref" in joined and "HEAD" in joined:
                return (0, "wt-sw-1\n", "")
            if "@{u}" in joined:
                return (128, "", "fatal: no upstream configured for branch 'wt-sw-1'")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            result = _upstream_drift(tmp_path)
        assert result is not None
        assert "no upstream" in result

    def test_wt_branch_wrong_upstream_returns_warning(self, tmp_path):
        def run(cmd):
            joined = " ".join(cmd)
            if "--abbrev-ref" in joined and "HEAD" in joined:
                return (0, "wt-sw-1\n", "")
            if "@{u}" in joined:
                return (0, "origin/wt-sw-1\n", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            result = _upstream_drift(tmp_path)
        assert result is not None
        assert "origin/wt-sw-1" in result

    def test_wt_branch_correct_upstream_returns_none(self, tmp_path):
        def run(cmd):
            joined = " ".join(cmd)
            if "--abbrev-ref" in joined and "HEAD" in joined:
                return (0, "wt-sw-1\n", "")
            if "@{u}" in joined:
                return (0, "origin/main\n", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            assert _upstream_drift(tmp_path) is None


# ---------------------------------------------------------------------------
# ws_pull drift reporting (AI-CLI-128)
# ---------------------------------------------------------------------------


class TestWsPullDriftReporting:
    def test_ws_pull_when_worktree_drifted_then_logs_warning(self, tmp_path, capsys):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])

        def run(cmd):
            joined = " ".join(cmd)
            if "--abbrev-ref" in joined and "HEAD" in joined and str(wt) in joined:
                return (0, "wt-branch\n", "")
            if "@{u}" in joined and str(wt) in joined:
                return (128, "", "fatal: no upstream configured for branch 'wt-branch'")
            if "rev-parse" in joined:
                return (0, ".git\n", "")
            if "status" in joined and "--porcelain" in joined:
                return (0, "", "")
            if "worktree" in joined and "list" in joined:
                return (0, wt_output, "")
            if "pull" in joined:
                return (0, "", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            result = ws_pull(ws)

        out = capsys.readouterr().out
        assert "AI-CLI-128" in out
        assert "not tracking origin/main" in out
        assert result == 0  # report-only, never fails the pull

    def test_ws_pull_when_no_drift_then_no_warning(self, tmp_path, capsys):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        wt = tmp_path / "wt1"
        wt.mkdir()
        ws = _make_workspace(tmp_path, [repo])
        wt_output = _worktree_porcelain(repo, [(wt, "wt-branch")])

        def run(cmd):
            joined = " ".join(cmd)
            if "--abbrev-ref" in joined and "HEAD" in joined and str(wt) in joined:
                return (0, "wt-branch\n", "")
            if "@{u}" in joined and str(wt) in joined:
                return (0, "origin/main\n", "")
            if "rev-parse" in joined:
                return (0, ".git\n", "")
            if "status" in joined and "--porcelain" in joined:
                return (0, "", "")
            if "worktree" in joined and "list" in joined:
                return (0, wt_output, "")
            if "pull" in joined:
                return (0, "", "")
            return (0, "", "")

        with patch("ai_cli.workspace._run", side_effect=run):
            ws_pull(ws)

        out = capsys.readouterr().out
        assert "AI-CLI-128" not in out
        assert "not tracking origin/main" not in out
