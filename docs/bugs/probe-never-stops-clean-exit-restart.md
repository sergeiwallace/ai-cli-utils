---
title: "[AI-CLI-4dil8] A probe's boot policy never also stopped its restart after /exit"
category: bugs
tags: [bug, session, probe, auto-restart, relaunch-policy]
status: fix-deployed
severity: P2
task: AI-CLI-4dil8
---

# [AI-CLI-4dil8] A probe never auto-restarted after a clean exit

**Status:** fix-deployed

**Severity:** P2

**Created:** 2026-11-22

**Task:** `AI-CLI-4dil8`

## Symptoms

A session launched with `ai c -t` (a probe) ran for 60 s, then `/exit` printed only
`Auto-restart stopped. Returning to an interactive shell.` The README says the wrapper
restarts the agent whenever it exits inside tmux, and `[session] auto_restart` was on.
After `ai sessions policy <tmux name> auto`, a second probe restarted as documented. The
exit named no reason.

## Root cause

A probe's run record gets `relaunch: never` with `relaunch_set_by: default`
(`default_relaunch` in `src/ai_cli/session_run_state.py`), so boot relaunch and the
iTerm2 restore skip it. That field is documented as the boot relaunch policy.

The supervisor's `SHELL RETURN policy` block in `src/ai_cli/session_script.py` read the
same field after every agent exit and stopped on any `never`. One field carried two
policies: boot relaunch and restart after an exit. For an ordinary session they agree,
because the automatic default is `auto`. For a probe they part: the automatic `never` was
meant only for boot, and the supervisor read it as "do not restart".

The exit message printed a reason only for `[session] auto_restart = false`, so a stop by
policy said nothing about why.

## Fix

`stops_restart_after_exit(record)` in `src/ai_cli/session_run_state.py` is the one reader of
the boot policy for an exit. Only a `never` someone chose stops the restart: one written
by `ai sessions policy` (`relaunch_set_by: owner`), or an unattributed one from before
`relaunch_set_by` existed. A `never` an automatic writer set keeps the session out of
boot relaunch and restore only, and `[session] auto_restart` decides the restart. Probes
are not special-cased: the rule is about who chose the value. The supervisor's policy
command calls it, and a stop by policy now prints
`not restarting: relaunch policy never (`ai sessions policy NAME auto` restores restarts)`.

`boot_veto` still refuses every `never`, so a probe is still never relaunched at boot.

## Class search

Every reader that could treat the boot policy as a restart policy.

- Query: `grep -rnE "[\"']relaunch[\"']|relaunch=|\.relaunch\b" src/ai_cli`, plus
  `grep -rn relaunch src/ai_cli --include=*.py` filtered of `relaunch_argv`,
  `session_relaunch` and `relaunched`.
- Haystack: every `.py` file under `src/ai_cli/` at base `cec73193`.
- Hits, and what each does:
  - `session_script.py` `_relaunch_policy_command`: the exit restart decision. The one
    instance, fixed.
  - `session_run_state.py` `boot_veto`: boot relaunch, which should refuse `never`.
  - `session_run_state.py` `default_relaunch` and `register`: writers of the default.
  - `session_commands.py` `policy` and `repair-policy`: writers.
  - `session_commands.py` list rows, `session_state_publish.py`, `session_list_view.py`
    and `session_registry.py`: display and publication of the value.
  - `iterm2_restore.py` and `iterm2.py`: the separate `restore.relaunch` settings table,
    not the record field.

## Reproduction and tests

Real entry point: the generated supervisor script, run in an isolated tmux pane by
`tests/test_session_interrupt_shell.py`, with the agent faked at the process boundary.

- `test_given_a_probe_when_agent_exits_cleanly_then_wrapper_restarts_it` (bash, zsh): RED
  on `cec73193`, with the capture ending in
  `Agent exited; restarting in 3s` then `Auto-restart stopped`. Green after the fix.
- `test_given_a_probe_its_owner_set_to_never_when_agent_exits_then_same_pane_returns_to_shell_naming_why`
  (bash, zsh): RED on `cec73193` for the missing reason line. Green after the fix.
- `test_given_policy_changed_to_never_when_engine_exits_then_same_pane_returns_to_shell`
  is unchanged. Its `set_policy` helper now writes `relaunch_set_by: owner`, the write
  `ai sessions policy` makes; before, it left the launch's `default` in place, which no real
  policy change does.
