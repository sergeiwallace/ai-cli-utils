import asyncio
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
