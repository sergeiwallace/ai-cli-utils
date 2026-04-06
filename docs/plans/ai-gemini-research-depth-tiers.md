---
title: "Implementation Plan — ai gemini Research Depth Tiers (--depth flag)"
category: plan
tags: [ai-cli, ai-gemini, research-pipeline, depth-tiers, track-a]
status: approved
source: session-2026-04-04
related_docs:
  - ~/projects/sergei/docs/research/gemini-research-pipeline-synthesis.md
  - ~/projects/sergei/docs/research/deep-research-pipeline-design.md
---

# Implementation Plan — `ai gemini` Research Depth Tiers

**Research basis:** `~/projects/sergei/docs/research/gemini-research-pipeline-synthesis.md` (R-61 + R-62)

## Problem

The current `ai gemini` command is a single-shot call: prompt → model → output. This has a hard ceiling: the model cannot search for information it doesn't have during its verification phase. Intrinsic self-correction fails 64.5% of the time (Wu et al. 2024) without external tool grounding. For research prompts that require up-to-date or multi-source information, single-shot `deep-think` confidently produces plausible but unverifiable outputs.

## Proposed Solution

Add a `--depth` flag to `ai gemini` with three tiers, implemented as a pure Python orchestration wrapper — no LangGraph, no new service dependencies beyond what's already available.

**Default search method:** Gemini native grounding (built into the Gemini API via `tools=[{"googleSearch": {}}]`) — already included in the existing Google AI Ultra subscription, zero additional cost. Third-party search providers (Tavily, Firecrawl, Serper) are optional config overrides, not defaults. See Search Provider section below.

---

## Options

### Option 1 — Three-tier `--depth` flag (recommended)

Implement `--depth quick|standard|deep` as a configurable orchestration layer in `ai-cli-utils`, modelled on aido's depth config/preset structure for consistency across the platform.

**`--depth quick` (default — current behavior)**
- Single model call: `prompt → model → output`
- No changes to existing behavior; `quick` is the default when `--depth` is omitted
- No search, no extra API calls, no additional cost
- Best for: logic, math, coding, internal reasoning tasks

> **Feedback:**

---

**`--depth standard` (Planner-Executor, ~2x tokens, 2 model calls)**
1. Model (configurable, see OQ-3) generates 3-5 structured JSON search queries from the prompt
2. CLI executes queries via Gemini native grounding concurrently, collects results as Markdown
3. Synthesis model (`deep-think` by default) takes results + original prompt, runs CoVe and produces final output

Best for: competitive analysis, library/framework comparisons, API docs, factual research

> **Feedback:**

---

**`--depth deep` (Recursive, ~4x tokens, 3-4 model calls)**
1. Model generates initial search queries
2. CLI runs searches via Gemini native grounding
3. Model (Reflection turn): reviews first batch, identifies gaps, emits follow-up queries
4. CLI runs follow-up searches
5. Synthesis model: final CoVe synthesis across all gathered context

Best for: broad exploratory surveys, multi-source technical deep dives, strategic analysis

> **Feedback:**

---

**Pros:** CLI-native, composable with existing flags (`-m`, `-o`, `--quiet`), no new mandatory dependencies, linear and debuggable, no extra subscription cost using default search provider
**Cons:** `standard` and `deep` have higher latency and token cost than `quick`

---

### Option 2 — Prompt engineering improvements only (not recommended)

**Pros:** Zero new dependencies, zero cost increase
**Cons:** Doesn't solve the fundamental ceiling — model still can't access information it doesn't have.

---

### Option 3 — LangGraph-based (not recommended)

Heavy dependency for a CLI command; couples `ai-cli` to `aido` graph logic.

---

## Recommendation

**Option 1** — Start with `standard` only, evaluate quality lift on 3-5 real runs, then add `deep`.

---

## Depth Config Structure

Mirror aido's depth config/preset structure (`aido/configs/research_normal.yaml`, `aido/src/aido/research_config.py`). Each preset specifies all configurable parameters explicitly.

Config file location: `~/.config/ai-cli/research.yaml`

```yaml
depth_presets:
  quick:
    model: deep-think
    search_provider: null       # no search
    search_rounds: 0
    planning_model: null
    reflection_rounds: 0

  standard:
    model: deep-think           # synthesis model
    search_provider: gemini     # gemini native grounding (default, free)
    search_rounds: 1
    planning_model: deep-think  # model used to generate queries (see OQ-3)
    reflection_rounds: 0
    retries:
      cli_timeout_retries: 1
      step_node_retries: 2

  deep:
    model: deep-think
    search_provider: gemini
    search_rounds: 2
    planning_model: deep-think
    reflection_rounds: 1
    retries:
      cli_timeout_retries: 1
      step_node_retries: 2
```

---

## Search Provider Configuration

**Default:** Gemini native grounding — included in the Google AI Ultra subscription, no additional cost.

**Optional providers** (not defaults — require separate API keys; implement as follow-up tasks SW-762, SW-763):

| # | Provider | Cost | Notes |
|---|----------|------|-------|
| 1 | Tavily | ~$0.01/search | Purpose-built for LLM agents; clean Markdown |
| 2 | Firecrawl | ~$0.01/page | Better for scraping known URLs |
| 3 | Serper | ~$0.001/search | Cheapest; Google results; raw JSON |

To override: `--search-provider tavily|firecrawl|serper` (or set in depth preset config).

---

## OQ-3: Model selection per depth step — resolved

`planning_model` is a configurable field in each depth preset. Default to `deep-think` for both planning and synthesis initially. After `standard` ships: test `pro`/`flash` vs `deep-think` for query generation step, update preset defaults from findings. Expose as `--planning-model MODEL` CLI override for experimentation.

---

## OQ-4: OAuth timeout handling — resolved (B + C)

**Per-step checkpointing (C):** Save state to disk after every orchestration step as a JSON snapshot.
- Location: `~/.local/state/ai-cli/research-runs/<run-id>/`
- Per-step files: `step_01_query_generation.json`, `step_02_search_results.json`, `step_03_synthesis.json`
- Resume via `ai gemini --resume <run-id>` — picks up from last completed step, no full re-run

**Auto-fallback to REST (B):** Apply `invoke_with_fallback()` pattern (ref: `aido/src/aido/research_config.py:332–559`) to every Gemini call in the orchestration:
1. Try OAuth CLI with timeout
2. Timeout → retry once with 1.5x backoff
3. Capacity error → 30s cooldown, skip to REST
4. Quota exhaustion → circuit-breaker for model family
5. REST API: free tier (`GOOGLE_API_KEY_FREE_TIER`) first, then paid tier (`GOOGLE_API_KEY_TIER_1`)

REST budget only charged if OAuth truly fails after retries.

---

## Implementation Steps

Order by dependency:

1. Review aido's depth config schema — mirror structure for `research.yaml` (ref: `aido/configs/research_normal.yaml`, `aido/src/aido/research_config.py`)
2. Add `--depth` flag to `ai gemini` CLI argument parser (`quick` default); add `--resume <run-id>` flag
3. Define `~/.config/ai-cli/research.yaml` depth preset config with `quick`, `standard`, `deep` presets (including `retries` section)
4. Implement per-step checkpoint/resume: JSON snapshots at `~/.local/state/ai-cli/research-runs/<run-id>/`
5. Implement per-step fallback: `invoke_with_fallback()` pattern for each Gemini call (CLI → REST, mirroring aido)
6. Implement Gemini native grounding search integration (`tools=[{"googleSearch": {}}]`)
7. Implement `standard` orchestration: query generation → concurrent search → synthesis
8. Write tests: query generation output format; synthesis receives search context; mock grounding API; checkpoint written after each step; resume skips completed steps
9. Ship `standard` tier; run on 3-5 real research prompts, evaluate quality
10. Test `planning_model` variants (`pro`, `flash` vs `deep-think`) — update preset defaults
11. Implement `deep` tier (add Reflection turn)
12. Update `ai gemini --help` and relevant docs

**Project:** `ai-cli-utils` (Hetzner CC session)

---

## Phase 2 Enhancements (Post-Shipping Phase 1)

Informed by R-66 (2026 gap analysis, 2026-04-06). Not blocking Phase 1 implementation — ship `--depth standard` and `--depth deep` first, then evaluate these enhancements.

### P2-1: IEW Voting for `--depth deep` (arXiv:2601.12707)

When `--depth deep` explores multiple sequential refinement paths (or if a future `--depth exhaustive` tier runs parallel branches), aggregate via **Inverse-Entropy Weighted (IEW) voting** rather than taking the last output or simple majority. IEW assigns higher weight to lower-entropy (higher-confidence) outputs.

*Impact:* Higher accuracy on multi-path deep tier. Low implementation effort once `--depth deep` ships.

### P2-2: Hierarchical Search Provider (arXiv:2602.03442)

Current plan uses a single search modality (Gemini native grounding). A-RAG validated that exposing multiple retrieval modalities improves coverage on complex queries. Future enhancement to `search_provider` config:

```yaml
search_providers:
  - type: keyword        # Gemini native grounding or Serper (fast, named entities)
  - type: semantic       # Future: embedding-based search if provider supports it
  - type: direct_read    # Firecrawl — for known URLs or specific documents
```

Allow the planning model to select modality per query via structured JSON output:
`{"tool": "keyword|semantic|direct_read", "query": "..."}`

*Impact:* Better coverage on research tasks with mixed query types. Depends on SW-762/SW-763 (Tavily/Firecrawl providers) shipping first.

### P2-3: Token-Level Fault Tolerance (Once-More, ICLR 2026)

Once-More (ICLR 2026) shows in-generation perplexity-based correction is effective but requires deep model integration. **Not feasible via standard Gemini API.** Our per-step JSON checkpointing remains the right fault-tolerance ceiling for a CLI wrapper. No action needed — confirming existing approach is correct.

---

## Approval Log

| Date | Round | Decisions / Approvals |
|------|-------|----------------------|
| 2026-04-04 | Round 1 | Plan proposed |
| 2026-04-04 | Round 2 | OQ-1: Gemini native grounding as default; Tavily/Firecrawl/Serper as optional follow-up tasks (SW-762, SW-763). OQ-2: `quick` stays default. OQ-3: configurable `planning_model` per preset; test `pro`/`flash` vs `deep-think` after `standard` ships; mirror aido depth config structure. New OQ-4 added. |
| 2026-04-05 | Round 3 | OQ-4 resolved: B + C — per-step JSON checkpointing + per-call OAuth→REST auto-fallback mirroring aido's `invoke_with_fallback()`. `--resume <run-id>` flag added. |
| 2026-04-05 | Round 4 | Plan approved. Moved to ai-cli-utils project for implementation. |
| 2026-04-06 | Round 5 | Phase 2 enhancements added based on R-66 (2026 gap analysis): P2-1 IEW voting for --depth deep, P2-2 hierarchical search provider, P2-3 token-level fault tolerance (confirmed no action — existing checkpointing is correct ceiling). |
