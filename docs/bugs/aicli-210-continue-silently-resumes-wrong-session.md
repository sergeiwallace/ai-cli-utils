---
title: "ai c silently resumes the wrong (older, unrelated) session when the intended most-recent one is a live background agent"
category: bug
tags: [bug, ai-cli, claude-code, session-resume, continue, background-agent]
status: diagnosed
source: "aido-1-session-2026-08-12"
template_version: "bug-1.0.0"
task: AI-CLI-210
---
<!-- Canonical Jinja source: STUB.md.jinja. This direct-copy stub is retained for consumers that have not migrated to rendering it. -->

# ai c silently resumes the wrong (older, unrelated) session when the intended most-recent one is a live background agent

**Status:** diagnosed — root cause confirmed live; fix not yet implemented.

**Created:** 2026-08-12

**Task:** AI-CLI-210

<!-- doc:region name="summary" kind="replaceable" -->

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Summary

A live interactive `ai c` session (custom title `sw-6`, session id `e708efd4-…`)
was exited by the user intending to relaunch in bypass-permissions mode. It did
not actually terminate — it kept running as a detached background agent. When
the user then ran `ai c 6` to relaunch, `ai c` silently resumed a completely
different, unrelated, older transcript instead (an ad-hoc research conversation
about Israeli-Palestinian negotiations) with **no indication that this had
happened** — no warning, no fallback notice, nothing distinguishing it from a
normal, correct `--continue`. The user spent an extended troubleshooting session
before the actual cause surfaced.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

1. Launch `ai c <n>` and have an active conversation (session A, e.g. custom
   title `sw-6`).
2. Exit the session in a way that leaves it running as a background agent
   rather than terminating it cleanly (exact trigger not yet isolated — observed
   once during a bypass-permissions-mode relaunch flow; not yet reproduced
   deliberately).
3. Confirm session A is still alive: `claude --resume <session-A-id>` returns:
   ```
   Session <id> is currently running as a background agent (bg). Use `claude
   agents` to find and attach to it, or add --fork-session to branch off a copy.
   ```
4. Run `ai c <n>` again (no flags). Expected: resumes session A (the most
   recently active conversation for that cwd). Actual: silently lands on a
   different, older, unrelated transcript for the same cwd — with the launched
   session's custom title re-applied to whichever transcript it picked,
   overwriting any prior manual rename.
5. `ai c <n> -r` does not help recover session A: `-r`/`--resume` in `ai c` is
   an unrelated flag — it does `tmux attach-session -t c-<name>`, and returns
   "No matching session found" once the underlying tmux pane has been closed.
   This is a separate, correctly-working feature that happens to share a name
   with Claude Code's own native resume concept, which is a source of confusion
   in its own right.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

`claude --continue` (which `ai c <n>`'s bare invocation wraps) cannot resume a
session that is already running live. When the actual most-recently-modified
transcript for a cwd is in that state, `ai c`'s continue-resolution logic
appears to silently fall through to the next-most-recent transcript for the
same cwd instead of surfacing the conflict — with zero signal to the user that
a substitution occurred. From the user's perspective this is indistinguishable
from a normal, correct resume: the wrong conversation loads, and the launcher
re-applies the expected custom title (e.g. `sw-6`) onto it, actively erasing the
one piece of metadata (a manually-renamed title) that could have made the mixup
visible.

Two separable problems:

1. **(this repo, `ai c`'s launch/continue-resolution logic, `src/ai_cli/main.py`)**
   No visibility when the most-recently-used transcript for a cwd cannot be
   resumed because it's a currently-running background agent. Should
   explicitly report the fallback (e.g. "most recent session `<title>` is
   running as a background agent; resuming `<fallback-title>` instead — run
   `claude agents` to attach to the live one") rather than silently
   substituting.
2. **(Claude Code itself, or a documentation/procedure gap)** A session the
   user believed they cleanly exited was instead left running as an orphaned
   background agent. Not yet established whether this is expected behavior
   tied to a specific launch-flag/mode combination (observed once during a
   bypass-permissions-mode relaunch) or a genuine leak. A companion CC-native
   `/bug-report` for this half was discussed but not yet filed by the user as
   of this writing (the `/bug-report` skill is locked from model-invocation
   per AIH-876 — only the user can trigger it).

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
|------|--------|-------|

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

Two transcripts, same project directory
(`-Users-sergeiwallace-projects-sergei--worktrees-sw-6`), only two candidates
total:

```
-rw-------  17274590  Aug 12 01:05  e708efd4-bdb7-4a55-a1c8-997e9747adc8.jsonl  (correct — custom title "sw-6")
-rw-------  79513021  Aug 12 01:30  e2184270-60ed-44f2-b198-3b16abfa6dc3.jsonl  (wrong — custom title "sw-6-2", then re-titled "sw-6" by a later `ai c 6` launch)
```

`claude --resume e708efd4-bdb7-4a55-a1c8-997e9747adc8 --name sw-6
--dangerously-skip-permissions` output:

```
Session e708efd4-bdb7-4a55-a1c8-997e9747adc8 is currently running as a background agent (bg). Use `claude agents` to
find and attach to it, or add --fork-session to branch off a copy.
```

`ai c --help` confirms `-r/--resume` is unrelated to Claude Code's own resume
concept — it is `ai c`'s own tmux-reattach flag:

```
-r, --resume        Resume an existing session
```

`src/ai_cli/main.py` (this repo), around the `-r/--resume` handling:

```python
# -r/--resume means "re-attach to the running tmux session". In bare mode
# there is no tmux session to attach to, and the engine's own conversation
# resume is already applied unconditionally below, so the flag is a no-op
# rather than an error.
if resume and not bare:
    session = _session.resolve_session(prefix, name)
    if not session:
        print(f"No matching session found for '{prefix}{name or '*'}'")
        sys.exit(1)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])
```

<!-- /doc:region name="appendix_evidence" -->
