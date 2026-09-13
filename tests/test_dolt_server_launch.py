"""Regression coverage for session-launch Dolt supervision.

Deliberately config.toml-driven, never env-var-driven (see `_dolt_server_script`'s
docstring). The `script_path` override is read via `load_config()` directly;
the `[project] projects_dir`-based fallback goes through `_find_project_dir`, so
those tests patch `ai_cli.config._get_projects_dir` -- the same seam
`conftest.py`'s `_projects_dir_contains_the_checkout` autouse fixture patches
and explicitly documents as overridable by an inner test-level patch.
"""

from __future__ import annotations

from unittest.mock import patch

from ai_cli.main import _ensure_dolt_server


def test_given_configured_script_path_when_launching_then_runs_ensure(monkeypatch, tmp_path):
    script = tmp_path / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = {"dolt_server": {"script_path": str(script)}}

    with (
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.main.subprocess.run") as run,
    ):
        _ensure_dolt_server()

    command = run.call_args.args[0]
    assert command[1:3] == [str(script), "ensure"]
    assert command[-2:] == ["--repo", str(tmp_path)]
    assert run.call_args.kwargs["check"] is False


def test_given_projects_dir_config_when_launching_then_resolves_its_dolt_server(monkeypatch, tmp_path):
    """[project] projects_dir, not an env var, is what a config edit actually changes.

    A CC session or agent already running picks this up on its NEXT launch --
    load_config() re-reads config.toml from disk every call, unlike an env var,
    which would need every already-running process to restart.
    """
    harness = tmp_path / "ai-harness"
    (harness / "scripts").mkdir(parents=True)
    script = harness / "scripts" / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    other_cwd = tmp_path / "job-pilot"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    with (
        patch("ai_cli.main.load_config", return_value={}),
        patch("ai_cli.config._get_projects_dir", return_value=tmp_path),
        patch("ai_cli.main.subprocess.run") as run,
    ):
        _ensure_dolt_server()

    command = run.call_args.args[0]
    assert command[1] == str(script)
    assert command[-1] == str(other_cwd)


def test_given_no_supervisor_when_launching_then_does_not_run_a_guess(monkeypatch, tmp_path):
    empty_projects_dir = tmp_path / "empty-projects"
    empty_projects_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    with (
        patch("ai_cli.main.load_config", return_value={}),
        patch("ai_cli.config._get_projects_dir", return_value=empty_projects_dir),
        patch("ai_cli.main.subprocess.run") as run,
    ):
        _ensure_dolt_server()

    run.assert_not_called()
