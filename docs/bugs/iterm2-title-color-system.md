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
- [Bug 11 — Dynamic Profiles "invalid JSON" popup on session start](#bug-11--dynamic-profiles-invalid-json-popup-on-session-start)
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

### Fix Round 2 — Root cause (REOPENED 2026-04-26) — INCORRECT EXPLANATION

The Round 2 root cause explanation was wrong. It described a scenario where `ai c N` is launched from inside an existing tmux session. This can't happen — the tool warns and stops when run inside tmux. The Round 1 code and Round 2 code are functionally identical for this user (since `TMUX` is never set when `ai c N` runs). The bug was not fixed.

**What was shipped in Round 2 (`3076d60`, `5719b45`):**
- Part A: Simplified `_get_current_iterm_session_id()` to always return shell-env `ITERM_SESSION_ID` (removed dead `tmux show-environment` branch). Correct implementation but didn't fix the bug.
- Part B: Session script hot-reload — stable script path, mtime-based exec reload, `AI_SESSION_STARTED` guard. Unrelated to the session name bug; ships as a separate improvement.

### Fix Round 3 — Confirmed root cause (REOPENED 2026-04-27)

**Observed state:**
```
c-ai-cli-1: ITERM_SESSION_ID=w0t0p15:C37C7927...  ← correct owner
c-hm-1:     ITERM_SESSION_ID=w0t0p15:C37C7927...  ← stale
g-myproject-1: ITERM_SESSION_ID=w0t0p15:C37C7927... ← stale
g-sw-1:     ITERM_SESSION_ID=w0t0p15:C37C7927...  ← stale
```text

**Root cause:** Multiple tmux sessions accumulate the same `ITERM_SESSION_ID` GUID in their environments. This happens because `_do_session_launch` writes the current pane's GUID into the target session's tmux env (via `_iterm_env_flags` on new-session, or `tmux set-environment` on re-attach) but never removes it from other sessions that already hold the same GUID. Over time, every session that was ever attached from the same physical pane retains that pane's GUID forever.

**Why this causes clobbering:** On each CC restart, every session's `while true` loop calls `_iterm2_fleet_setup` → `_live_iterm_id()` → `ai internal set-iterm2-name <shared-guid> <session-name>`. All sessions sharing the GUID write to the same pane. Last writer wins. The pane name oscillates between session names depending on which session restarted CC most recently.

**Fix:** When `_do_session_launch` claims a GUID for a session, evict that GUID from all other tmux sessions by running `tmux set-environment -t <other> -u ITERM_SESSION_ID` for any session whose stored GUID matches.

**Files to change:**
- `src/ai_cli/main.py` — add `_evict_iterm2_guid(guid, owner_session)` helper; call it after writing GUID to new session env (`_iterm_env_flags`) and after `tmux set-environment` on re-attach
- `tests/test_iterm2.py` — tests: eviction clears stale sessions, correct owner is unaffected, no-op when no other session has GUID

**Round 3 shipped:** `_evict_iterm2_guid` implemented and tested. Evicts GUID from all other sessions on GUID claim. 6 tests pass.

### Fix Round 4 — Regression (REOPENED 2026-04-29)

**Observed state after Round 3 ship:**
```
c-ai-cli-1: shell env ITERM_SESSION_ID=w0t0p15:C37C7927... (correct physical pane)
c-ai-cli-1: tmux env — ITERM_SESSION_ID unset (evicted!)
c-hm-1:     tmux env ITERM_SESSION_ID=w0t0p15:C37C7927... (stale, holds the GUID)
```

**Root cause:** `_evict_iterm2_guid` is over-aggressive. When a new CC session starts in the same physical pane (same GUID), it evicts the GUID from sessions that still have active tmux clients attached. A session with active clients is currently visible and legitimately owns the GUID — evicting it causes it to lose its pane title on the next CC restart loop.

**Fix:** Before evicting a session, check `tmux list-clients -t <session>`. If the session has any active clients (non-empty output), skip the eviction — it owns the pane.

**Files changed:**
- `src/ai_cli/iterm2.py` — `_evict_iterm2_guid`: added `tmux list-clients -t session` check; skip eviction if clients present
- `tests/test_iterm2.py` — updated `_make_fake_run` to support `sessions_with_clients` parameter; new test `test_when_stale_session_has_active_clients_then_not_evicted`

**Shipped:** 2026-04-29 (AI-CLI-59)

### Fix Round 5 — tty-based resolution (FINAL, 2026-07-02)

**Why reopened:** Rounds 1–4 all patched a fundamentally racy design — the pane
GUID was tracked in two places (shell env + tmux session env) and reconciled at
*launch time* by one-directional eviction. Collisions that form *between*
launches were never healed. Observed live: `c-sw-3` (attached, pane `w0t0p0`) and
`c-hm-1` (detached) both holding `w0t0p0:6A6AC15E…` — because `c-hm-1` occupied
that physical pane after `c-sw-3`'s last launch, so no eviction ever ran. On the
next CC restart either session's `set-iterm2-name` would relabel the other's
pane. This is the same failure class as Rounds 2–4, and eviction cannot close it
(it only runs on launch).

**Root-cause reframing:** The GUID is the wrong key. A stored GUID drifts, is
inherited across nested launches, and is shared when a session moves panes. The
physically-correct key is the **client tty** — the OS assigns each live pane
exactly one controlling terminal, so it can never collide.

**Fix (retires the GUID approach entirely):** Resolve the target pane *live, at
set-name time*, by the tmux session's client tty:
`tmux list-clients -t <session>` → tty → the iTerm2 session whose `tty` matches →
`set name`. No stored GUID, no env propagation, no eviction, no reconciliation.
Display name keeps the engine prefix (`c-sw-3` / `g-…`) so Claude vs Gemini
sessions stay distinguishable at a glance. (Corrected 2026-07-03 — an interim
"strip to `sw-3`" change was reverted per user preference; the prefix is load-bearing.)

**Files changed:**
- `src/ai_cli/iterm2.py` — replaced `_set_iterm2_name_applescript` (matched
  `unique id`) with `_set_iterm2_name_by_tty` (matches `tty of s`); added
  `_iterm_pane_tty_for_tmux_session` (session → client tty) and
  `_current_pane_tty` (launcher's own tty). **Deleted** `_evict_iterm2_guid` and
  `_get_current_iterm_session_id`.
- `src/ai_cli/main.py` — `set-iterm2-name` handler now takes `<tmux_session|tty>`
  and resolves tty; removed ITERM_SESSION_ID env-propagation + eviction from the
  launch/re-attach path (LC_TERMINAL/TERM_PROGRAM still propagated).
- `src/ai_cli/session_script.py` — new shared `_iterm2_rename` helper renames via
  `set-iterm2-name "$tmux_session"`; removed `_live_iterm_id`; callers now display
  `$ai_name` (clean) instead of `$tmux_session`.
- `tests/test_iterm2.py`, `tests/test_session_launch_integration.py` — reworked
  the GUID-contract tests to the tty contract.

**Verified live:** every attached session's `client_tty` maps 1:1 to one iTerm2
session tty; renaming `c-sw-3`'s pane by resolved tty (`/dev/ttys000`) set and
read back `sw-3` via the real `ai internal set-iterm2-name` path.

**Shipped:** 2026-07-02 (AI-CLI-59, final)

---

## Bug 11 — Dynamic Profiles "invalid JSON" popup on session start

### Symptom

iTerm2 shows a popup: **"Dynamic Profiles file contains invalid JSON"** for `ai-cli-session-<name>.json` immediately after or during `ai c N` session launch. The file on disk is valid JSON when inspected after the fact. Reported 2026-04-29 with the following logs:

```
[2026-04-29 14:21:19] Error in /Users/sergeiwallace/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-session-sw-1.json: Dynamic Profiles file contains invalid JSON
[2026-04-29 14:21:19] Error loading dynamic profiles: (null)
```

### Root cause

`generate_dynamic_profile()` in `icon_generator.py` used `Path.write_text()` (non-atomic: open → write → close). iTerm2 monitors the `DynamicProfiles/` directory via FSEvents and reads any changed file immediately. If iTerm2's FSEvents callback fires while the write is still in progress — the file descriptor is open but `write()` has not yet returned and `close()` has not been called — iTerm2 reads a partial or empty file and fails to parse it as JSON.

This is a race condition inherent to non-atomic file writes on macOS FSEvents-monitored directories. The file is valid JSON once the write completes, but that's after iTerm2 has already seen the intermediate state.

A secondary issue appears in the logs: hundreds of `[DynamicProfiles] Couldn't load /Users/sergeiwallace/Library/Application Support/iTerm2/DynamicProfiles/ai-core-profiles.json — no such file or directory` entries. This is a stale reference in iTerm2's preferences from before the ai-core→ai-core rename. It is a separate iTerm2 configuration issue (not a code bug) — remove the stale entry by deleting the `ai-core-profiles.json` path from iTerm2 preferences or by creating a redirect file.

### Fix (2026-04-29, AI-CLI-84)

Replaced the direct `write_text()` call in `generate_dynamic_profile()` with an atomic write:

```python
# Before (non-atomic):
out_path.write_text(json.dumps(data, indent=2))

# After (atomic):
with tempfile.NamedTemporaryFile(mode="w", dir=out_path.parent, suffix=".json.tmp", delete=False) as tmp:
    tmp.write(json.dumps(data, indent=2))
    tmp_path = tmp.name
os.replace(tmp_path, out_path)
```

`os.replace()` is a POSIX-atomic rename: iTerm2's FSEvents watcher either sees the old complete file or the new complete file — never a partial write. The temp file is written in the same directory as the target to guarantee both are on the same filesystem (required for rename atomicity).

**File changed:** `src/ai_cli/icon_generator.py` — `generate_dynamic_profile()`

**Shipped:** 2026-04-29 (AI-CLI-84)

### Reopened (2026-07-27) — the same symptom class persisted for 3 months

**Symptom:** the user's iTerm2 Script Console log (7/12–7/27) still showed all three
error strings recurring, dozens of times, well after AI-CLI-84 shipped:

```
Could not read Dynamic Profile from file .../tmpXXXXXXXX.json.tmp: The file
"tmpXXXXXXXX.json.tmp" couldn't be opened because there is no such file.
Dynamic Profiles file .../tmpXXXXXXXX.json.tmp contains invalid JSON: The data
couldn't be read because it isn't in the correct format.
Two dynamic profiles have the same Guid: ai-cli-sw-1
```

**Root cause (Round 2 — confirmed against iTerm2's actual shipped source, not
inferred):** Fetched `sources/Settings/Profiles/iTermDynamicProfileManager.m` from
`gnachman/iTerm2` on GitHub. `reallyReloadDynamicProfiles` fires on **any** watched
filesystem event ("Path watcher noticed a change") and, on every fire, re-enumerates
**every file in the DynamicProfiles directory** via `[fileManager
enumeratorAtPath:path]`, skipping only dotfiles (`hasPrefix:@"."`) and GNU-style
backup files (`hasSuffix:@"~"`). **There is no `.json` extension filter and no
`.tmp` exclusion** — every other file, regardless of suffix, is passed to
`loadDynamicProfilesFromFile:intoArray:guids:` and parsed as a candidate profile.
That method's `guids` set is shared across every file processed in one reload pass;
a duplicate is reported the instant two files in the same pass share a `Guid`.

AI-CLI-84's fix wrote the atomic-write temp file with
`tempfile.NamedTemporaryFile(dir=out_path.parent, suffix=".json.tmp")` — i.e.
**inside the exact directory iTerm2 enumerates**. It correctly eliminated
partial-content reads of the *final* filename, but the temp file itself is a new,
independent artifact in the watched directory that iTerm2's reload can now observe
and attempt to parse:

- **"no such file"** — the reload's directory listing captures the temp filename,
  but by the time `dataWithContentsOfFile:` actually opens it (after processing
  other alphabetically-earlier files in the same pass), `os.replace()` has already
  renamed it away → `ENOENT`.
- **"contains invalid JSON"** — `tempfile.NamedTemporaryFile()` creates the file
  (0 bytes) at construction time, before `tmp.write()`'s buffered content is ever
  flushed to disk at `close()`. A reload that fires in that window reads a 0-byte
  file → `NSJSONSerialization` fails to parse empty data.
- **"Two dynamic profiles have the same Guid"** — the temp file's JSON content
  already carries the deterministic target `Guid` (`ai-cli-{session_name}`) before
  the rename lands. If a reload fires while both the temp file and the final file
  it's about to replace (e.g. during `ai color`'s cleanup-then-regenerate cycle, or
  simply a stale file from a previous run not yet overwritten) are independently
  readable and valid in the same pass, both get added to `newProfiles` under the
  same `Guid` — the second one processed triggers the duplicate report.

This is one causal mechanism explaining all three symptom strings — confirmed by
matching each `reportError:` call site in the actual shipped source against the
observed log lines character-for-character, not inferred from behavior alone.
AI-CLI-84's underlying assumption — that atomicity requires the temp file to share
the target's directory — is the reopening cause: POSIX rename atomicity requires
only the same filesystem (`st_dev`), not the same parent directory.

**Fix Round 2 (2026-07-27, AIH-478):** Stage the temp file in
`_dynamic_profile_dir().parent` (`~/Library/Application Support/iTerm2/`) instead of
inside `DynamicProfiles/` itself. Confirmed via the same source read that iTerm2's
`pathsToWatch` only ever adds the `DynamicProfiles` directory (and any per-file
symlink targets inside it) to its watched folder set — never its parent — so a
temp file staged there can never be observed by the reload. The parent is
guaranteed to be the same filesystem as `DynamicProfiles/` (it *is* the literal
parent), so `os.replace()` remains a single atomic cross-directory rename.

**Regression test:**
`tests/test_icon_generator.py::TestGenerateDynamicProfile::test_temp_write_never_visible_inside_the_watched_directory`
— a real background thread polls `os.listdir()` on the watched directory for the
full duration of the call (no mock at the filesystem boundary), while a
deterministic 50ms delay is injected into the real `os.replace()` call to make the
race window reliably observable regardless of machine speed. Confirmed RED against
the pre-fix code (observed the stray `tmpXXXXXXXX.json.tmp` inside the watched
dir), confirmed GREEN after the fix, confirmed RED again after reverting only the
production change (test left frozen).

**Verification:** focused test passes; full `tests/test_icon_generator.py` (55
tests) passes; full repo suite (1902 tests) passes; `ruff check` / `ruff format
--check` clean on the changed files (pre-existing unrelated lint findings in this
file were confirmed present on the unmodified code too, not introduced by this
fix). Live end-to-end exercise against the real
`~/Library/Application Support/iTerm2/DynamicProfiles/` directory (unmocked, no
injected delay) confirmed the watched directory only ever showed the final
canonical filename during a real `generate_dynamic_profile()` call, with correct
cleanup after.

**File changed:** `src/ai_cli/icon_generator.py` — `generate_dynamic_profile()`

**Tracked:** `AIH-478` (ai-harness Beads store, per explicit user request — this is
an ai-cli-utils defect, tracked cross-repo).

**Shipped:** 2026-07-27 (AIH-478)

---

## Diagnosis Approach

> Note: This section documents the proposed diagnostic direction. It will be replaced with root cause findings and confirmed fixes once the design doc is reviewed and approved.

### Color collision (Bug 1)

The core issue is that `idx = (num - 1) % 12` uses only the session number suffix, which is not unique across projects. The fix likely involves querying what colors are already in use in the current iTerm2 window and assigning a non-colliding slot. This requires either reading `ITERM_SESSION_ID` to enumerate same-window panes or maintaining a local state file of window → active colors.

### Remote OSC sequence filtering (Bug 2)

Mosh intercepts and filters `\033]1337;` (iTerm2-proprietary) sequences — so `SetProfile` and `SetColors=tab=` never reach iTerm2 from remote sessions. Mosh also prepends `[mosh]` to all OSC 0 window titles it receives. The fix for remote sessions likely requires running the profile/color setup on the local side before the mosh connection is established, or an iTerm2 Automatic Profile Switching rule keyed on the remote hostname.

### Gemini title not applied (Bug 3)

The `SetProfile=GeminiCLI` OSC sequence applies correctly but the OSC 0 title appears to be overridden or suppressed. Likely candidates: the GeminiCLI iTerm2 profile has a fixed "Tab title" override setting, or the `session_name` argument to `_emit_iterm2_profile_setup` is empty when gemini sessions launch, or the title is being reset by something (tmux, gemini CLI itself) after it's set.

### Gemini chats directory mismatch (Bugs 4, 5, 6)

Gemini derives its chats storage path from the current working directory at launch time. When launched from the wrong directory, the UUID stored in the session map points to a chats directory that gemini doesn't look in. The fix requires ensuring gemini always launches (or at minimum looks for sessions) in the target project's directory. The `cd {worktree_path}` in the bash script should handle this when a worktree exists, but may fail silently when it doesn't or when the path is wrong.

### Icon color variety (Bug 7)

Adding more color variants requires either: (a) additional iTerm2 Dynamic Profile entries with differently-colored logo PNGs, or (b) a mechanism to dynamically colorize the icon at emit time. The feasibility of option (b) needs investigation — iTerm2's badge system or inline image protocol may allow this without requiring static profile proliferation.
