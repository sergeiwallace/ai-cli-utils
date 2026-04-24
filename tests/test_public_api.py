"""Contract tests for the public importable API surface (AI-CLI-46).

Two layers:
- Symbol/import checks: verify __all__ declarations and basic instantiation.
- Contract enforcement: catch parameter renames, field removals, default changes,
  and version drift that would silently break downstream callers (e.g. aido).
"""

from __future__ import annotations

import dataclasses
import inspect
import tomllib
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Version sync
# ---------------------------------------------------------------------------


def test_package_version_matches_pyproject_toml():
    """Regression guard: __init__.__version__ must stay in sync with pyproject.toml."""
    import ai_cli

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    expected = data["project"]["version"]
    assert ai_cli.__version__ == expected, (
        f"__init__.__version__ ({ai_cli.__version__!r}) out of sync with "
        f"pyproject.toml ({expected!r}) — bump one to match the other"
    )


# ---------------------------------------------------------------------------
# __all__ symbol declarations
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Contract enforcement — parameter and field names
# ---------------------------------------------------------------------------


def test_run_gemini_signature_has_all_documented_parameters():
    """Catch parameter renames in run_gemini() before aido callers break."""
    from ai_cli.gemini import run_gemini

    params = inspect.signature(run_gemini).parameters

    assert "prompt" in params
    assert params["prompt"].default is inspect.Parameter.empty  # positional, required

    expected_kwargs: dict[str, object] = {
        "model": "deep-think",
        "output": None,
        "quiet": False,
        "verbose": False,
        "timeout_s": 600,
        "start_tier": 1,
        "paid_fallback_enabled": None,
    }
    for name, default in expected_kwargs.items():
        assert name in params, f"documented kwarg {name!r} missing from run_gemini()"
        assert params[name].default == default, (
            f"run_gemini kwarg {name!r}: expected default {default!r}, got {params[name].default!r}"
        )


def test_gemini_result_has_all_documented_fields():
    """Catch field renames or removals in GeminiResult."""
    from ai_cli.gemini import GeminiResult

    field_names = {f.name for f in dataclasses.fields(GeminiResult)}
    documented = {
        "content",
        "model",
        "tier",
        "tier_name",
        "success",
        "error",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "is_deep_research",
        "attempts",
        "event_id",
        "machine",
    }
    missing = documented - field_names
    assert not missing, f"GeminiResult missing documented fields: {missing}"


def test_attempt_log_has_all_documented_fields():
    """Catch field renames or removals in AttemptLog."""
    from ai_cli.gemini import AttemptLog

    field_names = {f.name for f in dataclasses.fields(AttemptLog)}
    documented = {"tier", "tier_name", "model", "success", "error", "duration_ms"}
    missing = documented - field_names
    assert not missing, f"AttemptLog missing documented fields: {missing}"


def test_quota_snapshot_has_all_documented_fields():
    """Catch field renames or removals in QuotaSnapshot."""
    from ai_cli.quota import QuotaSnapshot

    field_names = {f.name for f in dataclasses.fields(QuotaSnapshot)}
    documented = {
        "weekly_all_models_pct",
        "session_pct",
        "weekly_sonnet_pct",
        "extra_pct",
        "reset_at",
    }
    missing = documented - field_names
    assert not missing, f"QuotaSnapshot missing documented fields: {missing}"


def test_read_latest_snapshot_maps_usage_percent_and_reset_at():
    """Verify field mapping from quota_db status dict — both fields callers depend on."""
    from ai_cli.quota import QuotaSnapshot, read_latest_snapshot

    status = {
        "latest_snapshot": {"usage_percent": 75.0, "snapshotted_at": "2026-04-17T10:00:00Z"},
        "reset_at": "2026-04-28T06:00:00Z",
        "week_start": "2026-04-21T06:00:00Z",
        "days_remaining": 7.0,
        "total_consumed": 0,
        "per_session_breakdown": [],
        "burn_rate": {},
        "alerts": [],
    }
    with patch("ai_cli.quota_db.get_current_status", return_value=status):
        result = read_latest_snapshot()

    assert isinstance(result, QuotaSnapshot)
    assert result.weekly_all_models_pct == 75.0
    assert result.reset_at == "2026-04-28T06:00:00Z"
    assert result.session_pct is None  # not in snapshot dict — must default to None


# ---------------------------------------------------------------------------
# Basic instantiation / importability
# ---------------------------------------------------------------------------


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
