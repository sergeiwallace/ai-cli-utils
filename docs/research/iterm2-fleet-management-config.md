---
title: "iTerm2 Power User Configuration for AI Agent Fleet Management"
category: research
tags: [research, iterm2, terminal, fleet-management, ssh, tmux, developer-tools]
status: complete
source: "claude-opus-2026-03-29"
---

# iTerm2 Power User Configuration for AI Agent Fleet Management

**Status:** complete

**Created:** 2026-03-29

**Task:** SW-730
**Prompt:** R-36 in `docs/research/prompts/research-prompt-registry.md`

## Executive Summary

iTerm2 provides a comprehensive feature set for managing 5-10 parallel AI coding agent SSH sessions, including per-profile custom icons, programmatic tab coloring via escape sequences, dynamic badge updates, profile switching via escape codes, and full automation through both AppleScript and Python APIs. The recommended architecture uses Dynamic Profiles (JSON) for declarative session type definitions, escape sequences emitted by `ai-cli` for runtime customization (tab color, badge, profile switch), and a lightweight Python API script or AppleScript for layout creation. Automatic Profile Switching based on tmux session name is NOT natively supported -- the workaround is escape-sequence-based profile switching triggered from the `ai c N` launch flow.

## 1. Tab and Pane Icons

### What iTerm2 Supports Natively

iTerm2 has three icon modes per profile, configurable in Settings > Profiles > General > Basics: [VERIFIABLE FACT]

| # | Mode | Description |
|---|------|-------------|
| 1 | **None** | No icon displayed |
| 2 | **Built-in** (Automatic) | Icon based on the foreground application (e.g., shows Python icon when python is running). This is the default. |
| 3 | **Custom** | User-specified image file |

Icons appear in the **tab bar** and the **window title bar**. [VERIFIABLE FACT: [iTerm2 Profiles General docs](https://iterm2.com/documentation-preferences-profiles-general.html)]

### Custom Icons

**Custom icon support is confirmed.** You set a custom image path per profile. The Python API exposes:

```python
await profile.async_set_icon_mode(iterm2.profile.IconMode.CUSTOM)  # value = 2
await profile.async_set_custom_icon_path("/path/to/icon.png")
```

The corresponding Dynamic Profile JSON keys are: [VERIFIABLE FACT: [iTerm2 source code, profile.py](https://github.com/gnachman/iTerm2/blob/master/api/library/python/iterm2/iterm2/profile.py)]

```json
{
  "Icon": 2,
  "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/claude-logo.png"
}
```

**Icon format/size:** The documentation does not specify accepted formats or recommended sizes. [NO SOURCE FOUND for explicit format docs.] [INDUSTRY HEURISTIC] Standard practice is PNG, 32x32 or 64x64 pixels, with transparency. The icon is displayed at tab-bar scale (~16-20px rendered), so larger images are downscaled. Test with PNG first.

### Icon Sources for Terminal Use

| # | Source | Type | Notes |
|---|--------|------|-------|
| 1 | [Nerd Fonts](https://www.nerdfonts.com/) | Patched fonts with 3600+ icons | Glyph-based, rendered in prompt/status bars, not as tab icons |
| 2 | SF Symbols (macOS) | Apple's system icon set | iTerm2 supports SF Symbols for custom buttons (`Button=type=custom;icon=[sf-symbol]`). Tab icons use image files, not SF Symbols directly. |
| 3 | [Simple Icons](https://simpleicons.org/) | SVG brand icons | Download Anthropic/Claude logo as SVG, convert to PNG |
| 4 | [Codicons](https://github.com/microsoft/vscode-codicons) | VS Code icons | SVG/font format, convert to PNG for tab use |
| 5 | Custom creation | Any image editor | Create 64x64 PNG with transparent background |

**Recommended approach:** Download or create small PNG icons for each session type (Claude logo, coffee cup, SSH icon, Chrome icon). Store in `~/.config/iterm2/icons/`. Reference from Dynamic Profiles.

### Can Icons Be Set on Split Pane Title Bars?

[NO SOURCE FOUND] The documentation only mentions icons in tabs and window title bars. Split pane title bars show the session name/title but there is no documented way to display custom icons in pane title bars specifically. The icon set on the profile appears in the tab for that session; in a split layout, the active pane's icon is shown in the tab.

## 2. Per-Session Profiles with Auto-Detection

### Automatic Profile Switching (APS)

APS rules support these matchers: [VERIFIABLE FACT: [APS docs](https://iterm2.com/documentation-automatic-profile-switching.html)]

| # | Matcher | Example | Notes |
|---|---------|---------|-------|
| 1 | Username | `root@` | Requires Shell Integration |
| 2 | Hostname | `*.example.com` | Wildcards supported, requires Shell Integration |
| 3 | Path | `/home/sergei/projects/*` | Wildcards supported, requires Shell Integration |
| 4 | Job name | `&python` (prefix with `&`) | Matches foreground process name (v3.6+: matches full command line) |

**Critical limitation: APS does NOT support matching on tmux session name.** The rule system is restricted to user, host, path, and job name. The `tmuxClientName` variable exists in iTerm2's variable system but is only available to the scripting/Python API, not to APS rules. [VERIFIABLE FACT: confirmed by [APS docs](https://iterm2.com/documentation-automatic-profile-switching.html) and [GitLab issue #4543](https://gitlab.com/gnachman/iterm2/-/issues/4543)]

### The Workaround: Escape-Sequence Profile Switching

Since all sessions SSH to the same host with the same user, APS hostname/username rules cannot differentiate sessions. Instead, use the **SetProfile escape sequence** from inside the session:

```bash
# Switch to a named profile
printf '\e]1337;SetProfile=ClaudeCode\a'

# Or using the older OSC 50 form (also works):
echo -e "\033]50;SetProfile=ClaudeCode\a"
```

[VERIFIABLE FACT: [Proprietary Escape Codes docs](https://iterm2.com/documentation-escape-codes.html), [community examples](https://til-engineering.nulogy.com/Changing-Your-iterm2-Profile-Programmatically/)]

**This is the recommended approach for ai-cli.** When `ai c 3` launches, it emits a SetProfile escape sequence before SSH + tmux attach. The profile sets the icon, base color scheme, badge template, and other visual identity.

### Triggers as Supplementary Detection

Triggers fire on regex matches in terminal output. Relevant trigger actions: [VERIFIABLE FACT: [Triggers docs](https://iterm2.com/documentation-triggers.html)]

| # | Action | Use Case |
|---|--------|----------|
| 1 | **Set Title** | Auto-set session title when tmux session name appears in output |
| 2 | **Set User Variable** | Store tmux session name as `user.tmuxSession` for badge interpolation |
| 3 | **Run Command** | Execute profile-switching script when pattern detected |
| 4 | **Highlight Line** | Color-code error output from agent sessions |

**There is NO "Set Profile" trigger action.** Profile switching via triggers requires the indirect "Run Command" action or "Invoke Script Function" calling a Python API script. [VERIFIABLE FACT: [Triggers docs](https://iterm2.com/documentation-triggers.html)]

### Dynamic Profiles (JSON)

Dynamic Profiles let you define profiles as JSON files that iTerm2 hot-reloads. Files go in `~/Library/Application Support/iTerm2/DynamicProfiles/`. [VERIFIABLE FACT: [Dynamic Profiles docs](https://iterm2.com/documentation-dynamic-profiles.html)]

```json
{
  "Profiles": [
    {
      "Name": "ClaudeCode",
      "Guid": "cc-claude-code-base-001",
      "Dynamic Profile Parent Name": "Default",
      "Badge Text": "\\(user.sessionType) \\(user.sessionNum)",
      "Use Tab Color": true,
      "Tab Color": {
        "Red Component": 0.4,
        "Green Component": 0.3,
        "Blue Component": 0.9,
        "Color Space": "sRGB"
      },
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/claude-logo.png",
      "Tags": ["claude", "ai-agent"]
    },
    {
      "Name": "ShellUtility",
      "Guid": "cc-shell-utility-001",
      "Dynamic Profile Parent Name": "Default",
      "Badge Text": "\\(user.sessionType)",
      "Use Tab Color": true,
      "Tab Color": {
        "Red Component": 0.6,
        "Green Component": 0.6,
        "Blue Component": 0.6,
        "Color Space": "sRGB"
      },
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/terminal.png",
      "Tags": ["shell", "utility"]
    }
  ]
}
```

**Key points:**
- Files are monitored for changes and auto-reloaded. [VERIFIABLE FACT]
- Profiles inherit from parent via `"Dynamic Profile Parent Name"`. [VERIFIABLE FACT]
- Export an existing profile via Settings > Profiles > Other Actions > Save Profile as JSON to discover all available key names. [VERIFIABLE FACT]
- JSON keys for visual properties confirmed from [iTerm2 source](https://github.com/gnachman/iTerm2/blob/master/api/library/python/iterm2/iterm2/profile.py): `"Icon"`, `"Custom Icon Path"`, `"Use Tab Color"`, `"Tab Color"`, `"Badge Text"`.

## 3. Rolling Colors for CC Sessions

### Escape Sequences for Tab Color

Two methods for setting tab color programmatically: [VERIFIABLE FACT: [Escape Codes docs](https://iterm2.com/documentation-escape-codes.html)]

**Method 1: OSC 6 (legacy, simpler)**
```bash
# Set tab color to RGB values (0-255 per channel)
printf '\e]6;1;bg;red;brightness;100\a'
printf '\e]6;1;bg;green;brightness;60\a'
printf '\e]6;1;bg;blue;brightness;220\a'

# Reset to default
printf '\e]6;1;bg;*;default\a'
```

**Method 2: OSC 1337 SetColors (newer)**
```bash
# Set tab color using hex
printf '\e]1337;SetColors=tab=6440dc\a'

# Reset tab color
printf '\e]1337;SetColors=tab=default\a'
```

### Color Cycling Implementation for ai-cli

Define a preset color list and assign by session number modulo list length:

```bash
# Color palette: 10 distinct colors for CC sessions
# Format: hex RGB values
CC_COLORS=(
  "6440dc"  # purple (Anthropic brand)
  "4a90d9"  # blue
  "2ecc71"  # green
  "e67e22"  # orange
  "e74c3c"  # red
  "1abc9c"  # teal
  "9b59b6"  # violet
  "f39c12"  # amber
  "3498db"  # sky blue
  "e91e63"  # pink
)

# In ai-cli's session launch function:
set_tab_color() {
  local session_num=$1
  local color_index=$(( (session_num - 1) % ${#CC_COLORS[@]} ))
  local hex_color="${CC_COLORS[$color_index]}"
  printf '\e]1337;SetColors=tab=%s\a' "$hex_color"
}
```

### Persistence After Reconnect

Tab color set via escape sequences is **ephemeral** -- it does not persist if the terminal tab is closed or iTerm2 restarts. [SYNTHESIZED INFERENCE: escape sequences modify the running session state, not the stored profile.] To persist across reconnects:

1. **Profile-level tab color** (via Dynamic Profile JSON `"Tab Color"` key) persists with the profile.
2. **Escape-sequence override** (for per-session cycling) must be re-emitted on reconnect. The `ai c N` command should emit the color escape sequence as part of its launch/reattach flow.

**Recommended approach:** Set a base color in the ClaudeCode Dynamic Profile, then override with the session-specific rolling color via escape sequence on each `ai c N` invocation.

### Customizing the Tab Color Picker Presets

The right-click tab color picker in iTerm2 shows a fixed set of preset colors. [NO SOURCE FOUND for how to customize these specific presets.] The picker appears to use a hardcoded set of colors in iTerm2's UI. There is no documented mechanism to add custom colors to or replace colors in this specific picker menu.

**Workaround approaches:**
- The picker also includes a "Custom..." option that opens the macOS system color picker, which supports saving custom swatches. [INDUSTRY HEURISTIC]
- For your workflow, the tab color picker is mostly irrelevant -- `ai-cli` sets colors programmatically via escape sequences, bypassing the picker entirely.

## 4. Badges and Dynamic Status

### Setting Badges

Badges appear as large, faint text in the top-right corner of a terminal session. [VERIFIABLE FACT: [Badges docs](https://iterm2.com/documentation-badges.html)]

**Via escape sequence:**
```bash
# Set badge (value must be base64-encoded)
printf '\e]1337;SetBadgeFormat=%s\a' \
  "$(echo -n 'CC sw-\(user.sessionNum)' | base64)"
```

**Via profile (Dynamic Profile JSON):**
```json
{
  "Badge Text": "\\(user.sessionType) sw-\\(user.sessionNum)"
}
```

### User-Defined Variables for Badges

Set custom variables via escape sequence, then reference them in badge text: [VERIFIABLE FACT: [Escape Codes docs](https://iterm2.com/documentation-escape-codes.html)]

```bash
# Set user variable (value must be base64-encoded)
printf '\e]1337;SetUserVar=%s=%s\a' \
  "sessionType" "$(echo -n 'CC' | base64)"
printf '\e]1337;SetUserVar=%s=%s\a' \
  "sessionNum" "$(echo -n '3' | base64)"
printf '\e]1337;SetUserVar=%s=%s\a' \
  "tmuxSession" "$(echo -n 'c-r-sw-3' | base64)"
```

Badge text can then interpolate: `\(user.sessionType) sw-\(user.sessionNum)` [VERIFIABLE FACT]

### Badge Formatting Options

All configurable via Python API and Dynamic Profile JSON: [VERIFIABLE FACT: [Python API profile.py](https://iterm2.com/python-api/profile.html)]

| # | Property | Python API Method | JSON Key | Notes |
|---|----------|-------------------|----------|-------|
| 1 | Text content | `async_set_badge_text()` | `"Badge Text"` | Supports variable interpolation |
| 2 | Color | `async_set_badge_color()` | `"Badge Color"` | RGBA color dict |
| 3 | Font | `async_set_badge_font()` | `"Badge Font"` | Font name string |
| 4 | Max width | `async_set_badge_max_width()` | [key from export] | Percentage of session width |
| 5 | Max height | `async_set_badge_max_height()` | [key from export] | Percentage of session height |
| 6 | Top margin | `async_set_badge_top_margin()` | [key from export] | Pixels from top |
| 7 | Right margin | `async_set_badge_right_margin()` | [key from export] | Pixels from right |

**Position:** Fixed at top-right. No option to move to other corners. [VERIFIABLE FACT: "in the top right of a terminal session"]

**Opacity:** Controlled via the alpha component of Badge Color. [SYNTHESIZED INFERENCE from RGBA color model support.]

### Available Built-in Variables for Badges

Key variables from iTerm2's variable system: [VERIFIABLE FACT: [Variables docs](https://iterm2.com/documentation-variables.html)]

| # | Variable | Description |
|---|----------|-------------|
| 1 | `session.name` | Session title as displayed in tab |
| 2 | `session.hostname` | Current hostname (requires Shell Integration) |
| 3 | `session.username` | Current username (requires Shell Integration) |
| 4 | `session.commandLine` | Current foreground command with args |
| 5 | `session.columns` / `session.rows` | Terminal dimensions |
| 6 | `user.*` | User-defined variables (set via escape sequence) |
| 7 | `tmuxClientName` | tmux session name (only in tmux -CC integration mode) |
| 8 | `tmuxWindowTitle` | tmux window title |

## 5. Fleet Overview and Quick Navigation

### Keyboard Shortcuts Reference

[VERIFIABLE FACT: [General Usage docs](https://iterm2.com/documentation-general-usage.html), [Highlights docs](https://iterm2.com/3.3/documentation-highlights.html)]

| # | Shortcut | Action |
|---|----------|--------|
| 1 | `Cmd+Option+E` | **Expose all tabs** -- shows thumbnails of all tabs for visual search |
| 2 | `Cmd+1` through `Cmd+9` | Jump to tab by position number |
| 3 | `Cmd+Option+Number` | Jump to window by number |
| 4 | `Cmd+Left/Right` or `Cmd+{/}` | Navigate between tabs |
| 5 | `Cmd+Option+Arrow` | Navigate between split panes |
| 6 | `Cmd+]` / `Cmd+[` | Navigate split panes in order of use |
| 7 | `Cmd+Shift+Enter` | Maximize/restore current pane (hide others in tab) |
| 8 | `Cmd+D` | Split vertically |
| 9 | `Cmd+Shift+D` | Split horizontally |
| 10 | `Cmd+T` | New tab |
| 11 | `Cmd+W` | Close tab/pane |

### Expose Mode

`Cmd+Option+E` shows all tabs simultaneously as thumbnails with live previews. You can type to search/filter across all tabs. This is the primary fleet overview tool. [VERIFIABLE FACT]

### tmux Dashboard (tmux -CC Integration)

The tmux Dashboard is available via Shell > tmux > Dashboard when using tmux integration mode (`tmux -CC`). [VERIFIABLE FACT: [tmux Integration docs](https://iterm2.com/documentation-tmux-integration.html)]

**Key features of tmux -CC mode:**
- tmux panes become native iTerm2 panes, tmux windows become iTerm2 tabs
- Native macOS scrolling, copy/paste, Cmd+F search
- Session persistence across disconnects (tmux keeps running on server)
- `Cmd+T` creates a new tmux window on the server
- `Cmd+1/2/3` switches between tmux windows as tabs

**Critical limitation for your use case:** tmux -CC maps one tmux session to one iTerm2 window. You would need to attach to each CC tmux session (`c-r-sw-1`, `c-r-sw-2`, etc.) separately with `tmux -CC`, which creates a separate window per session. This is useful for individual session management but does NOT give you a unified fleet view of all sessions in one window with splits. [SYNTHESIZED INFERENCE from tmux -CC architecture.]

**Also:** "A tab with a tmux window may not contain non-tmux split panes." This means you cannot mix tmux -CC tabs with regular split panes. [VERIFIABLE FACT: [tmux Integration docs](https://iterm2.com/documentation-tmux-integration.html)]

**Recommendation:** For fleet management of independent tmux sessions, standard SSH + `tmux attach` (not -CC mode) gives more flexibility. Use iTerm2 tabs/splits for layout, escape sequences for identity. Reserve tmux -CC for scenarios where you want native macOS UX on a single persistent session.

### Fuzzy Search Across Sessions

No built-in fuzzy session search. Expose mode (`Cmd+Option+E`) provides visual search with text filtering. For programmatic session search, the Python API can enumerate all sessions:

```python
app = await iterm2.async_get_app(connection)
for window in app.terminal_windows:
    for tab in window.tabs:
        for session in tab.sessions:
            name = await session.async_get_variable("name")
            # match by name pattern
```

[VERIFIABLE FACT for Python API methods; NO SOURCE FOUND for a built-in fuzzy search feature]

## 6. Automation with ai-cli Integration

### Architecture Decision: AppleScript vs Python API

| # | Criterion | AppleScript | Python API |
|---|-----------|-------------|------------|
| 1 | Complexity | Simple, well-documented | More powerful, async, steeper setup |
| 2 | Tab creation | `create tab with profile "X"` | `await window.async_create_tab(profile="X")` |
| 3 | Profile switching | Not directly supported (use escape seq) | `await session.async_set_profile(profile)` |
| 4 | Tab color | Set via session color properties | `await profile.async_set_tab_color(color)` |
| 5 | Badge | Not directly supported (use escape seq) | `await profile.async_set_badge_text(text)` |
| 6 | Icon | Not directly supported | `await profile.async_set_custom_icon_path(path)` |
| 7 | Send commands | `write text "command"` | `await session.async_send_text("command\n")` |
| 8 | External invocation | `osascript -e '...'` from any shell | `python3 script.py` (requires iterm2 pip package) |
| 9 | Runs from remote? | No -- must run on Mac | No -- must run on Mac |
| 10 | Dependencies | None (macOS built-in) | `pip3 install iterm2` (+ `pyobjc` for launch) |

**Recommendation:** Use **AppleScript** for the simple case (create tab, send SSH command, set name) because it has zero dependencies and can be invoked from any shell via `osascript`. Use **escape sequences** emitted by `ai-cli` on the remote server for runtime customization (tab color, badge, profile switch, user variables). The Python API is overkill unless you need complex conditional logic or session monitoring.

### What ai-cli Should Do on `ai c 3`

The launch flow spans two environments: the local Mac (iTerm2 automation) and the remote server (tmux + escape sequences).

**Option A: Escape-sequence-only (simpler, recommended)**

All customization happens via escape sequences after SSH connects. The Mac side just needs a tab with SSH. This can be a single `osascript` call or manual tab creation.

```bash
# ai-cli on the remote server, after tmux attach:
_iterm2_setup() {
  local session_num=$1
  local session_type=$2  # "cc", "shell", "caffeinate", etc.

  # 1. Switch to the right profile
  case "$session_type" in
    cc)       printf '\e]1337;SetProfile=ClaudeCode\a' ;;
    shell)    printf '\e]1337;SetProfile=ShellUtility\a' ;;
    *)        printf '\e]1337;SetProfile=Default\a' ;;
  esac

  # 2. Set rolling tab color (CC sessions only)
  if [[ "$session_type" == "cc" ]]; then
    local colors=("6440dc" "4a90d9" "2ecc71" "e67e22" "e74c3c"
                  "1abc9c" "9b59b6" "f39c12" "3498db" "e91e63")
    local idx=$(( (session_num - 1) % ${#colors[@]} ))
    printf '\e]1337;SetColors=tab=%s\a' "${colors[$idx]}"
  fi

  # 3. Set user variables for badge interpolation
  printf '\e]1337;SetUserVar=%s=%s\a' \
    "sessionType" "$(echo -n "$session_type" | base64)"
  printf '\e]1337;SetUserVar=%s=%s\a' \
    "sessionNum" "$(echo -n "$session_num" | base64)"

  # 4. Set badge directly
  local badge_text="$session_type sw-$session_num"
  printf '\e]1337;SetBadgeFormat=%s\a' \
    "$(echo -n "$badge_text" | base64)"

  # 5. Set tab title
  printf '\e]0;%s\a' "CC sw-$session_num"
}

# Called from ai-cli after tmux attach succeeds:
_iterm2_setup 3 "cc"
```

**Option B: AppleScript from Mac (for initial layout creation)**

```applescript
tell application "iTerm2"
  tell current window
    -- Create a new tab with the ClaudeCode profile
    create tab with profile "ClaudeCode"
    tell current session of current tab
      set name to "CC sw-3"
      write text "ssh sergei@178.104.70.139 -t 'tmux attach -t c-r-sw-3 || tmux new -s c-r-sw-3'"
    end tell
  end tell
end tell
```

Invoke from terminal: `osascript -e 'tell application "iTerm2" ...'`

**Option C: Python API (for complex fleet operations)**

```python
#!/usr/bin/env python3
import iterm2

CC_COLORS = [
    iterm2.Color(100, 64, 220),   # purple
    iterm2.Color(74, 144, 217),   # blue
    iterm2.Color(46, 204, 113),   # green
    iterm2.Color(230, 126, 34),   # orange
    iterm2.Color(231, 76, 60),    # red
    iterm2.Color(26, 188, 156),   # teal
    iterm2.Color(155, 89, 182),   # violet
    iterm2.Color(243, 156, 18),   # amber
    iterm2.Color(52, 152, 219),   # sky blue
    iterm2.Color(233, 30, 99),    # pink
]

async def launch_cc_session(connection, session_num):
    app = await iterm2.async_get_app(connection)
    window = app.current_terminal_window

    # Create tab with profile
    customizations = iterm2.LocalWriteOnlyProfile()
    color = CC_COLORS[(session_num - 1) % len(CC_COLORS)]
    customizations.set_tab_color(color)
    customizations.set_use_tab_color(True)
    customizations.set_badge_text(f"CC sw-{session_num}")

    tab = await window.async_create_tab(
        profile="ClaudeCode",
        profile_customizations=customizations
    )

    session = tab.current_session
    await session.async_send_text(
        f"ssh sergei@178.104.70.139 -t "
        f"'tmux attach -t c-r-sw-{session_num} || "
        f"tmux new -s c-r-sw-{session_num}'\n"
    )

async def main(connection):
    # Launch sessions 1-5
    for n in range(1, 6):
        await launch_cc_session(connection, n)

iterm2.run_until_complete(main)
```

Note: `command` and `profile_customizations` are mutually exclusive in `async_create_tab()`. Use `profile_customizations` for visual setup + `async_send_text()` for the SSH command. [VERIFIABLE FACT: [Window API docs](https://iterm2.com/python-api/window.html)]

### Escape Sequence Compatibility Through SSH + tmux

Escape sequences emitted on the remote server travel through SSH to iTerm2 on the Mac. iTerm2 parses its proprietary OSC sequences regardless of whether they originate locally or remotely. This works for standard SSH. For tmux (non-CC mode), sequences must pass through tmux's terminal -- tmux may need `set -g allow-passthrough on` in tmux.conf for iTerm2 proprietary sequences to reach the terminal emulator. [INDUSTRY HEURISTIC: tmux passthrough is a known requirement for escape sequences]

```bash
# In remote ~/.tmux.conf:
set -g allow-passthrough on
```

## 7. Persistence Across Restarts

### Window Arrangements

Save and restore window/tab/pane layouts: [VERIFIABLE FACT: [Arrangements docs](https://iterm2.com/documentation-preferences-arrangements.html)]

| # | Action | Shortcut | Notes |
|---|--------|----------|-------|
| 1 | Save Window Arrangement | `Cmd+Shift+S` | Snapshots all windows, tabs, pane positions |
| 2 | Restore Window Arrangement | `Cmd+Shift+R` | Restores saved layout |
| 3 | Auto-restore on launch | Settings > General > Startup > "Open saved window arrangement" | Requires a saved arrangement |

### Session Restoration

iTerm2 can restore session content and running processes using persistent servers. [VERIFIABLE FACT: [Session Restoration docs](https://iterm2.com/documentation-restoration.html)]

**Limitation for SSH sessions:** Session restoration restores locally-running processes. SSH connections are local processes, so the SSH tunnel itself may be re-established, but the remote tmux session state is maintained by tmux independently. On iTerm2 restart, the SSH connections will be dead; you need to reconnect. [SYNTHESIZED INFERENCE]

### Recommended Persistence Strategy

For your workflow, persistence is the **wrong goal** -- your sessions are SSH tunnels to tmux sessions that are already persistent on the server. The layout is simple and fast to recreate. Recommended approach:

1. **Don't rely on Window Arrangements** for SSH sessions. They will save the layout geometry but not the live SSH connections.
2. **Let `ai-cli` be the layout creator.** Add a command like `ai fleet` or `ai layout` that:
   - Opens N tabs (one per active CC session)
   - SSHs and attaches to each tmux session
   - Sets colors/badges/profiles via escape sequences
3. **tmux sessions are the persistence layer.** They survive across iTerm2 restarts, Mac reboots, and network drops. Just reconnect.

## 8. Configuration Walkthrough

### Step 1: Create Dynamic Profiles

Create the file `~/Library/Application Support/iTerm2/DynamicProfiles/humanware-sessions.json`:

```json
{
  "Profiles": [
    {
      "Name": "ClaudeCode",
      "Guid": "humanware-claude-code-001",
      "Dynamic Profile Parent Name": "Default",
      "Badge Text": "\\(user.sessionType) sw-\\(user.sessionNum)",
      "Use Tab Color": true,
      "Tab Color": {
        "Red Component": 0.39,
        "Green Component": 0.25,
        "Blue Component": 0.86,
        "Color Space": "sRGB"
      },
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/claude-logo.png",
      "Tags": ["claude", "ai-agent"]
    },
    {
      "Name": "ShellUtility",
      "Guid": "humanware-shell-utility-001",
      "Dynamic Profile Parent Name": "Default",
      "Badge Text": "\\(user.sessionType)",
      "Use Tab Color": true,
      "Tab Color": {
        "Red Component": 0.5,
        "Green Component": 0.5,
        "Blue Component": 0.5,
        "Color Space": "sRGB"
      },
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/terminal.png",
      "Tags": ["shell", "utility"]
    },
    {
      "Name": "Caffeinate",
      "Guid": "humanware-caffeinate-001",
      "Dynamic Profile Parent Name": "ShellUtility",
      "Badge Text": "caffeinate",
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/coffee.png",
      "Tags": ["utility", "caffeinate"]
    },
    {
      "Name": "PortForward",
      "Guid": "humanware-port-forward-001",
      "Dynamic Profile Parent Name": "ShellUtility",
      "Badge Text": "SSH tunnel",
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/ssh.png",
      "Tags": ["utility", "ssh"]
    },
    {
      "Name": "DebugBrowser",
      "Guid": "humanware-debug-browser-001",
      "Dynamic Profile Parent Name": "ShellUtility",
      "Badge Text": "Chrome debug",
      "Icon": 2,
      "Custom Icon Path": "/Users/sergei/.config/iterm2/icons/chrome.png",
      "Tags": ["utility", "browser"]
    }
  ]
}
```

### Step 2: Prepare Icons

Create icon directory and add PNG icons:

```bash
mkdir -p ~/.config/iterm2/icons

# Download or create these icons (64x64 PNG, transparent background):
# claude-logo.png  -- Anthropic/Claude logo
# terminal.png     -- generic terminal icon
# coffee.png       -- coffee cup for caffeinate
# ssh.png          -- lock/key icon for SSH tunnels
# chrome.png       -- Chrome logo for debug browser
```

### Step 3: Configure tmux Passthrough

On the remote server, add to `~/.tmux.conf`:

```bash
# Allow iTerm2 escape sequences to pass through tmux
set -g allow-passthrough on
```

Reload: `tmux source-file ~/.tmux.conf`

### Step 4: Add iTerm2 Setup to ai-cli

Add the `_iterm2_setup` function from Section 6 to `ai-cli`. Call it after tmux session creation/attachment in the `ai c N` flow. The function should:

1. Detect if running inside iTerm2 (check `$TERM_PROGRAM == "iTerm.app"` or `$LC_TERMINAL == "iTerm2"`)
2. Emit SetProfile escape sequence
3. Emit rolling tab color
4. Set user variables and badge
5. Set tab title

### Step 5: Configure Fleet Navigation Shortcuts

In iTerm2 Settings > Keys, consider adding:

| # | Shortcut | Action | Purpose |
|---|----------|--------|---------|
| 1 | `Cmd+Option+E` | Expose All Tabs | Fleet overview (built-in) |
| 2 | `Cmd+1-9` | Select Tab | Jump to session by position (built-in) |
| 3 | `Ctrl+Option+N` (custom) | Open URL / Run Script | Launch `ai fleet` to recreate layout |

### Step 6: Create a Fleet Layout Script

For initial session creation from the Mac:

```bash
#!/bin/bash
# ~/bin/iterm2-fleet-launch.sh
# Creates tabs for all active CC sessions

SERVER="sergei@178.104.70.139"
SESSIONS=(1 2 3 4 5)  # Active session numbers

for num in "${SESSIONS[@]}"; do
  osascript <<EOF
tell application "iTerm2"
  tell current window
    create tab with profile "ClaudeCode"
    tell current session of current tab
      set name to "CC sw-$num"
      write text "ssh $SERVER -t 'tmux attach -t c-r-sw-$num 2>/dev/null || echo No session c-r-sw-$num'"
    end tell
  end tell
end tell
EOF
done
```

### Step 7 (Optional): 4-Pane Monitoring Layout

For a monitoring dashboard view:

```applescript
tell application "iTerm2"
  create window with profile "ShellUtility"
  tell current window
    tell current session of current tab
      set name to "Monitor 1"
      write text "ssh sergei@178.104.70.139 -t 'tmux attach -t c-r-sw-1'"
      -- Split right
      set rightPane to (split vertically with profile "ShellUtility")
      tell rightPane
        set name to "Monitor 2"
        write text "ssh sergei@178.104.70.139 -t 'tmux attach -t c-r-sw-2'"
      end tell
    end tell
    -- Split current pane down
    tell current session of current tab
      set bottomLeft to (split horizontally with profile "ShellUtility")
      tell bottomLeft
        set name to "Monitor 3"
        write text "ssh sergei@178.104.70.139 -t 'tmux attach -t c-r-sw-3'"
      end tell
    end tell
  end tell
end tell
```

[Note: AppleScript split pane commands use `split horizontally/vertically with profile "X"`. The exact nesting for 4-pane layouts may require testing -- the above is illustrative. [INDUSTRY HEURISTIC]]

## Recommendation

**Implement in this order:**

1. **Dynamic Profiles JSON** (30 min) -- immediate visual identity for all session types, hot-reloaded
2. **Escape sequences in ai-cli** (1 hr) -- rolling colors, badges, profile switching on `ai c N`
3. **tmux passthrough** (5 min) -- one-line config change on server
4. **Fleet launch script** (30 min) -- AppleScript to create all tabs at once
5. **Icons** (30 min) -- download/create PNGs, add to Dynamic Profiles
6. **Python API script** (optional, 2 hr) -- only if AppleScript proves insufficient

Skip tmux -CC integration mode for now. It adds complexity without benefit for your use case of managing independent tmux sessions. Standard SSH + tmux attach gives full flexibility with iTerm2 escape sequence customization.

## Open Questions

1. **Icon format verification needed:** What image formats does iTerm2 actually accept for custom profile icons? The documentation does not specify. Test with PNG, then ICNS if needed.

2. **tmux passthrough behavior:** Does `allow-passthrough on` work for all iTerm2 proprietary escape sequences, or only some? Test SetProfile, SetColors, SetBadgeFormat, and SetUserVar through tmux.

3. **Tab color picker customization:** Is there a plist or config file that defines the preset colors in iTerm2's right-click tab color menu? The source code would need to be checked. [NO SOURCE FOUND]

4. **Badge visibility in small panes:** How readable are badges in a 4-pane split layout? May need to reduce badge max width/height or use shorter text for split configurations.

5. **Python API remote invocation:** Can the Python API be invoked from a remote machine over the iTerm2 websocket? The docs suggest it connects to a running iTerm2 instance, which implies local-only, but this should be verified.

6. **Dynamic Profile key completeness:** The JSON key names were confirmed from source code for icon, tab color, and badge. Other keys (badge font, badge margins, etc.) should be confirmed by exporting a profile from the iTerm2 UI.

## Sources

- [iTerm2 General Profile Preferences (Icons)](https://iterm2.com/documentation-preferences-profiles-general.html) -- official docs
- [iTerm2 Automatic Profile Switching](https://iterm2.com/documentation-automatic-profile-switching.html) -- official docs
- [iTerm2 Proprietary Escape Codes](https://iterm2.com/documentation-escape-codes.html) -- official docs
- [iTerm2 Badges](https://iterm2.com/documentation-badges.html) -- official docs
- [iTerm2 Dynamic Profiles](https://iterm2.com/documentation-dynamic-profiles.html) -- official docs
- [iTerm2 Triggers](https://iterm2.com/documentation-triggers.html) -- official docs
- [iTerm2 tmux Integration](https://iterm2.com/documentation-tmux-integration.html) -- official docs
- [iTerm2 Variables](https://iterm2.com/documentation-variables.html) -- official docs
- [iTerm2 Session Restoration](https://iterm2.com/documentation-restoration.html) -- official docs
- [iTerm2 Window Arrangements](https://iterm2.com/documentation-preferences-arrangements.html) -- official docs
- [iTerm2 Scripting (AppleScript)](https://iterm2.com/documentation-scripting.html) -- official docs
- [iTerm2 Python API - Profile](https://iterm2.com/python-api/profile.html) -- official API docs
- [iTerm2 Python API - Window](https://iterm2.com/python-api/window.html) -- official API docs
- [iTerm2 Python API - Set Tab Color Example](https://iterm2.com/python-api/examples/settabcolor.html) -- official example
- [iTerm2 Python API - Set Profile Example](https://iterm2.com/python-api/examples/setprofile.html) -- official example
- [iTerm2 Python API - Launch and Run](https://iterm2.com/python-api/examples/launch_and_run.html) -- official example
- [iTerm2 source: profile.py (JSON key names)](https://github.com/gnachman/iTerm2/blob/master/api/library/python/iterm2/iterm2/profile.py) -- source code
- [GitLab Issue #4543: APS with Integrated Tmux](https://gitlab.com/gnachman/iterm2/-/issues/4543) -- issue tracker
- [Changing iTerm2 Profile Programmatically](https://til-engineering.nulogy.com/Changing-Your-iterm2-Profile-Programmatically/) -- community reference
- [tmux in practice: iTerm2 and tmux (freeCodeCamp)](https://www.freecodecamp.org/news/tmux-in-practice-iterm2-and-tmux-integration-7fb0991c6c01/) -- community reference
- [iterm2-tab-color (GitHub)](https://github.com/connordelacruz/iterm2-tab-color) -- community tool
- [iterm2-tab-set (GitHub)](https://github.com/jonathaneunice/iterm2-tab-set) -- community tool

## Appendix: Research Prompt

**Registry ID:** R-36
**Model:** `claude-opus-4-6` (1M context)
**Date:** 2026-03-29

```
Research iTerm2 power user configuration for managing 5-10 parallel AI coding agent SSH sessions.

CONTEXT:
I manage 5-10 concurrent Claude Code sessions on a remote Hetzner dev server via SSH + tmux.
I've just switched from Ghostty to iTerm2 for its richer split/tab UX (tab-to-split drag, badges,
tmux Dashboard, per-session profiles). I already know the basics: renaming tab/window titles,
setting tab colors, doing 2-way and 4-way splits, and setting per-pane session names.

Session types: Claude Code (5-10, rolling colors), Shell/utility (coffee, SSH, Chrome icons).
All SSH to same Hetzner server, attach to different tmux sessions (c-r-sw-1, c-r-sw-2, etc.).

QUESTIONS:
1. Tab/pane icons: custom images in tabs, formats, programmatic setting, icon sources
2. Per-session profiles with auto-detection based on tmux session name
3. Rolling colors for CC sessions: auto-assign, escape sequences, persistence, picker customization
4. Badges: dynamic status, escape sequences, formatting options
5. Fleet overview: Expose, tmux Dashboard, navigation shortcuts, fuzzy search
6. Automation: AppleScript vs Python API, ai-cli integration
7. Persistence: Window Arrangements, auto-restore
8. Configuration walkthrough: step-by-step for the use case

IMPORTANT: Verify ALL claims against iTerm2 documentation. Tag unverifiable claims.
```
