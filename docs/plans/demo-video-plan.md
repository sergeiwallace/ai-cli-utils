---
title: "Terminal Demo Video — Implementation Plan"
category: plan
tags: [demo, gif, screencapture, ffmpeg, readme]
status: in_progress
task: AI-CLI-52
---

# Terminal Demo Video — Implementation Plan

## Table of Contents

- [Goal](#goal)
- [Approach](#approach)
- [Demo Sequence](#demo-sequence)
- [Implementation](#implementation)
- [Acceptance Criteria](#acceptance-criteria)
- [Approval Log](#approval-log)

## Goal

Produce a polished terminal demo GIF embedded in the README that shows `ai-cli-utils` core value to new visitors in under 30 seconds.

## Approach

**Window-specific recording via `screencapture -l <CGWindowID>`** — records a dedicated iTerm2 window by ID without requiring it to be in focus. The demo window is a regular (non-fullscreen) 1200×780 iTerm2 window positioned at {200, 80, 1400, 860}. The user's main full-screen session is untouched throughout.

Architecture:
- `demo/record-demo.sh` (coordinator): creates the demo window, gets its CGWindowID via Python Quartz, starts `screencapture -l`, launches `demo-runner.sh` inside the window, waits for sentinel file, stops recording, converts to GIF.
- `demo/demo-runner.sh` (runner): executes the demo sequence inside the demo window, opens session tabs via osascript, touches sentinel when done.

Requires: screen recording permission granted to iTerm2 in System Settings → Privacy & Security → Screen Recording. iTerm2 must be restarted after granting permission.

**Why not VHS?** VHS renders a headless terminal and can't capture iTerm2-specific visuals (tab colors, session icons). **Why not full-screen capture?** Would capture the user's active workflow.

## Demo Sequence

| Step | Command | Purpose | Est. time |
| --- | --- | --- | --- |
| 1 | *(title card)* | Tool name + tagline | 2s |
| 2 | `ai ls` | Show active session list | 3s |
| 3 | `ai sync push` | Cross-machine memory sync | 3s |
| 4 | `ai quota status` | Claude usage monitoring | 2s |
| 5 | `ai gemini "…" -m flash` | AI query passthrough | 5s |
| 6 | *(end card)* | GitHub URL | 2s |

Total target: ~20 seconds.

`ai c 1` (session launch) is intentionally deferred from v1 — it may attach to tmux and not return cleanly in a scripted context. Add in v2 once we verify behavior.

## Implementation

### Files

- `demo/record-demo.sh` — main automation script
- `demo/demo.gif` — output (git-ignored or tracked via Git LFS)
- `demo/demo.tape` — kept as fallback reference, not used by this plan

### Script design

1. Kill any stale screencapture processes
2. `screencapture -v -D1 demo/demo.mov &` — background recording of main display
3. Sleep 2s for screencapture to initialize
4. Run demo commands via `run()` helper that simulates typing character-by-character
5. `kill -INT $SCAP_PID` — SIGINT tells screencapture to finalize the file
6. Two-pass ffmpeg palette GIF conversion (best quality, smallest size)

### GIF optimization

Two-pass approach: generate palette from full video stats → apply palette with Bayer dithering. Target: ≤5 MB at 1200px wide, 15 fps.

## Acceptance Criteria

- [ ] `bash demo/record-demo.sh` runs end-to-end without intervention
- [ ] Output GIF ≤ 5 MB, legible at README width (600–800px render)
- [ ] All 4 demo commands visible with typing animation
- [ ] GIF loops cleanly (no flash/jump at loop point)
- [ ] README updated to embed GIF

## Tool Consideration — Claude Code `computer-use` MCP (added 2026-04-23)

Claude Code ships a **built-in `computer-use` MCP server** (Anthropic-bundled, currently disabled on this machine) that exposes screenshot + keyboard + mouse + multi-display control to Claude itself.

**For future (re-)recordings of this demo**, consider enabling it instead of (or alongside) the current automation stack (`record-demo.sh`, `demo-runner.sh`, osascript). Could let Claude drive the iTerm2 window and terminal interactions end-to-end without AppleScript / screencapture intermediaries, potentially simplifying timing reliability.

- **Enable per-session:** `/mcp` → `computer-use` → Enable. Scopes per-project. Disable after recording.
- **Requires:** macOS only, Claude Code v2.1.85+, Pro/Max claude.ai auth, interactive session, Accessibility + Screen Recording permissions.
- **Safety:** machine-wide lock, per-app approval, terminal excluded from screenshots, global `Esc` to abort.

Full details: `~/projects/sergei/docs/plans/mcp-config-hygiene-cleanup.md`

---

## Approval Log

- **2026-04-19, Round 1**: Plan drafted. Approach approved — screencapture + ffmpeg, full-screen iTerm2 recording, autonomous script.
