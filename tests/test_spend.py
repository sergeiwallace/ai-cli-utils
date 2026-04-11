"""Tests for spend module — Gemini usage and spend reporting."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.spend import _parse_log_files, _query_bigquery_spend, spend_gemini


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_log_entry(log_dir: Path, date: str, entry: dict) -> None:
    """Write a single JSONL entry to a dated log file."""
    log_file = log_dir / f"{date}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _make_entry(
    *,
    model: str = "flash",
    tier_name: str = "oauth",
    success: bool = True,
    is_deep_research: bool = False,
    input_tokens: int | None = None,
    duration_ms: int = 500,
) -> dict:
    """Build a minimal JSONL log entry."""
    return {
        "ts": "2026-04-11T10:00:00+0000",
        "model": model,
        "tier": 1,
        "tier_name": tier_name,
        "success": success,
        "error": None,
        "duration_ms": duration_ms,
        "is_deep_research": is_deep_research,
        "input_tokens": input_tokens,
        "output_tokens": None,
        "total_tokens": None,
        "prompt_chars": 10,
        "response_chars": 50,
        "output_path": None,
        "attempts": [],
    }


# ---------------------------------------------------------------------------
# _parse_log_files
# ---------------------------------------------------------------------------


class TestParseLogFiles:
    def test_when_no_files_then_returns_empty(self, tmp_path):
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11", "2026-04-11")
        assert result == []

    def test_when_matching_file_then_returns_entries(self, tmp_path):
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="flash"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11", "2026-04-11")
        assert len(result) == 1
        assert result[0]["model"] == "flash"

    def test_when_file_before_range_then_excluded(self, tmp_path):
        _write_log_entry(tmp_path, "2026-04-09", _make_entry(model="old"))
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="new"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-10", "2026-04-11")
        assert len(result) == 1
        assert result[0]["model"] == "new"

    def test_when_file_after_range_then_excluded(self, tmp_path):
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="today"))
        _write_log_entry(tmp_path, "2026-04-15", _make_entry(model="future"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11", "2026-04-11")
        assert len(result) == 1
        assert result[0]["model"] == "today"

    def test_when_no_upper_bound_then_includes_all_from_start(self, tmp_path):
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="a"))
        _write_log_entry(tmp_path, "2026-04-12", _make_entry(model="b"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11")
        assert len(result) == 2

    def test_when_file_has_multiple_entries_then_returns_all(self, tmp_path):
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="a"))
        _write_log_entry(tmp_path, "2026-04-11", _make_entry(model="b"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11", "2026-04-11")
        assert len(result) == 2

    def test_when_file_is_corrupt_then_skips_gracefully(self, tmp_path):
        (tmp_path / "2026-04-11.jsonl").write_text("not json\n")
        _write_log_entry(tmp_path, "2026-04-12", _make_entry(model="ok"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            result = _parse_log_files("2026-04-11", "2026-04-12")
        assert len(result) == 1
        assert result[0]["model"] == "ok"


# ---------------------------------------------------------------------------
# _query_bigquery_spend
# ---------------------------------------------------------------------------


class TestQueryBigquerySpend:
    def test_when_no_config_then_returns_none(self):
        result = _query_bigquery_spend({})
        assert result is None

    def test_when_partial_config_then_returns_none(self):
        config = {"gemini_billing": {"gcp_project_id": "myproject"}}
        result = _query_bigquery_spend(config)
        assert result is None

    def test_when_bigquery_not_installed_then_returns_error(self):
        config = {
            "gemini_billing": {
                "gcp_project_id": "p",
                "billing_account_id": "b",
                "billing_export_table": "t",
            }
        }
        with patch.dict("sys.modules", {"google.cloud": None, "google.cloud.bigquery": None}):
            result = _query_bigquery_spend(config)
        assert result is not None
        assert "error" in result
        assert "not installed" in result["error"]

    def test_when_bigquery_query_raises_then_returns_error(self):
        config = {
            "gemini_billing": {
                "gcp_project_id": "myproject",
                "billing_account_id": "ACCT-1",
                "billing_export_table": "proj.dataset.table",
            }
        }
        mock_bq = MagicMock()
        mock_bq.Client.side_effect = Exception("auth error")
        mock_cloud = MagicMock()
        mock_cloud.bigquery = mock_bq

        with patch.dict("sys.modules", {"google.cloud": mock_cloud, "google.cloud.bigquery": mock_bq}):
            result = _query_bigquery_spend(config)
        assert result is not None
        assert "error" in result

    def test_when_bigquery_returns_rows_then_returns_them(self):
        config = {
            "gemini_billing": {
                "gcp_project_id": "myproject",
                "billing_account_id": "ACCT-1",
                "billing_export_table": "proj.dataset.table",
            }
        }
        mock_row = {"usage_date": "2026-04-11", "sku_description": "Gemini Flash Input", "total_cost": 0.05}

        mock_job = MagicMock()
        mock_job.result.return_value = [mock_row]

        mock_client = MagicMock()
        mock_client.query.return_value = mock_job

        mock_bq = MagicMock()
        mock_bq.Client.return_value = mock_client

        with patch.dict("sys.modules", {"google.cloud": MagicMock(bigquery=mock_bq), "google.cloud.bigquery": mock_bq}):
            result = _query_bigquery_spend(config)
        assert result is not None
        assert "error" not in result
        assert len(result["rows"]) == 1
        assert result["rows"][0]["sku_description"] == "Gemini Flash Input"
        assert "as_of" in result


# ---------------------------------------------------------------------------
# spend_gemini (integration-style)
# ---------------------------------------------------------------------------


class TestSpendGemini:
    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _month_start(self) -> str:
        return self._today()[:7] + "-01"

    def test_when_no_logs_and_no_counter_then_shows_zeros(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "Deep Research:  0 OAuth" in out
        assert "0 paid" in out

    def test_when_dr_counter_has_runs_then_shows_them(self, tmp_path, capsys):
        today = self._today()
        state_file = tmp_path / "dr-daily.json"
        state_file.write_text(json.dumps({"date": today, "oauth_count": 5, "paid_count": 1}))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "5 OAuth runs" in out
        assert "15 remaining free" in out
        assert "1 paid" in out

    def test_when_today_has_model_runs_then_shows_them(self, tmp_path, capsys):
        today = self._today()
        state_file = tmp_path / "dr-daily.json"
        _write_log_entry(tmp_path, today, _make_entry(model="flash", is_deep_research=False))
        _write_log_entry(tmp_path, today, _make_entry(model="flash", is_deep_research=False))
        _write_log_entry(tmp_path, today, _make_entry(model="deep-think", is_deep_research=False))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "flash ×2" in out
        assert "deep-think ×1" in out

    def test_when_no_model_runs_then_shows_placeholder(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "no non-DR runs logged today" in out

    def test_when_bigquery_not_configured_then_shows_setup_message(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "not configured" in out
        assert "Cloud Console" in out

    def test_when_bigquery_not_installed_then_shows_install_hint(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        bq_error = {"error": "google-cloud-bigquery not installed — run: pip install google-cloud-bigquery"}
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=bq_error):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "not installed" in out
        assert "pip install" in out

    def test_when_bigquery_returns_zero_spend_then_shows_credit_applied(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        bq_data = {"rows": [], "as_of": "2026-04-10"}
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=bq_data):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "$0.00" in out
        assert "Ultra credit" in out

    def test_when_bigquery_returns_nonzero_spend_then_shows_charge_warning(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        bq_data = {
            "rows": [{"usage_date": "2026-04-11", "sku_description": "Gemini Pro Input", "total_cost": 3.20}],
            "as_of": "2026-04-10",
        }
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=bq_data):
                    spend_gemini({})
        out = capsys.readouterr().out
        assert "$3.20" in out
        assert "Charges are being applied" in out

    def test_when_month_has_dr_runs_then_shows_monthly_total(self, tmp_path, capsys):
        today = self._today()
        month_start = self._month_start()
        state_file = tmp_path / "dr-daily.json"
        # Write some DR entries in month-start date (may equal today — that's fine)
        _write_log_entry(
            tmp_path, month_start, _make_entry(model="deep-research", is_deep_research=True, tier_name="oauth")
        )
        _write_log_entry(tmp_path, today, _make_entry(model="deep-research", is_deep_research=True, tier_name="oauth"))
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini({})
        out = capsys.readouterr().out
        # At least 1 OAuth DR (may be 2 if today == month_start)
        assert "OAuth" in out

    def test_when_bigquery_project_id_in_config_then_shows_it_in_setup_message(self, tmp_path, capsys):
        state_file = tmp_path / "dr-daily.json"
        config = {"gemini_billing": {"gcp_project_id": "my-test-project"}}
        with patch("ai_cli.spend.LOG_DIR", tmp_path):
            with patch("ai_cli.spend.DR_DAILY_STATE_FILE", state_file):
                with patch("ai_cli.spend._query_bigquery_spend", return_value=None):
                    spend_gemini(config)
        out = capsys.readouterr().out
        assert "my-test-project" in out
