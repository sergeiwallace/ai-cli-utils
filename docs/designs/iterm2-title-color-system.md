---
title: "iTerm2 Tab Title and Color System — Design"
category: design
tags: [iterm2, tab-title, tab-color, session-title, fleet, gemini, remote, mosh]
status: draft
source: ai-cli-utils
---

# iTerm2 Tab Title and Color System — Design

**Status:** DRAFT

**Created:** 2026-04-02

**Related:**
- `docs/bugs/iterm2-title-color-system.md` — 7 bugs this design addresses
- `docs/test/uat-iterm2-title-color-redesign.md` — UAT test cases (`[AI-CLI-18]`)
- `docs/plans/iterm2-smart-titles-plan.md` — prior approved plan (partially superseded)
- `docs/research/iterm2-terminal-customization-research.md` — technical findings (R-50)

## Table of Contents

- [System Overview](#system-overview)
- [Session Type Behavior](#session-type-behavior)
  - [CC Local](#cc-local)
  - [CC Remote (mosh)](#cc-remote-mosh)
  - [Gemini Local](#gemini-local)
  - [Gemini Remote (mosh)](#gemini-remote-mosh)
  - [Shell Pane](#shell-pane)
- [Color Assignment Strategy](#color-assignment-strategy)
- [Configuration](#configuration)
- [Icon Color System](#icon-color-system)
- [OSC/DCS Sequence Reference](#oscdcs-sequence-reference)
- [Gemini Chats Directory](#gemini-chats-directory)
- [What Changes vs. Current Implementation](#what-changes-vs-current-implementation)
- [Open Questions and Constraints](#open-questions-and-constraints)
- [Approval Log](#approval-log)

---

## System Overview

This system provides visual identity and status feedback for parallel AI coding sessions managed by ai-cli in iTerm2. It controls three visual properties per tab/pane:

1. **Tab/pane title text** — shows session name and status symbol (e.g., `▶ c-sw-5`)
2. **Tab background color** — a rolling color from a 12-color palette, unique among currently open tabs
3. **Profile icon** — Claude logo, Gemini logo, or terminal icon shown in the tab bar and pane headers

The system operates at two timing layers:

- **Pre-launch layer** (Python, `_emit_iterm2_profile_setup`): Emits SetProfile, SetColors, and OSC 0 directly to stdout before `os.execvp` hands control to tmux. No DCS wrapping needed because we are not yet inside tmux.
- **In-session layer** (bash, `_iterm2_fleet_setup` / `_iterm2_status`): Runs inside the tmux session. All iTerm2-proprietary sequences (`\033]1337;...`) are DCS-wrapped via the `_it2` helper. OSC 0 title updates also go through `_it2` for tmux passthrough. Status updates fire on session start, restart, exit (done/error), and resume.

**Scope boundary:** This system is the ambient in-terminal status channel only. Push notifications are owned by ntfy. OSC 9 is eliminated. This design does not expand into notification territory.

**What this system does NOT do:**
- Combined multi-pane tab titles (dropped — each pane owns its own title independently)
- Window titles (heuristic or AI-generated — dropped from scope)
- Badge text overlays (not used for status; reserved for future use)
- tmux status bar customization (out of scope)

---

## Session Type Behavior

### CC Local

| Property | Value |
|----------|-------|
| **Profile** | One of `ClaudeCode-{color}` variants (see [Icon Color System](#icon-color-system)) |
| **Tab color** | Rolling color from 12-color palette, assigned by collision-free slot (see [Color Assignment Strategy](#color-assignment-strategy)) |
| **Tab title** | `▶ c-sw-5` (status symbol + tmux session name) |
| **Pane header** | `[Claude logo] * ▶ c-sw-5` (type symbol + status + name) |
| **Status updates** | `▶` running, `✓` done, `✗` error, `↻` resuming, `⏸` waiting |

**Tab title vs. pane header distinction:** In the tab bar, the profile icon (Claude logo) already communicates session type, so the `*` type symbol is redundant and omitted. In pane headers (visible in split-pane layouts), the profile icon may not render or may be too small, so the `*` type symbol is included as a text fallback.

**Title format:**
- Tab bar: `{status_sym} {session_name}` — e.g., `▶ c-sw-5`
- Pane header: `* {status_sym} {session_name}` — e.g., `* ▶ c-sw-5`

**Implementation note:** OSC 0 sets both the tab title and the pane header title to the same string. iTerm2 does not offer separate sequences for tab-only vs. pane-header-only title. Therefore, the pane header format (with type symbol) is what gets set via OSC 0, and the tab bar displays the same string alongside the profile icon. The type symbol in the tab bar is a minor redundancy that is acceptable given the constraint. If the user finds this unacceptable, the alternative is to omit the type symbol entirely and rely solely on the profile icon in both contexts.

> **Feedback (2026-04-03):** Drop `type_sym` and `status_sym` from the title for the initial implementation — use plain session name only (e.g., `c-sw-5`). Symbols deferred to a follow-up iteration.

### CC Remote (mosh)

| Property | Value |
|----------|-------|
| **Profile** | One of `ClaudeCode-{color}` variants, set pre-launch (before mosh) |
| **Tab color** | Rolling color, set pre-launch (before mosh) |
| **Tab title** | `[mosh] ▶ c-r-sw-1` — mosh prepends `[mosh] ` to all OSC 0 titles; this is unavoidable |
| **Pane header** | Same as tab title (mosh controls it) |
| **Status updates** | Initial title only; in-session status updates are filtered by mosh |

**Hard constraint — mosh filters all `\033]1337;` sequences.** This means:
- `SetProfile` emitted from inside the remote tmux session never reaches iTerm2
- `SetColors=tab=` emitted from inside the remote tmux session never reaches iTerm2
- OSC 0 title updates DO pass through mosh, but with `[mosh] ` prepended

**Design response:**

The pre-launch layer (Python `_emit_iterm2_profile_setup`) already runs on the local Mac before `os.execvp("mosh", ...)`. This is the correct and only place to set the profile and tab color for remote sessions. The current implementation already does this (line 2409 in main.py fires before the remote branch at line 2306).

The in-session bash functions (`_iterm2_fleet_setup`, `_iterm2_status`) will silently fail for `SetProfile` and `SetColors` when running over mosh, but OSC 0 title updates will partially work (with `[mosh] ` prefix). This is acceptable degradation.

**What works for remote sessions:**
- Profile icon (Claude logo) — set pre-launch, persists for tab lifetime
- Tab color — set pre-launch, persists for tab lifetime
- Initial title — set pre-launch: `▶ c-r-sw-1`

**What does NOT work for remote sessions:**
- In-session status symbol updates in the tab title (the `_iterm2_status` OSC 0 updates go through mosh and arrive as `[mosh] ✓ c-r-sw-1` — the status symbol works but the `[mosh]` prefix is unavoidable)
- Profile/color changes during the session (filtered by mosh)
- Re-emit of profile/color on session restart within the while loop (filtered)

**The `[mosh]` prefix is a permanent constraint of mosh's architecture.** There is no configuration to disable it. The only way to avoid it would be to use SSH instead of mosh for these connections, which trades title cleanliness for connection resilience. This is a user preference tradeoff, not a bug to fix.

**What changes from current behavior (Bug 2 fix):**
- The pre-launch `_emit_iterm2_profile_setup` must be called for remote sessions with the correct profile and color. Currently it IS called (line 2409), but the `_emit_iterm2_profile_setup` function emits `* ▶ {session_name}` as the title. The `*` should be removed from the tab title (per requirement 2). The profile and color are correctly set pre-launch.
- The current bug where the terminal/shell icon appears instead of the Claude logo suggests the pre-launch profile switch is being overridden. Root cause: the `_ai_iterm2_precmd` function in `~/.zshrc` fires on every prompt and unconditionally sets `SetProfile=ShellUtility`. When the local zsh prompt appears (between the pre-launch setup and the mosh connection establishing), precmd fires and overrides the profile. The fix: `_ai_iterm2_precmd` must check whether the current shell is about to hand off to mosh/ssh and skip the override, or ai-cli must suppress precmd during the launch sequence.

> **Feedback (2026-04-03):** The `[mosh]` prefix is avoidable. Use **OSC 1** (`\033]1;name\007`) instead of OSC 0 to set the session name. OSC 1 sets the iTerm2 "Name" field — mosh does not intercept or prepend to OSC 1, only to OSC 0. The user has already configured the iTerm2 "Session Title" dropdown to `Name` mode (not `Shell`). With this approach, the `[mosh] ` prefix never appears. The "permanent constraint" assessment above no longer applies. Additional constraint: `SetProfile` must not point to a profile that has its own title-mode override (e.g., a profile configured to use `Shell` mode) — if it does, applying the profile will revert the user's `Name` dropdown setting. Profiles should only set icon and tab color, not title configuration. Symbols (type/status) are dropped for now — defer to follow-up (can be embedded in the OSC 1 name string when re-introduced).

### Gemini Local

| Property | Value |
|----------|-------|
| **Profile** | One of `GeminiCLI-{color}` variants (see [Icon Color System](#icon-color-system)) |
| **Tab color** | Rolling color from the same 12-color palette as CC sessions |
| **Tab title** | `▶ g-art-1` (status symbol + tmux session name) |
| **Pane header** | `✦ ▶ g-art-1` (type symbol + status + name) |
| **Status updates** | Same symbols as CC: `▶` running, `✓` done, `✗` error, `↻` resuming |

**Bug 3 root cause — title shows "Default":**

Two issues combine to produce this:

1. The GeminiCLI profile in `humanware-profiles.json` does not inherit from `ClaudeCode` — it inherits from `Default`. The `Default` profile likely has its own title rules. When `SetProfile=GeminiCLI` fires, iTerm2 applies the profile's title configuration, which overwrites any OSC 0 title that was set before the profile switch.

2. The `ClaudeCode` base profile has `"Title Components": 1` in the Dynamic Profile JSON. The iterm2-setup.md doc explicitly warns: "Do NOT add `Title Components` key to Dynamic Profiles — breaks all key mappings in that profile." This key also controls how iTerm2 composes the title. Value `1` likely means "use profile name" or "use job name", which would explain why Gemini shows "Default" (the parent profile's name).

**Fix:** The OSC 0 title emission must happen AFTER the SetProfile sequence, not before. The pre-launch function currently does this correctly (profile first, then title at line 664), but the in-session `_iterm2_fleet_setup` also does this correctly (profile at line 831, title at line 837). The actual problem is that the GeminiCLI profile's `Tab Color` is set statically in the JSON, which makes it work, but the profile likely has title settings inherited from Default that override OSC 0. The fix is to ensure the GeminiCLI profile (and all humanware profiles) do NOT set any title-related keys (`Title Components`, etc.) in the Dynamic Profile JSON, and to remove `"Title Components": 1` from the ClaudeCode base profile.

Additionally, the Gemini code path in `_emit_iterm2_profile_setup` does not set a rolling tab color — it relies on the static blue in the profile JSON. This should be changed to use the same rolling color system as CC sessions.

> **Feedback:**

### Gemini Remote (mosh)

| Property | Value |
|----------|-------|
| **Profile** | One of `GeminiCLI-{color}` variants, set pre-launch |
| **Tab color** | Rolling color, set pre-launch |
| **Tab title** | `[mosh] ▶ g-r-art-1` — same `[mosh]` constraint as CC remote |
| **Pane header** | Same as tab title |
| **Status updates** | Same constraints as CC remote — initial only, mosh filters in-session updates |

Same mosh constraints and design responses as CC Remote. The pre-launch layer sets profile, color, and title before mosh takes over.

> **Feedback:**

### Shell Pane

| Property | Value |
|----------|-------|
| **Profile** | `ShellUtility` (terminal icon, grey tab color) |
| **Tab color** | Grey (`#666666`) from the ShellUtility profile |
| **Tab title** | `$ {project-name}` — derived from git root basename |
| **Pane header** | Same |
| **Status updates** | None — static title, refreshed on each prompt via `_ai_iterm2_precmd` |

No changes from current behavior. The `_ai_iterm2_precmd` function in `~/.zshrc` handles this correctly today.

> **Feedback:**

---

## Color Assignment Strategy

### Problem

The current implementation uses `(num - 1) % 12` where `num` is the trailing digit of the session name (e.g., `sw-2` -> `2`). This means all sessions with the same trailing number across different projects get the same color (Bug 1: `c-ai-cli-2`, `c-art-2`, `c-aido-2` all get orange).

### Design: Local state file with lease-based assignment

**Mechanism:** A JSON file at `~/.local/state/ai-cli-utils/iterm2/color-leases.json` tracks which color slots are currently in use.

```
// color-leases.json
{
  "leases": {
    "c-sw-5": { "slot": 0, "pid": 12345, "ts": "2026-04-02T10:30:00" },
    "c-art-2": { "slot": 1, "pid": 12346, "ts": "2026-04-02T10:31:00" },
    "g-art-1": { "slot": 2, "pid": 12347, "ts": "2026-04-02T10:32:00" }
  }
}
```

**Assignment algorithm:**

1. On session launch, `_emit_iterm2_profile_setup` reads `color-leases.json`.
2. Stale leases are pruned: any lease whose `pid` is no longer running (checked via `os.kill(pid, 0)`) is removed.
3. Collect the set of currently occupied slots.
4. Pick the lowest-numbered free slot from the 12-color palette.
5. Write the new lease (session name, slot, current PID, timestamp).
6. Use the assigned slot to index into the color and profile arrays.

**Release:** On session exit, the bash EXIT trap calls a cleanup function that removes the lease. If the trap fails to fire (crash, kill -9), the stale-lease pruning on next launch handles it.

**Why not the iTerm2 Python API for tab color enumeration?** The research doc confirms this is possible (`app.current_terminal_window.tabs`), but it requires the iTerm2 Python runtime (only available inside iTerm2 Scripts, not from a regular Python process). The local state file approach works from ai-cli's normal Python process and is simpler. The iTerm2 Python API is an escalation path if the lease file proves unreliable.

**Why not a hash of the full session name?** Hashing distributes well on average but does not guarantee no collisions among a small number of concurrent sessions (birthday problem with 12 slots and 5-10 sessions). The lease file guarantees no collisions.

**Concurrency:** File locking via `fcntl.flock` on the JSON file prevents race conditions when multiple sessions launch simultaneously.

> **Feedback:**

---

## Configuration

The iTerm2 system is configured via a TOML file at `~/.config/ai-cli-utils/iterm2.toml`. This enables/disables features independently and makes the system configurable for external open-source users who may have different preferences or terminal setups.

### Feature Flags

```toml
[iterm2]
enabled = true                  # master switch — set false to disable all iTerm2 integration

[iterm2.tab_title]
show_type_symbol = true         # include *, ✦ type prefix in tab/pane title (e.g. "* ▶ c-sw-5")
show_status_symbol = true       # include ▶ ✓ ✗ ↻ ⏸ status prefix in tab/pane title

[iterm2.color]
enabled = true                  # set tab/pane background color on session launch
collision_avoidance = true      # use lease-file-based slot assignment (vs. simple modulo)
```

### Color Palette

The palette lives in the same config file. Named colors participate in the auto-rotation system. Users can add their own — they will be appended to the rotation pool. The `color_schemes` section (below) maps each color to a contrasting icon profile variant.

```toml
[iterm2.palette]
# Built-in preset colors (modify hex values or add/remove entries freely)
red         = "#e74c3c"
orange      = "#e67e22"
yellow      = "#f0b429"
green       = "#2ecc71"
teal        = "#1abc9c"
sky_blue    = "#039be5"
blue        = "#1e88e5"
purple      = "#5e35b1"
pink        = "#d81b60"
cyan        = "#00acc1"
deep_orange = "#ff5722"
lime        = "#7cb342"
# Add your own — will be included in auto-rotation:
# midnight    = "#1a1a2e"
# rose        = "#f43f5e"
```

### Color Schemes

Each palette color maps to a `[claude_profile, gemini_profile]` pair that provides good contrast between the icon color and the tab background. These pairings were derived using color theory (complementary/contrasting hue selection). The scheme ensures icons are never combined with poorly contrasting backgrounds.

```toml
[iterm2.color_schemes]
# palette_name = [claude_profile, gemini_profile]
red         = ["ClaudeCode-White",  "GeminiCLI-White"]
orange      = ["ClaudeCode-Cyan",   "GeminiCLI-White"]
yellow      = ["ClaudeCode-Navy",   "GeminiCLI-Navy"]
green       = ["ClaudeCode-Purple", "GeminiCLI-Navy"]
teal        = ["ClaudeCode-Coral",  "GeminiCLI-Navy"]
sky_blue    = ["ClaudeCode-Gold",   "GeminiCLI-Gold"]
blue        = ["ClaudeCode-Coral",  "GeminiCLI-Gold"]
purple      = ["ClaudeCode-Gold",   "GeminiCLI-Gold"]
pink        = ["ClaudeCode-Teal",   "GeminiCLI-White"]
cyan        = ["ClaudeCode-White",  "GeminiCLI-White"]
deep_orange = ["ClaudeCode-Cyan",   "GeminiCLI-White"]
lime        = ["ClaudeCode-Navy",   "GeminiCLI-Navy"]
# Custom color entries added above need corresponding scheme entries here
```

**Default config:** The TOML file ships as part of ai-cli-utils at `src/ai_cli/data/iterm2-defaults.toml` and is copied to `~/.config/ai-cli-utils/iterm2.toml` on first run if missing. Users edit their local copy; the shipped defaults are never modified.

> **Feedback:**

---

## Icon Color System

### Problem

Currently only 3 Claude icon color variants exist (coral, white, dark navy) and 1 Gemini variant. This limits visual variety — many tabs look similar.

### Design: 8 Claude variants + 3 Gemini variants

Each variant is a separate Dynamic Profile entry with a differently-tinted PNG icon. Runtime icon color mutation is confirmed impossible by the research doc — static profiles with pre-rendered PNGs is the only path.

**Claude icon color variants:**

| # | Profile Name | Icon Color | Best With Background Colors | Hex Example |
|---|-------------|------------|----------------------------|-------------|
| 1 | `ClaudeCode-Coral` | Coral/salmon (brand) | Cool/dark (blue, purple, navy) | `#E87461` |
| 2 | `ClaudeCode-White` | White | Warm/saturated (red, orange, deep orange) | `#FFFFFF` |
| 3 | `ClaudeCode-Navy` | Dark navy | Bright/light (yellow, lime, light green) | `#1A1A2E` |
| 4 | `ClaudeCode-Purple` | Purple | Green, teal, amber backgrounds | `#8B5CF6` |
| 5 | `ClaudeCode-Gold` | Gold/amber | Blue, purple, dark teal backgrounds | `#F59E0B` |
| 6 | `ClaudeCode-Cyan` | Cyan/aqua | Red, orange, pink, warm backgrounds | `#06B6D4` |
| 7 | `ClaudeCode-Teal` | Teal | Red, orange, pink, magenta backgrounds | `#14B8A6` |
| 8 | `ClaudeCode-Green` | Green | Purple, magenta, red, dark blue backgrounds | `#22C55E` |

**Gemini icon color variants:**

| # | Profile Name | Icon Color | Best With Background Colors |
|---|-------------|------------|----------------------------|
| 1 | `GeminiCLI-White` | White | Dark/saturated backgrounds |
| 2 | `GeminiCLI-Navy` | Dark navy | Bright/light backgrounds |
| 3 | `GeminiCLI-Gold` | Gold | Cool/dark backgrounds |

**Mapping from tab color to icon profile:**

The 12-color palette is divided into groups by warmth/brightness, and each group is assigned an icon color that contrasts well:

| Slot | Tab Color | Tab Hex | Claude Profile | Gemini Profile |
|------|-----------|---------|----------------|----------------|
| 0 | Red | `e74c3c` | ClaudeCode-White | GeminiCLI-White |
| 1 | Orange | `e67e22` | ClaudeCode-Cyan | GeminiCLI-White |
| 2 | Yellow | `f0b429` | ClaudeCode-Navy | GeminiCLI-Navy |
| 3 | Green | `2ecc71` | ClaudeCode-Purple | GeminiCLI-Navy |
| 4 | Teal | `1abc9c` | ClaudeCode-Coral | GeminiCLI-Navy |
| 5 | Sky Blue | `039be5` | ClaudeCode-Gold | GeminiCLI-Gold |
| 6 | Blue | `1e88e5` | ClaudeCode-Coral | GeminiCLI-Gold |
| 7 | Purple | `5e35b1` | ClaudeCode-Gold | GeminiCLI-Gold |
| 8 | Pink | `d81b60` | ClaudeCode-Teal | GeminiCLI-White |
| 9 | Cyan | `00acc1` | ClaudeCode-White | GeminiCLI-White |
| 10 | Deep Orange | `ff5722` | ClaudeCode-Cyan | GeminiCLI-White |
| 11 | Lime | `7cb342` | ClaudeCode-Navy | GeminiCLI-Navy |

**Dynamic Profile JSON structure (one example variant):**

```json
{
  "Name": "ClaudeCode-Purple",
  "Guid": "humanware-claude-code-purple",
  "Dynamic Profile Parent Name": "ClaudeCode",
  "Icon": 2,
  "Custom Icon Path": "/Users/sergeiwallace/.config/iterm2/icons/claude-icon-purple.png"
}
```

Each variant inherits everything from the `ClaudeCode` base profile and only overrides the icon path. The base `ClaudeCode` profile must NOT set `"Title Components"` (this key is confirmed to break key mappings and title management).

**Icon generation:** Pre-render tinted PNGs from the Claude SVG source (`~/.config/iterm2/icons/claude-logo.svg`) using a Python script with Pillow. The script takes the base SVG, renders it at 128x128, and applies a color tint. This is a one-time asset generation step, not a runtime operation. The same approach for Gemini using the Gemini logo source.

**PNG storage:** Pre-rendered PNGs are checked into the repo at `assets/iterm2-icons/`. 11 PNGs (8 Claude + 3 Gemini) at ~10–20 KB each is negligible. Users can regenerate at any time if they want custom colors.

**Generation script:** `scripts/generate_iterm2_icons.py`. Reads the palette and color scheme config from `iterm2.toml`, renders each variant, and writes to `assets/iterm2-icons/`. Options:

```
python scripts/generate_iterm2_icons.py            # regenerate all from config
python scripts/generate_iterm2_icons.py --outline  # add white/black outline for contrast
python scripts/generate_iterm2_icons.py --size 256 # output resolution (default 128)
```

**Outline option:** A white or black outline around the icon improves legibility on backgrounds where the icon color is close to the tab background color. The script auto-selects outline color (white for dark icons, black for light) based on computed luminance contrast. This is an explicit flag rather than always-on because some users may prefer clean logomarks without outlines. This is included in the initial implementation of the script (not a follow-up).

**Color theory for pairings:** The `color_schemes` config in `iterm2.toml` encodes the contrasting pairings. Pairing rules:
- Warm tab backgrounds (red, orange, deep orange, pink) → cool icon colors (cyan, teal, white)
- Cool tab backgrounds (blue, purple, cyan, sky blue) → warm icon colors (coral, gold)
- Neutral/natural (green, teal, yellow, lime) → contrasting by hue rotation (purple, navy, coral)

> **Feedback:**

---

## OSC/DCS Sequence Reference

### Sequences Used

| Sequence | Purpose | Where Emitted | Mosh Pass? |
|----------|---------|---------------|------------|
| `\033]1337;SetProfile={name}\007` | Switch iTerm2 profile (icon, settings) | Pre-launch (Python) + in-session (bash via `_it2`) | No |
| `\033]1337;SetColors=tab={hex}\007` | Set tab background color | Pre-launch (Python) + in-session (bash via `_it2`) | No |
| `\033]0;{title}\007` | Set tab/pane title | Pre-launch (Python) + in-session (bash via `_it2`) | Yes (prepends `[mosh] `) |

### DCS Wrapping for tmux

When inside a tmux session, iTerm2-proprietary sequences must be wrapped in DCS passthrough. The `_it2` bash helper does this:

```
Raw:     \033]1337;SetProfile=ClaudeCode-Purple\007
Wrapped: \033Ptmux;\033\033]1337;SetProfile=ClaudeCode-Purple\007\033\\
```

Rules:
- Every `\033` (ESC) inside the payload must be doubled to `\033\033`
- The envelope is `\033Ptmux;` ... `\033\\`
- Requires `set -g allow-passthrough on` in tmux.conf

OSC 0 (`\033]0;...`) also needs DCS wrapping inside tmux to reliably reach iTerm2 (tmux may intercept bare OSC 0 and use it for its own window title).

### What mosh Filters vs. Passes

| Category | Passes Through | Filtered |
|----------|---------------|----------|
| OSC 0 (window/tab title) | Yes, with `[mosh] ` prefix | - |
| OSC 1337 (iTerm2 proprietary) | - | Completely filtered |
| OSC 6 (legacy tab color) | - | Completely filtered |
| DCS passthrough | - | Completely filtered (mosh is not tmux-aware) |

**Consequence:** For mosh sessions, all visual identity (profile, color) must be set BEFORE `os.execvp("mosh", ...)`. Only OSC 0 title updates work from inside the mosh session, and they arrive with the `[mosh]` prefix.

> **Feedback:**

---

## Gemini Chats Directory

### Problem

Gemini CLI determines its chats storage directory from the working directory at launch time. The path is derived as `~/.gemini/tmp/{sanitized-cwd}/chats/`. When `ai g 1 -p artelier` is run from `~/projects/sergei/`, Gemini looks for sessions in `~/.gemini/tmp/{sergei-path}/chats/` instead of `~/.gemini/tmp/{artelier-worktree-path}/chats/`. This causes Bugs 4, 5, and 6.

Gemini CLI has no `--chats-dir` flag (confirmed by inspecting `gemini --help` output). The working directory at launch is the sole determinant of the chats path.

### Design

**For local sessions:** When `-p PROJECT` is provided, ai-cli must `os.chdir()` to the target project's worktree directory before creating the tmux session. The current code does this for `is_remote` (lines 2317-2324) but NOT for local sessions. The fix is to add the same `os.chdir()` logic for local `-p` sessions.

Specifically, when `args.project` is set and the session is local (not remote):
1. Resolve the project name via aliases
2. Find the project directory via `_find_project_dir()`
3. Create/find the worktree if worktree mode is enabled
4. `os.chdir()` to the worktree (or project root if no worktree)

This ensures the `cd {worktree_dir}` command in the bash script template and the gemini launch both operate from the correct directory, so Gemini finds the right chats directory.

**For remote sessions:** The `is_remote` branch already does `os.chdir()` to the project directory on the remote machine (lines 2317-2324). However, the worktree for the Gemini session may not exist yet at that point. The worktree creation happens later (line 2351-2355), and the `cd_cmd` in the script template uses `worktree_dir`. If the worktree creation fails or the path is wrong, Gemini will be in the wrong directory. The fix: ensure worktree creation happens BEFORE the chdir, or pass the worktree path correctly into the script.

**Session UUID tracking:** The `get_session_map` / `update-session-map` system tracks Gemini session UUIDs by `ai_name` (e.g., `art-1`). When resuming, the UUID is passed via `-r {uuid}`. But the UUID is associated with a specific chats directory path inside Gemini's internal state. If the UUID was created from one directory and the resume happens from a different directory, Gemini cannot find the session. The directory must match.

**Clarification on recoverability:** Gemini sessions launched from the wrong directory are NOT permanently broken. Running `ai g 1 -p artelier` from `~/projects/artelier/` (the correct root) will successfully resume the session — Gemini finds the right chats directory because the working directory now matches. The bug is a UX issue: `ai g N -p PROJECT` should work from ANY directory (not just the project root), which is what the `os.chdir()` fix provides. No session-map cleanup is required.

> **Feedback:**

---

## What Changes vs. Current Implementation

### Kept as-is

| Component | Notes |
|-----------|-------|
| Per-pane title ownership | Each pane independently sets its own title via OSC 0 |
| Status symbols | `▶` `⏸` `✓` `✗` `↻` — unchanged |
| Two-layer emission (pre-launch + in-session) | Pre-launch Python + in-session bash pattern retained |
| `_it2` DCS wrapper helper | Unchanged |
| `_ai_iterm2_precmd` in zshrc for shell panes | Unchanged (with one race-condition fix, see below) |
| iTerm2 env var propagation into tmux | `_iterm_env_flags` mechanism retained |
| ShellUtility, Caffeinate, ChromeDebug, SSHForward profiles | Unchanged |

### Fixed (bug repairs, no architectural change)

| Bug | Fix |
|-----|-----|
| Bug 1 — Color collision | Replace `(num-1) % 12` with lease-file-based slot assignment |
| Bug 2 — Remote icon/title wrong | Fix `_ai_iterm2_precmd` race condition; remove `*` from tab title in pre-launch emission |
| Bug 3 — Gemini title shows "Default" | Remove `"Title Components": 1` from ClaudeCode profile; ensure OSC 0 fires after SetProfile |
| Bugs 4/5/6 — Gemini wrong directory | Add `os.chdir()` for local `-p` sessions (matching existing remote behavior) |

### Redesigned

| Component | Old | New | Rationale |
|-----------|-----|-----|-----------|
| Color assignment | `(num-1) % 12` modulo | Lease-file with collision-free slot assignment | Eliminates color collision across projects |
| Icon color profiles | 3 Claude variants (coral/white/dark) + 1 Gemini | 8 Claude variants + 3 Gemini variants | Richer visual variety per user requirement |
| Profile map | 12-slot array mapping to 3 profiles | 12-slot array mapping to 8+ profiles | More distinct icon/background pairings |
| Tab title format | `* ▶ {name}` (type sym always included) | `▶ {name}` in tab; `* ▶ {name}` in pane header only | Type symbol is redundant with profile icon in tab bar |
| Gemini tab color | Static blue from profile JSON | Rolling color from shared palette | Gemini sessions get distinct colors like CC |
| Multi-pane combined titles | Abbreviation/aggregation logic | Dropped entirely | Each pane owns its title; tab shows focused pane |
| Window title generation | Heuristic + async Claude Haiku | Dropped from scope | Complexity not justified; tab titles are sufficient |
| Session names file | `/tmp/iterm2-cc-names-{tab_key}` shared state | Eliminated | Per-pane ownership means no shared state needed |

### Removed

| Component | Reason |
|-----------|--------|
| Multi-pane abbreviation logic (`c-r-sw-{▶1|⏸2}`) | Requirement 1: no combined titles |
| Window title via Claude Haiku subprocess | Was causing freezes; dropped from scope |
| Window registry files (`/tmp/iterm2-win-{win_key}`) | Only needed for window titles |
| Badge text updates | Not used for status display in this design |

> **Feedback:**

---

## Open Questions and Constraints

### Resolved

1. **OSC 0 type symbol in tab title.** ✅ **Decision:** Include type symbols (`*`, `✦`) by default — enabled via `show_type_symbol = true` in `iterm2.toml`. Users can set `show_type_symbol = false` to disable. See [Configuration](#configuration).

2. **`_ai_iterm2_precmd` race condition with remote launches.** Defer to debugging post-implementation. In principle, `os.execvp` replaces the process immediately, so precmd should not fire after the pre-launch emit. If Bug 2 persists after the other fixes, the root cause is elsewhere and will be diagnosed with real sessions. Unit and integration tests will be written during implementation to catch regressions.

3. **Icon PNG generation pipeline.** ✅ **Decision:** PNGs checked into repo at `assets/iterm2-icons/`. Generation script at `scripts/generate_iterm2_icons.py`, reads from `iterm2.toml`, supports `--outline` and `--size` flags. See [Icon Color System](#icon-color-system).

4. **Gemini `--resume` with wrong chats directory.** ✅ **Correction:** Sessions are NOT permanently broken — running `ai g N -p PROJECT` from the correct project root successfully resumes the session. The fix (`os.chdir()` for local `-p` sessions) is a UX improvement so the command works from ANY directory, not a recovery mechanism for broken sessions. No session-map cleanup needed.

### Hard Constraints

- Mosh filters all `\033]1337;` sequences — this is architectural, not configurable
- Mosh prepends `[mosh] ` to all OSC 0 titles — this is hardcoded in mosh source
- Runtime icon color mutation is impossible in iTerm2 — static profiles with pre-rendered PNGs only
- Gemini CLI has no `--chats-dir` flag — working directory at launch is the sole determinant
- `"Title Components"` key in Dynamic Profiles breaks key mappings — must not be set

---

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| 2026-04-02 | Round 1 | OQ-1: type symbols enabled by default, TOML config file for all feature flags + color palette. OQ-2: tests during implementation, debug race condition post-impl. OQ-3: PNGs in repo, generation script with `--outline` flag, color scheme config with color theory pairings. OQ-4: corrected — sessions not permanently broken; `os.chdir` fix is UX improvement only. |
