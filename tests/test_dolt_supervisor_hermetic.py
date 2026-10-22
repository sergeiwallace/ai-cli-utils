"""The suite cannot depend on a Dolt supervisor the host happens to have.

A main-tree checkout at ``~/projects/<repo>`` sits beside the operator's real
environment checkout, and the autouse ``projects_dir`` fixture points at exactly that
parent. Session-launch tests then ran the host's ``dolt_server.py`` through their own
SSH ``subprocess.run`` stub and failed, while the same tests passed in every worktree
and CI checkout, where no such sibling exists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import conftest
import pytest

from ai_cli.main import cli


@pytest.fixture
def projects_dir_with_a_supervisor(tmp_path):
    script = tmp_path / "myhub" / "scripts" / "dolt_server.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    return tmp_path


def _launch_commands(projects_dir) -> list[list[str]]:
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with (
        patch("sys.argv", ["ai", "c"]),
        patch("ai_cli.config.load_config", return_value={"project": {"environment_repo": "myhub"}}),
        patch("ai_cli.main.load_config", return_value={}),
        patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
        patch("ai_cli.main.subprocess.run", run),
        patch("ai_cli.main._do_session_launch"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.tunnel._ensure_nats_tunnel"),
        pytest.raises(SystemExit),
    ):
        cli()
    return [list(map(str, call.args[0])) for call in run.call_args_list]


def _ran_a_supervisor(commands) -> bool:
    return any(any(part.endswith("dolt_server.py") for part in command) for command in commands)


def test_given_the_hosts_supervisor_beside_the_checkout_when_a_session_launches_then_it_is_never_run(
    projects_dir_with_a_supervisor, monkeypatch
):
    monkeypatch.setattr(conftest, "dolt_supervisor_is_the_hosts", lambda _script: True)

    assert not _ran_a_supervisor(_launch_commands(projects_dir_with_a_supervisor))


def test_given_a_supervisor_planted_in_the_test_temp_tree_when_a_session_launches_then_it_still_runs(
    projects_dir_with_a_supervisor,
):
    assert _ran_a_supervisor(_launch_commands(projects_dir_with_a_supervisor))
