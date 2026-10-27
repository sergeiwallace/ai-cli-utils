"""Run the real session supervisor with an isolated Claude stand-in on PATH."""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "The POSIX supervisor needs a controlling pty; helper paths are tested on Windows", allow_module_level=True
    )

import json
import os
import shlex
import shutil
from pathlib import Path

from test_reload_progress import Session, session_factory  # noqa: F401

from ai_cli import config

PLUGIN = "example-plugin@example-marketplace"
OTHER = "other-plugin@other-marketplace"


@pytest.fixture
def refresh_session(monkeypatch, tmp_path, session_factory):  # noqa: F811
    original = Session._fakes
    events = tmp_path / "plugin-events.jsonl"
    launch_log = tmp_path / "launch log.txt"

    def start(*, plugins=(PLUGIN,), failure="", restart=False, shell="bash", absent=False, reconfigure=False):
        # Probe before building anything: a runner without this shell cannot run the case.
        shell_path = shutil.which(shell)
        if shell_path is None:
            pytest.skip(f"{shell} is not installed on this runner")

        def fakes(bin_dir, *args):
            original(bin_dir, *args)
            (bin_dir / "claude").write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys, time\n"
                "from pathlib import Path\n"
                f"events = Path({str(events)!r})\n"
                f"failure = {failure!r}\n"
                "args = sys.argv[1:]\n"
                "previous = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []\n"
                "version = str(1 + sum(row[0:2] == ['plugin', 'update'] for row in previous))\n"
                "with events.open('a') as log: log.write(json.dumps(args if args[0] == 'plugin' else ['start', version]) + '\\n')\n"
                "if args[0] == 'plugin':\n"
                "    step = 'list' if args[1] == 'list' else ('marketplace' if args[1] == 'marketplace' else 'update')\n"
                "    if failure == step: sys.exit(1)\n"
                "    if failure == 'timeout-' + step: time.sleep(30)\n"
                f"    if step == 'list': print(json.dumps([{{'id': {PLUGIN!r}, 'version': version}}, {{'id': {OTHER!r}, 'version': version}}]))\n"
                "else:\n"
                f"    with Path({str(tmp_path / 'agent-started')!r}).open('a') as log: log.write(str(os.getpid()) + '\\n')\n"
                f"    if {reconfigure!r}: Path(os.environ['XDG_CONFIG_HOME'], 'ai-cli-utils', 'config.toml').write_text('[launch]\\nrefresh_plugins = [\"{OTHER}\"]\\n')\n"
                f"    time.sleep(3.2 if {restart!r} and not any(row[0] == 'start' for row in previous) else 600)\n"
            )
            if absent:
                stub = bin_dir / "claude"
                assert stub.read_text().startswith(f"#!{sys.executable}")
                stub.unlink()
            # Keep subprocess imports on the same source tree as the test interpreter.
            python = bin_dir / "python3"
            assert python.is_symlink()
            python.unlink()
            source = shlex.quote(str(Path(config.__file__).resolve().parents[1]))
            python.write_text(f'#!/bin/sh\nPYTHONPATH={source} exec {shlex.quote(sys.executable)} "$@"\n')
            python.chmod(0o755)

        monkeypatch.setattr(Session, "_fakes", staticmethod(fakes))
        import ai_cli.session_script as scripts

        monkeypatch.setattr(scripts, "resolve_session_shell", lambda: shell_path)
        if absent:
            # System utilities remain available, but the user's Claude installation cannot be found.
            monkeypatch.setenv("PATH", os.pathsep.join(("/usr/bin", "/bin")))
        cfg = "" if plugins is None else "[launch]\nrefresh_plugins = " + json.dumps(plugins) + "\n"
        session = session_factory(reload_on=(), config=cfg, launch_log=launch_log, renderer=False)
        if absent:
            session.wait_for_exit()
        else:
            session.wait_for_agents(2 if restart else 1)
        rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        return rows, launch_log.read_text(), session

    return start


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_given_configured_plugins_when_fresh_launch_and_restart_run_then_updates_precede_each_start(
    refresh_session, shell
):
    rows, log, _ = refresh_session(restart=True, shell=shell)
    commands = [
        ["plugin", "list", "--json"],
        ["plugin", "marketplace", "update", "example-marketplace"],
        ["plugin", "update", PLUGIN],
        ["plugin", "list", "--json"],
    ]
    assert rows == [*commands, ["start", "2"], *commands, ["start", "3"]]
    assert f"{PLUGIN}: 1 -> 2" in log
    assert f"{PLUGIN}: 2 -> 3" in log


@pytest.mark.parametrize("failure", ["marketplace", "update", "timeout-marketplace", "timeout-update", "list"])
def test_given_failed_plugin_command_when_session_launches_then_one_warning_is_logged_and_launch_continues(
    refresh_session, failure
):
    rows, log, _ = refresh_session(failure=failure)
    assert rows[-1][0] == "start"
    assert log.count("Warning:") == (2 if failure == "list" else 1)
    assert "plugin refresh" in log.lower()
    assert ("timed out" in log) == failure.startswith("timeout-")
    assert f"{PLUGIN}:" in log


@pytest.mark.parametrize("plugins", [None, []])
def test_given_unconfigured_plugins_when_session_launches_then_no_plugin_command_runs(refresh_session, plugins):
    rows, log, _ = refresh_session(plugins=plugins)
    assert rows == [["start", "1"]]
    assert "plugin refresh" not in log.lower()


def test_given_multiple_plugins_when_session_launches_then_every_marketplace_and_plugin_is_updated(refresh_session):
    rows, log, _ = refresh_session(plugins=[PLUGIN, OTHER])
    assert ["plugin", "marketplace", "update", "other-marketplace"] in rows[:-1]
    assert ["plugin", "update", OTHER] in rows[:-1]
    assert f"{OTHER}:" in log


def test_given_config_changed_during_session_when_wrapper_restarts_then_new_plugin_list_is_used(refresh_session):
    rows, log, _ = refresh_session(restart=True, reconfigure=True)
    first_start = rows.index(["start", "2"])
    assert ["plugin", "update", PLUGIN] in rows[:first_start]
    assert ["plugin", "update", PLUGIN] not in rows[first_start:]
    assert ["plugin", "update", OTHER] in rows[first_start:]
    assert f"{OTHER}: 2 -> 3" in log


def test_given_missing_claude_when_generated_script_runs_then_refresh_is_skipped_silently(refresh_session):
    rows, log, session = refresh_session(absent=True)
    assert rows == []
    assert "plugin refresh" not in log.lower()
    assert "claude" in "\n".join(session.rows())
