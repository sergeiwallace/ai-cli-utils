---
title: "Claude Token Efficiency Guide"
category: procedures
tags: [claude, tokens, efficiency, models, quota]
status: active
source: sergei
---

# Claude Token Efficiency Guide

## Model Selection

Switch with `/model <name>` or start with `claude --model sonnet`.

| Model | Relative Cost | Use for | Target % of work |
|-------|:-------------:|---------|:----------------:|
| **Haiku** | 1x | File reads, glob/grep, explaining errors, formatting, boilerplate | ~15% |
| **Sonnet** | 3x | Standard implementation, tests, bug fixes, doc updates, propagation, routine refactors | ~70% |
| **Opus** | 5x | Architecture decisions, complex multi-file refactors, tricky cross-service debugging | ~15% |

**Default to Sonnet.** Only escalate to Opus when Sonnet gets stuck or the task is genuinely architectural. Routine work at Opus prices is the #1 source of unnecessary token burn.

Set global default:
```bash
claude --model sonnet
# or
export ANTHROPIC_MODEL="sonnet"
```

## Effort Levels

Switch with `/effort <level>` or set globally in `~/.claude/settings.json` as `"effortLevel": "medium"`.

| Level | Token Impact | Use for |
|-------|:------------:|---------|
| `low` | ~60-70% cheaper | Mechanical tasks, formatting, simple lookups |
| `medium` | Balanced | Good general default (currently set) |
| `high` | Full reasoning | Complex architecture, debugging, design |
| `max` | Unrestricted | Only for Opus on the hardest problems |

Current setting: `medium` (already configured in settings.json).

## Context Management

Context bloat is a silent token killer — every prompt re-sends the entire session history.

- **`/context`** — diagnostic: see what's eating your tokens (system prompt vs history vs tool output)
- **`/compact`** — summarizes session history, restarts with only the summary. Run at ~70% context, don't wait for auto-compact at 95%
- **`/compact focus on X`** — direct the summarization to preserve specific decisions
- **`/clear`** — hard reset: wipes all history, resets token count. Use when switching to an unrelated task

**Rule:** Run `/compact` before context hits 70%. Run `/clear` immediately after a git commit or finishing a cohesive sub-task.

## Offload to Gemini CLI (Free Tier)

Gemini CLI costs nothing on the free tier. Use it for everything that doesn't require file edits or tool use:

| Route to Gemini CLI | Route to Claude |
|---------------------|-----------------|
| Research, web searches | Code writing |
| Doc drafting | Multi-file refactors |
| Analysis, comparisons | Debugging |
| Design review | Tool use (read, edit, bash) |
| Data extraction | Architecture decisions |

Call from Claude: `mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3-flash-preview")`

Default: `gemini-3-flash-preview` (fast). Escalate: `gemini-3-pro-preview` (quality). Deep research: `model="deep-think"` (gemini-3.1-pro-preview, thinking level high).

## The "Haiku Triage" Pattern

For complex tasks with heavy discovery work:

1. Start session in Haiku: do file reads, glob searches, write an implementation plan to a markdown file
2. `/clear`
3. Switch to Sonnet: implement the plan
4. Only escalate to Opus if Sonnet gets stuck

This isolates expensive "discovery" context to the cheapest model.

## Prompt Discipline

- **Directive over conversational** — "Fix lint errors in src/main.py" not a paragraph of context
- **Pipe noisy output to files** — `pytest > /tmp/results.txt` then read the file, not 5000 lines dumped into context
- **Disable unused MCPs** — each active MCP server injects tool definitions into every request
- **Start fresh sessions** for unrelated tasks — don't reuse bloated sessions from different work

## Quota System

- **168-hour rolling window** (not a fixed weekly reset) — usage ages out to the exact minute
- **5-hour burst limit** — fresh allocation every 5 hours from your first message
- **No API to query remaining quota** — you find out when you hit the wall
- Each Claude Code command triggers 8-12 internal API calls — burns quota faster than the web UI

**Monitor with:**
```bash
npx claude-spend          # web dashboard at localhost:3456
npx ccusage@latest weekly # terminal weekly report
npx ccusage@latest blocks # 5-hour billing blocks (most granular)
```

## Your Usage Baseline (from ccusage)

| Week | API-equivalent cost |
|------|:-------------------:|
| Feb 16 | $19 |
| Feb 23 | $434 |
| Mar 2 | $157 |
| Mar 9 | **$2,567** |
| Mar 16 | **$2,641** |
| **Total** | **$5,818** |

At $2,600+/week on API pricing, the Max subscription (~$200/mo) provides ~13x leverage. But weeks like Mar 9/16 are when you hit quota walls — those are Opus-heavy sessions on large propagation/ecosystem work.

## Quick Reference

```
/model haiku     → cheap discovery work
/model sonnet    → standard implementation (default)
/model opus      → architecture + hard problems only
/effort low      → mechanical tasks
/effort high     → complex reasoning
/compact         → at 70% context
/clear           → after each task
```
