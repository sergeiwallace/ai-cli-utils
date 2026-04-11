"""Gemini usage and spend reporting.

Combines two data sources:
  - Local JSONL logs (~/.local/state/ai-cli/gemini-logs/) for run counts per model/tier
  - GCP BigQuery billing export for actual billed amounts on paid API runs

The BigQuery path is optional and requires one-time human setup (enable billing
export in Cloud Console). The command degrades gracefully when not configured.
"""

from __future__ import annotations

import json
import sys
import time

from .gemini import DEEP_RESEARCH_DAILY_LIMIT, DR_DAILY_STATE_FILE, LOG_DIR


def _parse_log_files(since_date: str, until_date: str | None = None) -> list[dict]:
    """Return all JSONL log entries whose file date is in [since_date, until_date].

    ``since_date`` and ``until_date`` are ``YYYY-MM-DD`` strings (inclusive).
    When ``until_date`` is None, no upper bound is applied.
    """
    entries: list[dict] = []
    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
        date_str = log_file.stem  # filename is "YYYY-MM-DD.jsonl"
        if date_str < since_date:
            continue
        if until_date and date_str > until_date:
            continue
        try:
            for line in log_file.read_text().splitlines():
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def _query_bigquery_spend(config: dict) -> dict | None:
    """Query GCP BigQuery billing export for Gemini API spend this month.

    Returns:
        None                        — billing not configured in config.toml
        {"error": "<msg>"}          — BigQuery library missing or query failed
        {"rows": [...], "as_of": "YYYY-MM-DD"}  — success

    Each row is a dict with keys: ``usage_date``, ``sku_description``, ``total_cost``.
    """
    billing_cfg = config.get("gemini_billing", {})
    project_id = billing_cfg.get("gcp_project_id", "")
    billing_account_id = billing_cfg.get("billing_account_id", "")
    export_table = billing_cfg.get("billing_export_table", "")

    if not all([project_id, billing_account_id, export_table]):
        return None

    try:
        from google.cloud import bigquery  # type: ignore[import]
    except ImportError:
        return {"error": "google-cloud-bigquery not installed — run: pip install google-cloud-bigquery"}

    try:
        client = bigquery.Client(project=project_id)
        query = f"""
            SELECT
                DATE(usage_start_time) AS usage_date,
                sku.description AS sku_description,
                SUM(cost) AS total_cost
            FROM `{export_table}`
            WHERE
                service.description = 'Gemini API'
                AND billing_account_id = '{billing_account_id}'
                AND DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
            GROUP BY usage_date, sku_description
            ORDER BY usage_date DESC, total_cost DESC
        """
        job = client.query(query)
        rows = [dict(row) for row in job.result()]
        return {"rows": rows, "as_of": time.strftime("%Y-%m-%d")}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def spend_gemini(config: dict) -> None:
    """Print a Gemini usage summary to stdout.

    Combines:
    - Today's deep-research OAuth/paid run counter (dr-daily.json)
    - Per-model run counts from today's JSONL log
    - Monthly DR totals from this month's JSONL logs
    - Paid API spend from GCP BigQuery billing export (when configured)
    """
    today = time.strftime("%Y-%m-%d")
    month_start = today[:7] + "-01"

    # --- Daily DR counter ---
    dr_counter: dict = {"date": today, "oauth_count": 0, "paid_count": 0}
    try:
        data = json.loads(DR_DAILY_STATE_FILE.read_text())
        if data.get("date") == today:
            dr_counter = data
    except Exception:
        pass

    oauth_count = dr_counter.get("oauth_count", 0)
    paid_count = dr_counter.get("paid_count", 0)
    oauth_remaining = max(0, DEEP_RESEARCH_DAILY_LIMIT - oauth_count)

    # --- JSONL logs ---
    today_entries = _parse_log_files(today, today)
    month_entries = _parse_log_files(month_start, today)

    # Per-model counts for non-DR runs today
    today_model_counts: dict[str, int] = {}
    for entry in today_entries:
        if not entry.get("is_deep_research", False):
            model = entry.get("model", "unknown")
            today_model_counts[model] = today_model_counts.get(model, 0) + 1

    # Monthly DR totals from logs (separate from daily counter — covers rollover days)
    month_oauth_dr = sum(1 for e in month_entries if e.get("is_deep_research") and e.get("tier_name") == "oauth")
    month_paid_dr = sum(
        1 for e in month_entries if e.get("is_deep_research") and e.get("tier_name") == "ai_studio_paid"
    )

    # --- Print today's summary ---
    print(f"Gemini usage — today ({today})")
    print(f"  Deep Research:  {oauth_count} OAuth runs ({oauth_remaining} remaining free)  |  {paid_count} paid runs")

    if today_model_counts:
        model_parts = "  •  ".join(f"{m} ×{n}" for m, n in sorted(today_model_counts.items()))
        print(f"  Other models:   {model_parts}")
    else:
        print("  Other models:   (no non-DR runs logged today)")

    # --- Print monthly summary ---
    print()
    print(f"This month ({today[:7]})")
    paid_label = f"{month_paid_dr} paid run" + ("s" if month_paid_dr != 1 else "")
    print(f"  Deep Research:  {month_oauth_dr} OAuth  •  {paid_label}")

    # --- BigQuery spend ---
    bq_data = _query_bigquery_spend(config)

    if bq_data is None:
        billing_cfg = config.get("gemini_billing", {})
        project_id = billing_cfg.get("gcp_project_id", "")
        print("  Paid API spend: not available — BigQuery billing export not configured.")
        print("    To enable: Cloud Console → Billing → Billing export → Detailed usage cost")
        print("    One-time setup (~5 min); data appears within 24-48h.")
        if project_id:
            print(f"    GCP project: {project_id}")
    elif "error" in bq_data:
        err = bq_data["error"]
        if "not installed" in err:
            print("  Paid API spend: not available — google-cloud-bigquery not installed.")
            print("    Install: pip install google-cloud-bigquery")
        else:
            print(f"  Paid API spend: query error — {err}", file=sys.stderr)
    else:
        rows = bq_data.get("rows", [])
        as_of = bq_data.get("as_of", "unknown")
        total_cost = sum(float(r.get("total_cost", 0) or 0) for r in rows)

        if total_cost == 0.0:
            print(f"  Paid API spend: $0.00  (source: GCP billing export, as of {as_of})")
            print("    → Ultra credit appears to be applied ✓")
        else:
            print(f"  Paid API spend: ${total_cost:.2f}  (source: GCP billing export, as of {as_of})")
            print("    → Charges are being applied — Ultra credit may not cover AI Studio API keys")

        # Print SKU breakdown to help with model→SKU mapping on first use
        if rows:
            print("\n  SKU breakdown:")
            for row in rows[:10]:
                cost = float(row.get("total_cost", 0) or 0)
                print(f"    {row.get('usage_date')}  {row.get('sku_description', 'unknown')}  ${cost:.4f}")
