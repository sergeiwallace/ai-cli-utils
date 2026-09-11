---
title: "Session maintenance can terminate unrelated live mosh transports"
category: bugs
tags: [session, mosh, tmux, process-hygiene]
status: fix-verified
severity: P1
template_version: "bug-1.0.0"
---

# Session maintenance can terminate unrelated live mosh transports

## Symptoms

When a managed agent exits or restarts, several unrelated remote terminal
connections can return to their local shells at once even though their tmux
sessions are independent.

## Environment and reproduction

The incident occurred with multiple macOS mosh clients connected to managed
tmux sessions on a Linux host. Host logs were unavailable, so the exact process
list and ages from the incident could not be preserved.

The causal path is deterministic in the current source. Scoring a typical live
`mosh-server` command with a local-client result of empty, an age of 25 hours,
and an unrelated live tmux session produces:

```text
score=80 threshold=80 verdict=orphaned detail=no active client, age > 24h, no matching tmux session
```

The regression starts a real sibling process, feeds that exact classification
to `ai ps cron`, and observes the subprocess receive SIGTERM on the unfixed
code.

## Root cause analysis

```text
a long-running agent exits normally
  -> its supervisor starts a replacement child
  -> the child runs `ai ps cron` during startup
  -> host-wide scoring mistakes live mosh servers for orphans
  -> cron sends SIGTERM to every local score-at-threshold process
  -> unrelated mosh clients exit to their local shells
```

`process_hygiene._run_lsof_udp()` only recognizes local `mosh-client`
processes. On the remote server the clients run on other machines, so their UDP
ports cannot appear in that local process listing. A normal mosh-server command
also does not contain the tmux session name, making both negative signals
incapable of proving abandonment. Age supplies the final points needed for the
automatic kill threshold.

The generated session child invokes `ai ps cron` before each agent launch. A
normal agent exit replaces the entire child, so the implicit cleanup reruns
during the reported exit/relaunch transition.

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| Clean-exit tmux teardown targets a sibling | Traced the supervisor variable scopes | Rejected for the reported incident: at the time of this investigation the baked `tmux_session` name and this transcript's live session coincided, so this specific teardown did not cause the reported symptom. **This was never general safety evidence that a baked name is a safe teardown target** — the AI-CLI-1wzz audit (docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md) subsequently found and fixed two real name-based ownership races in exactly this teardown/bootstrap mechanism: N-4 (raw name-only `kill-session` on supervisor clean exit, fixed PR #134) and N-6 (supervisor ownership bootstrap resolved by mutable name instead of live pane context, fixed PR #135) |
| The heartbeat reaper kills a live sibling | Read its lease, process-state, generation, and atomic tmux-fingerprint gates | Rejected for this path: every failed revalidation makes the kill unreachable |
| Session-start process hygiene kills live sibling transports | Score reproduction plus a real subprocess regression through `cmd_ps cron` | Confirmed: sibling exited with status `-15` |

## Scope-of-fix decision

The initial cron-only change contained the reported incident but was not sufficient to eliminate
the broader class. A complete authority inventory found other launch-time and explicit kill paths
that trusted a score, tmux name, or PID without immutable ownership proof. The remediation therefore
covers every identified path: implicit cleanup is non-destructive, tmux kills are generation-fenced,
and PID-based explicit commands revalidate captured process identity immediately before signalling.

The initial 9-finding remediation (PR #129) was not sufficient either: the subsequent AI-CLI-1wzz
audit initiative (docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md) ran seven more
verification rounds and found six further distinct mechanisms of the same class, each fixed and
independently RED/GREEN-verified in turn:

- N-1 (PR #132): a dead-pane relaunch race.
- N-2 (PR #133): a new-session configuration-failure cleanup race.
- N-3 (PR #134): a new-session ownership-bootstrap race that acquired identity through the mutable
  name instead of tmux's own opaque ID.
- N-4 (PR #134): a supervisor clean-exit teardown that killed by raw mutable name.
- N-5 (PR #134): an abandoned-process reclamation path that validated identity but dropped it
  before signalling, a PID-reuse race.
- N-6 (PR #135): the generated supervisor's own ownership-bootstrap step repeating N-3's
  mutable-name race one level down.
- N-7 (PR #136): the launcher reverting from its captured opaque ID back to the mutable name for
  post-creation configuration/attach.

The class-level fix is now the union of all of these: every destructive tmux/process authority edge
in this codebase is fenced by an atomically-captured opaque ID (and, for tmux, a generation token),
never by a mutable, reusable name or a bare PID alone.

## Fix

`ai ps cron` remains available for stale transport-file cleanup and remote
inventory refresh, but it can no longer call `auto_clean_orphans`. Only the
explicit, user-confirmed `ai ps clean` path can signal a scored process, and it
must match the PID and creation time captured during inventory.

Launch-time stale-session cleanup no longer signals background helpers. Quota scraping allocates a
unique tmux name and cleans up only after atomically matching its captured opaque session ID and
generation token. Sandbox recreation refuses tokenless collisions and applies the same atomic
identity fence. Tunnel and browser state records now include PID, creation time, executable,
command, and port; stale or mismatched records are removed without touching the live PID holder.

## Verification

Class-wide remediation adds real-process survival regressions for hyphenated launch cleanup,
process-inventory identity mismatch, and legacy tunnel/browser PID records, plus real isolated-tmux
coverage for tokenless sessions and generation changes.

The frozen sibling-survival regression was RED before the production edit
because the real sibling exited with status `-15`. It was GREEN after the
guard, RED for the same reason when only the production guard was temporarily
reversed, and GREEN again after restoration.

**Full independent verification (2026-09-11):** the AI-CLI-1wzz audit initiative ran eight
verification rounds (docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md), each finding and
each fix independently RED/GREEN-verified by the orchestrating session -- not merely trusted from a
worker's or auditor's self-report. The final commit (`552a883`) was run through the full repository
test suite directly by the orchestrating session, with real tmux and temp-directory access: 26 failed
/ 2854 passed / 15 skipped, identically in serial and parallel execution, and every one of those 26
failures is a member of a 29-item pre-existing flaky baseline captured on an earlier commit
(`e15a51f`) -- diffed by exact test node ID, zero new failures. The audit tooling's own sandbox could
never independently execute the real-tmux regression suite (AF_UNIX socket creation is denied in
every `cx audit`/`cx research` sandbox on this host, confirmed across two separate attempts including
one with `--allow-test-execution`); this is recorded as an accepted tooling limitation, not an open
code-safety gap. See the audit document's "Closure Determination" section for the full evidence.

## Lessons learned

A score is suitable for prioritizing an operator's review, not for proving
process ownership. Implicit lifecycle hooks must not gain host-wide signal
authority from heuristic evidence, especially evidence collected on the
opposite side of a network connection.

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-10 | Regression and causal fix | Implemented with high effort because the recurring symptom had several independent historical mechanisms. |
| 2026-09-10/11 | N-1 through N-7, JA-2 | Six further mechanisms of the same class found and fixed across the AI-CLI-1wzz audit initiative's Rounds 2-7 (PRs #132, #133, #134, #135, #136); full-suite verified against a diffed pre-existing baseline with zero new failures; status raised to `fix-verified`. |
