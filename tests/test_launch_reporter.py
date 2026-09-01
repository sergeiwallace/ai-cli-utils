from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.launch_reporter import InstallOrigin, LaunchReporter
from ai_cli.main import _do_session_launch, _launch_install_origin, cli


def test_given_a_launch_phase_when_reported_then_it_uses_prefixed_stderr_grammar(capsys):
    reporter = LaunchReporter()

    reporter.start(engine="Claude Code", mode="local, tmux")
    with reporter.phase("Install", "checking installed version") as phase:
        phase.outcome("editable checkout 0.8.0; current")
    reporter.handoff(engine="Claude Code", session="c-myproject-1")

    assert capsys.readouterr().err.splitlines() == [
        "[launch] Starting Claude Code session: local, tmux",
        "[launch] Install: checking installed version",
        "[launch] Install: editable checkout 0.8.0; current",
        "[launch] Ready: handing off to Claude Code (c-myproject-1)",
    ]


def test_given_quiet_launch_when_reported_then_it_emits_no_progress(capsys):
    reporter = LaunchReporter(quiet=True)

    reporter.start(engine="Gemini", mode="remote, bare")
    reporter.handoff(engine="Gemini", session="g-r-myproject-1")

    assert capsys.readouterr().err == ""


def test_given_editable_tool_environment_when_origin_is_detected_then_it_is_not_reported_as_pypi(tmp_path):
    with (
        patch("ai_cli.main._running_uv_tool_venv", return_value=tmp_path),
        patch("ai_cli.main._install_is_editable", return_value=True),
    ):
        assert _launch_install_origin() is InstallOrigin.EDITABLE_CHECKOUT


def test_given_unproven_install_origin_when_reported_then_it_remains_unknown():
    with patch("ai_cli.main._running_uv_tool_venv", return_value=None):
        assert _launch_install_origin() is InstallOrigin.UNKNOWN


def test_given_local_launch_when_worktree_and_handoff_run_then_they_are_reported(tmp_path, capsys):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    reporter = LaunchReporter()
    reporter.start(engine="Claude Code", mode="local, bare")
    with (
        patch("ai_cli.main._direnv_setup.ensure_direnv"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.session.detect_repo_root", return_value=None),
        patch("ai_cli.session.create_worktree", return_value=(worktree, False)),
        patch("ai_cli.main.pull_rebase_autostash", return_value=(MagicMock(returncode=0), None)),
        patch("ai_cli.main.detect_missing_tracked_symlinks", return_value=[]),
        patch("ai_cli.main.detect_phantom_deleted_files", return_value=[]),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        patch("ai_cli.main._exec_with_direnv", side_effect=SystemExit(0)),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="myproject",
                extra_args=[],
                config={"worktree": {"enabled": True}},
                reporter=reporter,
            )

    err = capsys.readouterr().err
    assert "[launch] Session: resolved myproject-1" in err
    assert f"[launch] Worktree: reusing {worktree}" in err
    assert "[launch] Ready: handing off to Claude Code (myproject-1)" in err


def test_given_reexec_marker_when_session_command_starts_then_it_reports_continuing(capsys, monkeypatch):
    monkeypatch.setenv("AI_CLI_LAUNCH_REEXEC", "1")
    with (
        patch("sys.argv", ["ai", "c"]),
        patch("ai_cli.main._auto_update_if_stale", return_value=False),
        patch("ai_cli.main._do_session_launch"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.tunnel._ensure_nats_tunnel"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    assert "[launch] Continuing Claude Code session: local, tmux" in capsys.readouterr().err
    assert "AI_CLI_LAUNCH_REEXEC" not in os.environ
