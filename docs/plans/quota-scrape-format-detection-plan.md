---
title: "AI-CLI-68: Quota Scrape Format-Change Detection — Plan"
category: plan
tags: [quota, scrape, telemetry, statusline, db]
status: active
source: claude-sonnet-4-6
task_id: AI-CLI-68
created: 2026-04-27
template_version: "plan-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# AI-CLI-68: Quota Scrape Format-Change Detection

## Problem

`_scrape_usage_hidden_pane()` scrapes CC's hidden `/usage` pane. If Anthropic changes the output format, `_parse_usage_output()` silently returns `None` — the statusline goes blank with no indication of whether data is unavailable or the scraper is broken. Developers only notice if they manually inspect `ai quota status`.

## Signal

The format-change condition is precise: `"% used"` appears in the captured output (so CC rendered the dialog) **and** `_parse_usage_output()` returns `None` (so the regex failed). This is distinct from startup timeouts (no output at all) and from quota being genuinely unreported by CC.

---

## Decision Summary

<!-- Recommendation-vs-choice tracking (AIH-148): track the AI recommendation and the human
  choice in SEPARATE columns so preference-divergence is queryable, not buried in prose.
  - Recommended (AI): the AI's pick. If the rec was CORRECTED mid-discussion, put the final pick
  here and KEEP the original recommendation + its reasoning in Rationale (or the detail) — never
  silently overwrite it; the correction is signal.
  - Chosen: the human's final pick. Fill when decided.
  - Diverged?: `Yes` if Chosen != Recommended (final), else `No`. On `Yes`, Rationale MUST state
  WHY the human chose differently — that "why" is the highest-value datapoint.
  Full rules: ai-harness docs/procedures/decision-framework.md (Decision Summary tracking). -->

| # | Decision | Options Considered | Status |
| --- | --- | --- | --- |
| D1 | Scraper detection approach | (A) DB flag + debug dump + statusline indicator, (B) log-only | `APPROVED: (A)` |
| D2 | Broken statusline indicator style | (A) red bg banner, (B) bold red text, (C) red banner + hint, (D) emoji-only | `PENDING` |

<!-- DECISION FORMATTING (AIH-114) — applies when filling in REAL option content below:
  each option's Pros and Cons must be BULLETED lists, and `**Pros:**` / `**Cons:**` must be
  each on its own line — a blank line before each header, and a hard newline between the header
  and its bullet list — otherwise PDF export collapses them onto one line. The placeholder
  skeleton below already shows the correct shape; match it exactly. -->

---

## Decision Details

### D1: Scraper Detection Approach — `APPROVED: (A)`

#### (A) In-band DB flag + debug dump + statusline indicator

**How it works:**

1. In `_scrape_usage_hidden_pane()`, after each poll iteration where `"% used"` is in output but parse returns `None`, dump raw capture to a debug file and increment a DB counter.
2. `quota_statusline_part()` reads the counter; shows a highly visible broken-scraper indicator (see D2) instead of quota data when the flag is active.
3. `ai quota status` / `ai quota scrape` print the debug file path when the flag is set.
4. Counter and timestamp clear automatically on the next successful parse.

**Pros:**

- No external dependencies
- Debug file immediately actionable — developer opens it, sees the new CC format, fixes the regex
- Statusline indicator is impossible to miss (see D2)
- Auto-clears on next successful parse

**Cons:**

- Adds a DB table and two new DB functions
- Slightly increases scrape path complexity

#### (B) Log-only (no statusline signal)

Write debug file only; no DB state; no statusline indicator.

**Pros:** Simpler.

**Cons:** Silent — developer won't notice until they manually check the debug file. Defeats the purpose.

##### Recommendation

> **Decision:** `APPROVED — (A) In-band DB flag + debug dump + statusline indicator`
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

Option (A). The statusline indicator is the reason for this feature — silent logging misses the goal entirely.

---

### D2: Broken Statusline Indicator Style — `APPROVED`

The indicator prepends to the left of the existing quota output when `scrape_format_mismatch_count > 0`. It must be immediately obvious that something is broken. Quota data remains visible to the right in case the scrape is only partially broken.

**Format:** `🚨 BROKEN 🚨` + space, prepended to the normal quota statusline output.

Example statusline when mismatch is active:

```text
🚨 BROKEN 🚨 📊 Week 42% →8% ✅  |  Son 87% →X%
```

**Constraints:**

- No ANSI escape codes — emoji-only, no `\033[...]m` sequences
- Siren emojis on both sides of `BROKEN`
- Shorter label — just `BROKEN`, not `SCRAPER BROKEN`
- Prepended, not replacing — quota items stay to the right so they remain visible if the scrape is partially working
- In narrow terminal windows the quota items get pushed to the right (or off-screen), which is acceptable — the warning takes priority

#### Recommendation

> **Decision:** `APPROVED`
<!-- decision-record: chosen-option=N/A; ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

No ANSI codes. Siren prefix `🚨 BROKEN 🚨` prepended to the left of normal quota output. Quota data preserved to the right. Consistent with how other statusline prefixes work in this codebase.

---

> **Feedback Round 1:**
> - D1: approved — Option A
> - D2: user wants a large, highly visible indicator (red text / "SCRAPER BROKEN" / broken emojis). Options (A)–(D) presented above for selection.

---

> **Feedback Round 2:**
> - D2: no ANSI escape codes (`\033[...]m`); siren emoji on both sides; shorter — just `BROKEN`; prepend to the left of existing quota output (don't replace it); quota items remain visible to the right.

---

> **AI Response Round 2:**
> - D2 approved: `🚨 BROKEN 🚨` prefix, emoji-only, prepended before normal quota output. T-03 updated to prepend rather than replace. ACs updated accordingly. Both D1 and D2 now approved — ready to implement.

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

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

### T-03 — Surface broken-scraper indicator in statusline

File: `src/ai_cli/quota.py` — `quota_statusline_part()`

Before writing the normal quota output, check the mismatch flag. If set, prepend the indicator prefix so it appears to the left of the quota items:

```python
_SCRAPER_BROKEN_PREFIX = "🚨 BROKEN 🚨 "

mismatch_count = _get_quota_meta("scrape_format_mismatch_count")
if mismatch_count and int(mismatch_count) > 0:
    sys.stdout.write(_SCRAPER_BROKEN_PREFIX)
# continue to write normal quota output
```

The prefix is prepended; quota data writes after it on the same line. In narrow windows the quota items shift right or off-screen — the warning still appears. Only show when `mismatch_count > 0`; check is a fast DB read with no scraping.

### T-04 — Print debug path in CLI commands

File: `src/ai_cli/quota.py` — `cmd_quota_status()` and `cmd_quota_scrape()`

After displaying normal output, check mismatch flag. If set, print:

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
3. `quota_statusline_part()` outputs `_SCRAPER_BROKEN_INDICATOR` when mismatch count > 0
4. `quota_statusline_part()` returns normal output when mismatch count is 0
5. `_parse_reset_datetime()` returns correct week start when time is past (old CC format)

---

## Files to Modify

| File | Change |
| --- | --- |
| `src/ai_cli/quota_db.py` | Add `quota_meta` table + `_set_quota_meta` / `_get_quota_meta` |
| `src/ai_cli/quota.py` | `_scrape_usage_hidden_pane()` mismatch detection; `quota_statusline_part()` D2 indicator; CLI output; `_parse_reset_datetime` fix |
| `tests/test_quota_format_detection.py` | New test file covering T-01 through T-05 ACs |

---

## Acceptance Criteria

- [ ] When `"% used"` is in scrape output but parse fails: raw text written to `quota-scrape-debug.txt`, `scrape_format_mismatch_count` incremented, `scrape_format_mismatch_at` updated
- [ ] `quota_statusline_part()` prepends `🚨 BROKEN 🚨` to the left of normal quota output when `scrape_format_mismatch_count > 0`
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
| 2026-04-29 | 2 | D1 approved (Option A); D2 options presented for user selection; plan restructured with D1/D2 format |
| 2026-04-29 | 3 | D2 approved: emoji-only `🚨 BROKEN 🚨` prefix, no ANSI codes, prepended (not replacing) quota output |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="approval_log" -->
