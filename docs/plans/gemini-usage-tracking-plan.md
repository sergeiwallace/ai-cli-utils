---
title: "Gemini API Cost and Usage Tracking Overhaul"
category: plans
tags: [ai-gemini, usage-tracking, billing, deep-research, quota, ai-cli-41]
status: DRAFT
source: internal
---

# Gemini API Cost and Usage Tracking Overhaul — Implementation Plan

**Status:** DRAFT

**Created:** 2026-04-10
**Revised:** 2026-04-11

**Task:** `AI-CLI-41`
**Related:** SW-767 (sergei — track token usage per research run)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

- [Overview](#overview)
- [Background and Context](#background-and-context)
- [Auth Tier Naming](#auth-tier-naming)
- [Options](#options)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Surface Gemini API usage and cost data automatically, replacing the brittle manual
MEMORY.md ledger. Key deliverables: (1) fix token count extraction so per-run data is
actually logged; (2) a daily Deep Research OAuth run counter with real-time stderr
feedback per run; (3) an `ai spend gemini` command that pulls actual billed amounts from
the GCP BigQuery billing export for paid runs and shows OAuth run counts from local logs.

> **Feedback Round 1:** Is the scope right? Anything to add or cut?
>
> - \<enter feedback here>

## Background and Context

### Current JSONL Log Schema (audited 2026-04-11)

`~/.local/state/ai-cli/gemini-logs/YYYY-MM-DD.jsonl` already exists with one entry per
run. The schema is already richer than originally planned — most fields exist. The gaps
are:

| Field | Status | Note |
|-------|--------|------|
| `ts` | ✅ present | ISO timestamp with offset |
| `model` | ✅ present | alias used (e.g. `flash`, `deep-research`) |
| `tier` | ✅ present | int, but naming is inconsistent — see below |
| `tier_name` | ✅ present | e.g. `"gemini-cli (OAuth)"` — to be renamed |
| `success` | ✅ present | bool |
| `error` | ✅ present | string or null |
| `duration_ms` | ✅ present | int |
| `input_tokens` | ⚠️ present but always 0 | extraction bug — see T-01 |
| `output_tokens` | ⚠️ present but always 0 | extraction bug — see T-01 |
| `total_tokens` | ⚠️ present but always 0 | extraction bug — see T-01 |
| `prompt_chars` | ✅ present | int |
| `response_chars` | ✅ present | int |
| `output_path` | ✅ present | string or null |
| `attempts` | ✅ present | list of per-tier attempt logs |
| `is_deep_research` | ❌ missing | bool — to be added |
| `estimated_cost_usd` | ❌ missing | deferred — use BigQuery for paid spend instead |

**Token extraction bug:** `_call_rest_api()` correctly reads `response.usage_metadata`
(lines 438–443 of `gemini.py`), but live logs confirm all token fields are `0`. Likely
cause: the REST API response for these models doesn't populate `usage_metadata`, or the
SDK version returns it under a different attribute. T-01 investigates and fixes this.

**Deep-research tier naming inconsistency:** `_run_deep_research()` currently logs
`tier=2, tier_name="Interactions API"`. This conflicts with the 3-tier system (tier 2 =
free API key) and doesn't reflect whether OAuth or paid key was used. T-01 fixes this
alongside the tier naming overhaul.

### Why the Deep Research Counter Matters

OAuth deep-research runs are free under the Google AI Ultra consumer subscription
(~20 runs/day). Paid runs (`ai_studio_paid`) cost ~$2–5 each from the billing account.
Without a counter, a session config session has no visibility into how many free runs
remain before the daily limit is hit and billing kicks in.

### Auth Infrastructure (as of 2026-04-11)

`_get_google_oauth_token()` was added in the previous session — OAuth is now tried first
for deep-research and falls back to the paid API key if unavailable. The 3-tier REST API
fallback chain is already implemented and working.

### GCP Billing Export Research (2026-04-11)

Key finding: **GCP has no direct billing query API.** The Cloud Billing API manages
account links; it does not return cost data. The only path to actual billed amounts is
BigQuery billing export, queried via `google-cloud-bigquery`.

- Gemini API usage (AI Studio key) appears under service `generativelanguage.googleapis.com`,
  displayed as `"Gemini API"` in billing.
- Granularity: daily per-model SKU (not per-request). Data is delayed 24–48h.
- BigQuery billing export is **not currently configured** on the user's GCP projects.
  One-time setup required (~5 min in Cloud Console) before T-03 paid spend queries work.
- Google AI Studio has no public spend API — web UI only.
- OAuth runs (free via consumer subscription) will **not appear** in the billing export
  at all (they are not billed). These are tracked via local JSONL logs only.

### AI-CLI-25 Status

The ai-cli-utils code changes from AI-CLI-25 are complete: `quota_sync_from_remote()`
exists, Slack webhook sending is implemented. The remaining items are human actions
(creating the Slack webhook URL, storing in Doppler) and humanware-project work — they
do not block AI-CLI-41.

## Auth Tier Naming

The old `tier 1/2/3` numbering conflates an internal fallback-chain concept with
Google's own billing terminology and is not useful for communicating with humans or
in log output. Replacing throughout with Google-aligned names:

| New name | Old label | What it is |
|----------|-----------|-----------|
| `oauth` | "gemini-cli (OAuth)" / tier 1 | Gemini CLI OAuth via Google AI Ultra consumer subscription — free, ~20 DR runs/day |
| `ai_studio_free` | "API free-tier" / tier 2 | Google AI Studio free-tier API key — free for Flash/Gemma only |
| `ai_studio_paid` | "API paid" / tier 3 | Google AI Studio paid API key linked to billing account — billed |

These names are used in:
- `TIER_NAMES` dict in `gemini.py`
- `tier_name` field in JSONL log entries
- Stderr output (`[deep-research] OAuth runs today: N/20`)
- `ai spend gemini` display
- Session config and doc references (replace all "tier 3" references with "AI Studio paid")

**In-code tier integers (1/2/3) are kept** for fallback-chain ordering logic only and
are not user-facing.

## Options

### Option A: Token-count calculation only (no BigQuery)

Extract token counts from API responses, multiply by published per-token pricing rates
hardcoded into the tool. No external dependencies beyond existing ones.

**Pros:**
- No setup required — works immediately
- Real-time (not delayed like BigQuery)

**Cons:**
- We're back to calculating spend ourselves, not reading actual billed amounts
- Published rates and billing reality diverge (credits, discounts, rounding)
- Per-token prices change; the tool falls out of sync
- OAuth runs have no token data from the subprocess path

### Option B: BigQuery billing export for paid spend

Enable GCP BigQuery billing export (one-time human setup, ~5 min). Python tool queries
`google-cloud-bigquery` to get actual billed amounts for `ai_studio_paid` runs. OAuth and
`ai_studio_free` runs tracked via JSONL logs only (they are not billed).

**Pros:**
- Actual billed amounts — what Google charged, not what we calculated
- Per-model SKU breakdown available
- `google-cloud-bigquery` is the standard, well-supported client; ADC auth works

**Cons:**
- One-time human setup required before paid-spend data is available
- Data is delayed 24–48h — "today's spend" shows yesterday's data
- Per-request granularity is not available (hourly SKU aggregates only)
- Exact `sku.description` strings per model need to be mapped empirically (first run)

### Option C: Hybrid — BigQuery for paid, JSONL for OAuth

Use BigQuery for `ai_studio_paid` actual billed amounts; use JSONL logs for OAuth and
free-tier run counts. `ai spend gemini` combines both sources. Graceful degradation
when BigQuery is not set up (shows "not configured" with setup instructions instead of
crashing).

**Pros:**
- Best of both: actual billing data + free-run tracking
- No paid-run cost calculation needed — ground truth from Google

**Cons:**
- Two data sources to integrate in T-03
- Requires BigQuery setup gate (human action)

### Recommendation

**Option C (hybrid).** The only honest way to know what Google actually charged is to
read what they actually charged. Option A (calculate ourselves) defeats the purpose of
this feature. Option C adds one human gate (BigQuery setup) but delivers accurate paid
spend alongside the free OAuth run counter. T-03 detects missing BigQuery config and
guides the user through setup rather than failing silently.

## Task Breakdown

### T-01: Fix token extraction + log schema enrichment

**Size:** S
**Batch:** 1

Debug why `input_tokens`, `output_tokens`, `total_tokens` are always `0` in JSONL logs
even though `_call_rest_api()` reads `response.usage_metadata`. Likely culprits: the
`gemini-3-flash-preview` model doesn't populate `usage_metadata`, the SDK attribute name
differs from `prompt_token_count`/`candidates_token_count`, or `usage_metadata` is `None`
for some responses. Fix extraction and add a fallback log if the field is absent.

Also as part of this task:
- Add `is_deep_research: bool` to each log entry (True when `model == "deep-research"`
  or the Interactions API path was used)
- Update `TIER_NAMES` dict to new naming system: `{1: "oauth", 2: "ai_studio_free", 3: "ai_studio_paid"}`
- Fix `_run_deep_research()` to log the correct tier name (`"oauth"` if OAuth token was
  obtained, `"ai_studio_paid"` if paid key was used) instead of `"Interactions API"`

**Deliverables:**

- `src/ai_cli/gemini.py` — fix token extraction, add `is_deep_research`, update `TIER_NAMES`, fix deep-research log tier

**Acceptance criteria:**

- [ ] After a `ai gemini -m flash` REST API run, log entry has non-zero `input_tokens` and `output_tokens` (or `null` with a warning if the model doesn't return them)
- [ ] After a `ai gemini -m deep-research` run, log entry has `is_deep_research: true`
- [ ] Deep-research OAuth run logs `tier_name: "oauth"`; paid key run logs `tier_name: "ai_studio_paid"`
- [ ] Flash OAuth run (tier 1, subprocess) logs `tier_name: "oauth"`
- [ ] `TIER_NAMES` dict uses new naming system in all log entries going forward

**Dependencies:** None

---

### T-02: Deep Research daily OAuth run counter

**Size:** S
**Batch:** 1

Write/increment `~/.local/state/ai-cli/dr-daily.json` on each successful deep-research
run completion. Reset on date rollover. Print per-run status to stderr after each run.

**Schema:**

```json
{
  "date": "2026-04-11",
  "oauth_count": 3,
  "paid_count": 1,
  "last_run": "2026-04-11T18:30:00"
}
```

**Constants (in `gemini.py`):**

```python
DEEP_RESEARCH_DAILY_LIMIT: int = 20       # approx. Ultra subscription daily cap
DEEP_RESEARCH_DAILY_WARNING: int = 18     # warn when this many runs used
```

**Stderr output format (printed after each deep-research run):**

For OAuth run:
```
[deep-research] OAuth runs today: 3/20
```

For paid run:
```
[deep-research] Paid (AI Studio) runs today: 1
```

Warning (when `oauth_count >= DEEP_RESEARCH_DAILY_WARNING` after an OAuth run):
```
[deep-research] Warning: 18/20 OAuth runs used today. Next 2 are free; after that AI Studio paid billing applies.
```

The counter is incremented on **completion**, not start — a cancelled or errored run does
not count. On first run, the file is created if absent.

**Deliverables:**

- `src/ai_cli/gemini.py` — counter read/write on deep-research completion; `DEEP_RESEARCH_DAILY_LIMIT` and `DEEP_RESEARCH_DAILY_WARNING` constants
- State file: `~/.local/state/ai-cli/dr-daily.json`

**Acceptance criteria:**

- [ ] `oauth_count` increments after a successful OAuth deep-research run
- [ ] `paid_count` increments after a successful `ai_studio_paid` deep-research run
- [ ] Counter resets on date rollover (test with mocked date)
- [ ] Cancelled or errored run does not increment the counter
- [ ] Counter file is created on first run if absent
- [ ] Stderr prints `[deep-research] OAuth runs today: N/20` after each OAuth run
- [ ] Stderr prints warning line when `oauth_count >= 18` after an OAuth run
- [ ] `quiet=True` suppresses counter output (consistent with other stderr output)

**Dependencies:** T-01 (need correct `tier_name` in log to distinguish oauth vs paid)

---

### T-03: `ai spend gemini` command

**Size:** M
**Batch:** 2

New subcommand combining two data sources: BigQuery billing export for `ai_studio_paid`
actual billed amounts; local JSONL logs + `dr-daily.json` for OAuth and free-tier run
counts.

**Example output:**

```
Gemini usage — today (2026-04-11)
  Deep Research:  3 OAuth runs (17 remaining free)  |  1 paid run
  Other models:   flash ×12  •  deep-think ×2

This month (Apr 2026)
  Deep Research:  18 OAuth  •  4 paid
  Paid API spend: $14.80  (source: GCP billing export, as of 2026-04-10)
  Est. monthly credit remaining: $85.20  (vs $100/mo — offset by Ultra subscription)
```

When BigQuery export is not configured:
```
Paid API spend: not available — BigQuery billing export not configured.
  To enable: https://console.cloud.google.com/billing/export
  One-time setup (~5 min); data appears within 24-48h.
```

**BigQuery integration:**
- Optional dependency: `google-cloud-bigquery` (skip spend query gracefully if not installed)
- Auth: ADC (`gcloud auth application-default login`)
- Config keys added to `config.toml` under `[gemini_billing]`:
  - `gcp_project_id` — GCP project to run BQ jobs from (auto-detectable via `gcloud config get-value project`)
  - `billing_export_table` — full table path `PROJECT.DATASET.TABLE` of the billing export
- Query: filter `service.description = 'Gemini API'`, group by `DATE(usage_start_time)` and `sku.description`
- On first successful query, log the SKU descriptions found so the model→SKU mapping can be confirmed

**Deliverables:**

- `src/ai_cli/main.py` — `ai spend gemini` subcommand dispatch
- `src/ai_cli/spend.py` (new) — log parsing and BigQuery query logic
- `docs/tools/ai-cli-usage.md` — document the command and BigQuery setup steps

**Acceptance criteria:**

- [ ] `ai spend gemini` prints daily OAuth DR counter with free quota remaining
- [ ] Shows per-model run counts for today from JSONL logs
- [ ] When BigQuery is configured: shows paid spend with data-as-of date
- [ ] When BigQuery is not configured: prints actionable setup message, does not crash
- [ ] When `google-cloud-bigquery` is not installed: prints install hint, does not crash
- [ ] Monthly aggregate reads all JSONL log files for current calendar month
- [ ] Graceful output when no runs logged today ("No Gemini runs today")

**Dependencies:** T-01 (correct tier names in logs), T-02 (DR counter file)

---

### T-04: Replace MEMORY.md ledger in `gemini_cost_sync` handler

**Size:** S
**Batch:** deferred

**Status: OUT OF SCOPE for ai-cli-utils / DEFERRED**

The `gemini_cost_sync` hw-scheduling handler lives in the humanware/sergei project
(linked to SW-767), not in this repo. The ai-cli-utils side of this integration
(log schema + `ai spend gemini`) is delivered by T-01–T-03. Once those land, the
humanware side can be updated separately to read from the JSONL logs or the `ai spend`
command output instead of the MEMORY.md ledger.

No action needed in this task — remove the T-04 blocker from the AI-CLI-41 roadmap
entry.

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Foundation — fix token extraction, tier naming, DR counter | Plan approval |
| 2 | T-03 | Surface — `ai spend gemini` command | Human review of BigQuery setup + output format |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
>
> - \<enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before Batch 1 | Approve scope, approach, tier naming system |
| BigQuery setup | Before T-03 | Enable GCP BigQuery billing export (one-time, ~5 min in Cloud Console); enable BigQuery API on the billing project |
| Output format review | After T-03 draft | Approve `ai spend gemini` display and SKU→model mapping before finalizing |
| UAT | After Batch 2 | Confirm counter increments correctly, paid spend pulls correctly |

## Open Questions

1. **BigQuery export project:** Which GCP project should the billing export write to?
   `gen-lang-client-0651020461` (Tier 1) is linked to the paid billing account
   (`01AC33-5BE8AD-2F4E8A`). Should the export write to that project, or to
   `humanware-492904`? (This determines the `billing_export_table` config key.)

2. **Exact `sku.description` strings:** The BigQuery export uses opaque SKU strings
   (e.g. `"Gemini 3.1 Pro Preview Input Tokens"`). These need to be mapped to
   model aliases used by `ai gemini`. The first live query will reveal actual SKU strings —
   this mapping is confirmed empirically, not upfront.

3. **`ai_studio_free` token counts:** The free-tier API key path uses the same REST API
   as the paid key and should also populate `usage_metadata`. If token extraction is
   fixed in T-01 and free-tier runs still show zeros, it may be a model-specific gap
   (some Gemma/Flash variants don't return token counts). Mark as `null` with a comment
   in the log if so.

4. **`DEEP_RESEARCH_DAILY_LIMIT = 20`:** This is an approximate limit from research
   (R-4, 2026-04-10); Google has not published an exact number. The constant is easy to
   adjust. If you've hit a limit in practice, update this value and note it here.

> **Feedback Round 1:** Your thoughts on the open questions:
>
> - \<enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-10 | Plan drafted | Initial sketch based on AI-CLI-41 task + R-4 billing research |
| 2026-04-11 | Plan revised | Full audit of JSONL schema (token counts = 0, schema already rich); BigQuery approach adopted for paid spend; tier naming system overhauled; T-01 reframed as token extraction debug; T-04 marked out-of-scope; AI-CLI-25 confirmed not blocking |
