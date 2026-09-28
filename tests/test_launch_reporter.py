from __future__ import annotations

import ast
import io
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from ai_cli import direnv_setup as direnv_setup_module
from ai_cli import launch_reporter
from ai_cli import main as main_module
from ai_cli import session as session_module
from ai_cli import tmux_setup as tmux_setup_module
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


# --- styling ------------------------------------------------------------------


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_given_a_tty_stream_when_reported_then_tokens_are_styled(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = _TtyStream()

    reporter = LaunchReporter(stream=stream)
    reporter.phase("Install").outcome("current")
    reporter.handoff(engine="Claude Code", session="c-myproject-1")

    out = stream.getvalue()
    assert click.style("[launch]", dim=True) in out
    assert click.style("Install:", bold=True, fg="cyan") in out
    assert click.style("Ready:", bold=True, fg="green") in out


def test_given_a_non_tty_stream_when_reported_then_no_escape_sequence_reaches_it(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()

    reporter = LaunchReporter(stream=stream)
    reporter.start(engine="Claude Code", mode="local, tmux")
    with reporter.phase("Worktree", "creating isolated worktree") as phase:
        phase.outcome("created /home/user/src/myproject/.worktrees/c-myproject-1")
    reporter.warning("something to know")
    reporter.error("something went wrong")
    reporter.handoff(engine="Claude Code", session="c-myproject-1")

    out = stream.getvalue()
    assert "\x1b" not in out
    assert out.splitlines() == [
        "[launch] Starting Claude Code session: local, tmux",
        "[launch] Worktree: creating isolated worktree",
        "[launch] Worktree: created /home/user/src/myproject/.worktrees/c-myproject-1",
        "[launch] Warning: something to know",
        "[launch] Error: something went wrong",
        "[launch] Ready: handing off to Claude Code (c-myproject-1)",
    ]


@pytest.mark.parametrize(
    ("variable", "value"),
    [("NO_COLOR", "1"), ("TERM", "dumb")],
    ids=["no-color", "term-dumb"],
)
def test_given_a_colour_opt_out_when_the_stream_is_a_tty_then_no_escape_sequence_is_emitted(
    monkeypatch, variable, value
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv(variable, value)
    stream = _TtyStream()

    LaunchReporter(stream=stream).phase("Install").outcome("current")

    assert "\x1b" not in stream.getvalue()
    assert stream.getvalue() == "[launch] Install: current\n"


def test_given_a_tty_stream_when_reported_then_the_logger_receives_plain_text(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("ai_cli.test.launch_reporter.plain")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(_Collect())

    LaunchReporter(stream=_TtyStream(), logger=logger).phase("Install").outcome("current")

    assert [record.getMessage() for record in records] == ["[launch] Install: current"]


# --- quiet policy ---------------------------------------------------------------


def test_given_quiet_launch_when_a_warning_or_error_is_reported_then_it_is_still_emitted(capsys):
    reporter = LaunchReporter(quiet=True)

    reporter.phase("Install").outcome("current")
    reporter.warning("auto-update failed; the existing installation is intact")
    reporter.error("tmux client is 3.5a but the running server is 3.4")

    assert capsys.readouterr().err.splitlines() == [
        "[launch] Warning: auto-update failed; the existing installation is intact",
        "[launch] Error: tmux client is 3.5a but the running server is 3.4",
    ]


def test_given_warning_and_error_when_logged_then_they_carry_their_levels():
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("ai_cli.test.launch_reporter.levels")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(_Collect())
    reporter = LaunchReporter(quiet=True, logger=logger, stream=io.StringIO())

    reporter.warning("w")
    reporter.error("e")

    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.WARNING, "[launch] Warning: w"),
        (logging.ERROR, "[launch] Error: e"),
    ]


# --- elapsed time and heartbeat -------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("ai_cli.launch_reporter.monotonic", fake)
    return fake


def test_given_a_slow_started_phase_when_its_outcome_is_recorded_then_elapsed_is_shown(clock, capsys):
    reporter = LaunchReporter(heartbeat_seconds=3600)

    with reporter.phase("Worktree", "synchronizing") as phase:
        clock.now += 12.4
        phase.outcome("ready")

    assert capsys.readouterr().err.splitlines()[-1] == "[launch] Worktree: ready (12.4s)"


def test_given_a_fast_started_phase_when_its_outcome_is_recorded_then_elapsed_is_omitted_unless_verbose(clock, capsys):
    for verbose, expected in ((False, "[launch] Remote: host ready"), (True, "[launch] Remote: host ready (0.3s)")):
        reporter = LaunchReporter(verbose=verbose, heartbeat_seconds=3600)
        with reporter.phase("Remote", "probing configured host") as phase:
            clock.now += 0.3
            phase.outcome("host ready")
        assert capsys.readouterr().err.splitlines()[-1] == expected


def test_given_a_phase_without_a_start_line_when_its_outcome_is_slow_then_no_elapsed_is_appended(clock, capsys):
    reporter = LaunchReporter(verbose=True, heartbeat_seconds=3600)
    phase = reporter.phase("Session")
    clock.now += 5
    phase.outcome("resolved c-myproject-1")

    assert capsys.readouterr().err == "[launch] Session: resolved c-myproject-1\n"


def test_given_a_started_phase_when_no_outcome_is_recorded_then_done_is_reported(capsys):
    reporter = LaunchReporter(heartbeat_seconds=3600)

    with reporter.phase("Update", "syncing remote"):
        pass

    assert capsys.readouterr().err.splitlines() == [
        "[launch] Update: syncing remote",
        "[launch] Update: done",
    ]


def test_given_a_started_phase_when_it_outlives_the_heartbeat_threshold_then_heartbeats_persist_until_outcome(
    capsys,
):
    reporter = LaunchReporter(heartbeat_seconds=0.05)

    with reporter.phase("Worktree", "creating isolated worktree") as phase:
        time.sleep(0.3)
        phase.outcome("created")
    err = capsys.readouterr().err
    beats = [line for line in err.splitlines() if "still creating isolated worktree (" in line]
    assert beats, err
    assert all(line.endswith("s elapsed)") for line in beats)
    assert err.splitlines()[-1].startswith("[launch] Worktree: created")

    time.sleep(0.2)
    assert capsys.readouterr().err == "", "a heartbeat fired after the phase had ended"


def test_given_a_phase_without_a_start_line_when_it_takes_long_then_no_heartbeat_is_emitted(capsys):
    reporter = LaunchReporter(heartbeat_seconds=0.05)

    phase = reporter.phase("Session")
    time.sleep(0.2)
    phase.outcome("resolved c-myproject-1")

    assert capsys.readouterr().err == "[launch] Session: resolved c-myproject-1\n"


# --- failure paths --------------------------------------------------------------


def test_given_an_exception_inside_a_started_phase_when_it_escapes_then_the_phase_is_named_and_it_propagates(
    clock, capsys
):
    reporter = LaunchReporter(quiet=True, heartbeat_seconds=3600)

    def _create_worktree() -> None:
        with reporter.phase("Worktree", "creating isolated worktree"):
            clock.now += 3.2
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _create_worktree()

    # Quiet suppresses progress, never failures.
    assert capsys.readouterr().err == "[launch] Worktree: failed after 3.2s: boom\n"


def test_given_an_interrupt_inside_a_started_phase_when_it_escapes_then_the_cancelled_phase_is_named(clock, capsys):
    reporter = LaunchReporter(heartbeat_seconds=3600)

    def _probe() -> None:
        with reporter.phase("Remote", "probing configured host"):
            clock.now += 4
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _probe()

    assert capsys.readouterr().err.splitlines()[-1] == "[launch] Remote: interrupted after 4.0s"


def test_given_a_system_exit_inside_a_started_phase_when_it_escapes_then_nothing_extra_is_reported(capsys):
    reporter = LaunchReporter(heartbeat_seconds=3600)

    def _allocate() -> None:
        with reporter.phase("Session", "allocating remote session"):
            reporter.error("no free slot")
            sys.exit(1)

    with pytest.raises(SystemExit):
        _allocate()

    assert capsys.readouterr().err.splitlines() == [
        "[launch] Session: allocating remote session",
        "[launch] Error: no free slot",
    ]


# --- active reporter --------------------------------------------------------------


def test_given_no_activated_reporter_when_active_is_asked_then_a_default_reporter_still_prints(monkeypatch, capsys):
    monkeypatch.setattr(launch_reporter, "_active", None)

    launch_reporter.active().phase("Worktree").outcome("relocated")

    assert capsys.readouterr().err == "[launch] Worktree: relocated\n"


def test_given_an_activated_reporter_when_active_is_asked_then_its_policy_applies_and_can_be_restored(
    monkeypatch, capsys
):
    monkeypatch.setattr(launch_reporter, "_active", None)
    default = launch_reporter.active()
    quiet = LaunchReporter(quiet=True)

    previous = quiet.activate()
    launch_reporter.active().phase("Worktree").outcome("relocated")
    assert previous is default
    assert launch_reporter.active() is quiet
    assert capsys.readouterr().err == ""

    previous.activate()
    assert launch_reporter.active() is default


# --- the launch path uses one reporter ---------------------------------------------


def _launch_functions() -> list[ast.FunctionDef]:
    tree = ast.parse(Path(main_module.__file__).read_text(encoding="utf-8"))
    wanted = {"_do_session_launch", "_session_command", "_exit_missing_project_dir"}
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in wanted]


def test_given_the_launch_path_when_scanned_then_no_line_bypasses_the_reporter():
    """Every launcher-owned line goes through the reporter, never a bare print.

    The two dry-run plan printers are the deliberate exception: they are a stdout
    report of resolved values, not progress, and stdout is the right stream for it.
    """
    functions = _launch_functions()
    assert {node.name for node in functions} == {"_do_session_launch", "_session_command", "_exit_missing_project_dir"}
    prints = [
        f"{node.name}:{call.lineno}"
        for node in functions
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"
    ]
    assert prints == []
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "if reporter is not None" not in source


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        (session_module, '"[launch]'),
        (tmux_setup_module, "ai-cli-utils: "),
        (tmux_setup_module, "ai-cli: "),
        (direnv_setup_module, "ai-cli-utils: "),
    ],
)
def test_given_a_launch_adjacent_module_when_scanned_then_no_copied_prefix_remains(module, forbidden):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert forbidden not in source


def test_given_no_reporter_when_launching_then_the_active_reporter_is_used(tmp_path, monkeypatch, capsys):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(launch_reporter, "_active", None)
    with (
        patch("ai_cli.main._direnv_setup.ensure_direnv", return_value=MagicMock(installed=True, detail="")),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.session.detect_repo_root", return_value=None),
        patch("ai_cli.session.create_worktree", return_value=(worktree, True)),
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
            )

    err = capsys.readouterr().err
    assert "[launch] Worktree: creating isolated worktree" in err
    assert f"[launch] Worktree: created {worktree} (disable with -W/--no-worktree" in err
    assert "[launch] Ready: handing off to Claude Code (myproject-1)" in err


def test_given_a_resume_with_no_matching_session_when_launching_then_the_refusal_is_an_error_line(capsys):
    reporter = LaunchReporter()
    with (
        patch("ai_cli.main._direnv_setup.ensure_direnv", return_value=MagicMock(installed=True, detail="")),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.main._tmux_setup.tmux_runs", return_value=True),
        patch("ai_cli.main._tmux_setup.formats_expand", return_value=True),
        patch("ai_cli.main._tmux_setup.probe", return_value=MagicMock(versions_disagree=False)),
        patch("ai_cli.main._tmux_setup.report_lines", return_value=[]),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.resolve_session", return_value=None),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(
                engine="c",
                name="7",
                resume=True,
                once=False,
                bare=False,
                notify=False,
                sandbox=False,
                no_worktree=True,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="myproject",
                extra_args=[],
                config={},
                reporter=reporter,
            )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[launch] Error: no matching session found for 'c-myproject-7'" in captured.err


def test_given_quiet_session_command_when_it_starts_then_the_active_reporter_carries_the_policy(monkeypatch):
    monkeypatch.setattr(launch_reporter, "_active", None)
    seen: dict[str, LaunchReporter] = {}

    def _capture(**kwargs):
        seen["active"] = launch_reporter.active()
        seen["passed"] = kwargs["reporter"]

    with (
        patch("sys.argv", ["ai", "c", "-q"]),
        patch("ai_cli.main._ensure_dolt_server"),
        patch("ai_cli.main._auto_update_if_stale", return_value=False),
        patch("ai_cli.main._do_session_launch", side_effect=_capture),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.tunnel._ensure_nats_tunnel"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    assert seen["active"] is seen["passed"]
    assert seen["active"].quiet is True
    # The launch's reporter is bound to this process's stderr and launch log; once
    # the launch has unwound it must not stay active for whatever runs next.
    assert launch_reporter.active() is not seen["active"]


class _FlushRecorder(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushed_after: list[str] = []

    def flush(self) -> None:
        super().flush()
        self.flushed_after.append(self.getvalue())


def test_given_a_handoff_when_reported_then_the_line_is_flushed_before_returning():
    """The exec that follows replaces the process; a buffered line would vanish."""
    stream = _FlushRecorder()

    LaunchReporter(stream=stream).handoff(engine="Claude Code", session="c-myproject-1")

    assert stream.flushed_after[-1] == "[launch] Ready: handing off to Claude Code (c-myproject-1)\n"
