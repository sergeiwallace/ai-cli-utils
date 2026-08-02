---
title: Gemini Deep Research — OAuth Fix & GCP Client Setup
category: plan
tags: [gemini, oauth, deep-research, gcp, ai-cli-45]
status: archived
---

## Table of Contents

- [Context](#context)
- [Research Findings (Verified 2026-04-11)](#research-findings-verified-2026-04-11)
- [Track A — Code Fix: Fall Through on 403 Scope Error](#track-a--code-fix-fall-through-on-403-scope-error)
- [Track B — GCP Custom OAuth Client Setup](#track-b--gcp-custom-oauth-client-setup)
- [Track C — agent-browser Investigation](#track-c--agent-browser-investigation)
- [Track D — R-12 Retry (artelier)](#track-d--r-12-retry-artelier)
- [Acceptance Criteria](#acceptance-criteria)
- [Approval Log](#approval-log)

## Context

**Task:** `[AI-CLI-45]`

`ai gemini -m deep-research` on Hetzner was failing with "OAuth unavailable" because
`_get_google_oauth_token()` only tried `google.auth.default()` (ADC) and ignored
`~/.gemini/oauth_creds.json`. Fix shipped in `fb2496b` — token is now refreshed correctly.

However, even with a valid token, the Interactions API returns HTTP 403
"Request had insufficient authentication scopes." Root cause confirmed: the gemini CLI
authenticates to `cloudcode-pa.googleapis.com` (Code Assist backend), not
`generativelanguage.googleapis.com`. Its OAuth token cannot be reused for the Interactions API.

**Current deep-research auth reality:**
- `GOOGLE_API_KEY_FREE_TIER` → no quota for Gemini 3.1 Pro, always skipped
- `GOOGLE_API_KEY_TIER_1` → works, bills to GCP billing account `01AC33-5BE8AD-2F4E8A`
- Google Developer Program Premium \$100/mo credit IS confirmed linked to that billing account
- Every deep-research run since AI-CLI-36 has been using the paid key (no free OAuth path existed)

**Billing account:** `01AC33-5BE8AD-2F4E8A` — confirmed in GCP → Billing → Credits with
Developer Program Premium credit applied. Cost per deep-research task is likely well under \$1
(total spend \$7.61 for multiple runs this month).

## Research Findings (Verified 2026-04-11)

Source: researcher agent run 2026-04-11, citations from ai.google.dev official docs.

1. **gemini CLI OAuth is architecturally wrong for Interactions API** — CLI token is scoped
   to `cloudcode-pa.googleapis.com` (Code Assist), not `generativelanguage.googleapis.com`.
   No configuration change fixes this.

2. **OAuth for Interactions API IS possible** but requires a custom Desktop-app OAuth client:
   - Create OAuth 2.0 client (Desktop app) in GCP Console
   - Download `client_secret.json`
   - Run: `gcloud auth application-default login --client-id-file=client_secret.json --scopes='https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/generative-language.retriever'`
   - Creates ADC credentials → `google.auth.default()` reads them automatically
   - Still bills to the GCP project; credits apply if project linked to `01AC33-5BE8AD-2F4E8A`

3. **`gcloud auth application-default login --scopes=.../generative-language`** fails with
   `invalid_scope` because the default gcloud OAuth client does not allow `generative-language.*`
   scopes. Only a custom client you own can request them.

4. **Paid API key is the officially documented primary auth** for Interactions API — OAuth is
   a footnote in the docs. Paid key approach is simpler and produces the same billing result
   if the GCP project is linked to the credit-bearing billing account.

> **Feedback:**

## Track A — Code Fix: Fall Through on 403/429 Scope Error

**Status:** Complete (`322ea4d`, `2baa3ff`)

**Problem:** `_run_deep_research()` currently returns an error on 403 PERMISSION_DENIED
(scope error) rather than falling through to the paid API key. This means if OAuth is
configured but has wrong scope, the fallback never triggers.

**Fix shipped:** `_run_deep_research()` catches 403 and 429 from OAuth submit and falls
through to paid key. Logs: `→ OAuth returned {code} ({reason}) — falling through to paid key`.

**Files:** `src/ai_cli/gemini.py` — `_run_deep_research()`

**Tests:** parametrized over [403, 429]:
- `test_when_oauth_returns_4xx_then_falls_through_to_paid_key`
- `test_when_oauth_returns_4xx_and_no_paid_key_then_error`

> **Feedback:**

## Track B — GCP Custom OAuth Client Setup

**Status:** Complete (2026-04-11)

**Goal:** Set up a custom Desktop-app OAuth client in GCP so `ai gemini -m deep-research`
can authenticate via OAuth (free, using Developer Program credits) instead of API key.

**Steps:**
1. Use CDP Chrome (port 5002) to navigate GCP Console
2. Select/create project linked to billing account `01AC33-5BE8AD-2F4E8A`
3. Enable Generative Language API (APIs & Services → Library)
4. Configure OAuth consent screen (Internal or External, Desktop app type)
5. Create OAuth 2.0 client ID → Desktop app → download `client_secret.json`
6. SCP key to Hetzner (do NOT print contents in conversation)
7. On Hetzner: `~/google-cloud-sdk/bin/gcloud auth application-default login --client-id-file=~/.config/gcp/client_secret.json --scopes='https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/generative-language.retriever'`
8. Test: `ai gemini -m deep-research` should use OAuth without 403
9. No code changes needed — `google.auth.default()` reads ADC automatically

**Completed steps:**
- `agent-browser` installed: `npm install -g agent-browser --ignore-scripts`
- GCP Auth Platform configured for `gen-lang-client-0651020461`
- OAuth 2.0 Desktop app client `ai-cli-gemini-desktop` created
- `client_secret.json` saved to `~/.config/gcp/` on Mac and Hetzner (mode 600)
- Test user `user@example.com` added to Audience
- ADC login run on Mac, credentials SCP'd to Hetzner
- OAuth token confirmed valid with `generative-language.retriever` scope

**Outstanding:** HTTP 429 "prepayment credits depleted" on `gen-lang-client-0651020461`.
Both OAuth and paid key hit this. Root cause: AI Studio project has no prepayment balance.
Action needed: add AI Studio credits at `aistudio.google.com` or verify GCP billing link.
The Track A fallthrough means deep-research will still try paid key automatically.

> **Feedback:**

## Track C — agent-browser Investigation

**Status:** Complete (2026-04-11)

**Root cause:** `agent-browser` is an npm package (`agent-browser@0.25.3`, published by Vercel).
The symlink `/usr/local/bin/agent-browser` existed but pointed to a non-existent target — a
previous install attempt failed during the `postinstall` script (ELOOP — symlink loop), leaving
an orphaned symlink.

**Fix:**
```bash
npm install -g agent-browser --ignore-scripts
```text
`--ignore-scripts` skips the problematic postinstall. The CLI works normally once installed.

**Version:** 0.25.3. CDP usage: `agent-browser --cdp <port> <command>`.

> **Feedback:**

## Track D — R-12 Retry (artelier)

**Status:** Blocked on AI Studio billing issue (Track B outstanding)

**Current state on Hetzner:**
- Registry status: "⚠️ Blocked — OAuth scope error; retry from Mac or with paid key"
- Prompt file: `/tmp/r12_prompt.txt` (still on Hetzner from previous run)
- tmux session: killed (r12-research)

**Unblock path:** Resolve AI Studio 429 billing issue (add prepayment credits or verify GCP
billing link for `gen-lang-client-0651020461`). Once resolved, `ai gemini -m deep-research`
will work — OAuth first, paid key fallback if OAuth hits limits.

**Post-run steps (for artelier session c-r-art-1 to handle):**
- Update R-12 status to ✅ Complete in research registry
- Move registry entry from Pending to Completed section
- Run gap analysis (ART-7)
- Update artist expansion plan doc

> **Feedback:**

## Acceptance Criteria

- [x] `_run_deep_research()` uses paid key directly (OAuth path removed — `12e308d`)
- [x] AI Studio prepayment balance added (\$10); GCP credits absorb actual spend (case #70078827)
- [x] `ai gemini -m deep-research` unblocked on Hetzner
- [x] R-12 research run launched in tmux `r12-research` on Hetzner; c-r-art-1 handed off
- [x] `agent-browser` fixed: `npm install -g agent-browser --ignore-scripts`
- [x] Custom OAuth client and ADC credentials cleaned up (GCP + disk)

## Approval Log

- 2026-04-11 Round 1: Plan drafted mid-session. R-12 run authorized. Paid key use deferred pending
  credit confirmation. GCP OAuth client approach approved in principle. agent-browser investigation
  needed before browser automation can proceed.
- 2026-04-11 Round 2: Tracks A, B, C completed. agent-browser installed. OAuth client created in GCP.
  ADC credentials deployed to Hetzner. 403/429 fallthrough shipped (`322ea4d`, `2baa3ff`).
- 2026-04-11 Round 3: Billing confirmed (GCP support #70078827) — prepay is anti-abuse floor, GCP
  credits absorb actual spend. OAuth path removed (`12e308d`) — no benefit over paid key. \$10 prepay
  added. R-12 launched on Hetzner. ADC + client_secret.json cleaned up on Mac and Hetzner.
  Outstanding: AI Studio `gen-lang-client-0651020461` has no prepayment credits; both OAuth and paid
  key return 429. Client secret exposed in session JSONL — rotation recommended after billing resolved.
