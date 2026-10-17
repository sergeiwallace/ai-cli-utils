---
title: "How Claude Code identifies a session to Remote Control and ListAgents, and how to probe true reachability"
category: research
tags: [research, claude-code, remote-control, list-agents, session-restart, tmux]
status: complete
source: "claude-opus-5-5"
template_version: "research-1.2.0"
related_docs:
  - docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
delegation_provenance:
  version: 2
  contributors:
    - delegated_to: codex/research
      tier: flagship
      model: gpt-6.1-sol
      effort: high
      persona: research
      worktree: none
      session: ai-cli-restart-idle-cc-sessions-safely-nyz8
---

# How Claude Code identifies a session to Remote Control and ListAgents, and how to probe true reachability

## Founding Ask Coverage

`founding_ask_ref`: `canonical_root_json`: `{"blob_oid":"bee6d633307dfebd3b496531813afbf335f3f9a3","capture_commit":"e869869b929551fb4faa4e9c0584ac6a0a7803cb","content_sha256":"0548b7e69f6e27353a1c56ce9b14c312ed19b3c2a3ddb3689a580640f8d9a9bf","entry_ids":["A001","A002"],"generation":2,"path":"docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md","previous_ref_sha256":"50b9450fc8bfb71d4df4706ba470679c9d3b4153f5c3c7af3601dbdd48932d72","repository":"ai-harness","schema_version":1,"task_id":"AI-CLI-restart-idle-cc-sessions-safely-nyz8"}`; `founding_ask_ref_sha256`: `fa12347e1984ca65de5716c748bf5f20e424851b4c54d2468116628648d4d889`. The record lives in the operator's private harness repository; this public doc quotes only the generic parts of it.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| A001 (original ask) | The identity, visibility and renaming questions are answered in [Findings](#findings) sections 1 to 3. The state detection the restart gates need (idle, background work, compaction, context %, the update notice) is in section 4. The probe is in [Recommended probe](#recommended-probe). The restart mechanism, gates, triggers and rollout are planned in the linked plan doc, not here. |
| A002 (follow-up) | Out of scope for this doc. Its feedback report, issue filing and issue comments concern the mobile app prompt box, and its delegation instructions concern how the work was handed off. |

**Status:** complete

**Created:** 2026-10-17

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Context](#context)
- [Executive Summary](#executive-summary)
- [Method](#method)
- [Findings](#findings)
  - [1. Local session identity and naming](#1-local-session-identity-and-naming)
  - [2. What ListAgents and SendMessage key on](#2-what-listagents-and-sendmessage-key-on)
  - [3. Why /rc can say connected while peers cannot find the session](#3-why-rc-can-say-connected-while-peers-cannot-find-the-session)
  - [4. Detecting session state from outside the process](#4-detecting-session-state-from-outside-the-process)
  - [5. What a restart does to identity](#5-what-a-restart-does-to-identity)
  - [6. Existing restart machinery in this package](#6-existing-restart-machinery-in-this-package)
- [Recommended probe](#recommended-probe)
- [Decisions the build depends on](#decisions-the-build-depends-on)
- [Gaps, blindspots and emergent findings](#gaps-blindspots-and-emergent-findings)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

A long-running Claude Code session inside an `ai c` tmux pane can lose its Remote Control
(`/rc`) connection, or stay connected under a name nobody expects, so the mobile app's Code tab
and other sessions' `ListAgents` can no longer find it. The same sessions also need a restart to
load a Claude Code update that installed in the background. This research answers what identity
each surface uses, what an outside process can observe, and which probe tells true reachability
apart from what the `/rc` status screen claims. It feeds the plan for `ai --restart-session`.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Executive Summary

Claude Code keeps three identities per interactive session, and each surface reads a different
one. The conversation is the `sessionId` UUID. Local peers address the session by its registry
`name` over a Unix socket. Remote Control publishes a separate `bridgeSessionId` and a remote
title. [VERIFIABLE][^9] (local observation, `~/.claude/sessions/<pid>.json`)

The `/rc` screen and the registry's `bridgeSessionId` both describe what the local process
believes about its bridge, so neither proves the server or the mobile app can see it. A local
`ListAgents` view is cheap and exact for same-host peers: a headless `claude -p` with only the
`ListAgents` tool listed all 14 live sessions on the test host by name, status and tmux pane.
That view showed no cloud or bridge rows, so it says nothing about mobile visibility.
[VERIFIABLE][^9] (local observation, headless probe, 2026-10-17)

The probe that can be built today is tiered. A cheap local tier gives a definite answer. The
remote-registration tier reads the bridge id and name source. An optional remote-observer
command answers from the other side of the bridge. Without a configured observer, the remote
verdict stays "registered, not observed", and the restart trigger treats a missing bridge id or
a non-user name source as lost reachability.

The update notice is not stored per session. The reliable signal is the registry's running
`version` against the version the native launcher now points to; the rendered
`✓ Update installed · Restart to apply` text is corroboration from `tmux capture-pane`.
[VERIFIABLE][^9] (local observation, bundle strings and registry)

## Method

The host was a Linux workstation running Claude Code 2.1.302 with 14 interactive sessions, each
in its own `ai c` tmux pane. A read-only Codex research leg read the Claude Code documentation,
the installed binary's strings and the local state files. A second read-only leg mapped this
package's supervisor, registry, probes and configuration. Neither leg could see host processes
or the tmux server from its sandbox, so every process, socket and pane observation below was
re-run by the coordinator on the host. Nothing was signalled, injected or restarted while
gathering this evidence.

Host commands that carry the load-bearing observations:

```bash
jq 'with_entries(.value |= type)' ~/.claude/sessions/<pid>.json    # registry schema
claude agents --json                                               # local session list
claude -p "Call the ListAgents tool once. Then print its raw output verbatim and nothing else." \
  --model haiku --allowedTools ListAgents                          # headless peer view
tmux capture-pane -p -t <pane> -S -12                              # rendered footer
strings -n 8 "$(readlink -f "$(command -v claude)")"               # bundle text
```

## Findings

### 1. Local session identity and naming

Each live interactive session writes `~/.claude/sessions/<pid>.json`. The fields that matter
here are `pid`, `procStart`, `sessionId`, `name`, `nameSource`, `status`, `statusUpdatedAt`,
`version`, `tmux`, `messagingSocketPath` and `bridgeSessionId`. One record, with values
replaced by placeholders:

```json
{"version": "2.1.302", "kind": "interactive", "tmux": "session-1:@2.%2",
 "messagingSocketPath": "/run/user/<uid>/cc-socks/<pid>.sock", "name": "session-1",
 "nameSource": "user", "status": "idle", "bridgeSessionId": "session_<opaque>"}
```

[VERIFIABLE][^9] (local observation, `~/.claude/sessions/<pid>.json`, 2026-10-17)

The `tmux` field binds the record to a pane as `<tmux session>:@<window>.%<pane>`, so a supervisor
can join a tmux pane to its Claude Code process without guessing from the process tree.
[VERIFIABLE][^9] (local observation, same record)

`status` takes at least `idle`, `busy` and `shell` on this host. `version` is the build the
process is running, not the one installed. [VERIFIABLE][^9] (local observation, registry records
and `claude agents --json`)

The custom title is stored twice. The transcript carries
`{"type":"custom-title","customTitle":"<title>"}` from `/rename` or `--name`, and the registry
`name` mirrors it with `nameSource: "user"`. [VERIFIABLE][^9] (local observation,
`~/.claude/projects/<slug>/<sessionId>.jsonl`)

The remote title follows a documented priority: an explicit name first, then `/rename`, then
conversation history, then a generated name. The documentation says "The remote session title is
chosen in this order". A random-looking remote name is therefore the documented fallback when
the explicit name did not reach the bridge registration. [VERIFIABLE][^1]

Claude Code offers four ways to set that name: `--name`, `--remote-control "<title>"`,
`/remote-control <title>` and `/rename <title>`. Server mode adds `claude remote-control
--name`, and its help documents a `CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX` variable.
[VERIFIABLE][^1] (also local `claude remote-control --help`)

No documented interface renames an already-registered remote session from outside the process.
A relaunch with `--resume <id> --name <title>`, which is what the `ai c` supervisor already does,
is the automatable way to put the expected title back. [INFERENCE] (no rename API found in the
docs or `--help`; the supervisor's launch line is in section 6)

### 2. What ListAgents and SendMessage key on

`ListAgents` merges several sources: local sessions that bind an inbox socket, in-process agents,
teammates, and eligible server-backed sessions. The documentation says "A session appears only
when it binds an inbox socket." Same-host messages travel over the Unix socket; cross-machine
messages travel through Anthropic's service. [VERIFIABLE][^2]

The bundle assembles peers from three transports, `uds`, `cloud` and `bridge`, and carries
`localListFailed`, `bridgeWalkFailed` and `listTruncated` flags. A listing can therefore be
silently partial on the remote side while the local part is complete. [VERIFIABLE][^9] (local
observation, bundle strings around the peer-listing function)

`SendMessage` addresses by `name`, adding a short `[ref]` only to break a tie. Two live rows with
the same name, which happens after a restart if a stale registration lingers, make the bare name
ambiguous. [VERIFIABLE][^2] [VERIFIABLE][^7]

A headless peer reproduces the local view exactly. The probe above printed one row per session,
for example `session-1 [034f38] · interactive · idle · tmux session-1:@2.%2 · started 20h ago`,
and the 14 rows matched `claude agents --json` and the registry one for one. [VERIFIABLE][^9] (local
observation, 2026-10-17)

`claude agents --json` returns the same population with `pid`, `sessionId`, `name`, `status` and
`kind`, needs no TTY, and makes no model call. It is the cheaper way to read the local view.
[VERIFIABLE][^9] (local observation and `claude agents --help`)

### 3. Why /rc can say connected while peers cannot find the session

The bundle writes the registry's `bridgeSessionId` when a bridge handle attaches and writes
`null` when it detaches. It also exports `CLAUDE_CODE_BRIDGE_SESSION_ID` to the process
environment. So `bridgeSessionId` is the local process's belief about its bridge, the same belief
the `/rc` screen renders. [VERIFIABLE][^9] (local observation, bundle strings: `bridgeSessionId:e`
written through the registry update helper, and the `i??null` clear path)

That belief can be true while peers still miss the session, for reasons the evidence names:

- The remote listing is paginated, and the bundle's own warning text says "sessions beyond the
  first pages are missing". [VERIFIABLE][^9] (local observation, bundle strings)
- A failed registry rename leaves peers showing the old name; the bundle warns "other sessions
  may keep showing the old name". [VERIFIABLE][^9] (local observation, bundle strings)
- Same-name registrations can pile up after restarts, so the expected title points at a dead
  row. [VERIFIABLE][^7]
- A connected session can stop receiving mobile messages without any status change; one report
  says "no turn ever starts". [VERIFIABLE][^6]

The `/rc` status screen therefore cannot be the reachability test. It reports the local side of
a connection whose failure modes are on the server side. [INFERENCE] (from the four causes
above)

### 4. Detecting session state from outside the process

The restart gates need five observations. Each has a primary signal and a corroborating one.

| State | Primary signal | Corroboration | Unknown means |
|---|---|---|---|
| Live ai session | Registry record whose `pid` is alive with matching `procStart`, whose `tmux` field names the pane, in a pane whose start command is the `ai c` session script | `claude agents --json` row | refuse |
| In-flight turn | Registry `status` is not `idle` | Transcript tail has an unanswered tool call or no end-of-turn record; footer shows a spinner | refuse |
| Background shells and subagents | Transcript records of background launches (`"status":"async_launched"`, `backgroundTaskId`) with no matching completion, and `pendingBackgroundAgentCount` above zero | Stop-hook `background_tasks` arrays when a hook writes them; live descendant processes of the Claude Code pid that are not its MCP servers | refuse |
| Compaction | Latest transcript `{"type":"system","subtype":"compact_boundary"}` with `compactMetadata.trigger` and `postTokens` | `isCompactSummary:true` record; `PostCompact` and `SessionStart` (`source: "compact"`) hooks | treat as not compacted |
| Context % | Latest main-chain `usage`: `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` over the model's window | A statusline cache for that `session_id`, if fresh | treat as over threshold |

Evidence for the table: the registry statuses and transcript records are local observations on
the test host [VERIFIABLE]; hook payloads are in the hooks reference [VERIFIABLE][^3]; the
context formula is in the statusline reference, which also warns that usage can be null right
after a compaction and that historical usage must not be summed [VERIFIABLE][^4].

The update notice comes from the background updater's in-memory status (`status==="success"`
renders `Update installed · Restart to apply`). No per-session file records it.
[VERIFIABLE][^9] (local observation, bundle strings) The outside signal is that the registry
`version` is older than the version the native launcher links to:
`readlink ~/.local/bin/claude` ends in `.../versions/<version>`. On the test host every session
read `2.1.302` against an installed `2.1.302`, and a separate log on the same host shows the same
comparison finding `running=2.1.301 installed=2.1.302` the previous night. [VERIFIABLE][^9] (local
observation) `tmux capture-pane -p` of the pane footer corroborates it when the text is
rendered. [HEURISTIC]

The footer of an idle pane on the test host showed no Remote Control indicator at all and ended
with `← 1 agent`. Footer text is a rendering, clipped by width, and its meaning is not documented,
so it is corroboration only and never a gate on its own. [VERIFIABLE][^9] (local observation)
[INFERENCE]

Process inspection has a namespace trap. A sandboxed observer saw the registry PIDs as dead
because its `/proc` was a different PID namespace, which the registry's `pidDomain` field
records. A probe must treat "cannot see the process" as unknown, never as dead. [VERIFIABLE][^9]
(local observation, research leg sandbox against host)

### 5. What a restart does to identity

`--resume` "reconnects to the Remote Control session recorded in that conversation", and resume
can fail while local work continues. A relaunch can therefore come back under the old bridge
registration, a new one, or none. [VERIFIABLE][^1]

The custom title survives because the transcript keeps `customTitle` and the supervisor passes
`--name` again. Whether the remote title follows depends on the bridge registration that resume
gets back, which nobody observed here because nothing was restarted. [VERIFIABLE][^5]
[NO SOURCE] (post-restart remote title on 2.1.302 not observed)

So a restart command must verify after the fact: the new process is in the same pane, has the
same `sessionId`, has `name` equal to the expected title with `nameSource: "user"`, and has a
non-null `bridgeSessionId`. [INFERENCE] (from the identity fields in section 1)

### 6. Existing restart machinery in this package

The `ai c` supervisor relaunches the agent whenever its child body exits with any status other
than 77 or 79, and launches Claude Code with `--resume "$session_id" --name "$ai_name"`. It stops
relaunching an agent that returns within three seconds, or after three consecutive runs that
each lasted under 60 seconds. [VERIFIABLE][^8] (`src/ai_cli/session_script.py`, the
`_child_status == 77 || _child_status == 79` branch and the launch line)

So `SIGTERM` to the exact Claude Code pid, never to the pane leader, is a restart: the pane
survives and the supervisor relaunches the conversation. Signalling the supervisor tears the
session down instead. [INFERENCE] (from the branch above; the plan verifies it end to end)

`process_probe` already captures a process's birth identity (`capture_identity`,
`start_time_match`), which is what a kill needs to avoid hitting a reused pid. Its `end_process`
signals a whole group or tree, which is too broad for this. [VERIFIABLE][^8]
(`src/ai_cli/process_probe.py`)

`session_adopt.live_sessions()` already reads `~/.claude/sessions/*.json` with a liveness check.
[VERIFIABLE][^8] (`src/ai_cli/session_adopt.py`)

Configuration is TOML under the XDG config directory, read at call time, and the package already
reaches private tooling through drop-in command files under `<config dir>/<feature>.d/*.toml`.
[VERIFIABLE][^8] (`src/ai_cli/config.py`; the `session-migrate.d` precedent)

The operator's private harness has a prompt-injection library with a command-line entry point.
It can type a slash command into a named pane and verify it landed, and it already has a
session-restart use case that injects `/exit` and watches for the replacement pid. The same
harness runs an update-restart pass that restarts idle sessions onto an installed update through
that `/exit` path. Neither checks a context threshold or reachability. [VERIFIABLE][^9] (local
observation, harness source and its update-restart log)

## Recommended probe

The probe returns one verdict per session: `REACHABLE`, `LOCAL_ONLY`, `UNREACHABLE` or
`UNKNOWN`, plus the evidence for it. A supervisor process runs it on the session's own host.

1. Resolve the target to exactly one registry record: by custom title (`name` with
   `nameSource: "user"`), by `sessionId`, or by tmux pane through the `tmux` field. More than
   one match is `UNKNOWN`, never a pick.
2. Local tier. The pid is alive and its start time matches `procStart`; the socket at
   `messagingSocketPath` exists and accepts a connection (connect, then close, sending nothing);
   `claude agents --json` lists the same `sessionId` under the expected name. Any failure is
   `UNREACHABLE`. An observation the probe cannot make, such as a foreign PID namespace, is
   `UNKNOWN`.
3. Remote-registration tier. `bridgeSessionId` is non-null and `name` equals the expected title
   with `nameSource: "user"`. A null bridge id or a non-user name source is `UNREACHABLE` for the
   remote surfaces.
4. Remote-observer tier, optional. If the operator configures an observer command, such as a
   `ListAgents` run on another machine, run it and require one row under the expected title.
   Without it, a session that passes steps 2 and 3 is `LOCAL_ONLY`: locally reachable, registered
   with the bridge, not observed from the other side.
5. Before any action on the verdict, re-read the registry `status` and the transcript tail. A
   probe is passive, and a message can start a turn between the probe and the action.

The plan doc decides how each verdict feeds the restart trigger.

## Decisions the build depends on

These are inputs to the plan's Decisions, recorded there with options.

- Use the registry, not the process tree, as the identity source, and join it to the pane
  through its `tmux` field.
- Detect "update installed" by version comparison, with the footer text as corroboration.
- Treat `/rc`'s own report as insufficient; use the tiered probe above.
- Restore the expected remote title by relaunching with `--name`, the only automatable route
  found.
- Make "unknown" refuse every restart gate.

## Gaps, blindspots and emergent findings

No probe available on the session's own host proves what the mobile app shows. The headless
`ListAgents` view on the test host listed only local socket peers and no cloud or bridge rows,
so it cannot stand in for the mobile view. Closing this needs an observer on the far side of the
bridge, which is why the probe has an optional observer tier. [VERIFIABLE][^9] (local observation)
[INFERENCE]

The background-work signal is the weakest of the gates. Transcript launch records and the
pending-agent counter are good evidence, but no single file is documented as a complete registry
of running background tasks. The plan should refuse on any positive signal and on missing
evidence. [NO SOURCE] (complete background-task registry)

A cross-session message wakes an idle receiver and starts a turn there. [VERIFIABLE][^2]
A round-trip message probe is therefore not passive, and it would itself defeat the idle gate
for a while.

Restarting drops in-process background shells and subagents. The gates exist for that reason;
the harness's update-restart pass makes the same point in its own docstring. [VERIFIABLE][^9] (local
observation)

Two restarters on one host, this package's command and the harness pass, could race for the same
session. The plan needs a single per-session lock or a single act path. [INFERENCE]

Windows was not examined; no Windows host was available, and process signalling semantics differ
there. [NO SOURCE]

## Sources

[^1]: Anthropic. (2026). [Remote Control](https://code.claude.com/docs/en/remote-control). Claude Code documentation. Verified accessible 2026-10-17.
[^2]: Anthropic. (2026). [Cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging). Claude Code documentation. Verified accessible 2026-10-17.
[^3]: Anthropic. (2026). [Hooks reference](https://code.claude.com/docs/en/hooks). Claude Code documentation. Verified accessible 2026-10-17.
[^4]: Anthropic. (2026). [Status line configuration](https://code.claude.com/docs/en/statusline). Claude Code documentation. Verified accessible 2026-10-17.
[^5]: Anthropic. (2026). [Manage sessions](https://code.claude.com/docs/en/sessions). Claude Code documentation. Verified accessible 2026-10-17.
[^6]: GitHub user report. (2026). [Mobile delivery failure while Remote Control shows connected](https://github.com/anthropics/claude-code/issues/85187). anthropics/claude-code issue tracker. Firsthand report; reproduction on 2.1.302 not established. Verified accessible 2026-10-17.
[^7]: GitHub user report. (2026). [Same-name Remote Control registrations accumulate](https://github.com/anthropics/claude-code/issues/81299). anthropics/claude-code issue tracker. Reported on 2.1.219. Verified accessible 2026-10-17.
[^8]: ai-cli-utils contributors. (2026). [ai-cli-utils source at bd49eb4d](https://github.com/user/ai-cli-utils/tree/bd49eb4df6c373ae45ebb204802b922e8be60859). Read locally 2026-10-17; the file named beside each claim is the evidence.
[^9]: Anthropic. (2026). [Claude Code setup](https://code.claude.com/docs/en/setup). Claude Code documentation, verified accessible 2026-10-17. The field values cited with this note were observed on the test host running Claude Code 2.1.302 on 2026-10-17 (registry files, transcripts, bundle strings, `claude agents --json`, a headless `ListAgents` run); no online source located for those values, so the page links the product and install layout they come from.

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none yet; auto-populated by verify_and_remediate node)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** none (orchestrator-owned research; the prompts were two read-only Codex legs)
**Model:** gpt-6.1-sol (Codex research, flagship, high effort)
**Date:** 2026-10-17

````text
Leg 1 objective (identity and reachability): establish, with evidence from this host, how Claude
Code identifies a running interactive session to Remote Control (/rc, the mobile "Code" tab) and
to the cross-session ListAgents / SendMessage tools, and specify a probe that a separate process
can run to decide whether a given session is truly reachable under its expected custom title.
Also establish how an outside process can detect four session states: the update notice
"✓ Update installed · Restart to apply", an in-flight turn, running background shells or
subagents, and "just past an auto-compaction" or the session's current context percentage.
Questions: (1) where Claude Code records per-session identity locally and what the remote title
is; can it be renamed programmatically; (2) what ListAgents lists and what SendMessage addresses
by, and why a connected session can be missing from a peer's list; (3) rank reachability probes;
(4) state detection from outside the process; (5) what a relaunch with --resume does to Remote
Control identity. Read-only; no process signals, no key sends; secrets never printed.

Leg 2 objective (package code map): map the existing code a new `ai --restart-session [ID]`
command must build on: the supervisor and its relaunch conditions, the session registries and
target resolution, existing probes for liveness and idleness, prompt injection inside and outside
the package, context percentage computation, configuration loading and free short flags, test
fixtures, and related restart work. Cite file:line for every claim. Read-only.

Both prompts carried the Tier 3 founding-ask carrier, the grounding instructions block and the
scope-note and independent-exploration blocks from the canonical research prompt template.
````

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

| Contributor | Role | What it produced |
|---|---|---|
| codex/research, gpt-6.1-sol, high | Read-only research leg 1 | Identity, listing and state-detection evidence (sandboxed; host process and tmux observations re-run by the coordinator) |
| codex/research, gpt-6.1-sol, high | Read-only research leg 2 | Package code map |
| claude/architect, claude-opus-5-5, medium | Coordinator | Host re-verification, headless ListAgents probe, synthesis and authorship |

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-10-17: first complete version, orchestrator-authored from two read-only research legs and
  host observations.

<!-- /doc:region name="run_history" -->
