---
title: "Claude Code subscription quota surfaces (2026) — statusline rate_limits vs /usage scraping"
category: research
tags: [research, claude-code, quota, statusline, rate_limits, AI-CLI-98, AI-CLI-94, AIH-120]
status: complete
source: "opus-manual-2026-07-13"
template_version: "research-1.2.0"
related_docs:
  - docs/designs/claude-usage-telemetry.md
  - docs/designs/cc-statusline.md
---

# Claude Code subscription quota surfaces (2026) — statusline `rate_limits` vs `/usage` scraping

**Status:** complete

**Created:** 2026-07-13

**Tasks:** AI-CLI-98 (capture all per-model weekly quotas), AI-CLI-94 (first-class Fable tracking), AIH-120 (secondary weekly line)

<!-- doc:region name="context" kind="immutable" -->

## Context

Re-survey of what Anthropic exposes for **subscription-tier** (Pro/Max, OAuth) Claude Code
quota data, prompted by two discoveries on **CC 2.1.207**: (1) `claude -p "/usage"` no longer
emits the quota bars (serializes an insights-only view) — the AI-CLI-95 print-mode primary
path returns `None`; (2) the per-model `Current week (<model>)` line the statusline secondary
slot depends on is gone from the TUI, replaced by a dedicated, frequently rate-limited
"Per-model breakdown" section. Question from the user: *has Anthropic shipped a better
(programmatic) way to read subscription quota since this system was designed (2026-04)?*

**Primary period:** 2026
**Source weighting:** 2026 primary (CC 2.1.x release notes, live empirical capture on 2.1.207)

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

CC 2.1.80–2.1.207 (2026). Empirical captures performed live on 2.1.207, 2026-07-13.
Supersedes the "no programmatic surface" finding in `claude-usage-telemetry.md` (R-5,
2026-04-01) for the **all-models weekly + 5-hour** windows.

## Executive Summary

**Yes — for the all-models weekly and 5-hour windows.** As of **CC v2.1.80**, the statusLine
command receives a `rate_limits` object on **stdin** (`five_hour` + `seven_day`, each with
`used_percentage` 0–100 and a `resets_at` unix epoch) [VERIFIABLE][^1]. This is the
 official, deterministic, \$0, zero-latency source that replaces all `/usage` scraping for the
primary numbers. First-party empirical capture on 2.1.207 confirmed this directly
(`seven_day.used_percentage=20`, `five_hour=23`, reset epochs matching the `/usage` TUI
exactly — see Provenance Ledger).

**No — for the per-model breakdown.** `rate_limits` carries **only** `five_hour` + `seven_day`
(all-models). There is still **no** official programmatic surface for the per-model weekly
split (Sonnet-only / Fable-only). It lives only in the interactive `/usage` TUI "Per-model
breakdown" section — backed by the internal `/api/oauth/usage` OAuth endpoint — which is
itself frequently server-side "rate limited (try again in a moment)" [INFERENCE]. The official
`claude quota --json` for headless use remains an **open, unshipped** feature request
(#13585) [VERIFIABLE][^2].

## 1. Statusline stdin `rate_limits` (official; all-models + 5-hour)

Shipped CC v2.1.80. The JSON piped to the statusLine command now includes:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 23, "resets_at": 1783978200 },
  "seven_day": { "used_percentage": 20, "resets_at": 1784052000 }
}
```

- `seven_day.used_percentage` = the **weekly all-models %** (our `usage_pct`; first-party
  capture matched the TUI "Current week (all models)" 19–20% and its Jul 14 2pm reset).
- `five_hour.used_percentage` = the **5-hour rolling** window (our session/`extra` analog;
  first-party capture matched the TUI "Current session" and its 5:30pm reset).
- `resets_at` = unix epoch seconds — replaces the scraped `reset_at` and lets `week_elapsed`
  be computed from `resets_at − 7d` instead of a fixed week-start anchor.
- **Availability caveat:** `rate_limits` "appears only for Claude.ai subscribers (Pro/Max)
  after the first API response in the session. Each window (`five_hour`, `seven_day`) may be
  independently absent." Handle absence gracefully (`jq -r '.rate_limits.seven_day.used_percentage // empty'`) [VERIFIABLE][^1].

### When to use

The primary capture path for the all-models weekly + 5-hour numbers. Because the statusLine
fires on every render of every active session, this yields frequent authoritative updates for
free — feed stdin → SQLite → NATS to keep the cross-machine snapshot fresh, and demote the
`/usage` scrape to a fallback for when no session is active. No per-model data here.

## 2. `/usage` TUI scrape + internal `/api/oauth/usage` (per-model; unofficial)

The per-model weekly split is only in the interactive `/usage` "Per-model breakdown" section
(the `Current week (Sonnet only/Fable)` line was removed as a standalone in ~2.1.20x). That
section is populated from an internal OAuth endpoint (`/api/oauth/usage`) and is frequently
returned as "unavailable (rate limited — try again in a moment)" [INFERENCE]. Community
stopgaps (e.g. a browser dashboard hitting `/api/oauth/usage` directly) exist but are
unofficial and auth-token-bound [VERIFIABLE][^2].

### When to use <!-- 2 -->

Only if first-class per-model (Fable) tracking is required (AI-CLI-94/98). Must be designed
around the rate limit: cache last-good per-model %, mark it stale/aged, and back off on a
progressive retry cadence rather than hammering (hammering worsens the rate limit). This is
the genuinely hard, brittle part — the all-models path (Option 1) has no such constraint.

## Comparison

| # | Criterion | Statusline `rate_limits` (Opt 1) | `/usage` TUI / OAuth (Opt 2) |
|---|-----------|----------------------------------|------------------------------|
| 1 | Official / supported | Yes (documented stdin field) | No (TUI scrape / internal endpoint) |
| 2 | Determinism | Deterministic (structured JSON) | Flaky (async render; rate-limited) |
| 3 | Cost / latency | \$0, zero-latency (already on stdin) | subprocess + TUI render, or HTTP |
| 4 | Coverage | all-models weekly + 5-hour + resets | per-model weekly split |
| 5 | Availability | after 1st API response; windows may be absent | frequently "rate limited" |

## Recommendation

1. **Regression fix (AI-CLI, now):** re-plumb the statusline to read `rate_limits` from stdin
   as the primary source for the weekly all-models % + 5-hour % + reset times. Retire
   `claude -p "/usage"` (dead on 2.1.207) and demote the hidden-pane scrape to a fallback.
   This is the robust, official, no-hack fix — it eliminates the flakiness class entirely for
   the primary numbers.
2. **AI-CLI-98 (per-model, separate plan):** treat per-model (Fable) tracking as the hard,
   optional layer. Design around the `/api/oauth/usage` rate limit with cache-last-good +
   staleness marker + progressive-backoff recheck. Do not block the regression fix on it.

## Open Questions

1. **[RESOLVED]** Does `seven_day` ever represent the **Sonnet-only** weekly cap for Max, or
   always all-models?
   - **Resolution (2026-07-13):** Always **all-models**. Full `/usage` capture on 2.1.207 shows
     exactly three bars — `Current session` (=`five_hour`), `Current week (all models)`
     (=`seven_day`), and `Current week (Fable)` (the one model-specific cap, NOT in `rate_limits`).
     There is **no** Opus/Sonnet/Haiku breakdown. `--model sonnet` was tested and the secondary
     line stayed `Current week (Fable)`, so **model-switching does not change it** — the second
     cap is a fixed Anthropic-defined premium-model cap, not per-active-model. Implication:
     AI-CLI-98's "capture ALL per-model via model-switching" premise is invalid; the only per-model
     datum to capture is the single Fable cap (rate-limit-aware TUI scrape — AIH-164 D-4/T-06).
2. Is `/api/oauth/usage` stable enough (auth, shape, rate-limit policy) to depend on for
   per-model, or is TUI scraping the lesser evil? (Resolve in the AI-CLI-98 plan.)
3. Cross-machine: with stdin `rate_limits` as primary, is the NATS KV + quota-subscriber
   daemon still needed, or does per-session stdin capture + publish suffice?

## Sources

[^1]: Anthropic. (2026). [Customize your status line — rate_limits fields](https://code.claude.com/docs/en/statusline). Claude Code Docs. Verified accessible (HTTP 200) 2026-07-13. (`rate_limits.five_hour|seven_day.used_percentage|resets_at`; "appears only for Claude.ai subscribers after the first API response; each window may be independently absent".)
[^2]: Anthropic community. (2026). [Add Quota Information Access to Claude Code CLI (#13585, OPEN)](https://github.com/anthropics/claude-code/issues/13585). GitHub. Verified accessible 2026-07-13. (Confirms `rate_limits` statusline fix landed v2.1.80; official `claude quota --json` for headless still unshipped; community references the internal `/api/oauth/usage` endpoint. #44328 closed as duplicate of this.)

<!-- /doc:region name="body" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** manual (no aido run)
**Model:** `opus` (inline WebSearch/WebFetch/gh + live empirical capture)
**Date:** 2026-07-13

```text
Manually-authored research doc (not an aido run). Investigation performed inline by the
aih-2 session: reviewed existing repo research/design docs (claude-usage-telemetry.md),
WebSearch for Anthropic subscription-quota APIs + CC /usage JSON, gh issue view on the
canonical feature requests (#13585, #44328), WebFetch of the official statusline docs, and
a live statusLine-stdin capture on CC 2.1.207. This doc predates use of the aido prompt
appendix template for its authoring flow.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

First-party empirical capture (session aih-2, CC 2.1.207, 2026-07-13): launched an
interactive `claude --settings` with a throwaway statusLine command that dumped its stdin
JSON. Observed `rate_limits.seven_day.used_percentage=20`, `five_hour.used_percentage=23`;
`resets_at` epochs `1783978200` → 5:30pm and `1784052000` → Jul 14 2pm — matching the
`/usage` TUI "Current session" / "Current week (all models)" bars and reset times captured
in the same session. Also confirmed `claude -p "/usage"` on 2.1.207 emits insights-only
text (no quota bars). No online source (first-party runtime observation).

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

2026-07-13 — Manual authoring (opus, session aih-2). Findings from inline research + live
CC 2.1.207 capture. No aido run.

<!-- /doc:region name="run_history" -->
