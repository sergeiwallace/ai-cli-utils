import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch


from ai_cli.messaging import NATSClient


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
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
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
            with patch("nats.connect", new=fake_connect):
                with patch("asyncio.sleep", new=AsyncMock()):
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
                result = await client.publish_heartbeat("sess-1", {"status": "WORKING"})
            return result

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
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
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
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
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
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", new=fake_sleep):
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
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
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
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    await client.subscribe("sync.pull.requested", on_message)

        asyncio.run(run())
        # Malformed JSON produces empty dict, callback still called
        assert received == [{}]


class TestSshTunnel:
    """Tests for NATSClient._open_ssh_tunnel Mac auto-tunnel logic."""

    def test_when_not_mac_then_no_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_CLI_HOST", "hetzner")
        client = NATSClient()
        with patch("ai_cli.messaging.subprocess.Popen") as mock_popen:
            asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()
        assert client._tunnel_proc is None

    def test_when_mac_and_port_reachable_then_no_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_CLI_HOST", "mac")
        client = NATSClient()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("ai_cli.messaging.socket.create_connection", return_value=mock_conn):
            with patch("ai_cli.messaging.subprocess.Popen") as mock_popen:
                asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()
        assert client._tunnel_proc is None

    def test_when_mac_and_port_unreachable_then_tunnel_opened(self, monkeypatch):
        monkeypatch.setenv("AI_CLI_HOST", "mac")
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
        with patch("ai_cli.messaging.socket.create_connection", side_effect=mock_create_connection):
            with patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc) as mock_popen:
                with patch("asyncio.sleep", new=AsyncMock()):
                    with patch("ai_cli.main.load_config", return_value=fake_cfg):
                        asyncio.run(client._open_ssh_tunnel())

        mock_popen.assert_called_once_with(
            ["ssh", "-fNL", "4222:localhost:4222", "-p", "22", "user@192.0.2.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert client._tunnel_proc is mock_proc

    def test_when_mac_and_tunnel_never_comes_up_then_exits_gracefully(self, monkeypatch):
        monkeypatch.setenv("AI_CLI_HOST", "mac")
        client = NATSClient()
        mock_proc = MagicMock()
        with patch("ai_cli.messaging.socket.create_connection", side_effect=OSError("refused")):
            with patch("ai_cli.messaging.subprocess.Popen", return_value=mock_proc):
                with patch("asyncio.sleep", new=AsyncMock()):
                    asyncio.run(client._open_ssh_tunnel())
        assert client._tunnel_proc is mock_proc  # proc stored even if port never came up


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
        with patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}):
            with patch("subprocess.Popen") as mock_popen:
                asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()

    def test_skips_when_port_already_reachable(self):
        client = NATSClient()
        with patch.dict("os.environ", {"AI_CLI_HOST": "mac"}):
            with patch("socket.create_connection"):  # succeeds — port open
                with patch("subprocess.Popen") as mock_popen:
                    asyncio.run(client._open_ssh_tunnel())
        mock_popen.assert_not_called()

    def test_uses_configured_host_not_hardcoded_ip(self):
        """Tunnel must use config remote.host (Tailscale IP) not a hardcoded IP."""
        client = NATSClient()
        config = {"remote": {"host": "100.106.24.69", "user": "user", "port": 22}}
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return MagicMock()

        async def fake_sleep(_):
            pass

        with patch.dict("os.environ", {"AI_CLI_HOST": "mac"}):
            with patch("socket.create_connection", side_effect=OSError):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch("asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
                        with patch("ai_cli.messaging.load_config", return_value=config, create=True):
                            with patch("ai_cli.main.load_config", return_value=config):
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

        with patch.dict("os.environ", {"AI_CLI_HOST": "mac"}):
            with patch("socket.create_connection", side_effect=OSError):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch("asyncio.sleep", new=AsyncMock()):
                        with patch("ai_cli.main.load_config", side_effect=Exception("no config")):
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

        with patch.dict("os.environ", {"AI_CLI_HOST": "mac"}):
            with patch("socket.create_connection", side_effect=OSError):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch("asyncio.sleep", new=AsyncMock()):
                        with patch("ai_cli.messaging.load_config", return_value=config, create=True):
                            with patch("ai_cli.main.load_config", return_value=config):
                                asyncio.run(client._open_ssh_tunnel())

        assert len(popen_calls) == 1
        ssh_cmd = " ".join(popen_calls[0])
        assert "-i" in ssh_cmd
        assert "/home/user/.ssh/id_rsa" in ssh_cmd
