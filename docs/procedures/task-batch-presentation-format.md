# Task Batch Presentation Format

> Standard format for presenting "what's next" / "what do we have to work on" overviews.
>
> Last updated: 2026-03-18

## When to Use

Any time the user asks what work is available, what's next, or requests a task overview. This is the **input** format (what to work on). For **output** format (what was done), see `autonomous-completion-format.md`.

## Format

### Section 1: Full P0-P3 Task Tables

One table per priority tier. Columns:

| Column | Description |
|--------|-------------|
| # | Sequential number across all tables (for easy reference) |
| Task | Bold name + brief description |
| Complexity | XS / S / M / L |
| Recommended Model | Opus, Sonnet, Haiku, Gemini CLI, or "You" (human gate) |
| Autonomous? / Token-cheap? | Flag whether Claude can do it without human gates, and whether it's low-cost |

Group by priority: P0 first, then P1, P2, P3. Include all open tasks from the roadmap.

### Section 2: Docs Awaiting Review

Table showing all docs that need the user's feedback/approval to unblock work.

| Column | Description |
|--------|-------------|
| # | Sequential number |
| Doc | File path (clickable) |
| Pending | Number of pending decisions/questions |
| Blocks | What implementation work is gated on this review |

### Section 3: Recommended Next Batch

Group recommended tasks by model tier, cheapest first:

1. **Gemini CLI work** (free, zero Claude tokens) — table with #, Task, What Gemini does
2. **Sonnet work** (low token cost) — table with #, Task, What Sonnet does
3. **Opus work** (only if needed) — table with #, Task, Why Opus

### Section 4: Recommendation

Clear 1-2 sentence recommendation of what to start with and why. Always lead with the cheapest effective option.

## Rules

1. **Always include model recommendation per task** — this is the primary differentiator from a plain task list.
2. **Gemini CLI and Sonnet before Opus** — present cheaper options first, Opus only when justified.
3. **Flag human gates clearly** — tasks that need user input before Claude can start get "You" as the model.
4. **Note what's blocked** — if a task can't start until a doc is reviewed, say so inline.
5. **Keep it scannable** — tables over prose. The user should be able to pick a task in under 30 seconds.

## Session Status Variant

A lightweight hybrid for mid-session check-ins ("where are we?", "give me a status update") or at compaction/save-state boundaries. Combines backward-looking progress with forward-looking next steps.

### Format

```markdown
## Session Status

### Completed this session
- [bulleted list of tasks/items completed]

### In progress
- [current task + progress notes]

### Blocked / needs your input
- [decisions, doc reviews, or human gates needed]

### Recommended next steps
- [2-3 options with model recommendations, cheapest first]
- **Recommendation:** [which and why]
```text

### When to use

- User asks "status?", "where are we?", or "what's going on?"
- At compaction or context save boundaries
- After completing a task but before the user picks the next one
- Lighter than the full task batch format — skip the full P0-P3 roadmap scan

For a full roadmap overview, use the main Task Batch format above. For results after autonomous work, use `autonomous-completion-format.md`.

## Anti-Patterns

- **Listing tasks without model recommendations** — the whole point is cost-aware routing.
- **Putting Opus work first** — always lead with what's cheapest.
- **Omitting the docs-awaiting-review section** — blocked docs are the #1 velocity bottleneck.
- **Mixing autonomous and human-gated tasks** — flag which is which so the user knows what they can delegate vs what needs their attention.
