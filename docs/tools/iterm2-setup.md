---
title: "iTerm2 Setup & Shortcuts"
category: tools
tags: [iterm2, terminal, shortcuts, configuration, fleet-management]
status: active
source: sergei
---

# iTerm2 Setup & Shortcuts

Quick reference for iTerm2 configuration and keyboard shortcuts for managing parallel Claude Code sessions.

## Essential Configuration

### Option Key as Word-Skip Modifier

By default, Option+arrow sends raw escape sequences instead of skipping words.

**Fix:** Preferences → Profiles → Keys → General → **Left Option Key** → set to **Esc+** (repeat for Right Option Key)

### Shift+Enter as Newline in Claude Code

CC needs the CSI u escape sequence for Shift+Enter.

**Fix:** Preferences → Profiles → Keys → Key Mappings → **+** → Shortcut: Shift+Enter → Action: Send Escape Sequence → Value: `[13;2u`

### Extended Keys for tmux

Already configured in `.tmux.conf` on Hetzner:
```
set -s extended-keys on
set -as terminal-features 'xterm*:extkeys'
```

### tmux Passthrough for iTerm2 Escape Sequences

Required for tab colors, badges, profile switching, and icons to work through SSH+tmux.

**Configured in Hetzner `.tmux.conf`:**
```
set -g allow-passthrough on
```

Without this, none of the fleet management features (rolling tab colors, badges, profile switching) reach iTerm2 through the tmux session.

## Keyboard Shortcuts

### Text Navigation (in CC prompt / shell)

| Action | Keys |
|--------|------|
| Skip word left | `Option+←` |
| Skip word right | `Option+→` |
| Jump to start of line | `Ctrl+A` |
| Jump to end of line | `Ctrl+E` |
| Delete word back | `Option+Backspace` |
| Delete word forward | `Option+D` |
| Delete to start of line | `Ctrl+U` |
| Delete to end of line | `Ctrl+K` |
| Delete single char back | `Backspace` |
| Delete single char forward | `Ctrl+D` |

### Copy/Paste

| Action | Keys |
|--------|------|
| Select text in TUI (CC session) | **Hold Option** + mouse drag (bypasses TUI mouse capture) |
| Copy | `Cmd+C` (after selecting) |
| Paste | `Cmd+V` |
| Paste plain text | `Cmd+Shift+V` |
| Clipboard history | `Cmd+Shift+H` |
| tmux paste (fallback) | `Ctrl+Space ]` |

**Note:** In Claude Code sessions, you must hold **Option** while selecting text with the mouse — otherwise mouse events go to CC's ink TUI renderer instead of iTerm2's text selection.

### Splits & Tabs

| Action | Keys |
|--------|------|
| New tab | `Cmd+T` |
| Split vertical (side by side) | `Cmd+D` |
| Split horizontal (top/bottom) | `Cmd+Shift+D` |
| Drag tab to create split | Drag tab into terminal area (visual overlay shows split position) |
| Navigate panes | `Cmd+Option+arrow` |
| Navigate tabs | `Cmd+number` (1-9) |
| Next tab | `Cmd+Shift+]` |
| Previous tab | `Cmd+Shift+[` |
| Close pane/tab | `Cmd+W` |
| Maximize pane (toggle) | `Cmd+Shift+Enter` |

### Fleet Navigation

| Action | Keys |
|--------|------|
| Exposé (all tabs as thumbnails) | `Cmd+Option+E` |
| Jump to tab by number | `Cmd+1` through `Cmd+9` |
| Jump to window by number | `Cmd+Option+number` |
| Navigate panes in order | `Cmd+]` / `Cmd+[` |

### Session Configuration

| Action | How |
|--------|-----|
| Rename tab | Double-click tab title, or Edit → Session Name |
| Set tab color | Right-click tab → Tab Color, or "Custom..." for macOS color picker |
| Set pane title | Edit → Set Session Name (per split pane) |

## Fleet Management (ai-cli Integration)

When `ai c N` launches a CC session, ai-cli automatically configures iTerm2 via escape sequences (requires `TERM_PROGRAM=iTerm.app`):

1. **Profile switch** — activates `ClaudeCode` Dynamic Profile (sets icon, base color scheme, badge template)
2. **Rolling tab color** — assigns a distinct color from a 10-color palette based on session number:

| # | Color | Hex | Session |
|---|-------|-----|---------|
| 1 | Purple (Anthropic) | `#6440dc` | sw-1 |
| 2 | Blue | `#4a90d9` | sw-2 |
| 3 | Green | `#2ecc71` | sw-3 |
| 4 | Orange | `#e67e22` | sw-4 |
| 5 | Red | `#e74c3c` | sw-5 |
| 6 | Teal | `#1abc9c` | sw-6 |
| 7 | Violet | `#9b59b6` | sw-7 |
| 8 | Amber | `#f39c12` | sw-8 |
| 9 | Sky blue | `#3498db` | sw-9 |
| 10 | Pink | `#e91e63` | sw-10 |

3. **Badge** — shows session type and number (e.g., "cc sw-3") as faint overlay in top-right
4. **User variables** — sets `sessionType`, `sessionNum`, `tmuxSession` for badge interpolation
5. **Tab title** — sets to "cc sw-N"

Colors and badges re-emit on each session restart (reconnect restores visual identity).

## Dynamic Profiles (Mac-local)

**Location:** `~/Library/Application Support/iTerm2/DynamicProfiles/humanware-profiles.json`

Profiles defined:

| Profile | Icon | Base Tab Color | Badge | Use Case |
|---------|------|---------------|-------|----------|
| ClaudeCode | Claude logo | Purple | `\(user.sessionType) sw-\(user.sessionNum)` | CC agent sessions |
| ShellUtility | Terminal icon | Grey | `\(user.sessionType)` | Shell, git, monitoring |
| Caffeinate | Coffee icon | Amber | — | `caffeinate` keep-alive |
| ChromeDebug | Chrome icon | Blue | — | Chrome CDP debug |
| SSHForward | SSH icon | Green | — | Port forwarding |

**Icons location:** `~/.config/iterm2/icons/` (64x64 PNG with transparent bg)

Profiles are hot-reloaded by iTerm2 — edit the JSON and changes apply immediately.

## Escape Sequences Reference

For manual use or scripting (requires tmux passthrough):

```bash
# Set profile
printf '\e]1337;SetProfile=ClaudeCode\a'

# Set tab color (hex RGB)
printf '\e]1337;SetColors=tab=6440dc\a'

# Reset tab color to default
printf '\e]1337;SetColors=tab=default\a'

# Set badge (base64-encoded text)
printf '\e]1337;SetBadgeFormat=%s\a' "$(echo -n 'cc sw-3' | base64)"

# Set user variable (base64-encoded value)
printf '\e]1337;SetUserVar=%s=%s\a' "sessionType" "$(echo -n 'cc' | base64)"

# Set tab title
printf '\e]0;CC sw-3\a'
```

## Smart Tab & Window Titles (ai-cli)

Tab and window titles are managed dynamically by ai-cli via OSC escape sequences emitted on each shell prompt (`_ai_iterm2_precmd` in `~/.zshrc`).

### Tab Title Format

Tab title = tmux session name, prefixed by pane-type symbols when in a multi-pane layout:

| Symbol | Meaning |
|--------|---------|
| `*` | Claude Code pane |
| `✦` | Gemini CLI pane |
| `$` | Plain shell pane |

Status suffix appended on task events:

| Symbol | Meaning |
|--------|---------|
| `▶` | Running |
| `⏸` | Waiting for input |
| `✓` | Done |
| `✗` | Error |
| `↻` | Resuming |

Multi-pane example: `* c-sw-5` (single CC pane) or `* ✦ $ c-sw-5` (CC + Gemini + shell).

Window title uses a heuristic derived from the set of active tmux session names (common prefix abbreviated).

### Known Constraints

- **Do NOT add `"Title Components"` key to Dynamic Profiles** — breaks all key mappings in that profile. This key conflicts with humanware's dynamic title management. Test confirmed 2026-04-01.
- **Do NOT add `_ai_zshrc_autoreload` to `precmd_functions`** — causes infinite sourcing loop inside CC sessions (source → mtime changes → source again). Attempted and reverted 2026-04-01.
- Window title no longer uses `claude -p` subprocess — was spawning a headless CC process from within an active CC session, causing freezes. Fixed in ai-cli-utils commit `add120f` (heuristic only, written directly to file).

## ntfy → iTerm2 Bridge

**File:** `~/Library/Application Support/iTerm2/Scripts/ntfy-iterm2-bridge.py`
**Logs:** `~/.config/iterm2/logs/ntfy-bridge.log` / `ntfy-bridge.err`
**Channel:** `https://ntfy.sergeiwallace.com/humanware-alerts/sse`

Subscribes to the ntfy SSE stream, matches session events to iTerm2 tabs, updates badges/titles, and fires macOS notifications for `done`/`error`/`waiting` states.

### How to Restart

Must be restarted via **iTerm2 menu → Scripts → ntfy-iterm2-bridge**. Cannot be run with `python3` directly — the `iterm2` module is only available inside iTerm2's embedded Python runtime (`ModuleNotFoundError` otherwise).

### Notification Content

macOS notification body uses the ntfy event `message` field if present, falls back to `"Session {status}: {session_name}"`. Parses three event patterns:

1. **Structured JSON** in message body: `{"session": "sw-3", "status": "done", "message": "..."}`
2. **Regex in message text**: `sw-3 done`
3. **Session in title**: `sw-3` in ntfy title + status keyword in message/tags

### Scripts Directory Note

iTerm2 looks for scripts in `~/Library/Application Support/iTerm2/Scripts/`. The `~/.config/iterm2/scripts/` path is a secondary backup only — edits must be made to (or copied to) the `~/Library/` path to take effect.

## Window Arrangements

Save a fleet monitoring layout via **Window → Save Window Arrangement**. Restore via **Window → Restore Window Arrangement** or `Cmd+Shift+R`.

Recommended 4-pane layout:
```
┌──────────────────┬──────────┐
│                  │ sw-2     │
│     sw-1         │          │
│   (main CC)      ├──────────┤
│                  │ sw-3     │
│                  │          │
└──────────────────┴──────────┘
```
