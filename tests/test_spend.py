"""Tests for spend module — ai spend gemini command."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.spend import (
    DailyStats,
    _read_dr_daily_counter,
    cmd_spend_gemini,
    get_daily_stats,
    get_monthly_stats,
    query_bigquery_spend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_log(log_dir: Path, date: str, entries: list[dict]) -> None:
    """Write a JSONL log file for the given date."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{date}.jsonl"
    with open(log_file, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# DailyStats dataclass
# ---------------------------------------------------------------------------


class TestDailyStats:
    def test_daily_stats_when_default_then_zeroed(self):
        s = DailyStats()
        assert s.total_runs == 0
        assert s.successful_runs == 0
        assert s.by_model == {}
        assert s.input_tokens is None


# ---------------------------------------------------------------------------
# get_daily_stats
# ---------------------------------------------------------------------------


class TestGetDailyStats:
    def test_when_no_log_file_then_returns_zeroed_stats(self, tmp_path):
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.total_runs == 0
        assert stats.successful_runs == 0

    def test_when_successful_run_then_counts_model(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [{"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False}],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.total_runs == 1
        assert stats.successful_runs == 1
        assert stats.by_model["flash"] == 1

    def test_when_failed_run_then_not_in_successful_count(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [{"success": False, "model": "flash", "tier_name": "oauth", "is_deep_research": False}],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.total_runs == 1
        assert stats.successful_runs == 0
        assert stats.by_model == {}

    def test_when_deep_research_oauth_then_counted(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [{"success": True, "model": "deep-research", "tier_name": "oauth", "is_deep_research": True}],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.deep_research_oauth == 1
        assert stats.deep_research_paid == 0

    def test_when_deep_research_paid_then_counted_separately(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {
                    "success": True,
                    "model": "deep-research",
                    "tier_name": "ai_studio_paid",
                    "is_deep_research": True,
                }
            ],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.deep_research_paid == 1
        assert stats.deep_research_oauth == 0

    def test_when_tokens_present_then_accumulated(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {
                    "success": True,
                    "model": "flash",
                    "tier_name": "ai_studio_free",
                    "is_deep_research": False,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
                {
                    "success": True,
                    "model": "flash",
                    "tier_name": "ai_studio_free",
                    "is_deep_research": False,
                    "input_tokens": 200,
                    "output_tokens": 75,
                    "total_tokens": 275,
                },
            ],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.input_tokens == 300
        assert stats.output_tokens == 125
        assert stats.total_tokens == 425

    def test_when_tokens_null_then_stays_none(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {
                    "success": True,
                    "model": "flash",
                    "tier_name": "oauth",
                    "is_deep_research": False,
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                }
            ],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.input_tokens is None

    def test_when_multiple_models_then_each_counted(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False},
                {"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False},
                {"success": True, "model": "deep-think", "tier_name": "oauth", "is_deep_research": False},
            ],
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.by_model["flash"] == 2
        assert stats.by_model["deep-think"] == 1

    def test_when_malformed_jsonl_line_then_skipped(self, tmp_path):
        (tmp_path / "2026-04-11.jsonl").write_text(
            '{"success": true, "model": "flash", "tier_name": "oauth", "is_deep_research": false}\n'
            "not-valid-json\n"
            '{"success": true, "model": "pro", "tier_name": "oauth", "is_deep_research": false}\n'
        )
        stats = get_daily_stats("2026-04-11", log_dir=tmp_path)
        assert stats.successful_runs == 2


# ---------------------------------------------------------------------------
# get_monthly_stats
# ---------------------------------------------------------------------------


class TestGetMonthlyStats:
    def test_when_no_log_files_then_returns_zeroed_stats(self, tmp_path):
        stats = get_monthly_stats("2026-04", log_dir=tmp_path)
        assert stats.total_runs == 0

    def test_when_multiple_days_then_aggregated(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-10",
            [{"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False}],
        )
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False},
                {"success": True, "model": "deep-think", "tier_name": "oauth", "is_deep_research": False},
            ],
        )
        stats = get_monthly_stats("2026-04", log_dir=tmp_path)
        assert stats.total_runs == 3
        assert stats.successful_runs == 3
        assert stats.by_model["flash"] == 2
        assert stats.by_model["deep-think"] == 1

    def test_when_deep_research_across_days_then_aggregated(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-04-10",
            [{"success": True, "model": "deep-research", "tier_name": "oauth", "is_deep_research": True}],
        )
        _write_log(
            tmp_path,
            "2026-04-11",
            [
                {"success": True, "model": "deep-research", "tier_name": "oauth", "is_deep_research": True},
                {
                    "success": True,
                    "model": "deep-research",
                    "tier_name": "ai_studio_paid",
                    "is_deep_research": True,
                },
            ],
        )
        stats = get_monthly_stats("2026-04", log_dir=tmp_path)
        assert stats.deep_research_oauth == 2
        assert stats.deep_research_paid == 1

    def test_when_different_month_files_then_not_included(self, tmp_path):
        _write_log(
            tmp_path,
            "2026-03-31",
            [{"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False}],
        )
        _write_log(
            tmp_path,
            "2026-04-01",
            [{"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False}],
        )
        stats = get_monthly_stats("2026-04", log_dir=tmp_path)
        assert stats.total_runs == 1


# ---------------------------------------------------------------------------
# query_bigquery_spend
# ---------------------------------------------------------------------------


class TestQueryBigquerySpend:
    def test_when_no_table_configured_then_not_available(self):
        result = query_bigquery_spend("my-project", "", "2026-04")
        assert result["available"] is False
        assert "billing_export_table" in result["error"]

    def test_when_google_cloud_bigquery_not_installed_then_not_available(self):
        with patch.dict("sys.modules", {"google.cloud": None, "google.cloud.bigquery": None}):
            # Simulate ImportError
            import builtins

            original_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if "bigquery" in name:
                    raise ImportError("no module named google.cloud.bigquery")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                result = query_bigquery_spend("my-project", "proj.dataset.table", "2026-04")
        assert result["available"] is False
        assert "not installed" in result["error"]

    def _make_bq_mocks(self, rows=None, side_effect=None):
        """Build a mock google.cloud.bigquery module."""
        mock_query_job = MagicMock()
        if side_effect:
            mock_query_job.result.side_effect = side_effect
        else:
            mock_query_job.result.return_value = rows or []
        mock_client = MagicMock()
        mock_client.query.return_value = mock_query_job
        mock_bq = MagicMock()
        mock_bq.Client.return_value = mock_client
        mock_google_cloud = MagicMock()
        mock_google_cloud.bigquery = mock_bq
        modules = {
            "google": MagicMock(),
            "google.cloud": mock_google_cloud,
            "google.cloud.bigquery": mock_bq,
        }
        return modules

    def test_when_query_raises_then_not_available(self):
        modules = self._make_bq_mocks(side_effect=Exception("connection refused"))
        with patch.dict("sys.modules", modules):
            result = query_bigquery_spend("my-project", "proj.dataset.table", "2026-04")
        assert result["available"] is False
        assert "connection refused" in result["error"]

    def test_when_query_succeeds_then_returns_costs(self):
        mock_row1 = MagicMock()
        mock_row1.sku_description = "Gemini 1.5 Pro Input Tokens"
        mock_row1.total_cost = 1.50
        mock_row1.data_as_of = "2026-04-10"

        mock_row2 = MagicMock()
        mock_row2.sku_description = "Gemini 1.5 Pro Output Tokens"
        mock_row2.total_cost = 3.00
        mock_row2.data_as_of = "2026-04-10"

        modules = self._make_bq_mocks(rows=[mock_row1, mock_row2])
        with patch.dict("sys.modules", modules):
            result = query_bigquery_spend("my-project", "proj.dataset.table", "2026-04")
        assert result["available"] is True
        assert result["total_cost_usd"] == pytest.approx(4.5)
        assert len(result["by_sku"]) == 2
        assert result["data_as_of"] == "2026-04-10"

    def test_when_zero_rows_then_available_with_zero_cost(self):
        modules = self._make_bq_mocks(rows=[])
        with patch.dict("sys.modules", modules):
            result = query_bigquery_spend("my-project", "proj.dataset.table", "2026-04")
        assert result["available"] is True
        assert result["total_cost_usd"] == 0.0
        assert result["by_sku"] == {}
        assert result["data_as_of"] is None


# ---------------------------------------------------------------------------
# _read_dr_daily_counter
# ---------------------------------------------------------------------------


class TestReadDrDailyCounter:
    def test_when_no_file_then_returns_zeroed_dict(self, tmp_path):
        counter = _read_dr_daily_counter(tmp_path / "dr-daily.json")
        assert counter["oauth_count"] == 0
        assert counter["paid_count"] == 0
        assert counter["last_run"] is None

    def test_when_today_then_returns_existing(self, tmp_path):
        today = time.strftime("%Y-%m-%d")
        dr_file = tmp_path / "dr-daily.json"
        dr_file.write_text(json.dumps({"date": today, "oauth_count": 5, "paid_count": 2, "last_run": "x"}))
        counter = _read_dr_daily_counter(dr_file)
        assert counter["oauth_count"] == 5
        assert counter["paid_count"] == 2

    def test_when_stale_date_then_returns_zeroed(self, tmp_path):
        dr_file = tmp_path / "dr-daily.json"
        dr_file.write_text(json.dumps({"date": "2020-01-01", "oauth_count": 10, "paid_count": 3, "last_run": "x"}))
        counter = _read_dr_daily_counter(dr_file)
        assert counter["oauth_count"] == 0
        assert counter["paid_count"] == 0


# ---------------------------------------------------------------------------
# cmd_spend_gemini
# ---------------------------------------------------------------------------


class TestCmdSpendGemini:
    def test_when_no_runs_then_prints_no_runs_message(self, tmp_path, capsys):
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "No runs logged today" in out

    def test_when_daily_oauth_dr_runs_then_shows_count(self, tmp_path, capsys):
        today = time.strftime("%Y-%m-%d")
        dr_file = tmp_path / "dr-daily.json"
        dr_file.write_text(json.dumps({"date": today, "oauth_count": 3, "paid_count": 0, "last_run": None}))
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=dr_file)
        out = capsys.readouterr().out
        assert "3 OAuth runs" in out
        assert "17 remaining free" in out

    def test_when_paid_dr_runs_today_then_shown(self, tmp_path, capsys):
        today = time.strftime("%Y-%m-%d")
        dr_file = tmp_path / "dr-daily.json"
        dr_file.write_text(json.dumps({"date": today, "oauth_count": 0, "paid_count": 2, "last_run": None}))
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=dr_file)
        out = capsys.readouterr().out
        assert "2 paid" in out

    def test_when_other_model_runs_then_shown(self, tmp_path, capsys):
        today = time.strftime("%Y-%m-%d")
        _write_log(
            tmp_path,
            today,
            [
                {"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False},
                {"success": True, "model": "flash", "tier_name": "oauth", "is_deep_research": False},
                {"success": True, "model": "deep-think", "tier_name": "oauth", "is_deep_research": False},
            ],
        )
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "flash" in out
        assert "deep-think" in out

    def test_when_bigquery_not_configured_then_setup_message(self, tmp_path, capsys):
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "BigQuery billing export not configured" in out
        assert "Cloud Console" in out

    def test_when_bigquery_configured_and_zero_cost_then_credit_hint(self, tmp_path, capsys):
        config = {
            "gemini_billing": {
                "gcp_project_id": "my-project",
                "billing_export_table": "my-project.billing.export_table",
            }
        }
        bq_result = {
            "available": True,
            "total_cost_usd": 0.0,
            "by_sku": {},
            "data_as_of": "2026-04-10",
            "error": None,
        }
        with patch("ai_cli.spend.query_bigquery_spend", return_value=bq_result):
            cmd_spend_gemini(config, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "$0.00" in out
        assert "Ultra credit appears" in out

    def test_when_bigquery_shows_charges_then_warning_hint(self, tmp_path, capsys):
        config = {
            "gemini_billing": {
                "gcp_project_id": "my-project",
                "billing_export_table": "my-project.billing.export_table",
            }
        }
        bq_result = {
            "available": True,
            "total_cost_usd": 3.20,
            "by_sku": {"Gemini Pro Input": 3.20},
            "data_as_of": "2026-04-10",
            "error": None,
        }
        with patch("ai_cli.spend.query_bigquery_spend", return_value=bq_result):
            cmd_spend_gemini(config, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "$3.20" in out
        assert "Charges are being applied" in out

    def test_when_bigquery_package_missing_then_install_hint(self, tmp_path, capsys):
        config = {
            "gemini_billing": {
                "gcp_project_id": "my-project",
                "billing_export_table": "my-project.billing.export_table",
            }
        }
        bq_result = {
            "available": False,
            "error": "google-cloud-bigquery not installed (pip install google-cloud-bigquery)",
        }
        with patch("ai_cli.spend.query_bigquery_spend", return_value=bq_result):
            cmd_spend_gemini(config, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_when_bigquery_query_fails_then_error_shown(self, tmp_path, capsys):
        config = {
            "gemini_billing": {
                "gcp_project_id": "my-project",
                "billing_export_table": "my-project.billing.export_table",
            }
        }
        bq_result = {"available": False, "error": "BigQuery query failed: permission denied"}
        with patch("ai_cli.spend.query_bigquery_spend", return_value=bq_result):
            cmd_spend_gemini(config, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "query failed" in out

    def test_when_monthly_dr_runs_then_shown(self, tmp_path, capsys):
        today = time.strftime("%Y-%m-%d")
        _write_log(
            tmp_path,
            today,
            [{"success": True, "model": "deep-research", "tier_name": "oauth", "is_deep_research": True}],
        )
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "This month" in out
        assert "OAuth" in out

    def test_returns_zero_on_success(self, tmp_path):
        result = cmd_spend_gemini({}, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        assert result == 0

    def test_when_gcp_project_configured_but_no_table_then_shows_project(self, tmp_path, capsys):
        config = {"gemini_billing": {"gcp_project_id": "my-gcp-project", "billing_export_table": ""}}
        cmd_spend_gemini(config, log_dir=tmp_path, dr_file=tmp_path / "dr-daily.json")
        out = capsys.readouterr().out
        assert "my-gcp-project" in out

    def test_when_daily_limit_custom_then_free_remaining_correct(self, tmp_path, capsys):
        today = time.strftime("%Y-%m-%d")
        dr_file = tmp_path / "dr-daily.json"
        dr_file.write_text(json.dumps({"date": today, "oauth_count": 8, "paid_count": 0, "last_run": None}))
        cmd_spend_gemini({}, log_dir=tmp_path, dr_file=dr_file, dr_daily_limit=10)
        out = capsys.readouterr().out
        assert "2 remaining free" in out
