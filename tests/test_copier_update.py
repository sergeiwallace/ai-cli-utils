"""Tests for ai copier-update subcommand."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from conftest import run_cli

from ai_cli.copier_update import (
    EX_CONFIG,
    EX_PARTIAL_MUTATION,
    EX_TEMPFAIL,
    CopierUpdateInspection,
    DeliveredUpdate,
    RecoveredPushFailure,
    TemplateChanges,
    _changed_paths,
    _conflict_files,
    _do_update_in_worktree,
    _excluded_on_update,
    _find_copier_projects,
    _reachable_source,
    _repo_root,
    _resolve_copier_source,
    _run_copier_update,
    _run_isolated,
    _template_diff,
    _update_one_isolated,
    _verify_update_parity,
    run_copier_update,
    write_copier_update_inspection,
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


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def test_given_subdirectory_template_changes_when_verifying_parity_then_ignores_template_repository_files(tmp_path):
    """Only rendered subdirectory changes are required in a Copier destination."""
    template = tmp_path / "template"
    template.mkdir()
    subprocess.run(["git", "init"], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=template, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=template, check=True)
    (template / "copier.yml").write_text("_subdirectory: template\n_skip_if_exists:\n  - .gitignore\n")
    (template / "template").mkdir()
    (template / "template" / ".gitignore.jinja").write_text(".cache/\n")
    (template / "pyproject.toml").write_text("[project]\nname = 'template'\n")
    subprocess.run(["git", "add", "."], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    previous_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    (template / "template" / ".gitignore.jinja").write_text(".cache/\n.build/\n")
    (template / "pyproject.toml").write_text("[project]\nname = 'template-next'\n")
    subprocess.run(["git", "add", "."], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "update"], cwd=template, check=True, capture_output=True)

    template_changes = _template_diff(str(template), previous_commit)

    assert template_changes is not None
    template_paths = template_changes.paths
    template_hunks = template_changes.hunks
    assert template_paths == {".gitignore"}
    assert {hunk[0] for hunk in template_hunks} == {".gitignore"}
    assert template_changes.skip_if_exists == {".gitignore"}

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    subprocess.run(["git", "init"], cwd=consumer, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=consumer, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=consumer, check=True)
    (consumer / ".gitignore").write_text(".cache/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=consumer, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=consumer, check=True, capture_output=True)
    (consumer / ".gitignore").write_text(".cache/\n.build/\n")
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=consumer, check=True, capture_output=True, text=True
    ).stdout

    assert (
        _verify_update_parity(
            consumer,
            porcelain,
            template_changes.paths,
            template_changes.hunks,
            template_changes.skip_if_exists,
            template_changes.deleted_paths,
            template_changes.excluded_on_update,
        )
        is None
    )


def test_given_existing_skip_if_exists_path_when_verifying_parity_then_accepts_copier_omission(tmp_path):
    """A configured existing-file skip is not a missing Copier update."""
    (tmp_path / ".gitignore").write_text("local-rule\n")
    _init_git_repo(tmp_path)

    assert (
        _verify_update_parity(
            tmp_path,
            "",
            {".gitignore"},
            {(".gitignore", ("old-rule",), ("new-rule",))},
            skip_if_exists={".gitignore"},
        )
        is None
    )


def test_given_nameless_skip_entry_when_a_nested_file_exists_then_parity_accepts_copier_omission(tmp_path):
    """``README.md`` in _skip_if_exists covers a README at any depth, as Copier reads it."""
    (tmp_path / "docs" / "audits").mkdir(parents=True)
    (tmp_path / "docs" / "audits" / "README.md").write_text("project text\n")
    _init_git_repo(tmp_path)

    assert (
        _verify_update_parity(
            tmp_path,
            "",
            {"docs/audits/README.md"},
            {("docs/audits/README.md", ("old",), ("new",))},
            skip_if_exists={"README.md"},
        )
        is None
    )


def test_given_nameless_skip_entry_when_the_nested_file_is_absent_then_parity_still_reports_it(tmp_path):
    (tmp_path / ".keep").write_text("keep\n")
    _init_git_repo(tmp_path)

    error = _verify_update_parity(
        tmp_path,
        "",
        {"docs/audits/README.md"},
        {("docs/audits/README.md", ("old",), ("new",))},
        skip_if_exists={"README.md"},
    )
    assert error is not None and "docs/audits/README.md" in error


def test_given_slash_skip_entry_then_it_matches_the_whole_path_only(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("project text\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.md").write_text("project text\n")
    _init_git_repo(tmp_path)

    error = _verify_update_parity(
        tmp_path,
        "",
        {"docs/notes.md", "other/notes.md"},
        set(),
        skip_if_exists={"docs/*.md"},
    )
    assert error is not None
    assert "other/notes.md" in error and "docs/notes.md" not in error


def test_given_already_absent_template_deletion_when_verifying_parity_then_accepts_convergence(tmp_path):
    """A deletion already reflected downstream is not an omitted update."""
    (tmp_path / ".keep").write_text("keep\n")
    _init_git_repo(tmp_path)

    assert (
        _verify_update_parity(
            tmp_path,
            "",
            {"docs/obsolete.md"},
            {("docs/obsolete.md", ("obsolete",), ())},
            deleted_template_paths={"docs/obsolete.md"},
        )
        is None
    )


def test_given_update_only_excluded_path_when_verifying_parity_then_accepts_copier_omission(tmp_path):
    """A static Copier update exclusion is not a missing template change."""
    template = tmp_path / "template"
    template.mkdir()
    subprocess.run(["git", "init"], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=template, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=template, check=True)
    (template / "copier.yml").write_text(
        "_exclude:\n  - \"{% if _copier_operation != 'copy' %}pytest.ini{% endif %}\"\n"
    )
    (template / "pytest.ini.jinja").write_text("[pytest]\n")
    subprocess.run(["git", "add", "."], cwd=template, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    previous_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    (template / "pytest.ini.jinja").write_text("[pytest]\naddopts = -q\n")
    subprocess.run(["git", "commit", "-am", "update"], cwd=template, check=True, capture_output=True)

    template_changes = _template_diff(str(template), previous_commit)

    assert template_changes is not None
    assert template_changes.excluded_on_update == {"pytest.ini"}

    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".keep").write_text("keep\n")
    _init_git_repo(tmp_path / "consumer")
    assert (
        _verify_update_parity(
            tmp_path / "consumer",
            "",
            template_changes.paths,
            template_changes.hunks,
            template_changes.skip_if_exists,
            template_changes.deleted_paths,
            template_changes.excluded_on_update,
        )
        is None
    )


def test_given_unskipped_present_template_path_when_verifying_parity_then_reports_omission(tmp_path):
    """A non-skipped template change still requires a downstream change."""

    error = _verify_update_parity(tmp_path, "", {"settings.toml"}, set())

    assert error == "template parity failed: missing changed file(s): settings.toml"


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


def _copier_sees_status(project: Path, answers_source: str, resolved: str) -> tuple[str, str, str]:
    """Run ``_run_copier_update`` with a stand-in copier; return the status copier saw and the file before and after."""
    _make_answers(project, answers_source)
    _init_git_repo(project)
    real_run = subprocess.run
    seen: dict[str, str] = {}

    def run(command, **kwargs):
        if command[0] == "fake-copier":
            seen["status"] = real_run(
                ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True, check=True
            ).stdout
            seen["answers"] = (project / ".copier-answers.yml").read_text()
            return MagicMock(returncode=0, stderr="")
        return real_run(command, **kwargs)

    changes = TemplateChanges(paths=set(), hunks=set(), skip_if_exists=set(), deleted_paths=set())
    with patch("ai_cli.copier_update._template_diff", return_value=changes):
        with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
            failure, _, _ = _run_copier_update(project, "fake-copier", resolved)
    assert failure is None
    return seen["status"], seen["answers"], (project / ".copier-answers.yml").read_text()


def test_given_resolved_source_differs_from_stored_then_copier_sees_a_clean_checkout_on_the_resolved_source(tmp_path):
    """A rewritten source must not make the destination dirty: copier refuses to update a dirty one."""
    status, during, after = _copier_sees_status(
        tmp_path,
        "git@github.com:example-org/example-template.git",
        "https://github.com/example-org/example-template.git",
    )
    assert status == ""
    assert "https://github.com/example-org/example-template.git" in during
    assert "git@github.com:example-org/example-template.git" in after


def test_given_resolved_source_differs_from_stored_then_the_hidden_flag_is_cleared_afterwards(tmp_path):
    _copier_sees_status(
        tmp_path,
        "git@github.com:example-org/example-template.git",
        "https://github.com/example-org/example-template.git",
    )
    listing = subprocess.run(["git", "ls-files", "-v"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert ".copier-answers.yml" in listing
    assert not any(line.startswith("S ") for line in listing.splitlines())


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
        with patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ):
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
    assert isinstance(detail, str)
    assert "boom" in detail


def test_do_update_conflict(tmp_path):
    """Changes + conflict markers → conflict with relative paths, no commit."""
    wt = tmp_path / "wt"
    runner = _wt_runner(porcelain=" M docs/x.py\n")
    with (
        patch("ai_cli.copier_update.subprocess.run", side_effect=runner),
        patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ),
        patch(
            "ai_cli.copier_update._conflict_files",
            return_value=[str(wt / "docs" / "x.py")],
        ),
    ):
        status, detail = _do_update_in_worktree(wt, tmp_path / "root", "/usr/bin/copier", True)
    assert status == "conflict"
    assert isinstance(detail, list)
    assert [Path(path) for path in detail] == [Path("docs") / "x.py"]
    # never committed or pushed on conflict
    assert not any("commit" in c for c in runner.calls)
    assert not any("push" in c for c in runner.calls)


def test_do_update_ok_push(tmp_path):
    """Clean changes + push → ok; pushes HEAD:main and rebases the main tree."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                status, _ = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "ok"
    assert any("push" in c and "HEAD:main" in c for c in runner.calls)
    assert any("pull" in c and "--rebase" in c for c in runner.calls)


def test_do_update_ok_no_push(tmp_path):
    """push=False commits but never pushes or rebases."""
    runner = _wt_runner(porcelain=" M file\n")
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ):
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


def test_given_non_ascii_answers_when_source_is_unchanged_then_copier_starts_clean(tmp_path):
    """The temporary Copier data-file write must not dirty a tracked answers file."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    source = str(tmp_path / "template")
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text(
        f"_src_path: {source}\n"
        "_commit: previous\n"
        "project_description: Automated example — bridge to autonomy\n"
        "project_vision: ''\n"
    )
    original_bytes = answers.read_bytes()
    subprocess.run(["git", "add", ".copier-answers.yml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    real_run = subprocess.run

    def run_copier(command, **kwargs):
        status = real_run(["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True)
        assert status.stdout == ""
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("ai_cli.copier_update._template_diff", return_value=TemplateChanges(set(), set(), set(), set())):
        with patch("ai_cli.copier_update.subprocess.run", side_effect=run_copier):
            failure, _, _ = _run_copier_update(tmp_path, "/usr/bin/copier", resolved_source=source)

    assert failure is None
    assert answers.read_bytes() == original_bytes


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
        if "status" in command and "--porcelain" in command:
            return MagicMock(returncode=0, stdout=" M .copier-answers.yml\n", stderr="")
        if "commit" in command:
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        with patch("ai_cli.copier_update._conflict_files", return_value=[]):
            status, detail = _do_update_in_worktree(wt_dir, tmp_path / "root", "/usr/bin/copier", False)

    assert status != "ok"
    assert isinstance(detail, str)
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
    assert isinstance(detail, str)
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


def test_given_template_file_in_new_directory_when_verifying_parity_then_update_commits(tmp_path):
    """A file Copier adds inside a directory the project lacks counts as changed.

    `git status --porcelain` collapses a wholly new directory to one `?? dir/` entry, which
    made the parity check report the file missing for every project the first time a template
    added a directory (`.claude/skills/lint-md/SKILL.md`, fleet-wide).
    """
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
    (template / "skills" / "lint").mkdir(parents=True)
    (template / "skills" / "lint" / "SKILL.md").write_text("skill\n")
    subprocess.run(["git", "add", "skills"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "add a skill"], cwd=template, check=True, capture_output=True)

    (project / "message.txt").write_text("original\n")
    (project / ".copier-answers.yml").write_text(
        f"_src_path: {template}\n_commit: {old_commit}\nfeature_enabled: true\n"
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial project"], cwd=project, check=True, capture_output=True)

    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            (project / "skills" / "lint").mkdir(parents=True)
            (project / "skills" / "lint" / "SKILL.md").write_text("skill\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        status, detail = _do_update_in_worktree(project, tmp_path / "root", "/usr/bin/copier", False)

    assert (status, detail) == ("ok", "")


def _project_with_added_skill(tmp_path: Path) -> tuple[Path, Path]:
    """A template that adds skills/lint/SKILL.md and a project one commit behind it."""
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
    (template / "skills" / "lint").mkdir(parents=True)
    (template / "skills" / "lint" / "SKILL.md").write_text("skill\n")
    subprocess.run(["git", "add", "skills"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "add a skill"], cwd=template, check=True, capture_output=True)
    (project / "message.txt").write_text("original\n")
    (project / ".copier-answers.yml").write_text(
        f"_src_path: {template}\n_commit: {old_commit}\nfeature_enabled: true\n"
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial project"], cwd=project, check=True, capture_output=True)
    return template, project


def _update_with_pre_commit_hook(tmp_path: Path, hook_body: str) -> tuple[str, str, Path]:
    _, project = _project_with_added_skill(tmp_path)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + hook_body)
    hook.chmod(0o755)
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            (project / "skills" / "lint").mkdir(parents=True)
            (project / "skills" / "lint" / "SKILL.md").write_text("skill\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        status, detail = _do_update_in_worktree(project, tmp_path / "root", "/usr/bin/copier", False)
    return status, str(detail), project


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_pre_commit_hook_that_reformats_files_when_committing_update_then_it_retries_and_commits(tmp_path):
    """A formatter hook aborts the first commit but leaves its fixes; the update commits them."""
    status, detail, project = _update_with_pre_commit_hook(
        tmp_path,
        "if [ ! -f .git/hook-ran ]; then touch .git/hook-ran; echo formatted >> skills/lint/SKILL.md; exit 1; fi\n",
    )
    assert (status, detail) == ("ok", "")
    committed = subprocess.run(
        ["git", "show", "HEAD:skills/lint/SKILL.md"], cwd=project, check=True, capture_output=True, text=True
    ).stdout
    assert committed == "skill\nformatted\n"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_pre_commit_hook_that_keeps_rewriting_and_failing_when_committing_update_then_it_retries_once(tmp_path):
    """A hook that rewrites files and fails every time is not looped on: one retry, then the update fails."""
    status, detail, project = _update_with_pre_commit_hook(
        tmp_path,
        'echo x >> .git/hook-count; echo y >> skills/lint/SKILL.md; echo "hook says no" >&2; exit 1\n',
    )
    assert status == "failed"
    assert "hook says no" in detail
    assert (project / ".git" / "hook-count").read_text().count("x") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_pre_commit_hook_that_fails_without_rewriting_when_committing_update_then_it_does_not_retry(tmp_path):
    """A hook rejecting the content (no rewrite) is a real failure, not an autofix: no second attempt."""
    status, detail, project = _update_with_pre_commit_hook(
        tmp_path, 'echo x >> .git/hook-count; echo "hook says no" >&2; exit 1\n'
    )
    assert status == "failed"
    assert "hook says no" in detail
    assert (project / ".git" / "hook-count").read_text().count("x") == 1


_NEEDS_DEV_TOOL_HOOK = (
    "echo x >> .git/hook-count\n"
    '[ -x node_modules/.bin/lint-tool ] || { echo "sh: 1: lint-tool: not found" >&2; exit 127; }\n'
)


def _js_consumer_update(
    tmp_path: Path,
    lockfile: str | None = "package-lock.json",
    install_returncode: int = 0,
    installers_on_path: bool = True,
    ignore_node_modules: bool = True,
    with_package_json: bool = True,
) -> tuple[str, str, Path, list[tuple[list[str], str | None]]]:
    """Update a consumer whose pre-commit hook needs a dev tool only a dependency install provides."""
    _, project = _project_with_added_skill(tmp_path)
    if with_package_json:
        (project / "package.json").write_text('{"name": "myproject", "devDependencies": {"lint-tool": "1.0.0"}}\n')
    if lockfile is not None:
        (project / lockfile).write_text("lockfile\n")
    if ignore_node_modules:
        (project / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "add package files"], cwd=project, check=True, capture_output=True)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + _NEEDS_DEV_TOOL_HOOK)
    hook.chmod(0o755)
    installs: list[tuple[list[str], str | None]] = []
    real_run = subprocess.run
    real_which = shutil.which

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            (project / "skills" / "lint").mkdir(parents=True)
            (project / "skills" / "lint" / "SKILL.md").write_text("skill\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[0].startswith("/opt/fake-bin/"):
            cwd = kwargs.get("cwd")
            installs.append((command, None if cwd is None else str(cwd)))
            if install_returncode == 0:
                tool = project / "node_modules" / ".bin" / "lint-tool"
                tool.parent.mkdir(parents=True)
                tool.write_text("#!/bin/sh\n")
                tool.chmod(0o755)
            return MagicMock(returncode=install_returncode, stdout="", stderr="npm ERR! network unreachable")
        return real_run(command, **kwargs)

    def which(name, *args, **kwargs):
        if name in ("npm", "pnpm", "yarn"):
            return f"/opt/fake-bin/{name}" if installers_on_path else None
        return real_which(name, *args, **kwargs)

    with (
        patch("ai_cli.copier_update.subprocess.run", side_effect=run),
        patch("ai_cli.copier_update.shutil.which", side_effect=which),
    ):
        status, detail = _do_update_in_worktree(project, tmp_path / "root", "/usr/bin/copier", False)
    return status, str(detail), project, installs


def _head_subject(project: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [
        ("package-lock.json", ["/opt/fake-bin/npm", "ci"]),
        ("pnpm-lock.yaml", ["/opt/fake-bin/pnpm", "install", "--frozen-lockfile"]),
        ("yarn.lock", ["/opt/fake-bin/yarn", "install", "--frozen-lockfile"]),
    ],
)
def test_given_js_lockfile_and_hook_needing_dev_dependency_when_committing_update_then_it_installs_and_commits(
    tmp_path, lockfile, expected
):
    """The isolated tree has no node_modules; the lockfile's installer fills it so the hook finds its tool."""
    status, detail, project, installs = _js_consumer_update(tmp_path, lockfile=lockfile)
    assert (status, detail) == ("ok", "")
    assert installs == [(expected, str(project))]
    assert _head_subject(project) != "add package files"
    tracked = subprocess.run(
        ["git", "ls-files", "node_modules"], cwd=project, check=True, capture_output=True, text=True
    ).stdout
    assert tracked == ""


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_dependency_install_fails_when_committing_update_then_project_fails_naming_command_without_commit(
    tmp_path,
):
    """A failed install is reported as such, not as the hook's later "not found", and nothing is committed."""
    status, detail, project, _ = _js_consumer_update(tmp_path, install_returncode=1)
    assert status == "failed"
    assert "npm ci" in detail
    assert "exit status 1" in detail
    assert "network unreachable" in detail
    assert not (project / ".git" / "hook-count").exists()
    assert _head_subject(project) == "add package files"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_installer_not_on_path_when_committing_update_then_project_fails_naming_installer(tmp_path):
    """A lockfile whose installer is missing fails the project before any commit attempt."""
    status, detail, project, installs = _js_consumer_update(
        tmp_path, lockfile="pnpm-lock.yaml", installers_on_path=False
    )
    assert status == "failed"
    assert "pnpm" in detail and "not found" in detail
    assert installs == []
    assert not (project / ".git" / "hook-count").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_node_modules_not_gitignored_when_committing_update_then_project_fails_without_committing_it(
    tmp_path,
):
    """Installed dependencies are never staged: an unignored node_modules fails the project instead."""
    status, detail, project, _ = _js_consumer_update(tmp_path, ignore_node_modules=False)
    assert status == "failed"
    assert "node_modules" in detail and "ignored" in detail
    assert _head_subject(project) == "add package files"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_no_package_json_when_committing_update_then_nothing_is_installed(tmp_path):
    """Without a package.json the update behaves as before: no installer runs."""
    status, detail, _, installs = _js_consumer_update(tmp_path, lockfile=None, with_package_json=False)
    assert installs == []
    assert status == "failed"
    assert "lint-tool: not found" in detail


_NEEDS_PYTHON_DEV_DEPENDENCY_HOOK = (
    "echo x >> .git/hook-count\n"
    "[ -x .venv/bin/dev-tool ] || "
    "{ echo \"ModuleNotFoundError: No module named 'yaml'\" >&2; exit 1; }\n"
)


def _uv_consumer_update(
    tmp_path: Path,
    with_lockfile: bool = True,
    sync_returncode: int = 0,
    uv_on_path: bool = True,
    venv_self_ignored: bool = True,
) -> tuple[str, str, Path, list[tuple[list[str], str | None]]]:
    """Update a uv consumer whose pre-commit hook needs a dev dependency only a sync installs."""
    _, project = _project_with_added_skill(tmp_path)
    (project / "pyproject.toml").write_text('[project]\nname = "myproject"\nversion = "0.1.0"\n')
    if with_lockfile:
        (project / "uv.lock").write_text("version = 1\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "add python project files"], cwd=project, check=True, capture_output=True)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + _NEEDS_PYTHON_DEV_DEPENDENCY_HOOK)
    hook.chmod(0o755)
    syncs: list[tuple[list[str], str | None]] = []
    real_run = subprocess.run
    real_which = shutil.which

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            (project / "skills" / "lint").mkdir(parents=True)
            (project / "skills" / "lint" / "SKILL.md").write_text("skill\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[0] == "/opt/fake-bin/uv":
            cwd = kwargs.get("cwd")
            syncs.append((command, None if cwd is None else str(cwd)))
            if sync_returncode == 0:
                tool = project / ".venv" / "bin" / "dev-tool"
                tool.parent.mkdir(parents=True)
                tool.write_text("#!/bin/sh\n")
                tool.chmod(0o755)
                if venv_self_ignored:
                    # uv writes this into every virtual environment it creates.
                    (project / ".venv" / ".gitignore").write_text("*\n")
            return MagicMock(
                returncode=sync_returncode, stdout="", stderr="error: Failed to fetch: `https://example.com/simple/`"
            )
        return real_run(command, **kwargs)

    def which(name, *args, **kwargs):
        if name == "uv":
            return "/opt/fake-bin/uv" if uv_on_path else None
        return real_which(name, *args, **kwargs)

    with (
        patch("ai_cli.copier_update.subprocess.run", side_effect=run),
        patch("ai_cli.copier_update.shutil.which", side_effect=which),
    ):
        status, detail = _do_update_in_worktree(project, tmp_path / "root", "/usr/bin/copier", False)
    return status, str(detail), project, syncs


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_uv_lock_and_hook_needing_python_dev_dependency_when_committing_update_then_it_syncs_and_commits(
    tmp_path,
):
    """The isolated tree has no synced .venv; a frozen uv sync fills it so the hook finds its dependency."""
    status, detail, project, syncs = _uv_consumer_update(tmp_path)
    assert (status, detail) == ("ok", "")
    assert syncs == [(["/opt/fake-bin/uv", "sync", "--frozen"], str(project))]
    assert _head_subject(project) != "add python project files"
    tracked = subprocess.run(
        ["git", "ls-files", ".venv"], cwd=project, check=True, capture_output=True, text=True
    ).stdout
    assert tracked == ""


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_uv_not_on_path_when_committing_update_then_project_fails_naming_uv_before_any_commit(tmp_path):
    """A uv.lock whose installer is missing fails the project with that cause, not the hook's import error."""
    status, detail, project, syncs = _uv_consumer_update(tmp_path, uv_on_path=False)
    assert status == "failed"
    assert "uv not found" in detail and "uv.lock" in detail
    assert "ModuleNotFoundError" not in detail
    assert syncs == []
    assert not (project / ".git" / "hook-count").exists()
    assert _head_subject(project) == "add python project files"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_uv_sync_fails_when_committing_update_then_project_fails_naming_command_without_commit(tmp_path):
    """A failed sync is reported as such, with uv's own output, and nothing is committed."""
    status, detail, project, _ = _uv_consumer_update(tmp_path, sync_returncode=2)
    assert status == "failed"
    assert "uv sync --frozen" in detail
    assert "exit status 2" in detail
    assert "Failed to fetch" in detail
    assert not (project / ".git" / "hook-count").exists()
    assert _head_subject(project) == "add python project files"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_synced_venv_not_gitignored_when_committing_update_then_project_fails_without_committing_it(
    tmp_path,
):
    """A synced environment is never staged: a .venv the commit would pick up fails the project instead."""
    status, detail, project, _ = _uv_consumer_update(tmp_path, venv_self_ignored=False)
    assert status == "failed"
    assert ".venv" in detail and "ignored" in detail
    assert not (project / ".git" / "hook-count").exists()
    assert _head_subject(project) == "add python project files"


@pytest.mark.skipif(sys.platform == "win32", reason="the fixture hook is a POSIX shell script")
def test_given_pyproject_without_uv_lock_when_committing_update_then_nothing_is_synced(tmp_path):
    """Without a uv.lock the update behaves as before: uv never runs and the hook's own result stands."""
    status, detail, _, syncs = _uv_consumer_update(tmp_path, with_lockfile=False)
    assert syncs == []
    assert status == "failed"
    assert "ModuleNotFoundError" in detail


def _probe_runner(ssh_reachable: bool):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return MagicMock(returncode=0 if ssh_reachable else 128, stdout="", stderr="Permission denied (publickey).")

    return run, calls


def test_given_ssh_template_source_when_host_has_no_ssh_access_then_it_is_read_over_https(tmp_path):
    """The framework host authenticates over HTTPS; a stored git@github.com source must still resolve."""
    _reachable_source.cache_clear()
    run, calls = _probe_runner(ssh_reachable=False)
    answers: dict[str, object] = {"_src_path": "git@github.com:example-org/example-template.git"}
    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        resolved = _resolve_copier_source(tmp_path, answers)
        again = _resolve_copier_source(tmp_path, answers)
    assert resolved == again == "https://github.com/example-org/example-template.git"
    assert len(calls) == 1, "the reachability probe is cached per source"


def test_given_ssh_template_source_when_host_has_ssh_access_then_the_stored_source_is_kept(tmp_path):
    _reachable_source.cache_clear()
    run, _ = _probe_runner(ssh_reachable=True)
    answers: dict[str, object] = {"_src_path": "ssh://git@github.com/example-org/example-template"}
    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        assert _resolve_copier_source(tmp_path, answers) == "ssh://git@github.com/example-org/example-template"


def test_given_bare_template_name_when_only_a_sibling_checkout_exists_then_the_sibling_is_used(tmp_path):
    project = tmp_path / "app"
    project.mkdir()
    sibling = tmp_path / "project-template"
    sibling.mkdir()
    assert _resolve_copier_source(project, {"_src_path": "project-template"}) == str(sibling.resolve())


def test_given_bare_template_name_when_a_nested_copy_exists_then_the_nested_copy_wins(tmp_path):
    project = tmp_path / "app"
    nested = project / "project-template"
    nested.mkdir(parents=True)
    (tmp_path / "project-template").mkdir()
    assert _resolve_copier_source(project, {"_src_path": "project-template"}) == str(nested.resolve())


def test_given_https_template_source_then_no_reachability_probe_runs(tmp_path):
    _reachable_source.cache_clear()
    run, calls = _probe_runner(ssh_reachable=False)
    answers: dict[str, object] = {"_src_path": "https://github.com/example-org/example-template"}
    with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
        assert _resolve_copier_source(tmp_path, answers) == "https://github.com/example-org/example-template"
    assert calls == []


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_stale_local_template_source(tmp_path: Path, source_spelling: str = "absolute") -> dict[str, Path | str]:
    """Create a Copier project whose local template clone is behind its bare origin."""
    origin = tmp_path / "template-origin.git"
    author = tmp_path / "template-author"
    projects = tmp_path / "projects"
    local_template = projects / "project-template"
    project = projects / "myproject"
    author.mkdir()
    projects.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=author, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=author, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=author, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=author, check=True)
    (author / "copier.yml").write_text("_subdirectory: template\nproject_name:\n  type: str\n  default: myproject\n")
    (author / "template").mkdir()
    (author / "template" / ".copier-answers.yml.jinja").write_text(
        "# Generated by Copier\n{{ _copier_answers|to_nice_yaml -}}\n"
    )
    (author / "template" / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=author, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=author, check=True, capture_output=True, text=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=author, check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", str(origin), str(local_template)], check=True, capture_output=True, text=True)

    copier_bin = shutil.which("copier")
    assert copier_bin is not None
    subprocess.run(
        [copier_bin, "copy", "--trust", "--defaults", str(local_template), str(project)],
        check=True,
        capture_output=True,
        text=True,
    )
    if source_spelling == "bare":
        answers_file = project / ".copier-answers.yml"
        answers = yaml.safe_load(answers_file.read_text())
        answers["_src_path"] = "project-template"
        answers_file.write_text(yaml.safe_dump(answers, sort_keys=False))
    _init_git_repo(project)

    (author / "template" / "message.txt").write_text("updated from origin\n")
    subprocess.run(["git", "commit", "-am", "update template"], cwd=author, check=True, capture_output=True, text=True)
    subprocess.run(["git", "push"], cwd=author, check=True, capture_output=True, text=True)
    return {
        "origin": origin,
        "author": author,
        "projects": projects,
        "local_template": local_template,
        "project": project,
        "origin_commit": _git_output(author, "rev-parse", "HEAD"),
    }


def test_given_origin_ahead_of_local_source_when_updating_then_project_uses_origin_commit(tmp_path, capsys):
    fixture = _make_stale_local_template_source(tmp_path)

    assert isinstance(fixture["projects"], Path)
    result = run_copier_update(projects_dir=fixture["projects"], isolate=False)

    assert result == 0
    project = fixture["project"]
    assert isinstance(project, Path)
    assert (project / "message.txt").read_text() == "updated from origin\n"
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert str(fixture["origin_commit"]).startswith(answers["_commit"])
    assert str(fixture["origin_commit"])[:12] in capsys.readouterr().out


def test_given_bare_name_local_source_when_updating_then_project_uses_origin_commit(tmp_path):
    fixture = _make_stale_local_template_source(tmp_path, source_spelling="bare")

    assert isinstance(fixture["projects"], Path)
    result = run_copier_update(projects_dir=fixture["projects"], isolate=False)

    assert result == 0
    project = fixture["project"]
    assert isinstance(project, Path)
    assert (project / "message.txt").read_text() == "updated from origin\n"
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["_src_path"] == "project-template"
    assert str(fixture["origin_commit"]).startswith(answers["_commit"])


def test_given_local_source_fetch_failure_when_updating_then_project_is_refused_untouched(tmp_path, capsys):
    fixture = _make_stale_local_template_source(tmp_path)
    local_template = fixture["local_template"]
    project = fixture["project"]
    assert isinstance(local_template, Path)
    assert isinstance(project, Path)
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "missing-origin.git")],
        cwd=local_template,
        check=True,
    )
    before_status = _git_output(project, "status", "--porcelain")
    before_answers = (project / ".copier-answers.yml").read_bytes()

    assert isinstance(fixture["projects"], Path)
    result = run_copier_update(projects_dir=fixture["projects"], isolate=False)

    assert result == EX_TEMPFAIL
    captured = capsys.readouterr()
    assert "fetch origin failed" in captured.out + captured.err
    assert (project / "message.txt").read_text() == "original\n"
    assert (project / ".copier-answers.yml").read_bytes() == before_answers
    assert _git_output(project, "status", "--porcelain") == before_status


def test_given_dirty_local_source_when_updating_then_source_head_and_worktree_are_unchanged(tmp_path):
    fixture = _make_stale_local_template_source(tmp_path)
    local_template = fixture["local_template"]
    project = fixture["project"]
    assert isinstance(local_template, Path)
    assert isinstance(project, Path)
    (local_template / "copier.yml").write_text(
        "_subdirectory: template\nproject_name:\n  type: str\n  default: myproject\n# local note\n"
    )
    (local_template / "local-only.txt").write_text("keep me\n")
    before_head = _git_output(local_template, "rev-parse", "HEAD")
    before_status = _git_output(local_template, "status", "--porcelain")
    before_config = (local_template / "copier.yml").read_bytes()

    assert isinstance(fixture["projects"], Path)
    result = run_copier_update(projects_dir=fixture["projects"], isolate=False)

    assert result == 0
    assert (project / "message.txt").read_text() == "updated from origin\n"
    assert _git_output(local_template, "rev-parse", "HEAD") == before_head
    assert _git_output(local_template, "status", "--porcelain") == before_status
    assert (local_template / "copier.yml").read_bytes() == before_config
    assert (local_template / "local-only.txt").read_text() == "keep me\n"


_FEATURE_QUESTIONS = """_subdirectory: template
_exclude:
  - "{% if not include_feature %}feature.txt{% endif %}"
project_name:
  type: str
  default: myproject
include_feature:
  type: bool
  default: true
"""


def _copier_project_from(tmp_path: Path, copier_yml: str, copy_data: dict[str, str]) -> dict[str, Path]:
    """A real Copier template (bare origin + local checkout) and a project generated from it."""
    origin = tmp_path / "template-origin.git"
    author = tmp_path / "template-author"
    projects = tmp_path / "projects"
    project = projects / "myproject"
    author.mkdir()
    projects.mkdir()
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=author, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=author, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=author, check=True)
    (author / "copier.yml").write_text(copier_yml)
    (author / "template").mkdir()
    (author / "template" / ".copier-answers.yml.jinja").write_text(
        "# Generated by Copier\n{{ _copier_answers|to_nice_yaml -}}\n"
    )
    (author / "template" / "message.txt.jinja").write_text("{{ project_name }} original\n")
    (author / "template" / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "."], cwd=author, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(projects / "project-template")], check=True, capture_output=True)

    copier_bin = shutil.which("copier")
    assert copier_bin is not None
    data_args = [arg for key, value in copy_data.items() for arg in ("--data", f"{key}={value}")]
    subprocess.run(
        [copier_bin, "copy", "--trust", "--defaults", *data_args, str(projects / "project-template"), str(project)],
        check=True,
        capture_output=True,
    )
    _init_git_repo(project)
    return {"author": author, "projects": projects, "project": project}


def _publish_template_change(author: Path, copier_yml: str) -> None:
    (author / "copier.yml").write_text(copier_yml)
    (author / "template" / "message.txt.jinja").write_text("{{ project_name }} updated\n")
    subprocess.run(["git", "commit", "-am", "update template"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=author, check=True, capture_output=True)


def _rendered_files(project: Path) -> set[str]:
    return {path.name for path in project.iterdir() if path.is_file()}


def _cli_update(projects: Path) -> tuple[int, str]:
    with patch("ai_cli.config._get_projects_dir", return_value=projects):
        exit_code, stdout, stderr = run_cli(["ai", "copier-update", "--no-isolate"])
    return exit_code, stdout + stderr


def test_given_non_default_stored_answers_when_updating_through_cli_then_they_survive_and_files_render_the_same(
    tmp_path,
):
    """Copier's --defaults keeps an answered question's stored value; it does not re-default it."""
    fixture = _copier_project_from(
        tmp_path, _FEATURE_QUESTIONS, {"project_name": "otherproject", "include_feature": "false"}
    )
    project = fixture["project"]
    files_before = _rendered_files(project)
    assert "feature.txt" not in files_before
    _publish_template_change(fixture["author"], _FEATURE_QUESTIONS)

    exit_code, output = _cli_update(fixture["projects"])

    assert exit_code == 0, output
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "otherproject"
    assert answers["include_feature"] is False
    assert _rendered_files(project) == files_before
    assert (project / "message.txt").read_text() == "otherproject updated\n"


def test_given_stored_answer_for_question_the_template_removed_when_updating_through_cli_then_update_lands(tmp_path):
    """An answer to a question the template no longer declares may drop; it must not block the update."""
    fixture = _copier_project_from(
        tmp_path,
        _FEATURE_QUESTIONS + "include_legacy:\n  type: bool\n  default: false\n",
        {"project_name": "otherproject", "include_feature": "false", "include_legacy": "true"},
    )
    project = fixture["project"]
    assert yaml.safe_load((project / ".copier-answers.yml").read_text())["include_legacy"] is True
    files_before = _rendered_files(project)
    _publish_template_change(fixture["author"], _FEATURE_QUESTIONS)

    exit_code, output = _cli_update(fixture["projects"])

    assert exit_code == 0, output
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert "include_legacy" not in answers
    assert answers["project_name"] == "otherproject"
    assert answers["include_feature"] is False
    assert _rendered_files(project) == files_before
    assert (project / "message.txt").read_text() == "otherproject updated\n"


def test_given_stored_answer_for_question_the_template_hid_when_updating_through_cli_then_refuses_naming_it(tmp_path):
    """Copier discards the answer to a `when: false` question, so the update refuses and says which one."""
    fixture = _copier_project_from(
        tmp_path, _FEATURE_QUESTIONS, {"project_name": "otherproject", "include_feature": "false"}
    )
    project = fixture["project"]
    _publish_template_change(fixture["author"], _FEATURE_QUESTIONS + "  when: false\n")

    exit_code, output = _cli_update(fixture["projects"])

    assert exit_code == EX_PARTIAL_MUTATION
    assert "dropped include_feature" in output
    assert "when: false" in output
    # Copier itself drops the answer from the file it writes, which is what the refusal guards against.
    assert "include_feature" not in yaml.safe_load((project / ".copier-answers.yml").read_text())


def test_given_file_mentioning_conflict_marker_mid_line_when_scanning_then_it_is_not_a_conflict(tmp_path):
    """A comment that quotes the marker (llm-lab's .markdownlint.yaml) is not a merge conflict."""
    quoting = tmp_path / "quoting.yaml"
    quoting.write_text("# audit docs show literal patterns (e.g. git conflict markers `^<<<<<<< `)\nMD038: false\n")
    assert _conflict_files(tmp_path, [str(quoting)]) == []
    assert _conflict_files(tmp_path) == []


def test_given_file_with_line_start_conflict_marker_when_scanning_then_it_is_a_conflict(tmp_path):
    conflicted = tmp_path / "conflicted.yaml"
    conflicted.write_text("a: 1\n<<<<<<< before updating\nb: 2\n=======\nb: 3\n>>>>>>> after updating\n")
    assert [Path(p) for p in _conflict_files(tmp_path, [str(conflicted)])] == [conflicted]
    assert [Path(p) for p in _conflict_files(tmp_path)] == [conflicted]


_PYTHON_ONLY_EXCLUDES = {
    "_exclude": [
        "docs/static-excluded.md",
        "{% if 'python' not in tech_stack %}pytest_memory_guard.py{% endif %}",
        "{% if not include_ci %}.github/workflows/ci.yml{% endif %}",
        "{% if not use_jira %}docs/jira.md{% endif %}",
        "*.pyc",
    ],
    "include_ci": {"type": "bool", "default": True},
}


def test_given_non_python_stack_when_listing_exclusions_then_python_only_files_are_excluded():
    """personal-site (typescript) never receives pytest_memory_guard.py, so it is not a missing file."""
    excluded = _excluded_on_update(_PYTHON_ONLY_EXCLUDES, {"tech_stack": "typescript", "use_jira": False})
    assert excluded == {"docs/static-excluded.md", "pytest_memory_guard.py", "docs/jira.md"}


def test_given_python_stack_when_listing_exclusions_then_the_python_file_is_expected():
    excluded = _excluded_on_update(_PYTHON_ONLY_EXCLUDES, {"tech_stack": "python", "use_jira": True})
    assert excluded == {"docs/static-excluded.md"}


def test_given_unknown_answer_when_listing_exclusions_then_that_entry_stays_strict():
    """No answer and no default for use_jira: leave docs/jira.md expected rather than guess."""
    excluded = _excluded_on_update(_PYTHON_ONLY_EXCLUDES, {"tech_stack": "typescript"})
    assert "docs/jira.md" not in excluded
    assert "pytest_memory_guard.py" in excluded


def test_given_file_project_removed_when_verifying_parity_then_it_is_not_missing(tmp_path):
    """The old template shipped install.sh, the project has no copy, and Copier keeps it removed."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "kept.txt").write_text("changed\n")
    porcelain = " M kept.txt\n"
    template_paths = {"kept.txt", ".githooks/install.sh"}
    error = _verify_update_parity(tmp_path, porcelain, template_paths, set())
    assert error is not None and ".githooks/install.sh" in error
    assert (
        _verify_update_parity(
            tmp_path,
            porcelain,
            template_paths,
            set(),
            previous_template_paths={"kept.txt", ".githooks/install.sh"},
        )
        is None
    )


def test_given_file_project_still_has_when_copier_leaves_it_unchanged_then_parity_still_fails(tmp_path):
    """Only a file the project removed is forgiven; one it has but Copier skipped is still missing."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "kept.txt").write_text("changed\n")
    (tmp_path / "other.txt").write_text("not touched\n")
    error = _verify_update_parity(
        tmp_path,
        " M kept.txt\n",
        {"kept.txt", "other.txt"},
        set(),
        previous_template_paths={"kept.txt", "other.txt"},
    )
    assert error is not None and "other.txt" in error


def test_do_update_pushfail(tmp_path):
    """Push rejected → pushfail with git stderr, worktree/commit preserved by caller."""
    runner = _wt_runner(porcelain=" M file\n", push_rc=1)
    with patch("ai_cli.copier_update.subprocess.run", side_effect=runner):
        with patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ):
            with patch("ai_cli.copier_update._conflict_files", return_value=[]):
                status, detail = _do_update_in_worktree(tmp_path / "wt", tmp_path / "root", "/usr/bin/copier", True)
    assert status == "pushfail"
    assert isinstance(detail, str)
    assert "rejected" in detail


def test_update_one_isolated_not_a_repo(tmp_path):
    """A non-git project dir returns failed without touching worktrees."""
    with patch("ai_cli.copier_update._repo_root", return_value=None):
        status, detail = _update_one_isolated(tmp_path, "/usr/bin/copier")
    assert status == "failed"
    assert isinstance(detail, str)
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


def test_given_no_push_without_inspection_when_update_succeeds_then_returns_legacy_detail_and_cleans_up(tmp_path):
    """The existing no-push path still discards its temporary commit and worktree."""
    _make_answers(tmp_path)
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", return_value=ok):
            with patch("ai_cli.copier_update._cleanup_worktree") as cleanup:
                with patch("ai_cli.copier_update._do_update_in_worktree", return_value=("ok", "")):
                    status, detail = _update_one_isolated(tmp_path, "/usr/bin/copier", push=False)

    assert (status, detail) == ("ok", "")
    assert cleanup.call_count == 2


def test_given_inspection_output_when_isolated_no_push_update_succeeds_then_writes_delivered_content(tmp_path):
    """CLI JSON preserves the actual commit while the temporary Git state is removed."""
    projects = tmp_path / "projects"
    projects.mkdir()
    template = tmp_path / "project-template"
    project = projects / "myproject"
    template.mkdir()
    project.mkdir()

    for directory in (template, project):
        subprocess.run(["git", "init"], cwd=directory, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=directory, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=directory, check=True)

    (template / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "message.txt"], cwd=template, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=template, check=True, capture_output=True)
    previous_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=template, check=True, capture_output=True, text=True
    ).stdout.strip()
    (template / "message.txt").write_text("updated\n")
    subprocess.run(["git", "commit", "-am", "update"], cwd=template, check=True, capture_output=True)
    template_origin = tmp_path / "template-origin.git"
    subprocess.run(["git", "clone", "--bare", str(template), str(template_origin)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(template_origin)], cwd=template, check=True)

    (project / "message.txt").write_text("original\n")
    (project / ".copier-answers.yml").write_text(f"_src_path: {template}\n_commit: {previous_commit}\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "/usr/bin/copier":
            # Byte-exact on purpose. `_snapshot_staged_files` reads the delivered
            # file with `read_bytes`, and the assertion below compares those bytes,
            # but `write_text` translates "\n" to os.linesep -- so on Windows the
            # stub delivered "updated\r\n" and the test blamed the snapshot code.
            (Path(kwargs["cwd"]) / "message.txt").write_bytes(b"updated\n")
            return MagicMock(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    inspection_output = tmp_path / "inspection.json"
    real_path = Path

    class CopierPath:
        def __new__(cls, *args):
            return real_path(*args)

        @staticmethod
        def home():
            return tmp_path

    # The projects root is resolved from configuration now, not from Path.home(), so the
    # CopierPath redirect above no longer reaches it.
    with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
        with patch("ai_cli.copier_update.Path", CopierPath):
            with patch("ai_cli.copier_update.subprocess.run", side_effect=run):
                with patch("shutil.which", return_value="/usr/bin/copier"):
                    exit_code, _, stderr = run_cli(
                        ["ai", "copier-update", "--no-push", "--inspect-output", str(inspection_output)]
                    )

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(inspection_output.read_text())
    assert payload["exit_code"] == 0
    assert len(payload["delivered_updates"]) == 1
    delivered = payload["delivered_updates"][0]
    assert delivered["project_name"] == "myproject"
    assert delivered["project_path"] == str(project)
    assert len(delivered["commit_hash"]) == 40
    assert delivered["changed_files"] == {"message.txt": {"encoding": "utf-8", "content": "updated\n"}}
    subprocess.run(
        ["git", "cat-file", "-e", f"{delivered['commit_hash']}^{{commit}}"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    assert not (project / ".worktrees" / "copier-update").exists()
    assert (
        "copier-update-tmp"
        not in subprocess.run(
            ["git", "branch", "--list", "copier-update-tmp"], cwd=project, check=True, capture_output=True, text=True
        ).stdout
    )


def test_given_inspection_when_written_then_json_represents_text_binary_and_deleted_content(tmp_path):
    """The JSON schema preserves all delivered file states for subprocess callers."""
    inspection = CopierUpdateInspection(
        exit_code=0,
        delivered_updates=(
            DeliveredUpdate(
                project_dir=tmp_path / "myproject",
                commit_hash="a" * 40,
                changed_files={"text.txt": b"updated\n", "binary.bin": b"\xff\x00", "removed.txt": None},
            ),
        ),
    )
    output = tmp_path / "inspection.json"

    write_copier_update_inspection(inspection, output)

    assert json.loads(output.read_text()) == {
        "exit_code": 0,
        "delivered_updates": [
            {
                "project_name": "myproject",
                "project_path": str(tmp_path / "myproject"),
                "commit_hash": "a" * 40,
                "changed_files": {
                    "text.txt": {"encoding": "utf-8", "content": "updated\n"},
                    "binary.bin": {"encoding": "base64", "content": "/wA="},
                    "removed.txt": None,
                },
            }
        ],
    }


def test_given_verified_conflict_anchor_when_isolated_update_ends_then_cleanup_runs(tmp_path):
    """A current-run conflict is cleaned up only after its recovery anchor exists."""
    _make_answers(tmp_path)
    ok = MagicMock(returncode=0, stdout="", stderr="")
    events = []
    ref = "refs/backup/2026-01-01/copier-update/myproject-abcdef0"

    def anchor(*args):
        events.append("anchor verified")
        return ref, "abcdef0"

    with (
        patch("ai_cli.copier_update._repo_root", return_value=tmp_path),
        patch("ai_cli.copier_update.subprocess.run", return_value=ok),
        patch("ai_cli.copier_update._cleanup_worktree", side_effect=lambda *args: events.append("cleanup")),
        patch("ai_cli.copier_update._anchor_failed_update", side_effect=anchor),
        patch("ai_cli.copier_update._check_recovery_target"),
        patch("ai_cli.copier_update._file_recovery_issue") as issue,
        patch("ai_cli.copier_update._do_update_in_worktree", return_value=("conflict", ["a.py"])),
    ):
        status, _ = _update_one_isolated(tmp_path, "/usr/bin/copier")
    assert status == "conflict"
    assert events == ["cleanup", "anchor verified", "cleanup"]
    issue.assert_called_once_with(tmp_path, ref, ["a.py"], "previous..abcdef0")


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
            return "pushfail", RecoveredPushFailure("refs/backup/example", "push rejected")
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
        with patch(
            "ai_cli.copier_update._run_copier_update",
            return_value=(None, {}, TemplateChanges(set(), set(), set(), set())),
        ):
            status, _ = _do_update_in_worktree(wt, tmp_path / "root", "/usr/bin/copier", False)
    assert status == "ok"  # NOT "conflict"


_SQUARE_DELIMITERS = {"block": ("[%", "%]"), "variable": ("[[", "]]")}
_PYTHON_ONLY_GUARD_EXCLUDES = [
    "{% if 'python' not in tech_stack %}guard.py{% endif %}",
    "{% if 'python' not in tech_stack %}tests/test_guard.py{% endif %}",
]


def _make_python_only_files_template(
    tmp_path: Path,
    tech_stack: str,
    exclude_entries: list[str],
    delimiters: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """A real Copier project whose next template commit adds a new directory and Python-only files.

    The template excludes ``guard.py`` and ``tests/test_guard.py`` for a non-Python stack, the shape
    a template uses to ship a Python test helper only to Python projects. Returns the projects dir.
    """
    origin = tmp_path / "template-origin.git"
    author = tmp_path / "template-author"
    projects = tmp_path / "projects"
    local_template = projects / "project-template"
    project = projects / "myproject"
    author.mkdir()
    projects.mkdir()
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=author, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=author, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=author, check=True)
    variable_start, variable_end = "{{", "}}"
    config: dict[str, object] = {"_subdirectory": "template", "_exclude": exclude_entries}
    if delimiters is not None:
        (block_start, block_end), (variable_start, variable_end) = delimiters["block"], delimiters["variable"]
        config["_envops"] = {
            "block_start_string": block_start,
            "block_end_string": block_end,
            "variable_start_string": variable_start,
            "variable_end_string": variable_end,
        }
    config["tech_stack"] = {"type": "str"}
    (author / "copier.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    (author / "template").mkdir()
    (author / "template" / ".copier-answers.yml.jinja").write_text(
        f"# Generated by Copier\n{variable_start} _copier_answers|to_nice_yaml -{variable_end}\n"
    )
    (author / "template" / "message.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], cwd=author, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=author, check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(local_template)], check=True, capture_output=True)

    copier_bin = shutil.which("copier")
    assert copier_bin is not None
    subprocess.run(
        [
            copier_bin,
            "copy",
            "--trust",
            "--defaults",
            "-d",
            f"tech_stack={tech_stack}",
            str(local_template),
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _init_git_repo(project)

    (author / "template" / "message.txt").write_text("updated\n")
    (author / "template" / "rules").mkdir()
    (author / "template" / "rules" / "new-rule.md").write_text("a rule in a directory the project lacks\n")
    (author / "template" / "tests").mkdir()
    (author / "template" / "guard.py").write_text("GUARD = True\n")
    (author / "template" / "tests" / "test_guard.py").write_text("def test_guard():\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=author, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add a rule and a python guard"], cwd=author, check=True, capture_output=True
    )
    subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=author, check=True, capture_output=True)
    return projects


@pytest.mark.parametrize("isolate", [True, False], ids=["isolated", "no-isolate"])
def test_given_template_adds_files_under_new_directory_when_updating_then_parity_sees_each_file(
    tmp_path, capsys, isolate
):
    """``git status --porcelain`` without ``-uall`` collapses a new directory to ``?? rules/``."""
    projects = _make_python_only_files_template(tmp_path, "python", _PYTHON_ONLY_GUARD_EXCLUDES)

    result = run_copier_update(projects_dir=projects, isolate=isolate, push=False)

    assert "parity failed" not in capsys.readouterr().out
    assert result == 0


@pytest.mark.parametrize("isolate", [True, False], ids=["isolated", "no-isolate"])
def test_given_python_only_files_and_non_python_answers_when_updating_then_parity_accepts_their_absence(
    tmp_path, capsys, isolate
):
    """The project's own answers render the exclusion, so files Copier never renders are not missing."""
    projects = _make_python_only_files_template(tmp_path, "typescript", _PYTHON_ONLY_GUARD_EXCLUDES)

    result = run_copier_update(projects_dir=projects, isolate=isolate, push=False)

    assert "parity failed" not in capsys.readouterr().out
    assert result == 0


def test_given_python_answers_when_copier_omits_python_only_file_then_parity_names_it_missing(tmp_path, capsys):
    """The exclusion renders empty for a Python project, so parity still demands the file."""
    projects = _make_python_only_files_template(tmp_path, "python", _PYTHON_ONLY_GUARD_EXCLUDES)
    project = projects / "myproject"
    real_run = subprocess.run
    # The exact binary the update runs: on Windows it is copier.EXE, so a name match misses it.
    copier_bin = shutil.which("copier")
    dropped = []

    def copier_that_drops_guard(command, **kwargs):
        result = real_run(command, **kwargs)
        if command[0] == copier_bin and command[1:2] == ["update"]:
            (project / "guard.py").unlink()
            dropped.append(True)
        return result

    with patch("ai_cli.copier_update.subprocess.run", side_effect=copier_that_drops_guard):
        result = run_copier_update(projects_dir=projects, isolate=False)

    assert dropped == [True]
    assert result == EX_PARTIAL_MUTATION
    assert "missing changed file(s): guard.py" in capsys.readouterr().out


def test_given_exclude_entry_rendering_several_lines_when_updating_then_each_line_is_excluded(tmp_path, capsys):
    """Copier splits a rendered ``_exclude`` entry on line breaks, one pattern per line."""
    projects = _make_python_only_files_template(
        tmp_path, "typescript", ["{% if 'python' not in tech_stack %}guard.py\ntests/test_guard.py{% endif %}"]
    )

    result = run_copier_update(projects_dir=projects, isolate=False)

    assert "parity failed" not in capsys.readouterr().out
    assert result == 0
    assert not (projects / "myproject" / "guard.py").exists()


def test_given_template_envops_delimiters_when_updating_then_exclude_entries_render_with_them(tmp_path, capsys):
    """Copier renders ``_exclude`` with the template's ``_envops``, custom delimiters included."""
    projects = _make_python_only_files_template(
        tmp_path,
        "typescript",
        [
            "[% if 'python' not in tech_stack %]guard.py[% endif %]",
            "[% if 'python' not in tech_stack %]tests/test_guard.py[% endif %]",
        ],
        delimiters=_SQUARE_DELIMITERS,
    )

    result = run_copier_update(projects_dir=projects, isolate=False)

    assert "parity failed" not in capsys.readouterr().out
    assert result == 0
    assert not (projects / "myproject" / "guard.py").exists()
