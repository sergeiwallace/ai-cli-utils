"""Tests for the vpn-watch Circus watcher (src/ai_cli/vpn_watch.py)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from ai_cli.vpn_watch import _vpn_watch_loop, run_vpn_watch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(poll_interval: int = 1) -> dict:
    return {
        "remote": {"vpn_poll_interval": poll_interval},
        "messaging": {"nats_servers": ["nats://localhost:4222"]},
    }


def _mock_nc(publish_side_effect=None):
    nc = MagicMock()
    nc.nc = MagicMock()
    nc.nc.publish = AsyncMock(side_effect=publish_side_effect)
    nc.connect = AsyncMock()
    nc.close = AsyncMock()
    return nc


# ---------------------------------------------------------------------------
# run_vpn_watch
# ---------------------------------------------------------------------------


class TestRunVpnWatch:
    def test_when_keyboard_interrupt_then_exits_cleanly(self, tmp_path):
        with (
            patch("ai_cli.vpn_watch._vpn_watch_loop", side_effect=KeyboardInterrupt),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
        ):
            # Should not raise
            run_vpn_watch({})


# ---------------------------------------------------------------------------
# _vpn_watch_loop — VPN state change detection and publishing
# ---------------------------------------------------------------------------


class TestVpnWatchLoop:
    def test_when_vpn_state_changes_then_publishes_to_nats(self, tmp_path):
        nc = _mock_nc()
        # VPN starts inactive, becomes active; extra value lets the loop run once more
        # before fake_sleep terminates it
        vpn_states = [False, True, True, True]
        vpn_iter = iter(vpn_states)
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", side_effect=vpn_iter),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        nc.nc.publish.assert_called()
        published_data = json.loads(nc.nc.publish.call_args[0][1].decode())
        assert published_data["vpn"] is True

    def test_when_vpn_inactive_after_debounce_then_no_publish(self, tmp_path):
        nc = _mock_nc()
        # VPN appears to change but reverts within debounce window; extra value lets
        # the loop iterate once more before fake_sleep terminates it
        vpn_states = [False, True, False, False]
        vpn_iter = iter(vpn_states)
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", side_effect=vpn_iter),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        nc.nc.publish.assert_not_called()

    def test_when_vpn_state_changes_then_logs_transition(self, tmp_path):
        nc = _mock_nc()
        vpn_states = [False, True, True, True]
        vpn_iter = iter(vpn_states)
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", side_effect=vpn_iter),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        log_file = tmp_path / "vpn-transitions.log"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["vpn"] is True
        assert "ts" in entry

    def test_when_nats_unavailable_then_still_logs(self, tmp_path):
        nc = _mock_nc()
        nc.nc = None  # NATS connection failed
        vpn_states = [False, True, True, True]
        vpn_iter = iter(vpn_states)
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", side_effect=vpn_iter),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        log_file = tmp_path / "vpn-transitions.log"
        assert log_file.exists()

    def test_when_vpn_goes_inactive_then_publishes_vpn_false(self, tmp_path):
        nc = _mock_nc()
        vpn_states = [True, False, False, False]
        vpn_iter = iter(vpn_states)
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", side_effect=vpn_iter),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        published_data = json.loads(nc.nc.publish.call_args[0][1].decode())
        assert published_data["vpn"] is False

    def test_when_no_state_change_then_no_publish(self, tmp_path):
        nc = _mock_nc()
        # VPN stays inactive throughout
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count > 4:
                raise asyncio.CancelledError

        async def run():
            with (
                patch("ai_cli.vpn_watch.NATSClient", return_value=nc),
                patch("ai_cli.vpn_watch.asyncio.sleep", side_effect=fake_sleep),
                patch("ai_cli.vpn_watch._is_vpn_active", return_value=False),
                patch("ai_cli.vpn_watch.get_xdg_state_home", return_value=tmp_path),
            ):
                try:
                    await _vpn_watch_loop(_make_config())
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        nc.nc.publish.assert_not_called()
