---
title: "iTerm2 + ntfy Session Status Integration — Implementation Plan"
category: plan
tags: [iterm2, ntfy, notifications, session-status, ai-cli, nats]
status: active
source: internal
task: SW-732
---

# iTerm2 + ntfy Session Status Integration — Implementation Plan

**Status:** ACTIVE (decisions approved, ready to implement)

**Created:** 2026-03-29

**Task:** SW-732
**Related:** SW-731 (iTerm2 Mac-local setup), SW-667 (notification system), R-36 (iTerm2 research)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log.
-->

## Design Decisions (Approved)

| # | Decision | Chosen | Notes |
|---|----------|--------|-------|
| 1 | Status visual channel | Badge + tab title for STATUS. Rolling hex tab colors for IDENTITY. | Unlimited hex colors via escape sequences (not limited to presets). Grey=dev shell, none=local Mac shell, rotating unique=CC sessions. |
| 2 | Status source | Hybrid: ai-cli escape sequences (immediate, per-tab) + NATS→ntfy (cross-session) + Mac daemon with iTerm2 Python API (cross-tab updates + tab-jump notifications). All three layers built now. | Escape sequences for <100ms per-tab status. Mac daemon for cross-tab awareness + macOS notification with jump-to-tab. |
| 3 | Status states | 4 CC states: running (▶), waiting (⏸), done (✓), error (✗). Plus non-CC session types with distinct icons. "Waiting" detected via CC Notification hook (already configured in settings.json). | Expandable — add states as detection improves. |
| 4 | macOS notifications | Both ntfy (already deployed, covers Mac + iPhone) + Mac daemon with iTerm2 tab-jump. Built now, not deferred. | ntfy for push alerts. Mac daemon for "click notification → focus right tab." |

## Session Types & Visual Identity

### Tab Color Scheme

| # | Session Type | Tab Color | Detection |
|---|-------------|-----------|-----------|
| 1 | Local Mac shell | None (default/transparent) | No SSH, no tmux, no ai-cli |
| 2 | Dev server shell | Grey (`#666666`) | SSH to Hetzner, no CC session |
| 3 | CC session (sw-1) | Rolling unique color (from 10+ palette) | ai-cli engine=c, assigned by session number |
| 4 | Gemini CLI session | Rolling unique color (same palette) | ai-cli engine=g |
| 5 | SSH tunnel / reverse tunnel | Green (`#2ecc71`) | Launch context |
| 6 | Chrome debug | Blue (`#4a90d9`) | Launch context |
| 7 | Caffeinate | Amber (`#f39c12`) | Process detection or launch context |

Colors set programmatically via `\e]1337;SetColors=tab=RRGGBB\a` — unlimited hex values, bypasses the preset picker entirely.

### CC Session States (badge + tab title, NOT color)

| # | State | Badge | Tab Title | Detection | Icon Behavior |
|---|-------|-------|-----------|-----------|--------------|
| 1 | Running | `▶ cc sw-3` | `▶ cc sw-3` | ai-cli: CC process active | Session type icon (Claude/Gemini logo) |
| 2 | Waiting for input | `⏸ WAIT sw-3` | `⏸ WAIT sw-3` | CC Notification hook fires | Session type icon |
| 3 | Done | `✓ DONE sw-3` | `✓ DONE sw-3` | ai-cli: CC exited normally | Session type icon |
| 4 | Error | `✗ ERROR sw-3` | `✗ ERROR sw-3` | ai-cli: CC exited <3 seconds | Session type icon |
| 5 | Resuming | `↻ sw-3` | `↻ sw-3` | ai-cli: between exit and restart | Session type icon |

### Icons

**Approach: Start with monochrome white silhouettes** (quick setup, clean with colored tabs). Then try pre-rendered color variants if monochrome doesn't feel right. Both use the same icon paths — just swap the PNG files.

**Monochrome set (Phase 1):**
- White silhouette on transparent background, 64x64 PNG
- Tab color provides visual differentiation
- Simple, fast to create

**Color variant set (Phase 2, if needed):**
- Pre-rendered PNGs tinted to match each rolling color (10 variants per icon)
- Plus status variants (green=done, red=error, amber=waiting)
- Auto-generated from SVG source via script (ImageMagick/Pillow tinting)
- ~30-40 PNGs total, auto-generated not hand-made

**Icon types:**

| # | Icon | Source | Use |
|---|------|--------|-----|
| 1 | Claude/Anthropic logo | simpleicons.org or brand assets | CC sessions |
| 2 | Gemini logo | simpleicons.org or brand assets | Gemini CLI sessions |
| 3 | Chrome logo | simpleicons.org | Chrome debug |
| 4 | Terminal/prompt | codicons or custom | Shell sessions |
| 5 | SSH key / link | codicons or custom | SSH tunnels |
| 6 | Coffee cup | codicons or custom | Caffeinate |

All stored at `~/.config/iterm2/icons/` on Mac.

## Architecture

### Three-Layer Status Pipeline

```text
Layer 1: ai-cli escape sequences (Hetzner-side, <100ms)
  ┌──────────────┐    escape seq    ┌─────────────┐
  │ ai-cli       │ ───────────────→ │ iTerm2 tab  │
  │ watcher loop │    (via tmux     │ badge+title  │
  │              │    passthrough)  │ update       │
  └──────────────┘                  └─────────────┘
  Detects: start, exit, error, resuming

  ┌──────────────┐    escape seq    ┌─────────────┐
  │ CC Notif.    │ ───────────────→ │ iTerm2 tab  │
  │ hook         │    (via tmux     │ badge+title  │
  │ (notify.sh)  │    passthrough)  │ "⏸ WAIT"    │
  └──────────────┘                  └─────────────┘
  Detects: waiting for input

Layer 2: NATS → ntfy (Hetzner-side, 1-3s)
  ┌──────────────┐    NATS event    ┌─────────────┐    ntfy    ┌──────────┐
  │ ai-cli       │ ───────────────→ │ notification│ ─────────→ │ ntfy.    │
  │ watcher      │                  │ gateway     │            │ host     │
  └──────────────┘                  └─────────────┘            │ wallace  │
                                                                │ .com     │
  Already publishes session events via:                        └────┬─────┘
  - ai internal publish-event                                      │
  - ai internal publish-session-event                              │
                                                                    ↓
                                                            Mac + iPhone
                                                            push notification

Layer 3: Mac daemon → iTerm2 Python API (Mac-side, 1-5s)
  ┌──────────────┐    subscribe     ┌─────────────────────┐
  │ ntfy         │ ───────────────→ │ ntfy-iterm2-bridge  │
  │ (HTTP/SSE)   │                  │ (Python daemon)     │
  └──────────────┘                  │                     │
                                     │ 1. Find session tab │
                                     │ 2. Update badge     │
                                     │ 3. macOS notif with │
                                     │    "Jump to tab"    │
                                     └─────────────────────┘
                                     Runs as launchd agent on Mac
```text

## Implementation Tasks

### Task 1: ai-cli status escape sequences (Hetzner)

Add `_iterm2_status()` to ai-cli script. Updates badge + tab title (NOT color) on state changes:
- Session start → `▶ cc sw-N`
- CC exits normally → `✓ DONE sw-N`
- CC exits <3s → `✗ ERROR sw-N`
- Loop resuming → `↻ sw-N`

**Files:** `~/projects/ai-cli-utils/src/ai_cli/main.py`

### Task 2: CC Notification hook for "waiting" state (Hetzner)

Extend `~/.claude/hooks/notify.sh` to emit iTerm2 escape sequence when CC fires a Notification (= waiting for input):
```bash
printf '\e]1337;SetBadgeFormat=%s\a' "$(echo -n '⏸ WAIT sw-N' | base64)"
printf '\e]0;⏸ WAIT sw-N\a'
```text

Need to extract the session number from the environment (`$AI_TMUX_SESSION`).

**Files:** `~/.claude/hooks/notify.sh`

### Task 3: Verify NATS session events publish to ntfy (Hetzner)

The ai-cli watcher already publishes NATS events. Verify the notification gateway forwards session state changes (started/completed/errored) to ntfy. Add session status payload if missing.

**Files:** notification gateway config, verify ntfy topic

### Task 4: Dynamic Profiles + icons (Mac)

Create `ai-cli-profiles.json` with profiles for each session type. Create monochrome white silhouette icons (64x64 PNG). Store at `~/.config/iterm2/icons/`.

**Files:** Mac-local config

### Task 5: Mac daemon — ntfy-iterm2-bridge.py (Mac)

Python script that:
1. Subscribes to ntfy topic via SSE (Server-Sent Events)
2. On session event: find matching iTerm2 tab by session name (Python API)
3. Update badge + tab title on that tab (cross-tab awareness)
4. Fire macOS notification with "Jump to tab" action
5. Runs as launchd agent (`~/Library/LaunchAgents/com.ai-cli.ntfy-iterm2-bridge.plist`)

**Dependencies:** `pip install iterm2` on Mac. iTerm2 → Settings → General → Magic → "Enable Python API" must be checked.

**Files:** New script (location TBD — possibly `~/.config/iterm2/scripts/` or `~/projects/myproject/scripts/`)

### Task 6: Icon color variant generator (optional, Phase 2)

If monochrome icons don't feel right after testing, create a Python script that takes SVG source icons and generates tinted PNG variants for each rolling color + status color.

**Files:** Generator script, output to `~/.config/iterm2/icons/`

### Task 7: Reinstall ai-cli everywhere

After ai-cli code changes:
- Hetzner: `cd ~/projects/ai-cli-utils && uv tool install -e . --force`
- Mac: `uv tool install -e ~/projects/ai-cli-utils --force`
- optional secondary venv: `uv pip install -e ~/projects/ai-cli-utils`

## Build Order

1. Task 1 (ai-cli escape sequences) — Hetzner, immediate value
2. Task 2 (CC Notification hook) — Hetzner, adds "waiting" state
3. Task 3 (verify ntfy events) — Hetzner, ensures Layer 2 works
4. Task 4 (Dynamic Profiles + icons) — Mac, visual identity
5. Task 5 (Mac daemon) — Mac, cross-tab + notifications
6. Task 7 (reinstall ai-cli) — after Task 1-2 are committed
7. Task 6 (color variants) — optional, after testing monochrome

Tasks 1-3 can be done from Hetzner. Tasks 4-5 require Mac. Task 6 is optional Phase 2.

## Open Questions (Resolved)

1. ~~Unicode in badges/titles~~ — Will test ▶ ✓ ✗ ↻ ⏸ on Mac. Fallback to ASCII (>, OK, X, ~, ||) if needed.
2. ~~Badge visibility in splits~~ — Use both badge AND tab title. Badge for the focused pane, tab title for the tab bar overview. User sees status in both places.
3. ~~"Waiting for input" detection~~ — **RESOLVED: CC Notification hook already configured.** Extend notify.sh to emit escape sequences.
4. ~~ntfy topic structure~~ — Use existing topic. Session events are lower-frequency than feared — only fires on session start/complete/error, not continuously.

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-03-29 | 1 | D1: badge+title for status, rolling hex colors for identity (approved). D2: hybrid all 3 layers, Mac daemon now not deferred (approved). D3: 4 CC states + non-CC types, "waiting" via CC Notification hook (approved). D4: ntfy + Mac daemon with tab-jump, both now (approved). Icons: start monochrome, try color variants if needed. |
