# ai-cli-utils — AI Orchestration Design

> **Version:** 0.1.0
> **Status:** Active

## Overview

This project uses a Claude-native multi-agent orchestration setup where Claude Code is the lead and Gemini CLI is a specialist partner. Six custom agents with defined roles, tool restrictions, and model assignments coordinate through shared task lists.

## Architecture

```text
┌──────────────────────────────────────────────────────┐
│                     HUMAN (you)                       │
│              Reviews, approves, steers                │
└────────────────────────┬─────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────┐
│                  CLAUDE CODE (lead)                   │
│          Owns conversation, makes decisions           │
│          Spawns teams, assigns tasks, synths          │
├──────────────┬───────────────────┬───────────────────┤
│  Agent tool  │  Gemini MCP tool  │  Task tools       │
│  (teammates) │  (ask-gemini)     │  (coordination)   │
└──────┬───────┴─────────┬─────────┴───────┬───────────┘
       │                 │                 │
       ▼                 ▼                 ▼
┌────────────┐  ┌──────────────┐  ┌──────────────────┐
│  Teammate  │  │  Gemini CLI  │  │  Shared Task List │
│  agents    │  │  (research,  │  │  (TaskCreate,     │
│  (.claude/ │  │   review,    │  │   TaskUpdate,     │
│   agents/) │  │   analysis)  │  │   TaskList)       │
└────────────┘  └──────────────┘  └──────────────────┘
```text

## Operating Principles

These 10 principles govern all multi-agent work in this project:

1. **Single responsibility** — each agent does one thing well
2. **Read-write separation** — only `engineer` edits code; all others are read-only
3. **Research before implementation** — research first, wait for findings, then implement
4. **Cross-validation** — get independent perspectives for high-stakes decisions
5. **External context once** — fetch docs/APIs once at the start, not per-agent
6. **Human gates** — pause for approval before commits, PRs, and architecture changes
7. **Tasks as coordination** — shared task list with owners and dependencies, not free-form chat
8. **Right-size teams** — start with 2-3 agents, scale only for genuinely parallel work
9. **Fail fast** — surface blockers immediately, don't retry silently
10. **Clean up** — shut down teammates and delete team resources when done

## Agents

Six custom agents defined in `.claude/agents/`:

| Agent | Model | Role | Edits Code |
|-------|-------|------|:---:|
| `researcher` | `opus` | Deep research, API docs, library comparisons, multi-source synthesis | No |
| `architect` | `opus` | System design, tradeoff analysis, design docs, schema design | No |
| `engineer` | `opus` | Write code, run tests, fix bugs, refactor, commit | Yes |
| `reviewer` | `sonnet` | Code review, quality audit, security review, slop check | No |
| `planner` | `sonnet` | scope estimation, dependency mapping | No |
| `analyst` | `sonnet` | Data extraction, summarization, classification, SQL queries | No |

### Tool access by agent

| Tool Category | researcher | architect | engineer | reviewer | planner | analyst |
|--------------|:---:|:---:|:---:|:---:|:---:|:---:|
| Read, Glob, Grep | Y | Y | Y | Y | Y | Y |
| Edit, Write | - | - | Y | - | - | - |
| Bash | Y | Y | Y | Y | Y | Y |
| WebFetch, WebSearch | Y | Y | - | - | - | - |
| Gemini CLI | Y | Y | Y | Y | Y | Y |
| SQLite (read) | - | - | - | - | - | Y |

### Gemini model selection per agent

- **Research-heavy agents** (researcher, architect) start with `gemini-3.1-pro-preview` or `gemini-3.0-pro`
- **Execution-heavy agents** (engineer, reviewer) start with `gemini-3.0-pro`
- **Analysis/planning agents** (planner, analyst) start with `gemini-3.0-flash`
- **Universal fallback**: `gemini-3.0-flash` handles nearly any task if higher-tier models hit quota

## Team Compositions

Five pre-defined team configurations:

### Feature implementation (`feature`)

```yaml
Teammates: researcher → engineer → reviewer
```text

- **Phase 1**: researcher finds API docs, usage examples, prior art
- **Phase 2**: engineer implements using research findings
- **Phase 3**: reviewer audits the diff before PR
- **When to use**: any task that requires new code

### Architecture / design (`design`)

```yaml
Teammates: researcher → architect → planner
```text

- **Phase 1**: researcher explores alternatives, finds prior art, benchmarks
- **Phase 2**: architect evaluates tradeoffs, produces design doc
- **Phase 3**: planner breaks design into tasks with dependencies
- **When to use**: new modules, major refactors, tech stack decisions

### Code review (`review-PR-NNN`)

```yaml
Teammates: reviewer + researcher (parallel)
```text

- reviewer checks quality, style, slop, security
- researcher verifies external API usage, checks docs for correctness
- Lead merges both reports
- **When to use**: any PR review

### Bug investigation (`fix`)

```yaml
Teammates: researcher + engineer (parallel)
```text

- researcher searches for known issues, CVEs, related upstream bugs
- engineer traces local code paths, reads stacktraces
- Engineer applies fix based on combined findings
- **When to use**: any bug report

### Sprint planning (`sprint-planning`)

```yaml
Teammates: analyst → planner
```text

- analyst summarizes current project state, extracts metrics from DB

- planner writes stories, estimates, links dependencies
- **When to use**: beginning of a new phase or sprint

## Team Workflow

```text
1. TeamCreate("feature")
2. Agent(name="researcher", team_name="...", prompt="Research...")
   Agent(name="engineer", team_name="...", prompt="Implement...")
3. TaskCreate(subject="Research APIs", description="...")
   TaskCreate(subject="Implement feature", description="...", blocked_by=["task-1"])
4. TaskUpdate(task_id="task-1", owner="researcher", status="in_progress")
5. [Teammates work, complete tasks, report back via messages]
6. Lead reviews, synthesizes, approves
7. SendMessage(target="researcher", type="shutdown_request")
   SendMessage(target="engineer", type="shutdown_request")
   TeamDelete()
```text

## Claude + Gemini Coordination

Claude and Gemini play complementary roles. Claude leads; Gemini advises.

| Activity | Claude does | Gemini does |
|----------|------------|-------------|
| Feature work | Writes and commits code | Finds API docs, usage examples |
| Bug fix | Traces code, applies fix | Searches for known issues externally |
| Design | Makes final design decisions | Researches alternatives, challenges assumptions |
| Code review | Checks architecture, correctness | Checks style, security, edge cases |
| Planning | Defines stories and acceptance criteria | Validates scope, flags gaps |
| Docs | Writes docs matching the codebase | Verifies external references |

**Rules:**
- Claude always makes the final call
- Launch Gemini research early — it runs in parallel while Claude works locally
- Don't duplicate work — if Gemini is researching, Claude doesn't also web-search
- Gemini results are input, not output — Claude synthesizes before presenting to user

## Context Management

### Lead session hygiene

- **Summarize before spawning.** Before creating a team, distill the current task into 2-3 sentences.
- **Write findings to files.** After each team phase completes, persist key decisions and findings to memory topic files or docs.
- **Recover from files.** When context feels heavy, read MEMORY.md and relevant docs to rebuild state.

### Agent spawn prompts

- **Self-contained.** Each spawn prompt includes everything the agent needs. Teammates don't inherit the lead's conversation.
- **Reference files for large context.** Point agents to file paths rather than pasting content inline.
- **Scope tightly.** Only include what the agent's specific task requires.

### Memory discipline

- **MEMORY.md under 200 lines.** Use topic files in `memory/` for details, link from MEMORY.md.
- **Update at milestones.** Write memories when a phase completes or a decision is made, not mid-task.
- **Remove stale entries.** When information is superseded, update or delete the old entry.

## File Locations

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Project-wide instructions (auto-loaded every session) |
| `.claude/settings.json` | Agent teams enabled, permissions, hooks |
| `.claude/agents/*.md` | Custom agent definitions (6 agents) |
| `.claude/hooks/enforce-workflow.sh` | Claude PreToolUse hook (branch naming) |
| `.githooks/pre-commit` | Markdown lint on staged files |
| `.githooks/pre-push` | markdown lint, tests |
