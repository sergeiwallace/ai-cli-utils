---
title: "iTerm2 Neighbor-Aware Pane Color — Plan"
category: plan
tags: [iterm2, tab-color, pane-color, layout, color-collision, spatial-awareness]
status: draft
task: AI-CLI-77
source: ai-cli-utils
template_version: "plan-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# iTerm2 Neighbor-Aware Pane Color — Plan

**Status:** DRAFT

**Created:** 2026-04-09

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
  with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
  they navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this
  once AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
  `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
  (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
  Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
  `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

## Table of Contents

- [Overview](#overview)
- [Problem Analysis](#problem-analysis)
- [Part 1 — Immediate Manual Fix](#part-1--immediate-manual-fix)
  - [Options](#options-part-1)
  - [Decision: Option B — `ai recolor` command](#decision-option-b--ai-recolor-command)
- [Part 2 — Neighbor-Aware Color Selection at Session Start](#part-2--neighbor-aware-color-selection-at-session-start)
  - [Options](#options-part-2)
  - [Decision: Option A — tmux geometry at startup](#decision-option-a--tmux-geometry-at-startup)
- [Part 3 — Dynamic Re-coloring on Pane Moves](#part-3--dynamic-re-coloring-on-pane-moves)
  - [Options](#options-part-3)
  - [Decision: Option C — Deferred / Manual Trigger](#decision-option-c--deferred--manual-trigger)
- [Color Similarity Definition](#color-similarity-definition)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Overview

Adjacent split panes in an iTerm2 window should have visually distinct tab/background colors and icon tints. The current collision-avoidance system assigns the first free palette slot globally — it knows "no two active sessions share a color" but is blind to spatial proximity. Panes can end up with hue-similar colors (e.g. teal next to lime, or sky-blue next to cyan) that are indistinguishable when arrayed side-by-side.

This plan covers two separate deliverables:

1. **Immediate fix** — a `ai recolor` command that reads the current tmux window layout, computes pane adjacency, and re-assigns colors so no two adjacent panes share a similar hue.
2. **Ongoing enforcement** — neighbor-aware color selection baked into the session-start path so new sessions automatically avoid neighbors' colors.

Out of scope: cross-tab color coordination (adjacent *tabs* are not split panes and are sufficiently separated visually), remote session color changes (mosh filters SetColors; already documented), color changes triggered by user rearranging panes post-start (addressed under Part 3).

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

---

## Problem Analysis

### Current color assignment

The `_assign_iterm2_color()` function reads `color-leases.json` to find which palette slots are occupied, then picks the **lowest-free slot** (or a hash-based fallback if all slots are taken). "Occupied" means any live session holds the slot — there is no spatial component.

### Why adjacent-pane collision still happens

The 16-color palette cycles in order: red → orange → yellow → green → teal → sky_blue → blue → purple → pink → cyan → deep_orange → lime → indigo → rose → amber → emerald. Adjacent slots in this list often have similar hues:
- slots 3/4: green (#2ecc71) / teal (#1abc9c) — ~15° hue difference
- slots 4/5: teal (#1abc9c) / sky_blue (#039be5) — ~30° hue difference
- slots 6/12: blue (#1e88e5) / indigo (#3949ab) — ~15° hue difference
- slots 9/10: cyan (#00acc1) / deep_orange (#ff5722) — far apart, but slot 9 and 11 (cyan/lime) — ~60°

If session A takes slot 3 and session B takes slot 4, and they're adjacent panes, the result is visually muddy.

### What tmux knows

`tmux list-panes -t <session> -F "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height} #{pane_pid}"` returns geometry for every pane in a window. Two panes are adjacent if their bounding boxes share an edge (not just a corner). This is a pure geometric computation — no iTerm2 API required.

The critical mapping:
- tmux pane PID → session bash PID → environment var `AI_TMUX_SESSION` → session name → color-leases.json entry

This chain is reliable for local sessions. Remote sessions (mosh/SSH) are a different matter (see Open Questions).

---

## Part 1 — Immediate Manual Fix

### Options (Part 1)

#### **Option A: `ai layout recolor` — only for layout-managed windows**

Add a `recolor` subcommand under `ai layout`. When invoked, reads the active layout YAML, gets all its tab colors, and re-assigns them so adjacent tabs in split-pane layouts have non-similar hues.

**Pros:**
- Clean scoping — only operates on layout-defined windows
- No tmux geometry parsing needed (topology comes from the YAML)

**Cons:**
- Useless for manually arranged windows (no YAML)
- User's "Main CC Sessions" window is not a YAML-managed layout — this wouldn't help them now
- All layout panes would need to be re-profiled and updated, not just swapped colors

---

#### **Option B: `ai recolor` — tmux-geometry-based, works for any window** ✅ RECOMMENDED

New top-level command `ai recolor` (and optionally `ai recolor -w <window>` for a specific tmux window). Algorithm:

1. Get active tmux window (`$TMUX_PANE` env var → current window)
2. `tmux list-panes -F ...` to get all pane geometries + PIDs
3. Resolve each PID → `AI_TMUX_SESSION` env → color-leases.json entry
4. Build adjacency graph (two panes adjacent if their bounding boxes share an edge)
5. Run **greedy graph coloring**: for each pane (ordered by adjacency degree desc), pick the lowest palette slot whose hue is ≥ 60° from all assigned neighbors' hues
6. For each pane whose color changed: update color-leases.json + regenerate Dynamic Profile JSON + send SetColors escape sequence to the live pane via `ai internal` call
7. Print summary of changes

**Pros:**
- Works for any tmux window, not just layout-managed ones
- Can be run on demand anytime
- Same mechanism reused by Part 2 (startup neighbor check)
- No new daemons

**Cons:**
- Requires resolving PID → session name, which involves reading each pane's env vars (`tmux show-environment -t <pane>` or reading `/proc/<pid>/environ` — needs testing on macOS)
- Color reassignment mid-session sends a SetColors escape to the live pane — minor visual "pop" as colors update
- Only applies to the current tmux window; doesn't recolor all windows at once (by design)

---

#### **Option C: Config-driven per-session color pins**

Let users manually pin colors per session in `config.toml` (`[iterm2.sessions."c-sw-5"] tab_color = "purple"`). User manually ensures no adjacent sessions share similar colors.

**Pros:**
- Zero code — already supported today
- Fully predictable

**Cons:**
- Entirely manual — requires user to manage the assignment themselves
- Doesn't scale as sessions multiply
- Doesn't help with the "I moved a pane" scenario at all

---

### **Decision: Option B — `ai recolor` command**

Option B works on any tmux window regardless of whether a layout YAML exists, reuses the same geometry + graph-coloring logic as Part 2, and gives the user a simple `ai recolor` they can run anytime. Option A is too narrow. Option C is manual overhead.

---

## Part 2 — Neighbor-Aware Color Selection at Session Start

When a new `ai c` or `ai g` session starts, it calls `_assign_iterm2_color()` to pick a palette slot. Currently that function only excludes globally-leased slots. It should also exclude slots whose hue is similar to adjacent panes in the current tmux window.

### Options (Part 2)

#### **Option A: tmux geometry at startup** ✅ RECOMMENDED

Extend `_assign_iterm2_color()` with an optional neighbor-exclusion step:

1. If `$TMUX_PANE` is set (we're inside tmux), enumerate pane geometries for the current window
2. Resolve each existing pane → session name → leased color
3. Identify which panes will be geometrically adjacent to the new pane (the new pane replaces or splits an existing pane — we can approximate its position from context, or use the most conservative assumption: treat all existing panes as potentially adjacent if there are ≤ 4 panes)
4. Exclude all palette slots whose hue is within 60° of any adjacent pane's color
5. Pick lowest free slot from remaining candidates

**Pros:**
- No daemon, no background process
- Runs once at session start — zero ongoing overhead
- Covers the common case (new session opened next to an existing one)

**Cons:**
- The new pane doesn't exist yet when color is assigned — approximate adjacency is imperfect
- If there are many panes (6+), the approximation becomes less useful
- Doesn't handle post-start pane moves (covered under Part 3)

---

#### **Option B: Defer color assignment until after pane is visible**

Split the session-start flow: assign a placeholder color, start the session, then after the pane is created read its actual position from tmux, and re-assign the final color.

**Pros:**
- Exact adjacency known (pane exists)

**Cons:**
- Requires a "re-color on startup" hook inside the session bash script
- Two Dynamic Profile writes per session start (placeholder + final)
- Visible color change at session start
- Significantly more complex

---

#### **Option C: Conservative "all current panes are adjacent" heuristic**

Skip geometry entirely. When assigning a color for a new session, exclude any palette slot that's similar (≤ 60° hue) to *any* currently leased slot in the same iTerm2 window.

Getting the iTerm2 window for the current session: `$ITERM_SESSION_ID` encodes window ID as the first segment (e.g. `w0t0p0` → window 0). Cross-reference leases that share the same window prefix.

**Pros:**
- Simpler than geometry parsing
- No pane-geometry resolution needed

**Cons:**
- Over-excludes: treats all panes in a window as neighbors, even if they're on opposite sides
- With 6+ panes and 16 colors, could force undesirable color choices
- Requires iTerm2 window-ID tracking across sessions (currently not stored in leases)

---

### **Decision: Option A — tmux geometry at startup**

Option A is the most correct approach with the least complexity. The approximation is acceptable for the common case (new session in a 2–4 pane split). Option C is simpler but over-restricts. Option B introduces visible startup flicker and two-phase writes.

Implementation note: for the "new pane doesn't exist yet" problem, we use a conservative approach: at startup, treat all existing panes in the window as potential neighbors. This over-excludes slightly but avoids the timing race. Once the user runs `ai recolor` it'll tighten up any imperfect assignments.

---

## Part 3 — Dynamic Re-coloring on Pane Moves

When a user drags a pane to a different position (or moves it to a different tab), neighbors change. The two approaches are:

### Options (Part 3)

#### **Option A: iTerm2 Python API event listener**

Add a handler to `fleet-layout.py` (the existing iTerm2 Python daemon) for the `iterm2.SessionTerminatedNotification` / `iterm2.LayoutChangedNotification` events. On pane move, recompute neighbors and push color updates.

**Pros:**
- Event-driven (no polling), immediate response
- Exact layout data from iTerm2's internal model

**Cons:**
- `fleet-layout.py` is not always running (manual launch)
- iTerm2's Python API for layout change events is limited — `LayoutChangedNotification` does not fire for pane rearrangement, only for tab/window structural changes
- Complexity: requires the daemon to know pane→session mappings, current color leases, and how to push color changes back

---

#### **Option B: Periodic tmux poll daemon**

A background daemon (similar to `ai vpn-watch`) polls tmux pane geometry every 5–10 seconds. If it detects that two adjacent panes have similar colors, it calls `ai recolor` to fix.

**Pros:**
- Works without iTerm2 Python API
- Reuses the geometry + graph-coloring logic from Parts 1/2

**Cons:**
- Polling every 5s is resource-wasteful for an aesthetic feature
- Adds another daemon to manage (PID file, Circus watcher, etc.)
- Up to 10s latency after a pane move before recolor triggers
- Visible color "pop" every time it detects and fixes a conflict

---

#### **Option C: Deferred — manual trigger only** ✅ RECOMMENDED

Do not implement automatic post-move recoloring. Instead:
- `ai recolor` (from Part 1) is the user's explicit re-trigger after rearranging panes
- Document this clearly: "run `ai recolor` after moving panes to rebalance colors"
- If the iTerm2 Python API in a future iTerm2 release exposes a reliable pane-rearrangement event, revisit then

**Pros:**
- Zero daemon overhead
- No polling, no flicker
- Correct behavior (color changes happen when user explicitly asks for them)
- `ai recolor` already built in Part 1

**Cons:**
- Requires user action after each pane rearrangement
- If user forgets, adjacent panes may share similar colors until they run `ai recolor`

---

### **Decision: Option C — Deferred / Manual Trigger**

The cost/benefit of a polling daemon for an aesthetic feature doesn't hold up. `ai recolor` covers the use case cleanly with explicit intent. If iTerm2 exposes a reliable layout event in the future, Option A becomes viable.

---

## Color Similarity Definition

Two colors are **similar** (and therefore should not be adjacent) if their hue difference on the 360° wheel is **< 60°**. This matches human perception well for saturated terminal colors.

Implementation: convert both hex values to HSL. Compute `|h1 - h2|` and normalize to [0°, 180°]. Similar if < 60°.

Edge cases:
- Achromatic colors (white, black, grey; saturation < 0.15): never considered similar to any hue. They're always acceptable next to any color.
- Very low-saturation colors: same rule — skip hue comparison, always usable.

This threshold is configurable: `[iterm2.color] similarity_threshold_degrees = 60` in `config.toml`.

---

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes each task from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
  gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
  structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
  review against the ACs), never self-declared "done". -->

### T-01: Color similarity function

**Size:** S
**Batch:** 1

Implement `_hue_similar(hex1: str, hex2: str, threshold_deg: float = 60.0) -> bool` in `icon_generator.py` (alongside the existing color math). Handles achromatic edge case. Unit tested.

**Deliverables:**
- `src/ai_cli/icon_generator.py` — `_hue_similar()` function
- `tests/test_icon_generator.py` — tests for similar/dissimilar pairs, achromatic edge case

**Acceptance criteria:**
- [ ] Returns `True` for green/teal (< 60° apart)
- [ ] Returns `False` for green/red (180° apart)
- [ ] Returns `False` when either color is achromatic (saturation < 0.15)
- [ ] Threshold parameter respected
- [ ] Unit tests pass

**Dependencies:** None

---

### T-02: tmux pane geometry + adjacency

**Size:** M
**Batch:** 1

Implement `_tmux_pane_adjacency(window_id: str | None = None) -> dict[str, list[str]]` in `iterm2.py`. Uses `tmux list-panes` to get geometry, resolves each pane's PID to its `AI_TMUX_SESSION` env var via `tmux show-environment`, returns an adjacency dict `{session_name: [neighbor_session_name, ...]}`.

**Deliverables:**
- `src/ai_cli/iterm2.py` — `_tmux_pane_adjacency()` and supporting geometry helpers
- `tests/test_iterm2.py` — tests with mocked `subprocess.run` for tmux commands

**Acceptance criteria:**
- [ ] Correctly identifies geometric neighbors (shared edge, not just corner)
- [ ] Skips panes where `AI_TMUX_SESSION` is unset (non-ai-cli panes)
- [ ] Returns empty dict if not inside tmux
- [ ] Handles single-pane window (no neighbors)
- [ ] Unit tests pass with mocked subprocess

**Dependencies:** T-01

---

### T-03: `ai recolor` command

**Size:** M
**Batch:** 2

New top-level command `ai recolor [-w WINDOW]`. Uses T-02 for adjacency, runs greedy graph coloring (sort panes by descending degree, assign lowest-hue-distinct palette slot), then for each changed session: updates `color-leases.json`, regenerates Dynamic Profile JSON, and sends `SetColors` escape to the live pane via tmux `send-keys`.

**Deliverables:**
- `src/ai_cli/iterm2.py` — `run_recolor()` function; registered via Click command group in `main.py`
- `tests/test_iterm2.py` — recolor tests
- `docs/tools/ai-cli-usage.md` — `ai recolor` entry

**Acceptance criteria:**
- [ ] `ai recolor` runs from inside any ai session
- [ ] Adjacent panes get non-similar colors after recolor
- [ ] Dynamic Profile JSONs updated on disk
- [ ] SetColors escape sent to running panes (observable via iTerm2 tab color change)
- [ ] Sessions with pinned colors (`[iterm2.sessions]` config) are not reassigned
- [ ] Prints summary of changes (which sessions changed from what to what)
- [ ] `--dry-run` / `-d` flag prints planned changes without executing
- [ ] Unit tests pass

**Dependencies:** T-01, T-02

---

### T-04: Neighbor-aware `_assign_iterm2_color()` extension

**Size:** S
**Batch:** 2

Extend `_assign_iterm2_color()` to call `_tmux_pane_adjacency()` when inside tmux. After building the occupied-slots set, also exclude any slots whose hue is similar to a neighbor's current color. Controlled by config: `[iterm2.color] neighbor_aware = true` (default: `true`).

**Deliverables:**
- `src/ai_cli/iterm2.py` — updated `_assign_iterm2_color()`
- `tests/test_iterm2.py` — tests for neighbor exclusion path

**Acceptance criteria:**
- [ ] New session does not receive a color similar to any neighbor's color
- [ ] Falls back to any free slot if all dissimilar slots are occupied
- [ ] `neighbor_aware = false` in config bypasses the new logic
- [ ] Unit tests pass

**Dependencies:** T-01, T-02

---

### T-05: Docs + config template update

**Size:** S
**Batch:** 3

Update all relevant docs and the default config template.

**Deliverables:**
- `docs/tools/ai-cli-usage.md` — `ai recolor` section (if not done in T-03)
- `src/ai_cli/config.py` — config template comments for `similarity_threshold_degrees` and `neighbor_aware`
- `CHANGELOG.md` — entry for this feature

**Acceptance criteria:**
- [ ] `ai recolor` documented in usage reference
- [ ] Config template includes new `[iterm2.color]` keys with comments
- [ ] CHANGELOG updated

**Dependencies:** T-03, T-04

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Color math + tmux geometry foundation | Human approval of this plan |
| 2 | T-03, T-04 | `ai recolor` command + startup neighbor-awareness | Tests pass locally |
| 3 | T-05 | Docs + config template | Tests pass; UAT |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

---

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| **Plan approval** | Before coding | Approve scope, decisions, open questions |
| **UAT** | After Batch 3 | Approve `ai recolor` output manually and verify neighbor-aware startup |

---

## Open Questions

1. **PID → session name resolution on macOS.** Reading `/proc/<pid>/environ` is Linux-only. On macOS, `tmux show-environment -t <pane>` returns the environment *at pane creation time*, not the current process env. `AI_TMUX_SESSION` is set by the bash script at session start, so it should be in the pane's initial environment. Needs empirical verification — does `tmux show-environment -t %<pane_id> AI_TMUX_SESSION` return the correct value on macOS?

2. **SendKeys vs escape sequence for live recolor.** The `ai recolor` command needs to send a `SetColors` escape sequence to a running pane. Options: (a) `tmux send-keys -t <pane> "printf '\\033]...\\007'"` — noisy if pane has active process, (b) `ai color <name> <hex>` called in the pane's context — requires NATS delivery or a session-specific socket. Best approach?

3. **Hue-60° threshold.** Is 60° the right cutoff? The current palette's most-similar adjacent pair (green/teal) is ~15° apart. Indigo/blue is ~15°. Setting the threshold to 60° would reject a large portion of the palette as neighbors. Should the threshold be configurable, and what should the default be — 60°, 45°, or 30°?

4. **What constitutes "adjacent" for a single-pane-per-session layout?** If the user has a single-pane window with multiple tmux sessions stacked (not split), there are no geometric neighbors. `ai recolor` in that case is a no-op. Correct behavior?

5. **Scope: current window only, or all tmux windows?** `ai recolor` with no args operates on the current tmux window. Should there be an `ai recolor --all` flag that recolors all windows at once? Or is per-window always sufficient?

6. **Remote sessions.** Remote session panes (`c-r-*`) have their color leases on the local Mac but their tmux panes on the remote server. `tmux list-panes` on Mac won't see those panes. Should `ai recolor` skip remote sessions, or attempt to SSH and query the remote tmux layout?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- macOS tmux env resolution -->
> 2. <!-- live recolor mechanism -->
> 3. <!-- hue threshold -->
> 4. <!-- single-pane no-op -->
> 5. <!-- scope: current window or all -->
> 6. <!-- remote sessions -->
> - <enter feedback here>

---

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| 2026-04-09 | 0 | Plan drafted |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="approval_log" -->
