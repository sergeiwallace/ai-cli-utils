---
title: "iTerm2 Fleet Management Configuration — Implementation Plan"
category: plan
tags: [iterm2, terminal, fleet-management, ai-cli, configuration]
status: active
source: internal
task: SW-730
---

# iTerm2 Fleet Management Configuration — Implementation Plan

**Status:** ACTIVE

**Created:** 2026-03-29

**Task:** SW-730
**Research:** R-36 ([`iterm2-fleet-management-config.md`](../research/iterm2-fleet-management-config.md))

## Overview

Configure iTerm2 for fleet-style management of 5-10 parallel Claude Code sessions on a remote Hetzner dev server. The approach uses Dynamic Profiles (JSON) for declarative session type definitions + escape sequences from ai-cli for runtime customization (tab color, badge, profile switch). No iTerm2 Python API required — escape sequences + AppleScript covers all needs.

## Prerequisites

1. `set -g allow-passthrough on` in Hetzner `.tmux.conf` (lets escape sequences reach iTerm2 through tmux)
2. Icon PNGs stored at `~/.config/iterm2/icons/` on Mac
3. Dynamic Profiles JSON at `~/Library/Application Support/iTerm2/DynamicProfiles/`

## Implementation Tasks

### Task 1: Enable tmux passthrough on Hetzner

Add `set -g allow-passthrough on` to `~/.tmux.conf` on Hetzner server. Without this, none of the escape sequences (tab color, badge, profile switch) pass through tmux to iTerm2.

**Files:** Hetzner `~/.tmux.conf`
**Risk:** Low — single config line, reversible

### Task 2: Create icon assets

Download/create PNG icons (64x64, transparent bg) for each session type:

| # | Icon | Source | Filename |
|---|------|--------|----------|
| 1 | Claude/Anthropic logo | simpleicons.org or custom | `claude-logo.png` |
| 2 | Terminal/shell | SF Symbols export or codicons | `terminal.png` |
| 3 | Coffee (caffeinate) | codicons or custom | `coffee.png` |
| 4 | SSH/key | codicons | `ssh-key.png` |
| 5 | Chrome | simpleicons.org | `chrome.png` |

**Location:** Mac `~/.config/iterm2/icons/`

### Task 3: Create Dynamic Profiles JSON

Create `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-profiles.json` with profiles:

- **ClaudeCode** — Claude logo icon, Anthropic purple base tab color, badge template `\(user.sessionType) sw-\(user.sessionNum)`, dark color scheme
- **ShellUtility** — terminal icon, grey tab color, badge with session type
- **Caffeinate** — coffee icon, warm amber tab color
- **ChromeDebug** — Chrome icon, blue tab color
- **SSHForward** — SSH icon, green tab color

Each inherits from Default profile via `"Dynamic Profile Parent Name": "Default"`.

**Files:** Mac `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-profiles.json`

### Task 4: Add iTerm2 escape sequence helper to ai-cli

Add `_iterm2_setup()` function to ai-cli that runs after tmux attach:

1. Set profile via `\e]1337;SetProfile=ClaudeCode\a`
2. Set rolling tab color via `\e]1337;SetColors=tab=hex\a` (10-color palette, assigned by session number mod 10)
3. Set user variables for badge interpolation (`sessionType`, `sessionNum`, `tmuxSession`)
4. Set badge via `\e]1337;SetBadgeFormat=base64\a`
5. Set tab title via `\e]0;CC sw-N\a`

Detect iTerm2 via `$TERM_PROGRAM == "iTerm.app"` — skip on other terminals (Ghostty, Windows Terminal).

**Files:** `packages/ai-cli/src/ai_cli/main.py`
**Risk:** Medium — ai-cli changes require reinstall in 3 places (Mac uv tool, Hetzner uv tool, optional secondary venv)

### Task 5: Create 4-pane fleet monitoring Window Arrangement

Set up and save a named Window Arrangement in iTerm2:

```text
┌──────────────────┬──────────┐
│                  │ sw-2     │
│     sw-1         │          │
│   (main CC)      ├──────────┤
│                  │ sw-3     │
│                  │          │
└──────────────────┴──────────┘
```text

Save via Window → Save Window Arrangement. Can be restored via Window → Restore Window Arrangement or `Cmd+Shift+R`.

### Task 6: Update iterm2-setup.md tool doc

Update `docs/tools/iterm2-setup.md` with all new configuration, keyboard shortcuts, fleet workflow, and Dynamic Profiles reference.

## Build Order

1. Task 1 (tmux passthrough) — unblocks all escape sequences
2. Task 2 (icons) — needed by Dynamic Profiles
3. Task 3 (Dynamic Profiles) — the core config
4. Task 4 (ai-cli escape sequences) — runtime customization
5. Task 5 (Window Arrangement) — layout convenience
6. Task 6 (docs) — capture everything

Tasks 1-3 are config-only (no code). Task 4 is the only code change.

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
