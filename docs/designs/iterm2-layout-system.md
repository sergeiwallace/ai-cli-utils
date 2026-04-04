---
title: "iTerm2 YAML Layout Templating System — Design"
category: design
tags: [iterm2, layout, yaml, dynamic-profiles, panes, tmux, templates]
status: implemented
source: ai-cli-utils
---

# iTerm2 YAML Layout Templating System — Design

**Status:** IMPLEMENTED — 2026-04-04

**Created:** 2026-04-04

**Task:** `[AI-CLI-26]`

**Related:**
- `docs/designs/iterm2-title-color-system.md` — color slot assignment system this integrates with
- `docs/roadmap/master-roadmap.md` — `[AI-CLI-24]` per-project color preference, `[AI-CLI-20]` UX redesign

## Table of Contents

- [Problem Statement](#problem-statement)
- [Design Decisions](#design-decisions)
- [YAML Schema](#yaml-schema)
- [Dynamic Profile Generation](#dynamic-profile-generation)
- [Python API Launch Script](#python-api-launch-script)
- [CLI Command](#cli-command)
- [Integration with `ai c N`](#integration-with-ai-c-n)
- [File Locations](#file-locations)
- [Implementation Phases](#implementation-phases)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Problem Statement

With 12–16 panes per tab across multiple projects, the current setup has no file-driven way to define, reproduce, or fine-tune layouts. Each session is hand-assembled. Colors are buried in iTerm2's binary plist preferences. There's no way to template a "sw-dev" workspace with consistent splits, directories, and colors and spin it up in one command.

Goal: define workspaces in YAML files. Running `ai layout sw-dev` (or launching `ai c 5` when a matching layout file exists) builds the full iTerm2 window — tabs, pane splits, profiles, colors, working directories, startup commands — exactly as specified. Colors and profile overrides are file-driven via iTerm2 Dynamic Profiles (JSON, hot-reloaded, no plist editing required).

---

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Config file location | User home vs project repo vs XDG | XDG (`~/.config/iterm2/layouts/`) | Machine-local, easily symlinked to a dotfiles repo, consistent with existing iTerm2 config convention | Pending |
| 2 | Color override mechanism | iTerm2 Python API per-session vs Dynamic Profiles | Dynamic Profiles (generated JSON) | Persistent across restarts; can be committed; works without a running script | Pending |
| 3 | Pane split model | Flat list with `split` fields vs nested tree | Nested tree | Matches iTerm2's actual split hierarchy; avoids ambiguity in >2 splits | Pending |
| 4 | `ai c N` auto-apply | Always auto-apply if layout exists vs explicit flag | Auto-apply if `sw-N.yaml` exists, skip silently if not | Zero friction for configured sessions; no change for unconfigured ones | Pending |
| 5 | Profile inheritance | All colors inline vs inherit from named base profile | Inherit from base + only override what differs | Avoids duplication; one base profile change propagates | Pending |

### Decision Details

#### Decision 1: Config file location

##### (a) XDG (`~/.config/iterm2/layouts/`)

**Pros:**
- Consistent with `~/.config/iterm2/` convention already in use (logs, AppSupport symlink)
- Easily managed by a dotfiles repo or symlinked
- Not inside any project — works for cross-project layouts

**Cons:**
- Not version-controlled by default (user must add to dotfiles manually)

##### (b) Project repo (`~/projects/foo/.iterm2/layouts/`)

**Pros:**
- Version-controlled with the project

**Cons:**
- Layouts are machine-local config, not project code — wrong home
- Layout files reference absolute paths that differ per machine

##### Recommendation

XDG. Layouts are personal workspace config, not project artifacts. Users who want version control can symlink from a dotfiles repo.

---

#### Decision 2: Color override mechanism

##### (a) iTerm2 Python API per-session color changes

**Pros:**
- Applied at runtime, no persistent files

**Cons:**
- Colors reset if the session is closed and reopened
- Requires the script to be running for colors to apply

##### (b) Dynamic Profiles (generated JSON files)

**Pros:**
- Persistent — survive iTerm2 restarts
- Hot-reloaded by iTerm2 when files change (no restart needed)
- File-driven — diff-able, commit-able
- Can inherit from a base profile and only override specific color keys

**Cons:**
- Requires generating JSON files alongside the YAML
- Profile names must be stable (GUID-keyed)

##### Recommendation

Dynamic Profiles. The YAML-to-JSON generation step is handled once at layout apply time (`ai layout <name>`). Generated files live in `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-generated/`. The layout script sets each pane to use its generated profile by name.

**Icon integration:** The Dynamic Profile generated per tab also includes a `Custom Icon Path` pointing to a runtime-generated tinted PNG (same Pillow pipeline as the color system described in `docs/designs/iterm2-title-color-system.md`). The icon tint is auto-derived from `colors.tab_color` via HSL color theory. No static icon variants or pre-baked assets needed — the layout apply step generates both the profile JSON and the icon PNG.

---

#### Decision 3: Pane split model

##### (a) Flat list with `split` direction fields

**Pros:**
- Simple to write for 2-pane layouts

**Cons:**
- Ambiguous for 3+ panes: "split the second pane vertically" — relative to what?
- Doesn't map cleanly to iTerm2's actual recursive split tree

##### (b) Nested tree (panes contain child panes)

**Pros:**
- Directly models iTerm2's split structure
- Unambiguous for any depth: a pane is either a leaf (runs a command) or a container (splits into children)
- Natural to extend: add a level of nesting for more splits

**Cons:**
- Slightly more verbose YAML for simple cases

##### Recommendation

Nested tree. The verbosity is worth the precision — especially for 12–16 pane layouts where split order matters. See schema below.

---

#### Decision 4: `ai c N` auto-apply

Auto-apply if `~/.config/iterm2/layouts/sw-N.yaml` exists. If no file, launch normally (current behavior). This means zero-config for existing sessions and full templating for configured ones — no flag required.

---

#### Decision 5: Profile inheritance

Each layout YAML specifies a `base_profile` (e.g. `"Claude Base"`). The generated Dynamic Profile inherits all settings from the base and only overrides what's specified in YAML (colors, title settings, etc.). This means updating the base profile in iTerm2 propagates globally; YAML only carries the delta.

---

> **Feedback Round 1 (2026-04-04):** All 5 decisions approved. Additional context: layouts use a single maximized window, tabs as workflow tiers (active multi-pane work / back burner sessions / utilities). Open questions resolved: (1) single window only, (2) create new window if layout already open, (3) leaf pane `name` field deferred to Phase 3.

---

## YAML Schema

### Layout file: `~/.config/iterm2/layouts/<name>.yaml`

```yaml
# ~/.config/iterm2/layouts/sw-dev.yaml
name: sw-dev
description: "Sergei main dev workspace — sergei + sw-5 CC session"

tabs:
  - name: "sw-5"
    base_profile: "Claude Base"          # iTerm2 profile to inherit from
    colors:
      background: "#0D0D1F"             # override only what you want
      foreground: "#E8E8F0"
      tab_color: "#3B4BC8"              # color shown in tab bar
    root:                               # root pane — becomes the tab's first session
      dir: "~/projects/sergei/.worktrees/sw-5"
      command: "ai c 5"
      split:
        direction: vertical             # split this pane vertically
        ratio: 0.65                     # left pane gets 65% width
        right:
          dir: "~/projects/sergei"
          command: "git log --oneline -20"
          split:
            direction: horizontal
            ratio: 0.5
            bottom:
              dir: "~/projects/sergei"
              command: null             # shell prompt, no startup command

  - name: "gemini"
    base_profile: "Gemini Base"
    colors:
      background: "#0F1A0F"
      tab_color: "#2E7D32"
    root:
      dir: "~/projects/sergei"
      command: "ai gemini -m flash"

  - name: "monitoring"
    base_profile: "Default"
    root:
      dir: "~"
      command: "htop"
      split:
        direction: vertical
        ratio: 0.5
        right:
          dir: "~"
          command: "ai quota status"
```

### Schema reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Layout name, used as `ai layout <name>` argument |
| `description` | string | no | Human-readable description |
| `tabs[].name` | string | yes | Tab label |
| `tabs[].base_profile` | string | yes | iTerm2 profile to inherit (must exist in iTerm2) |
| `tabs[].colors` | map | no | Color overrides — any key from iTerm2 color schema |
| `tabs[].root` | pane | yes | Root pane definition (recursive) |
| `pane.dir` | string | no | Working directory (default: `~`) |
| `pane.command` | string\|null | no | Startup command. `null` = shell prompt |
| `pane.split.direction` | `horizontal`\|`vertical` | — | Required if `split` present |
| `pane.split.ratio` | float 0–1 | no | Fraction of space the primary pane takes (default: 0.5) |
| `pane.split.right` / `.bottom` | pane | — | Child pane for vertical / horizontal split respectively |

---

## Dynamic Profile Generation

At layout apply time, for each tab that specifies color overrides, the system writes a generated Dynamic Profile JSON file:

**Output:** `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-generated/<layout-name>-<tab-name>.json`

```json
{
  "Profiles": [{
    "Name": "ai-cli:sw-dev:sw-5",
    "Guid": "ai-cli-sw-dev-sw-5",
    "Dynamic Profile Parent Name": "ClaudeCode",
    "Background Color": {
      "Red Component": 0.051, "Green Component": 0.051,
      "Blue Component": 0.122, "Alpha Component": 1.0
    },
    "Tab Color": {
      "Red Component": 0.231, "Green Component": 0.294,
      "Blue Component": 0.784, "Alpha Component": 1.0
    },
    "Custom Icon Path": "/Users/sergei/.local/state/ai-cli-utils/iterm2-icons/sw-dev-sw-5.png"
  }]
}
```

iTerm2 hot-reloads this directory — no restart required. GUIDs are deterministic (`ai-cli-<layout>-<tab>`), so re-running `ai layout` is idempotent.

Color key mapping (hex → iTerm2 RGB dict) is handled by a small utility function in the layout module. The `Custom Icon Path` PNG is generated by the same Pillow pipeline used by `ai c N` sessions — the layout apply step calls the shared icon generator with the tab color as input.

---

## Python API Launch Script

The launch script (`~/.config/iterm2/scripts/ai_layout_runner.py` or invoked directly via iTerm2 Python API) does:

1. Parse the YAML layout file
2. Generate Dynamic Profile JSON for any tabs with color overrides
3. Create an iTerm2 window (or new tab in the current window)
4. For each tab:
   - Create tab with the generated profile
   - Recursively build the pane tree (depth-first, split each pane per the schema)
   - For each leaf pane: `cd <dir>` then send startup command
5. Focus the first tab

The recursive pane builder maps directly to `session.async_split_pane(vertical=bool, profile=str, percent=int)`.

---

## CLI Command

```
ai layout <name>              # apply layout from ~/.config/iterm2/layouts/<name>.yaml
ai layout list                # list available layout files
ai layout validate <name>     # validate YAML schema without applying
ai layout profiles <name>     # regenerate Dynamic Profiles without rebuilding window
```

Implementation: new `layout.py` module in `src/ai_cli/`, registered as `ai layout` subcommand group.

---

## Integration with `ai c N`

When `ai c 5` launches:

1. Check if `~/.config/iterm2/layouts/sw-5.yaml` exists
2. If yes: run layout apply instead of bare `ai c` launch
3. If no: existing behavior unchanged

The `ai c N` → layout integration happens in `main.py`'s session launch path. The layout YAML can embed `command: "ai c 5"` for the primary pane, so the CC session still starts normally inside the templated layout.

---

## File Locations

| Path | Purpose |
|------|---------|
| `~/.config/iterm2/layouts/*.yaml` | User layout definitions |
| `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-generated/` | Generated Dynamic Profile JSON (auto-managed, do not edit manually) |
| `src/ai_cli/layout.py` | Layout parser, Dynamic Profile writer, CLI handlers |
| `src/ai_cli/iterm2_api.py` | iTerm2 Python API wrappers (new or extend existing) |

---

## Implementation Phases

### Phase 1 — Core (MVP)

- YAML parser + schema validation (pydantic)
- Dynamic Profile JSON generation from color overrides
- Python API launch script: build window from YAML (tabs + recursive pane splits)
- `ai layout <name>` CLI command
- `ai layout list` and `ai layout validate`
- Tests: schema validation, profile generation, pane tree builder (unit); one integration test with a minimal YAML

### Phase 2 — `ai c N` integration

- Auto-detect `sw-N.yaml` on `ai c N` launch
- Apply layout transparently if file exists

### Phase 3 — Fine-tuning UX

- `ai layout profiles <name>` — regenerate profiles without rebuilding window (for live color tweaks)
- Support `env` map per pane (environment variables injected before startup command)
- Support `maximize` flag per pane (opens pane maximized, toggles back on focus)
- Support `title` override per pane (sets session name)

---

> **Feedback Round 1:** Does the phasing feel right? Should Phase 2 (ai c N integration) move to Phase 1?
> - <enter feedback here>

---

## Open Questions

1. **Multiple windows vs tabs**: Should a layout be able to define multiple iTerm2 windows, or always a single window with multiple tabs? (Current design: single window, multiple tabs.)
2. **Layout update vs create**: If a layout with matching name is already open, should `ai layout <name>` add tabs to the existing window or create a new window?
3. **Pane naming**: Should leaf panes support a `name` field that sets the session name (feeds into the color/title system from AI-CLI-20)?

> **Feedback Round 1 (2026-04-04):** (1) Single window only — user maximizes one window full-screen, tabs are workflow tiers. (2) Create new window if layout already open. (3) Pane `name` field deferred to Phase 3.

---

## Approval Log

| Date | Round | Key Decisions |
|------|-------|---------------|
| 2026-04-04 | Round 1 | All 5 decisions approved. Single window, tabs as workflow tiers. Layout-already-open → create new window. Pane `name` field deferred to Phase 3. Icon generation uses shared Pillow pipeline from color system. |
