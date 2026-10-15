---
title: "A launch for a free slot attaches to, or kills, a longer-named live slot"
category: bug
tags: [session, tmux, sandbox]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A launch for a free slot attaches to, or kills, a longer-named live slot

## Founding Ask Coverage

N/A: this public record covers one defect found while sweeping the remote-reattach
bug class (AI-CLI-fkvp). The caller tracks the rest of that sweep.
This coverage index is non-authoritative.

**Status:** resolved

**Severity:** P1

**Created:** 2026-10-15

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Table of Contents](#table-of-contents)
- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

With slot 1 free and slot 10 (or any of 10 to 19) running, `ai c 1`:

- attaches with `attach-session -d` to slot 10, detaching whoever was on it, and never
  creates slot 1;
- with `-s/--sandbox`, kills slot 10 as "the existing session to replace" and creates
  slot 1 in its place.

`ai c 1 -r` (`resolve_session`) likewise resumes slot 10.

## Environment

Any host with tmux. Measured on tmux 3.7c on a private socket.

## Reproduction Steps

1. `tmux new-session -d -s c-r-llm-fw-10 'sleep 60'` on a private server.
2. `tmux has-session -t c-r-llm-fw-1` exits 0, and
   `tmux display-message -p -t c-r-llm-fw-1 '#{session_name}'` prints `c-r-llm-fw-10`.
3. `tmux has-session -t =c-r-llm-fw-1` exits 1.

## Root Cause Analysis

tmux resolves a bare session target by exact name, then by unique prefix, then by
pattern. `_do_session_launch` decides attach-vs-create with
`has-session -t <session_id>`, and its sandbox branch captures the identity to fence and
kill through the same bare name. The slot allocator (`_matching_tmux_sessions`) compares
names exactly, so it correctly reports slot 1 free; only the later tmux probes
disagreed with it. The launch tests could not see this: their fixture answered
`has-session` with an exact Python name comparison instead of asking tmux.

Rejected: "attach-session also needs `=`". Once the probe is exact, the attach and
configuration calls target a name that exists, and tmux's exact match takes precedence
over prefix matching.

## Prior Fix Attempts

| Date | What was tried | Outcome |
|------|----------------|---------|
| 2026-10-15 | First RED draft with an unmarked neighbour | The sandbox case refused ("not owned") instead of killing; marked the neighbour as real launches do, which reproduced the kill |

## Fix

Exact `=NAME` targets for every probe of the launching session's existence or identity:
the four `has-session` calls and the sandbox `capture_tmux_session_identity` in
`_do_session_launch`, and `resolve_session`'s `has-session`. The test fixture now
answers `has-session` from the real tmux server.

Not changed: `ai attach <name>` keeps tmux's own resolution of a name a person typed,
and `sync.py`'s status label is display-only.

## Verification

- Frozen regression
  `test_given_slot_10_live_when_free_slot_1_is_launched_then_slot_1_is_created_and_slot_10_untouched`
  (plain and `--sandbox`), real tmux. RED on unfixed code: plain, "slot 1 was never
  created"; sandbox, "the launch for slot 1 replaced slot 10" (slot 10 gone). GREEN after.
- `tests/test_session_launch_integration.py`: 28 passed, including every pre-existing
  case with `has-session` now answered by real tmux.
- Full gate: ruff check exit 0, ruff format clean, pyright src/ 0 errors,
  pytest 4317 passed and 21 skipped.

## Lessons Learned

A tmux name passed to `-t` is a search, not an identifier. Use `=NAME` (or the opaque
`$id`) whenever the code means one specific session, and let a test fixture answer
tmux questions with tmux so its resolution rules stay under test.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

2026-10-15: Opus worker delegated for the remote-reattach bug class found this while
reading the reattach path's tmux probes; measured tmux's prefix resolution on a private
server, froze a two-case RED against real tmux, then made the probes exact.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
