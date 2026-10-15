"""Every launch names the machine it runs on and the machine the session will run on (AI-CLI-x5vn).

A launch from one machine can start the session on another (`-R`), and the launcher's lines
did not say which machine was which, so the only way to tell was to read the remote command.
"""

from unittest.mock import patch

import pytest

from ai_cli.main import _do_session_launch


def _launch(*, remote, config):
    _do_session_launch(
        engine="c",
        name="7",
        resume=False,
        once=False,
        bare=False,
        notify=False,
        sandbox=False,
        no_worktree=False,
        remote=remote,
        project="",
        is_remote=False,
        project_prefix_override="test",
        extra_args=[],
        config=config,
        no_direnv=True,
        dry_run=True,
    )


@pytest.fixture
def local_plan():
    with (
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-test-7", "test-7")),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        yield


def test_given_a_local_launch_when_it_starts_then_both_hosts_are_this_machine(local_plan, monkeypatch, capsys):
    monkeypatch.setenv("AI_HOST", "host-a")

    _launch(remote=False, config={})

    err = capsys.readouterr().err
    assert "launcher on host-a; session on host-a (local)" in err


def test_given_a_remote_launch_when_it_starts_then_the_session_host_is_the_remote(monkeypatch, capsys):
    monkeypatch.setenv("AI_HOST", "host-a")
    config = {"remote": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}}

    _launch(remote=True, config=config)

    err = capsys.readouterr().err
    assert "launcher on host-a; session on fw.example.com (remote)" in err


def test_given_a_named_remote_machine_when_it_starts_then_the_line_names_its_alias_and_host(monkeypatch, capsys):
    monkeypatch.setenv("AI_HOST", "host-a")
    config = {
        "remote": {
            "default": "box",
            "machines": {
                "box": {"host": "box.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}
            },
        }
    }

    _launch(remote=True, config=config)

    err = capsys.readouterr().err
    assert "launcher on host-a; session on box (box.example.com, remote)" in err
