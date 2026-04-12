# Gemini Model Guidance

This document defines the rigorous standards for model selection, research workflows, and resiliency within the Gemini CLI fleet.

## Model Routing Table

Follow this table to ensure the appropriate balance of reasoning depth, context capacity, and speed.

| Task Category | Primary Model | Search / Research Type | Rationale |
| :--- | :--- | :--- | :--- |
| **Architecture & Design** | `deep-think` (Alias) | **Tradeoff Exploration** | HIGH thinking explores multiple design paths and failure modes before committing. Use `pro` for straightforward architecture tasks without deep tradeoffs. |
| **Implementation Plans** | `gemini-3.1-pro-preview` | **Dependency Mapping** | Precision in multi-file dependencies and execution flow. For complex cross-service plans (3+ files, new services), consider `deep-think`. |
| **Complex Research** | `deep-think` (Alias) | **Deep Reasoning** | For topics requiring multi-step logical chains or chain-of-thought. |
| **Intermediate Research** | `gemini-3.1-pro-preview` | **Technical Synthesis** | Balanced technical review (API docs, library comparisons). |
| **Rapid Retrieval** | `gemini-3-flash-preview` | **Quick Lookup** | Fast retrieval of simple technical facts or shell commands. |
| **Initial Triage** | `gemini-3-flash-preview` | **Conversation Start** | Receiving user prompts and delegating to specialized agents. |

## API Key Tier Model Availability

`ai gemini` uses a 3-tier auth fallback chain. **Not all models are available on all tiers.**

| Tier | Auth Method | Models Available | Notes |
|------|-------------|-----------------|-------|
| 1 | OAuth (Gemini CLI) | All models except Deep Research | Free via Google AI consumer subscription. **Deep Research is not available via OAuth** — the CLI OAuth token is scoped to the Code Assist backend and cannot reach the Interactions API. Use tier 3 for Deep Research. |
| 2 | `GOOGLE_API_KEY_FREE_TIER` | Flash family only (Gemini 3 Flash, 3.1 Flash-Lite, 2.5 Flash, 2.0 Flash, Gemma 4) | **Gemini 3.1 Pro has no free quota tier.** Using tier 2 for Pro/deep-think/deep-research will fail immediately with a billing error, not a 429. |
| 3 | `GOOGLE_API_KEY_TIER_1` | All models | Paid, billed per token. Deep Research ~$2–5/task. |

**Key rule:** For Pro, deep-think, or deep-research calls where OAuth fails — use `-s 3`, not `-s 2`. Free-tier key cannot serve these models.

Free tier model list (verified 2026-04-10, source: [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)):
- ✅ Free: Gemini 3 Flash Preview, 3.1 Flash-Lite Preview, 2.5 Flash, 2.5 Flash-Lite, 2.0 Flash, 2.0 Flash-Lite, Gemma 4, Gemini Embedding, Gemini Robotics-ER 1.5 Preview
- ⚠️ Limited free: Gemini 2.5 Pro (limited availability)
- ❌ No free tier: Gemini 3.1 Pro Preview, Imagen 4, Veo models, Lyria 3

## Resiliency & Fallback Chain

If a primary model hits a quota limit (429), repeatedly times out, or returns a capacity error (503), follow this tiered fallback strategy. **Note:** Always inform the user when dropping tiers.

1. **Tier 1 (Deep Think) Failure**:
   - Fallback to: `gemini-3.1-pro-preview`
   - Tradeoff: Reduced reasoning depth, but maintains high technical accuracy.
2. **Tier 2 (3.1-Pro) Failure**:
   - Fallback to: `gemini-3-flash-preview`
   - Tradeoff: Reduced context window and creative nuance. Proceed with caution on large files.
3. **Tier 3 (3.x series) Failure**:
   - Fallback to: `gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite` (in sequence)
   - Tradeoff: Significant capability drop. Exhausting both 3.1-pro AND 3-flash is rare — treat 2.5 as emergency fallback only. No use-case delineation needed at this tier.
4. **Platform-Wide Outage**:
   - Transition to **Claude Code** (check quota first) or wait for reset.

## Documentation Standards

### Single Document Authority
- **No Phase Fragmenting**: Architecture, design, and implementation plans must be comprehensive and live in a single file.
- **Filename Convention**: Never include phase numbers in the filename (e.g., `fleet-management-phase1.md` is an anti-pattern).
- **Internal Phasing**: Detail multiple phases within the document using headers (`## Phase 1`, `## Phase 2`).
- **Context Integrity**: Maintaining the full project scope in a single file ensures agents remain aware of future states while implementing current tasks.

## Fleet Operations

### Session Naming
- **Format**: `<engine>-<project>-<optional_tag>-N` (e.g., `gemini-sw-research-1`).
- **Index logic**: Always use the lowest available index (`N`) based on active tmux sessions (gap-filling).
- **Cleaning**: Automatically sanitize double-hyphens and redundant prefixes.

### Refresh Signals
- **`/memory reload`**: Use for `GEMINI.md` or instructional context updates. Instant, no restart.
- **`Shift+R` (Restart)**: Use for `settings.json`, tool policies, or experimental flag changes.
