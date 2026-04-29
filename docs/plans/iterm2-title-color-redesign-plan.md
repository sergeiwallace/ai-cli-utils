---
title: "iTerm2 Title & Color System Redesign — Plan"
category: plan
tags: [iterm2, tab-title, tab-color, session-title, fleet, gemini, remote, mosh, research]
status: implemented
task: AI-CLI-27
source: ai-cli-utils
related_docs:
  - docs/bugs/iterm2-title-color-system.md
  - docs/plans/iterm2-smart-titles-plan.md
  - docs/designs/iterm2-title-color-system.md
---

# iTerm2 Title & Color System Redesign — Plan

**Status:** ACTIVE

**Created:** 2026-04-02

**Related:** `docs/bugs/iterm2-title-color-system.md`, `docs/plans/iterm2-smart-titles-plan.md`

## Table of Contents

- [Overview](#overview)
- [Step-by-Step Plan](#step-by-step-plan)
- [Human Gates](#human-gates)
- [Opus Sub-Agent Prompt](#opus-sub-agent-prompt)
- [Approval Log](#approval-log)

---

## Overview

The iTerm2 per-pane title and rolling tab color system was refactored in late March 2026 to replace a flawed shared-state combined-title architecture. The refactor resolved ghosting/contamination bugs but introduced or exposed 7 new bugs covering: color collision across same-numbered sessions, broken remote session titles (mosh filtering), Gemini title not being applied, Gemini session resume failing when launched from wrong directory, and limited icon color variety.

This plan covers the full redesign workflow: research, design doc (opus-authored), human review, and implementation. The goal is a robust, well-researched design before any code is written.

---

## Step-by-Step Plan

### Step 1 — Append raw user feedback to bug doc (Claude Sonnet, now)

Append the user's raw feedback message to `docs/bugs/iterm2-title-color-system.md` as a `## Raw User Feedback` section, then commit and push.

**Deliverable:** Bug doc contains both structured bug descriptions and verbatim user feedback.

---

### Step 2 — Write research prompt R-50 and add to registry (Claude Sonnet, now)

Write research prompt R-50 and add it to the project research prompt registry following all registry conventions.

**Research scope for R-50:**
- iTerm2 customization best practices: Dynamic Profiles, escape sequences (OSC, DCS, `\033]1337;`), tab color/title/icon control, tmux passthrough, mosh constraints
- Programmatic tab/pane title and color customization, icon and symbol usage for session state/status tracking, and auto-rotating color systems when managing parallel AI coding agent sessions — specifically: what each terminal supports (Ghostty, WezTerm, Kitty, Alacritty, etc.) for runtime title/color/icon control via escape sequences or APIs; how they handle remote sessions over SSH/mosh; whether and how they support custom tab/pane icons or symbols to communicate session type and live state (running, waiting, done, error); how others have implemented dynamic color assignment that compares against neighboring tabs/panes and selects visually distinct colors rather than cycling through a fixed palette; and any prior art or open-source implementations from the AI agent or developer productivity community that tackle this class of problem
- Dynamic icon colorization: whether runtime icon color changes are feasible via OSC sequences, badge overlays, or other mechanisms vs. requiring static profile proliferation

**Deliverable:** R-50 entry in research prompt registry, committed and pushed.

---

### [Human Gate 1] — Review and approve research prompt R-50

Review the R-50 entry in the research prompt registry. Provide feedback or approve. No research run is launched until approved.

---

### Step 3 — Launch Gemini deep-think research run (Claude Sonnet, after gate 1)

Following the research workflow procedure:
1. Pre-run: commit and push any outstanding changes to all 3 branches (main, worktree, server)
2. Run: `ai gemini -m deep-think -o ~/projects/ai-cli-utils/docs/research/iterm2-terminal-customization-research.md` with the approved R-50 prompt
3. Post-run: commit and push the research doc immediately before any related work

**Deliverable:** `docs/research/iterm2-terminal-customization-research.md` committed and pushed.

---

### Step 4 — Launch opus sub-agent to write design doc (after step 3)

Spawn opus sub-agent with the prompt below. The agent reads all relevant code and docs (including the new research doc) and writes `docs/designs/iterm2-title-color-system.md`.

See [Opus Sub-Agent Prompt](#opus-sub-agent-prompt) below for the full prompt.

**Deliverable:** `docs/designs/iterm2-title-color-system.md` written and committed.

---

### [Human Gate 3] — Review and approve design doc

Review the design doc. Provide feedback. Iterate until approved.

---

### Step 5 — Update bug doc with confirmed fixes (Claude Sonnet, after gate 3)

Once the design doc is approved, update `docs/bugs/iterm2-title-color-system.md` — replace the placeholder Diagnosis Approach section with the concrete root causes and confirmed fix approaches from the approved design.

---

### Step 6 — Implementation (separate session/plan)

Implementation of the approved design. A separate implementation plan will be created at that point.

---

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| **Gate 1** | Step 2 | Approve research prompt R-50 before launching run |
| **Gate 2** | Step 4 | Approve design doc before implementation begins |

---

## Opus Sub-Agent Prompt

The following is the exact prompt used to spawn the opus sub-agent in Step 4.

```text
You are an architect agent. Your job is to read all relevant code and documentation,
then produce a design doc for the iTerm2 tab title and color system in the
ai-cli-utils project. You are explicitly free to propose a full redesign or clean
rewrite if that better satisfies the requirements — do not feel anchored to the
current implementation.

Do not implement anything. Read-only. Output is one design doc.

---

## Step 1 — Read all of these files in full before writing anything

**Code:**
- src/ai_cli/main.py
  Pay particular attention to: _ITERM2_TAB_COLORS, _ITERM2_PROFILE_MAP,
  _emit_iterm2_profile_setup, the bash template in get_engine_script (specifically
  _it2, _iterm2_fleet_setup, _iterm2_status, the _iterm_env_flags propagation block,
  and the os.execvp launch paths for both local and remote sessions including the
  args.is_remote branch)
- ~/.zshrc — search for and read the _ai_iterm2_precmd function

**Core iTerm2 design docs (ai-cli-utils project):**
- docs/plans/iterm2-smart-titles-plan.md
  Previously approved plan, partially implemented
- docs/bugs/iterm2-title-color-system.md
  Active bug report with 7 bugs + verbatim user feedback. Primary statement of what
  is broken and what correct behavior looks like.

**Supporting design docs:**
- docs/plans/iterm2-fleet-config-plan.md
  Foundational plan for Dynamic Profiles + escape sequence architecture
- docs/plans/iterm2-ntfy-session-status-plan.md
  Approved design decisions for tab color scheme, status states, session type
  visual identity table
- docs/tools/iterm2-setup.md
  Ground truth for what is actually configured in iTerm2 right now: tmux passthrough,
  DCS wrapping, profile inventory, mosh behavior
- docs/research/iterm2-fleet-management-config.md
  Comprehensive research on iTerm2 capabilities: Dynamic Profile JSON format, escape
  sequence mechanics, mosh limitations, icon formats, profile switching. Factual
  foundation for the design.
- docs/plans/notification-system-redesign.md
  Defines channel ownership: iTerm2 tab/badge is the ambient in-terminal status
  channel; ntfy owns push notifications. Read to understand scope boundary — do not
  redesign the notification boundary.

**Research doc (written after the above docs — most current findings):**
- docs/research/iterm2-terminal-customization-research.md

---

## Step 2 — Requirements

### Retained from prior approved design
- Per-pane title ownership: each pane independently owns and sets its own title via
  OSC 0, no shared state files between panes
- Session type symbols: * for CC, ✦ for Gemini, $ for shell
- Status symbols in title: ▶ running, ⏸ waiting, ✓ done, ✗ error, ↻ resuming
- Shell panes: ShellUtility profile, title shows "$ {project-name}" derived from
  git root basename
- Rolling tab color: each CC/Gemini session slot gets a distinct background color;
  the profile icon color contrasts with the background
- iTerm2 tab/badge is the ambient in-terminal status channel — scope is fixed,
  do not expand into push notifications

### Revised/new requirements (these override the prior plan where they conflict)

1. No multi-pane combined titles. Drop the abbreviation/aggregation logic from the
   prior plan entirely. Each pane shows only its own session info. Tab title =
   focused pane's title. There is no combined "c-sw-{▶1|⏸2}" format.

2. Type symbols are pane-header only, not tab title. The *, ✦, $ symbols are
   meaningful in split-pane headers where the profile icon may not be visible. In
   the tab bar, the profile icon (Claude logo, Gemini logo, terminal icon) is the
   type indicator — do not also prefix the tab title text with * or ✦. Tab title
   text is just "{status_sym} {session-name}".

3. Remote sessions via mosh must work correctly. This is a hard constraint — mosh
   filters \033]1337; (iTerm2-proprietary) sequences entirely, so SetProfile and
   SetColors=tab= never reach iTerm2 from inside a mosh session. Mosh also prepends
   "[mosh] " to OSC 0 window titles. The design must address this explicitly: what
   can and cannot work for remote sessions, and what the best achievable UX is given
   the constraint.

4. Gemini tab title must show "{status_sym} {session-name}". Currently shows
   "Default". Root cause must be identified in the design (likely the GeminiCLI
   profile has a fixed title override, or the OSC 0 sequence is being emitted before
   the profile is applied and the profile resets it).

5. "ai g N -p PROJECT" must work from any directory. Gemini derives its chats
   directory from the working directory at launch time. Running "ai g 1 -p artelier"
   from  fails to resume the artelier session because it looks in
   the wrong chats dir. The design must address this — likely by ensuring gemini
   always launches from the target project's worktree directory, and by investigating
   whether gemini exposes a --chats_dir flag or equivalent.

6. Color collision must be resolved. The current (num - 1) % 12 scheme assigns the
   same color to all sessions with the same trailing number (e.g. c-ai-cli-2,
   c-proj-2, c-other-2 all get orange). The design must ensure open tabs in the same
   iTerm2 window have visually distinct colors. Propose a concrete strategy —
   whether state-file-based, ITERM_SESSION_ID window enumeration, or another approach.

7. Richer icon color variety. Currently only 3 Claude logo color variants (coral,
   white, dark navy) and 1 Gemini variant. The user wants 6+ Claude logo color
   options with genuinely distinct, colorful choices (purple, gold, cyan, teal,
   green, etc.) — not just legibility fallbacks. Investigate whether dynamic icon
   colorization is feasible via OSC sequences (badge, inline image, etc.) or whether
   more iTerm2 profiles are required. Be explicit about what is technically possible.

---

## Step 3 — Output

Write the design doc to:
docs/designs/iterm2-title-color-system.md

Use this frontmatter:
---
title: "iTerm2 Tab Title and Color System — Design"
category: design
tags: [iterm2, tab-title, tab-color, session-title, fleet, gemini, remote, mosh]
status: draft
source: ai-cli-utils
---

The doc must include a ## Table of Contents. Required sections:
- System overview — architecture summary, what the system does and doesn't do
- Session type behavior — per type: CC local, CC remote (mosh), Gemini local,
  Gemini remote, shell pane. For each: profile, tab color, tab title format,
  pane header format, status update behavior
- Color assignment strategy — how rolling colors are assigned without collision;
  concrete mechanism
- Icon color system — how many variants, what colors, how they're set (profiles
  vs. dynamic), mapping of background color to icon color
- OSC/DCS sequence reference — which sequences are used where, DCS wrapping rules
  for tmux, what mosh passes vs. filters
- Gemini chats directory — how directory-at-launch affects session resume, how
  the design handles "ai g N -p PROJECT" from any directory
- What changes vs. current implementation — honest assessment of what's kept,
  what's fixed, what's redesigned
- Open questions and constraints — anything that needs a decision or has an
  unresolved technical dependency
- Approval Log — empty table with headers (Date / Round / Decisions)
```text

---

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| 2026-04-02 | 0 | Plan drafted |
