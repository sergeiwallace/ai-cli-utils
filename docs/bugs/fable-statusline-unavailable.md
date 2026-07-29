---
title: "AI-CLI-127: Fable statusline meter silently relabeled missing data"
category: bugs
tags: [bug, statusline, quota, fable]
status: fixed
severity: P1
task: AI-CLI-127
---

# AI-CLI-127: Fable Statusline Meter Silently Relabeled Missing Data

**Status:** fixed

**Severity:** P1

**Created:** 2026-07-29

**Task:** AI-CLI-127

## Symptoms

The Fable weekly statusline segment changed from a Fable percentage to `ccS -% →-%` after a
quota-week boundary. The new label suggested a Sonnet meter even though the secondary slot had
been tracking Fable. Before the boundary, a frozen Fable percentage still looked plausible
because it retained its normal percentage color and pace delta.

## Root Cause Analysis

Claude Code no longer exposes the per-model `Current week (Fable)` line that the secondary quota
scrape used. The scraper consequently records all-models snapshots with null Fable columns.

`_get_last_fable_snapshot()` intentionally limits its last-good lookup to the current quota week.
That is correct for a weekly quota value, but after a rollover with no successful Fable scrape it
returns no Fable row even when a frozen value remains in the prior week. The renderer treated this
absence as an unknown model and selected its legacy `ccS` fallback. The fallback originated as a
display for non-null historical rows that predated `weekly_model_name`; it is not a valid label for
missing Fable data.

The renderer also computed the Fable pace delta from a stale value. As the week elapsed, that
delta changed even though the Fable numerator did not, making a failed source look active.

## Fix

The statusline now renders these states distinctly:

| State | Rendered secondary segment |
| --- | --- |
| Fresh Fable snapshot (at most two hours old) | `ccF 2% →16%` |
| Stale Fable snapshot (more than two hours old) | `ccF 2% STALE` |
| No Fable snapshot in the current week | `ccF UNAVAILABLE` |

Stale and unavailable values are dimmed and omit the pace delta. A non-null legacy row without a
stored model name continues to use `ccS`; only a missing secondary value is identified as the
unavailable Fable meter.

## Regression Coverage

`tests/test_quota_fable_availability.py` constructs actual temporary SQLite quota databases and
calls `quota_statusline_part()` directly. It asserts the complete ANSI stdout for fresh, stale,
prior-week-only, and never-recorded Fable states. The prior-week-only and never-recorded states
produce the same explicit unavailable output because neither has a current-week Fable value.

## Verification

- `uv run pytest -n 0 tests/test_quota_fable_availability.py -q` — passed.
- `uv run pytest -n 0 tests/test_quota_fable_availability.py tests/test_quota_fable_scrape.py -q` — passed.
- `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` — passed.
- A direct `quota_statusline_part()` call against a temporary SQLite database rendered a fresh
  `ccF` percentage and pace delta.
- `uv run pytest -n 0 tests/test_quota_fable_availability.py tests/test_quota_fable_scrape.py tests/test_quota.py -q`
  — the Fable coverage passed; one unrelated existing scrape-spawn test failed because its lock-file
  parent directory is absent in this fresh sandbox.
