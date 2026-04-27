---
title: "[BUG-001] iTerm2 tab title and color system — multiple bugs"
category: bugs
tags: [iterm2, tab-title, tab-color, session-title, gemini, remote, mosh]
status: uat-in-progress
severity: P1
related_docs:
  - docs/designs/iterm2-title-color-system.md
  - docs/test/uat-iterm2-title-color-redesign.md
  - docs/plans/iterm2-smart-titles-plan.md
---

# [BUG-001] iTerm2 Tab Title and Color System — Multiple Bugs

**Status:** UAT in progress — Bugs 8/9 and UX feedback added 2026-04-03. Bugs 1–7 fix shipped; awaiting full UAT (`[AI-CLI-18]`, `docs/test/uat-iterm2-title-color-redesign.md`)

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
- [Bug 8 — Session Name reverts to "Default" after Edit Session interaction](#bug-8--session-name-reverts-to-default-after-edit-session-interaction)
- [Bug 9 — `ai c` launch drops session into tmux copy mode](#bug-9--ai-c-launch-drops-session-into-tmux-copy-mode)
- [Bug 10 — Session Name changes to another session's name randomly](#bug-10--session-name-changes-to-another-sessions-name-randomly)
- [Diagnosis Approach](#diagnosis-approach)

---

## Overview

The iTerm2 per-pane title and rolling tab color system was refactored in the previous session to replace a flawed shared-state combined-title architecture. The new per-pane approach resolves the original ghosting/contamination bugs but has introduced or revealed a new set of issues affecting color assignment, remote sessions, Gemini sessions, and icon color variety.

All bugs were reported 2026-04-02 after testing on freshly opened iTerm2 tabs.

---

## Bug 1 — Color collision across same-numbered sessions

### Symptom

Multiple CC sessions with the same trailing session number but different project names all receive the same tab color. Examples observed:

- `c-ai-cli-2`, `c-proj-2`, and `c-other-2` → all orange with white Claude logo
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

Running `ai g N -p PROJECT` from a directory that is not the target project root (e.g., running `ai g 1 -p myproject` from a different project directory) when an existing Gemini session conversation exists for that project produces a repeating error loop:

```text
Error resuming session: Invalid session identifier "e2c504cc-c47e-4988-a268-1b4db0688464".
  Searched for sessions in ~/.gemini/tmp/sw-1-1/chats.
  Use --list-sessions to see available sessions, then use --resume {number}, --resume {uuid}, or --resume latest.
Resuming... (Ctrl-C to exit)
```text

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

## Raw User Feedback

Verbatim message from user (2026-04-02), basis for all bugs documented above:

---

the iterm2 session/tab title & auto-rotating color system isn't working correctly. I've created brand new terminal tabs for all this so we're starting completely fresh with regards to that.

bugs I've noticed:
1. the session/tab title system appears to be working for local cc session startup e.g. `ai c {N} -p {PROJECT_NAME}`, `ai c` or `ai c {N}`. it shows the proper logo and the play symbol to the right of the cc tmux session name.
2. for local cc sessions, the terminal session/tab color appears to rotate colors inconsistently or not at all, it seems. here's what I observed:
   2a. `c-ai-cli-2`, `c-proj-2`, and `c-other-2` became orange with a white claude logo
   2b. `c-ai-cli-3` became yellow with black claude logo
   2c. `c-sw-5` became turqoise with a black claude logo. this is the only instance where i've seen a non-standard color.
   2d. `c-sw-6` and `c-sw-7` became blue with an orange claude logo
3. for remote cc sessions, the tab title formatting is wrong. it shows the terminal icon and the text "[mosh] * {PLAY_SYMBOL} {CC_TMUX_SESSION_NAME}". the tab/session color is always grey (the actual color, not no color). it should have the claude logo icon instead of the terminal and no "[mosh]" and no "*" unless it's a tab with split panes. that * is for the pane title, not the tab title. the tab title should already have the claude etc logo icon which should be sufficient. and we don't need to worry about signaling multiple types of terminal sessions with a single title. i asked for the previously but we can scrap that.
4. for local gemini sessions, there are couple issues:
   4a. if you're in the correct directory and use or don't use the `-p {PROJECT_NAME`, then it's able to start and the tab color appears as a custom color lighter blue with a white gemini logo icon but no tab/session title. it just says "Default". it's able to start a new gemini session conversation or successfully resume an existing one.
   4b. if you're in a different directory than the project root you want to open gemini session from (e.g. running `ai g 1 -p myproject` from a different project directory) and there's an existing gemini session to resume in the target project's git worktree (e.g. `c-proj-1` and `wt-proj-1`), then you get this error on repeat and can't resume the existing gemini session if there is one:
```text
YOLO mode is enabled. All tool calls will be automatically approved.
Keychain initialization encountered an error: Cannot find module '../build/Release/keytar.node'
Require stack:
- /usr/local/Cellar/gemini-cli/0.36.0/libexec/lib/node_modules/@google/gemini-cli/node_modules/keytar/lib/keytar.js
Using FileKeychain fallback for secure storage.
Loaded cached credentials.
YOLO mode is enabled. All tool calls will be automatically approved.
Detected terminal background color: #121521
Detected terminal name: tmux 3.6a
Error resuming session: Invalid session identifier "e2c504cc-c47e-4988-a268-1b4db0688464".
  Searched for sessions in ~/.gemini/tmp/sw-1-1/chats.
  Use --list-sessions to see available sessions, then use --resume {number}, --resume {uuid}, or --resume latest.Resuming... (Ctrl-C to exit)
```text
   4c. if you're in a different directory than the target directory you want to launch a gemini session (e.g. `ai g 2 -p myproject` from a different project directory) and there's no existing git worktree or gemini session conversation to resume, then you just get a blank screen that never even gets to the `session context written to .gemini/signals/session-context.md` text. it just stays blank black screen indefinitely until you ctrl + C out of it. if you ctrl + C quickly enough, it's take you back to local machine shell. if you wait a bit, it'll start doing the same failure to resume existing gemini session error above in 4b. also, sometimes the custom light blue tab color and "Default" tab title persist and sometimes it goes back to terminal logo with "Default" title.
5. for remote gemini sessions that are in different directory than the target direct you want to launch gemini session from, you get same can't resume existing gemini session error from 4b. it exiting me back to a `sw-1` git worktree root on dev server once (the tab title was the usual "[mosh] ..." and other times it kicked me back to local machine shell. not sure what to make of that inconsistency.

review this and create a bug doc (create a `docs/bugs` directory if needed) and in your words write out all the bugs/behaviors I identified and what the fixed behaviors should be (not how to fix, but just what the corrected tab/session titles & color system should be). then also propose how you might go about diagnosing the root causes of the bugs and potential ways to fix them (it's okay if this is a high level outline since you'll need to actually debug etc to identify it). i'll review to make sure you understand each of the bugs/incorrect behaviors and understand what the behaviors should actually be before you start working on diagnosing and fixing the bugs (root cause). we need a more robust and systematic implementation to do this robustly (appropriate tab/session title naming that dynamically changes depending on whether it's a cc / gemini session on either local or remote or a local shell or remote shell etc). right now it's still very buggy and the auto-color rotation to make sure colors between neighboring tabs in a iterm2 window or neighboring split terminal panes within a tab are always different and contrasting colors. also, I want more colors for the claude/gemini logos than orange (claude logo), black, and white. we should have a number of different templates for different colors to have better and more colorful contrast. sometimes black and white with a color background is fine but i want more variety. we should have templates. and ideally we don't have to rely on creating a bunch of different profiles unless that's the only way to have different color logo icons in the tab title.

---

## Bug 8 — Session Name reverts to "Default" after Edit Session interaction

### Symptom

After `ai c N` launches and the tab title correctly shows `c-sw-N`, opening the Edit Session dialog reveals that the **Session Name field still reads "Default"**. Closing the dialog (even without making changes) causes the tab title to revert to "Default", overwriting the correct session name that was set at launch.

### Expected behavior

The Session Name field should be permanently set to the tmux session name (e.g., `c-sw-6`) at launch time, so that it persists through Edit Session interactions and is stable as the ground truth for the tab title. The Session Title dropdown should be set to `Name` (not `Shell`) so the tab always displays the Session Name value without any shell-controlled overrides.

---

## Bug 9 — `ai c` launch drops session into tmux copy mode

### Symptom

Running `ai c 7` and `ai c 8` from a project root directory results in the new pane getting stuck in tmux copy mode immediately after launch. The session name and tab color apply correctly, but the terminal is unresponsive until the user manually exits copy mode. Not observed with `ai c 6` or `ai c 9 -R`.

### Expected behavior

`ai c N` should always land in a normal interactive shell/CC prompt, never in tmux copy mode.

---

## UX Feedback — Ad hoc color and icon control

### Current limitation

The auto-rotating color system assigns a color slot at launch time and locks it. There is no mechanism to change the tab color or icon color after a session has started without restarting the session. The user has no way to override the assigned color ad hoc.

### Desired behavior

A way to reassign the tab color or switch between color profiles for an already-running session — ideally a command like `ai color <name>` or via a simple UI mechanism. The color assignment should feel like a user-controllable preference, not an immutable system decision made at launch.

---

## UX Feedback — Custom `"Shell"` Session Title option disappears

### Current limitation

When a ClaudeCode-* or GeminiCLI profile is applied to a session, the Session Title dropdown shows a custom `"Shell"` option. Once the user switches to any built-in option (Name, Profile Name, etc.), the `"Shell"` option permanently disappears and cannot be restored without re-applying the profile.

### Desired behavior

Nice-to-have: the `"Shell"` option should remain available as a persistent dropdown choice so users can toggle between it and built-in options freely. Not a priority for the redesign — flagged as a known limitation.

---

## Bug 10 — Session Name changes to another session's name randomly

### Symptom

After `ai c N` launches and sets the tab title correctly, the tab/pane Session Name subsequently changes to a different session's name without any user action. This is distinct from Bug 8 (reverts to "Default") — the name changes to an actively running session name (e.g., the current pane shows "c-ai-cli-3" but the Session Name field changes to "c-ai-cli-2").

The user also reported receiving an iTerm2 error popup during or shortly after session launch.

### Root cause

`_do_session_launch` in `main.py` used `os.environ.get("ITERM_SESSION_ID")` in two places:

1. Building `_iterm_env_flags` (the `-e ITERM_SESSION_ID=...` flag passed to `tmux new-session`)
2. Setting `_new_iterm_id` for the `tmux set-environment` call on the re-attach path

The shell environment's `ITERM_SESSION_ID` is set once when the iTerm2 pane is first created and is never refreshed — it becomes stale after the session is re-attached to a different pane. When `ai c` is run from inside an existing tmux session, the stale shell-env GUID propagates into the new/target session's tmux environment. The session script then calls `ai internal set-iterm2-name` with that stale GUID, renaming a pane that no longer belongs to it.

Meanwhile, `_emit_iterm2_profile_setup` already had the correct fix — it reads from the tmux session env via `tmux show-environment` when inside tmux. But this logic was not shared with the other two call sites.

### Fix Round 1 (2026-04-25, AI-CLI-59) — INCOMPLETE

Extracted the GUID resolution logic into `_get_current_iterm_session_id()` in `iterm2.py`:
- Outside tmux: returns `os.environ.get("ITERM_SESSION_ID")` (unchanged)
- Inside tmux: reads `tmux show-environment ITERM_SESSION_ID` from the session env (current GUID, not stale)

Updated all three call sites in `_do_session_launch` and `_emit_iterm2_profile_setup` to use this helper. Added 6 tests. Bug persists — see Round 2 analysis below.

### Fix Round 2 — Root cause (REOPENED 2026-04-26)

The Round 1 fix introduced a new failure path. `_get_current_iterm_session_id()` prefers `tmux show-environment` when `TMUX` is set — but `tmux show-environment` (without `-t`) reads from the **calling session's** env, which is the **parent** session's stored GUID when `ai c N` is launched from inside another tmux session.

**Failing scenario:** user is in pane B (GUID `bbb`, set by iTerm2 shell integration at pane creation) attached to existing tmux session `c-aido-1` (whose tmux session env has GUID `aaa` from its original pane). Runs `ai c 1 -p ai-cli-utils`. With `TMUX` set, `_get_current_iterm_session_id()` returns `aaa` (stale parent GUID) instead of `bbb` (correct current pane). New session `c-ai-cli-1` is initialized with `aaa`; all `set-iterm2-name` calls target the wrong pane.

**Correct source:** `os.environ.get("ITERM_SESSION_ID")` — the shell env value set by iTerm2 integration at pane creation. This is always current for the physical pane running `ai c N`, regardless of which tmux session the shell is attached to.

### Fix (2026-04-27, AI-CLI-59) — DEPLOYED

**Part A: Fix GUID resolution** (`3076d60`)
- Simplified `_get_current_iterm_session_id()` to always return shell env `ITERM_SESSION_ID`. Removed `tmux show-environment` branch entirely.
- 9 tests updated/added in `tests/test_iterm2.py`.
- Note: `_live_iterm_id()` in the bash session script still correctly uses `tmux show-environment` because the re-attach path explicitly calls `tmux set-environment` to write the current pane's GUID into the session env before attaching.

**Part B: Session script hot-reload**
- `src/ai_cli/main.py`: script written to stable deterministic path `~/.local/state/ai-cli-utils/sessions/<session_id>.sh` on every `ai c` call (new session and re-attach). Removed `NamedTemporaryFile` usage.
- `src/ai_cli/session_script.py`:
  - Removed self-delete line (stable file must persist).
  - Added `AI_SESSION_STARTED` guard at startup: if `tmux show-environment AI_SESSION_STARTED` is `1`, set `first_run=false` to skip handoff drain, fleet wait, session-broker.
  - Added `_script_stable_path` and `_script_start_mtime` vars at startup.
  - Added mtime check at top of each `while true` iteration: if stable file mtime changed, `exec zsh "$_script_stable_path"`.
  - After `first_run=false`: `tmux set-environment AI_SESSION_STARTED 1` so subsequent `exec`s skip first-run setup.
- 6 tests added in `tests/test_cli.py` (`TestGetEngineScript` + `TestCliSessionStablePath`), 1 test updated in `tests/test_iterm2.py`.

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
