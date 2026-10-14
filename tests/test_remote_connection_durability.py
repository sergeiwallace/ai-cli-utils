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
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import tmux_runnable  # noqa: F401  (imported for parity with sibling modules)

from ai_cli import transport as _transport
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, _run_transport_loop, cli


@pytest.fixture(autouse=True)
def supported_platform_for_remote_unit_tests():
    """Exercise remote command construction independently of the host OS.

    Mirrors ``tests/test_remote.py``'s fixture of the same name, and it is required
    rather than tidy: remote SSH transport is deliberately REFUSED on native Windows,
    exiting 1 at ``main.py``'s platform check before the launch reaches the transport
    at all. Without this pin, every test here that drives a real ``ai c --remote``
    asserts against a launch that never happened -- measured on test-windows, where
    exactly that failed while Linux and macOS passed.

    ``shutil.which`` is pinned alongside it because its own stdlib win32 branch needs
    ``_winapi``, so patching the platform alone crashes it on a non-Windows host
    before the code under test is reached.
    """
    with (
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
    ):
        yield


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

        def fake_call(argv, **_kwargs):
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


class TestARemoteLaunchRefusalIsNotADroppedLink:
    """The remote `ai` refusing to launch exits with its own code, and the ladder stops on it.

    Measured (AI-CLI-a8h5): the remote side refused every attempt with the same configuration
    error, ssh relayed its exit 1, and the ladder printed "Connection lost" and retried ten
    times. A refusal is deterministic, so retrying only repeats it.
    """

    def _remote_launch(self, ssh_exit_codes):
        """Drive a real `ai c firstmate-1 -R` dispatch with ssh stubbed at the subprocess boundary."""
        from conftest import make_subprocess_result, run_cli

        calls = []

        def fake_call(argv, **_kwargs):
            calls.append(list(argv))
            return ssh_exit_codes[min(len(calls), len(ssh_exit_codes)) - 1]

        def fake_run(command, **_kwargs):
            if command[0] != "ssh":
                return make_subprocess_result()
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return make_subprocess_result(stdout="/usr/bin/bash\n")
            remote = command[-1]
            if "allocate-session-name" in remote:
                return make_subprocess_result(
                    stdout=json.dumps({"session_id": "c-r-aih-fw-firstmate-1", "ai_name": "aih-fw-firstmate-1"})
                )
            return make_subprocess_result(stdout="current")

        config = {
            "remote": {
                "default": "fw",
                "machines": {
                    "fw": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}
                },
            }
        }
        with (
            # One patch serves the preflight and the cleanup command alike: both modules
            # share the one ``subprocess`` module, so a second patch would replace this one.
            patch("ai_cli.main.subprocess.run", side_effect=fake_run),
            patch("ai_cli.transport.subprocess.call", side_effect=fake_call),
            patch("ai_cli.transport.time.sleep"),
            patch("ai_cli.transport.restore_terminal"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.config.get_project_aliases", return_value={"aih": "ai-harness"}),
            patch("ai_cli.config.resolve_project_prefix_by_name", return_value="aih"),
        ):
            code, _out, err = run_cli(["ai", "c", "firstmate-1", "-R"], config=config)
        return code, calls, err

    def test_given_the_remote_launcher_refuses_when_dispatched_then_ssh_runs_once_and_the_refusal_is_final(self):
        code, calls, err = self._remote_launch([_transport.REMOTE_LAUNCH_REFUSED_EXIT])

        assert len(calls) == 1
        assert code == _transport.REMOTE_LAUNCH_REFUSED_EXIT
        assert "reattaching in" not in err
        assert "refused to launch" in err

    def test_given_a_refusal_after_a_drop_when_reattaching_then_the_ladder_stops_there(self):
        code, calls, err = self._remote_launch([255, _transport.REMOTE_LAUNCH_REFUSED_EXIT, 0])

        assert len(calls) == 2
        assert code == _transport.REMOTE_LAUNCH_REFUSED_EXIT
        assert err.count("reattaching in") == 1


class TestALongLivedSessionKeepsItsReconnectBudget:
    """The budget bounds ONE outage, not the life of the session.

    Measured on acn-mac (AI-CLI-qre8): ``c-r-kg-1`` and ``c-r-aih-2`` on the remote host
    reattached nine times over three days, each reattach succeeding and then holding for
    hours, and the tenth drop (a 2.5-minute network blip that every other session on the
    same host rode out) ended both with "10 reconnect attempts did not restore it -- giving
    up". The remote tmux sessions and their agents were still running. The counter was never
    reset after a reattach that worked, so it counted drops since launch rather than failed
    attempts in a row.

    Every test drives a real ``ai c --remote`` launch through the real reconnect loop; only
    the ssh child and the clock are fakes.
    """

    def _launch(self, runs, config_extra=None):
        """Drive ``ai c 1 --remote`` where each ssh run is ``(exit_code, seconds_connected)``.

        Every ``runs`` list ends in a clean exit, so a broken bound fails the count rather
        than hanging the test.
        """
        clock = {"now": 0.0}
        calls = []

        def fake_probe(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
            return MagicMock(returncode=1, stdout="")

        def fake_ssh(argv, **_kwargs):
            exit_code, seconds = runs[min(len(calls), len(runs) - 1)]
            calls.append(list(argv))
            clock["now"] += seconds
            return exit_code

        remote = {"host": "h.example.com", "user": "dev", "transport": "ssh", **(config_extra or {})}
        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch("ai_cli.config.load_config", return_value={"remote": remote}),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
            patch("ai_cli.transport.subprocess.call", side_effect=fake_ssh),
            patch("ai_cli.transport._monotonic", side_effect=lambda: clock["now"]),
            patch("ai_cli.transport.time.sleep"),
            patch("ai_cli.transport.restore_terminal"),
            patch("os.execvp", side_effect=AssertionError("this path must not exec")),
        ):
            with pytest.raises(SystemExit) as exited:
                cli()
        return exited.value.code, calls

    @staticmethod
    def _outcome():
        outcomes = list((_transport.get_xdg_state_home() / "transport-outcomes").glob("*.json"))
        assert len(outcomes) == 1, f"expected one durable outcome record, found {outcomes}"
        return json.loads(outcomes[0].read_text())

    def test_given_a_session_that_reattached_nine_times_over_days_when_the_tenth_drop_comes_then_it_reattaches(
        self,
    ):
        """The reproduction. Twelve drops, each after hours of a working connection, then a
        clean detach. Before the fix the tenth drop gave up without trying, exiting 255."""
        rc, calls = self._launch([(255, 4 * 3600.0)] * 12 + [(0, 60.0)])

        assert rc == 0
        assert len(calls) == 13, "every drop after a stable connection must get its own reattach"

    def test_given_reattaches_that_keep_failing_after_a_stable_session_when_the_budget_runs_out_then_it_gives_up(
        self,
    ):
        """Negative control: the bound still holds for one outage. After a stable run, every
        reattach fails in seconds, so the ladder spends its ten attempts and stops."""
        rc, calls = self._launch([(255, 4 * 3600.0)] + [(255, 2.0)] * 20 + [(0, 60.0)])

        assert rc == 255
        assert len(calls) == 10

    def test_given_a_configured_stability_window_when_a_run_is_shorter_then_it_does_not_reset_the_budget(self):
        """Config over code: a reattach whose ProxyCommand waits minutes before failing must
        not read as a stable connection, so the window is reachable from config."""
        rc, calls = self._launch([(255, 600.0)] * 20 + [(0, 60.0)], config_extra={"reconnect_stable_seconds": 900})

        assert rc == 255
        assert len(calls) == 10

    def test_given_the_ladder_gives_up_when_the_launcher_has_exited_then_the_outcome_is_on_disk(self):
        """The terminal outcome survives the process: read back after ``cli()`` has exited."""
        self._launch([(255, 4 * 3600.0)] + [(255, 2.0)] * 20 + [(0, 60.0)])

        outcome = self._outcome()
        assert outcome["verdict"] == "gave_up"
        assert outcome["last_exit_code"] == 255
        assert outcome["failed_attempts"] == 10
        assert outcome["drops"] == 10
        assert outcome["session"].startswith("c-r-")
        assert outcome["ended_at"]

    def test_given_a_clean_detach_when_the_launcher_has_exited_then_the_outcome_says_so(self):
        """A record exists for every way out, so its absence never has to be interpreted."""
        self._launch([(255, 4 * 3600.0), (0, 60.0)])

        outcome = self._outcome()
        assert outcome["verdict"] == "clean_exit"
        assert outcome["last_exit_code"] == 0
        assert outcome["drops"] == 1


class TestACredentialOutageDoesNotSpendTheReconnectBudget:
    """An expired credential is not a dropped link, and waiting it out must not cost attempts.

    Measured: a remote host reached through a ``ProxyCommand`` that needs a short-lived SSO
    token. Once the token expired, every reattach failed in seconds with ssh exit 255, the
    same status as a dropped link, so ten attempts were spent in under four minutes and the
    session gave up while the remote tmux session was still running. The token lives an
    hour and refreshes only when a human logs in again, so no fixed attempt count can
    outlast it. The only place the two causes differ is the text ssh and its
    ``ProxyCommand`` write to stderr, so that is what the runner now reads.

    Every test drives a real ``ai c --remote`` launch through the real reconnect loop; the
    ssh child, its stderr and the clock are the only fakes.
    """

    AUTH_REASON = "[proxy] SSO token is expired -- Token has expired and refresh failed\n"
    LINK_REASON = "Connection closed by 192.0.2.10 port 22\n"

    def _launch(self, fault_until, reason, config_extra=None, first_runs=()):
        """Drive ``ai c 1 --remote`` where every ssh run fails with *reason* until the clock
        passes *fault_until* seconds, and then exits cleanly.

        ``time.sleep`` advances the same clock, so the simulated outage is measured in the
        runner's own backoff rather than in a count of calls chosen by the test.
        ``first_runs`` are ``(exit_code, stderr, seconds_connected)`` runs played first.
        """
        clock = {"now": 0.0}
        calls = []

        def fake_probe(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
            return MagicMock(returncode=1, stdout="")

        def fake_ssh(argv, **kwargs):
            calls.append(list(argv))
            if len(calls) <= len(first_runs):
                exit_code, text, seconds = first_runs[len(calls) - 1]
                clock["now"] += seconds
                kwargs["stderr"].write(text.encode())
                return exit_code
            clock["now"] += 2.0
            if clock["now"] < fault_until:
                stderr = kwargs.get("stderr")
                if stderr is not None:
                    stderr.write(reason.encode())
                    stderr.flush()
                return 255
            return 0

        def fake_sleep(seconds):
            clock["now"] += seconds

        remote = {"host": "h.example.com", "user": "dev", "transport": "ssh", **(config_extra or {})}
        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch("ai_cli.config.load_config", return_value={"remote": remote}),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
            patch("ai_cli.transport.subprocess.call", side_effect=fake_ssh),
            patch("ai_cli.transport._monotonic", side_effect=lambda: clock["now"]),
            patch("ai_cli.transport.time.sleep", side_effect=fake_sleep),
            patch("ai_cli.transport.restore_terminal"),
            patch("os.execvp", side_effect=AssertionError("this path must not exec")),
        ):
            with pytest.raises(SystemExit) as exited:
                cli()
        return exited.value.code, calls, clock["now"]

    @staticmethod
    def _outcome():
        outcomes = list((_transport.get_xdg_state_home() / "transport-outcomes").glob("*.json"))
        assert len(outcomes) == 1, f"expected one durable outcome record, found {outcomes}"
        return json.loads(outcomes[0].read_text())

    def test_given_an_expired_credential_for_longer_than_the_ladder_when_it_returns_then_the_session_reattaches(
        self,
    ):
        """The reproduction. A 65-minute credential outage, longer than the token's own hour
        and far longer than the ~4 minutes ten backed-off attempts last, then credentials
        return. Before the fix the ladder spent all ten attempts and exited 255."""
        rc, calls, elapsed = self._launch(fault_until=65 * 60, reason=self.AUTH_REASON)

        assert rc == 0, "the session gave up during a credential outage instead of waiting it out"
        assert elapsed >= 65 * 60
        outcome = self._outcome()
        assert outcome["verdict"] == "clean_exit"
        assert outcome["failed_attempts"] == 0, "auth faults consumed the transport attempt budget"
        assert outcome["auth_faults"] == len(calls) - 1

    def test_given_credentials_that_never_return_when_the_auth_wait_expires_then_it_gives_up_naming_the_credential(
        self,
    ):
        """The wait is bounded too, and the record left behind joins ssh's exit 255 to the
        ProxyCommand's reason, so one artifact names the credential."""
        rc, _, elapsed = self._launch(
            fault_until=float("inf"), reason=self.AUTH_REASON, config_extra={"reconnect_auth_wait_seconds": 1800}
        )

        assert rc == 255
        assert 1800 <= elapsed < 1800 + 15 * 60
        outcome = self._outcome()
        assert outcome["verdict"] == "gave_up"
        assert outcome["cause"] == "auth"
        assert outcome["last_exit_code"] == 255
        assert outcome["failed_attempts"] == 0
        assert "Token has expired" in outcome["last_error"]

    def test_given_a_link_loss_with_no_credential_reason_when_it_persists_then_the_budget_still_bounds_it(self):
        """Negative control: a fault whose stderr names no credential is a transport fault,
        so the ten-attempt bound still holds and the record says so."""
        rc, calls, _ = self._launch(fault_until=float("inf"), reason=self.LINK_REASON)

        assert rc == 255
        assert len(calls) == 10
        outcome = self._outcome()
        assert outcome["cause"] == "transport"
        assert outcome["failed_attempts"] == 10
        assert outcome["auth_faults"] == 0
        assert "Connection closed by 192.0.2.10" in outcome["last_error"]

    def test_given_an_auth_fault_when_the_runner_waits_then_the_operator_sees_the_reason_and_a_re_auth_prompt(
        self, capsys
    ):
        """ssh's stderr is still relayed live, and the wait is announced as a credential
        problem with the action that ends it rather than as another dropped link."""
        self._launch(fault_until=120, reason=self.AUTH_REASON)

        err = capsys.readouterr().err
        assert "Token has expired" in err, "ssh's stderr was swallowed instead of relayed"
        assert "re-authenticate" in err.lower()
        assert "attempt 1/10" not in err

    def test_given_a_stable_session_between_two_credential_outages_when_the_second_starts_then_it_gets_a_full_wait(
        self,
    ):
        """The wait bounds one outage, like the attempt budget. A reattach that then held for
        hours was a working connection, so the next credential outage must not inherit the
        time already spent waiting on the first one and give up on arrival."""
        long_run_ends = 3 * 2.0 + (30 + 60 + 120) + 4 * 3600.0
        rc, _, _ = self._launch(
            fault_until=long_run_ends + 25 * 60,
            reason=self.AUTH_REASON,
            config_extra={"reconnect_auth_wait_seconds": 1800},
            first_runs=[(255, self.AUTH_REASON, 2.0)] * 3 + [(255, self.AUTH_REASON, 4 * 3600.0)],
        )

        assert rc == 0, "the second outage gave up on time spent waiting out the first"

    def test_given_custom_auth_patterns_when_the_reason_matches_one_then_it_is_treated_as_an_auth_fault(self):
        """Config over code: a credential helper with its own wording is reachable from
        config, not only the built-in list."""
        rc, _, _ = self._launch(
            fault_until=30 * 60,
            reason="vault: lease revoked\n",
            config_extra={"reconnect_auth_patterns": ["lease revoked"]},
        )

        assert rc == 0
        assert self._outcome()["failed_attempts"] == 0


class TestALongLivedLauncherSaysWhatItIsRunning:
    """A launcher that gave up must leave enough behind to say why, from artifacts alone.

    Measured: a remote session's local launcher gave up on its tenth drop in five days. Its
    process had started before the fix that resets the budget after a stable connection, so
    it ran the old ladder all along. Nothing it wrote said which code it was running, how
    long each connection had held, or how close it was to giving up, and the launchers still
    running on the same old code could only be found by reading each one's launch log.

    Every test drives a real ``ai c --remote`` launch through the real reconnect loop; the
    ssh child and the clock are the only fakes.
    """

    LOADED = "1111111111111111111111111111111111111111"
    INSTALLED = "2222222222222222222222222222222222222222"

    @staticmethod
    def _stamp(commit):
        path = _transport.get_xdg_state_home() / "last_update_commit.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit + "\n")

    @staticmethod
    def _record():
        outcomes = list((_transport.get_xdg_state_home() / "transport-outcomes").glob("*.json"))
        assert len(outcomes) == 1, f"expected one outcome record, found {outcomes}"
        return json.loads(outcomes[0].read_text())

    def _launch(self, runs, on_call=None):
        """``runs`` are ``(exit_code, seconds_connected)``; ``on_call(n)`` runs before ssh call n."""
        clock = {"now": 0.0}
        calls = []

        def fake_probe(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
            return MagicMock(returncode=1, stdout="")

        def fake_ssh(argv, **_kwargs):
            calls.append(list(argv))
            if on_call is not None:
                on_call(len(calls))
            exit_code, seconds = runs[min(len(calls), len(runs)) - 1]
            clock["now"] += seconds
            return exit_code

        remote = {"host": "h.example.com", "user": "dev", "transport": "ssh"}
        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch("ai_cli.config.load_config", return_value={"remote": remote}),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
            patch("ai_cli.transport.subprocess.call", side_effect=fake_ssh),
            patch("ai_cli.transport._monotonic", side_effect=lambda: clock["now"]),
            patch("ai_cli.transport.time.sleep"),
            patch("ai_cli.transport.restore_terminal"),
            patch("os.execvp", side_effect=AssertionError("this path must not exec")),
        ):
            with pytest.raises(SystemExit) as exited:
                cli()
        return exited.value.code, calls

    def test_given_a_drop_after_hours_connected_when_it_is_reported_then_the_notice_says_how_long_it_held(self, capsys):
        """Whether attempt N was a fresh drop after a working connection or one more failure
        in a flapping outage was only inferable from gaps between log timestamps."""
        self._launch([(255, 4 * 3600.0 + 125), (255, 2.0), (0, 60.0)])

        err = capsys.readouterr().err
        assert "after 4h02m connected" in err
        assert "after 2s connected" in err

    def test_given_an_update_installed_after_launch_when_the_link_drops_then_the_notice_says_the_code_is_stale(
        self, capsys
    ):
        """A launcher keeps the code it started with. When the installed code has moved on,
        each drop says so once and names the remedy, which costs nothing because the remote
        session survives a reattach."""
        self._stamp(self.LOADED)

        def install_update(n):
            if n == 2:
                self._stamp(self.INSTALLED)

        self._launch([(255, 3600.0)] * 4 + [(0, 60.0)], on_call=install_update)

        err = capsys.readouterr().err
        assert err.count("older than the installed") == 1, "the stale-code notice must appear once per update"
        assert self.LOADED[:12] in err and self.INSTALLED[:12] in err
        assert "reattach with the same command" in err.lower()

    def test_given_no_update_since_launch_when_the_link_drops_then_no_stale_code_notice_appears(self, capsys):
        """Negative control: an unchanged stamp is current code, and says nothing."""
        self._stamp(self.LOADED)

        self._launch([(255, 3600.0)] * 3 + [(0, 60.0)])

        assert "older than the installed" not in capsys.readouterr().err

    def test_given_a_launcher_that_has_exited_when_its_outcome_is_read_then_it_names_the_process_and_code(self):
        """The record ties an ending to the process and the code that produced it."""
        self._stamp(self.LOADED)

        self._launch([(255, 3600.0), (0, 60.0)])

        record = self._record()
        assert record["code_commit"] == self.LOADED
        assert record["pid"] > 0
        assert record["launched_at"]
        assert "code_version" in record

    def test_given_a_live_ladder_when_it_has_dropped_then_its_budget_is_on_disk_before_it_ends(self):
        """A record written only on the way out says nothing about the launchers still
        running, which is exactly where the next give-up comes from."""
        seen = {}

        def read_mid_run(n):
            if n == 4:
                seen.update(self._record())

        self._launch([(255, 2.0)] * 3 + [(0, 60.0)], on_call=read_mid_run)

        assert seen["verdict"] == "reconnecting"
        assert seen["failed_attempts"] == 3
        assert seen["drops"] == 3
        assert self._record()["verdict"] == "clean_exit"
