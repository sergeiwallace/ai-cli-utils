"""An unqualified `ai c` must not be refused because of unrelated unregistered checkouts.

The registry-completeness scan runs before an unqualified launch. It used to
refuse the launch when *any* directory under the projects root was missing from
the registry, so a user with an unrelated checkout they had no prefix for could
not start a session in a fully registered project. Only the launch target's own
registration gates the launch; the other directories are named in a warning.
"""

from unittest.mock import patch

import pytest
from conftest import run_cli

UNRELATED = ("notes", "otherrepo", "scratch-tools")


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    for name in ("myproject", *UNRELATED):
        (root / name / ".git").mkdir(parents=True)
    registry = tmp_path / "projects.toml"
    registry.write_text('[[projects]]\nname = "myproject"\ntask_prefix = "MYPROJECT"\ntype = "tool"\nactive = true\n')
    monkeypatch.chdir(root / "myproject")
    with (
        patch("ai_cli.config._get_project_registry_path", return_value=registry),
        patch("ai_cli.config._get_projects_dir", return_value=root),
        patch("ai_cli.session._get_projects_dir", return_value=root),
    ):
        yield root


def _dry_run(isatty: bool):
    with (
        patch("sys.stdin.isatty", return_value=isatty),
        patch("builtins.input", side_effect=AssertionError("prompted about an unrelated directory")),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.get_project_prefix", return_value="MYPROJECT"),
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
        patch("ai_cli.session.create_worktree", side_effect=AssertionError("dry run created a worktree")),
        patch("os.execvp", side_effect=AssertionError("dry run executed")),
    ):
        return run_cli(["ai", "c", "--dry-run"], config={"worktree": {"enabled": False}})


@pytest.mark.parametrize("isatty", [False, True], ids=["no-terminal", "terminal"])
def test_given_registered_target_and_unrelated_unregistered_checkouts_when_dry_run_then_plans_and_warns(
    projects_root, isatty
):
    code, out, err = _dry_run(isatty)

    assert code == 0, err
    assert "myproject-1" in out
    warning = next(line for line in err.splitlines() if "notes" in line)
    for name in UNRELATED:
        assert name in warning
    assert "myproject" not in warning


def test_given_unregistered_target_when_dry_run_without_terminal_then_refuses_naming_it(projects_root, monkeypatch):
    (projects_root / "newproject" / ".git").mkdir(parents=True)
    monkeypatch.chdir(projects_root / "newproject")

    code, _out, err = _dry_run(False)

    assert code == 1
    assert "newproject" in err
