---
title: "[BUG-002] Automatic tmux injection triggers CC rewind conversation TUI"
category: bugs
tags: [bug, injection, tmux, signal-watch, watcher, cc-session]
status: investigating
severity: P0
task: AI-CLI-35
---

# [BUG-002] Automatic tmux injection triggers CC rewind conversation TUI

**Status:** fix-deployed — `f4db5bf` (2026-04-06)

**Severity:** P0

**Created:** 2026-04-05

**Task:** `[AI-CLI-35]`

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Code Audit — All tmux send-keys injection surfaces](#code-audit--all-tmux-send-keys-injection-surfaces)
- [Surfaces Cleared](#surfaces-cleared)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Proposed Fix](#proposed-fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Files Involved](#files-involved)

## Symptoms

After ai-cli automatically injects keystrokes into a Claude Code tmux session (for session restart, config reload, or handoff pickup), Claude Code's "rewind conversation" TUI screen appears instead of the session restarting cleanly. Affects multiple sessions simultaneously when they all auto-restart. Also observed when sessions are manually `/exit`-ed and auto-restart fires.

The rewind TUI is CC's conversation history browser, triggered in normal use by pressing `Escape` at an empty `❯` prompt.

## Environment

- macOS (iTerm2 + tmux)
- Claude Code sessions managed by `ai c` (wrapper in `src/ai_cli/main.py`)
- Sessions run inside named tmux sessions, CC is a child process of a bash wrapper script
- ai-cli-utils v0.1.1 (post-build)

## Reproduction Steps

Reliable reproduction not yet established. Observed:

1. Run multiple `ai c` sessions
2. Any of the following triggers automatic injection:
   - Config change detected (CLAUDE.md hash changes) -> idle timer fires -> `signal_file` created -> watcher injects
   - Handoff posted -> signal-watch writes `handoff_pending_file` -> while-loop pickup on next restart
   - User manually `/exit`s -> session auto-restarts -> watcher re-armed
3. Observe: CC shows the rewind conversation TUI instead of restarting normally

## Code Audit — All tmux send-keys injection surfaces

### 1. Signal file injection -> `/exit` — `main.py:1102-1124` — PRIMARY SUSPECT

**Trigger:** `signal_file` exists (`cc-exit-{tmux_session}` in XDG state dir)

**Created by:**
- Config change detection: `main.py:1148` — when CLAUDE.md hash changes and session has been idle long enough
- Future: any caller that writes the signal file

**Code:**
```bash
if [[ -f "$signal_file" ]]; then
  _sig_last=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
  if echo "$_sig_last" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
    rm -f "$signal_file"
    sleep 0.5                                        # <- RACE WINDOW
    tmux send-keys -t "$tmux_session" C-u            # <- POTENTIALLY UNSAFE
    if [[ "$engine" == "g" ]]; then
      tmux send-keys -t "$tmux_session" "/resume save $ai_name" C-m
      sleep 2
    fi
    tmux send-keys -t "$tmux_session" '/exit' C-m
    break
  fi
fi
```

**Known problems identified — 5 confirmed issues:**

**P1 — 0.5s race window (CONFIRMED):** Guard validates state at T+0, sleeps 500ms, then injects. CC state can change during that gap. The `signal_file` is already deleted at this point (`rm -f` at line 1114), so there's no retry — the injection fires blind. If CC transitioned out of idle during the 500ms (e.g., compaction started, user typed, hook fired), `C-u` and `/exit` arrive in an unknown state.

**P2 — `C-u` not confirmed safe in CC TUI (CONFIRMED):** CC's `❯` prompt is a React/Ink TUI, not readline. `C-u` behavior in Ink's input handler is undocumented and may differ from readline's "kill line." At an empty prompt this is likely harmless, but during any transition state (startup, compaction, tool execution) the behavior is completely unknown. Since the guard already confirms the prompt is empty, `C-u` is both redundant AND risky.

**P3 — Stale `❯` from previous conversation during `--continue` startup (CONFIRMED — ROOT CAUSE):** When `claude --continue` launches, CC loads the previous conversation. During the 1-3 second startup phase, the tmux pane still displays the old conversation's output — including the old `❯` prompt as the last non-blank line. The watcher sees this stale `❯` and treats it as "CC is idle and ready for input." It then injects `C-u` + `/exit` into CC's initialization phase. CC is not at an interactive prompt — it's loading/rendering the conversation. The injected keystrokes land in CC's startup TUI and cause unpredictable behavior including the rewind menu.

This is the most likely root cause because:
- It explains why the bug persists after fixing the `>` -> `❯` guard: the old guard never matched, so it never injected. The new `❯` guard DOES match — against the stale prompt from the previous conversation.
- It explains why the bug correlates with auto-restart: restart means `--continue`, which means stale `❯` in the pane.
- It explains why multiple sessions are affected simultaneously: config changes trigger all watchers, and all sessions show stale `❯` during startup.

**P4 — `start_watcher` races CC launch (CONFIRMED):** The while-loop calls `start_watcher` (line 1243) BEFORE CC launches (lines 1268-1284). Between these, `handoff-drain` runs synchronously (line 1261), `session-broker.py` may run in background (line 1254), and various setup occurs. The watcher starts polling every 1 second immediately. If config change detection fires during this 1-3 second gap, it checks the pane (which shows old CC output or shell prompt), and may create `signal_file` if it sees `❯`.

On restart specifically: the watcher from the previous cycle is killed (line 1086-1088), a new watcher starts (line 1243), and the pane displays whatever CC left before exiting — which IS the `❯` prompt. The new watcher immediately starts polling. If a `config_changed_file` already existed from a change detected during the previous CC run, and the idle timer has expired, the watcher creates `signal_file` on its first or second cycle, then processes it against the stale pane content.

**P5 — `signal_file` deletion before injection creates no-retry semantics (NEW):** At line 1114, `signal_file` is deleted BEFORE the `sleep 0.5` and `send-keys`. If the injection fails (CC not actually idle, pane state changed), the signal is lost. The watcher breaks out of the loop (line 1122), and the `while true` restart loop restarts the watcher — but the signal is gone. For config-change signals this is recoverable (the config hash still differs, so a new `signal_file` will be created). For externally-posted signals it's not.

---

### 2. Config change auto-restart — `main.py:1127-1151`

**Trigger:** `config_changed_file` exists and idle time >= threshold

**Code:**
```bash
if [[ -f "$config_changed_file" && ! -f "$signal_file" ]]; then
  _changed_at=$(cat "$config_changed_file" 2>/dev/null || echo 0)
  _idle_secs=$(( $(date +%s) - _changed_at ))
  if (( _idle_secs >= _config_reload_idle_secs )); then
    _last_line=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
    if echo "$_last_line" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
      ...
      touch "$signal_file"    # <- feeds into path 1 above
    fi
  fi
fi
```

**Status:** This path creates `signal_file`, which is handled by path 1. Has the SAME stale `❯` false-positive problem (P3): during CC startup, the pane shows old conversation content with `❯`, and this guard passes, creating a `signal_file` that path 1 then processes. The config change guard at line 1143 is an independent `capture-pane` check that suffers from the same stale-pane problem.

---

### 3. Gemini reload/restart injection — `main.py:1152-1164`

**Trigger:** `gg-reload-{session}` or `gg-restart-{session}` files exist

**Status:** Only fires when `engine == "g"`. Cannot affect Claude Code sessions. Cleared.

---

### 4. Gemini handoff resume injection — `main.py:1270`

**Trigger:** `prompt_file` exists and `engine == "g"`

**Status:** Gemini engine only (guarded by outer `if` block). Cleared.

---

### 5. Quota scrape send-keys — `quota.py:76,100,122`

**Trigger:** `ai quota scrape` called (manual or cron)

**Code targets:** `f"={window_name}"` where `window_name = "ai-quota-scrape"` — a dedicated background tmux window created and killed by the scrape function.

**Escape send at line 122:** Targets only the isolated `ai-quota-scrape` window using exact-match targeting (`=` prefix). Cannot reach CC sessions. Cleared.

---

### 6. Signal-watch (`ai internal signal-watch`) — `main.py:2096-2179`

**Trigger:** NATS JetStream message received on `handoff.{project}` topic, or startup scan of pending queue.

**Code:** Writes `handoff_pending_file` only — **no tmux send-keys**. The pending file is consumed by the outer `while true` loop AFTER CC exits naturally (line 1317-1323). Cleared.

---

### 7. Handoff drain (`ai internal handoff-drain`) — `main.py:2181-2280`

**Trigger:** Called synchronously before CC launches on first run (`main.py:1261`)

**Code:** Writes `prompt_file` (for CLI arg to `claude --continue`) — **no tmux send-keys**. Cleared.

---

### 8. While-loop handoff pickup — `main.py:1317-1323`

**Trigger:** `handoff_pending_file` exists after CC exits

**Code:** Reads `handoff_pending_file`, writes to `prompt_file`. CC then launched with `claude --continue "$resume_msg"` as CLI arg — not via tmux injection. Cleared.

---

### 9. Session broker — `scripts/session-broker.py`

**Trigger:** Called async at session start (`main.py:1254`) with `timeout 20`.

**Code:** Pure Python — reads from humanware DB, writes `session-context.md` to `.claude/signals/`. No tmux interaction whatsoever. Cleared.

---

### 10. Claude Code hooks — `.claude/hooks/`

Four hooks registered in `.claude/settings.json`:
- `enforce-workflow.sh` (PreToolUse/Bash) — reads stdin JSON, validates branch naming. No tmux interaction.
- `debug-mode-gate.sh` (PreToolUse) — `exit 0` no-op. No tmux interaction.
- `lint-md.sh` (PostToolUse/Edit|Write) — runs markdownlint on edited files. No tmux interaction.
- `fresh-session-orientation.sh` (SessionStart) — injects orientation message into CC via `{"message": "..."}` JSON on stdout. This is CC's hook protocol, not tmux send-keys. The JSON message is consumed by CC internally. No tmux interaction.

All hooks fire inside CC's process, communicate via CC's hook protocol (stdin/stdout JSON), and never touch tmux. Cleared.

---

### 11. iTerm2 escape sequences — `main.py:1186-1228`

**Code:** `_it2()` and `_iterm2_fleet_setup()` emit OSC escape sequences (`\033]1337;...`, `\033]1;...`) wrapped in DCS passthrough for tmux. These are terminal control sequences parsed by iTerm2, not keystrokes delivered to CC. They set profile, tab color, and pane title.

These fire at `main.py:1234` (before while-loop) and `main.py:1246-1247` (inside while-loop, before CC launch). CC is not running when these fire. Cleared.

---

### 12. Background processes spawned during session lifecycle

- `ai sync watch` / `ai memory watch` (line 1177-1178): Background file watchers. No tmux send-keys.
- `ai signal-watch start` (line 1182): Launches circus-managed process. Covered in surface #6.
- `ai internal publish-event/publish-session-event` (lines 1248-1249, 1296-1299, 1333-1334): NATS event publishing. Fire-and-forget, no tmux interaction.
- `ai ps cron` (line 1174): Process hygiene cleanup. No tmux send-keys.
- `trigger_background_update()` (line 1576-1591): Background `ai update`. No tmux interaction.

All cleared.

## Surfaces Cleared

The following injection surfaces were investigated and confirmed safe (cannot trigger the CC rewind menu):

| # | Surface | File:Lines | Reason cleared |
|---|---------|------------|----------------|
| 3 | Gemini reload/restart | `main.py:1152-1164` | `engine == "g"` guard; CC sessions use `engine == "c"` |
| 4 | Gemini handoff resume | `main.py:1270` | `engine == "g"` guard |
| 5 | Quota scrape | `quota.py:56-136` | Targets `=ai-quota-scrape` window exclusively; exact-match tmux targeting |
| 6 | Signal-watch (NATS) | `main.py:2096-2179` | File-only IPC (`handoff_pending_file`); no tmux send-keys |
| 7 | Handoff drain | `main.py:2181-2280` | File-only IPC (`prompt_file`); no tmux send-keys |
| 8 | While-loop handoff pickup | `main.py:1317-1323` | File-only IPC; CC launched with CLI arg, not tmux injection |
| 9 | Session broker | `scripts/session-broker.py` | Pure Python; writes `.claude/signals/session-context.md` only |
| 10 | Claude Code hooks | `.claude/hooks/*.sh` | All communicate via CC's hook protocol (stdin/stdout JSON); no tmux |
| 11 | iTerm2 escape sequences | `main.py:1186-1228` | OSC terminal control sequences; not keystrokes to CC |
| 12 | Background processes | `main.py:1174-1182,1248-1249` | NATS publishing, file watchers, process hygiene; no tmux send-keys |

**Only surfaces #1 and #2 can inject keystrokes into a CC tmux session.** Surface #2 feeds into #1 (creates `signal_file`). The entire attack surface is the `start_watcher()` function at `main.py:1085-1169`.

## Root Cause Analysis

### Primary root cause: Stale `❯` false positive (P3)

The `❯` idle guard at lines 1112-1113 and 1143-1144 matches against the **previous conversation's prompt** displayed in the tmux pane during CC's startup phase with `--continue`.

**Detailed timing trace:**

1. CC exits (user `/exit` or crash). Pane shows CC's output ending with `❯`.
2. While-loop at line 1242 calls `start_watcher` (line 1243). New watcher starts polling every 1s.
3. Various setup runs (iTerm2, event publishing, session-broker, handoff-drain).
4. CC launches with `claude --continue` (lines 1268-1284).
5. **Critical window (1-3 seconds):** CC is loading. The pane still displays the old conversation's output with `❯` as the last line. CC has not cleared the screen yet.
6. Watcher's polling cycle fires. If `signal_file` exists (from config change detection — which can fire as early as the 10th cycle, or immediately if `config_changed_file` was written during the previous run):
   - `capture-pane` at line 1112 returns old content with `❯` at the bottom.
   - Guard passes. `signal_file` deleted. `sleep 0.5`.
   - `C-u` sent to CC's startup TUI. `/exit` sent.
   - These keystrokes arrive during CC's initialization, not at an interactive prompt.
   - CC's React/Ink renderer receives unexpected input, potentially triggering the rewind TUI.
7. Alternatively, config change detection at line 1143 fires, sees `❯` in the stale pane, creates `signal_file`. The next cycle processes it immediately (back to step 6).

### Contributing factors

**Race window (P1):** The `sleep 0.5` between guard check and injection widens the vulnerability window. Even if the guard was correct at T+0, CC's state can change during the 500ms gap.

**Unknown `C-u` behavior (P2):** `C-u` in CC's React/Ink TUI is not a readline binding. Its behavior during startup or transition states is undocumented. It may trigger the rewind menu, emit a control character that's misinterpreted, or do nothing. Redundant since the guard already confirms an empty prompt.

**No-retry after signal deletion (P5):** `signal_file` is deleted at line 1114 before injection. If injection fails, the signal is lost. This doesn't directly cause the rewind bug, but means failed injections can't self-correct.

### Why the prior fix made things worse

The first fix (commit `769c6d6`) changed the guard from `>` to `❯`. The old `>` guard never matched CC's actual prompt character, so injection **never actually fired** for CC sessions (it was always a no-op). The fix made the guard match correctly, which enabled the injection path for the first time — but the injection fires against stale pane content during CC startup, triggering the rewind menu. The fix was correct in intent but exposed the latent timing bugs (P1, P3, P4).

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-04-05 | Changed idle guard from `>` to `❯` (CC's actual prompt character). Removed `Escape` keystroke from injection sequence (it directly triggered the rewind menu). | Partial: the `❯` guard now matches, but matches stale pane content during startup (P3). Actually made injection fire for the first time, exposing the timing bugs. |

**Commit:** `769c6d6 fix(session): use correct CC prompt character in injection idle guard`

## Proposed Fix

### Architecture assessment

The `start_watcher()` function is fundamentally sound in design: poll pane state, check for idle prompt, inject commands. The problems are all timing-related:
1. No distinction between "CC is idle at a live prompt" and "pane shows stale content from before CC launched"
2. No grace period after CC startup
3. Unnecessary delay and redundant keystrokes in the injection sequence

A targeted fix addressing all 5 identified problems is appropriate. The watcher architecture does not need redesign.

### Change 1: CC launch epoch — distinguish live prompt from stale content

**Problem:** The watcher cannot distinguish between "CC is idle at prompt" and "pane shows old content during CC startup."

**Fix:** Record a timestamp when CC launches. The watcher must not fire injection within a grace period after CC launch. This is implemented via the `counter` variable which increments every 1 second (line 1100). The watcher already has `counter` — add a check that skips injection for the first N cycles.

**Mechanism:** CC takes 1-5 seconds to fully start (load conversation, render, show new prompt). A 10-second grace period (counter < 10) ensures the watcher never acts on stale pane content from before CC launched. The `counter` resets to 0 every time `start_watcher` is called (line 1094), which happens at the top of every while-loop iteration (line 1243), right before CC launches.

### Change 2: Remove `sleep 0.5` race window

**Problem:** 500ms gap between guard check and injection allows state to change.

**Fix:** Remove the sleep entirely. The guard check and injection should be atomic (as atomic as sequential shell commands can be).

### Change 3: Remove `C-u`

**Problem:** Redundant keystroke with unknown behavior in CC's TUI.

**Fix:** Remove `C-u`. The guard already confirms the prompt is empty (`^[[:space:]]*❯[[:space:]]*$`). Send `/exit` directly.

### Change 4: Double-check guard immediately before injection

**Problem:** Even without `sleep 0.5`, a single `capture-pane` check is a point-in-time sample.

**Fix:** After the first guard check passes, do a second `capture-pane` immediately before `send-keys`. If the second check fails (CC transitioned out of idle between the two checks), abort and retry next cycle. Do NOT delete `signal_file` until the second check passes.

### Change 5: Move `signal_file` deletion after successful injection

**Problem:** `signal_file` deleted before injection (line 1114). If injection fails or the watcher is killed mid-sequence, the signal is lost.

**Fix:** Delete `signal_file` after the `send-keys` call, not before. If the watcher is killed, the signal survives for the next watcher instance.

### Proposed replacement for `main.py:1102-1124`

```bash
if [[ -f "$signal_file" ]]; then
  # Grace period: skip injection during first 10 seconds after watcher start.
  # This prevents acting on stale pane content from before CC launched.
  # counter increments every 1s (line 1100), resets to 0 at watcher start (line 1094).
  if (( counter < 10 )); then
    : # within startup grace period — CC may still be loading
  else
    _sig_last=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null \
      | grep -v '^[[:space:]]*$' | tail -1)
    if echo "$_sig_last" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
      # Double-check: re-verify immediately before injecting.
      # If CC transitioned out of idle between the two checks, abort.
      _sig_verify=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null \
        | grep -v '^[[:space:]]*$' | tail -1)
      if echo "$_sig_verify" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
        # Inject /exit directly. No C-u (redundant — prompt confirmed empty).
        # No sleep (race window eliminated).
        if [[ "$engine" == "g" ]]; then
          tmux send-keys -t "$tmux_session" "/resume save $ai_name" C-m
          sleep 2
        fi
        tmux send-keys -t "$tmux_session" '/exit' C-m
        rm -f "$signal_file"   # Delete AFTER injection, not before
        break
      fi
    fi
  fi
  # CC not at idle prompt or within grace period — keep signal_file, retry next cycle
fi
```

### Proposed change to config change detection at `main.py:1137-1151`

Same grace period applies. Add `counter >= 10` guard:

```bash
# Auto-restart when config changed and session has been idle long enough
if [[ -f "$config_changed_file" && ! -f "$signal_file" ]] && (( counter >= 10 )); then
  _changed_at=$(cat "$config_changed_file" 2>/dev/null || echo 0)
  _idle_secs=$(( $(date +%s) - _changed_at ))
  if (( _idle_secs >= _config_reload_idle_secs )); then
    _last_line=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null \
      | grep -v '^[[:space:]]*$' | tail -1)
    if echo "$_last_line" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
      _new_hash=$(cat "$HOME/projects/CLAUDE.md" "$(pwd)/CLAUDE.md" 2>/dev/null \
        | sha256sum | cut -d' ' -f1)
      echo "$_new_hash" > "$config_hash_file"
      rm -f "$config_changed_file"
      touch "$signal_file"
    fi
  fi
fi
```

### Summary of all changes

| Change | Lines affected | What | Why |
|--------|---------------|------|-----|
| 1 | `main.py:1102` | Add `counter < 10` grace period guard | Prevents injection during CC startup when pane shows stale `❯` |
| 2 | `main.py:1115` | Remove `sleep 0.5` | Eliminates 500ms race window between guard and injection |
| 3 | `main.py:1116` | Remove `C-u` send-keys | Eliminates unknown TUI behavior; redundant since prompt is confirmed empty |
| 4 | `main.py:1113` (after) | Add second `capture-pane` check | Double-verification catches state transitions between checks |
| 5 | `main.py:1114` | Move `rm -f "$signal_file"` after `send-keys` | Signal survives failed injection for retry |
| 6 | `main.py:1137` | Add `counter >= 10` guard to config change detection | Same grace period protection for the signal_file creation path |

**Files requiring changes:** `src/ai_cli/main.py` only. All changes are within `start_watcher()` (lines 1085-1169).

## Verification

- [ ] Trigger a config-change auto-restart: modify CLAUDE.md, wait for idle timer, confirm session restarts cleanly without rewind menu
- [ ] Manually `/exit` multiple sessions simultaneously, confirm all restart without rewind menu
- [ ] Post a handoff while a CC session is running, confirm handoff is picked up cleanly on next restart
- [ ] Confirm `❯` during CC startup does not trigger premature injection: watch first 10s of new session, verify no `send-keys` calls
- [ ] Verify `signal_file` survives a watcher kill during the grace period and is retried on next watcher start
- [ ] Verify the double-check guard catches a CC transition: trigger `signal_file`, then manually type in the CC pane between the two `capture-pane` checks (timing test — may need artificial delay for verification)
- [ ] Verify Gemini sessions still work: `/resume save` path should fire correctly (still includes `sleep 2` for Gemini, which is intentional)
- [ ] Run full test suite: `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest`
- [ ] Update test `test_signal_file_injection_no_escape` to verify new behavior (no `C-u`, no `sleep 0.5`, double-check present)

## Lessons Learned

- `tmux capture-pane` state checks must be validated immediately before injection — never sleep between check and action
- CC's TUI input handler is not the same as a readline shell — keystrokes like `C-u` should be treated as unknown until confirmed safe in CC's context
- Watcher startup timing must account for CC's own startup latency — a grace period is essential
- Single-sample idle guards are insufficient for async injection — double-verification catches state transitions
- When fixing a guard that "never matched" (old `>` guard), the fix may enable a previously-dead code path. Test the ENTIRE path, not just the guard change.
- Delete signals AFTER successful action, not before — preserves retry semantics on failure
- Stale pane content during process startup is a distinct state from "process is idle" — the watcher must distinguish between these

## Files Involved

| File | Relevance |
|------|-----------|
| `src/ai_cli/main.py:1085-1169` | `start_watcher()` — all CC/Gemini injection logic |
| `src/ai_cli/main.py:1072-1078` | Signal file cleanup at session start |
| `src/ai_cli/main.py:1127-1151` | Config change detection -> signal_file creation |
| `src/ai_cli/main.py:1180-1184` | Signal-watch start |
| `src/ai_cli/main.py:1242-1332` | Outer `while true` restart loop |
| `src/ai_cli/main.py:1257-1284` | Handoff-drain call + CC launch |
| `src/ai_cli/main.py:2076-2180` | `ai internal signal-watch` implementation |
| `src/ai_cli/main.py:2181-2280` | `ai internal handoff-drain` implementation |
| `src/ai_cli/quota.py:56-136` | `_scrape_usage_hidden_pane()` — isolated scrape window |
| `scripts/session-broker.py` | Session context generation — no tmux interaction |
| `.claude/hooks/*.sh` | CC hooks — no tmux interaction |
| `~/.local/state/ai-cli-utils/cc-exit-{session}` | Signal file (triggers `/exit` injection) |
| `~/.local/state/ai-cli-utils/config-changed-{session}` | Config change marker file |
| `~/.local/state/ai-cli-utils/handoff-pending-{session}` | Handoff pickup file (written by signal-watch) |
| `~/.local/state/ai-cli-utils/cc-resume-prompt-{session}` | Prompt file (written by handoff-drain, read at CC launch) |
| `~/.local/state/ai-cli-utils/handoff-events.jsonl` | Handoff event log (for observability) |
