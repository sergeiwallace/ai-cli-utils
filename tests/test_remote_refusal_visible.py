"""A remote launch the far side refuses prints that refusal in the launching terminal and exits nonzero.

Over mosh the remote command's stderr used to be redirected into a diagnostic file, so the remote
side's progress lines never reached the screen, and mosh carries no exit status, so a refusal that
took longer than the fast-failure windows ended the local command with exit 0 and nothing printed.
These tests drive the real mosh wrapper command under a real shell, the real transport loop, the
real ``ai c -R`` dispatch and the real ``ai c`` refusal on the remote side.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import shlex
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_session_migrate import NEW
from test_session_migrate_on_launch import (  # noqa: F401 - fixture
    FINISH,
    _launch,
    _ledger,
    _legacy_finished_row,
    hooks,
    migrated,
    slot,
)

from ai_cli import transport

#: The mosh wrapper is POSIX shell code that only ever runs on the remote host; these tests run it
#: under a local shell. A Windows runner has none: its `bash` is the WSL launcher, which fails
#: without an installed distribution, so `shutil.which("bash")` alone does not tell.
posix_shell = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the remote mosh wrapper under a local POSIX shell; on Windows `bash` is the WSL launcher",
)

DIAGNOSTIC = ".local/state/ai-cli-utils/transport-errors/refused.stderr"
REFUSAL = (
    "[migrate] migrate: refusing to launch session-2: the migration session-1 -> session-2 is partial; "
    "finish it with `ai migrate-session session-1 session-2 --finish -r /home/user/myproject`, then launch again"
)


def _nats() -> MagicMock:
    nc = MagicMock()
    nc.nc = MagicMock()
    nc.nc.subscribe = AsyncMock()
    nc.connect = AsyncMock()
    nc.close = AsyncMock()
    return nc


def _remote_loop(tmp_path, remote_cmd: str, *, seconds: float) -> int | None:
    """Run the transport loop with mosh standing in as a local shell running the real wrapper."""
    from ai_cli.main import _mosh_remote_command

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    mosh_args = ["bash", "-c", _mosh_remote_command(remote_cmd, DIAGNOSTIC)]
    clock = itertools.chain([0.0, seconds], itertools.repeat(seconds))

    async def run():
        with (
            patch.dict(os.environ, {"HOME": str(home)}),
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path / "state"),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
            patch("ai_cli.messaging.NATSClient", return_value=_nats()),
            patch("ai_cli.transport._monotonic", side_effect=lambda: next(clock)),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            return await transport._run_transport_loop(
                ["false"],
                mosh_args,
                ["true"],
                "c-r-session-2",
                {"messaging": {"nats_servers": []}},
                diagnostic_ssh_args=["bash", "-c"],
                remote_diagnostic_file=DIAGNOSTIC,
            )

    return asyncio.run(run())


@posix_shell
def test_given_a_remote_refusal_after_minutes_over_mosh_when_the_session_ends_then_it_prints_and_returns_its_status(
    tmp_path, capsys
):
    # A subshell, as the real remote ``ai`` is its own process: a bare exit would end the wrapper too.
    remote_cmd = f'(printf "%s\\n" {shlex.quote(REFUSAL)} >&2; exit 78)'

    code = _remote_loop(tmp_path, remote_cmd, seconds=180.0)

    err = capsys.readouterr().err
    assert code == 78
    assert "refusing to launch session-2" in err
    assert "ai migrate-session session-1 session-2 --finish -r /home/user/myproject" in err
    assert not (tmp_path / "home" / DIAGNOSTIC).exists(), "the read removes the remote record"


@posix_shell
@pytest.mark.parametrize("seconds", [1.0, 12.0])
def test_given_remote_stderr_before_a_launch_log_exists_when_mosh_ends_then_the_refusal_and_status_survive(
    tmp_path, capsys, seconds
):
    remote_cmd = f'(printf "%s\\n" {shlex.quote(REFUSAL)} >&2; exit 78)'

    code = _remote_loop(tmp_path, remote_cmd, seconds=seconds)

    err = capsys.readouterr().err
    assert REFUSAL in err
    assert code == 78
    assert "transient session-name collision" not in err


@posix_shell
def test_given_a_remote_session_that_ends_cleanly_over_mosh_when_it_ends_then_nothing_is_printed_and_it_returns_0(
    tmp_path, capsys
):
    code = _remote_loop(tmp_path, '(echo "progress line" >&2; exit 0)', seconds=600.0)

    err = capsys.readouterr().err
    assert code == 0
    assert "Remote command error" not in err
    assert not (tmp_path / "home" / DIAGNOSTIC).exists()


@posix_shell
def test_given_the_mosh_wrapper_when_the_remote_command_writes_progress_then_stderr_stays_on_the_terminal(tmp_path):
    """The remote side's progress lines must reach the screen while it works, not a file."""
    from ai_cli.main import _mosh_remote_command

    home = tmp_path / "home"
    home.mkdir()
    wrapper = _mosh_remote_command('(echo "re-planning the migration" >&2; exit 0)', DIAGNOSTIC)

    done = subprocess.run(
        ["bash", "-c", wrapper], capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, check=False
    )

    assert done.returncode == 0
    assert "re-planning the migration" in done.stderr


def test_given_the_remote_side_refuses_over_mosh_when_dispatched_then_the_local_command_exits_with_its_status(
    tmp_path,
):
    config = {"remote": {"host": "server.example.com", "user": "dev", "transport": "mosh"}}

    async def refused_loop(*_args, **_kwargs):
        return 78

    def probe(*_args, **_kwargs):
        return MagicMock(stdout="/bin/bash\n", returncode=0)

    from test_remote import _answering_allocation

    with (
        patch("sys.argv", ["ai", "c", "2", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.main.subprocess.run", side_effect=_answering_allocation(probe)),
        patch("ai_cli.transport._is_vpn_active", return_value=False),
        patch("ai_cli.transport._run_transport_loop", side_effect=refused_loop),
        patch("ai_cli.transport._ensure_vpn_watcher"),
        patch("ai_cli.transport._maybe_stop_vpn_watcher"),
    ):
        from ai_cli.main import cli

        with pytest.raises(SystemExit) as exit_info:
            cli()

    assert exit_info.value.code == 78


def test_given_a_partial_migration_when_the_remote_side_refuses_then_the_refusal_is_recorded_for_the_launcher(
    migrated,  # noqa: F811 - the imported fixture
    tmp_path,
    hooks,  # noqa: F811 - the imported fixture
):
    """The real remote-side refusal lands in the file the mosh wrapper names, finish command included."""
    _refusing(migrated, hooks)
    record = tmp_path / "remote-diagnostic.stderr"

    with patch.dict(os.environ, {"AI_CLI_REMOTE_DIAGNOSTIC_FILE": str(record)}):
        code, out, err, launched = _launch(migrated)

    assert code == 1, out + err
    assert launched == []
    text = record.read_text(encoding="utf-8")
    assert f"refusing to launch {NEW}" in text
    assert f"{FINISH} {migrated['repo']}" in text
    assert _ledger(migrated)


def _refusing(fixture: dict, add_hook) -> None:
    """An unverified finish that still has a blocking hook."""
    _legacy_finished_row(fixture)
    add_hook("10-notes", "notes", fail="plan")


def _launcher_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if "[launch]" in line]


def test_given_the_remote_side_of_a_launch_when_it_prints_then_every_launcher_line_names_its_host(
    migrated,  # noqa: F811 - the imported fixture
    hooks,  # noqa: F811 - the imported fixture
):
    _refusing(migrated, hooks)

    with patch("ai_cli.session_naming.host_id", return_value="remote-host"):
        code, out, err, _launched = _launch(migrated, None, "--is-remote", "-p", "myrepo")

    lines = _launcher_lines(err)
    assert code != 0, out + err
    assert lines, err
    assert all("[remote remote-host] [launch]" in line for line in lines), lines
    assert any(f"refusing to launch {NEW}" in line and "[remote remote-host]" in line for line in err.splitlines())


def test_given_a_local_launch_when_it_prints_then_no_line_is_marked_remote(
    migrated,  # noqa: F811 - the imported fixture
    hooks,  # noqa: F811 - the imported fixture
):
    _refusing(migrated, hooks)

    with patch("ai_cli.session_naming.host_id", return_value="remote-host"):
        code, out, err, _launched = _launch(migrated)

    assert code == 1, out + err
    assert _launcher_lines(err)
    assert "[remote " not in err
