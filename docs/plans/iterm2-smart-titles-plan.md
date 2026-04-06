---
title: "iTerm2 Smart Tab/Window Titles"
category: plan
tags: [iterm2, fleet, tab-title, window-title, session-title]
status: APPROVED
source: ai-cli-utils
---

# iTerm2 Smart Tab/Window Titles — Implementation Plan

**Status:** APPROVED
**Created:** 2026-03-31

## Overview

Replace static `cc sw-N` titles with actual tmux session names (e.g. `c-sw-5`, `c-r-sw-1`). Abbreviate multi-session tabs with per-session status symbols. Show Claude logo + pane-type symbols in tab/pane headers. Generate descriptive window titles via hybrid heuristic + async Claude Haiku.

---

## Title System in iTerm2

| Element | Where it shows | Set via | Behavior |
|---|---|---|---|
| **Profile icon** | Tab bar (focused pane) + pane header | `SetProfile=ClaudeCode` | Per-pane; tab shows focused pane's icon |
| **Tab title text** | Tab bar, next to icon | OSC `\033]0;{text}\007` | Includes pane-type symbols + abbreviated sessions |
| **Pane header text** | Title bar above each split pane | OSC 0 (per-session) | Per-pane: session name + status |
| **Window title** | macOS title bar | OSC `\033]2;{text}\007` | Hybrid heuristic + async Claude Haiku |
| **Status symbol** | Prefix in title text | Text in OSC 0 | Per-session inside brackets |

**Tab bar icon constraint:** iTerm2 shows one profile image in the tab — from the focused pane. Multi-icon is not possible there. Pane headers (split view title bars) show per-pane profile icons independently, always — that's where per-pane logos work properly.

---

## Icon / Symbol Set

| Session type | Tab text symbol | Pane profile icon |
|---|---|---|
| Claude CC | `*` | Claude logo PNG (`ClaudeCode` profile family) |
| Gemini | `✦` | Gemini icon (`GeminiCLI` profile) |
| Shell / zsh | `$` | Terminal icon PNG (`ShellUtility` profile) |
| Other process | `·` | Default / no icon |

---

## Tab Title Format

### Single pane
`* ▶ c-sw-5`

### Multi-pane — shared prefix
Abbreviation: longest common prefix (min 4 chars) + `{symbol+suffix|…}` per session.

`**** c-r-sw-{▶1|⏸2|✓3|✓4}`
`*$ c-sw-{▶5}` (CC top/left, shell bottom/right)
`$* c-sw-{▶5}` (shell top/left, CC bottom/right)

### Multi-pane — no shared prefix
Space-joined: `*$ ▶ c-sw-5 $→sh`

### Status symbols

| State | Symbol | Example |
|---|---|---|
| Running | `▶` | `▶ c-sw-5` |
| Waiting | `⏸` | `⏸ c-sw-5` |
| Done | `✓` | `✓ c-sw-5` |
| Error | `✗` | `✗ c-sw-5` |
| Resuming | `↻` | `↻ c-sw-5` |

Multi-pane: per-session status inside brackets: `c-r-sw-{▶1|⏸2|✓3|✓4}`.

### Pane ordering for symbol prefix
Use pane creation index (from `ITERM_SESSION_ID` `p{N}`) as proxy for spatial order. Creation order typically matches: top-left → top-right → bottom-left → bottom-right for standard splits.

---

## Split Pane Headers

Each pane's title bar shows independently:
- CC pane: `[Claude logo] ▶ c-sw-5`
- Shell pane: `[terminal icon] $ → c-sw-5` (paired CC session name)
- Gemini pane: `[Gemini icon] ▶ g-sw-1`

Profile icon comes from the applied profile (real PNG). Title text set via OSC 0 per-session.

Shell companion pane uses `ShellUtility` profile. A `>_` style terminal icon PNG will be added to `~/.config/iterm2/icons/` and set in the ShellUtility Dynamic Profile.

---

## Session Name Tracking Files

**`/tmp/iterm2-cc-names-{tab_key}`** — lines of `{pane_idx}:{session_name}:{type}:{status}` for all registered sessions in this tab.

- **On CC start** (Python `_emit_iterm2_profile_setup` + bash `_iterm2_fleet_setup`): append own line; compute tab title; emit.
- **On CC exit** (EXIT trap): remove own line; if non-empty, re-emit remaining title; if empty, delete + reset.
- **On status change** (`_iterm2_status`): update own status field in file; re-emit tab title.
- **precmd hook**: re-reads file on every shell prompt; re-emits tab title + shell pane header.

---

## Window Title (Option C — Hybrid)

1. **Heuristic fires instantly** on CC start:
   - Parse session prefixes → label: `c-r-sw-*` → `SW Remote CC`, `c-sw-*` → `SW Local CC`, mixed → `CC Sessions`
   - Emit `\033]2;{label}\007` immediately

2. **Claude Haiku refines async**:
   - Collect all session names in window from registry `/tmp/iterm2-win-{win_key}`
   - Spawn: `claude -p "..." --model claude-haiku-4-5-20251001 --output-format text > /tmp/iterm2-win-title-{win_key} 2>/dev/null &`
   - Prompt: `"2-4 word iTerm2 window title for terminal window with these Claude Code sessions: {sessions}. Concise, descriptive. Examples: 'SW Remote CC', 'Local Dev Sessions'. Title only."`
   - Falls back to heuristic permanently if `claude` not in PATH

3. **precmd hook** checks title file on next prompt; emits `\033]2;{title}\007` once when updated.

**Window key**: `${ITERM_SESSION_ID%%t*}` → e.g. `w0`
**Registry**: `/tmp/iterm2-win-{win_key}` → lines of `{tab_key}:{session_name}:{type}`
**Title file**: `/tmp/iterm2-win-title-{win_key}` → generated window title

---

## Gemini Sessions

Same rules. `g-sw-1`, `g-r-proj-2` — same abbreviation, same status symbols, `◇` symbol, `GeminiCLI` profile icon.

---

## Files Changed

| File | Change |
|---|---|
| `src/ai_cli/main.py` | `_emit_iterm2_profile_setup`: use full tmux session name; write names file + window registry; emit heuristic window title; spawn async Claude Haiku |
| `src/ai_cli/main.py` | `_iterm2_fleet_setup` bash: use `$sname`; manage names file with pane index + type; compute tab title with symbols + abbreviated sessions |
| `src/ai_cli/main.py` | `_iterm2_status` bash: update status field in names file; re-emit tab title with per-session status |
| `src/ai_cli/main.py` | EXIT trap: remove own names file entry; re-emit remaining; clean window registry |
| `~/.config/iterm2/icons/terminal-icon.png` | New: `>_` style terminal icon for ShellUtility profile |
| `~/Library/.../ai-cli-profiles.json` | ShellUtility: add `Custom Icon Path` pointing to terminal-icon.png |
| `~/.zshrc` | `_ai_iterm2_precmd`: abbreviation logic; shell pane header `$ → {session}`; window title file check |

---

## Approval Log

| Date | Round | Decisions |
|---|---|---|
| 2026-03-31 | 0 | Draft created |
| 2026-03-31 | 1 | Window title: Option C hybrid. Gemini: same format. |
| 2026-03-31 | 2 | Status: Option B (per-session in brackets). CC symbol: `*`. Gemini symbol: `✦`. Shell pane: `$ → {session}`. Terminal icon: create `>_` PNG. No more open questions. |
