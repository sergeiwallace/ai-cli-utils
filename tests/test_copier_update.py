"""Tests for ai copier-update subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import (
    _conflict_files,
    _do_update_in_worktree,
    _find_copier_projects,
    _repo_root,
    _run_isolated,
    _update_one_isolated,
    run_copier_update,
)


# ---------------------------------------------------------------------------
# _find_copier_projects
# ---------------------------------------------------------------------------


def test_find_copier_projects_returns_matching_dirs(tmp_path):
    """Projects whose _src_path contains 'project-template' are returned."""
    proj_a = tmp_path / "alpha"
    proj_a.mkdir()
    (proj_a / ".copier-answers.yml").write_text("_src_path: /home/user/projects/project-template\n")

    proj_b = tmp_path / "beta"
    proj_b.mkdir()
    (proj_b / ".copier-answers.yml").write_text("_src_path: /home/user/projects/other-template\n")

    result = _find_copier_projects(tmp_path)
    assert result == [proj_a]


def test_find_copier_projects_skips_unreadable_answers_file(tmp_path):
    """Answers files that fail to parse are silently skipped."""
    proj = tmp_path / "broken"
    proj.mkdir()
    (proj / ".copier-answers.yml").write_text(": : invalid yaml :::\n")

    result = _find_copier_projects(tmp_path)
    assert result == []


def test_find_copier_projects_empty_dir(tmp_path):
    """Empty projects dir returns empty list."""
    result = _find_copier_projects(tmp_path)
    assert result == []


def test_find_copier_projects_no_src_path_key(tmp_path):
    """Answers file with no _src_path key is skipped."""
    proj = tmp_path / "no-src"
    proj.mkdir()
    (proj / ".copier-answers.yml").write_text("project_name: foo\n")

    result = _find_copier_projects(tmp_path)
    assert result == []


def test_find_copier_projects_returns_sorted(tmp_path):
    """Results are sorted alphabetically by directory name."""
    for name in ("zebra", "apple", "mango"):
        d = tmp_path / name
        d.mkdir()
        (d / ".copier-answers.yml").write_text("_src_path: /projects/project-template\n")

    result = _find_copier_projects(tmp_path)
    assert [p.name for p in result] == ["apple", "mango", "zebra"]


# ---------------------------------------------------------------------------
# _conflict_files
# ---------------------------------------------------------------------------


def test_conflict_files_returns_files_with_markers(tmp_path):
    """Files containing <<<<<<< are returned."""
    conflict_file = tmp_path / "conflict.py"
    conflict_file.write_text("<<<<<<< HEAD\nfoo\n=======\nbar\n>>>>>>>\n")

    result = _conflict_files(tmp_path)
    assert str(conflict_file) in result


def test_conflict_files_returns_empty_when_none(tmp_path):
    """Clean directory returns empty list."""
    (tmp_path / "clean.py").write_text("x = 1\n")
    result = _conflict_files(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# run_copier_update
# ---------------------------------------------------------------------------


def _make_answers(proj_dir: Path, src_path: str = "/projects/project-template") -> None:
    (proj_dir / ".copier-answers.yml").write_text(f"_src_path: {src_path}\n")


def test_run_copier_update_projects_dir_not_found(tmp_path):
    """Returns 1 when projects_dir does not exist."""
    missing = tmp_path / "no-such-dir"
    result = run_copier_update(projects_dir=missing)
    assert result == 1


def test_run_copier_update_copier_not_in_path(tmp_path):
    """Returns 1 when copier binary is not in PATH."""
    with patch("shutil.which", return_value=None):
        result = run_copier_update(projects_dir=tmp_path)
    assert result == 1


def test_run_copier_update_no_projects_found(tmp_path):
    """Returns 0 (success) when no matching projects exist."""
    with patch("shutil.which", return_value="/usr/bin/copier"):
        result = run_copier_update(projects_dir=tmp_path)
    assert result == 0


def test_run_copier_update_dry_run_prints_projects(tmp_path, capsys):
    """--dry-run prints project names and returns 0 without calling copier."""
    for name in ("proj-a", "proj-b"):
        d = tmp_path / name
        d.mkdir()
        _make_answers(d)

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run") as mock_run:
            result = run_copier_update(projects_dir=tmp_path, dry_run=True)

    assert result == 0
    mock_run.assert_not_called()
    out = capsys.readouterr().out
    assert "proj-a" in out
    assert "proj-b" in out
    assert "dry-run" in out


def test_run_copier_update_project_filter_found(tmp_path, capsys):
    """--project filters to the named project only."""
    for name in ("alpha", "beta"):
        d = tmp_path / name
        d.mkdir()
        _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = ""

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                result = run_copier_update(projects_dir=tmp_path, project_filter="alpha", isolate=False)

    assert result == 0
    # copier should be called exactly once, for alpha, with --vcs-ref HEAD
    copier_calls = [c for c in mock_run.call_args_list if "copier" in str(c)]
    assert len(copier_calls) == 1
    call_args = copier_calls[0][0][0]  # positional args list
    assert str(tmp_path / "alpha") in str(copier_calls[0])
    assert "--vcs-ref" in call_args
    assert "HEAD" in call_args


def test_run_copier_update_project_filter_not_found(tmp_path, capsys):
    """--project with unknown name returns 1."""
    d = tmp_path / "alpha"
    d.mkdir()
    _make_answers(d)

    with patch("shutil.which", return_value="/usr/bin/copier"):
        result = run_copier_update(projects_dir=tmp_path, project_filter="nonexistent")

    assert result == 1


def test_run_copier_update_success(tmp_path, capsys):
    """Returns 0 when all projects update successfully."""
    d = tmp_path / "myproj"
    d.mkdir()
    _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                result = run_copier_update(projects_dir=tmp_path, isolate=False)

    assert result == 0
    out = capsys.readouterr().out
    assert "✓" in out
    # Verify --vcs-ref HEAD is passed so untagged template commits are picked up
    cmd = mock_run.call_args[0][0]
    assert "--vcs-ref" in cmd
    assert "HEAD" in cmd


def test_run_copier_update_uses_vcs_ref_head(tmp_path):
    """copier is invoked with --vcs-ref HEAD, not latest tag."""
    d = tmp_path / "myproj"
    d.mkdir()
    _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                run_copier_update(projects_dir=tmp_path, isolate=False)

    cmd = mock_run.call_args[0][0]
    assert "--vcs-ref" in cmd
    idx = cmd.index("--vcs-ref")
    assert cmd[idx + 1] == "HEAD"


def test_run_copier_update_copier_failure(tmp_path, capsys):
    """Returns 1 when copier exits non-zero."""
    d = tmp_path / "myproj"
    d.mkdir()
    _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "something went wrong"

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result):
            result = run_copier_update(projects_dir=tmp_path, isolate=False)

    assert result == 1
    out = capsys.readouterr().out
    assert "✗" in out


def test_run_copier_update_conflict_markers(tmp_path, capsys):
    """Returns 1 when conflict markers are found after update."""
    d = tmp_path / "myproj"
    d.mkdir()
    _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result):
            with patch(
                "ai_cli.copier_update._conflict_files",
                return_value=[str(d / "file.py")],
            ):
                result = run_copier_update(projects_dir=tmp_path, isolate=False)

    assert result == 1
    out = capsys.readouterr().out
    assert "CONFLICTS" in out


def test_run_copier_update_partial_failure(tmp_path, capsys):
    """Returns 1 when at least one project fails; success count still shown."""
    for name in ("ok-proj", "bad-proj"):
        dd = tmp_path / name
        dd.mkdir()
        _make_answers(dd)

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        if kwargs.get("cwd") == tmp_path / "bad-proj":
            r.returncode = 1
            r.stderr = "error"
        else:
            r.returncode = 0
            r.stderr = ""
        return r

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", side_effect=fake_run):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                result = run_copier_update(projects_dir=tmp_path, isolate=False)

    assert result == 1
    out = capsys.readouterr().out
    assert "errors or conflicts" in out


def test_run_copier_update_when_projects_dir_none_then_uses_home_projects(tmp_path):
    """line 54: projects_dir=None defaults to Path.home() / 'projects'."""
    fake_home = tmp_path
    # Create a 'projects' directory under fake home
    projects = fake_home / "projects"
    projects.mkdir()
    with patch("pathlib.Path.home", return_value=fake_home):
        with patch("shutil.which", return_value=None):  # no copier binary
            result = run_copier_update(projects_dir=None)
    # copier not found → returns non-zero, but the default path was used (not crash)
    assert result != 0  # copier not found error


# ---------------------------------------------------------------------------
# Isolated worktree flow (AI-CLI-91)
# ---------------------------------------------------------------------------


def test_repo_root_real_git(tmp_path):
    """_repo_root returns the git top-level for a real repo."""
    import subprocess as sp

    sp.run(["git", "init"], cwd=tmp_path, capture_output=True)
    root = _repo_root(tmp_path)
    assert root is not None
    assert root.resolve() == tmp_path.resolve()


def test_repo_root_non_git(tmp_path):
    """_repo_root returns None outside a git repo."""
    assert _repo_root(tmp_path) is None


def _wt_runner(*, porcelain="", copier_rc=0, push_rc=0):
    """Build a subprocess.run side_effect for _do_update_in_worktree calls."""

    calls = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        if "copier" in str(cmd[0]):
            r.returncode = copier_rc
            r.stderr = "copier boom" if copier_rc else ""
        elif "--porcelain" in cmd:
            r.stdout = porcelain
        elif "push" in cmd:
            r.returncode = push_rc
            r.stderr = "! [rejected]" if push_rc else ""
        return r

    run.calls = calls
    return run


def test_do_update_nochange(tmp_path):
    """copier ok + empty porcelain → nochange."""
    runner = _wt_runner(porcelain="")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "nochange"


def test_do_update_copier_failure(tmp_path):
    """Non-zero copier exit → failed, with stderr surfaced."""
    runner = _wt_runner(copier_rc=1)
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        status, detail = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "failed"
    assert "boom" in detail


def test_do_update_conflict(tmp_path):
    """Changes + conflict markers → conflict with relative paths, no commit."""
    wt = tmp_path / "wt"
    runner = _wt_runner(porcelain=" M docs/x.py\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch(
            "ai_cli.copier_update._conflict_files",
            return_value=[str(wt / "docs" / "x.py")],
        ):
            status, detail = _do_update_in_worktree(wt, tmp_path / "root", "/usr/bin/copier", True)
    assert status == "conflict"
    assert detail == ["docs/x.py"]
    # never committed or pushed on conflict
    assert not any("commit" in c for c in runner.calls)
    assert not any("push" in c for c in runner.calls)


def test_do_update_ok_push(tmp_path):
    """Clean changes + push → ok; pushes HEAD:main and rebases the main tree."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "ok"
    assert any("push" in c and "HEAD:main" in c for c in runner.calls)
    assert any("pull" in c and "--rebase" in c for c in runner.calls)


def test_do_update_ok_no_push(tmp_path):
    """push=False commits but never pushes or rebases."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", False)
    assert status == "ok"
    assert any("commit" in c for c in runner.calls)
    assert not any("push" in c for c in runner.calls)


def test_do_update_pushfail(tmp_path):
    """Push rejected → pushfail with git stderr, worktree/commit preserved by caller."""
    runner = _wt_runner(porcelain=" M file\n", push_rc=1)
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, detail = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "pushfail"
    assert "rejected" in detail


def test_update_one_isolated_not_a_repo(tmp_path):
    """A non-git project dir returns failed without touching worktrees."""
    with patch("ai_cli.copier_update._repo_root", return_value=None):
        status, detail = _update_one_isolated(tmp_path, "/usr/bin/copier")
    assert status == "failed"
    assert "not a git repository" in detail


def test_update_one_isolated_cleans_up_on_ok(tmp_path):
    """On ok, the temp worktree is torn down (stale-clear + teardown = 2 cleanups)."""
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", return_value=ok):
            with patch("ai_cli.copier_update._cleanup_worktree") as mclean:
                with patch("ai_cli.copier_update._do_update_in_worktree", return_value=("ok", "")):
                    status, _ = _update_one_isolated(tmp_path, "/usr/bin/copier")
    assert status == "ok"
    assert mclean.call_count == 2


def test_update_one_isolated_leaves_worktree_on_conflict(tmp_path):
    """On conflict, the temp worktree is left in place (only the stale-clear cleanup runs)."""
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", return_value=ok):
            with patch("ai_cli.copier_update._cleanup_worktree") as mclean:
                with patch(
                    "ai_cli.copier_update._do_update_in_worktree",
                    return_value=("conflict", ["a.py"]),
                ):
                    status, _ = _update_one_isolated(tmp_path, "/usr/bin/copier")
    assert status == "conflict"
    assert mclean.call_count == 1


def test_run_isolated_mixed_results(tmp_path, capsys):
    """_run_isolated aggregates: any conflict → exit 1; ok/nochange are non-fatal."""
    projects = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]

    def fake(pd, cb, push=True):
        return {"a": ("ok", ""), "b": ("nochange", ""), "c": ("conflict", ["x.py"])}[pd.name]

    with patch("ai_cli.copier_update._update_one_isolated", side_effect=fake):
        rc = _run_isolated(projects, "/usr/bin/copier", True)
    assert rc == 1
    out = capsys.readouterr().out
    assert "CONFLICTS" in out
    assert "no changes" in out
    assert "updated + pushed" in out


def test_run_isolated_all_clean(tmp_path, capsys):
    """All ok/nochange → exit 0."""
    projects = [tmp_path / "a", tmp_path / "b"]

    def fake(pd, cb, push=True):
        return ("ok", "") if pd.name == "a" else ("nochange", "")

    with patch("ai_cli.copier_update._update_one_isolated", side_effect=fake):
        rc = _run_isolated(projects, "/usr/bin/copier", True)
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_run_copier_update_isolated_is_default(tmp_path):
    """isolate defaults True → routes to _run_isolated, not _run_direct."""
    d = tmp_path / "proj"
    d.mkdir()
    _make_answers(d)
    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("ai_cli.copier_update._run_isolated", return_value=0) as miso:
            with patch("ai_cli.copier_update._run_direct") as mdir:
                rc = run_copier_update(projects_dir=tmp_path)
    assert rc == 0
    miso.assert_called_once()
    mdir.assert_not_called()


def test_run_copier_update_no_isolate_routes_direct(tmp_path):
    """isolate=False → routes to _run_direct."""
    d = tmp_path / "proj"
    d.mkdir()
    _make_answers(d)
    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("ai_cli.copier_update._run_direct", return_value=0) as mdir:
            with patch("ai_cli.copier_update._run_isolated") as miso:
                rc = run_copier_update(projects_dir=tmp_path, isolate=False)
    assert rc == 0
    mdir.assert_called_once()
    miso.assert_not_called()


def test_run_copier_update_dry_run_shows_isolated_mode(tmp_path, capsys):
    """Dry-run labels the isolated mode."""
    d = tmp_path / "proj"
    d.mkdir()
    _make_answers(d)
    with patch("shutil.which", return_value="/usr/bin/copier"):
        rc = run_copier_update(projects_dir=tmp_path, dry_run=True)
    assert rc == 0
    assert "isolated worktree" in capsys.readouterr().out
