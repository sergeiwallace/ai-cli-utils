"""Gemini API usage and cost reporting for `ai spend gemini`.

Combines two data sources:
  - Local JSONL logs (~/.local/state/ai-cli/gemini-logs/) — OAuth and free-tier
    run counts, per-model stats, and token usage.
  - GCP BigQuery billing export — actual billed amounts for ai_studio_paid runs.

BigQuery access requires:
  - google-cloud-bigquery installed (``pip install google-cloud-bigquery``)
  - GCP billing export enabled (Cloud Console → Billing → Billing export →
    Detailed usage cost)
  - ``[gemini_billing]`` config section with ``gcp_project_id`` and
    ``billing_export_table``

Usage::

    ai spend gemini
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

LOG_DIR = Path.home() / ".local" / "state" / "ai-cli" / "gemini-logs"
DR_DAILY_FILE = Path.home() / ".local" / "state" / "ai-cli" / "dr-daily.json"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DailyStats:
    """Aggregated usage stats for a single day (from JSONL logs)."""

    date: str = ""
    total_runs: int = 0
    successful_runs: int = 0
    by_model: dict = field(default_factory=dict)  # model -> success count
    by_tier: dict = field(default_factory=dict)  # tier_name -> success count
    deep_research_oauth: int = 0
    deep_research_paid: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


# ---------------------------------------------------------------------------
# JSONL log parsing
# ---------------------------------------------------------------------------


def _read_jsonl_file(log_file: Path) -> list[dict]:
    """Parse a JSONL log file, returning all valid entries. Silently skips malformed lines."""
    entries: list[dict] = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return entries


def get_daily_stats(date_str: str, log_dir: Path | None = None) -> DailyStats:
    """Aggregate usage stats for a given date (YYYY-MM-DD) from the JSONL log."""
    stats = DailyStats(date=date_str)
    log_file = (log_dir or LOG_DIR) / f"{date_str}.jsonl"
    if not log_file.exists():
        return stats

    for entry in _read_jsonl_file(log_file):
        stats.total_runs += 1
        if not entry.get("success"):
            continue
        stats.successful_runs += 1

        model = entry.get("model") or "unknown"
        stats.by_model[model] = stats.by_model.get(model, 0) + 1

        tier_name = entry.get("tier_name") or "unknown"
        stats.by_tier[tier_name] = stats.by_tier.get(tier_name, 0) + 1

        if entry.get("is_deep_research"):
            if tier_name == "oauth":
                stats.deep_research_oauth += 1
            elif tier_name == "ai_studio_paid":
                stats.deep_research_paid += 1

        # Accumulate tokens where available; preserve None when no entry has data
        for fname in ("input_tokens", "output_tokens", "total_tokens"):
            val = entry.get(fname)
            if val is not None:
                current = getattr(stats, fname)
                setattr(stats, fname, (current or 0) + int(val))

    return stats


def get_monthly_stats(year_month: str, log_dir: Path | None = None) -> DailyStats:
    """Aggregate usage stats for a calendar month (YYYY-MM) across all daily log files."""
    combined = DailyStats(date=year_month)
    resolved_dir = log_dir or LOG_DIR
    if not resolved_dir.exists():
        return combined

    for log_file in sorted(resolved_dir.glob(f"{year_month}-*.jsonl")):
        day = get_daily_stats(log_file.stem, log_dir=resolved_dir)
        combined.total_runs += day.total_runs
        combined.successful_runs += day.successful_runs
        combined.deep_research_oauth += day.deep_research_oauth
        combined.deep_research_paid += day.deep_research_paid
        for model, count in day.by_model.items():
            combined.by_model[model] = combined.by_model.get(model, 0) + count
        for tier, count in day.by_tier.items():
            combined.by_tier[tier] = combined.by_tier.get(tier, 0) + count
        for fname in ("input_tokens", "output_tokens", "total_tokens"):
            val = getattr(day, fname)
            if val is not None:
                current = getattr(combined, fname)
                setattr(combined, fname, (current or 0) + val)

    return combined


# ---------------------------------------------------------------------------
# GCP BigQuery billing query
# ---------------------------------------------------------------------------


def query_bigquery_spend(
    gcp_project_id: str,
    billing_export_table: str,
    year_month: str,
) -> dict:
    """Query the GCP BigQuery billing export for Gemini API spend for a given month.

    Args:
        gcp_project_id: GCP project that owns the BigQuery dataset.
        billing_export_table: Fully-qualified BigQuery table ID
            (e.g. ``my-project.billing_export.gcp_billing_export_v1_XXXXX``).
        year_month: Calendar month to query in YYYY-MM format.

    Returns a dict with these keys:

    - ``available`` (bool): True when data was successfully retrieved.
    - ``total_cost_usd`` (float): Total billed amount for the month.
    - ``by_sku`` (dict): ``{sku_description: cost_usd}`` breakdown.
    - ``data_as_of`` (str | None): Latest usage_start_time date in the result.
    - ``error`` (str | None): Human-readable error when ``available=False``.
    """
    if not billing_export_table:
        return {
            "available": False,
            "error": "billing_export_table not configured in [gemini_billing] config",
        }

    try:
        from google.cloud import bigquery  # type: ignore[import]
    except ImportError:
        return {
            "available": False,
            "error": "google-cloud-bigquery not installed (pip install google-cloud-bigquery)",
        }

    try:
        year, month = year_month.split("-")
        client = bigquery.Client(project=gcp_project_id)
        query = f"""
            SELECT
              sku.description AS sku_description,
              SUM(cost) AS total_cost,
              MAX(DATE(usage_start_time)) AS data_as_of
            FROM `{billing_export_table}`
            WHERE
              service.description = 'Gemini API'
              AND EXTRACT(YEAR FROM usage_start_time) = {year}
              AND EXTRACT(MONTH FROM usage_start_time) = {month}
            GROUP BY sku.description
            ORDER BY total_cost DESC
        """
        rows = list(client.query(query).result())
    except Exception as exc:
        return {"available": False, "error": f"BigQuery query failed: {exc}"}

    by_sku: dict[str, float] = {}
    total_cost = 0.0
    data_as_of: str | None = None
    for row in rows:
        by_sku[row.sku_description] = float(row.total_cost)
        total_cost += float(row.total_cost)
        row_date = str(row.data_as_of)
        if data_as_of is None or row_date > data_as_of:
            data_as_of = row_date

    return {
        "available": True,
        "total_cost_usd": round(total_cost, 4),
        "by_sku": by_sku,
        "data_as_of": data_as_of,
        "error": None,
    }


# ---------------------------------------------------------------------------
# DR daily counter reader (mirrors gemini._read_dr_daily without import cycle)
# ---------------------------------------------------------------------------


def _read_dr_daily_counter(dr_file: Path | None = None) -> dict:
    """Read today's DR run counter from dr-daily.json.

    Returns a zeroed dict if the file is absent or from a different day.
    """
    today = time.strftime("%Y-%m-%d")
    path = dr_file or DR_DAILY_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "oauth_count": 0, "paid_count": 0, "last_run": None}


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def cmd_spend_gemini(
    config: dict,
    *,
    log_dir: Path | None = None,
    dr_file: Path | None = None,
) -> int:
    """Print Gemini usage and cost summary.

    Args:
        config: Loaded config dict (from load_config()).
        log_dir: Override for LOG_DIR (used in tests).
        dr_file: Override for DR_DAILY_FILE (used in tests).

    Returns 0 on success.
    """
    today = time.strftime("%Y-%m-%d")
    year_month = today[:7]

    dr_today = _read_dr_daily_counter(dr_file)
    daily = get_daily_stats(today, log_dir=log_dir)
    monthly = get_monthly_stats(year_month, log_dir=log_dir)

    # --- Today ---
    print(f"Gemini usage — today ({today})")

    paid_dr = dr_today.get("paid_count", 0)

    if paid_dr:
        print(
            f"  Deep Research:  {paid_dr} paid run{'s' if paid_dr != 1 else ''} (check `ai spend gemini` for charges)"
        )
    else:
        print("  Deep Research:  0 paid runs today")

    _sep = " \u00b7 "  # middle dot separator
    _times = "\u00d7"  # multiplication sign
    other_models = {m: c for m, c in daily.by_model.items() if m != "deep-research"}
    if other_models:
        model_parts = [f"{m} {_times}{c}" for m, c in sorted(other_models.items(), key=lambda x: -x[1])]
        print(f"  Other models:   {_sep.join(model_parts)}")
    elif not paid_dr and not daily.successful_runs:
        print("  No runs logged today.")

    print()

    # --- This month ---
    print(f"This month ({year_month})")

    m_oauth_dr = monthly.deep_research_oauth
    m_paid_dr = monthly.deep_research_paid
    _bullet = "\u2022"
    dr_monthly = f"{m_oauth_dr} OAuth"
    if m_paid_dr:
        plural = "s" if m_paid_dr != 1 else ""
        dr_monthly += f"  {_bullet}  {m_paid_dr} paid run{plural}"
    print(f"  Deep Research:  {dr_monthly}")

    m_other = {m: c for m, c in monthly.by_model.items() if m != "deep-research"}
    if m_other:
        m_parts = [f"{m} {_times}{c}" for m, c in sorted(m_other.items(), key=lambda x: -x[1])]
        print(f"  Other models:   {_sep.join(m_parts)}")

    # --- BigQuery paid spend ---
    billing_cfg = config.get("gemini_billing", {})
    gcp_project = billing_cfg.get("gcp_project_id", "")
    export_table = billing_cfg.get("billing_export_table", "")

    if not export_table:
        print("  Paid API spend: not available — BigQuery billing export not configured.")
        if gcp_project:
            print(f"    GCP project: {gcp_project}")
        print("    To enable: Cloud Console → Billing → Billing export → Detailed usage cost")
        print("    One-time setup (~5 min); data appears within 24-48h.")
    else:
        bq = query_bigquery_spend(gcp_project, export_table, year_month)
        if not bq["available"]:
            err = bq.get("error") or "unknown error"
            if "not installed" in err:
                print(f"  Paid API spend: not available — {err}")
            else:
                print(f"  Paid API spend: query failed — {err}")
        else:
            total = bq["total_cost_usd"]
            as_of = bq.get("data_as_of") or "unknown"
            print(f"  Paid API spend: ${total:.2f}  (source: GCP billing export, as of {as_of})")
            if total == 0.0:
                print("    \u2192 Ultra credit appears to be applied \u2713")
            else:
                print("    \u2192 Charges are being applied \u2014 Ultra credit may not cover AI Studio API keys")
            if bq.get("by_sku"):
                for sku, cost in sorted(bq["by_sku"].items(), key=lambda x: -x[1]):
                    print(f"    {sku}: ${cost:.4f}")

    return 0
