# Instructions

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Core | Python |

| AI orchestration | Claude Code CLI + Gemini CLI |


## Terminology

- **Session config** — files that shape every Claude Code session: `CLAUDE.md`, `MEMORY.md`, `.claude/settings.json`, `.claude/hooks/`
- **Orchestration config** — files that define multi-agent team setup: `.claude/agents/*.md`, `docs/designs/orchestration.md`, `.githooks/`


## Memory Management

**When saving memories, ALWAYS do both steps — never skip the index update:**

1. Write the memory file to `memory/` with frontmatter (name, description, type)
2. **Immediately** add a pointer to `MEMORY.md` — if it's not indexed, it's invisible in future sessions

## Compaction

**PreCompact hook** fires automatically before compaction — run `/save-state` immediately when you see the `SAVE_STATE_REQUIRED` message. This saves session state to memory files and docs before context is lost.

## Common Patterns

- **Scope creep**: Implement only what the spec requires. Do not add features, defensive abstractions, or "nice to have" improvements not in the spec.
- **Config over code**: Prefer configuration files over hardcoded values.
- **Test behavior, not implementation**: Tests assert outcomes through the public API.
- **Persist research**: When doing research (API comparisons, architecture spikes, technology evaluations), save findings to `docs/research/{topic-slug}.md`. Update existing docs rather than creating duplicates.

## Test Requirements




### Python

- Test names: `test_{given}_{when}_{then}`
- Use pytest fixtures for shared setup
- `reviewer` audits test quality on every review (see `.claude/agents/reviewer.md`)
- Run: `pytest`



## Development Workflow

**All feature work follows this pipeline:**

```
Feature Branch → Plan → Implement → /simplify → Checks → UAT → PR → Merge
```

### Branch Strategy

- All dev work branches from `main`: `feature/short-description`
- Never push feature branches directly to `main` — always PR
- Atomic commits

- Non-dev changes (docs, tooling, markdown) commit directly to `main`

### Implementation Pipeline

| Phase | Gate |
|-------|------|
| **Plan** | **Human approves plan** before coding starts |
| **Implement** | Tests must pass locally |
| **Simplify** | Run `/simplify` — scope creep, dead code, over-engineering removed |
| **Automated Checks** | **Hard gate** — `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest` must pass. Always run **after** `/simplify` (it modifies code). |
| **UAT** | **Human approves** before PR |
| **PR** | Open PR to `main` |

### AI Slop Checklist (enforced by /simplify)

Before presenting for UAT, verify none of these exist:

- Unnecessary wrapper types or abstractions not in the spec
- Builder patterns where a simple constructor suffices
- Error types that will only ever have one variant
- Defensive code for scenarios the spec explicitly excludes
- Verbose comments restating what the code already says
- Feature flags or configuration for things that don't vary
- TODO/FIXME comments used as placeholders

### UAT Presentation Format

When presenting for UAT, always use this template:

```
## UAT Summary

### What was built — [1-3 bullet points]
### Files changed — [list of files created/modified]
### Test results — [test output summary]
### How to verify — [steps the human can take to manually test]
### Acceptance criteria status — [x] Criterion 1 ...
```


## Document Structure

All markdown docs with 3+ sections must include a `## Table of Contents` as the first section — immediately after the title/header block, before any other content. Use anchor links to all `##` and `###` headings. Plan docs must include an `## Approval Log` section tracking decisions. Update plan docs as work progresses — they are living documents.

## File Naming

- **Standard names** (ALL_CAPS): README, CLAUDE, GEMINI, SKILL, MEMORY, SCRATCH
- **All other markdown docs**: kebab-case (e.g., `api-design.md`, `migration-plan.md`)

## MCP Servers

Required: `github`, `gemini-cli`.


## AI Orchestration

Claude is the lead — it owns the conversation, writes code, uses tools, and makes final decisions. Gemini is a specialist invoked via `mcp__gemini-cli__ask-gemini` for research, review, and analysis. Agent teams coordinate multiple Claude agents for parallel work.

### Operating principles

1. **Single responsibility.** Each agent does one thing. Research agents research. Engineers write code. Reviewers review.
2. **Read-write separation.** Only `engineer` can modify code. All others are read-only. This makes parallel agents safe.
3. **Research before implementation.** Launch research first. Wait for findings. Then implement. Two-phase workflows beat jumping straight to code.
4. **Cross-validation for high-stakes decisions.** Get independent perspectives (multiple agents, or Claude + Gemini) and synthesize. Different models catch different gaps.
5. **External context once at the start.** Read Jira, docs, or API specs once. Store it. Don't re-fetch from external sources in every agent.
6. **Human gates at decision points.** Pause for approval before committing, PRs, or architecture changes. Automate work between gates, not the gates.
7. **Tasks as the coordination primitive.** Agents coordinate through a shared task list with owners, statuses, and dependencies — not free-form chat.
8. **Right-size your team.** Start with 2-3 agents. Only add more for genuinely parallel, independent work.
9. **Fail fast.** Report blockers immediately. Don't retry silently or loop.
10. **Clean up.** Shut down teammates and delete team resources when done.

### Gemini CLI

**Always pass an explicit `model` argument.** Do not rely on defaults. On quota exhaustion, fall down the tier list. `gemini-3.0-flash` is the universal safety net.

```
mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3.0-flash")
```

Models ranked by capability: `gemini-3.1-pro-preview` (preview, high quota risk) → `gemini-3.0-pro` (flagship, high) → `gemini-3.0-flash` (workhorse, low) → `gemini-2.5-pro` (previous gen, moderate) → `gemini-2.5-flash` (bulk, low) → `gemini-2.5-flash-lite` (simple extraction, minimal).

### Agents

Custom agents in `.claude/agents/` — each file defines its role, tools, model, and Gemini fallback chain. Agent teams enabled via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`.

| Agent | Claude Model | Role | Edits Code |
|-------|-------------|------|:---:|
| `researcher` | `opus` | Deep research, API docs, library comparisons | No |
| `architect` | `opus` | System design, tradeoff analysis, design docs | No |
| `engineer` | `opus` | Write code, run tests, fix bugs, commit | Yes |
| `reviewer` | `sonnet` | Code review, quality audit, security review | No |
| `planner` | `sonnet` | Scope estimation, task breakdown, dependencies | No |
| `analyst` | `sonnet` | Data extraction, summarization, SQL queries | No |

### Team compositions

| Task | Team Name | Teammates | Flow |
|------|-----------|-----------|------|
| Feature implementation | `feature` | researcher, engineer, reviewer | researcher finds docs → engineer implements → reviewer audits diff |
| Architecture / design | `design` | researcher, architect, planner | researcher explores alternatives → architect designs → planner writes stories |
| Code review | `review-PR-NNN` | reviewer, researcher | both review in parallel → lead merges findings |
| Bug investigation | `fix` | researcher, engineer | both investigate in parallel → engineer applies fix |
| Sprint planning | `sprint-planning` | planner, analyst | analyst summarizes state → planner writes and links stories |

### Context management & team workflow

Summarize context before spawning teams. Spawn prompts are self-contained (teammates don't inherit conversation). After each phase, persist decisions to files. See `docs/designs/orchestration.md` § Context Management.

1. **Create team** → **Spawn teammates** → **Create tasks** with dependencies
2. **Teammates work** — claim tasks, complete them, report back
3. **Lead synthesizes** — merge findings, approve next steps
4. **Shutdown** — `SendMessage` shutdown to each teammate, then `TeamDelete`

