# Gemini Model Guidance

This document defines the rigorous standards for model selection, research workflows, and resiliency within the Gemini CLI fleet.

## Model Routing Table

Follow this table to ensure the appropriate balance of reasoning depth, context capacity, and speed.

| Task Category | Primary Model | Search / Research Type | Rationale |
| :--- | :--- | :--- | :--- |
| **Architecture & Design** | `gemini-3.1-pro-preview` | **Strategic Synthesis** | Requires maximum context window and structural reasoning. |
| **Implementation Plans** | `gemini-3.1-pro-preview` | **Dependency Mapping** | Precision in multi-file dependencies and execution flow. |
| **Complex Research** | `deep-think` (Alias) | **Deep Reasoning** | For topics requiring multi-step logical chains or chain-of-thought. |
| **Intermediate Research** | `gemini-3.1-pro-preview` | **Technical Synthesis** | Balanced technical review (API docs, library comparisons). |
| **Rapid Retrieval** | `gemini-3-flash-preview` | **Quick Lookup** | Fast retrieval of simple technical facts or shell commands. |
| **Initial Triage** | `gemini-3-flash-preview` | **Conversation Start** | Receiving user prompts and delegating to specialized agents. |

## Resiliency & Fallback Chain

If a primary model hits a quota limit (429), repeatedly times out, or returns a capacity error (503), follow this tiered fallback strategy. **Note:** Always inform the user when dropping tiers.

1. **Tier 1 (Deep Think) Failure**: 
   - Fallback to: `gemini-3.1-pro-preview`
   - Tradeoff: Reduced reasoning depth, but maintains high technical accuracy.
2. **Tier 2 (3.1-Pro) Failure**: 
   - Fallback to: `gemini-3-flash-preview`
   - Tradeoff: Reduced context window and creative nuance. Proceed with caution on large files.
3. **Tier 3 (3.x series) Failure**: 
   - Fallback to: `gemini-2.0-flash` (or current 2.x baseline)
   - Tradeoff: Significant drop in capability. Only for basic file ops or rapid triage.
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
