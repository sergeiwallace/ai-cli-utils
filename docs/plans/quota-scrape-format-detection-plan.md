---
title: "AI-CLI-68: Quota Scrape Format-Change Detection — Plan"
category: plan
tags: [quota, scrape, telemetry, statusline, db]
status: active
source: claude-sonnet-4-6
task_id: AI-CLI-68
created: 2026-04-27
---

# AI-CLI-68: Quota Scrape Format-Change Detection

## Problem

`_scrape_usage_hidden_pane()` scrapes CC's hidden `/usage` pane. If Anthropic changes the output format, `_parse_usage_output()` silently returns `None` — the statusline goes blank with no indication of whether data is unavailable or the scraper is broken. Developers only notice if they manually inspect `ai quota status`.

## Signal

The format-change condition is precise: `"% used"` appears in the captured output (so CC rendered the dialog) **and** `_parse_usage_output()` returns `None` (so the regex failed). This is distinct from startup timeouts (no output at all) and from quota being genuinely unreported by CC.

## Design

### Option A — In-band DB flag + debug dump (recommended)

**How it works:**
1. In `_scrape_usage_hidden_pane()`, after each poll iteration where `"% used"` is in output but parse returns `None`, dump raw capture to a debug file and increment a DB counter.
2. `quota_statusline_part()` reads the counter; shows `📊 ⚠` instead of quota data when the flag is active.
3. `ai quota status` / `ai quota scrape` print the debug file path when the flag is set.
4. Counter and timestamp clear automatically on the next successful parse.

**Pros:** No external dependencies. Debug file immediately actionable — developer opens it, sees the new CC format, fixes the regex. Statusline ⚠ is hard to miss. Auto-clears.

**Cons:** Adds a DB table and two new DB functions. Slightly increases scrape path complexity.

### Option B — Log-only (no statusline signal)

Write debug file only; no DB state; no statusline indicator.

**Pros:** Simpler.
**Cons:** Silent — developer won't notice until they manually check the debug file. Defeats the purpose.

**Recommendation: Option A.** The statusline ⚠ is the reason for this feature — silent logging misses the goal.

> **Feedback:**

---

## Implementation Tasks

### T-01 — Add `quota_meta` table to DB schema

File: `src/ai_cli/quota_db.py`

Add to `_init_db()` executescript:
```sql
CREATE TABLE IF NOT EXISTS quota_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Add migration in `_migrate_snapshot_columns()` or a new `_migrate_quota_meta()` function (existing DBs use `CREATE TABLE IF NOT EXISTS` so no ALTER needed — this is a new table).

Add two helpers:
```python
def _set_quota_meta(key: str, value: str) -> None: ...
def _get_quota_meta(key: str) -> str | None: ...
```

Keys used:
- `scrape_format_mismatch_count` — integer string, incremented on each mismatch, reset to `"0"` on success
- `scrape_format_mismatch_at` — ISO UTC timestamp of most recent mismatch

### T-02 — Detect format mismatch in `_scrape_usage_hidden_pane()`

File: `src/ai_cli/quota.py`

In the polling loop (lines 361–372), after `"% used"` is confirmed in output but `_parse_usage_output()` returns `None`:

```python
if cap.returncode == 0 and "% used" in cap.stdout:
    snapshot = _parse_usage_output(cap.stdout)
    if snapshot:
        _clear_scrape_format_mismatch()   # new helper: resets count to 0
        break
    else:
        _record_scrape_format_mismatch(cap.stdout)  # new helper: dump + increment
```

`_record_scrape_format_mismatch(raw: str)`:
1. Write `raw` to `~/.local/state/ai-cli-utils/quota-scrape-debug.txt` (overwrite — always the most recent failure)
2. Call `_set_quota_meta("scrape_format_mismatch_at", utcnow_iso())`
3. Read current count, increment, write back

`_clear_scrape_format_mismatch()`:
1. `_set_quota_meta("scrape_format_mismatch_count", "0")`

### T-03 — Surface ⚠ in statusline

File: `src/ai_cli/quota.py` — `quota_statusline_part()`

Before reading the latest snapshot, check:
```python
mismatch_count = _get_quota_meta("scrape_format_mismatch_count")
if mismatch_count and int(mismatch_count) > 0:
    sys.stdout.write("📊 ⚠")
    return 0
```

Only show ⚠ if `mismatch_count > 0`; the check is a fast DB read, no scraping.

### T-04 — Print debug path in CLI commands

File: `src/ai_cli/quota.py` — `cmd_quota_status()` and `cmd_quota_scrape()`

After displaying normal output, check mismatch flag. If set:
```text
⚠  Scrape parse failure — CC /usage format may have changed.
   Raw output saved to: ~/.local/state/ai-cli-utils/quota-scrape-debug.txt
   Last failure: <scrape_format_mismatch_at>
```

### T-05 — Fix `_parse_reset_datetime` day-of-week ambiguity (old CC format)

File: `src/ai_cli/quota.py` — `_parse_reset_datetime()` (line 81)

Old CC format (`"Resets 6:59am"`) has no date, only a time. Current code assumes the next occurrence of that weekday — but if the scrape runs after that time on the same day, it picks the wrong week. Fix:

- If parsed time is in the past (relative to `utcnow()`), advance by one week before returning.
- Add a comment: this only affects CC versions pre-dating the IANA format.

### T-06 — Tests

New test file: `tests/test_quota_format_detection.py` (or extend `tests/test_quota.py`)

ACs to cover:
1. `_record_scrape_format_mismatch()` writes to debug file and increments DB counter
2. `_clear_scrape_format_mismatch()` resets counter to 0
3. `quota_statusline_part()` returns `"📊 ⚠"` when mismatch count > 0
4. `quota_statusline_part()` returns normal output when mismatch count is 0
5. `_parse_reset_datetime()` returns correct week start when time is past (old CC format)

---

## Files to Modify

| File | Change |
| --- | --- |
| `src/ai_cli/quota_db.py` | Add `quota_meta` table + `_set_quota_meta` / `_get_quota_meta` |
| `src/ai_cli/quota.py` | `_scrape_usage_hidden_pane()` mismatch detection; `quota_statusline_part()` ⚠; CLI output; `_parse_reset_datetime` fix |
| `tests/test_quota_format_detection.py` | New test file covering T-01 through T-05 ACs |

---

## Acceptance Criteria

- [ ] When `"% used"` is in scrape output but parse fails: raw text written to `quota-scrape-debug.txt`, `scrape_format_mismatch_count` incremented, `scrape_format_mismatch_at` updated
- [ ] `quota_statusline_part()` outputs `📊 ⚠` when `scrape_format_mismatch_count > 0`
- [ ] `ai quota status` and `ai quota scrape` print the debug file path and timestamp when mismatch flag is active
- [ ] Mismatch flag auto-clears (`count = 0`) on the next successful parse
- [ ] `_parse_reset_datetime()` correctly handles past-time old CC format without week drift
- [ ] All new DB writes use `_set_quota_meta` / `_get_quota_meta`; no raw SQL in quota.py
- [ ] Full test suite passes (`pytest`)

---

## Approval Log

| Date | Round | Decision |
| --- | --- | --- |
| 2026-04-27 | 1 | Design proposed by Claude (Option A); plan doc created |
