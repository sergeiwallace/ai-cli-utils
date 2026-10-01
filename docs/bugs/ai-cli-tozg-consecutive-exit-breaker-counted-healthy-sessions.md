---
title: "A 'consecutive agent exits' breaker was cumulative, so the third healthy exit stopped auto-restart for good"
category: bugs
tags: [session, supervisor, restart, circuit-breaker, remote, tmux, state-file]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/ai-cli-1rk1-pi-launch-missing-provider-flag.md
  - docs/bugs/ai-cli-aob3-child-exit-status-discarded-when-already-reaped.md
  - docs/bugs/ai-cli-2139-session-exit-leaves-stopped-process.md
---

# A "consecutive agent exits" breaker was cumulative, so the third healthy exit stopped auto-restart for good

The generated session script relaunches the agent after every exit. That is what lets an agent exit
*deliberately* and come straight back — re-authenticating from inside the agent is the everyday case:
the agent asks the user to log in again, then exits, expecting the session to relaunch it.

A circuit breaker sits in that loop so a supervisor which is merely cycling — relaunching an agent
that cannot host a session and returns at once — gives up instead of spinning. Its message says it
counts **three consecutive agent exits**. It counted every agent exit for the whole life of the
supervisor, and nothing ever cleared the count. The third exit therefore stopped auto-restart
permanently, no matter how healthy those three runs were or how many hours separated them.

On a local session that ends the session. On a **remote** one the launch loop hands the pane back to
a shell instead of exiting, so the user is dropped into a bare shell on the remote host, inside the
tmux pane, with a banner that told them only how to close the session and nothing about how to get
the agent back.

## Symptoms

Reported as two complaints about one sequence: re-authenticating inside the agent forces an exit, the
session does not come back, and the user lands in a shell on the remote machine inside tmux with no
evident way out.

Reproduced against the unmodified template with a stand-in agent that sleeps and exits `0`:

    === supervisor iteration 1 (fake agent lives 4s) ===
        child exit status: 0   counter: 1
    === supervisor iteration 2 (fake agent lives 4s) ===
        child exit status: 0   counter: 2
    === supervisor iteration 3 (fake agent lives 4s) ===
    AI CLI keeps failing to start (3 consecutive agent exits) — stopping. Run 'ai c' to retry.
    Session ended. Exit shell to close tmux session.
        child exit status: 79   counter: 3
    agent launches observed: 3

Three runs, each four seconds long, each exiting `0` — none of them a failure to start — and the
breaker fires with a message about consecutive failures.

## Causal mechanism

The count lived in a per-session state file and was only ever incremented:

    agent_exit_count=$(cat "$agent_exit_count_file" 2>/dev/null || echo 0)
    [[ "$agent_exit_count" =~ ^[0-9]+$ ]] || agent_exit_count=0
    agent_exit_count=$((agent_exit_count + 1))
    printf '%s\n' "$agent_exit_count" > "$agent_exit_count_file"
    if (( agent_exit_count >= 3 )); then ... break; fi

The only two places that cleared it were the supervisor's own startup and its exit cleanup, so
"consecutive" was really "since this supervisor started". A supervisor that hosts a session for days
accumulates one count per agent exit, and the third one is terminal.

The file is genuinely needed — each replacement child body is a fresh shell, so the count cannot live
in a shell variable — but a counter that is only ever written upwards is not a measure of anything
consecutive. The missing half was the reset.

### Why the breaker could never fire for the case it was added for

Worse than useless: actively inverted. Immediately below the breaker, the same loop already stops the
session on the **first** short run:

    elapsed=$_exit_elapsed
    if (( elapsed < 3 )); then
      echo "AI CLI exited too quickly ($elapsed s) — stopping..."
      break
    fi

That branch is unconditional, so any run under three seconds breaks before a second iteration can
happen. The breaker therefore only ever accumulated counts from runs that lasted **three seconds or
more** — that is, from runs the template itself had already classified as not-too-quick. Its only
reachable effect was to stop sessions that were working.

### Why a reset at the three-second boundary would be a worse bug

The obvious patch — clear the count whenever the run was not "too quick" — trades this defect for a
harder one. An agent that reliably fails five seconds into startup passes the three-second check,
would clear the count on every attempt, and would be relaunched forever.

The two guards answer different questions and so need different thresholds. "Could anything have
started?" is a three-second question. "Was that a session, or is this supervisor cycling?" is a
question about minutes. `HEALTHY_SESSION_SECONDS` is the second threshold, deliberately well clear of
the first.

## The remote recovery shell

A remote session's launch loop ends with a shell rather than an exit, so the user is not stranded
without one inside a pane reached over the network. Its banner read:

    Session ended. Exit shell to close tmux session.

One route, and not the one people reach for. The restart route was undiscoverable, and the banner
invited exactly the wrong inference — that exiting the shell brings the agent back. It does the
opposite: the status that exit returns (`79`) is one of the two statuses that tell the supervisor to
tear the session down.

`ai c <name>` cannot be the advice either, because run from inside the pane it would attach a live
session to itself. The supervisor, though, already relaunches any child body that returns neither of
its stop statuses, so handing `78` back is an in-place restart over a contract that already exists.
The banner now names both routes and the restart one is real:

    Session ended — the agent will not be relaunched on its own.
      exit 78   relaunch the agent in this pane
      exit      close this session

The relaunch clears the consecutive-exit count on the way out, because asking for a restart deserves
the same fresh budget a new supervisor would give; without that the relaunched agent would inherit a
tripped breaker and stop again immediately.

## What was ruled out

Two leads were measured and rejected rather than carried forward.

**An unexpanded `$0` reaching tmux as a session name.** A live process table contained
`tmux attach-session -d -t $0`, with `$0` literal. That is not a lost shell expansion: `$0` is tmux's
own *opaque session id* format, and the launcher asks for exactly that with
`new-session -P -F '#{session_id}'`. `tmux_ownership._SESSION_ID_RE` is `^\$\d+$` and the launcher
treats anything else as a tmux that does not expand formats. Confirmed on the affected host, where
three sessions showed ids `$0`, `$1`, `$2` and three attach clients showed `-t $0`, `-t $1`, `-t $2`,
matching pairwise.

**Immortal heartbeat tickers keeping a dead session alive.** Four detached tickers were running with
PPID 1 at ages of four to five days, publishing heartbeats for sessions that no longer existed, and
their recorded supervisor pids were all confirmed gone. The stand-down check those tickers need
already exists and is correct in current code, and it was present in the on-disk script at the time
of measurement — so these were legacy processes started before it landed, not a live defect. Not
fixed here; nothing in this change affects them.

A **client/server tmux version mismatch** on the affected host is real and was confirmed — a
PATH-resolved `tmux 3.2a` reports `server exited unexpectedly` against a demonstrably live `3.7c`
server holding the socket — but it is a separate provisioning defect on that host, not part of this
one, and it is not reachable from the launch loop under test.

## The RED tests

`tests/test_session_restart_circuit_breaker.py` drives the real generated template with a real shell
and a stand-in agent, and asserts behaviour — launch counts and the status the child body hands its
supervisor — never the generated text.

- `test_given_repeated_healthy_agent_exits_when_each_hosted_a_session_then_restarts_continue` —
  four healthy exits must produce four launches. RED before the fix with
  `statuses=[0, 0, 79]` and three launches.
- `test_given_a_stopped_remote_session_when_the_pane_is_handed_back_then_both_routes_are_named` and
  `test_given_the_recovery_shell_when_the_human_asks_for_a_relaunch_then_the_supervisor_gets_one` —
  RED before the fix, the latter with `79 != 78`.
- `test_given_an_agent_that_cannot_host_a_session_when_it_keeps_exiting_then_the_session_stops` is
  the negative constraint: durable restarts must not become a spin loop. GREEN both before and
  after, deliberately.
- `test_given_the_harness_when_it_builds_a_path_then_the_real_agent_cannot_leak_in` guards the
  harness. It earns its place: an earlier draft of this harness ran the operator's **real** agent,
  because the baked interpreter was zsh and a zsh child body re-reads `~/.zshenv`, which restored the
  real PATH. Every behavioural assertion above would have been vacuous.

Measured: **3 failed, 2 passed** before the fix; **5 passed** after.

## Prevention lesson

**A counter that is only ever incremented cannot measure anything consecutive, and the word in the
message is not the behaviour.** The breaker's own text said "consecutive" from the day it was
written; the reset that would have made that true was never there, and no test asserted the word
against the behaviour. A guard with no path back to its safe state is a latch, and a latch on the
restart path eventually fires on a healthy session.

Two corollaries:

- **Two guards on the same decision need two thresholds, or one of them is unreachable.** Both the
  breaker and the "exited too quickly" stop keyed off three seconds, which made the breaker dead for
  its stated purpose and live only against working sessions. Sharing a threshold hid that for weeks.
- **When a stop path hands control back to a human, what it prints is the whole interface.** The
  recovery shell's one-line banner was the only instruction available at the moment the user most
  needed one, and it documented the route nobody wanted while staying silent about the route
  everybody tried.
