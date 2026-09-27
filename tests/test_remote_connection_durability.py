"""A remote session must survive an idle network, and must hand the terminal back.

Two defects with one report behind them (AI-CLI-w679). The operator's session died with

    Shared connection to <instance-id> closed.

after a while of reading rather than typing, and the local shell was then left echoing
``35;66;6M`` at every mouse movement, fixable only by killing the terminal.

Both halves are about what happens on a connection that is ESTABLISHED, which is the case
neither the existing ``ConnectTimeout`` options nor the fast-failure branches in
``_run_transport_loop`` address -- every one of those bounds or diagnoses *setup*.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import tmux_runnable  # noqa: F401  (imported for parity with sibling modules)

from ai_cli import transport as _transport
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, _run_transport_loop, cli

MOSH_ARGS = ["mosh", "user@host", "--", "bash", "-l", "-c", "ai c"]
CLEANUP_CMD = ["ai", "internal", "cleanup-session-files", "c-r-session-1"]
SESSION = "c-r-session-1"
CONFIG = {"messaging": {"nats_servers": ["nats://localhost:4222"]}}


def _make_proc(returncode=0, poll_sequence=None):
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.poll = MagicMock(side_effect=poll_sequence if poll_sequence is not None else [None, returncode])
    proc.wait = MagicMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _mock_nats_client():
    nc = MagicMock()
    nc.connect = AsyncMock()
    nc.close = AsyncMock()
    nc.nc = None
    return nc


def _capture_ssh_argv(config):
    """Drive a real ``ai c --remote`` launch and return the interactive ssh argv."""
    captured = {}

    def fake_probe(command, **_kwargs):
        if command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
        return MagicMock(returncode=1, stdout="")

    async def fake_transport_loop(ssh_args, mosh_args, *_args, **_kwargs):
        captured["ssh_args"] = ssh_args
        captured["mosh_args"] = mosh_args

    # ``os.execvp`` MUST be patched here, and not as belt-and-braces: the remote branch
    # ends in ``os.execvp("zsh", ["zsh", "-c", "<the ssh command>; <cleanup>"])``, and the
    # conftest spawn guard allowlists by program name, where the program is ``zsh`` rather
    # than ``ssh``. Unpatched, this test REPLACES the pytest process with a shell that runs
    # the real ssh invocation -- measured: the run ended mid-collection with exit 0 and no
    # summary. Tracked as its own guard gap; see AI-CLI-013v.
    def _refuse_exec(*_args, **_kwargs):
        raise SystemExit(0)

    with (
        patch("os.execvp", side_effect=_refuse_exec),
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
        patch("ai_cli.transport._is_vpn_active", return_value=False),
        patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
        patch("ai_cli.transport._ensure_vpn_watcher"),
        patch("ai_cli.transport._maybe_stop_vpn_watcher"),
    ):
        with pytest.raises(SystemExit):
            cli()
    return captured


class TestTheEstablishedSessionIsKeptAlive:
    """An idle SSH session sends nothing, so an idle-timeout reaper cannot tell it is alive.

    The reaper in the reported case is a managed SSH data channel with a 20-minute idle
    default, but a corporate NAT or a home router does the same thing on a shorter timer.
    A keepalive fixes both the detection and the prevention: the probe traffic itself is
    what stops the flow being classed as idle.
    """

    def test_given_an_interactive_remote_launch_when_ssh_argv_is_built_then_it_carries_a_keepalive(self):
        """The reproduction. This is the long-lived connection; it had no keepalive at all.

        ``tunnel.py`` already sets exactly these two options for its own short-lived
        forward, so the codebase had the idiom and applied it to the connection that
        needed it least.
        """
        captured = _capture_ssh_argv({"remote": {"host": "server.example.com", "user": "dev", "transport": "mosh"}})

        assert "ServerAliveInterval=30" in captured["ssh_args"]
        assert "ServerAliveCountMax=3" in captured["ssh_args"]

    def test_given_a_configured_keepalive_interval_when_ssh_argv_is_built_then_it_is_used(self):
        """Config over code: a hostile network needs a shorter interval than a LAN.

        A 20-minute idle reaper and a 60-second one are both real, and no single default
        serves both, so the value has to be reachable without editing source.
        """
        captured = _capture_ssh_argv(
            {
                "remote": {
                    "host": "server.example.com",
                    "user": "dev",
                    "transport": "mosh",
                    "server_alive_interval": 15,
                    "server_alive_count_max": 8,
                }
            }
        )

        assert "ServerAliveInterval=15" in captured["ssh_args"]
        assert "ServerAliveCountMax=8" in captured["ssh_args"]
        assert "ServerAliveInterval=30" not in captured["ssh_args"]

    def test_given_the_keepalive_is_added_when_argv_is_built_then_the_remote_command_stays_last(self):
        """Ordering guard. ``ssh`` takes options before the destination, and the remote
        command must remain the final argument -- inserting ``-o`` pairs in the wrong
        place silently turns the command into an option argument."""
        captured = _capture_ssh_argv({"remote": {"host": "server.example.com", "user": "dev", "transport": "mosh"}})

        assert captured["ssh_args"][-1].startswith("/usr/bin/bash -l -c ")
        assert captured["ssh_args"][-2] == "dev@server.example.com"


class TestTheTerminalIsHandedBack:
    """A dropped connection leaves the local terminal in whatever modes the remote set.

    tmux and the agent both enable mouse reporting, bracketed paste and an extended key
    mode. Those are turned off by the remote on a clean exit. A connection that dies has
    no clean exit, so the modes persist locally and the shell starts receiving mouse
    reports as literal keystrokes -- which is the ``35;66;6M`` the operator saw, an SGR
    mouse report (``CSI < 35 ; 66 ; 6 M``) with its introducer already consumed.
    """

    def _drive_loop(self, tmp_path, returncode=0, poll_sequence=None, isatty=True):
        proc = _make_proc(returncode=returncode, poll_sequence=poll_sequence or [None, returncode])
        nc = _mock_nats_client()
        written = []

        stream = MagicMock()
        stream.write = MagicMock(side_effect=lambda text: written.append(text))
        stream.isatty = MagicMock(return_value=isatty)
        stream.fileno = MagicMock(return_value=1)

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=True),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 3600.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch.object(_transport.sys, "stdout", stream),
            ):
                await _run_transport_loop(["ssh", "-t", "user@host", "cmd"], MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        return "".join(written)

    def test_given_a_session_that_ends_when_the_loop_exits_then_mouse_reporting_is_turned_off(self, tmp_path):
        """The reproduction for the reported symptom.

        All three tracking modes, because the remote may have enabled any of them, and
        SGR/urxvt encodings too -- disabling tracking while leaving the encoding set is
        what produces a half-decoded report rather than none.
        """
        emitted = self._drive_loop(tmp_path)

        for mode in ("1000", "1002", "1003", "1006", "1015"):
            assert f"\x1b[?{mode}l" in emitted, f"mouse mode {mode} was never disabled"

    def test_given_the_loop_exits_when_restoring_then_the_other_persistent_modes_are_reset_too(self, tmp_path):
        """Mouse reporting is the symptom that was noticed, not the whole leak.

        Bracketed paste makes a paste arrive wrapped in literal ``200~``/``201~``; focus
        reporting emits a stray ``I``/``O`` on every window switch; a left-behind
        alternate screen or hidden cursor makes the shell look broken. All are set by the
        same remote programs and cleared by the same clean exit that did not happen.
        """
        emitted = self._drive_loop(tmp_path)

        assert "\x1b[?2004l" in emitted, "bracketed paste"
        assert "\x1b[?1004l" in emitted, "focus reporting"
        assert "\x1b[?1049l" in emitted, "alternate screen"
        assert "\x1b[?25h" in emitted, "cursor visibility"
        assert "\x1b[>4;0m" in emitted, "xterm modifyOtherKeys"

    def test_given_a_child_that_had_to_be_killed_when_the_loop_exits_then_the_terminal_is_still_restored(
        self, tmp_path
    ):
        """The path that matters most, and the one a ``finally`` exists for.

        A child that exits non-zero after a long session is the dropped-connection case:
        ``ssh`` restores the local tty itself when it exits or takes a SIGTERM, but a
        connection reset gives the remote no chance to send its own disable sequences, so
        the restore has to be unconditional on the way out rather than tied to a
        successful exit.
        """
        emitted = self._drive_loop(tmp_path, returncode=255, poll_sequence=[None, 255])

        assert "\x1b[?1006l" in emitted
        assert "\x1b[?2004l" in emitted

    def test_given_the_pure_ssh_launch_when_it_runs_then_it_goes_through_the_reconnecting_runner(self):
        """The path the report came from, and the one a ``finally`` could not reach.

        ``transport: ssh`` does not use ``_run_transport_loop``. It used to ``execvp`` a
        shell, replacing this process -- so no Python cleanup of ours survived and a restore
        placed only in the loop's ``finally`` would have fixed the mosh path while leaving
        the reported one exactly as broken. It now runs in-process so it can both reattach
        and restore.
        """
        captured = {}

        def fake_probe(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
            return MagicMock(returncode=1, stdout="")

        def fake_runner(ssh_args, cleanup_cmd, **kwargs):
            captured["ssh_args"] = ssh_args
            captured["cleanup_cmd"] = cleanup_cmd
            captured["kwargs"] = kwargs
            return 0

        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch(
                "ai_cli.config.load_config",
                return_value={"remote": {"host": "h.example.com", "user": "dev", "transport": "ssh"}},
            ),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
            patch("ai_cli.transport.run_ssh_with_reconnect", side_effect=fake_runner) as runner,
            patch("os.execvp", side_effect=AssertionError("this path must no longer exec")),
        ):
            with pytest.raises(SystemExit):
                cli()

        assert runner.call_count == 1, "the pure-SSH path did not use the reconnecting runner"
        assert captured["ssh_args"][0] == "ssh"
        assert captured["cleanup_cmd"][:2] == ["ai", "internal"]

    def test_given_output_is_not_a_terminal_when_the_loop_exits_then_no_escape_bytes_are_written(self, tmp_path):
        """Negative control, and a correctness requirement rather than tidiness.

        Writing control sequences into a pipe or a log corrupts it. Without this the
        restore would 'pass' by emitting unconditionally, so this is what makes the
        isatty check falsifiable.
        """
        emitted = self._drive_loop(tmp_path, isatty=False)

        assert "\x1b[" not in emitted, f"escape sequences leaked into a non-tty: {emitted!r}"


class TestADroppedLinkReattaches:
    """A transport drop is not the end of the session, because the session is not local.

    The remote side runs under ``tmux``, so a dropped link leaves it detached and intact --
    reattaching costs nothing and loses nothing. The pure-SSH path used to end outright
    instead, which is what turned a network blip into a lost session. Deliberately
    indifferent to WHY the link dropped: an intermediary's idle timeout, its absolute
    session cap and a real network failure are indistinguishable from this side, and
    reattaching covers all three where tuning a keepalive covers only the first.
    """

    def _run(self, exit_codes, **kwargs):
        calls = []
        cleanup = []

        def fake_call(argv):
            calls.append(list(argv))
            return exit_codes[len(calls) - 1] if len(calls) <= len(exit_codes) else exit_codes[-1]

        with (
            patch("ai_cli.transport.subprocess.call", side_effect=fake_call),
            patch("ai_cli.transport.subprocess.run", side_effect=lambda *a, **k: cleanup.append(a[0])),
            patch("ai_cli.transport.time.sleep"),
            patch("ai_cli.transport.restore_terminal"),
        ):
            rc = _transport.run_ssh_with_reconnect(["ssh", "-t", "h", "cmd"], CLEANUP_CMD, **kwargs)
        return rc, calls, cleanup

    def test_given_ssh_exits_zero_when_the_session_ends_then_it_does_not_reconnect(self):
        """A clean exit is the operator detaching or the session finishing. Reconnecting
        there would fight the user, reopening something they deliberately closed."""
        rc, calls, _ = self._run([0])

        assert rc == 0
        assert len(calls) == 1

    def test_given_the_operator_interrupts_when_ssh_reports_sigint_then_it_does_not_reconnect(self):
        """130 is SIGINT. Same reasoning as a clean exit, and a separate case because the
        code distinguishes them by an explicit set rather than by 'non-zero means retry'."""
        rc, calls, _ = self._run([130])

        assert rc == 130
        assert len(calls) == 1

    def test_given_the_connection_drops_when_ssh_reports_255_then_it_reattaches(self):
        """255 is ssh's own 'connection failed or was lost'. This is the reported case, and
        the reattach is the durability the previous single-shot exec could not provide."""
        rc, calls, _ = self._run([255, 255, 0])

        assert rc == 0
        assert len(calls) == 3, "expected two reattaches after two drops"
        assert all(call[0] == "ssh" for call in calls)

    def test_given_a_host_that_stays_gone_when_reconnecting_then_attempts_are_bounded(self):
        """A genuinely dead host must produce a handful of attempts, not an infinite loop.

        The bound is what makes this safe to run unattended; without it a decommissioned
        host would spin forever reconnecting to nothing.
        """
        rc, calls, _ = self._run([255] * 20, max_attempts=4)

        assert rc == 255
        assert len(calls) == 4

    def test_given_any_exit_path_when_the_runner_returns_then_cleanup_still_runs(self):
        """Cleanup is in a ``finally``, so it survives the give-up path too -- the session
        files would otherwise be orphaned exactly when the connection failed."""
        _, _, cleanup_after_drop = self._run([255] * 5, max_attempts=2)
        _, _, cleanup_after_clean = self._run([0])

        assert cleanup_after_drop == [CLEANUP_CMD]
        assert cleanup_after_clean == [CLEANUP_CMD]

    def test_given_a_drop_when_it_reattaches_then_the_terminal_is_restored_before_each_retry(self):
        """Not only at the end. The notice printed between attempts is unreadable if the
        drop left the terminal in the remote application's input modes, so the restore has
        to happen before each message as well as on the way out.
        """
        with (
            patch("ai_cli.transport.subprocess.call", side_effect=[255, 255, 0]),
            patch("ai_cli.transport.subprocess.run"),
            patch("ai_cli.transport.time.sleep"),
            patch("ai_cli.transport.restore_terminal") as restore,
        ):
            _transport.run_ssh_with_reconnect(["ssh", "-t", "h", "cmd"], CLEANUP_CMD)

        # Two retries, each restoring first, plus the unconditional one in the finally.
        assert restore.call_count == 3
