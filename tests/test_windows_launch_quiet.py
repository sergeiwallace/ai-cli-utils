"""A Windows launch with no tmux and no pty API starts without a warning, and says so truthfully.

Reported from a Git Bash launch on a Windows host: the first line read
``Starting Claude Code session (local, tmux)`` although tmux was not hosting the session,
and every launch printed a compaction ``Warning`` that no in-process action could clear.
The capability that is really missing (harness-driven compaction) must stay discoverable,
so ``ai doctor`` and a verbose launch report it instead.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from ai_cli import compact_transport
from ai_cli.main import cli

NO_PTY_PLATFORM = compact_transport.PtyProbe(
    False,
    "this platform has no termios module, so a pty cannot be configured",
    platform_supported=False,
)
ADVISORY = "harness cannot drive compaction"


class _WindowsSys:
    """The launcher's view of ``sys`` on Windows, and only the launcher's.

    Patching the real ``sys.platform`` would also send the standard library's own
    ``shutil.which`` down its Windows branch on this host, which is not under test.
    """

    platform = "win32"

    def __getattr__(self, name: str):
        return getattr(sys, name)


@pytest.fixture(autouse=True)
def _plain_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    # install.sh sets AI_HOST; without it, config registration prints its own warning.
    monkeypatch.setenv("AI_HOST", "test-host")


def _windows_without_tmux(stack: ExitStack) -> None:
    for target, kwargs in [
        ("ai_cli.main.sys", {"new": _WindowsSys()}),
        ("ai_cli.tmux_setup.tmux_present", {"return_value": False}),
        ("ai_cli.tmux_setup.tmux_runs", {"return_value": False}),
        ("ai_cli.compact_transport.probe_pty", {"return_value": NO_PTY_PLATFORM}),
    ]:
        stack.enter_context(patch(target, **kwargs))


def _dry_run(capsys, *flags: str) -> str:
    with ExitStack() as stack:
        _windows_without_tmux(stack)
        for target, kwargs in [
            ("sys.argv", {"new": ["ai", "c", "7", "--dry-run", *flags]}),
            ("ai_cli.session.detect_repo_root", {"return_value": "/tmp/repo"}),
            ("ai_cli.session._resolve_is_remote", {"return_value": False}),
            ("ai_cli.config.validate_registry_completeness", {"return_value": True}),
            ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
            ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
            ("ai_cli.session.build_session_name", {"return_value": ("c-myproject-7", "myproject-7")}),
            ("ai_cli.main.os.execvp", {"side_effect": AssertionError("a dry run must not exec")}),
            # The checkout this runs in may carry an .envrc on a runner without direnv; that
            # warning belongs to direnv, not to the tmux and pty behaviour under test here.
            ("ai_cli.direnv_setup.is_bypassed", {"return_value": True}),
        ]:
            stack.enter_context(patch(target, **kwargs))
        with pytest.raises(SystemExit) as exc_info:
            cli()
    assert exc_info.value.code == 0, capsys.readouterr().err
    return capsys.readouterr().err


def test_given_windows_without_tmux_when_ai_c_launches_then_the_start_line_says_bare(capsys):
    err = _dry_run(capsys)

    assert "Starting Claude Code session (local, bare)" in err, err
    assert "(local, tmux)" not in err


def test_given_windows_without_tmux_or_a_pty_when_ai_c_launches_then_no_warning_is_printed(capsys):
    err = _dry_run(capsys)

    assert "Warning" not in err, err
    assert ADVISORY not in err


def test_given_windows_without_tmux_or_a_pty_when_ai_c_launches_verbose_then_the_limit_is_shown(capsys):
    err = _dry_run(capsys, "-v")

    assert ADVISORY in err, err
    assert "Warning" not in err


def test_given_windows_without_tmux_or_a_pty_when_doctor_runs_then_it_reports_the_compaction_limit():
    from conftest import run_cli

    with ExitStack() as stack:
        _windows_without_tmux(stack)
        stack.enter_context(patch("ai_cli.direnv_setup.is_bypassed", return_value=True))
        _code, stdout, _err = run_cli(["ai", "doctor", "--dry-run"])

    row = next((line for line in stdout.splitlines() if " compact " in line), "")
    assert "MISS" in row, stdout
    assert "auto-compact still applies" in row
    assert "Install tmux" in row


def test_given_tmux_hosts_the_session_when_doctor_runs_then_compaction_is_ok():
    from conftest import run_cli

    with ExitStack() as stack:
        stack.enter_context(patch("ai_cli.tmux_setup.tmux_runs", return_value=True))
        stack.enter_context(patch("ai_cli.direnv_setup.is_bypassed", return_value=True))
        _code, stdout, _err = run_cli(["ai", "doctor", "--dry-run"])

    row = next((line for line in stdout.splitlines() if " compact " in line), "")
    assert "OK" in row, stdout


def test_given_windows_with_a_working_tmux_opted_out_when_doctor_runs_then_it_does_not_claim_tmux_is_missing():
    """psmux answers to ``tmux`` natively on Windows: an opted-out host has tmux, it just does not use it."""
    from conftest import run_cli

    with ExitStack() as stack:
        for target, kwargs in [
            ("ai_cli.main.sys", {"new": _WindowsSys()}),
            ("ai_cli.tmux_setup.tmux_runs", {"return_value": True}),
            ("ai_cli.tmux_setup.config_opts_out", {"return_value": True}),
            ("ai_cli.compact_transport.probe_pty", {"return_value": NO_PTY_PLATFORM}),
            ("ai_cli.direnv_setup.is_bypassed", {"return_value": True}),
        ]:
            stack.enter_context(patch(target, **kwargs))
        _code, stdout, _err = run_cli(["ai", "doctor", "--dry-run"])

    row = next((line for line in stdout.splitlines() if " compact " in line), "")
    assert "tmux does not host sessions here" in row, stdout
    assert "no native tmux" not in stdout
