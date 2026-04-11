---
title: "Gemini API Cost and Usage Tracking Overhaul"
category: plans
tags: [ai-gemini, usage-tracking, billing, deep-research, quota, ai-cli-41]
status: COMPLETE
source: internal
---

# Gemini API Cost and Usage Tracking Overhaul — Implementation Plan

**Status:** COMPLETE (T-01, T-02, T-03 shipped 2026-04-11; T-04/HW-3 tracked in humanware)

**Created:** 2026-04-10
**Revised:** 2026-04-11 (round 3)

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

Surface Gemini API usage and cost data automatically, with safety-first design around
paid API spending. Core deliverables: (1) fix token count extraction so per-run data is
actually logged; (2) a `paid_fallback_enabled` config toggle (default: `false`) that
removes the `ai_studio_paid` tier from the fallback chain entirely until billing credit
applicability is confirmed (AI-CLI-43); (3) a daily Deep Research OAuth run counter with
real-time stderr feedback; (4) an `ai spend gemini` command pulling actual billed amounts
from GCP BigQuery billing export for paid runs, with OAuth run counts from local logs.

---

> **Feedback Round 1:** Is the scope right? Anything to add or cut?
>
> - Scope seems fine. Revise to account for the feedback on open questions below, especially the billing uncertainty — that's the most important thing to get right before we implement.

> **AI Response Round 1:**
> - Scope confirmed. Revised to make the `ai_studio_paid` gate the primary safety mechanism — it's now the first thing implemented in T-02, not an afterthought. Billing uncertainty documented as a P0 open question with a prerequisite investigation step before T-02 implementation.

---

> **Feedback Round 2:**
> - Do not set up a Tier 1 paid API key / prepaid amount yet. Deep-research should use OAuth only for now; disable the `ai_studio_paid` fallback via a config toggle (`paid_fallback_enabled` or similar). Re-enable only after AI-CLI-43 is resolved and we're confident in the billing credit situation. Add the config toggle to the plan and implement accordingly. "Vertex-only" credit claim confirmed to have no reliable source — can drop that concern from the email.

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

### Billing Credit Uncertainty — P0 Prerequisite

**This is the most important open question in this plan.** The situation:

- The Google AI Ultra subscription ($100/month credit) was previously not connected to
  the billing account used for the `ai_studio_paid` API key. That's now fixed.
- **Open question:** Does the Ultra subscription credit apply to Google AI Studio API
  keys (`AIzaSy...`, billing account `01AC33-5BE8AD-2F4E8A`)? Or only to Vertex AI keys?
  Mixed information online — no authoritative answer found yet.
- The Interactions API (used for `ai gemini -m deep-research`) likely doesn't work with
  Vertex API keys at all, which would make a key switch impossible even if Vertex is
  the right answer.
- **If the Ultra credit does NOT apply to `ai_studio_paid`:** every paid deep-research
  run costs ~$2–5 out of pocket, unsubsidized. The current code silently falls back to
  this path when OAuth is unavailable — that's unacceptable.

**Consequence for implementation:** T-02 must gate `ai_studio_paid` deep-research behind
an explicit user opt-in (`--confirm-paid` / `-P` flag), regardless of what we later learn
about credit applicability. The tool should never silently spend money on deep-research.

Resolving the credit question (see Open Questions Q5) will inform whether the warning
message says "billing may or may not be subsidized" vs. a definitive statement, but the
gate itself is implemented unconditionally.

### Why the Deep Research Counter Matters

OAuth deep-research runs are free under the Google AI Ultra consumer subscription. The
daily limit (~20 runs, empirically unverified) is more than enough for typical usage, but
without a counter a session has no visibility into how many free runs remain. The counter
makes the free quota visible so the paid fallback is a conscious, last-resort choice —
not an accidental one.

### Auth Infrastructure (as of 2026-04-11)

`_get_google_oauth_token()` was added in a previous session — OAuth is now tried first
for deep-research and falls back to the paid API key if unavailable. T-02 changes this:
when OAuth is unavailable, the tool no longer auto-falls-back. Instead it exits with an
actionable error unless `--confirm-paid` / `-P` is explicitly provided.

### GCP Billing Configuration (confirmed 2026-04-11)

- Billing account: `01AC33-5BE8AD-2F4E8A` (Sergei Wallace)
- GCP project for paid AI Studio key: `gen-lang-client-0651020461` (Tier 1)
- BigQuery billing export: **not yet configured** — one-time human setup required
- Service name in billing export: `"Gemini API"` (`generativelanguage.googleapis.com`)
- OAuth runs are not billed and will not appear in the export

### GCP Billing API Research (2026-04-11)

GCP has no direct billing query API. The Cloud Billing API manages account links; it
does not return cost data. The only path to actual billed amounts is BigQuery billing
export queried via `google-cloud-bigquery`. Granularity: daily per-model SKU (not
per-request). Data is delayed 24–48h. Exact `sku.description` strings per model are
confirmed empirically on first query.

### AI-CLI-25 Status

The ai-cli-utils code changes from AI-CLI-25 are complete: `quota_sync_from_remote()`
and Slack webhook sending are implemented. Remaining items are human actions (webhook URL
creation, Doppler config) and a separate humanware-project task — they do not block
AI-CLI-41.

## Auth Tier Naming

The old `tier 1/2/3` numbering conflates an internal fallback-chain concept with
Google's own billing terminology. Replacing throughout with Google-aligned names:

| New name | Old label | What it is |
|----------|-----------|-----------|
| `oauth` | "gemini-cli (OAuth)" / tier 1 | Gemini CLI OAuth via Google AI Ultra consumer subscription — free, daily DR cap |
| `ai_studio_free` | "API free-tier" / tier 2 | Google AI Studio free-tier API key — free for Flash/Gemma only |
| `ai_studio_paid` | "API paid" / tier 3 | Google AI Studio paid API key — **disabled by default** (`paid_fallback_enabled = false`) until AI-CLI-43 resolves billing credit applicability |

These names are used in:
- `TIER_NAMES` dict in `gemini.py`
- `tier_name` field in JSONL log entries
- Stderr output and `ai spend gemini` display
- Session config and doc references

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
- We're calculating spend ourselves — not reading what Google actually charged
- Published rates and billing reality diverge (credits, discounts, rounding)
- Per-token prices change; the tool falls out of sync
- OAuth runs have no token data (subprocess path)
- Doesn't resolve the Ultra credit question — just estimates

### Option B: BigQuery billing export for paid spend

Enable GCP BigQuery billing export (one-time human setup, ~5 min). Python tool queries
`google-cloud-bigquery` to get actual billed amounts for `ai_studio_paid` runs.

**Pros:**
- Actual billed amounts — what Google charged, including any credits applied
- Per-model SKU breakdown available
- `google-cloud-bigquery` + ADC auth is the standard path

**Cons:**
- One-time human setup required before paid-spend data is available
- Data is delayed 24–48h — "today's spend" shows yesterday's data
- Per-request granularity not available (hourly SKU aggregates only)
- Exact SKU strings need empirical mapping on first query

### Option C: Hybrid — BigQuery for paid, JSONL for OAuth (recommended)

Use BigQuery for `ai_studio_paid` actual billed amounts; use JSONL logs for OAuth and
free-tier run counts. `ai spend gemini` combines both sources. Graceful degradation when
BigQuery is not set up.

**Pros:**
- Ground truth for paid spend — resolves the credit uncertainty empirically (if $0 shows
  in billing after a paid run, the credit is being applied)
- No paid-run cost calculation needed
- OAuth and free-tier runs fully tracked via JSONL

**Cons:**
- Two data sources to integrate in T-03
- BigQuery setup is a required human gate before paid-spend data appears

### Recommendation

**Option C (hybrid).** Most importantly: the BigQuery billing export will reveal exactly
what Google charged after a paid run, which is the only reliable way to determine whether
the Ultra credit is being applied to AI Studio API key usage. This makes Option C
doubly valuable — it's both the usage-display feature and the empirical answer to the
billing uncertainty.

## Task Breakdown

### T-01: Fix token extraction + log schema enrichment

**Size:** S
**Batch:** 1

Debug why `input_tokens`, `output_tokens`, `total_tokens` are always `0` in JSONL logs
even though `_call_rest_api()` reads `response.usage_metadata`. Likely culprits: the
model doesn't populate `usage_metadata`, the SDK attribute name differs from expected
(`prompt_token_count`/`candidates_token_count`), or `usage_metadata` is `None`. Fix
extraction; log `null` (not `0`) when unavailable so the two cases are distinguishable.

Also:
- Add `is_deep_research: bool` to each log entry
- Update `TIER_NAMES` dict: `{1: "oauth", 2: "ai_studio_free", 3: "ai_studio_paid"}`
- Fix `_run_deep_research()` to log the actual auth used (`"oauth"` or `"ai_studio_paid"`)
  instead of the generic `"Interactions API"`

**Deliverables:**

- `src/ai_cli/gemini.py`

**Acceptance criteria:**

- [ ] After a REST API run, log entry has non-zero `input_tokens`/`output_tokens`, or explicit `null` (not `0`) if the model doesn't return them
- [ ] After a `ai gemini -m deep-research` run, log entry has `is_deep_research: true`
- [ ] Deep-research OAuth run logs `tier_name: "oauth"`; paid key run logs `tier_name: "ai_studio_paid"`
- [ ] Flash OAuth subprocess run logs `tier_name: "oauth"`
- [ ] `TIER_NAMES` uses new naming in all log entries going forward

**Dependencies:** None

---

### T-02: `paid_fallback_enabled` config toggle + daily OAuth counter

**Size:** S
**Batch:** 1

Two closely related behaviors, implemented together:

**Part A — `paid_fallback_enabled` config toggle:**

Add a boolean config key to `config.toml` under `[gemini]`:

```toml
[gemini]
paid_fallback_enabled = false   # set true only after AI-CLI-43 confirms billing credit status
```

When `false` (default): `ai_studio_paid` is removed from the fallback chain entirely for
all models. If OAuth and free-tier both fail or are unavailable, the run exits cleanly:

```
[ai gemini] No available auth method succeeded.
  OAuth: unavailable  |  AI Studio free tier: not eligible for this model
  AI Studio paid fallback is disabled (paid_fallback_enabled = false in config).
  To enable: set paid_fallback_enabled = true in ~/.config/ai-cli/config.toml
  (Do this only after confirming billing credit status — see AI-CLI-43)
```

When `true`: `ai_studio_paid` re-enters the fallback chain. For deep-research
specifically, the `-P` / `--confirm-paid` flag is still required as an additional
runtime gate (conscious opt-in, even when paid is enabled in config):

```
[deep-research] OAuth unavailable. AI Studio paid key is enabled in config.
  This run may incur charges — billing credit status: unconfirmed (see AI-CLI-43).
  To proceed: ai gemini "..." -m deep-research -P
  To check spend: ai spend gemini
```

The `-P` / `--confirm-paid` flag is implemented now (for when paid is eventually
re-enabled) but has no effect while `paid_fallback_enabled = false`.

**Part B — Daily OAuth run counter:**

Write/increment `~/.local/state/ai-cli/dr-daily.json` on each successful deep-research
completion. Schema:

```json
{
  "date": "2026-04-11",
  "oauth_count": 3,
  "paid_count": 1,
  "last_run": "2026-04-11T18:30:00"
}
```

Constants in `gemini.py`:

```python
DEEP_RESEARCH_DAILY_LIMIT: int = 20       # approx. daily cap; unverified — update empirically
DEEP_RESEARCH_DAILY_WARNING: int = 18     # warn when this many OAuth runs used
```

Stderr output after each OAuth deep-research run:
```
[deep-research] OAuth runs today: 3/20
```

After each paid deep-research run (only reachable when `paid_fallback_enabled = true`):
```
[deep-research] Paid (AI Studio) runs today: 1 — check `ai spend gemini` for charges
```

Warning when `oauth_count >= DEEP_RESEARCH_DAILY_WARNING`:
```
[deep-research] Warning: 18/20 OAuth runs used today. Approaching daily limit.
```

Counter increments on **completion** only. Cancelled/errored runs not counted. File
created on first run. `quiet=True` suppresses counter output.

**Deliverables:**

- `src/ai_cli/gemini.py` — `paid_fallback_enabled` config check in fallback chain,
  `-P`/`--confirm-paid` gate for deep-research when paid is enabled, counter logic,
  `DEEP_RESEARCH_DAILY_LIMIT` / `DEEP_RESEARCH_DAILY_WARNING` constants
- `src/ai_cli/main.py` — add `-P`/`--confirm-paid` to `ai gemini` arg parser
- `src/ai_cli/config.py` (or equivalent) — `paid_fallback_enabled` key, default `false`
- State file: `~/.local/state/ai-cli/dr-daily.json`
- `docs/tools/ai-cli-usage.md` — document the config key and what it controls

**Acceptance criteria:**

- [ ] When `paid_fallback_enabled = false`: `ai_studio_paid` never attempted regardless of model; exits with actionable message if OAuth/free-tier exhaust
- [ ] When `paid_fallback_enabled = true` and OAuth unavailable for deep-research: exits unless `-P` provided
- [ ] When `paid_fallback_enabled = true` and `-P` provided: runs with `ai_studio_paid`, prints warning
- [ ] `oauth_count` increments after a successful OAuth deep-research run
- [ ] `paid_count` increments after a successful `ai_studio_paid` deep-research run
- [ ] Counter resets on date rollover (test with mocked date)
- [ ] Cancelled or errored run does not increment the counter
- [ ] Counter file created on first run if absent
- [ ] Stderr prints `[deep-research] OAuth runs today: N/20` after each OAuth run
- [ ] Warning line printed when `oauth_count >= 18`
- [ ] `quiet=True` suppresses counter output
- [ ] Default config has `paid_fallback_enabled = false`

**Dependencies:** T-01 (correct `tier_name` in log for oauth vs paid distinction)

---

### T-03: `ai spend gemini` command

**Size:** M
**Batch:** 2

New subcommand combining BigQuery billing export for `ai_studio_paid` actual billed
amounts and local JSONL logs + `dr-daily.json` for OAuth and free-tier run counts.

**Config keys** (`config.toml` under `[gemini_billing]`):
```toml
[gemini_billing]
gcp_project_id = "gen-lang-client-0651020461"
billing_account_id = "01AC33-5BE8AD-2F4E8A"
billing_export_table = ""   # filled after BigQuery export is enabled
```

**BigQuery query:** filter `service.description = 'Gemini API'`, group by
`DATE(usage_start_time)` and `sku.description`. On first successful query, print
raw SKU strings so model→SKU mapping can be confirmed.

**Example output:**

```
Gemini usage — today (2026-04-11)
  Deep Research:  3 OAuth runs (17 remaining free)  |  0 paid runs
  Other models:   flash ×12  •  deep-think ×2

This month (Apr 2026)
  Deep Research:  18 OAuth  •  1 paid run
  Paid API spend: $0.00  (source: GCP billing export, as of 2026-04-10)
    → Ultra credit appears to be applied ✓
```

Or, if billing shows a charge:

```
  Paid API spend: $3.20  (source: GCP billing export, as of 2026-04-10)
    → Charges are being applied — Ultra credit may not cover AI Studio API keys
```

When BigQuery not configured:
```
Paid API spend: not available — BigQuery billing export not configured.
  To enable: Cloud Console → Billing → Billing export → Detailed usage cost
  One-time setup (~5 min); data appears within 24-48h.
  GCP project: gen-lang-client-0651020461
```

**Deliverables:**

- `src/ai_cli/main.py` — `ai spend gemini` subcommand dispatch
- `src/ai_cli/spend.py` (new) — log parsing and BigQuery query logic
- `docs/tools/ai-cli-usage.md` — document command + BigQuery setup steps

**Acceptance criteria:**

- [ ] `ai spend gemini` prints daily OAuth DR counter with free quota remaining
- [ ] Shows per-model run counts for today from JSONL logs
- [ ] When BigQuery configured: shows paid spend with data-as-of date + credit status hint
- [ ] When BigQuery not configured: prints actionable setup message, does not crash
- [ ] When `google-cloud-bigquery` not installed: prints install hint, does not crash
- [ ] Monthly aggregate reads all JSONL log files for current calendar month
- [ ] Graceful output when no runs logged today

**Dependencies:** T-01, T-02

---

### T-04: Replace MEMORY.md ledger in `gemini_cost_sync` handler

**Size:** S
**Batch:** 3 (after T-01–T-03 are done)
**Repo:** humanware — `src/humanware/scheduling/handlers/gemini_cost_sync.py`
**Tracking:** `HW-3` in humanware roadmap

Confirmed: the handler exists in humanware. It reads a MEMORY.md cost ledger
(`project_claude_quota.md`) via regex and writes `quota.gemini.monthly` to NATS KV.

Replace the regex ledger read with JSONL log parsing from
`~/.local/state/ai-cli/gemini-logs/`. Sum `estimated_cost_usd` across today's and
yesterday's log entries (once T-01 populates that field). Fall back to BigQuery
billing export query (T-03) if token-count-based cost is unavailable.

**Blocked on:** T-01 (log enrichment), T-03 (`ai spend gemini` / BigQuery integration).

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Foundation — token extraction fix, tier naming, DR counter + paid gate | Plan approval |
| 2 | T-03 | Surface — `ai spend gemini` command | Human review of BigQuery setup + output format |
| 3 | T-04 | Humanware — replace MEMORY.md ledger in `gemini_cost_sync` | T-01 + T-03 done; work in humanware repo (HW-3) |

---

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
>
> - \<enter feedback here>

> **AI Response Round 1:**
> - No feedback received on batching — proceeding with two-batch plan as designed.

---

> **Feedback Round 2:**
> - No batching feedback — proceed as designed.

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before Batch 1 | Approve scope, approach, tier naming, config toggle behavior |
| BigQuery setup | Before T-03 | Enable GCP BigQuery billing export in Cloud Console; enable BigQuery API on `gen-lang-client-0651020461` |
| Output format review | After T-03 draft | Approve `ai spend gemini` display and SKU→model mapping |
| UAT | After Batch 2 | Confirm counter increments, paid gate config works, BigQuery spend data pulls |
| Re-enable paid fallback | After AI-CLI-43 resolved | Set `paid_fallback_enabled = true` in config once billing credit status confirmed |

## Open Questions

1. **Resolved** — BigQuery project: `gen-lang-client-0651020461`, billing account
   `01AC33-5BE8AD-2F4E8A`.

2. **Resolved** — SKU strings: confirmed empirically on first query.

3. **Resolved** — Token counts: if unavailable after T-01 fix, log `null` (not `0`).

4. **Partially resolved** — `DEEP_RESEARCH_DAILY_LIMIT = 20`: empirically unknown but
   reasonable starting point. Update the constant when a real limit is encountered.

5. **DEFERRED (not blocking) — Does the Ultra credit apply to AI Studio paid key?**
   - "Vertex-only" claim traced to a single unanswered forum post citing Reddit with no
     official source — not a reliable citation.
   - Official Developer Program docs say credits apply to "AI Studio and Vertex AI or
     any Google Cloud product" — suggests AI Studio keys ARE covered.
   - However, AI Studio still shows "Action Needed - No Available Credits" and requires
     Prepay for new projects, suggesting the credit may not satisfy the Prepay
     requirement regardless of whether it appears on the GCP invoice.
   - **Not blocking:** `paid_fallback_enabled = false` (default) means no paid runs fire
     until this is resolved. Email sent to `gdp-premium-support@google.com` 2026-04-11.
     Follow-up via Google Cloud Support chat/phone in progress (AI-CLI-43, due 2026-04-12).
   - **Resolution path:** once AI-CLI-43 is answered, update warning text in T-02,
     set `paid_fallback_enabled = true` in config, and optionally run one `--confirm-paid`
     run + check next-day BigQuery export to confirm empirically.

---

> **Feedback Round 1:** Your thoughts on the open questions:
>
> 1. Billing project confirmed: `gen-lang-client-0651020461`, billing account `01AC33-5BE8AD-2F4E8A`.
> 2. SKU mapping: understood, empirical on first query.
> 3. Token counts: fine, mark null if unavailable.
> 4. Daily limit: empirically unknown — we haven't been tracking effectively. More importantly: there's a critical open question about whether the AI Studio paid key (`GOOGLE_API_KEY_TIER_1`) has the Google AI Ultra subscription credits ($100/mo) applied. Situation: (a) the Ultra subscription wasn't connected to the billing account previously — that's now fixed. (b) there is mixed information online about whether Ultra credits apply to AI Studio API keys or only Vertex API keys. (c) the Interactions API (used for deep-research) may not work with Vertex API keys at all, so we can't just switch to Vertex. Result: we don't know if `ai_studio_paid` deep-research runs are ~free (subsidized by Ultra credit) or ~$2–5 out of pocket. The tool must NOT silently fall back to `ai_studio_paid` for deep-research. Need hard gates, explicit opt-in, and clear warnings before any paid deep-research run fires. This is the most important safety requirement for this feature.

> **AI Response Round 1:**
> - Q1, Q2, Q3 resolved as noted above.
> - Q4 (daily limit): keeping `DEEP_RESEARCH_DAILY_LIMIT = 20` as placeholder; easy to adjust empirically.
> - Q5 (billing credit uncertainty): this is now the P0 open question. T-02 implements an unconditional paid-run gate with explicit `--confirm-paid` / `-P` flag regardless of credit status — no silent fallback ever. Warning text will be updated once credit status is confirmed. BigQuery billing export (T-03) will provide empirical confirmation after one `--confirm-paid` run is observed in the billing data.

---

> **AI Response Round 2 (open questions):**
> - Q5: "Vertex-only" credit claim traced to a single unanswered forum post citing Reddit with no official source. Official Developer Program docs explicitly say credits apply to "AI Studio and Vertex AI or any Google Cloud product." Concern dropped from email. However, AI Studio still shows "Action Needed - No Available Credits" and requires Prepay for new projects — suggests a separate issue (credit not yet reflected, or Prepay is a separate requirement that coexists with credits). Email sent to `gdp-premium-support@google.com` 2026-04-11; AI-CLI-43 follow-up task added for 2026-04-12.

---

> **Feedback Round 2:**
> - \<enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-10 | Plan drafted | Initial sketch based on AI-CLI-41 task + R-4 billing research |
| 2026-04-11 | Plan revised (round 1) | Full JSONL schema audit; BigQuery approach adopted; tier naming overhauled; T-01 reframed; T-04 out-of-scope; AI-CLI-25 confirmed not blocking |
| 2026-04-11 | User feedback round 1 committed | Billing credit uncertainty (Q5) identified as P0 safety concern |
| 2026-04-11 | Plan revised (round 2) | T-02 redesigned with unconditional paid-run gate (`--confirm-paid` / `-P`); billing uncertainty documented; T-03 updated to show credit status hint from BigQuery data; Q5 added as P0 open question |
| 2026-04-11 | User feedback round 2 committed | Disable `ai_studio_paid` fallback by default via `paid_fallback_enabled` config toggle; OAuth-only for now; "Vertex-only" claim confirmed baseless; email sent to GDP premium support |
| 2026-04-11 | Plan revised (round 3) | T-02 redesigned around `paid_fallback_enabled` config toggle (default false); `-P`/`--confirm-paid` retained for when paid is re-enabled; human gate for billing credit investigation removed (not blocking); Q5 demoted to deferred |
| 2026-04-11 | T-04 confirmed and unlocked | `gemini_cost_sync` handler confirmed in humanware repo; T-04 added as Batch 3 (blocked on T-01+T-03); HW-3 added to humanware roadmap |
| 2026-04-11 | T-01, T-02, T-03 implemented | Full autonomous implementation. 1426 tests passing. Shipped to feature branch `feature/ai-cli-41-gemini-usage-tracking`. |
