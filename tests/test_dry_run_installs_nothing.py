"""`ai c --dry-run` must not install native tools either.

Measured on a host without direnv: `ai c 99 --dry-run` printed "Auto-install did not
succeed: apt-get (needs root ...)" before its plan said "nothing was created, started,
or reaped" -- the launch-path direnv preflight ran with its default auto_install=True,
and only the missing root permission stopped a package install. The tmux preflight
has the same default. A dry run is a promise to change nothing, so both installers are
tripwired here and the dry run is driven through the real `ai` entry point.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import cli


class _InstallTripwire(AssertionError):
    pass


def _tripwire(*_args, **_kwargs):
    raise _InstallTripwire("a dry run reached a package install")


def test_given_missing_direnv_and_tmux_when_dry_running_through_cli_then_no_installer_runs(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("AI_CLI_SKIP_DIRENV", raising=False)
    envrc = tmp_path / ".envrc"
    envrc.write_text("", encoding="utf-8")
    with (
        patch("sys.argv", ["ai", "c", "7", "--dry-run"]),
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.direnv_setup.find_envrc", return_value=Path(envrc)),
        patch("ai_cli.direnv_setup.direnv_available", return_value=False),
        patch("ai_cli.direnv_setup.install_direnv", side_effect=_tripwire),
        patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
        patch("ai_cli.tmux_setup.tmux_present", return_value=False),
        patch("ai_cli.tmux_setup.install_tmux", side_effect=_tripwire),
        patch("ai_cli.session.detect_repo_root", return_value=str(tmp_path)),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-7", "myproject-7")),
        patch("ai_cli.main.os.execvp", side_effect=AssertionError("a dry run must not exec")),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    assert "[plan] dry run -- nothing was created" in capsys.readouterr().out
