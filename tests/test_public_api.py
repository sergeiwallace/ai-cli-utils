"""Smoke tests for the public importable API surface.

Verifies that symbols declared in __all__ are importable as library code
and that read_latest_snapshot() returns the correct type.
"""

from __future__ import annotations

from unittest.mock import patch


def test_gemini_module_exports_declared_symbols():
    from ai_cli.gemini import __all__ as gemini_all

    assert "GeminiResult" in gemini_all
    assert "AttemptLog" in gemini_all
    assert "run_gemini" in gemini_all


def test_quota_module_exports_declared_symbols():
    from ai_cli.quota import __all__ as quota_all

    assert "QuotaSnapshot" in quota_all
    assert "read_latest_snapshot" in quota_all


def test_gemini_result_importable():
    from ai_cli.gemini import GeminiResult

    r = GeminiResult(model="gemini-3-flash-preview")
    assert r.model == "gemini-3-flash-preview"
    assert r.success is False


def test_quota_snapshot_importable():
    from ai_cli.quota import QuotaSnapshot

    s = QuotaSnapshot(weekly_all_models_pct=42.5)
    assert s.weekly_all_models_pct == 42.5
    assert s.session_pct is None


def test_read_latest_snapshot_when_no_data_then_returns_none():
    from ai_cli.quota import read_latest_snapshot

    empty_status = {
        "latest_snapshot": None,
        "week_start": "2026-04-14T06:00:00Z",
        "reset_at": "2026-04-21T06:00:00Z",
        "days_remaining": 4.0,
        "total_consumed": 0,
        "per_session_breakdown": [],
        "burn_rate": {},
        "alerts": [],
    }
    with patch("ai_cli.quota_db.get_current_status", return_value=empty_status):
        result = read_latest_snapshot()
    assert result is None


def test_read_latest_snapshot_when_data_exists_then_returns_snapshot():
    from ai_cli.quota import QuotaSnapshot, read_latest_snapshot

    status = {
        "latest_snapshot": {"usage_percent": 63.5, "snapshotted_at": "2026-04-17T10:00:00Z"},
        "week_start": "2026-04-14T06:00:00Z",
        "reset_at": "2026-04-21T06:00:00Z",
        "days_remaining": 4.0,
        "total_consumed": 0,
        "per_session_breakdown": [],
        "burn_rate": {},
        "alerts": [],
    }
    with patch("ai_cli.quota_db.get_current_status", return_value=status):
        result = read_latest_snapshot()
    assert isinstance(result, QuotaSnapshot)
    assert result.weekly_all_models_pct == 63.5
    assert result.reset_at == "2026-04-21T06:00:00Z"
    assert result.session_pct is None
