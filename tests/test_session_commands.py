"""Exercise session commands through the public Click group."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from test_session_registry import _launch_local
from test_session_state_publish import snapshot

from ai_cli import session_registry as registry
from ai_cli.main import _cli_group

pytest_plugins = ["test_session_registry", "test_session_state_publish"]


@pytest.fixture
def runner():
    return CliRunner()


def test_registered_session_when_cli_marks_then_list_and_published_ref_show_state(publishing, runner):
    _, remote, _, clock = publishing
    result = runner.invoke(
        _cli_group, ["sessions", "mark", "c-session-1", "exited", "-c", "manual_exit", "-e", '{"source":"test"}']
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(runner.invoke(_cli_group, ["sessions", "list", "-j"]).output)
    assert doc[0]["run_state"] == "exited" and doc[0]["cause"] == "manual_exit"
    assert runner.invoke(_cli_group, ["sessions", "publish", "-t"]).exit_code == 0
    assert snapshot(remote)["sessions"]["c-session-1"]["cause"] == "manual_exit"
    registry.record_exit("c-session-1", cause="manual_exit", evidence={"source": "hook"})
    assert runner.invoke(_cli_group, ["sessions", "mark", "c-session-1", "running"]).exit_code == 0
    assert registry.load_registry()["sessions"][0]["ended_at"] is None
    clock[0] += 60
    assert runner.invoke(_cli_group, ["sessions", "publish", "--tick"]).exit_code == 0
    assert snapshot(remote)["sessions"]["c-session-1"]["run_state"] == "running"


@pytest.mark.parametrize(
    "arguments",
    [
        ["missing", "running"],
        ["missing", "exited"],
        ["missing", "exited", "-c", "manual_exit", "-e", "[]"],
        ["missing", "exited", "-c", "manual_exit", "-e", "invalid"],
    ],
)
def test_invalid_mark_when_cli_invoked_then_failure_is_reported(runner, arguments):
    assert runner.invoke(_cli_group, ["sessions", "mark", *arguments]).exit_code != 0


@pytest.mark.parametrize("is_remote", [False, True])
def test_tracking_disabled_when_destination_launched_then_local_boot_owner_is_registered(
    state_home, iterm2_toml, outside_iterm2, is_remote
):
    iterm2_toml("[iterm2.persistence]\nenabled = false\n")
    with patch("ai_cli.session._tmux_session_names", return_value=[]):
        _, records = _launch_local(state_home, argv=["ai", "c", "1"], is_remote=is_remote)
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "local"
    assert record["name"].startswith("c-r-" if is_remote else "c-")
    assert record["boot_recipe"]["session_name"] == record["name"]
    assert "-R" not in record["boot_recipe"]["argv"]
    assert record["boot_recipe"]["cwd"]


@pytest.mark.parametrize("cause", ["manual_exit", "unknown"])
def test_forbidden_cause_when_config_loaded_then_loader_refuses_it(tmp_path, monkeypatch, cause):
    from ai_cli import config

    root = tmp_path / "config"
    root.mkdir()
    (root / "config.toml").write_text(f'[session_relaunch]\ncauses = ["{cause}"]\n')
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: root)
    with pytest.raises(ValueError, match="permits only"):
        config.load_config()
