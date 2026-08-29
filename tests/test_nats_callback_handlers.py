"""Unit tests for the remaining NATS callback handlers in main.py."""

import asyncio
from unittest.mock import patch

from ai_cli.main import _on_quota_snapshot_handler


def test_given_valid_snapshot_when_handler_then_calls_record():
    data = {
        "usage_percent": 55.0,
        "session_pct": 10.0,
        "weekly_sonnet_pct": 20.0,
        "extra_pct": 5.0,
        "reset_at": "2026-04-20T00:00:00Z",
    }

    with patch("ai_cli.quota_db.record_quota_snapshot") as mock_rec:
        asyncio.run(_on_quota_snapshot_handler(data))

    mock_rec.assert_called_once_with(
        usage_percent=55.0,
        session_pct=10.0,
        weekly_sonnet_pct=20.0,
        weekly_model_name=None,
        extra_pct=5.0,
        reset_at="2026-04-20T00:00:00Z",
    )


def test_given_record_error_when_handler_then_does_not_propagate():
    with patch("ai_cli.quota_db.record_quota_snapshot", side_effect=RuntimeError("db down")):
        asyncio.run(_on_quota_snapshot_handler({"usage_percent": 50.0}))
