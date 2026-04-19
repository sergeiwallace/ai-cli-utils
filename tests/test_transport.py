"""Tests for VPN-aware transport loop and Circus watcher lifecycle."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from ai_cli.main import (
    _ensure_tailscale_up,
    _ensure_vpn_watcher,
    _maybe_stop_vpn_watcher,
    _run_transport_loop,
    _write_transport_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SSH_ARGS = ["ssh", "-t", "user@host", "bash -l -c 'ai c'"]
MOSH_ARGS = ["mosh", "user@host", "--", "bash", "-l", "-c", "ai c"]
CLEANUP_CMD = ["ai", "internal", "cleanup-session-files", "c-r-sw-1"]
SESSION = "c-r-sw-1"
CONFIG = {"messaging": {"nats_servers": ["nats://localhost:4222"]}}


def _make_proc(returncode=0, poll_sequence=None):
    """Create a mock Popen that behaves like a real process."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    if poll_sequence is not None:
        proc.poll = MagicMock(side_effect=poll_sequence)
    else:
        # Returns None once (running), then returncode (exited)
        proc.poll = MagicMock(side_effect=[None, returncode])
    proc.wait = MagicMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _write_transport_state
# ---------------------------------------------------------------------------


class TestWriteTransportState:
    def test_when_called_then_writes_json_file(self, tmp_path):
        f = tmp_path / "transport-c-r-sw-1.json"
        _write_transport_state(f, SESSION, 999, 12345, "mosh")
        data = json.loads(f.read_text())
        assert data["parent_pid"] == 999
        assert data["child_pid"] == 12345
        assert data["transport"] == "mosh"
        assert data["session"] == SESSION
        assert "started_at" in data

    def test_when_called_with_ssh_then_records_ssh(self, tmp_path):
        f = tmp_path / "transport-c-r-sw-1.json"
        _write_transport_state(f, SESSION, 1, 2, "ssh")
        assert json.loads(f.read_text())["transport"] == "ssh"


# ---------------------------------------------------------------------------
# _ensure_vpn_watcher
# ---------------------------------------------------------------------------


class TestEnsureVpnWatcher:
    def test_when_no_sessions_then_starts_circus_watcher(self, tmp_path):
        mock_client = MagicMock()
        mock_client.send_message = MagicMock(return_value={"statuses": {}})
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", return_value="ipc:///tmp/circus.endpoint"),
            patch("ai_cli.main.shutil.which", return_value="/usr/local/bin/ai"),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _ensure_vpn_watcher(CONFIG)

        add_calls = [c for c in mock_client.send_message.call_args_list if c[0][0] == "add"]
        assert len(add_calls) == 1
        assert add_calls[0][1]["name"] == "vpn-watch"

    def test_when_transport_files_exist_then_skips_circus(self, tmp_path):
        # Another session already running — watcher already started
        existing = tmp_path / "transport-c-r-sw-2.json"
        existing.write_text('{"parent_pid": 999}')
        mock_client = MagicMock()
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", return_value="ipc:///tmp/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _ensure_vpn_watcher(CONFIG)

        mock_client.send_message.assert_not_called()

    def test_when_watcher_already_in_circus_then_skips_add(self, tmp_path):
        mock_client = MagicMock()
        mock_client.send_message = MagicMock(return_value={"statuses": {"vpn-watch": "active"}})
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", return_value="ipc:///tmp/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _ensure_vpn_watcher(CONFIG)

        add_calls = [c for c in mock_client.send_message.call_args_list if c[0][0] == "add"]
        assert len(add_calls) == 0

    def test_when_circus_unavailable_then_does_not_raise(self, tmp_path):
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", side_effect=RuntimeError("circus down")),
        ):
            _ensure_vpn_watcher(CONFIG)  # Should not raise


# ---------------------------------------------------------------------------
# _maybe_stop_vpn_watcher
# ---------------------------------------------------------------------------


class TestMaybeStopVpnWatcher:
    def test_when_no_sessions_remain_then_stops_watcher(self, tmp_path):
        mock_client = MagicMock()
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _maybe_stop_vpn_watcher()

        mock_client.send_message.assert_called_once_with("rm", name="vpn-watch")

    def test_when_sessions_remain_then_does_not_stop(self, tmp_path):
        existing = tmp_path / "transport-c-r-sw-2.json"
        existing.write_text('{"parent_pid": 999}')
        mock_client = MagicMock()
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _maybe_stop_vpn_watcher()

        mock_client.send_message.assert_not_called()

    def test_when_circus_unavailable_then_does_not_raise(self, tmp_path):
        with (
            patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=Exception("circus down")),
        ):
            _maybe_stop_vpn_watcher()  # Should not raise


# ---------------------------------------------------------------------------
# _run_transport_loop
# ---------------------------------------------------------------------------


def _mock_nats_client(subscribe_cb_store=None):
    nc = MagicMock()
    nc.nc = MagicMock()
    nc.connect = AsyncMock()
    nc.close = AsyncMock()

    async def fake_subscribe(subject, cb=None):
        if subscribe_cb_store is not None and cb is not None:
            subscribe_cb_store["cb"] = cb

    nc.nc.subscribe = AsyncMock(side_effect=fake_subscribe)
    return nc


class TestRunTransportLoop:
    def test_when_no_vpn_then_starts_mosh(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc) as mock_popen,
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
            return mock_popen.call_args[0][0]

        used_args = asyncio.run(run())
        assert used_args == MOSH_ARGS

    def test_when_vpn_active_then_starts_ssh(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=True),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc) as mock_popen,
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
            return mock_popen.call_args[0][0]

        used_args = asyncio.run(run())
        assert used_args == SSH_ARGS

    def test_when_nats_message_received_then_terminates_proc_and_loops(self, tmp_path):
        """NATS vpn.state.changed → proc.terminate() → reconnect with new transport."""
        subscribe_store = {}
        nc = _mock_nats_client(subscribe_cb_store=subscribe_store)

        # First iteration: mosh (no VPN), terminated by NATS message
        # Second iteration: SSH (VPN active), exits cleanly
        vpn_states = iter([False, True])

        proc1 = MagicMock()
        proc1.pid = 1111
        proc1.returncode = None
        proc1.terminate = MagicMock()
        proc1.wait = MagicMock(return_value=1)
        # Return None twice so vpn_changed.is_set() is checked after the event fires
        proc1.poll = MagicMock(side_effect=[None, None, 1])

        proc2 = _make_proc(returncode=0, poll_sequence=[None, 0])
        procs = iter([proc1, proc2])

        sleep_count = {"n": 0}

        async def fake_sleep(n):
            sleep_count["n"] += 1
            # On first sleep, fire the NATS callback to signal VPN change
            if sleep_count["n"] == 1 and "cb" in subscribe_store:
                await subscribe_store["cb"](MagicMock())

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", side_effect=vpn_states),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=lambda args: next(procs)),
                patch("asyncio.sleep", side_effect=fake_sleep),
                # iter 1: start=0.0, end=0.0 (vpn_changed→continue); iter 2: start=0.0, end=10.0
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 0.0, 0.0, 10.0]),
                patch("subprocess.run"),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        proc1.terminate.assert_called_once()

    def test_when_transport_exits_normally_then_breaks_loop(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())  # Should return cleanly without looping

    def test_when_mosh_fails_fast_with_vpn_then_switches_to_ssh(self, tmp_path):
        proc_mosh = _make_proc(returncode=1, poll_sequence=[None, 1])
        proc_ssh = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        procs = iter([proc_mosh, proc_ssh])
        vpn_states = iter([False, True, True])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", side_effect=vpn_states),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=lambda args: next(procs)) as mock_popen,
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 5.0, 0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch("ai_cli.main.time.sleep"),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
                return mock_popen.call_args_list

        call_list = asyncio.run(run())
        # First call: mosh, second call: ssh
        assert call_list[0][0][0] == MOSH_ARGS
        assert call_list[1][0][0] == SSH_ARGS

    def test_when_mosh_exits_too_quickly_without_vpn_then_falls_back_to_ssh(self, tmp_path, capsys):
        # First proc: mosh fails fast. Second proc: SSH succeeds.
        mosh_proc = _make_proc(returncode=1, poll_sequence=[None, 1])
        ssh_proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        popen_calls = iter([mosh_proc, ssh_proc])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=popen_calls),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0, 0.0, 5.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        err = capsys.readouterr().err
        assert "falling back to SSH" in err

    def test_when_mosh_and_ssh_both_fail_without_vpn_then_gives_up(self, tmp_path, capsys):
        # Mosh fails fast, SSH also fails fast → SSH retry backoff → give up.
        mosh_proc = _make_proc(returncode=1, poll_sequence=[None, 1])
        ssh_proc = _make_proc(returncode=1, poll_sequence=[None, 1])
        nc = _mock_nats_client()
        popen_calls = iter([mosh_proc, ssh_proc])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=popen_calls),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0, 0.0, 1.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch("time.sleep"),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        assert "giving up" in capsys.readouterr().err

    def test_when_mosh_fails_and_tailscale_recovers_then_mosh_retried(self, tmp_path, capsys):
        # Mosh fails fast, Tailscale comes up, mosh retried and succeeds.
        mosh_fail = _make_proc(returncode=1, poll_sequence=[None, 1])
        mosh_ok = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        popen_calls = iter([mosh_fail, mosh_ok])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=popen_calls),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0, 0.0, 5.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch("ai_cli.transport._ensure_tailscale_up", new_callable=AsyncMock, return_value=True),
            ):
                await _run_transport_loop(
                    SSH_ARGS,
                    MOSH_ARGS,
                    CLEANUP_CMD,
                    SESSION,
                    CONFIG,
                    tailscale_host="100.64.0.1",
                )

        asyncio.run(run())
        err = capsys.readouterr().err
        assert "Tailscale up" in err
        assert "retrying mosh" in err
        assert "falling back to SSH" not in err

    def test_when_mosh_fails_and_tailscale_cannot_start_then_ssh_fallback(self, tmp_path, capsys):
        # Mosh fails fast, Tailscale fails to come up → SSH fallback.
        mosh_proc = _make_proc(returncode=1, poll_sequence=[None, 1])
        ssh_proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        popen_calls = iter([mosh_proc, ssh_proc])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=popen_calls),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0, 0.0, 5.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch("ai_cli.transport._ensure_tailscale_up", new_callable=AsyncMock, return_value=False),
            ):
                await _run_transport_loop(
                    SSH_ARGS,
                    MOSH_ARGS,
                    CLEANUP_CMD,
                    SESSION,
                    CONFIG,
                    tailscale_host="100.64.0.1",
                )

        asyncio.run(run())
        err = capsys.readouterr().err
        assert "falling back to SSH" in err

    def test_when_mosh_running_and_vpn_detected_by_poll_then_switches_to_ssh(self, tmp_path):
        """Direct VPN poll fallback: mosh hangs (UDP blocked), NATS unavailable.

        Mosh never exits. The inner loop polls VPN every vpn_poll_interval seconds.
        On the first poll tick (interval=3s / 0.5s = tick 6) VPN is detected and mosh
        is terminated, causing the outer loop to restart with SSH.
        """
        nc = _mock_nats_client()
        nc.nc = None  # NATS unavailable — no vpn.state.changed signal

        # mosh: never exits on its own — simulates UDP-blocked mosh
        proc_mosh = MagicMock()
        proc_mosh.pid = 1111
        proc_mosh.returncode = None
        proc_mosh.terminate = MagicMock()
        proc_mosh.wait = MagicMock(return_value=0)
        # 7 None polls before terminate() kicks in, then stops
        proc_mosh.poll = MagicMock(side_effect=[None] * 7)

        proc_ssh = _make_proc(returncode=0, poll_sequence=[None, 0])
        procs = iter([proc_mosh, proc_ssh])

        # VPN state: False for initial check (→ mosh), True for every poll tick after
        vpn_call_count = {"n": 0}

        def vpn_side_effect():
            vpn_call_count["n"] += 1
            return vpn_call_count["n"] > 1  # First call False, rest True

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", side_effect=vpn_side_effect),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=lambda args: next(procs)) as mock_popen,
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 0.0, 0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
            return mock_popen.call_args_list

        call_list = asyncio.run(run())
        proc_mosh.terminate.assert_called_once()
        assert call_list[0][0][0] == MOSH_ARGS
        assert call_list[1][0][0] == SSH_ARGS

    def test_when_transport_starts_then_writes_state_file(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        written_states = []

        real_write = _write_transport_state

        def capture_write(path, session, ppid, cpid, transport):
            written_states.append({"transport": transport, "path": path})
            real_write(path, session, ppid, cpid, transport)

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._write_transport_state", side_effect=capture_write),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        assert len(written_states) >= 1
        assert written_states[0]["transport"] == "mosh"

    def test_when_loop_exits_then_state_file_is_deleted(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        transport_file = tmp_path / f"transport-{SESSION}.json"
        assert not transport_file.exists()

    def test_when_loop_exits_then_cleanup_cmd_is_run(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run") as mock_run,
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
                return mock_run

        mock_run = asyncio.run(run())
        mock_run.assert_called_once_with(CLEANUP_CMD, capture_output=True)

    def test_when_nats_unavailable_then_transport_still_starts(self, tmp_path):
        proc = _make_proc(returncode=0, poll_sequence=[None, 0])
        nc = _mock_nats_client()
        nc.nc = None  # NATS unavailable

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc) as mock_popen,
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 10.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)
            return mock_popen.call_args[0][0]

        used_args = asyncio.run(run())
        assert used_args == MOSH_ARGS

    def test_when_ssh_fails_fast_with_vpn_then_retries_with_backoff(self, tmp_path, capsys):
        """SSH fails quickly while VPN is active → retry with backoff, then give up."""
        # Initial SSH + 3 retries, all failing fast
        procs = iter(
            [
                _make_proc(returncode=1, poll_sequence=[None, 1]),
                _make_proc(returncode=1, poll_sequence=[None, 1]),
                _make_proc(returncode=1, poll_sequence=[None, 1]),
                _make_proc(returncode=1, poll_sequence=[None, 1]),
            ]
        )
        nc = _mock_nats_client()

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", return_value=True),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", side_effect=lambda args: next(procs)),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch("ai_cli.main.time.sleep"),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        assert "giving up" in capsys.readouterr().err

    def test_when_ssh_fails_fast_without_vpn_then_gives_up(self, tmp_path, capsys):
        """SSH exits fast when VPN dropped after the session started → give up."""
        proc = _make_proc(returncode=1, poll_sequence=[None, 1])
        nc = _mock_nats_client()
        # VPN active at start (→ ssh), then gone when SSH exits fast
        vpn_states = iter([True, False])

        async def run():
            with (
                patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
                patch("ai_cli.transport._is_vpn_active", side_effect=vpn_states),
                patch("ai_cli.messaging.NATSClient", return_value=nc),
                patch("subprocess.Popen", return_value=proc),
                patch("ai_cli.transport._monotonic", side_effect=[0.0, 1.0]),
                patch("subprocess.run"),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                await _run_transport_loop(SSH_ARGS, MOSH_ARGS, CLEANUP_CMD, SESSION, CONFIG)

        asyncio.run(run())
        assert "giving up" in capsys.readouterr().err


class TestEnsureTailscaleUp:
    def test_when_host_already_reachable_then_returns_true(self):
        async def run():
            with patch("ai_cli.transport.asyncio.to_thread", new_callable=AsyncMock, return_value=True):
                return await _ensure_tailscale_up("100.64.0.1")

        assert asyncio.run(run()) is True

    def test_when_host_unreachable_and_not_darwin_then_returns_false(self):
        async def run():
            with (
                patch("ai_cli.transport.asyncio.to_thread", new_callable=AsyncMock, return_value=False),
                patch("ai_cli.main.sys.platform", "linux"),
            ):
                return await _ensure_tailscale_up("100.64.0.1", timeout=0)

        assert asyncio.run(run()) is False

    def test_when_host_unreachable_then_opens_tailscale_app_on_darwin(self):
        # Tailscale not running → open -gj called; host reachable on second poll.
        open_calls = []
        # Sequence: _reachable(first), _tailscale_running(False=not running), _reachable(poll→True)
        bool_results = [False, False, True]
        bool_idx = {"i": 0}

        async def run():
            async def mock_to_thread(fn, *args, **kwargs):
                import subprocess as _sp

                if fn is _sp.run:
                    open_calls.append(args[0] if args else [])
                    return None
                idx = bool_idx["i"]
                bool_idx["i"] += 1
                return bool_results[idx]

            with (
                patch("ai_cli.transport.asyncio.to_thread", side_effect=mock_to_thread),
                patch("ai_cli.main.sys.platform", "darwin"),
                patch("ai_cli.transport.asyncio.sleep", new_callable=AsyncMock),
            ):
                return await _ensure_tailscale_up("100.64.0.1", timeout=5)

        assert asyncio.run(run()) is True
        assert any("-gj" in str(c) and "Tailscale" in str(c) for c in open_calls)

    def test_when_tailscale_already_running_then_no_open_call(self):
        # Tailscale running but host unreachable at first → wait without relaunching.
        open_calls = []
        # Sequence: _reachable(False), _tailscale_running(True=running), _reachable(poll→True)
        bool_results = [False, True, True]
        bool_idx = {"i": 0}

        async def run():
            async def mock_to_thread(fn, *args, **kwargs):
                import subprocess as _sp

                if fn is _sp.run:
                    open_calls.append(args[0] if args else [])
                    return None
                idx = bool_idx["i"]
                bool_idx["i"] += 1
                return bool_results[idx]

            with (
                patch("ai_cli.transport.asyncio.to_thread", side_effect=mock_to_thread),
                patch("ai_cli.main.sys.platform", "darwin"),
                patch("ai_cli.transport.asyncio.sleep", new_callable=AsyncMock),
            ):
                return await _ensure_tailscale_up("100.64.0.1", timeout=5)

        result = asyncio.run(run())
        assert result is True
        assert open_calls == [], "open should not be called when Tailscale is already running"

    def test_when_tailscale_does_not_come_up_within_timeout_then_returns_false(self):
        async def run():
            with (
                patch("ai_cli.transport.asyncio.to_thread", new_callable=AsyncMock, return_value=False),
                patch("ai_cli.main.sys.platform", "darwin"),
                patch("ai_cli.transport.asyncio.sleep", new_callable=AsyncMock),
                patch("ai_cli.main.time.monotonic", side_effect=[0.0, 0.0, 100.0]),
            ):
                return await _ensure_tailscale_up("100.64.0.1", timeout=5)

        assert asyncio.run(run()) is False
