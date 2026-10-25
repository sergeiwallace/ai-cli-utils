"""Configured plugins refresh before the generated launch script starts its agent."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from ai_cli import config

PLUGIN = "example-plugin@example-marketplace"


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_given_changed_launch_config_when_refresh_runs_then_current_platform_config_is_read(
    tmp_path, monkeypatch, platform
):
    from ai_cli import plugin_refresh

    monkeypatch.setattr(config.sys, "platform", platform)
    monkeypatch.setenv("APPDATA", str(tmp_path / "App Data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config space"))
    path = Path(config.config_file_text())
    path.parent.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(plugin_refresh.shutil, "which", lambda _: "C:/Program Files/Claude/claude.exe")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"id": PLUGIN, "version": "2.0"}]))

    monkeypatch.setattr(plugin_refresh.subprocess, "run", run)
    path.write_text("[launch]\nrefresh_plugins = []\n")
    plugin_refresh.refresh_plugins()
    assert calls == []
    path.write_text(f'[launch]\nrefresh_plugins = ["{PLUGIN}"]\n')
    plugin_refresh.refresh_plugins()
    assert [argv[1:] for argv, _ in calls] == [
        ["plugin", "list", "--json"],
        ["plugin", "marketplace", "update", "example-marketplace"],
        ["plugin", "update", PLUGIN],
        ["plugin", "list", "--json"],
    ]
    assert all(argv[0] == "C:/Program Files/Claude/claude.exe" for argv, _ in calls)
    assert all(0 < kwargs["timeout"] <= 10 and kwargs["check"] for _, kwargs in calls)


@pytest.mark.parametrize("value", ["bad", ["bad"], ["a@b@c"], ["-a@b"], ["a@b\n"], [1], None])
def test_given_invalid_plugin_ids_when_config_is_read_then_key_is_rejected(value):
    with pytest.raises(ValueError, match=r"launch.refresh_plugins"):
        config.read_launch_config({"launch": {"refresh_plugins": value}})


def test_given_default_config_when_launch_config_is_read_then_no_plugins_are_configured():
    assert config.read_launch_config(tomllib.loads(config.DEFAULT_CONFIG))["refresh_plugins"] == []
    assert config.read_launch_config({})["refresh_plugins"] == []


def test_given_missing_claude_when_refresh_runs_then_it_is_silent(monkeypatch, capsys):
    from ai_cli import plugin_refresh

    monkeypatch.setattr(plugin_refresh.shutil, "which", lambda _: None)
    monkeypatch.setattr(plugin_refresh, "load_config", lambda **_: pytest.fail("should skip config as well"))
    plugin_refresh.refresh_plugins()
    assert capsys.readouterr() == ("", "")
