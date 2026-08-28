---
title: "Atomic tmux Session Fencing"
category: research
tags: [research, tmux, concurrency, safety]
status: complete
source: "gpt-5-2026-08-28"
template_version: "research-1.2.0"
related_docs:
  - docs/designs/stale-session-reaper.md
  - docs/audits/stale-session-reaper-implementation-audit.md
---

# Atomic tmux Session Fencing

**Status:** complete

**Created:** 2026-08-28

## Table of Contents

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [Steps](#steps)
  - [Source and implementation inspection](#source-and-implementation-inspection)
  - [Empirical checks](#empirical-checks)
  - [Prior-art and fallback survey](#prior-art-and-fallback-survey)
- [Findings](#findings)
  - [Command queues and cross-client ordering](#command-queues-and-cross-client-ordering)
  - [The atomic primitive: synchronous `if-shell -F`](#the-atomic-primitive-synchronous-if-shell--f)
  - [Control mode](#control-mode)
  - [`lock-server`, `lock-session`, and `wait-for -L`](#lock-server-lock-session-and-wait-for--l)
  - [Hooks and user options](#hooks-and-user-options)
  - [Boundary between tmux state and external evidence](#boundary-between-tmux-state-and-external-evidence)
- [Mechanism construction](#mechanism-construction)
  - [Required final protocol](#required-final-protocol)
  - [Fingerprint and command shape](#fingerprint-and-command-shape)
  - [Required runtime tests](#required-runtime-tests)
- [Prior art](#prior-art)
- [Assessment of the two-phase-reap fallback](#assessment-of-the-two-phase-reap-fallback)
  - [Why N observations are not N independent race wins](#why-n-observations-are-not-n-independent-race-wins)
  - [Reasonable N if retained as defense in depth](#reasonable-n-if-retained-as-defense-in-depth)
  - [Distributed-systems analogues](#distributed-systems-analogues)
- [Gaps, blindspots & emergent findings](#gaps-blindspots--emergent-findings)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
- [Rationale](#rationale)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Revision Log](#revision-log)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

<!-- doc:region name="context" kind="immutable" -->

## Context

The reaper currently coordinates with its generated session supervisor through a
filesystem lease, revalidates process and heartbeat evidence, and then sends a separate
`tmux kill-session`. The lease excludes a cooperating filesystem writer, but tmux clients
do not consult it. The safety question is whether tmux can make the last tmux-state check
and the destructive command one non-interleavable server operation.

**Primary period:** 2024–2026
**Source weighting:** tmux 3.7c (2026) release documentation and tagged source are primary;
older material is used only where it defines stable command-language behavior.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

The primary target is tmux 3.7c, released on 2026-08-17 and installed in the research
environment. It was the current upstream release on 2026-08-28. [VERIFIABLE][^1] Core
queue behavior was verified against the 3.7c tag rather than inferred from general tmux
familiarity. Programmatic tmux fencing prior art: no significant post-2024 developments
addressing this exact check-then-kill race were found. [NO SOURCE]

## Executive Summary

**Mechanism found: synchronous `if-shell -F` server-side compare-and-kill, verified via
the tmux 3.7c manual and tagged source for the event loop, per-client command queues,
`if-shell`, formats, and `kill-session`; here is how the reaper should use it: retain the
generation filesystem lease for external heartbeat coordination, then issue one
non-waiting `if-shell -F` whose predicate compares the exact current tmux session/pane
fingerprint and whose true branch kills the captured opaque session ID.** [VERIFIABLE][^2][^3][^4][^5][^6]

This is stronger than a probabilistic two-phase reap. A competing tmux command is
linearized either before the predicate, in which case the changed pane state makes the
predicate false, or after the kill; it cannot execute between the synchronous predicate
and its inserted kill command under tmux 3.7c's queue-draining implementation. [INFERENCE]
The mechanism is source-verified but not advertised by tmux as a formal stable
transaction API, so the package should version-gate it and carry a real-server concurrency
regression test. [INFERENCE]

## Steps

### Source and implementation inspection

1. Confirmed the current upstream and installed version as tmux 3.7c.
2. Read the 3.7c manual's parsing, command-queue, format, control-mode, locking, hook,
   and target-ID sections.
3. Traced `proc_loop` → `server_loop` → `cmdq_next`, then traced the synchronous
   `if-shell -F` branch insertion and `kill-session` implementation.
4. Traced control-mode input into the same per-client queue and inspected the separate
   display-lock and cooperative `wait-for` lock implementations.
5. Inspected current libtmux lifecycle code as representative programmatic prior art.

### Empirical checks

The environment contains `/opt/homebrew/bin/tmux`; `tmux -V` returned exactly:

```text
tmux 3.7c
```

A read-only attempt to discover a usable server with
`tmux list-sessions -F '#{session_id} #{session_name}'` returned exactly:

```text
no suitable socket path
```

The scoped-write sandbox permits modifying only this research document, so creating the
Unix-domain socket and sessions required for a destructive concurrency experiment would
have violated the task's filesystem boundary. No live-server result is claimed. [NO SOURCE]
The source proof is therefore load-bearing, and the runtime tests in
[Required runtime tests](#required-runtime-tests) are a release gate rather than a claim
that was executed here.

### Prior-art and fallback survey

The survey checked current tmux-native facilities and current libtmux code, then evaluated
the proposed N-in-a-row fallback against the exact race rather than assuming independent
failure windows. It also followed two emergent threads: attached-client state should be in
the final fingerprint, and format/parser injection must be treated as part of the safety
boundary.

## Findings

### Command queues and cross-client ordering

The manual says each client has a command queue, commands added to it run in order, and a
conditional command is inserted immediately after `if-shell`. [VERIFIABLE][^2] The source
makes the cross-client implication concrete:

1. `proc_loop` dispatches one libevent iteration and only then calls `server_loop`.
   [VERIFIABLE][^3]
2. `server_loop` iterates the global queue and each identified client's queue.
   [VERIFIABLE][^4]
3. `cmdq_next(client)` loops until that client's queue is empty or an item returns
   `CMD_RETURN_WAIT`; only then can `server_loop` advance to another client.
   [VERIFIABLE][^5]

Therefore a sequence of ordinary synchronous commands already present on one client queue
does have non-interleaving behavior relative to commands on other clients in tmux 3.7c.
[INFERENCE] This is not true across a waiting command: `if-shell` without `-F`,
`run-shell` without background mode, `display-panes`, and `wait-for` can yield the queue,
after which other clients can run. The manual explicitly describes those queue stops, and
`cmdq_next` implements them through `CMD_RETURN_WAIT`. [VERIFIABLE][^2][^5]

A semicolon chain is consequently non-interleavable only while every command before the
destructive action is synchronous. A chain such as `display-message ; kill-session` is
queue-atomic with respect to other tmux clients, but a shell-based check such as
`if-shell 'external-check' 'kill-session ...'` is not. [INFERENCE]

### The atomic primitive: synchronous `if-shell -F`

With `-F`, `if-shell` does not run `/bin/sh`. It expands the predicate as a tmux format,
chooses a branch, creates that branch as tmux commands, inserts them immediately after the
current queue item, and returns normally rather than waiting. [VERIFIABLE][^6] The format
language provides string comparisons, boolean conjunction/disjunction, user-option lookup,
and `W:`/`P:` loops over windows and panes. [VERIFIABLE][^7]

This provides a compare-and-delete operation over tmux-owned state:

```text
compare current session/pane fingerprint inside tmux
                 |
          mismatch? -- yes --> preserve
                 |
                 no
                 v
       kill captured session ID
```

No other tmux client's command can change the compared state between those two boxes under
the 3.7c implementation because the true branch is inserted into, and immediately drained
from, the same client queue. [INFERENCE] `kill-session` synchronously resolves its target
and destroys the selected session in its command executor. [VERIFIABLE][^8]

### Control mode

Control mode is not a separate transactional protocol. It accepts ordinary tmux commands
or command sequences, one newline-terminated request at a time. [VERIFIABLE][^9] Its read
callback parses every complete line and appends it to that control client's ordinary
command queue. [VERIFIABLE][^10]

Accordingly, two separately written control-mode lines have same-connection ordering but
no exclusivity across the gap between lines. A single line containing the synchronous
`if-shell -F` compare-and-kill does inherit the per-client queue property described above.
[INFERENCE] Control mode is optional; a normal one-shot `tmux` client carrying the same
single command is simpler and has the same relevant server semantics. [INFERENCE]

### `lock-server`, `lock-session`, and `wait-for -L`

`lock-server` is a display/client lock: the manual says it locks each client individually,
and the source iterates clients, suspends non-control clients, stops their TTYs, and sends
the configured password-lock command. Control clients are explicitly skipped.
[VERIFIABLE][^11] It does not stop command dispatch, take a mutex around server state, or
block other clients from sending `respawn-pane`, `new-window`, or `kill-session`.
[INFERENCE]

`lock-session` calls the same client-lock routine only for clients attached to the target
session. It has no usable mutual-exclusion side effect. [VERIFIABLE][^11]

`wait-for -L channel` is a genuine server-resident cooperative channel lock, but only
clients that also issue `wait-for -L` on that same channel wait. Its source changes a
`wait_channel.locked` flag and queues competing lockers; unrelated commands never consult
that flag. [VERIFIABLE][^12] It can coordinate cooperating automation components but cannot
fence arbitrary tmux clients and is not the final safety mechanism.

### Hooks and user options

Hooks are commands run on triggers; most commands have an `after-` hook, and hook array
members run in order. [VERIFIABLE][^13] They are useful for observation but do not reserve
the session, and a hook that starts a shell introduces another asynchronous boundary.
[INFERENCE] `kill-session` itself is declared without the command flag used for normal
after-hooks, so hooks cannot be treated as a veto callback in front of destruction.
[VERIFIABLE][^8]

User options are valuable identity fields because formats can read them, but they are
mutable server data, not locks. A generation option should be one conjunct in the atomic
predicate, not the authority by itself. [INFERENCE]

### Boundary between tmux state and external evidence

The server-side predicate cannot atomically read an arbitrary filesystem heartbeat or an
OS-specific process-start timestamp. It can read tmux's authoritative `session_id`, user
generation option, session attachment count, window/pane IDs, `pane_pid`, and `pane_dead`.
The manual defines the IDs as unique for an object's lifetime and exposes the listed pane
and session fields as formats. [VERIFIABLE][^14]

The sound composition is therefore:

- the existing generation lease fences the cooperating heartbeat publisher while the
  external process/heartbeat checks run;
- the final `if-shell -F` predicate fences tmux-mediated state changes and requires every
  pane still to be dead at the tmux linearization point;
- the true branch kills by the opaque session ID.

Neither fence subsumes the other. The filesystem lease alone does not constrain tmux, and
the tmux predicate alone does not serialize a filesystem heartbeat writer. [INFERENCE]

## Mechanism construction

### Required final protocol

1. Acquire and verify the candidate generation's exclusive filesystem lease.
2. Re-read the heartbeat, boot identity, generation token, exact session ID, complete pane
   set, pane PIDs/process identities, and liveness. Fail closed on every ambiguity.
3. Require tmux to report every candidate pane dead. Treat `pane_dead=0`, a missing pane,
   an extra pane/window, a changed PID, a changed generation, or a newly attached client as
   a mismatch.
4. Capture a canonical tmux-only fingerprint using the same format expression that the
   final predicate will evaluate.
5. While still holding the filesystem lease, invoke exactly one synchronous
   `if-shell -F`: compare the current fingerprint to the captured fingerprint and kill the
   captured session ID only in the true branch.
6. Release the filesystem lease in a `finally` path. A false predicate is a preserve result,
   not a retry within the same evaluation cycle.

Steps 4 and 5 may remain separate client requests because the second request compares the
entire current fingerprint: an intervening tmux mutation is detected rather than trusted.
[INFERENCE] The atomicity boundary that matters is inside step 5, between evaluation of the
current fingerprint and execution of the true branch.

### Fingerprint and command shape

A suitable canonical fingerprint includes at least:

```text
#{session_id}|#{@managed_generation}|#{session_attached}|
#{W/i:#{window_id}[#{P:#{pane_id}=#{pane_pid}=#{pane_dead};}]}
```

`W/i:` orders windows by index; the nested `P:` loop expands panes for each current window.
The tagged source shows that a window-loop iteration creates a new window format context,
and a pane loop then enumerates panes of that context. [VERIFIABLE][^7]

The evaluator should capture this value after all external gates pass, validate the
captured components, then send an argv-based command equivalent to:

```text
tmux if-shell -F -t "<session-id>" \
  "#{==:<current-fingerprint-format>,<expected-fingerprint-literal>}" \
  "kill-session -t '<session-id>'"
```

The names above are placeholders. Production construction must not use a shell: pass argv
directly, accept only canonical `$<digits>`, `@<digits>`, `%<digits>`, positive PID,
`0|1`, and fixed-alphabet generation-token fields, and correctly escape tmux format/parser
metacharacters. Because `if-shell` parses its selected branch a second time, the canonical
opaque ID in the branch must be tmux-parser quoted as a literal, as illustrated above.
[HEURISTIC] A post-command query may establish the resulting state for logging, but it is
not part of safety. [INFERENCE]

The predicate must encode `pane_dead=1` for every expected pane, either because that value
is part of the captured fingerprint or as explicit boolean conjuncts. A respawn that runs
first changes `pane_dead` to `0` and normally changes `pane_pid`, so the predicate preserves;
if the atomic command runs first, the later respawn command finds no session. [INFERENCE]

### Required runtime tests

Before enabling reap mode, test the exact generated command against each supported tmux
version and platform:

1. **Positive control:** an isolated `remain-on-exit` session with all panes dead and a
   matching generation is killed exactly once.
2. **Respawn-before-compare:** another client completes `respawn-pane` first; the predicate
   reports mismatch and the live session survives.
3. **Compare-before-respawn:** the compare-and-kill completes first; the competing respawn
   fails because the session no longer exists.
4. **Stress:** repeat the two-client race thousands of times with a barrier; accept only
   the two linearizable outcomes above.
5. **Set mutation:** extra pane/window, removed pane, changed pane PID, changed user option,
   and newly attached client each preserve.
6. **Control mode parity:** send the entire conditional as one control-mode line and verify
   the same two linearizable outcomes.
7. **Negative queue control:** deliberately place a waiting `if-shell` or `run-shell`
   before kill and prove that a second client can run during the wait. This confirms the
   test can detect the interleaving it is meant to exclude.
8. **Parser controls:** hostile-but-valid tokens and malformed format strings must fail
   closed; no malformed predicate may expand to a truthy value.

These tests should run with an isolated socket and empty configuration. A runtime version
outside the tested set must force observe-only mode. [HEURISTIC]

## Prior art

Current libtmux exposes object IDs but its ordinary `Session.kill()` directly invokes
`kill-session`; its context-manager cleanup first calls `has_session(name)` and then calls
`kill()` as a separate request. [VERIFIABLE][^15] That is representative convenience-API
behavior, not a fenced stale-session protocol. It also demonstrates that using a typed tmux
wrapper does not automatically eliminate check-then-act races. [INFERENCE]

No maintained session manager or CI wrapper was found documenting this exact
"dead observation, concurrent pane respawn, destructive kill" problem or using a
server-side compare-and-kill fingerprint. [NO SOURCE] This negative result is bounded:
repository-wide code search was not available for every tmux management tool, so it is not
evidence that no private or unindexed implementation exists. [NO SOURCE]

## Assessment of the two-phase-reap fallback

### Why N observations are not N independent race wins

N consecutive stale observations are useful debounce, but they do **not** transform this
exact TOCTOU into a requirement for N unlucky read-to-kill windows. Only the final cycle
contains the destructive gap. A session may be dead for N observations and become live
once, after the Nth check but before its kill. No earlier race win is required. [INFERENCE]

The common probability sketch $p^N$ is therefore invalid unless the design establishes
that each qualifying observation includes an independent destructive race and that a
revival necessarily changes durable evidence before every later observation. This design
establishes neither condition. [INFERENCE] A persistent or periodic external respawner also
correlates observations, further defeating an independence assumption. [INFERENCE]

### Reasonable N if retained as defense in depth

With evaluation interval $\Delta=60$ seconds and stale threshold $T=10$ minutes, the
earliest reap after N qualifying cycles is approximately:

$$T_{reap}=T+(N-1)\Delta$$

Thus N=2 adds about one minute and N=3 about two minutes. [INFERENCE] If repeated
confirmation is retained behind the atomic tmux fence, N=2 is a reasonable operational
debounce for transient probe or scheduling anomalies; N=3 offers little additional safety
against the final race and delays cleanup further. [HEURISTIC] N must not be presented as
the P0 safety closure.

### Distributed-systems analogues

The applicable pattern is not quorum observation but an enforcing-resource conditional
mutation: compare-and-set/delete at the owner of the state. Here tmux owns pane/session
state, and `if-shell -F` plus the immediately inserted `kill-session` supplies that
conditional mutation. [INFERENCE]

A fencing token is effective only when the resource receiving the destructive operation
checks the token. The filesystem generation lease is therefore a valid fence for the
cooperating heartbeat ledger but cannot fence tmux. [INFERENCE] Quorum-style repeated
observation can improve confidence in noisy evidence; it cannot make an unfenced actuator
safe when the actuator's state can change after the final observation. [INFERENCE]

## Gaps, blindspots & emergent findings

1. **Source guarantee versus public contract.** The non-interleaving conclusion is strongly
   supported by tmux 3.7c source, but the manual promises in-queue order, not an explicit
   cross-client transaction compatibility guarantee. A future scheduler refactor could
   change it. Version-gated concurrency tests are required. [INFERENCE]
2. **Runtime experiment unavailable here.** The installed binary matched current upstream,
   but the sandbox exposed no usable socket path and prohibited creating one. The exact
   command syntax and race outcomes remain a pre-release empirical gate. [NO SOURCE]
3. **Attached-user blindspot.** Attachment does not itself revive a dead pane, but it is a
   strong signal of current human intent. Including `session_attached` in the atomic
   fingerprint prevents an attach command that wins the queue order from being followed by
   a reaper kill. [INFERENCE]
4. **Non-tmux process concurrency.** Pane applications execute concurrently with the tmux
   server. The predicate must depend on tmux's `pane_dead`, not on a child-process snapshot
   alone. A pane already marked dead cannot be revived without a tmux command; a still-live
   pane yields `pane_dead=0` and is preserved. [INFERENCE]
5. **Parser injection and silent format failure.** The final fence is only as safe as its
   generated predicate. Dynamic fields must be strictly canonicalized, and malformed or
   overlong formats must preserve. Current libtmux documentation itself warns that malformed
   native filters may silently expand to false, illustrating why positive and negative
   parser controls matter. [VERIFIABLE][^15]
6. **Session groups and linked windows.** The fingerprint should include window IDs and pane
   sets, and the reaper should either reject grouped sessions or explicitly verify the
   intended group semantics before killing. This interaction was not empirically resolved.
   [NO SOURCE]
7. **Threat-model boundary.** This prevents accidental interleaving by other clients. A
   same-user malicious client can issue its own `kill-session`, alter options, or race with
   intentionally crafted state; tmux's ordinary same-server authority model is not an
   adversarial isolation boundary. [INFERENCE]
8. **Anchor-bias correction.** The initial framing expected no atomic facility and focused
   on named lock features. Following the queue source instead exposed the stronger
   compare-and-kill construction; the research therefore rejects the expected fallback as
   the primary answer. [INFERENCE]

## Comparison

| # | Mechanism | Other tmux client can intervene between final tmux check and kill? | Covers filesystem heartbeat writer? | Verdict |
|---|-----------|-------------------------------------------------------------------|-------------------------------------|---------|
| 1 | Separate query then `kill-session` | Yes | Lease only | Unsafe |
| 2 | Control mode, two lines | Yes, between lines | No | Unsafe |
| 3 | Semicolon sequence with only synchronous commands | No under 3.7c source semantics | No | Useful building block |
| 4 | `if-shell` without `-F` | Yes, while shell job runs | Potentially, but yields queue | Unsafe |
| 5 | `if-shell -F` fingerprint → immediate ID-targeted kill | No under 3.7c source semantics | No | Required tmux fence |
| 6 | `lock-server` / `lock-session` | Yes | No | Display lock only |
| 7 | `wait-for -L` | Yes for nonparticipants | No | Cooperative only |
| 8 | N consecutive observations | Yes in final gap | No | Debounce, not safety |
| 9 | Filesystem lease + `if-shell -F` compare-and-kill | No for cooperating heartbeat writes or tmux commands | Yes, for cooperating writer | Recommended composition |

Rows 3 and 5 are implementation-derived conclusions, not an explicit tmux transactional
API promise. [INFERENCE]

## Recommendation

**Mechanism found: synchronous `if-shell -F` server-side compare-and-kill, verified via
tmux 3.7c's event-loop, queue-draining, format-expansion, branch-insertion, and synchronous
session-destruction source; here is how the reaper should use it: keep the generation lease,
repeat all fail-closed external gates, capture the complete tmux fingerprint, and issue one
non-waiting conditional request that kills only when the exact session ID, generation,
attachment state, window/pane set, pane PIDs, and all-dead flags still match.**
[VERIFIABLE][^3][^4][^5][^6][^7][^8]

Do not proceed with an unfenced two-phase reap as the primary P0 closure. N=2 may be kept
as defense-in-depth debounce after the atomic fence passes runtime verification, but it is
not a substitute for the fence. [HEURISTIC]

## Rationale

The recommendation places each check at the component that can enforce it. The filesystem
lease coordinates the external publisher; tmux's synchronous conditional coordinates all
tmux clients. The server source establishes one linearization point: either a competing
tmux mutation is visible to the predicate and causes preservation, or the kill happens
before that competing command can run. [INFERENCE]

The recommendation is deliberately version-scoped. Confidence is **high for tmux 3.7c
source semantics** and **conditional on the required live-server test for production
enablement**. [INFERENCE] This is a committed mechanism verdict, not a claim that tmux has
a documented, version-independent transaction API.

## Open Questions

1. What minimum tmux version will the package support for reap mode? The queue mechanism
   should be audited and tested at that exact lower bound.
2. Are grouped sessions eligible for automatic reap, or should they remain observe-only?
3. What maximum pane/window count should the generated fingerprint support before command
   size or parser complexity forces observe-only behavior?
4. Should `session_attached != 0` be an unconditional preservation gate even before the
   atomic predicate? The final predicate should include it regardless.

## Sources

[^1]: tmux. (2026). [tmux 3.7c release](https://github.com/tmux/tmux/releases/tag/3.7c). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Current release, release date, and tag commit `e476c12`.)

[^2]: tmux. (2026). [tmux(1), command queues and parsing, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L434-L529). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Per-client queues, ordered execution, immediate conditional insertion, waiting commands, and command sequences.)

[^3]: tmux. (2026). [`proc.c`, lines 210–216, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/proc.c#L210-L216). GitHub. Verified accessible (HTTP 200) 2026-08-28. (`event_loop(EVLOOP_ONCE)` followed by the server loop callback.)

[^4]: tmux. (2026). [`server.c`, lines 247–263, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/server.c#L247-L263). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Global and per-client queue iteration.)

[^5]: tmux. (2026). [`cmd-queue.c`, lines 688–763, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/cmd-queue.c#L688-L763). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Drain-until-empty-or-wait behavior.)

[^6]: tmux. (2026). [`cmd-if-shell.c`, lines 65–124, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/cmd-if-shell.c#L65-L124). GitHub. Verified accessible (HTTP 200) 2026-08-28. (`-F` synchronous format evaluation, branch creation, immediate queue insertion, and non-waiting return.)

[^7]: tmux. (2026). [`tmux.1` formats, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L6045-L6365) and [`format.c` window/pane loops, lines 4529–4649](https://github.com/tmux/tmux/blob/3.7c/format.c#L4529-L4649). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Comparisons, boolean operators, nested window/pane expansion, and loop ordering controls.)

[^8]: tmux. (2026). [`cmd-kill-session.c`, lines 19–71, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/cmd-kill-session.c#L19-L71). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Synchronous target resolution and session destruction; command flags.)

[^9]: tmux. (2026). [tmux(1), control mode, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L8107-L8149). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Newline-terminated commands/sequences and output blocks.)

[^10]: tmux. (2026). [`control.c`, lines 516–544, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/control.c#L516-L544). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Control input parses complete lines and appends them to the control client's queue.)

[^11]: tmux. (2026). [`cmd-lock-server.c`, lines 25–72](https://github.com/tmux/tmux/blob/3.7c/cmd-lock-server.c#L25-L72) and [`server-fn.c`, lines 128–170](https://github.com/tmux/tmux/blob/3.7c/server-fn.c#L128-L170), version 3.7c. GitHub. Verified accessible (HTTP 200) 2026-08-28. (Lock commands dispatch to client TTY suspension; control clients are skipped.)

[^12]: tmux. (2026). [`cmd-wait-for.c`, lines 183–226, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/cmd-wait-for.c#L183-L226). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Cooperative named-channel locker queue.)

[^13]: tmux. (2026). [tmux(1), hooks, version 3.7c](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L5807-L5864). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Hook triggers, ordering, and after-hooks.)

[^14]: tmux. (2026). [tmux(1), unique IDs](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L906-L935) and [format variables](https://github.com/tmux/tmux/blob/3.7c/tmux.1#L6529-L6683), version 3.7c. GitHub. Verified accessible (HTTP 200) 2026-08-28. (Lifetime uniqueness of session/window/pane IDs plus attachment, PID, dead-state, and count fields.)

[^15]: tmux-python. (2026). [`libtmux/session.py`, current main](https://github.com/tmux-python/libtmux/blob/master/src/libtmux/session.py#L119-L137). GitHub. Verified accessible (HTTP 200) 2026-08-28. (Context-manager existence check followed by a separate kill; direct `Session.kill` at lines 617–697; malformed native-filter warning at lines 278–379.)

## Revision Log

| Date | Revision | Summary |
|------|----------|---------|
| 2026-08-28 | 1 | Initial source-grounded verdict; identified synchronous `if-shell -F` compare-and-kill and rejected N-in-a-row as the primary safety closure. |

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none — this was a direct research run, not an auto-remediation pass)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** Unregistered direct wrapper prompt
**Model:** `gpt-5`
**Date:** 2026-08-28

> Public-repository note: one wrapper-internal tracking label in the opening sentence was
> generalized; all substantive research instructions are reproduced verbatim.

```text
## Background

The stale-tmux-session reaper feature in ai-cli-utils (a public open-source CLI
package). An implementation audit found a P0 safety finding (DV-1): the reaper's evaluator holds a
filesystem-level lease (via `flock`) while it re-verifies "this session is dead" (process-liveness +
heartbeat-staleness), then issues a separate `tmux kill-session -t <session-id>` command. Tmux itself
never consults the filesystem lease. A tmux client (or the generated session script itself, if it
respawns/reattaches) could recreate a live pane on the same session ID in the window between the
evaluator's final revalidation and the separate kill command, and the evaluator would still issue
the kill, because the immutable session ID, generation token, and heartbeat record are all unchanged
by that respawn. This is the exact bug class (falsely killing a session that turned out to still be
live) the entire feature exists to prevent -- it has already caused 4 real production incidents in a
prior, less careful implementation of similar logic.

## Objective

Determine, with high confidence and real evidence (not inference from general tmux familiarity),
whether tmux offers ANY mechanism that could let a caller atomically tie a state check (verifying a
session/pane's exact identity, e.g. its opaque session ID or pane PID) to a destructive action (kill,
or an equivalent operation) such that no other tmux client's intervening command can change the
outcome. This includes but is not limited to:

- tmux's control-mode (`tmux -C`) command queue and whether commands issued over one control
  connection have any exclusivity/ordering guarantee relative to OTHER clients' commands on the
  same server (not just ordering relative to other commands on the SAME connection, which does not
  help here).
- Any conditional-execution primitive in tmux's command language (e.g., `if-shell`, hooks,
  or scripting patterns) that could express "kill this session only if its current state still
  matches X" as a single request to the tmux server, evaluated atomically server-side.
- tmux's server-side locking mechanisms (`tmux lock-session`, `lock-server`) -- these are documented
  as CLIENT-facing (password-gated display lock), but verify definitively whether they have any
  server-side mutual-exclusion side effect that could be repurposed, or confirm they do not.
- Whether tmux's single-threaded server event loop provides any implicit atomicity guarantee for a
  SEQUENCE of commands sent together (e.g., a single `tmux` invocation with multiple `;`-separated
  commands, or a single control-mode message containing multiple commands) -- i.e., does the tmux
  server process a semicolon-chained command sequence as one atomic unit with respect to OTHER
  clients' concurrently-arriving commands, or can another client's command interleave between two
  commands in the same chain?
- Any tmux hook (`set-hook`) or session/pane variable mechanism that could serve as a
  request-and-confirm handshake tighter than the current flock-based approach.
- Prior art: how do other tools that manage tmux sessions programmatically (session managers,
  orchestration tools, CI wrappers) handle this exact "don't kill a session that became live after I
  checked" problem? Is there a known, established pattern in the wild for this?

## If no atomic mechanism exists (the likely outcome -- confirm or refute this expectation)

Research the practicality and soundness of the documented fallback: a "two-phase reap" pattern where
the destructive kill only happens after the SAME candidate has independently qualified for reap
across multiple separate, fully independent evaluation cycles in a row (each with its own fresh
revalidation), rather than relying on a single atomic check-then-act instant. Specifically assess:

- Does this genuinely reduce risk in a meaningful way (turning one race window into needing N
  consecutive unlucky windows), or are there correlated-failure scenarios where multiple consecutive
  windows could plausibly all be "unlucky" together (e.g., if the same external actor is
  respawning the session repeatedly across the whole multi-cycle window)?
- What is a reasonable N (number of independent confirming cycles) given this design's existing
  60-second evaluation interval and 10-minute staleness threshold, balancing risk reduction against
  delaying legitimate reaps?
- Are there other established patterns from distributed-systems literature for "safely act on a
  resource you don't have exclusive atomic control over" (e.g., leader-election-adjacent patterns,
  fencing tokens generalized beyond the single-check case, quorum-style multi-observation
  requirements) that might apply better than a simple N-in-a-row repeat?

## Scope note — questions, examples, and named references are a starting point, not a checklist
The questions, topics, and named examples below are illustrative anchors and a FLOOR for this
research -- not an exhaustive list to answer only or evaluate only. Reason independently: survey the
landscape broadly, follow the evidence where it leads, expand scope where warranted, and surface
relevant work, factors, and failure modes not named here. Actively resist answering only the listed
questions or evaluating only the named approaches -- an output that merely fills in the listed items
has NOT met the research goal.

## Independent exploration (gaps, blindspots, emergent threads) -- required
Treat the question list as a FLOOR, not a ceiling. As you research, actively surface what this
framing may be missing and pursue each promising thread to a logical conclusion:
- Adjacent or upstream factors the questions don't capture.
- Contrarian / disconfirming evidence -- report it even when it challenges the premise.
- Emerging 2025-2026 practices, tools, or research not anchored by the named examples.
- Known failure modes and second-order effects.
Whenever a load-bearing thread surfaces mid-research, follow it to its conclusion and report it in a
dedicated "Gaps, blindspots & emergent findings" subsection. Explicitly NAME any blindspot you
suspect but cannot resolve (and why) rather than omitting it. Anchor bias -- over-fitting to the
listed questions and example approaches -- is a known failure mode; counter it deliberately and say
where you did.

<grounding_instructions>
You are a principal engineer who has built and operated tmux-based session-management
infrastructure in production, including programmatic session lifecycle automation. You have deep,
source-level familiarity with tmux's client/server architecture and command protocol. You have
strong opinions backed by evidence. When you cannot find a source, you say so explicitly rather than
guessing from general familiarity with tmux.

Temporal scope: Weight sources by recency -- 2026 (primary) -> 2025 -> 2024, but tmux's core
client/server architecture and command-queue semantics are largely stable/foundational, so
authoritative sources (the tmux man page for the installed/current stable version, the tmux source
repository, and the tmux CHANGES file) are appropriate regardless of publication date -- cite the
tmux version each source describes. If post-2024 literature discussing this exact
atomicity/session-fencing problem is genuinely sparse, state
"[subtopic]: no significant post-2024 developments found" rather than backfilling with unrelated
older sources. Backfilling is a failure mode, not a hedge.

Before generating your final output, execute a Chain-of-Verification (CoVe) to ensure factual
fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because the prompt
   implied you should, rather than because you verified it.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale before
appending the tag -- writing the tag first causes post-hoc rationalization. Rationale -> evidence
check -> tag.

- [VERIFIABLE]: backed by documentation, peer-reviewed research, or official tech blogs (2024-2026).
  Carry an inline footnote ref to the source: [VERIFIABLE][^N].
- [HEURISTIC]: widely accepted best practice without a specific citation.
- [INFERENCE]: a logical conclusion drawn from context. Provide your reasoning in-text. Do not
  fabricate a source. Tier tag only -- NO footnote ref.
- [NO SOURCE]: explicitly state when you cannot find verifiable data. Tier tag only -- NO footnote
  ref.

Citation format (mandatory for every externally-sourced claim):
- Inline: append the GFM footnote ref directly after the tier tag -- [VERIFIABLE][^N]. A claim
  citing multiple sources carries ascending separate refs -- [VERIFIABLE][^3][^7] (never grouped
  [^3,^7]; never out of order).
- Footnote definitions live once, under ## Sources, in APA form with a clickable URL or DOI and an
  access-verification stamp.
- URL-or-DOI ALWAYS: every source entry carries a clickable URL or a doi.org link -- paywalled/gated
  is fine (link it anyway; stamp the access status). Only the truly-irreducible case (no online
  catalog presence anywhere) gets an explicit [no online source located] marker with a one-line
  justification. For claims verified by reading actual tmux source code, cite the exact file/line
  and commit/tag/version, and treat that as [VERIFIABLE] evidence (the source repository counts as a
  primary source).
- Integrity: footnote refs are contiguous from [^1], every [^N] ref has a matching definition, and
  every definition is referenced -- no gaps, no orphans.
- [INFERENCE] / [NO SOURCE] claims carry the tier tag with NO footnote ref.

Hard constraint (overrides all formatting preferences): never invent a citation to satisfy a
formatting instruction. Accuracy > completeness.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX. NEVER generate binary images.
</grounding_instructions>

## Constraints

- Public open-source package context (this repo is ai-cli-utils, a public OSS package) -- no
  personal identifiers, no proprietary/internal names in the output doc.
- Write ONLY to the designated research doc target. Do not modify any source, test, or design file.
- If you have actual tmux available in your environment, empirically TEST any candidate mechanism
  you find plausible (e.g., actually try a control-mode sequence, actually check `lock-session`'s
  server-side effect) rather than relying solely on documentation reading -- an empirical
  confirmation or refutation is stronger evidence than a documentation citation alone. Report exactly
  what you ran and its actual output.

## Output contract

Write your complete findings into the research document at the given `--write-target` path,
following its existing canonical structure (frontmatter, Steps, Rationale, Sources, Revision Log,
and the required "## Appendix: Research Prompt" section per docs/research/prompts/TEMPLATE.md,
containing this prompt verbatim). The document's final verdict must be one of exactly two shapes:
(1) "Mechanism found: <name>, verified via <evidence>, here is how the reaper should use it," or (2)
"No atomic mechanism exists (confirmed via <evidence>); recommend proceeding with the two-phase-reap
fallback, with reasoning on the appropriate N and any better alternative pattern found." Do not
return a hedge that fails to commit to one of these two shapes.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

No sidecar provenance ledger was generated for this direct research run. Load-bearing claims
instead cite exact tagged source files and line ranges inline.

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- **2026-08-28 — initial direct research run.** Live retrieval of current tmux release,
  manual, and tagged source; local binary/version inspection; source-level queue trace;
  current libtmux prior-art inspection. Runtime session creation was unavailable because
  the scoped sandbox exposed no suitable tmux socket path.

<!-- /doc:region name="run_history" -->
