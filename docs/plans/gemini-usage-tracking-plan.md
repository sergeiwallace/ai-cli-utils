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

**Task:** `AI-CLI-41`
**Related:** SW-767 (sergei — track token usage per research run), SW-73 (archived, superseded by this)

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
- [Options](#options)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Replace the brittle manual MEMORY.md Gemini cost ledger with automatic tracking derived
from `ai gemini` run logs. Surface usage data via `ai spend gemini`: per-run token counts
and costs, daily Deep Research OAuth run counter (free quota awareness), and aggregate
billing estimates. Enables conscious use of the free OAuth quota before falling back to
the paid Vertex API key.

> **Feedback Round 1:** Is the scope right? Anything to add or cut?
>
> - \<enter feedback here>

## Background and Context

### Current state

- `~/.local/state/ai-cli/gemini-logs/` — JSONL file per day, one entry per `ai gemini`
  run. Already written by the existing implementation. Content TBD (see Open Questions).
- `~/.local/state/ai-cli/gemini-output/` — auto-generated output files, one per run.
- The `gemini_cost_sync` hw-scheduling handler (renamed from `quota_sync` by AI-CLI-25
  T-01) reads a manual MEMORY.md ledger. This is the brittle part to replace.

### Why Deep Research counter matters (context from R-4, 2026-04-10)

From R-4 A/B research (see `humanware-mobile/docs/research/gemini-deep-research-billing-synthesis.md`):

- **OAuth (tier 1):** routes through Google AI Ultra consumer subscription. Deep Research
  via OAuth = ~20 runs/day free, no API cost. This is the preferred path.
- **Free-tier key (tier 2):** Flash/Gemma models only. Gemini 3.1 Pro has no free quota
  tier — deep-research, pro, and deep-think calls fail immediately with a billing error.
  The fallback chain now skips tier 2 for ineligible models.
- **Paid Vertex API key (tier 3):** all models. Deep Research = ~$2–5/task (standard),
  ~$3–5 (complex). Offset by $100/month Ultra credit (~20–50 runs/month).
- A single complex Deep Research task can use ~900K input tokens + 160 search queries.
  Unmonitored usage can drain the $100/month credit faster than expected.

**Goal:** make the daily OAuth DR run count visible so we consciously exhaust free quota
(~20/day — more than we've ever used in a day) before any paid tier 3 run.

### Auth key setup (as of 2026-04-10)

| Tier | Env var | Key type | Cost |
|------|---------|----------|------|
| 1 | — (OAuth) | Gemini CLI credentials | Free (Ultra consumer subscription) |
| 2 | `GOOGLE_API_KEY_FREE_TIER` | Google AI Studio free-tier key | Free for Flash/Gemma; fails for Pro |
| 3 | `GOOGLE_API_KEY_TIER_1` | Vertex AI API key | Billed; offset by $100/mo Ultra credit |

## Options

### Option A: Parse existing JSONL logs

Enrich the per-run JSONL log entries to include model, tier used, token counts, and
deep-research flag. Derive the daily DR counter and cost estimates by parsing today's
log file on-demand when `ai spend gemini` is called.

**Pros:**
- Minimal new state — JSONL logs already exist; extend what's there
- Single source of truth (logs are already written)
- Easy to backfill/recompute from historical logs

**Cons:**
- Need to verify what the current JSONL schema contains (may need enrichment)
- On-demand parsing could be slow if logs grow large (unlikely in practice)
- No real-time counter available mid-session without reading the log

### Option B: Explicit state file (lightweight counter)

Write a separate `~/.local/state/ai-cli/dr-daily.json` on each successful deep-research
completion: `{ "date": "2026-04-10", "oauth_count": 3, "paid_count": 1 }`. Reset when
date changes. Also track per-run data in JSONL logs for token/cost detail.

**Pros:**
- Trivial to read the daily count (single JSON file, no parsing)
- Date-rollover logic is simple
- Works for mid-session real-time count check

**Cons:**
- Second state file alongside JSONL logs (minor duplication)
- Needs to be written on completion, not start (partial-run crash = not counted)

### Option C: SQLite table

Add a `gemini_runs` table to the existing ai-cli SQLite DB. One row per run: model,
tier, timestamp, token counts (input/output/thinking/tool), estimated cost, dr_flag.

**Pros:**
- Queryable: `ai spend gemini --since 7d`, `ai spend gemini --model deep-research`
- Consistent with the rest of ai-cli's data model (Claude quota already in SQLite)
- Enables SW-767's per-run prompt-registry-ID linkage later

**Cons:**
- Heavier to implement than A or B
- SQLite dependency already present (not a new dependency)

### Recommendation

**Option B + A hybrid:** use the explicit state file (Option B) for the daily DR counter
(simple, fast, real-time) and enrich the existing JSONL logs (Option A) for per-run
token/cost detail. Defer the SQLite table (Option C) to a follow-up when cross-run
querying becomes needed. This delivers the most important feature (DR quota awareness)
with minimal complexity, and keeps the door open for Option C later.

## Task Breakdown

### T-01: Verify and enrich JSONL log schema

**Size:** S
**Batch:** 1

Inspect `~/.local/state/ai-cli/gemini-logs/` to understand what's currently logged per
run. Enrich entries to include: `model`, `tier_used` (1/2/3), `is_deep_research` (bool),
`token_input`, `token_output`, `token_thinking`, `token_tool_use`, `estimated_cost_usd`.

Token data source: the Interactions API completion response already includes usage in the
response object (confirmed from R-67: 288,937 total tokens). Standard `generateContent`
responses include usage metadata too. Extract and log on run completion.

**Deliverables:**

- `src/ai_cli/gemini.py` — enrich log-write call with new fields
- JSONL schema documented in `docs/tools/ai-cli-usage.md`

**Acceptance criteria:**

- [ ] After a `ai gemini -m flash` run, log entry contains `model`, `tier_used`, token counts
- [ ] After a `ai gemini -m deep-research` run, log entry contains all fields including `is_deep_research: true`
- [ ] Log entries for runs where token data is unavailable have `null` for token fields (not absent)

**Dependencies:** None

---

### T-02: Deep Research daily OAuth run counter

**Size:** S
**Batch:** 1

Write/increment `~/.local/state/ai-cli/dr-daily.json` on each successful deep-research
run completion. Schema:

```json
{
  "date": "2026-04-10",
  "oauth_count": 3,
  "paid_count": 1,
  "last_run": "2026-04-10T18:30:00"
}
```

Reset (date rollover) when `date` field differs from today. Track `oauth_count` (tier 1)
and `paid_count` (tier 3) separately. Increment on completion, not start — a failed or
cancelled run does not count.

**Deliverables:**

- `src/ai_cli/gemini.py` — counter write on deep-research completion
- State file: `~/.local/state/ai-cli/dr-daily.json`

**Acceptance criteria:**

- [ ] `oauth_count` increments after a successful tier-1 deep-research run
- [ ] `paid_count` increments after a successful tier-3 deep-research run
- [ ] Counter resets on date rollover (test with mocked date)
- [ ] A cancelled or errored run does not increment the counter
- [ ] Counter file is created on first run if absent

**Dependencies:** T-01 (tier_used tracking needed to distinguish oauth vs paid)

---

### T-03: `ai spend gemini` command

**Size:** M
**Batch:** 2

New subcommand surfacing Gemini usage data. Output:

```
Gemini usage — today (2026-04-10)
  Deep Research:  3 OAuth runs (17 remaining free)  |  1 paid run (~$3.20)
  Other models:   flash ×12  •  deep-think ×2
  Est. cost today: $3.20  (tier 3 only; OAuth = $0)

This month (Apr 2026)
  Deep Research:  18 OAuth  •  4 paid  (~$14.80)
  Est. total API cost: $14.80  (vs $100/mo Ultra credit — $85.20 remaining est.)
```

Parse today's JSONL log for model/tier/cost breakdown. Read `dr-daily.json` for
DR-specific counter. Monthly aggregate from log files in `gemini-logs/` dir.

**Deliverables:**

- `src/ai_cli/main.py` — `ai spend gemini` subcommand dispatch
- `src/ai_cli/spend.py` (new) — log parsing and display logic
- `docs/tools/ai-cli-usage.md` — document the command

**Acceptance criteria:**

- [ ] `ai spend gemini` prints daily DR counter with free quota remaining
- [ ] Distinguishes OAuth runs (free) from paid runs (tier 3)
- [ ] Shows per-model run counts for today
- [ ] Shows estimated cost for tier 3 runs (based on token counts × published pricing)
- [ ] Monthly aggregate reads all log files for current calendar month
- [ ] Graceful output when no runs logged today ("No Gemini runs today")

**Dependencies:** T-01, T-02

---

### T-04: Replace MEMORY.md ledger in `gemini_cost_sync`

**Size:** S
**Batch:** 2

Update the `gemini_cost_sync` hw-scheduling handler to derive cost data from JSONL logs
instead of the manual MEMORY.md ledger. The handler should read today's and yesterday's
log files, aggregate costs, and push the result to hw-scheduling.

**Deliverables:**

- `src/ai_cli/handlers/gemini_cost_sync.py` (or equivalent) — replace ledger read with
  log parse
- Remove MEMORY.md ledger read logic

**Acceptance criteria:**

- [ ] `gemini_cost_sync` runs without reading MEMORY.md
- [ ] Cost data in hw-scheduling DB reflects actual run data from logs
- [ ] Existing `aido spend` / `ai spend` surfaces show correct values

**Dependencies:** T-01, T-03; blocked on AI-CLI-25 T-01 landing (rename `quota_sync` →
`gemini_cost_sync`) — verify status before starting T-04.

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Foundation — log enrichment + DR counter | Plan approval |
| 2 | T-03, T-04 | Surface — `ai spend gemini` + replace ledger | Human review of output format |

> **Feedback Round 1:** Does the batching make sense? Output format for `ai spend gemini`?
>
> - \<enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before Batch 1 | Approve scope and approach |
| Output format review | After T-03 draft | Approve `ai spend gemini` display before finalizing |
| UAT | After Batch 2 | Confirm counter increments correctly, cost estimates look right |

## Open Questions

1. **What does the current JSONL log schema contain?** T-01 starts with an audit of
   `~/.local/state/ai-cli/gemini-logs/` to see what fields are already written. If token
   counts are already logged, T-01 is mostly an enrichment pass. If not, the
   `generateContent` and Interactions API responses need to be tapped.

2. **Does `generateContent` (flash, pro, deep-think) return token usage?** The Interactions
   API completion response definitely does (confirmed from R-67). Standard `generateContent`
   responses include `usageMetadata` in the Gemini API response — but does the current
   `ai gemini` implementation read and log it? Confirm in `src/ai_cli/gemini.py`.

3. **AI-CLI-25 T-01 status?** T-04 is blocked on the `quota_sync` → `gemini_cost_sync`
   rename from AI-CLI-25. Verify this has landed before starting Batch 2.

4. **Monthly cost estimate accuracy:** The $100/mo Ultra credit applies to all Gemini API
   usage on the linked billing account — not just Deep Research. Other projects or
   services using the same billing account will reduce the effective remaining credit.
   Should `ai spend gemini` show raw API cost only, or attempt to account for the credit?

> **Feedback Round 1:** Your thoughts on the open questions:
>
> - \<enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-10 | Plan drafted | Initial draft based on AI-CLI-41 task + R-4 billing research |
