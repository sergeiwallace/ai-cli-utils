---
title: "[BUG-001] iTerm2 tab title and color system — multiple bugs"
category: bugs
tags: [iterm2, tab-title, tab-color, session-title, gemini, remote, mosh]
status: investigating
severity: P1
related_docs:
  - docs/plans/iterm2-smart-titles-plan.md
---

# [BUG-001] iTerm2 Tab Title and Color System — Multiple Bugs

**Status:** investigating

**Severity:** P1

**Created:** 2026-04-02

---

## Table of Contents

- [Overview](#overview)
- [Bug 1 — Color collision across same-numbered sessions](#bug-1--color-collision-across-same-numbered-sessions)
- [Bug 2 — Remote CC sessions: wrong icon, wrong title format, no tab color](#bug-2--remote-cc-sessions-wrong-icon-wrong-title-format-no-tab-color)
- [Bug 3 — Gemini sessions: tab color applies but title shows "Default"](#bug-3--gemini-sessions-tab-color-applies-but-title-shows-default)
- [Bug 4 — Gemini launched from wrong directory: "Invalid session identifier" error](#bug-4--gemini-launched-from-wrong-directory-invalid-session-identifier-error)
- [Bug 5 — Gemini launched from wrong directory with no existing session: blank screen](#bug-5--gemini-launched-from-wrong-directory-with-no-existing-session-blank-screen)
- [Bug 6 — Remote gemini sessions: same invalid session identifier error](#bug-6--remote-gemini-sessions-same-invalid-session-identifier-error)
- [Bug 7 — Only three icon color variants for Claude and Gemini logos](#bug-7--only-three-icon-color-variants-for-claude-and-gemini-logos)
- [Diagnosis Approach](#diagnosis-approach)

---

## Overview

The iTerm2 per-pane title and rolling tab color system was refactored in the previous session to replace a flawed shared-state combined-title architecture. The new per-pane approach resolves the original ghosting/contamination bugs but has introduced or revealed a new set of issues affecting color assignment, remote sessions, Gemini sessions, and icon color variety.

All bugs were reported 2026-04-02 after testing on freshly opened iTerm2 tabs.

---

## Bug 1 — Color collision across same-numbered sessions

### Symptom

Multiple CC sessions with the same trailing session number but different project names all receive the same tab color. Examples observed:

- `c-ai-cli-2`, `c-art-2`, and `c-aido-2` → all orange with white Claude logo
- `c-sw-6` and `c-sw-7` → both blue (correct colors for their slot, but adjacent tabs should contrast)

### Expected behavior

Adjacent/open tabs in the same iTerm2 window should have visually distinct, contrasting colors. Color should not be assigned based solely on the session number suffix, because multiple projects open simultaneously will share the same number and therefore collide on the same color. The color assignment strategy must account for what colors are already in use in the current window.

---

## Bug 2 — Remote CC sessions: wrong icon, wrong title format, no tab color

### Symptom

Remote CC sessions (connected via mosh to the Hetzner server) show:

- **Tab icon**: terminal/shell icon instead of the Claude logo
- **Tab title**: `[mosh] * ▶ {session-name}` — includes a `[mosh]` prefix and a literal `*` that should not appear in a single-pane tab title
- **Tab color**: grey (the default; no custom color applied)

### Expected behavior

Remote CC sessions should display:
- **Tab icon**: Claude logo (matching the profile color based on session slot)
- **Tab title**: `▶ {session-name}` (no `[mosh]` prefix, no `*` unless in a split-pane context)
- **Tab color**: rolling color matching the session slot, same as local sessions

The `*` symbol is a session-type indicator that only makes sense in a multi-pane split context. In a single-pane tab, the Claude logo icon is sufficient to communicate session type. For multi-pane tabs, type symbols should appear but without the mosh prefix.

---

## Bug 3 — Gemini sessions: tab color applies but title shows "Default"

### Symptom

Local Gemini sessions (`ai g N` or `ai g N -p PROJECT`):

- The GeminiCLI iTerm2 profile does apply — the tab turns a custom light blue color and the Gemini logo appears
- However, the tab/pane **title text** shows "Default" rather than the expected `✦ ▶ {session-name}` format
- This occurs whether or not `-p PROJECT_NAME` is passed and whether or not the user is in the correct project directory

### Expected behavior

Gemini tabs should show: `✦ ▶ {session-name}` (e.g., `✦ ▶ g-art-1`) with the Gemini logo icon and the light blue color. The title should update dynamically as the session state changes (running, done, error, etc.), matching the same status symbol system used by CC sessions.

---

## Bug 4 — Gemini launched from wrong directory: "Invalid session identifier" error

### Symptom

Running `ai g N -p PROJECT` from a directory that is not the target project root (e.g., running `ai g 1 -p artelier` from `~/projects/sergei/`) when an existing Gemini session conversation exists for that project produces a repeating error loop:

```
Error resuming session: Invalid session identifier "e2c504cc-c47e-4988-a268-1b4db0688464".
  Searched for sessions in /Users/sergeiwallace/.gemini/tmp/sw-1-1/chats.
  Use --list-sessions to see available sessions, then use --resume {number}, --resume {uuid}, or --resume latest.
Resuming... (Ctrl-C to exit)
```

The error repeats indefinitely. The session is never resumed.

### Expected behavior

`ai g N -p PROJECT` should always launch or resume the Gemini session in the context of the target project, regardless of where the command is run from. If a saved session UUID exists for that project's session name, the resume should succeed by looking in the correct project's Gemini chats directory.

---

## Bug 5 — Gemini launched from wrong directory with no existing session: blank screen

### Symptom

Running `ai g N -p PROJECT` from a non-project directory when no existing Gemini session conversation exists for that project produces a blank black screen that never progresses. The `session context written to .gemini/signals/session-context.md` message (which normally appears on a successful fresh start) never appears. The terminal hangs indefinitely until `Ctrl+C`.

If you wait before pressing `Ctrl+C`, the behavior eventually transitions to the "Invalid session identifier" error loop from Bug 4.

After `Ctrl+C`, the custom tab color (light blue) and "Default" title sometimes persist and sometimes revert to the default terminal appearance — inconsistently.

### Expected behavior

`ai g N -p PROJECT` with no existing session should start a fresh Gemini session in the target project's directory, with the session context written and the Gemini prompt appearing normally.

---

## Bug 6 — Remote Gemini sessions: same "Invalid session identifier" error

### Symptom

Launching a remote Gemini session (via `ai g N -p PROJECT` over mosh from a non-project directory on the server) produces the same "Invalid session identifier" error as Bug 4. The session search path is incorrect — it references the wrong project's Gemini chats directory. The terminal ultimately exits back to either the remote server shell or the local machine shell inconsistently.

### Expected behavior

Remote Gemini sessions should launch and resume correctly from the target project's directory on the server, using the correct Gemini chats directory for that project, regardless of where the command is initiated.

---

## Bug 7 — Only three icon color variants for Claude and Gemini logos

### Symptom

The Claude logo in the tab/pane header can only appear in three colors:
- Coral/salmon (default `ClaudeCode` profile) — for cool/dark tab backgrounds
- White (`ClaudeCode-W`) — for warm/saturated tab backgrounds
- Dark navy (`ClaudeCode-D`) — for bright/light tab backgrounds

The Gemini logo has only one color variant. This produces limited visual variety — many tabs end up looking similar (white-on-orange, white-on-red) and there is no support for more colorful logo icon options (e.g., purple, gold, cyan, green Claude logo variants).

### Expected behavior

The system should support a richer set of logo icon color profiles — ideally 6 or more Claude variants and multiple Gemini variants — so that each tab's background color is paired with a distinct and visually interesting icon color rather than always defaulting to white or dark navy. The logo icon color should feel like a deliberate design choice, not just a legibility fallback.

The preference is not to rely on creating a large number of static iTerm2 profiles if there is a better mechanism (e.g., dynamic coloring via OSC sequences or badge overlays), but if profiles are the only viable path, a larger set of profiles is acceptable.

---

## Diagnosis Approach

> Note: This section documents the proposed diagnostic direction. It will be replaced with root cause findings and confirmed fixes once the design doc is reviewed and approved.

### Color collision (Bug 1)

The core issue is that `idx = (num - 1) % 12` uses only the session number suffix, which is not unique across projects. The fix likely involves querying what colors are already in use in the current iTerm2 window and assigning a non-colliding slot. This requires either reading `ITERM_SESSION_ID` to enumerate same-window panes or maintaining a local state file of window → active colors.

### Remote OSC sequence filtering (Bug 2)

Mosh intercepts and filters `\033]1337;` (iTerm2-proprietary) sequences — so `SetProfile` and `SetColors=tab=` never reach iTerm2 from remote sessions. Mosh also prepends `[mosh] ` to all OSC 0 window titles it receives. The fix for remote sessions likely requires running the profile/color setup on the local side before the mosh connection is established, or an iTerm2 Automatic Profile Switching rule keyed on the remote hostname.

### Gemini title not applied (Bug 3)

The `SetProfile=GeminiCLI` OSC sequence applies correctly but the OSC 0 title appears to be overridden or suppressed. Likely candidates: the GeminiCLI iTerm2 profile has a fixed "Tab title" override setting, or the `session_name` argument to `_emit_iterm2_profile_setup` is empty when gemini sessions launch, or the title is being reset by something (tmux, gemini CLI itself) after it's set.

### Gemini chats directory mismatch (Bugs 4, 5, 6)

Gemini derives its chats storage path from the current working directory at launch time. When launched from the wrong directory, the UUID stored in the session map points to a chats directory that gemini doesn't look in. The fix requires ensuring gemini always launches (or at minimum looks for sessions) in the target project's directory. The `cd {worktree_path}` in the bash script should handle this when a worktree exists, but may fail silently when it doesn't or when the path is wrong.

### Icon color variety (Bug 7)

Adding more color variants requires either: (a) additional iTerm2 Dynamic Profile entries with differently-colored logo PNGs, or (b) a mechanism to dynamically colorize the icon at emit time. The feasibility of option (b) needs investigation — iTerm2's badge system or inline image protocol may allow this without requiring static profile proliferation.
