---
title: CC Statusline — Design Document
category: design
tags: [statusline, quota, iterm2, claude-code]
status: active
source: claude-sonnet-4-6
---

# CC Statusline — Design Document

**Status:** ACTIVE

**Created:** 2026-04-29

**Task:** `[AI-CLI-65]`

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

- [Problem Statement](#problem-statement)
- [Statusline Segments](#statusline-segments)
- [Format Spec](#format-spec)
- [Quota Segment Details](#quota-segment-details)
- [Caching Architecture](#caching-architecture)
- [Telemetry](#telemetry)
- [Configuration](#configuration)
- [Files](#files)
- [Integration](#integration)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Problem Statement

Claude Code's `statusLine` hook fires on every render cycle (including during token streaming). The statusline must render in <1ms to avoid UI flicker, but quota data requires ~700ms Python startup + SQLite + optional NATS check. This design documents the full statusline pipeline: segment layout, caching strategies, quota integration, and the data flow between `statusline-command.sh` and `quota_statusline_part()`.

## Statusline Segments

The assembled statusline follows this left-to-right layout:

```
HH:MM │ Model │ project:branch │ [tmux-session │] ctx% │ [quota │] tip
```

| Segment | Source | Format | Notes |
|---------|--------|--------|-------|
| **Clock** | `date +%H:%M` | `HH:MM` | Rendered dim |
| **Model** | JSON `.model` | `Opus` / `Sonnet` / `Haiku` | Abbreviated from display name |
| **Project:Branch** | `$CLAUDE_PROJECT_DIR` + `git branch` | `project`:**branch** | Worktrees: `#N` for `wt-*-N` branches; truncated to 13 chars + `…` |
| **Tmux session** | `$TMUX` env + `tmux display-message` | `session-name` in magenta | Omitted when not inside tmux |
| **Context %** | JSON `.context_window.used_percentage` | `N%` color-coded | Green <50%, yellow <80%, red ≥80%; `--%` dimmed when absent |
| **Quota** | `ai quota statusline-part` (Python) | see [Quota Segment Details](#quota-segment-details) | Stale-while-revalidate cached; omitted when empty |
| **Tip** | Rotating array | dimmed italic text | Rotates every 45s based on `$(date +%s) / 45 % N` |

Segments are joined by a dimmed `│` separator: `\033[2m│\033[0m`.

The final line ends with `\033[0m\033[K\n` — reset + erase-to-EOL to clear leftover characters from previously longer renders.

## Format Spec

### ANSI Color Codes Used

| Name | Code | Used For |
|------|------|---------|
| Reset | `\033[0m` | End of any colored span |
| Dim | `\033[2m` | Clock, separators, tip, stale indicator |
| Bold | `\033[1m` | Project name |
| Cyan | `\033[36m` | Branch name |
| Magenta | `\033[35m` | Tmux session name |
| Green | `\033[32m` | Context % <50% |
| Yellow | `\033[33m` | Context % 50–79% |
| Red | `\033[31m` | Context % ≥80% |
| Bold Cyan | `\033[1;36m` | Week/W label in quota segment |
| Bold Magenta | `\033[1;35m` | Son/S label in quota segment |

### Branch Display Rules

- Pattern `wt-*-N` (worktree convention) → displayed as `#N`
- Length > 14 chars → truncated to first 13 + `…`
- Otherwise displayed as-is

### Git Branch Cache

Branch lookup (`git branch --show-current`) costs ~100ms. Cached in `$TMPDIR/.ai-sl-branch-$UID` with a 5-second TTL. Cache format: line 1 = Unix timestamp, line 2 = branch name. `$GIT_BRANCH_CACHE` env var overrides entirely.

## Quota Segment Details

Quota output is produced by `ai quota statusline-part` (`quota_statusline_part()` in `src/ai_cli/quota.py`).

### Output Format

**Normal phase** (week elapsed ≥ 24h):

```
📊 Week 42% →+8% ✅ | Son 87% →+5%
```

**Seedling phase** (week elapsed < 24h):

```
📊 Week 12% →+3% 🌱 [⏱] | Son 8% →+1%
```

**When Sonnet data absent** (background scrape triggered):

```
📊 Week 42% →+8% ✅ | Son -% →-%
```

**When scrape format changed** (DB mismatch counter raised):

```
⚠ quota scrape format changed
```

### Label Width Adaptation

The `AI_CLI_STATUSLINE_COLS` env var (set from `${COLUMNS:-0}` in the shell) controls label width:

| Terminal width | Week label | Son label |
|---------------|------------|-----------|
| ≥ 80 cols | `Week` | `Son` |
| < 80 cols (or 0) | `W` | `S` |

### Pace Indicator Colors

**Weekly all-models usage `→±X%`:**
- delta ≤ 10% → green (on pace)
- delta ≤ 25% → yellow (slightly ahead)
- delta > 25% → red (burning fast)

**Sonnet pace `→±X%`:**
- delta ≤ 10% → green
- delta ≤ 25% → yellow
- delta > 25% → red

Where `delta = usage_pct - week_elapsed_pct`.

### Acceleration Arrow

Requires ≥3 snapshots in the DB. Computes burn rate between most recent 3 snapshots:
- `↑` accelerating (rate_recent − rate_prev > 1.0 %/hr)
- `↓` decelerating (rate_recent − rate_prev < −1.0 %/hr)
- `→` steady (otherwise)

### Status Icons (normal phase)

| Condition | Icon | Color |
|-----------|------|-------|
| delta ≤ 10% | ✅ | Green arrow |
| delta ≤ 25% | ⚠️ | Yellow arrow |
| delta > 25% | 🚨 | Red arrow |

### Stale Indicator

`⏱` (dimmed) appended when quota data is >30 minutes old.

## Caching Architecture

### Problem

`ai quota statusline-part` has ~700ms startup overhead (Python + ai_cli import + SQLite). Claude Code calls `statusLine` on every render cycle — during streaming that's many times/second. Without caching, this stacks up dozens of competing background processes causing duplicate prompt boxes in the scrollback buffer.

### Solution: Stale-While-Revalidate

Cache file: `$TMPDIR/.ai-sl-quota-$UID`
Format: line 1 = Unix timestamp, line 2 = quota output string.

| Age | Action |
|-----|--------|
| < 30s (fresh) | Serve cached value directly |
| 30–300s (stale) | Serve cached value + launch background refresh |
| > 300s (very old) | Synchronous fetch (first call or after long gap) |

Lock file `$TMPDIR/.ai-sl-quota-lock-$UID` prevents concurrent background refreshes from stampeding. A lock is valid for 120s; expired locks are removed.

### Telemetry Rate Limiting

`ai quota record` has ~680ms startup overhead. Telemetry calls are rate-limited to once per 60s via `$TMPDIR/.ai-sl-telem-$UID`. Tokens = `total_input_tokens + total_output_tokens` from the JSON context window fields.

Skipped when `AI_HOST=acn-windows` (Windows host, no tmux/Python env available).

## Telemetry

The `ai quota record SESSION HOST MODEL TOKENS` call fires at most once per 60 seconds. Data stored to `quota.db` via `QuotaDB.record()`. Used by quota history, burn rate, and acceleration calculations.

Parameters sourced from the CC statusLine JSON:
- `SESSION` — tmux `session_id` (e.g. `$0`)
- `HOST` — `hostname`
- `MODEL` — `.model.id` or `.model` string
- `TOKENS` — `.context_window.total_input_tokens + total_output_tokens`

## Configuration

No user-facing configuration for the statusline itself. Related config:

| Key | File | Purpose |
|-----|------|---------|
| `AI_CLI_STATUSLINE_COLS` | Set from shell `${COLUMNS:-0}` | Terminal width for adaptive label width |
| `GIT_BRANCH_CACHE` | Shell env | Bypass branch cache (used in tests) |
| `AI_HOST` | `~/.zshenv` | Skip telemetry/quota on Windows |

## Files

| File | Purpose |
|------|---------|
| `src/ai_cli/data/statusline-command.sh` | Shell script: JSON parse, segment assembly, caching, telemetry |
| `src/ai_cli/quota.py` → `quota_statusline_part()` | Python: quota DB read, format output with ANSI codes |
| `~/.claude/statusline-command.sh` (ai-harness) | Copy installed by `ai setup`; must stay in sync with source |

The shell script is installed to `~/.claude/statusline-command.sh` by `ai setup` and referenced in `.claude/settings.json` as the `hooks.statusLine` command.

## Integration

- **`quota_statusline_part()`** in `quota.py` reads from `quota.db` (SQLite, `quota_snapshots` table) and the `quota_meta` table for format-change detection.
- **`ai quota record`** writes to `quota_snapshots` via `QuotaDB.record()`.
- **`ai quota scrape`** populates `quota_snapshots` via `_scrape_usage_hidden_pane()`. Format-change detection writes to `quota_meta`.
- **CC statusLine hook** — configured in `.claude/settings.json`: `{"hooks": {"statusLine": [{"type": "command", "command": "~/.claude/statusline-command.sh"}]}}`.

## Open Questions

1. Should the statusline design doc live in `ai-cli-utils/docs/designs/` (current) or migrate to a future `ai-harness` repo? The script has a copy in `ai-harness/.claude/` already.
2. Should the branch cache TTL (5s) be configurable via env var?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Location decision -->
> 2. <!-- Cache TTL config -->
> - <enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-29 | Initial doc created | Documents current implementation as of AI-CLI-64 + AI-CLI-68 |
