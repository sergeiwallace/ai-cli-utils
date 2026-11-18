"""`ai c NAME -p PROJECT` names the project from -p, whatever directory it runs in.

Two launches from the projects root (a directory that holds repositories and is not
one) failed with "No task prefix is registered for repository <projects root>". Their
launch logs showed why: the session NAME was the single argument ``session-1 -p myproject``. A
shell that does not split a variable on spaces (zsh does not, for an unquoted one)
had passed the options inside the name, so the launch never saw -p and derived the
project from the working directory. From inside a repository the same argument was
worse: it planned a new session named after the whole string in the wrong repository.

These tests pin both halves through the CLI: -p resolves from any working directory,
and a NAME containing whitespace is refused before anything is resolved, naming the
remedy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli

from ai_cli.config import FLEET_REGISTRY_MARKER

# Bare mode and index naming keep the launch off tmux and the name registry, and the
# old naming convention needs no host alias, so only project resolution is exercised.
_CONFIG = {
    "session": {"use_tmux": False},
    "worktree": {"enabled": False},
    "naming": {"alias_convention": False, "legacy_convention": "full"},
}


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    """A projects root holding one registered repository and the persistent registry."""
    root = tmp_path / "projects"
    (root / "myproject" / ".git").mkdir(parents=True)
    (root / "myproject" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    registry = root / "myconfig" / FLEET_REGISTRY_MARKER
    registry.parent.mkdir(parents=True)
    registry.write_text('[[projects]]\nname = "myproject"\ntask_prefix = "myp"\ntype = "tool"\nactive = true\n')
    monkeypatch.chdir(root)
    with (
        patch("ai_cli.config._get_projects_dir", return_value=root),
        patch("ai_cli.session._get_projects_dir", return_value=root),
        patch("ai_cli.config._get_project_registry_path", return_value=None),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.direnv_setup.ensure_direnv"),
        patch("sys.stdin.isatty", return_value=False),
        patch("ai_cli.session.create_worktree", side_effect=AssertionError("dry run created a worktree")),
        patch("os.execvp", side_effect=AssertionError("dry run executed")),
    ):
        yield root


def test_given_the_projects_root_as_cwd_when_dry_run_names_the_project_with_p_then_it_plans_in_that_project(
    projects_root,
):
    code, out, err = run_cli(["ai", "c", "session-1", "-p", "myp", "-d", "-I"], config=_CONFIG)

    assert code == 0, err
    assert "No task prefix is registered" not in err
    assert "c-myp-session-1" in out


def test_given_a_name_holding_options_when_launching_from_the_projects_root_then_it_refuses_naming_the_split(
    projects_root,
):
    code, out, err = run_cli(["ai", "c", "session-1 -p myp", "-d", "-I"], config=_CONFIG)

    assert code == 1
    assert "'session-1 -p myp'" in err
    assert "ai c session-1 -p myp" in err
    assert "No task prefix is registered" not in err
    assert "plan:" not in out + err


def test_given_a_name_holding_options_when_launching_from_inside_a_repository_then_no_session_is_planned(
    projects_root, monkeypatch
):
    monkeypatch.chdir(projects_root / "myproject")

    code, out, err = run_cli(["ai", "c", "session-1 -p myp", "-d", "-I"], config=_CONFIG)

    assert code == 1
    assert "session-1-p-myp" not in out + err
    assert "plan:" not in out + err


class _Handoff(Exception):
    """Raised in place of the exec that replaces the launcher with the engine."""


def test_given_the_projects_root_as_cwd_when_launching_for_real_with_p_then_the_engine_starts_in_that_project(
    projects_root,
):
    started: list[tuple[Path, Path]] = []

    def handoff(project_root, command, *args, **kwargs):
        started.append((Path(project_root), Path.cwd()))
        raise _Handoff

    with (
        patch("ai_cli.main._ensure_dolt_server"),
        patch("ai_cli.main._ensure_wake_sidecar"),
        patch("ai_cli.main._sync_launch_client", return_value=None),
        patch("ai_cli.main._tunnel._ensure_nats_tunnel"),
        patch("ai_cli.main._process_manager._start_stale_session_reaper_for_launch"),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.main._exec_with_direnv", side_effect=handoff),
        pytest.raises(_Handoff),
    ):
        run_cli(["ai", "c", "session-1", "-p", "myp", "-b", "-I"], config=_CONFIG)

    project = projects_root / "myproject"
    assert started == [(project, project)]
