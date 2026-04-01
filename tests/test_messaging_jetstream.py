"""Tests for JetStream-upgraded NATSClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from ai_cli.messaging import NATSClient, STREAM_CONFIG


class TestNATSClientJetStreamConnect:
    def test_connect_when_successful_then_sets_js_handle(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_nc.jetstream.return_value = mock_js

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                await client.connect()

        asyncio.run(run())
        assert client.nc is mock_nc
        assert client.js is mock_js

    def test_connect_when_nats_unavailable_then_js_remains_none(self):
        from nats.errors import NoServersError

        client = NATSClient()

        async def run():
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
                    await client.connect()

        asyncio.run(run())
        assert client.nc is None
        assert client.js is None


class TestNATSClientStreamMapping:
    def test_stream_for_subject_when_fleet_heartbeat_then_returns_fleet(self):
        assert NATSClient._stream_for_subject("fleet.worker.sess-1.heartbeat") == "fleet"

    def test_stream_for_subject_when_sync_pull_then_returns_sync(self):
        assert NATSClient._stream_for_subject("sync.pull.requested") == "sync"

    def test_stream_for_subject_when_session_started_then_returns_session(self):
        assert NATSClient._stream_for_subject("session.abc123.started") == "session"

    def test_stream_for_subject_when_memory_dream_then_returns_memory(self):
        assert NATSClient._stream_for_subject("memory.dream.started") == "memory"

    def test_stream_for_subject_when_unknown_then_returns_none(self):
        assert NATSClient._stream_for_subject("unknown.thing.here") is None


class TestNATSClientJetStreamPublish:
    def test_publish_when_js_available_then_uses_js_publish(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.publish = AsyncMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="sync")
        mock_nc.jetstream.return_value = mock_js
        mock_nc.publish = AsyncMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                result = await client.publish("sync.pull.requested", {"machine": "mac"})
            return result

        result = asyncio.run(run())
        assert result is True
        mock_js.publish.assert_called_once()
        # Core publish should NOT be called when JS succeeds
        mock_nc.publish.assert_not_called()

    def test_publish_when_js_fails_then_falls_back_to_core(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.publish = AsyncMock(side_effect=Exception("JS error"))
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="sync")
        mock_nc.jetstream.return_value = mock_js
        mock_nc.publish = AsyncMock()

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                result = await client.publish("sync.pull.requested", {"machine": "mac"})
            return result

        result = asyncio.run(run())
        assert result is True
        mock_nc.publish.assert_called_once()


class TestNATSClientDurableSubscribe:
    def test_subscribe_durable_when_js_available_then_uses_js_subscribe(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="sync")
        received = []

        async def fake_js_subscribe(subject, durable, cb):
            msg = MagicMock()
            msg.data = b'{"machine": "mac"}'
            msg.ack = AsyncMock()
            await cb(msg)

        mock_js.subscribe = fake_js_subscribe
        mock_nc.jetstream.return_value = mock_js

        async def on_message(data):
            received.append(data)

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    await client.subscribe_durable("sync.pull.requested", "sync-watch", on_message)

        asyncio.run(run())
        assert received == [{"machine": "mac"}]

    def test_subscribe_durable_when_no_js_then_falls_back_to_core(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = None
        received = []

        async def fake_subscribe(subject, cb):
            msg = MagicMock()
            msg.data = b'{"key": "val"}'
            await cb(msg)

        mock_nc.subscribe = fake_subscribe

        async def on_message(data):
            received.append(data)

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    await client.subscribe_durable("sync.pull.requested", "sync-watch", on_message)

        asyncio.run(run())
        assert received == [{"key": "val"}]


class TestNATSClientEnsureStream:
    def test_ensure_stream_when_no_js_then_returns_false(self):
        client = NATSClient()
        # js is None by default

        async def run():
            return await client._ensure_stream("sync.pull.requested")

        result = asyncio.run(run())
        assert result is False

    def test_ensure_stream_when_stream_already_ensured_then_returns_true(self):
        client = NATSClient()
        client.js = MagicMock()
        client._streams_ensured.add("sync")

        async def run():
            return await client._ensure_stream("sync.pull.requested")

        result = asyncio.run(run())
        assert result is True

    def test_ensure_stream_when_find_fails_and_add_succeeds_then_ensured(self):
        client = NATSClient()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(side_effect=Exception("not found"))
        mock_js.add_stream = AsyncMock()
        client.js = mock_js

        async def run():
            return await client._ensure_stream("sync.pull.requested")

        result = asyncio.run(run())
        assert result is True
        assert "sync" in client._streams_ensured


class TestNATSClientSubscribeDurableEdgeCases:
    def test_subscribe_durable_when_nc_unavailable_then_returns_immediately(self):
        client = NATSClient()
        # nc stays None — connect() is a no-op

        async def run():
            with patch.object(client, "connect", new=AsyncMock()):
                await client.subscribe_durable("sync.pull.requested", "test", AsyncMock())

        asyncio.run(run())  # must not raise

    def test_subscribe_durable_when_ensure_stream_raises_then_falls_back_to_core(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_nc.jetstream.return_value = mock_js
        received = []

        async def fake_core_subscribe(subject, cb):
            msg = MagicMock()
            msg.data = b'{"key": "fallback"}'
            await cb(msg)

        mock_nc.subscribe = fake_core_subscribe

        async def on_message(data):
            received.append(data)

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch.object(NATSClient, "_ensure_stream", new=AsyncMock(side_effect=Exception("stream error"))):
                    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                        await client.subscribe_durable("sync.pull.requested", "sync-watch", on_message)

        asyncio.run(run())
        assert received == [{"key": "fallback"}]

    def test_subscribe_durable_when_invalid_json_then_passes_empty_dict(self):
        client = NATSClient()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="sync")
        received = []

        async def fake_js_subscribe(subject, durable, cb):
            msg = MagicMock()
            msg.data = b"not valid json {{{"
            msg.ack = AsyncMock()
            await cb(msg)

        mock_js.subscribe = fake_js_subscribe
        mock_nc.jetstream.return_value = mock_js

        async def on_message(data):
            received.append(data)

        async def run():
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    await client.subscribe_durable("sync.pull.requested", "sync-watch", on_message)

        asyncio.run(run())
        assert received == [{}]


class TestStreamConfig:
    def test_all_design_doc_streams_have_config(self):
        expected_streams = {
            "fleet",
            "sync",
            "session",
            "memory",
            "quota",
            "telemetry",
            "aido",
            "task",
            "health",
            "handoff",
        }
        assert set(STREAM_CONFIG.keys()) == expected_streams
