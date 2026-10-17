"""Failed isolated updates are recoverable without leaving dirty checkouts.

The public entry point and Git repositories/worktrees are real. Only the Copier
process and issue tracker are substituted; no service or user repository is used.
"""

import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from ai_cli import copier_update
from ai_cli.git_repair import _git_env


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check, env=_git_env())


@pytest.fixture
def recovery_project(tmp_path):
    template = tmp_path / "project-template"
    template.mkdir()
    _git(template, "init", "-q", "-b", "main")
    _git(template, "config", "user.name", "Test User")
    _git(template, "config", "user.email", "test@example.com")
    _git(template, "config", "core.autocrlf", "false")
    _git(template, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    (template / "copier.yml").write_text("{}\n")
    (template / ".gitignore").write_text(".worktrees/\nignored.log\n")
    (template / "message.txt").write_text("original\n")
    (template / "other.txt").write_text("original\n")
    (template / "removed.txt").write_text("obsolete\n")
    _git(template, "add", "-A")
    _git(template, "commit", "-qm", "Initial template")
    previous = _git(template, "rev-parse", "HEAD").stdout.strip()

    project = tmp_path / "projects" / "myproject"
    project.parent.mkdir()
    _git(tmp_path, "clone", "-q", str(template), str(project))
    _git(project, "remote", "remove", "origin")
    _git(project, "config", "user.name", "Test User")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "core.autocrlf", "false")
    _git(project, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    (project / ".copier-answers.yml").write_text(
        yaml.safe_dump({"_src_path": str(template), "_commit": previous, "feature_enabled": True})
    )
    (project / "message.txt").write_text("project customization\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "Customize project")

    project_origin = tmp_path / "project-origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(project), str(project_origin))
    _git(project, "remote", "add", "origin", str(project_origin))

    (template / "message.txt").write_text("template update\n")
    (template / "other.txt").write_text("updated\n")
    # Git's apply step, rather than fixture cleanup, exercises a tracked deletion.
    _git(template, "rm", "removed.txt")
    _git(template, "commit", "-qam", "Update template")
    current = _git(template, "rev-parse", "HEAD").stdout.strip()
    template_origin = tmp_path / "template-origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(template), str(template_origin))
    _git(template, "remote", "add", "origin", str(template_origin))
    # Copy only local Git objects; the entry point never needs a network remote.
    _git(project, "fetch", "-q", str(template), current)
    return project, previous, current


def _run(project, current, *, mode="conflict", inject=None, file_issue=None):
    """Substitute Copier with a real merge, retaining actual unmerged index stages."""
    if mode in ("clean-push", "dirty-push", "reject-push", "anchor-push-fail"):
        # Push rejection needs an otherwise valid update. Restore the template's
        # old content before the run so the parity check accepts its exact hunk.
        (project / "message.txt").write_text("original\n")
        _git(project, "commit", "-qam", "Use template content before update")
        _git(project, "push", "-q", "origin", "main")
    real_run = subprocess.run
    observed = {}

    def run(command, **kwargs):
        if command[0] == "test-copier":
            wt = Path(kwargs["cwd"])
            observed["worktree"] = wt
            if mode == "conflict":
                merge = real_run(
                    ["git", "-C", str(wt), "merge", "--no-commit", current],
                    capture_output=True,
                    text=True,
                    env=_git_env(),
                    check=False,
                )
                assert merge.returncode == 1
                assert "message.txt" in merge.stdout
                observed["unmerged"] = _git(wt, "ls-files", "-u").stdout
                assert observed["unmerged"]
            else:
                (wt / "message.txt").write_text("template update\n")
                (wt / "other.txt").write_text("updated\n")
                _git(wt, "rm", "removed.txt")
            answers = yaml.safe_load((wt / ".copier-answers.yml").read_text())
            answers["_commit"] = current
            if mode == "semantic":
                answers["feature_enabled"] = False
            (wt / ".copier-answers.yml").write_text(yaml.safe_dump(answers))
            (wt / "new.txt").write_bytes(b"untracked content\n")
            (wt / "ignored.log").write_text("ignored content\n")
            if mode in ("reject-push", "anchor-push-fail"):
                competing = project.parent.parent / "competing"
                origin = real_run(
                    ["git", "-C", str(project), "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                ).stdout.strip()
                real_run(
                    ["git", "clone", "-q", origin, str(competing)],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
                real_run(
                    ["git", "-C", str(competing), "config", "user.name", "Test User"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
                real_run(
                    ["git", "-C", str(competing), "config", "user.email", "test@example.com"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
                (competing / "competing.txt").write_text("origin advanced\n")
                real_run(
                    ["git", "-C", str(competing), "add", "competing.txt"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
                real_run(
                    ["git", "-C", str(competing), "commit", "-qm", "Advance origin"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
                real_run(
                    ["git", "-C", str(competing), "push", "-q", "origin", "main"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=_git_env(),
                )
            return subprocess.CompletedProcess(command, 0, "", "")
        if "push" in command:
            wt = observed["worktree"]
            observed["commit"] = _git(wt, "rev-parse", "HEAD").stdout.strip()
            if mode == "anchor-push-fail" and any("refs/backup/" in arg for arg in command):
                return subprocess.CompletedProcess(command, 1, "", "anchor push rejected")
            if mode in ("reject-push", "anchor-push-fail"):
                return real_run(command, **kwargs)
            if mode == "dirty-push":
                (wt / "other.txt").write_text("post-commit edit\n")
                (wt / "late.txt").write_bytes(b"post-commit untracked\n")
            return subprocess.CompletedProcess(command, 1, "", "push rejected")
        if inject:
            result = inject(command, observed)
            if result is not None:
                return result
        return real_run(command, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(copier_update.shutil, "which", return_value="test-copier"))
        stack.enter_context(patch.object(copier_update.subprocess, "run", side_effect=run))
        if file_issue is not None:
            stack.enter_context(patch.object(copier_update, "_file_recovery_issue", file_issue, create=True))
        code = copier_update.run_copier_update(projects_dir=project.parent, project_filter=project.name)
    return code, observed


def _anchors(project):
    return _git(project, "for-each-ref", "--format=%(refname)", "refs/backup/").stdout.splitlines()


def _assert_removed(project):
    assert not (project / ".worktrees" / "copier-update").exists()
    assert _git(project, "branch", "--list", "copier-update-tmp").stdout == ""
    listed = _git(project, "worktree", "list", "--porcelain").stdout
    assert sum(line.startswith("worktree ") for line in listed.splitlines()) == 1
    assert _git(project, "status", "--porcelain").stdout == ""


def test_given_conflicting_update_when_entry_runs_then_verified_snapshot_survives_cleanup(recovery_project, capsys):
    project, previous, current = recovery_project
    issue = Mock(return_value="issue-1")
    dates = {datetime.now(UTC).strftime("%Y-%m-%d")}
    code, _ = _run(project, current, file_issue=issue)
    dates.add(datetime.now(UTC).strftime("%Y-%m-%d"))

    assert code == copier_update.EX_PARTIAL_MUTATION
    _assert_removed(project)
    anchors = _anchors(project)
    assert len(anchors) == 1
    anchor = anchors[0]
    short = _git(project.parent.parent / "project-template", "rev-parse", "--short", current).stdout.strip()
    assert anchor in {f"refs/backup/{date}/copier-update/myproject-{short}" for date in dates}
    conflict = _git(project, "show", f"{anchor}:message.txt").stdout
    assert "<<<<<<<" in conflict and "project customization" in conflict and "template update" in conflict
    assert _git(project, "show", f"{anchor}:other.txt").stdout == "updated\n"
    assert _git(project, "show", f"{anchor}:new.txt").stdout == "untracked content\n"
    tree = _git(project, "ls-tree", "-r", "--name-only", anchor).stdout.splitlines()
    assert "removed.txt" not in tree
    assert "ignored.log" not in tree
    assert _git(project, "rev-parse", f"{anchor}^1").stdout == _git(project, "rev-parse", "HEAD").stdout
    issue.assert_called_once_with(project, anchor, ["message.txt"], f"{previous}..{current}")
    output = capsys.readouterr().out
    assert anchor in output
    assert "resolve in temp worktree" not in output


@pytest.mark.parametrize("failure", ["write", "verify", "verify-mismatch"])
def test_given_anchor_failure_when_entry_runs_then_worktree_and_index_are_untouched(recovery_project, capsys, failure):
    project, _, current = recovery_project
    issue = Mock()
    before = {}

    def inject(command, observed):
        if "worktree" not in observed:
            return None
        if (failure == "write" and "update-ref" in command) or (
            failure.startswith("verify") and "rev-parse" in command and any("refs/backup/" in arg for arg in command)
        ):
            wt = observed["worktree"]
            before["status"] = _git(wt, "status", "--porcelain").stdout
            before["index"] = _git(wt, "ls-files", "--stage").stdout
            before["content"] = (wt / "message.txt").read_bytes()
            if failure == "verify-mismatch":
                return subprocess.CompletedProcess(command, 0, "0" * 40 + "\n", "")
            return subprocess.CompletedProcess(command, 1, "", "injected anchor failure")
        return None

    code, observed = _run(project, current, inject=inject, file_issue=issue)

    assert code != 0
    wt = observed["worktree"]
    assert wt.is_dir()
    assert _git(wt, "status", "--porcelain").stdout == before["status"]
    assert _git(wt, "ls-files", "--stage").stdout == before["index"]
    assert (wt / "message.txt").read_bytes() == before["content"]
    assert _git(project, "branch", "--list", "copier-update-tmp").stdout.strip()
    issue.assert_not_called()
    assert "anchor" in capsys.readouterr().out.lower()


def test_given_semantic_failure_when_entry_runs_then_state_is_anchored_and_removed(recovery_project):
    project, previous, current = recovery_project
    issue = Mock(return_value="issue-1")
    code, _ = _run(project, current, mode="semantic", file_issue=issue)

    assert code == copier_update.EX_PARTIAL_MUTATION
    _assert_removed(project)
    (anchor,) = _anchors(project)
    answers = yaml.safe_load(_git(project, "show", f"{anchor}:.copier-answers.yml").stdout)
    assert answers["feature_enabled"] is False
    assert _git(project, "show", f"{anchor}:new.txt").stdout == "untracked content\n"
    issue.assert_called_once_with(project, anchor, [], f"{previous}..{current}")


@pytest.mark.parametrize("mode", ["clean-push", "dirty-push"])
def test_given_main_and_anchor_pushes_are_rejected_when_entry_runs_then_local_recovery_state_is_kept(
    recovery_project, mode
):
    project, _, current = recovery_project
    issue = Mock(return_value="issue-1")
    code, observed = _run(project, current, mode=mode, file_issue=issue)

    assert code == copier_update.EX_PARTIAL_MUTATION
    wt = observed["worktree"]
    assert wt.is_dir()
    assert _git(project, "rev-parse", "copier-update-tmp").stdout.strip() == observed["commit"]
    assert _git(wt, "show", "HEAD:message.txt").stdout == "template update\n"
    (anchor,) = _anchors(project)
    if mode == "dirty-push":
        assert _git(wt, "status", "--porcelain").stdout == " M other.txt\n?? late.txt\n"
        assert _git(project, "show", f"{anchor}:other.txt").stdout == "post-commit edit\n"
        assert _git(project, "show", f"{anchor}:late.txt").stdout == "post-commit untracked\n"
    else:
        assert _git(wt, "status", "--porcelain").stdout == ""
    issue.assert_not_called()


def test_given_origin_rejects_update_when_entry_runs_then_anchor_is_pushed_and_temp_state_is_removed(
    recovery_project, capsys
):
    project, previous, current = recovery_project
    issue = Mock(return_value="issue-1")

    code, _ = _run(project, current, mode="reject-push", file_issue=issue)

    assert code == copier_update.EX_PARTIAL_MUTATION
    _assert_removed(project)
    (anchor,) = _anchors(project)
    local_tip = _git(project, "rev-parse", anchor).stdout.strip()
    remote = _git(project, "ls-remote", "--refs", "origin", anchor).stdout.split()
    assert remote == [local_tip, anchor]
    assert issue.call_args.args[1] == anchor
    assert f"{previous}..{current}" in issue.call_args.args
    assert "rejected" in issue.call_args.args[-1].lower()
    summary = next(line for line in capsys.readouterr().out.splitlines() if "PUSH FAILED" in line)
    assert anchor in summary


def test_given_anchor_push_fails_when_entry_runs_then_worktree_is_kept_and_recovery_failure_is_reported(
    recovery_project, capsys
):
    project, _, current = recovery_project
    issue = Mock(return_value="issue-1")

    code, observed = _run(project, current, mode="anchor-push-fail", file_issue=issue)

    assert code == copier_update.EX_PARTIAL_MUTATION
    wt = observed["worktree"]
    assert wt.is_dir()
    assert _git(project, "rev-parse", "copier-update-tmp").stdout.strip() == observed["commit"]
    (anchor,) = _anchors(project)
    assert _git(project, "ls-remote", "--refs", "origin", anchor).stdout == ""
    issue.assert_not_called()
    assert "RECOVERY FAILED" in capsys.readouterr().out


def test_given_repeat_conflicts_when_entry_runs_then_existing_issue_is_commented(recovery_project, monkeypatch):
    project, previous, current = recovery_project
    from ai_cli import session_migrate

    existing = []
    tracker_calls = []

    def bd(store, *args):
        assert store == project
        tracker_calls.append(args)
        if args[0] == "list":
            import json

            return subprocess.CompletedProcess(args, 0, json.dumps(existing), "")
        assert args[:3] == ("comments", "add", "issue-1")
        assert previous in args[3] and current in args[3] and "message.txt" in args[3]
        return subprocess.CompletedProcess(args, 0, "", "")

    def create(command, observed):
        if Path(command[0]).name != "bd-new":
            return None
        tracker_calls.append(command)
        assert command[command.index("--repo") + 1] == str(project)
        title = command[command.index("--title") + 1]
        body = command[command.index("--description") + 1]
        assert "copier-update" in title and project.name in title
        assert "refs/backup/" in body and "message.txt" in body
        assert f"{previous}..{current}" in body
        existing.append({"id": "issue-1", "title": title, "status": "open"})
        return subprocess.CompletedProcess(command, 0, "issue-1", "")

    monkeypatch.setattr(session_migrate, "_bd", bd)
    first_snapshot = None
    for _ in range(2):
        code, _ = _run(project, current, inject=create)
        assert code == copier_update.EX_PARTIAL_MUTATION
        _assert_removed(project)
        (anchor,) = _anchors(project)
        if first_snapshot is None:
            first_snapshot = _git(project, "rev-parse", anchor).stdout.strip()
        else:
            assert _git(project, "rev-parse", f"{anchor}^2").stdout.strip() == first_snapshot
    assert len([call for call in tracker_calls if Path(call[0]).name == "bd-new"]) == 1
    assert len([call for call in tracker_calls if call[:2] == ("comments", "add")]) == 1


def test_given_issue_filing_failure_when_entry_runs_then_anchor_and_clean_checkout_are_reported(
    recovery_project, capsys
):
    project, _, current = recovery_project
    code, _ = _run(project, current, file_issue=Mock(side_effect=RuntimeError("tracker unavailable")))

    assert code != 0
    _assert_removed(project)
    (anchor,) = _anchors(project)
    output = capsys.readouterr().out
    assert anchor in output and "tracker unavailable" in output
