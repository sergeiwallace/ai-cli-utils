"""Tests for ai copier-update subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import _conflict_files, _find_copier_projects, run_copier_update


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


def test_find_copier_projects_yaml_not_installed(tmp_path):
    """Returns empty list and prints error when PyYAML is missing."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".copier-answers.yml").write_text("_src_path: /projects/project-template\n")

    import ai_cli.copier_update as mod

    original = mod.yaml
    mod.yaml = None
    try:
        result = _find_copier_projects(tmp_path)
    finally:
        mod.yaml = original

    assert result == []


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
                result = run_copier_update(projects_dir=tmp_path, project_filter="alpha")

    assert result == 0
    # copier should be called exactly once, for alpha
    copier_calls = [c for c in mock_run.call_args_list if "copier" in str(c)]
    assert len(copier_calls) == 1
    assert str(tmp_path / "alpha") in str(copier_calls[0])


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
        with patch("subprocess.run", return_value=mock_result):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                result = run_copier_update(projects_dir=tmp_path)

    assert result == 0
    out = capsys.readouterr().out
    assert "✓" in out


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
            result = run_copier_update(projects_dir=tmp_path)

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
                result = run_copier_update(projects_dir=tmp_path)

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
                result = run_copier_update(projects_dir=tmp_path)

    assert result == 1
    out = capsys.readouterr().out
    assert "errors or conflicts" in out
