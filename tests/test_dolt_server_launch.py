"""Regression coverage for session-launch Dolt supervision."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ai_cli.main import _ensure_dolt_server


def _clear_resolution_env(monkeypatch, home: Path) -> None:
    """Neutralize every candidate so a test starts from a known-empty slate.

    ``home`` stands in for ``$HOME`` so the hardcoded ``~/projects/ai-harness``
    fallback cannot accidentally resolve to this machine's REAL ai-harness
    checkout -- without this, a "no supervisor configured" test would silently
    find the real script and stop testing what it claims to.
    """
    monkeypatch.delenv("AI_DOLT_SERVER_SCRIPT", raising=False)
    monkeypatch.delenv("AI_HARNESS_ROOT", raising=False)
    monkeypatch.delenv("PROJECTS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))


def test_given_configured_supervisor_when_launching_then_runs_ensure(monkeypatch, tmp_path):
    _clear_resolution_env(monkeypatch, tmp_path)
    script = tmp_path / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setenv("AI_DOLT_SERVER_SCRIPT", str(script))
    monkeypatch.chdir(tmp_path)

    with patch("ai_cli.main.subprocess.run") as run:
        _ensure_dolt_server()

    command = run.call_args.args[0]
    assert command[1:3] == [str(script), "ensure"]
    assert command[-2:] == ["--repo", str(tmp_path)]
    assert run.call_args.kwargs["check"] is False


def test_given_ai_harness_root_when_launching_then_resolves_its_dolt_server(monkeypatch, tmp_path):
    harness = tmp_path / "ai-harness"
    (harness / "scripts").mkdir(parents=True)
    script = harness / "scripts" / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    _clear_resolution_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_HARNESS_ROOT", str(harness))
    other_cwd = tmp_path / "job-pilot"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    with patch("ai_cli.main.subprocess.run") as run:
        _ensure_dolt_server()

    command = run.call_args.args[0]
    assert command[1] == str(script)
    assert command[-1] == str(other_cwd)


def test_given_no_supervisor_when_launching_then_does_not_run_a_guess(monkeypatch, tmp_path):
    _clear_resolution_env(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    with patch("ai_cli.main.subprocess.run") as run:
        _ensure_dolt_server()

    run.assert_not_called()
