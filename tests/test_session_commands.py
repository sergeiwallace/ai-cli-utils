"""Exercise session commands through the public Click group."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from test_session_registry import _launch_local
from test_session_registry import iterm2_toml as iterm2_toml
from test_session_registry import outside_iterm2 as outside_iterm2
from test_session_registry import state_home as state_home
from test_session_state_publish import publishing as publishing
from test_session_state_publish import snapshot

from ai_cli import session_commands
from ai_cli import session_registry as registry
from ai_cli.main import _cli_group


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


@pytest.mark.parametrize("status", [0, 7])
def test_single_run_when_agent_finishes_then_original_status_and_manual_exit_are_preserved(
    publishing, runner, monkeypatch, status
):
    import subprocess

    from ai_cli import session_commands

    monkeypatch.setattr(
        session_commands.output,
        "run_relayed",
        lambda tag, op, argv, **kwargs: subprocess.CompletedProcess(argv, status),
    )
    result = runner.invoke(_cli_group, ["sessions", "_run", "c-session-1", "--", "example-command", "--argument"])
    assert result.exit_code == status
    record = registry.load_registry()["sessions"][0]
    assert record["supervisor_exit"]["cause"] == "manual_exit"
    assert record["run_state"] == "exited"
    assert record["boot_recipe"]["command"] == ["example-command", "--argument"]


def test_given_child_output_when_single_run_finishes_then_output_is_tagged(publishing, runner):
    import sys

    result = runner.invoke(
        _cli_group,
        ["sessions", "_run", "c-session-1", "--", sys.executable, "-c", "print('child-message')"],
    )
    assert result.exit_code == 0, result.output
    assert "[session] session: child-message" in result.output


@pytest.mark.parametrize("policy", ["auto", "never", "always"])
def test_given_local_record_when_policy_changes_then_ac_4_1_only_policy_and_time_change_and_publish(
    publishing, runner, monkeypatch, policy
):
    _, remote, _, _ = publishing
    registry._mutate(lambda doc: doc["sessions"].append({"kind": "remote", "name": "other-session"}) or True)
    before = registry.load_registry()
    monkeypatch.setattr(registry, "_now", lambda: "2026-01-02T03:04:05Z")
    result = runner.invoke(_cli_group, ["sessions", "policy", "c-session-1", policy])
    assert result.exit_code == 0, result.output
    after = registry.load_registry()
    expected = deepcopy(before["sessions"])
    expected[0].update(relaunch=policy, changed_at="2026-01-02T03:04:05Z")
    assert after["sessions"] == expected
    assert after["dirty_revision"] == before["dirty_revision"] + 1
    assert runner.invoke(_cli_group, ["sessions", "publish", "--tick"]).exit_code == 0
    published = snapshot(remote)["sessions"]["c-session-1"]
    assert published["relaunch"] == policy
    assert published["changed_at"] == "2026-01-02T03:04:05Z"


@pytest.mark.parametrize("slug", ["", "bad.name", "bad/name", "bad name", "bad;name", "bad\n", "é"])
def test_given_invalid_slug_when_policy_requested_then_ac_4_3_refuses_before_registry_read(runner, monkeypatch, slug):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input reached registry or launcher")

    monkeypatch.setattr(registry, "load_registry", forbidden)
    monkeypatch.setattr(session_commands, "_launch_recipe", forbidden)
    result = runner.invoke(_cli_group, ["sessions", "policy", slug, "auto"])
    assert result.exit_code != 0
    assert "session name" in result.output


@pytest.mark.parametrize("policy", ["sometimes", "AUTO", "", "auto;command"])
def test_given_invalid_policy_when_requested_then_ac_4_3_refuses_before_registry_read(runner, monkeypatch, policy):
    monkeypatch.setattr(registry, "load_registry", lambda: pytest.fail("invalid policy read registry"))
    result = runner.invoke(_cli_group, ["sessions", "policy", "c-session-1", policy])
    assert result.exit_code != 0
    assert "Invalid value" in result.output


def test_given_unknown_slug_when_policy_requested_then_refuses_without_registry_change(publishing, runner):
    before = registry.registry_path().read_bytes()
    result = runner.invoke(_cli_group, ["sessions", "policy", "missing-session", "never"])
    assert result.exit_code != 0
    assert "unknown session" in result.output
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize("change", [{"kind": "remote"}, {"owning_host": "host-b"}])
def test_given_foreign_record_when_policy_requested_then_refuses_without_registry_change(publishing, runner, change):
    registry._mutate(lambda doc: doc["sessions"][0].update(change) or True)
    before = registry.registry_path().read_bytes()
    result = runner.invoke(_cli_group, ["sessions", "policy", "c-session-1", "never"])
    assert result.exit_code != 0
    assert "not owned by this host" in result.output
    assert registry.registry_path().read_bytes() == before


def test_given_readme_when_sessions_documented_then_ac_p_4_both_commands_share_one_paragraph():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Session boot relaunch\n", 1)[1].split("\n### ", 1)[0]
    paragraph = next(text for text in section.split("\n\n") if "`ai sessions relaunch <slug>`" in text)
    assert "`ai sessions policy <slug> <auto|never|always>`" in paragraph


@pytest.mark.parametrize("missing", ["record", "recipe"])
def test_given_begin_failure_when_cli_invoked_then_cause_and_exit_status_are_preserved(runner, missing):
    if missing != "record":
        registry._mutate(
            lambda doc: (
                doc["sessions"].append(
                    {
                        "kind": "local",
                        "name": "c-session-1",
                        "boot_recipe": {},
                    }
                )
                or True
            )
        )
    result = runner.invoke(_cli_group, ["sessions", "_begin", "c-session-1"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "cannot acquire a durable supervisor generation:" in result.stderr
    assert ("not registered" if missing == "record" else "boot recipe") in result.stderr


def test_given_valid_begin_when_cli_invoked_then_stdout_is_only_generation(publishing, runner):
    import uuid

    result = runner.invoke(_cli_group, ["sessions", "_begin", "c-session-1"])
    assert result.exit_code == 0
    assert str(uuid.UUID(result.stdout.strip())) == result.stdout.strip()
    assert result.stderr == ""
