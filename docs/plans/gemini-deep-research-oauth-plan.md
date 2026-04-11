---
title: Gemini Deep Research — OAuth Fix & GCP Client Setup
category: plan
tags: [gemini, oauth, deep-research, gcp, ai-cli-45]
status: in_progress
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
- Google Developer Program Premium $100/mo credit IS confirmed linked to that billing account
- Every deep-research run since AI-CLI-36 has been using the paid key (no free OAuth path existed)

**Billing account:** `01AC33-5BE8AD-2F4E8A` — confirmed in GCP → Billing → Credits with
Developer Program Premium credit applied. Cost per deep-research task is likely well under $1
(total spend $7.61 for multiple runs this month).

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

## Track A — Code Fix: Fall Through on 403 Scope Error

**Status:** Not started

**Problem:** `_run_deep_research()` currently returns an error on 403 PERMISSION_DENIED
(scope error) rather than falling through to the paid API key. This means if OAuth is
configured but has wrong scope, the fallback never triggers.

**Fix:** In `_run_deep_research()`, catch 403 from the submit call and fall through to
paid key (same as the "OAuth unavailable" path). Add a clear log message:
`→ OAuth returned 403 (insufficient scope) — falling through to paid key`

**Files:** `src/ai_cli/gemini.py` — `_run_deep_research()`

**Tests needed:**
- `test_when_oauth_returns_403_then_falls_through_to_paid_key`
- `test_when_oauth_returns_403_and_no_paid_key_then_error`

> **Feedback:**

## Track B — GCP Custom OAuth Client Setup

**Status:** In progress (blocked on agent-browser)

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

**Blocker:** `agent-browser` CLI not found — see Track C.

**Alternative if OAuth setup too complex:** Simplify to just doing Track A (paid key fallthrough)
and skipping OAuth setup. Deep-research costs ~$0.30-1 per task with credits applied; minimal
real cost.

> **Feedback:**

## Track C — agent-browser Investigation

**Status:** Not started

**Problem:** `agent-browser` command not found in PATH on Mac. CLAUDE.md documents it as
available but it's not installed anywhere obvious (not in npm global, not in ~/.local/bin,
not in ~/ACLI/).

**Investigation needed:**
1. Check if it's a separate CLI package that needs installation
2. Check npm global packages: `npm list -g | grep browser`
3. Check pip packages: `pip list | grep agent`
4. Check ~/ACLI/ directory contents in detail
5. Check if it's part of the `claude-plugins` or some other CC plugin
6. Check CC settings.json for any plugin/tool registration
7. If not installed: find install instructions and install it

**Alternative for GCP Console work:** `npx playwright` with CDP endpoint for browser automation

> **Feedback:**

## Track D — R-12 Retry (artelier)

**Status:** Blocked on Track A or B

**Current state on Hetzner:**
- Registry status: "⚠️ Blocked — OAuth scope error; retry from Mac or with paid key"
- Prompt file: `/tmp/r12_prompt.txt` (still on Hetzner from previous run)
- tmux session: killed (r12-research)

**Unblock path:**
- Option 1 (preferred): Complete Track B → retry with OAuth
- Option 2: Complete Track A → paid key fallback works → retry with paid key (approved if credit confirmed)

**Post-run steps (for artelier session c-r-art-1 to handle):**
- Update R-12 status to ✅ Complete in research registry
- Move registry entry from Pending to Completed section
- Run gap analysis (ART-7)
- Update artist expansion plan doc

> **Feedback:**

## Acceptance Criteria

- [ ] `_run_deep_research()` falls through to paid key on HTTP 403 (not just on OAuth None)
- [ ] `ai gemini -m deep-research` on Hetzner completes successfully (either OAuth or paid key)
- [ ] R-12 research doc landed in artelier at `docs/research/artist-expansion-survey-2026.md`
- [ ] Handoff sent to c-r-art-1
- [ ] `agent-browser` either found/fixed or workaround documented

## Approval Log

- 2026-04-11 Round 1: Plan drafted mid-session. R-12 run authorized. Paid key use deferred pending
  credit confirmation. GCP OAuth client approach approved in principle. agent-browser investigation
  needed before browser automation can proceed.
