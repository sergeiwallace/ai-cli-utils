"""AI-CLI-ok04 — a project directory that is not there must stop the launch.

``ai c`` chdirs into the resolved project directory before it creates the session
worktree, and both chdir sites used to skip that silently when the directory did
not exist. An SSH-driven remote launch starts in ``$HOME``, so the session built
its worktree there and resolved every git command against the wrong root with
nothing printed at all. These tests pin the refusal at both sites, and pin a
``-p`` value that names neither a registered prefix nor an existing repository to
an error that does not send the caller after the absent path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.config import ProjectPrefixError, resolve_project_prefix_by_name
from ai_cli.main import _resolve_remote_project, cli

_BARE_CONFIG = {"session": {"use_tmux": False}}
_HOST = {"host_id": "test-host", "os_type": "linux"}


@pytest.fixture
def launch_boundary():
    """Silence the launch's ambient preflights so only the chdir decision runs.

    Each of these reaches outside the process — an update check, a direnv probe —
    and none of them participates in resolving the project directory.
    """
    with (
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
        patch("ai_cli.direnv_setup.ensure_direnv"),
        patch("ai_cli.config.detect_machine_profile", return_value=_HOST),
    ):
        yield


# --- The remote side of an -R launch ---


def test_given_a_remote_launch_when_the_project_dir_is_absent_then_it_refuses_naming_path_and_host(
    launch_boundary, tmp_path, capsys
):
    absent = tmp_path / "projects" / "myproject"

    with (
        patch("sys.argv", ["ai", "c", "1", "--is-remote", "--project-prefix", "MYPROJECT", "-p", "myproject"]),
        patch("ai_cli.config.load_config", return_value=_BARE_CONFIG),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config._find_project_dir", return_value=absent),
        patch("os.chdir") as mock_chdir,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 1
    mock_chdir.assert_not_called()
    err = capsys.readouterr().err
    assert "test-host" in err
    assert str(absent) in err
    assert "-p/--project" in err
    assert "ai c -R -p PROJECT" in err


def test_given_a_remote_launch_when_the_project_dir_exists_then_it_chdirs_into_it(launch_boundary, tmp_path):
    """The positive control: refusing an absent directory must not refuse a present one."""
    present = tmp_path / "projects" / "myproject"
    present.mkdir(parents=True)

    with (
        patch(
            "sys.argv",
            ["ai", "c", "1", "--is-remote", "--dry-run", "--project-prefix", "MYPROJECT", "-p", "myproject"],
        ),
        patch("ai_cli.config.load_config", return_value=_BARE_CONFIG),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config._find_project_dir", return_value=present),
        patch("ai_cli.session.build_session_name", return_value=("c-r-myproject-1", "myproject-1")),
        patch("os.chdir") as mock_chdir,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    mock_chdir.assert_called_once_with(present)


def test_given_a_remote_launch_when_a_file_sits_at_the_project_path_then_it_refuses(launch_boundary, tmp_path, capsys):
    """A path that exists but is not a directory cannot be entered either."""
    not_a_dir = tmp_path / "projects" / "myproject"
    not_a_dir.parent.mkdir(parents=True)
    not_a_dir.write_text("not a repository\n", encoding="utf-8")

    with (
        patch("sys.argv", ["ai", "c", "1", "--is-remote", "--project-prefix", "MYPROJECT", "-p", "myproject"]),
        patch("ai_cli.config.load_config", return_value=_BARE_CONFIG),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config._find_project_dir", return_value=not_a_dir),
        patch("os.chdir") as mock_chdir,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 1
    mock_chdir.assert_not_called()
    assert str(not_a_dir) in capsys.readouterr().err


# --- The local -p launch, which strands the session identically ---


def test_given_a_local_project_flag_when_the_project_dir_is_absent_then_it_refuses(launch_boundary, tmp_path, capsys):
    """Reachable whenever a registry answers the prefix for a repository that is not checked out."""
    absent = tmp_path / "projects" / "myproject"

    with (
        patch("sys.argv", ["ai", "c", "1", "-p", "myproject"]),
        patch("ai_cli.config.load_config", return_value=_BARE_CONFIG),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.resolve_project_prefix_by_name", return_value="MYPROJECT"),
        patch("ai_cli.config._find_project_dir", return_value=absent),
        patch("os.chdir") as mock_chdir,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 1
    mock_chdir.assert_not_called()
    err = capsys.readouterr().err
    assert "test-host" in err
    assert str(absent) in err
    assert "ai c -p PROJECT" in err


# --- Which source named the project, and what the message says about it ---


def test_given_a_project_flag_when_resolving_the_remote_project_then_the_configured_machine_is_not_read():
    """``get_remote_machine`` raises without a default machine, so an answered launch must not ask."""
    with patch("ai_cli.config.get_remote_machine", side_effect=AssertionError("must not be consulted")):
        assert _resolve_remote_project("myproject", {}) == ("myproject", "-p/--project")


def test_given_no_project_flag_when_the_remote_machine_configures_one_then_that_source_is_reported():
    config = {"remote": {"project": "myproject"}}

    assert _resolve_remote_project("", config) == ("myproject", "[remote] project in the config file")


def test_given_no_project_anywhere_when_resolving_the_remote_project_then_nothing_is_reported():
    with patch("ai_cli.config._get_main_project_name", return_value=None):
        assert _resolve_remote_project("", {"remote": {}}) == ("", "")


# --- -p given a value that is neither a prefix nor a repository name ---


def test_given_an_unknown_project_value_when_resolving_its_prefix_then_no_absent_path_is_named(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr("ai_cli.config.load_config", dict)
    monkeypatch.setattr("ai_cli.config.get_fleet_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: None)

    with pytest.raises(ProjectPrefixError) as exc_info:
        resolve_project_prefix_by_name("mp")

    message = str(exc_info.value)
    assert str(projects_dir / "mp") not in message
    assert "'mp'" in message
    assert str(projects_dir) in message


def test_given_an_unregistered_but_present_project_when_resolving_its_prefix_then_registration_is_the_remedy(
    tmp_path, monkeypatch
):
    """The unknown-input error must not swallow the case it was carved out of."""
    projects_dir = tmp_path / "projects"
    repo = projects_dir / "myproject"
    repo.mkdir(parents=True)
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr("ai_cli.config.load_config", dict)
    monkeypatch.setattr("ai_cli.config.get_fleet_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config._can_prompt", lambda: False)

    with pytest.raises(ProjectPrefixError, match="ai register"):
        resolve_project_prefix_by_name("myproject")


def test_given_an_unknown_project_value_when_a_terminal_is_attached_then_nothing_is_persisted(tmp_path, monkeypatch):
    """The prompt tier used to register a prefix against a directory that did not exist."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr("ai_cli.config.load_config", dict)
    monkeypatch.setattr("ai_cli.config.get_fleet_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config._can_prompt", lambda: True)

    with patch("builtins.input", side_effect=AssertionError("must not prompt")):
        with pytest.raises(ProjectPrefixError, match="Unknown project"):
            resolve_project_prefix_by_name("mp")

    assert list(Path(projects_dir).iterdir()) == []
