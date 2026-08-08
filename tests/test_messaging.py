import asyncio
import socket
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

from ai_cli.messaging import NATSClient


def _unreachable_local_port() -> int:
    """Bind an ephemeral local port then close it so nothing listens there."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestNATSClientConnectRealBoundary:
    """connect() must return in bounded time against a live-but-
    unreachable server. Exercises the REAL nats-py connect() (unmocked) —
    the defect lives inside nats-py's own initial-connect retry loop
    (`_select_next_server`), which every other test in this file mocks
    away via `patch("nats.connect", ...)`. That mocking is correct for
    testing our own retry/backoff logic, but it can never catch this
    defect, since the defect is what nats.connect() itself does when
    left unmocked.
    """

    def test_connect_returns_within_bounded_time_when_server_unreachable(self):
        port = _unreachable_local_port()
        client = NATSClient(servers=[f"nats://127.0.0.1:{port}"])

        async def run():
            start = time.monotonic()
            await asyncio.wait_for(client.connect(), timeout=20)
            return time.monotonic() - start

        try:
            elapsed = asyncio.run(run())
        except TimeoutError:
            raise AssertionError(
                "NATSClient.connect() did not return within 20s against an "
                "unreachable server — this is the unbounded-retry hang: nats-py's "
                "max_reconnect_attempts=0 does not disable its internal "
                "retry loop (it is treated as unlimited), so "
                "_select_next_server() retries forever and connect() never "
                "returns. Orphaned `ai internal publish` processes were "
                "found alive for 3+ days as a result."
            ) from None

        assert client.nc is None
        assert elapsed < 20


class TestNATSClientConnect:
    def test_connect_sets_nc_on_success(self):
        client = NATSClient()
        mock_nc = MagicMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                await client.connect()

        asyncio.run(run())
        assert client.nc is mock_nc

    def test_connect_leaves_nc_none_after_all_retries_fail(self):
        from nats.errors import NoServersError

        client = NATSClient()

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                await client.connect()

        asyncio.run(run())
        assert client.nc is None

    def test_connect_retries_then_succeeds(self):
        from nats.errors import NoServersError

        client = NATSClient()
        mock_nc = MagicMock()
        call_count = 0

        async def fake_connect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise NoServersError
            return mock_nc

        async def run():
            with patch("nats.connect", new=fake_connect), patch("asyncio.sleep", new=AsyncMock()):
                await client.connect()

        asyncio.run(run())
        assert client.nc is mock_nc
        assert call_count == 3


class TestNATSClientPublish:
    def test_publish_heartbeat_returns_true_when_connected(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.publish = AsyncMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                return await client.publish_heartbeat("sess-1", {"status": "WORKING"})

        result = asyncio.run(run())
        assert result is True
        mock_nc.publish.assert_called_once()
        subject, payload = mock_nc.publish.call_args[0]
        assert subject == "fleet.worker.sess-1.heartbeat"
        assert b'"status": "WORKING"' in payload

    def test_publish_heartbeat_returns_false_when_nats_unavailable(self):
        from nats.errors import NoServersError

        client = NATSClient()

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                return await client.publish_heartbeat("sess-1", {"status": "WORKING"})

        result = asyncio.run(run())
        assert result is False

    def test_publish_event_sends_correct_subject_and_payload(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.publish = AsyncMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                await client.publish_event("sess-2", "STARTED", {"key": "val"})

        asyncio.run(run())
        subject, payload = mock_nc.publish.call_args[0]
        assert subject == "fleet.worker.sess-2.event"
        assert b'"type": "STARTED"' in payload
        assert b'"key": "val"' in payload

    def test_publish_event_returns_false_when_nats_unavailable(self):
        from nats.errors import NoServersError

        client = NATSClient()

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                return await client.publish_event("sess-2", "STARTED")

        result = asyncio.run(run())
        assert result is False

    def test_publish_arbitrary_subject(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.publish = AsyncMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                await client.publish("sync.pull.requested", {"machine": "mac"})

        asyncio.run(run())
        subject, payload = mock_nc.publish.call_args[0]
        assert subject == "sync.pull.requested"
        assert b'"machine": "mac"' in payload

    def test_publish_reuses_existing_connection(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.publish = AsyncMock()
        client.nc = mock_nc  # pre-set connection

        async def run():
            with patch("nats.connect", new=AsyncMock()) as mock_connect:
                await client.publish_heartbeat("sess-1", {})
                assert not mock_connect.called  # should not reconnect

        asyncio.run(run())
        mock_nc.publish.assert_called_once()


class TestNATSClientClose:
    def test_close_drains_connection(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.close = AsyncMock()
        client.nc = mock_nc

        asyncio.run(client.close())
        mock_nc.close.assert_called_once()

    def test_close_is_no_op_when_not_connected(self):
        client = NATSClient()
        # Should not raise
        asyncio.run(client.close())


class TestNATSClientSubscribe:
    def test_subscribe_invokes_callback_on_message(self):
        client = NATSClient()
        mock_nc = MagicMock()
        received = []

        async def fake_subscribe(subject, cb):
            # Simulate one incoming message then done
            msg = MagicMock()
            msg.data = b'{"machine": "mac"}'
            await cb(msg)

        mock_nc.subscribe = fake_subscribe

        # Override asyncio.sleep to raise CancelledError immediately after first tick
        sleep_count = 0

        async def fake_sleep(_):
            nonlocal sleep_count
            sleep_count += 1
            raise asyncio.CancelledError

        async def on_message(data):
            received.append(data)

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
                patch("asyncio.sleep", new=fake_sleep),
            ):
                await client.subscribe("sync.pull.requested", on_message)

        asyncio.run(run())
        assert received == [{"machine": "mac"}]

    def test_subscribe_is_no_op_when_nats_unavailable(self):
        from nats.errors import NoServersError

        client = NATSClient()
        called = []

        async def on_message(data):
            called.append(data)

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
                patch("asyncio.sleep", new=AsyncMock()),
            ):
                await client.subscribe("sync.pull.requested", on_message)

        asyncio.run(run())
        assert called == []  # No crash, no callback invoked

    def test_subscribe_handles_malformed_json_gracefully(self):
        client = NATSClient()
        mock_nc = MagicMock()
        received = []

        async def fake_subscribe(subject, cb):
            msg = MagicMock()
            msg.data = b"not json {"
            await cb(msg)

        mock_nc.subscribe = fake_subscribe

        async def on_message(data):
            received.append(data)

        async def run():
            with (
                patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
                patch("asyncio.sleep", side_effect=asyncio.CancelledError),
            ):
                await client.subscribe("sync.pull.requested", on_message)

        asyncio.run(run())
        # Malformed JSON produces empty dict, callback still called
        assert received == [{}]


class TestSshTunnel:
    """Tests for NATSClient._open_ssh_tunnel Mac auto-tunnel logic."""

    def test_when_not_mac_then_no_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_HOST", "hetzner")
        client = NATSClient()
        with patch("ai_cli.messaging.subprocess.Popen") as mock_popen:
            asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()
        assert client._tunnel_proc is None

    def test_when_mac_and_port_reachable_then_no_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_HOST", "mac")
        client = NATSClient()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with (
            patch("ai_cli.messaging.socket.create_connection", return_value=mock_conn),
            patch("ai_cli.messaging.subprocess.Popen") as mock_popen,
        ):
            asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()
        assert client._tunnel_proc is None

    def test_when_mac_and_port_unreachable_then_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_HOST", "mac")
        client = NATSClient()
        mock_proc = MagicMock()
        call_count = 0

        def mock_create_connection(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("refused")  # initial reachability check
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            return mock_conn  # tunnel up on retry

        fake_cfg = {"remote": {"host": "192.0.2.1", "user": "user", "port": 22}}
        with (
            patch("ai_cli.messaging.socket.create_connection", side_effect=mock_create_connection),
            patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", return_value=fake_cfg),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())

        mock_popen.assert_called_once_with(
            ["ssh", "-fNL", "4222:localhost:4222", "-o", "ConnectTimeout=5", "-p", "22", "user@192.0.2.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert client._tunnel_proc is mock_proc

    def test_when_mac_and_tunnel_never_comes_up_then_exits_gracefully(self, monkeypatch):
        monkeypatch.setenv("AI_HOST", "mac")
        client = NATSClient()
        mock_proc = MagicMock()
        fake_cfg = {"remote": {"host": "192.0.2.1", "user": "user", "port": 22}}
        with (
            patch("ai_cli.messaging.socket.create_connection", side_effect=OSError("refused")),
            patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", return_value=fake_cfg),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())
        assert client._tunnel_proc is mock_proc  # proc stored even if port never came up

    def test_when_tunnel_up_then_ssh_f_parent_is_reaped(self, monkeypatch):
        """AI-CLI-86: the `ssh -f` foreground parent exits after auth and must be
        reaped, or it lingers as a zombie for the watcher's whole lifetime."""
        monkeypatch.setenv("AI_HOST", "mac")
        client = NATSClient()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already exited → reaped by poll()
        call_count = 0

        def mock_create_connection(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("refused")
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            return conn

        fake_cfg = {"remote": {"host": "192.0.2.1", "user": "user", "port": 22}}
        with (
            patch("ai_cli.messaging.socket.create_connection", side_effect=mock_create_connection),
            patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", return_value=fake_cfg),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())

        mock_proc.poll.assert_called()  # reap ran

    def test_when_parent_still_running_at_reap_then_waited(self, monkeypatch):
        """If poll() shows the parent hasn't exited yet, reap must wait() on it."""
        monkeypatch.setenv("AI_HOST", "mac")
        client = NATSClient()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # not exited yet → must wait()

        fake_cfg = {"remote": {"host": "192.0.2.1", "user": "user", "port": 22}}
        with (
            patch("ai_cli.messaging.socket.create_connection", side_effect=OSError("refused")),
            patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", return_value=fake_cfg),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())

        mock_proc.wait.assert_called_once()  # reaped via wait when still running


class TestNATSClientCloseTunnel:
    def test_close_terminates_tunnel_proc_if_present(self):
        client = NATSClient()
        mock_proc = MagicMock()
        client._tunnel_proc = mock_proc
        asyncio.run(client.close())
        mock_proc.terminate.assert_called_once()
        assert client._tunnel_proc is None

    def test_close_with_no_tunnel_proc_does_not_raise(self):
        client = NATSClient()
        asyncio.run(client.close())  # no tunnel, no nc — should be silent


class TestOpenSshTunnel:
    def test_skips_when_not_mac(self):
        client = NATSClient()
        with patch.dict("os.environ", {"AI_HOST": "hetzner"}), patch("subprocess.Popen") as mock_popen:
            asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()

    def test_skips_when_port_already_reachable(self):
        client = NATSClient()
        with (
            patch.dict("os.environ", {"AI_HOST": "mac"}),
            patch("socket.create_connection"),  # succeeds — port open
            patch("subprocess.Popen") as mock_popen,
        ):
            asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()

    def test_uses_vpn_host_when_configured(self):
        """When vpn_host is set and VPN is active, the SSH tunnel uses vpn_host."""
        client = NATSClient()
        config = {"remote": {"host": "100.106.24.69", "vpn_host": "192.0.2.1", "user": "user", "port": 22}}
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return MagicMock()

        with (
            patch.dict("os.environ", {"AI_HOST": "mac"}),
            patch("socket.create_connection", side_effect=OSError),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", new=AsyncMock(side_effect=lambda _: None)),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.transport._is_vpn_active", return_value=True),
        ):
            asyncio.run(client._open_ssh_tunnel())

        assert len(popen_calls) == 1
        ssh_cmd = " ".join(popen_calls[0])
        assert "192.0.2.1" in ssh_cmd  # vpn_host used
        assert "100.106.24.69" not in ssh_cmd  # host not used

    def test_uses_configured_host_not_hardcoded_ip(self):
        """When VPN is off, tunnel uses config remote.host (not vpn_host)."""
        client = NATSClient()
        config = {"remote": {"host": "100.106.24.69", "user": "user", "port": 22}}
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return MagicMock()

        async def fake_sleep(_):
            pass

        with (
            patch.dict("os.environ", {"AI_HOST": "mac"}),
            patch("socket.create_connection", side_effect=OSError),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())

        assert len(popen_calls) == 1
        ssh_cmd = popen_calls[0]
        assert "100.106.24.69" in " ".join(ssh_cmd)
        assert "192.0.2.1" not in " ".join(ssh_cmd)

    def test_skips_tunnel_when_config_missing(self):
        """If config load fails and no host/user is available, no tunnel is opened."""
        client = NATSClient()
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return MagicMock()

        with (
            patch.dict("os.environ", {"AI_HOST": "mac"}),
            patch("socket.create_connection", side_effect=OSError),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", side_effect=Exception("no config")),
        ):
            asyncio.run(client._open_ssh_tunnel())

        # No tunnel opened — no host/user available when config fails
        assert len(popen_calls) == 0

    def test_includes_identity_file_when_configured(self):
        """identity_file in remote config adds -i flag to SSH command (line 58)."""
        client = NATSClient()
        config = {
            "remote": {
                "host": "192.0.2.1",
                "user": "user",
                "port": 22,
                "identity_file": "/home/user/.ssh/id_rsa",
            }
        }
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return MagicMock()

        with (
            patch.dict("os.environ", {"AI_HOST": "mac"}),
            patch("socket.create_connection", side_effect=OSError),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
        ):
            asyncio.run(client._open_ssh_tunnel())

        assert len(popen_calls) == 1
        ssh_cmd = " ".join(popen_calls[0])
        assert "-i" in ssh_cmd
        assert "/home/user/.ssh/id_rsa" in ssh_cmd
