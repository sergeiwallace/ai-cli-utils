"""Tests for ai copier-update subcommand."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import (
    EX_CONFIG,
    EX_PARTIAL_MUTATION,
    EX_TEMPFAIL,
    _changed_paths,
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
    assert conflict_file in {Path(path) for path in result}


def test_conflict_files_returns_empty_when_none(tmp_path):
    """Clean directory returns empty list."""
    (tmp_path / "clean.py").write_text("x = 1\n")
    result = _conflict_files(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# run_copier_update
# ---------------------------------------------------------------------------


def _make_answers(proj_dir: Path, src_path: str = "/projects/project-template") -> None:
    (proj_dir / ".copier-answers.yml").write_text(f"_src_path: {src_path}\n_commit: previous\n")


def test_run_copier_update_projects_dir_not_found(tmp_path):
    """Returns EX_CONFIG when projects_dir does not exist."""
    missing = tmp_path / "no-such-dir"
    result = run_copier_update(projects_dir=missing)
    assert result == EX_CONFIG


def test_run_copier_update_copier_not_in_path(tmp_path):
    """Returns EX_CONFIG when copier binary is not in PATH."""
    with patch("shutil.which", return_value=None):
        result = run_copier_update(projects_dir=tmp_path)
    assert result == EX_CONFIG


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
    copier_calls = [c for c in mock_run.call_args_list if "copier" in str(c.args[0][0])]
    assert len(copier_calls) == 1
    call_args = copier_calls[0][0][0]  # positional args list
    assert copier_calls[0].kwargs["cwd"] == tmp_path / "alpha"
    assert "--vcs-ref" in call_args
    assert "HEAD" in call_args


def test_run_copier_update_project_filter_not_found(tmp_path, capsys):
    """--project with unknown name returns EX_CONFIG."""
    d = tmp_path / "alpha"
    d.mkdir()
    _make_answers(d)

    with patch("shutil.which", return_value="/usr/bin/copier"):
        result = run_copier_update(projects_dir=tmp_path, project_filter="nonexistent")

    assert result == EX_CONFIG


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
    cmd = next(c.args[0] for c in mock_run.call_args_list if "copier" in str(c.args[0][0]))
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

    cmd = next(c.args[0] for c in mock_run.call_args_list if "copier" in str(c.args[0][0]))
    assert "--vcs-ref" in cmd
    idx = cmd.index("--vcs-ref")
    assert cmd[idx + 1] == "HEAD"


def test_run_copier_update_copier_failure(tmp_path, capsys):
    """Returns EX_TEMPFAIL when copier exits non-zero."""
    d = tmp_path / "myproj"
    d.mkdir()
    _make_answers(d)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "something went wrong"

    with patch("shutil.which", return_value="/usr/bin/copier"):
        with patch("subprocess.run", return_value=mock_result):
            result = run_copier_update(projects_dir=tmp_path, isolate=False)

    assert result == EX_TEMPFAIL
    out = capsys.readouterr().out
    assert "✗" in out


def test_run_copier_update_conflict_markers(tmp_path, capsys):
    """Returns EX_PARTIAL_MUTATION when conflict markers are found after update."""
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

    assert result == EX_PARTIAL_MUTATION
    out = capsys.readouterr().out
    assert "CONFLICTS" in out


def test_run_copier_update_partial_failure(tmp_path, capsys):
    """Returns EX_TEMPFAIL when at least one project fails; success count still shown."""
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

    assert result == EX_TEMPFAIL
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

    sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
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
        with patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "nochange"


def test_do_update_copier_failure(tmp_path):
    """Non-zero copier exit → failed, with stderr surfaced."""
    runner = _wt_runner(copier_rc=1)
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._run_copier_update", return_value=("copier boom", None, None)):
            status, detail = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status != "ok"
    assert "boom" in detail


def test_do_update_conflict(tmp_path):
    """Changes + conflict markers → conflict with relative paths, no commit."""
    wt = tmp_path / "wt"
    runner = _wt_runner(porcelain=" M docs/x.py\n")
    with (
        patch("ai_cli.copier_update.subprocess.run", side_effect=runner),
        patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))),
        patch(
            "ai_cli.copier_update._conflict_files",
            return_value=[str(wt / "docs" / "x.py")],
        ),
    ):
        status, detail = _do_update_in_worktree(wt, tmp_path / "root", "/usr/bin/copier", True)
    assert status == "conflict"
    assert [Path(path) for path in detail] == [Path("docs") / "x.py"]
    # never committed or pushed on conflict
    assert not any("commit" in c for c in runner.calls)
    assert not any("push" in c for c in runner.calls)


def test_do_update_ok_push(tmp_path):
    """Clean changes + push → ok; pushes HEAD:main and rebases the main tree."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "ok"
    assert any("push" in c and "HEAD:main" in c for c in runner.calls)
    assert any("pull" in c and "--rebase" in c for c in runner.calls)


def test_do_update_ok_no_push(tmp_path):
    """push=False commits but never pushes or rebases."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", False)
    assert status == "ok"
    assert any("commit" in c for c in runner.calls)
    assert not any("push" in c for c in runner.calls)


def test_given_stored_answers_when_updating_then_passes_them_to_copier(tmp_path):
    """Stored answers override defaults, including non-default project choices."""
    template = tmp_path / "template"
    template.mkdir()
    subprocess.run(["git", "init"], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=template, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=template, check=True)
    (template / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "message.txt"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    template_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text(f"_src_path: {template}\n_commit: {template_commit}\nfeature_enabled: true\n")
    runner = _wt_runner(porcelain=" M file\n")

    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            _do_update_in_worktree(tmp_path, tmp_path / "root", "/usr/bin/copier", False)

    copier_command = next(command for command in runner.calls if command[0] == "/usr/bin/copier")
    assert "--data-file" in copier_command
    assert copier_command[copier_command.index("--data-file") + 1] == str(answers)


def test_given_drifted_template_hunk_when_copier_exits_cleanly_then_update_fails_closed(tmp_path):
    """A clean copier exit must not commit when a template hunk was not applied."""
    template = tmp_path / "template"
    template.mkdir()
    subprocess.run(["git", "init"], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=template, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=template, check=True)
    (template / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "message.txt"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    old_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    (template / "message.txt").write_text("updated\n")
    subprocess.run(["git", "commit", "-am", "update message"], cwd=template, check=True, capture_output=True)

    wt_dir = tmp_path / "project"
    wt_dir.mkdir()
    (wt_dir / "message.txt").write_text("custom project content\n")
    (wt_dir / ".copier-answers.yml").write_text(
        f"_src_path: {template}\n_commit: {old_commit}\nfeature_enabled: true\n"
    )
    real_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/copier":
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[-1:] == ["--porcelain"]:
            return MagicMock(returncode=0, stdout=" M .copier-answers.yml\n", stderr="")
        if "commit" in command:
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, detail = _do_update_in_worktree(wt_dir, tmp_path / "root", "/usr/bin/copier", False)

    assert status != "ok"
    assert "parity" in detail
    assert not any("commit" in command for command in calls)


def test_given_non_default_answer_when_copier_resets_it_then_update_fails_closed(tmp_path):
    """A successful subprocess cannot replace a stored non-default answer."""
    template = tmp_path / "template"
    template.mkdir()
    subprocess.run(["git", "init"], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=template, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=template, check=True)
    (template / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "message.txt"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    template_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()

    answers = tmp_path / ".copier-answers.yml"
    answers.write_text(f"_src_path: {template}\n_commit: {template_commit}\nfeature_enabled: true\n")

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            answers.write_text(f"_src_path: {template}\n_commit: {template_commit}\nfeature_enabled: false\n")
        return MagicMock(returncode=0, stdout=" M .copier-answers.yml\n", stderr="")

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        status, detail = _do_update_in_worktree(tmp_path, tmp_path / "root", "/usr/bin/copier", False)

    assert status != "ok"
    assert "stored Copier answers" in detail


def test_given_relative_source_when_isolating_then_passes_absolute_source_to_worktree(tmp_path):
    """Relative Copier sources are resolved before the isolated cwd changes."""
    (tmp_path / ".copier-answers.yml").write_text("_src_path: ../template\n_commit: previous\n")
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", return_value=ok):
            with patch("ai_cli.copier_update._cleanup_worktree"):
                with patch("ai_cli.copier_update._do_update_in_worktree", return_value=("nochange", "")) as update:
                    _update_one_isolated(tmp_path, "/usr/bin/copier")

    assert update.call_args.args[-1] == str((tmp_path / "../template").resolve())


def test_given_applied_template_hunk_when_verifying_parity_then_update_commits(tmp_path):
    """A matching template hunk is accepted rather than treated as a conflict."""
    template = tmp_path / "template"
    project = tmp_path / "project"
    for directory in (template, project):
        directory.mkdir()
        subprocess.run(["git", "init"], cwd=directory, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=directory, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=directory, check=True)

    (template / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "message.txt"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    old_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    (template / "message.txt").write_text("updated\n")
    subprocess.run(["git", "commit", "-am", "update message"], cwd=template, check=True, capture_output=True)

    (project / "message.txt").write_text("original\n")
    (project / ".copier-answers.yml").write_text(
        f"_src_path: {template}\n_commit: {old_commit}\nfeature_enabled: true\n"
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial project"], cwd=project, check=True, capture_output=True)

    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            (project / "message.txt").write_text("updated\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        status, detail = _do_update_in_worktree(project, tmp_path / "root", "/usr/bin/copier", False)

    assert (status, detail) == ("ok", "")


def test_do_update_pushfail(tmp_path):
    """Push rejected → pushfail with git stderr, worktree/commit preserved by caller."""
    runner = _wt_runner(porcelain=" M file\n", push_rc=1)
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))):
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
    _make_answers(tmp_path)
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
    _make_answers(tmp_path)
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
    """_run_isolated aggregates: any conflict → EX_PARTIAL_MUTATION."""
    projects = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]

    def fake(pd, cb, push=True):
        return {"a": ("ok", ""), "b": ("nochange", ""), "c": ("conflict", ["x.py"])}[pd.name]

    with patch("ai_cli.copier_update._update_one_isolated", side_effect=fake):
        rc = _run_isolated(projects, "/usr/bin/copier", True)
    assert rc == EX_PARTIAL_MUTATION
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


def test_given_prerequisite_failure_when_isolated_updates_run_then_returns_ex_config(tmp_path):
    with patch("ai_cli.copier_update._update_one_isolated", return_value=("failed", "not a git repository")):
        rc = _run_isolated([tmp_path / "project"], "/usr/bin/copier", True)

    assert rc == EX_CONFIG


def test_given_transient_failure_when_isolated_updates_run_then_returns_ex_tempfail(tmp_path):
    with patch("ai_cli.copier_update._update_one_isolated", return_value=("failed", "copier update failed")):
        rc = _run_isolated([tmp_path / "project"], "/usr/bin/copier", True)

    assert rc == EX_TEMPFAIL


def test_given_partial_and_clean_failures_when_isolated_updates_run_then_partial_takes_precedence(tmp_path):
    projects = [tmp_path / "partial", tmp_path / "config"]

    def fake(project_dir, copier_bin, push=True):
        if project_dir.name == "partial":
            return "pushfail", "push rejected"
        return "failed", "worktree add failed: branch already exists"

    with patch("ai_cli.copier_update._update_one_isolated", side_effect=fake):
        rc = _run_isolated(projects, "/usr/bin/copier", True)

    assert rc == EX_PARTIAL_MUTATION


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


# ---------------------------------------------------------------------------
# Scoped conflict detection (false-positive regression, AI-CLI-91)
# ---------------------------------------------------------------------------


def test_changed_paths_parses_porcelain(tmp_path):
    """_changed_paths turns porcelain lines into absolute paths, handling renames."""
    porcelain = ' M src/a.py\n?? new.txt\nR  old.py -> src/b.py\n M "quoted name.py"\n'
    result = _changed_paths(porcelain, tmp_path)
    assert str(tmp_path / "src/a.py") in result
    assert str(tmp_path / "new.txt") in result
    assert str(tmp_path / "src/b.py") in result  # rename → new path
    assert str(tmp_path / "old.py") not in result
    assert str(tmp_path / "quoted name.py") in result


def test_conflict_files_scoped_ignores_unchanged_marker_file(tmp_path):
    """A file that merely CONTAINS <<<<<<< but wasn't changed is NOT a conflict."""
    fixture = tmp_path / "test_fixture.py"
    fixture.write_text('marker = "<<<<<<< HEAD\\nfoo\\n=======\\nbar\\n>>>>>>>"\n')
    changed = tmp_path / "changed.txt"
    changed.write_text("clean content\n")
    # scope = only the changed file (no markers) → fixture must be ignored
    result = _conflict_files(tmp_path, [str(changed)])
    assert result == []


def test_conflict_files_scoped_flags_real_conflict(tmp_path):
    """A changed file that DOES contain markers is reported."""
    conflicted = tmp_path / "doc.md"
    conflicted.write_text("<<<<<<< before\nx\n=======\ny\n>>>>>>> after\n")
    result = _conflict_files(tmp_path, [str(conflicted)])
    assert str(conflicted) in result


def test_conflict_files_scoped_empty_paths(tmp_path):
    """Empty changed set → no conflicts (nothing to scan)."""
    (tmp_path / "marker.py").write_text("<<<<<<<\n")
    assert _conflict_files(tmp_path, []) == []


def test_conflict_files_whole_tree_still_works(tmp_path):
    """paths=None preserves the original whole-tree scan (legacy direct mode)."""
    (tmp_path / "c.py").write_text("<<<<<<< HEAD\n")
    result = _conflict_files(tmp_path)
    assert tmp_path / "c.py" in {Path(path) for path in result}


def test_do_update_ignores_unchanged_marker_files(tmp_path):
    """Regression: copier changes only clean files; an unchanged fixture with markers
    elsewhere in the tree must NOT trigger a false conflict."""
    wt = tmp_path / "wt"
    wt.mkdir()
    # copier changed only this clean file
    (wt / "reasoning.md").write_text("clean rule text\n")
    # an unrelated, UNCHANGED file that references a conflict marker as content
    (wt / "test_fixture.py").write_text('m = "<<<<<<< HEAD"\n')

    def run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        if "--porcelain" in cmd:
            r.stdout = " M reasoning.md\n"  # only the clean file changed
        return r

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        with patch("ai_cli.copier_update._run_copier_update", return_value=(None, {}, (set(), set()))):
            status, _ = _do_update_in_worktree(wt, tmp_path / "root", "/usr/bin/copier", False)
    assert status == "ok"  # NOT "conflict"
