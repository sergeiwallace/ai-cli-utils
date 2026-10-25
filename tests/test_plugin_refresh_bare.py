"""Bare launches use the same pre-start refresh, including on native Windows."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_bare_worktree import _launch_kwargs

from ai_cli import config
from ai_cli.main import _do_session_launch

PLUGIN = "example-plugin@example-marketplace"


@pytest.fixture
def plugin_stub(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin space"
    bin_dir.mkdir()
    events = tmp_path / "events.jsonl"
    stub = bin_dir / "stub.py"
    stub.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"events = Path({str(events)!r})\n"
        "rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []\n"
        "with events.open('a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "version = '2' if any(row[1] == 'update' for row in rows) else '1'\n"
        f"print(json.dumps([{{'id': {PLUGIN!r}, 'version': version}}]))\n"
    )
    executable = bin_dir / ("claude.cmd" if sys.platform == "win32" else "claude")
    if sys.platform == "win32":
        executable.write_text(f'@echo off\n"{sys.executable}" "{stub}" %*\n')
    else:
        executable.write_text(f'#!{sys.executable}\nexec(compile(open({str(stub)!r}).read(), {str(stub)!r}, "exec"))\n')
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    path = tmp_path / "config.toml"
    path.write_text(f'[launch]\nrefresh_plugins = ["{PLUGIN}"]\n')
    monkeypatch.setattr(config, "config_file_text", lambda: str(path))
    run = subprocess.run

    def run_stub(argv, **kwargs):
        if Path(argv[0]).name in {"claude", "claude.cmd"}:
            assert Path(argv[0]) == executable
            return run([sys.executable, str(stub), *argv[1:]], **kwargs)
        return run(argv, **kwargs)

    # Explicitly replace the protected agent boundary with the test-owned Python stub.
    monkeypatch.setattr(subprocess, "run", run_stub)
    return events


def test_given_configured_plugin_when_bare_launcher_hands_off_then_refreshed_version_is_ready(
    plugin_stub, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    def handoff(file, args):
        rows = [json.loads(line) for line in plugin_stub.read_text().splitlines()]
        assert rows == [
            ["plugin", "list", "--json"],
            ["plugin", "marketplace", "update", "example-marketplace"],
            ["plugin", "update", PLUGIN],
            ["plugin", "list", "--json"],
        ]
        assert file == "claude"
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=handoff),
        patch("ai_cli.main._pty_handoff", return_value=handoff),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        pytest.raises(SystemExit, match="0"),
    ):
        _do_session_launch(**_launch_kwargs(no_worktree=True, project_prefix_override="myproject"))
    assert f"{PLUGIN}: 1 -> 2" in capsys.readouterr().err


def test_given_failed_commands_when_bare_refresh_runs_then_warnings_are_emitted_without_blocking(monkeypatch, capsys):
    from ai_cli import plugin_refresh
    from ai_cli.main import _refresh_claude_plugins

    monkeypatch.setattr(plugin_refresh.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(config, "load_config", lambda **_: {"launch": {"refresh_plugins": [PLUGIN]}})
    monkeypatch.setattr(
        plugin_refresh.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "claude")),
    )
    _refresh_claude_plugins()
    assert capsys.readouterr().err.count("Warning:") == 4
