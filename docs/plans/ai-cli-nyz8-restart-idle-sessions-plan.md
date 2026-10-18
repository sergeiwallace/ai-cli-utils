---
title: "ai --restart-session: restart an idle Claude Code session safely, by hand or on a trigger"
category: plan
tags: [plan, session-restart, remote-control, tmux, supervisor]
status: draft
source: "claude-opus-5-5"
template_version: "plan-1.0.0"
task: AI-CLI-nyz8
related_docs:
  - docs/research/claude-code-session-identity-and-reachability.md
delegation_provenance:
  version: 2
  contributors: []
---

<!-- DOC-LINK CONVENTION: link other docs as PLAIN RELATIVE Markdown links with plain link text,
     NO backticks. Never use the vscode://file scheme. -->

# ai --restart-session: restart an idle Claude Code session safely, by hand or on a trigger

## Founding Ask Coverage

`founding_ask_ref`: `canonical_root_json`: `{"blob_oid":"bee6d633307dfebd3b496531813afbf335f3f9a3","capture_commit":"e869869b929551fb4faa4e9c0584ac6a0a7803cb","content_sha256":"0548b7e69f6e27353a1c56ce9b14c312ed19b3c2a3ddb3689a580640f8d9a9bf","entry_ids":["A001","A002"],"generation":2,"path":"docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md","previous_ref_sha256":"50b9450fc8bfb71d4df4706ba470679c9d3b4153f5c3c7af3601dbdd48932d72","repository":"ai-harness","schema_version":1,"task_id":"AI-CLI-restart-idle-cc-sessions-safely-nyz8"}`; `founding_ask_ref_sha256`: `fa12347e1984ca65de5716c748bf5f20e424851b4c54d2468116628648d4d889`. The record lives in the operator's private harness repository. Session and host names in it are generalised here to `session-1`, `host-a` and `host-b`.

Coverage index (non-authoritative; the raw record governs):

| Authoritative raw entry | Coverage and disposition |
|---|---|
| A001: restart every session so it reconnects over `/rc`, picks up the CLI update and clears the update notice | T-02 (the command), T-05 (rollout on both hosts) |
| A001: "auto-kill the cc session inside the tmux process (leave the tmux process so ... auto-restart system fires)" | D-1 (kill in pane is the primary mechanism), T-02 |
| A001: prompt injection of `/exit` through the shared library | D-1 keeps it as the fallback mechanism, T-02 AC-2.9 |
| A001: "build this out as reusable infra", "`ai --restart-session <ai-session-id/custom-title>`", "whatever short option flag would be available" | D-6, T-02 |
| A001: "not pass an argument value after `--restart-session`", "`ai c 1 -R -p myproject --restart-session`", "supports option without arg and explicit ai session ID" | D-2, T-02 AC-2.2 and AC-2.3 |
| A001: "validate / check if it's an active ai session process first" | D-3, T-02 AC-2.4 |
| A001: "no agents or background shell processes running", "completely idle with no active work" | D-3, T-02 AC-2.5 and AC-2.6 |
| A001: "first either be at auto-compaction ... or under 20% context window (should be configurable %)" | D-4, T-02 AC-2.7 |
| A001: "auto-invoke / prompt inject & auto-submit (via shared library for this) `/save-state`", "after auto-compacting and entering new context window" | D-5, T-03 |
| A001: "optionally execute the restart-session command automatically", "configurable timers and other logic" | D-7, T-04 |
| A001: trigger on "`✓ Update installed · Restart to apply`" | D-8 (trigger 1), T-04 |
| A001: trigger when `/rc` "is no longer de facto connected and visible", for the mobile app or "via ListAgents / SendMessage", "under the expected cc session custom title and not some new random string" | Research doc sections 1 to 3; D-8 (trigger 2), T-04 |
| A001: "what controls they give us for automatically renaming remote session processes" ("idk if we can automate this") | Research doc section 1: no rename API; the relaunch passes `--name`, and T-02 AC-2.8 verifies the title came back |
| A001: "tell [the session on the other host] ... they can do the same for themselves" | T-05 AC-5.4 |
| A001: "merge it in and auto-reap worktree and /sync-git" | Every task lands by PR; the coordinator then syncs the main tree and reaps the task worktree, and T-05 AC-5.6 records each outcome |
| A001/A002: create and assign the epic and sub-tasks, hand off, delegate the run, start the goal | Owned by the launching sessions outside this repository; assignment, handoff, delegation and goal activation remain unverified here until execution receipts are attached. A run prompt alone is not proof of execution |
| A002: `/report-feedback`, SendFeedback, a GitHub issue and related-issue comments | T-06 tracks the launching session's Remote Control report, submission, filed issue and related-issue review with receipts; delivery remains unverified until those receipts exist |

**Status:** DRAFT

**Created:** 2026-10-17

**Task:** AI-CLI-nyz8 (epic); T-06 adds tracked feedback delivery under the launching session.

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section.
  4. Append AI response as > **AI Response Round N:** below user feedback.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log.
-->

<!-- doc:region name="overview" kind="replaceable" -->

## Table of Contents

- [Overview](#overview)
- [Existing behaviour this builds on](#existing-behaviour-this-builds-on)
- [Configuration](#configuration)
- [Decisions](#decisions)
  - [D-1: Restart mechanism](#d-1)
  - [D-2: How a target is named](#d-2)
  - [D-3: What counts as a live, idle session](#d-3)
  - [D-4: The context gate](#d-4)
  - [D-5: How the pre-step reaches the shared injection library](#d-5)
  - [D-6: Command shape and short flags](#d-6)
  - [D-7: What runs the automatic triggers](#d-7)
  - [D-8: Trigger definitions](#d-8)
  - [D-9: Coexisting with the harness's update-restart pass](#d-9)
  - [D-10: Making sure the relaunch resumes the same conversation](#d-10)
  - [D-11: Restarting for a failure no probe on the host can see](#d-11)
- [Open Questions](#open-questions)
- [Task Breakdown](#task-breakdown)
  - [T-01: Research (AI-CLI-nyz8.1)](#t-01-research-ai-cli-nyz81)
  - [T-02: The restart command (AI-CLI-nyz8.2)](#t-02-the-restart-command-ai-cli-nyz82)
  - [T-03: The save-state pre-step (AI-CLI-nyz8.3)](#t-03-the-save-state-pre-step-ai-cli-nyz83)
  - [T-04: Automatic triggers (AI-CLI-nyz8.4)](#t-04-automatic-triggers-ai-cli-nyz84)
  - [T-05: Rollout (AI-CLI-nyz8.5)](#t-05-rollout-ai-cli-nyz85)
  - [T-06: Feedback delivery](#t-06-feedback-delivery)
- [Verification](#verification)
- [Risks](#risks)
- [Feedback Rounds](#feedback-rounds)
- [Approval Log](#approval-log)

## Overview

A Claude Code session that has run for a long time inside an `ai c` tmux pane needs a restart for
two reasons: to load a Claude Code update that installed in the background, and to get its Remote
Control registration and peer visibility back when they have quietly gone. A restart is only safe
when the session is doing nothing, because it drops in-process background shells and subagents.

This plan adds `ai --restart-session [TARGET]`. It validates that the target is a live session
the `ai c` supervisor owns, refuses unless the session is completely idle, and refuses unless its
context is either just past an auto-compaction or under a configurable threshold (default 20%).
When the context gate fails, an optional pre-step types `/save-state` into the session through a
configured injection command and waits for the compaction that follows. The restart itself sends
`SIGTERM` to the one Claude Code process in the pane; the supervisor that owns the pane relaunches
it with `--resume <session id> --name <title>`. Before acting, the command checks that the
replacement child will resume this exact conversation (D-10) and that Remote Control starts
at launch. After the relaunch it checks the conversation, the title, the messaging socket, the
local `ListAgents` view, the bridge registration and, where one is configured, a remote
observer. It reports the local, peer and mobile verdicts separately and never reports a surface
it could not observe as verified.

An automatic mode runs the same command when a trigger holds: the session is running an older
Claude Code than the one installed, or the reachability probe positively establishes that it is
not reachable. `UNKNOWN` and `LOCAL_ONLY` verdicts never trigger a restart, regardless of age. Timers and
thresholds come from the config file, read at call time. The automatic mode ships enabled.

Research behind every decision: [Claude Code session identity and reachability](../research/claude-code-session-identity-and-reachability.md).

## Existing behaviour this builds on

| Capability | Where | Reused how |
|---|---|---|
| Supervisor relaunches the agent on any child exit except 77 and 79; resolves a transcript by title on each launch and can fall back to a new conversation; stops after a return within 3 s or three short runs under 60 s | `src/ai_cli/session_script.py`, `main.py` (`resolve-continue-target`, `_find_cc_session_candidates_by_title`) | Unchanged. The restart is a `SIGTERM` to the agent; D-10 pins the exact UUID through launch and prepares a compatible child script before the signal. `min_uptime_seconds` keeps every restart clear of the short-run breaker. |
| Live Claude Code registry reader | `session_adopt.live_sessions()` | Target resolution reads the same `~/.claude/sessions/*.json` records, adding the `tmux`, `status`, `version`, `nameSource`, `bridgeSessionId` and `messagingSocketPath` fields. |
| Launch-convention session naming | `session.build_session_name()`, `_resolve_explicit_tmux_slot()` | `ai c N [-R] -p P --restart-session` resolves the session `ai c` would attach. |
| Process birth identity | `process_probe.capture_identity()`, `start_time_match()` | The kill is gated on the pid's start time matching the registry's `procStart`. |
| Config read at call time | `config.load_config()`, `DEFAULT_CONFIG`, the validated-section pattern of `read_reaper_config()` | New `[restart_session]` section. |
| External commands through drop-in files | `<config dir>/session-migrate.d/*.toml` | New `<config dir>/restart-session.d/*.toml` for the injection command and the optional reachability observer. |
| Supervisor heartbeat every 30 s through `ai internal publish-heartbeat` | `session_script.py` ticker | Runs the automatic evaluator on its own interval (D-7). |

Parity: ordinary launches without a restart request retain their resolver and relaunch behaviour.
A requested restart instead pins the exact conversation and forbids fallback (D-10, AC-2.15 and
AC-2.18). The persistent supervisor and registry readers remain unchanged; T-04 adds the evaluator
launch to the heartbeat handler and keeps heartbeat publication unchanged.

## Configuration

`[restart_session]` in the package config file (`$XDG_CONFIG_HOME/ai-cli-utils/config.toml`, or
`%APPDATA%\ai-cli-utils\config.toml`), read on every call. Defaults:

| Key | Default | Meaning |
|---|---|---|
| `context_threshold_pct` | `20` | Context gate passes at or below this percentage. |
| `compaction_fresh_turns` | `1` | Context gate also passes when the latest auto-compaction is followed by at most this many assistant turns. |
| `context_window_tokens` | `0` | Window used for the percentage when the session's own window cannot be read; `0` means use the model table in code. |
| `settle_seconds` | `60` | The session must have been `idle` this long. |
| `min_uptime_seconds` | `1800` | Never restart a process younger than this. |
| `cooldown_seconds` | `3600` | Never restart the same session twice within this. |
| `term_grace_seconds` | `20` | Wait after `SIGTERM` before the one `SIGKILL` to the same verified pid. |
| `relaunch_timeout_seconds` | `180` | Wait for the relaunched process and its verification. |
| `mechanism` | `"kill"` | `"kill"` or `"exit"` (inject `/exit` instead, D-1). |
| `pre_step` | `true` | Run the `/save-state` pre-step when the context gate fails and an injection command is configured. |
| `compaction_wait_seconds` | `1800` | How long the pre-step waits for the compaction. |
| `auto` | `true` | Automatic mode on (D-7). |
| `auto_interval_seconds` | `600` | How often a supervisor evaluates its own session. |

`<config dir>/restart-session.d/*.toml` declares external commands, each an argv list with
`{tmux_pane}`, `{tmux_session}`, `{session_id}`, `{title}` and `{command}` placeholders:

```toml
[inject]            # used for /save-state (pre-step) and /exit (fallback mechanism)
argv = ["python3", "-m", "example_injector", "inject", "--target", "{tmux_pane}", "--command", "{command}", "--json"]

[observer]          # remote peer listing; structured result contract below
argv = ["ssh", "host-b", "list-sessions-as-seen-from-here"]
```

No file means no injection command and no observer. Missing injection refuses the pre-step and
the exit mechanism. Missing observer leaves the cross-host peer verdict `UNVERIFIED` and returns
non-zero `verification-incomplete`, even when the local process restarted and bridge checks pass.
A local restart is reported separately from verified peer reachability. Failed peer identity or
absence is a verification failure; timeout, malformed, stale or incomplete evidence is incomplete,
never success. Mobile visibility and SendMessage delivery remain separate rollout observations.

The observer returns structured JSON with `status` (`seen`, `absent`, `unknown`), `complete`,
`observed_at`, and matching rows carrying `session_id`, `title`, and `bridge_session_id` when
available. Only a fresh, complete listing from a peer on the other host can establish presence
or absence; timeout, non-zero exit, malformed output, truncation, stale output and transport
failure mean `UNKNOWN`. Fresh means observed during the current probe invocation. Duplicate
or mismatched rows fail identity verification. The observer must identify the replacement
conversation and expected title, and correlate its current bridge identity where exposed;
a title-only stdout response is insufficient. This is a peer listing check, not a claim that
SendMessage delivery or the mobile UI was tested.

Remote Control activation prerequisite: before signalling or injecting `/exit`, read
`remoteControlAtStartup` from the user settings file and from any project or local settings file
for the target's working directory, the last one present winning, and require `true`. Never
change a settings file. Missing, false or unreadable activation refuses with `rc-activation`,
even when a bridge id exists. A failed reconnection is recorded against the conversation and
suppresses further automatic attempts until a passive probe passes or the operator retries by
hand; cooldown alone never restarts a session whose last restart failed.

## Decisions

<a id="d-1"></a>

#### D-1: Restart mechanism — ✅ Resolved by Opus orchestrator

**Context.** The ask prefers killing the agent inside its pane over typing `/exit`, and keeps
prompt injection in play. The supervisor already relaunches on any exit but 77 and 79.

##### (a) `SIGTERM` to the verified agent pid, `/exit` injection as the fallback

**Pros:**

- Works whatever the TUI is showing (a dialog, a half-typed prompt, a hung render).
- Uses the supervisor's existing relaunch contract, so the pane, its scrollback and its tmux
  identity survive.
- One pid, checked against its registry start time, so nothing else can be hit.

**Cons:**

- Claude Code gets no chance to show its own "background work is running" dialog.
- Needs a supervisor in the pane; a bare `claude` in tmux would just exit.

##### (b) Inject `/exit` only

**Pros:**

- Claude Code's own exit path runs, including its background-work warning.

**Cons:**

- Depends on the TUI accepting input, which is exactly what fails in a stuck session.
- Needs the private injection command on every host, so the public package cannot restart
  anything alone.

##### (c) `tmux respawn-pane -k`

**Pros:**

- No pid bookkeeping.

**Cons:**

- Kills the supervisor too, so the session's launch environment and its relaunch loop are lost.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) `SIGTERM` to the verified agent pid, `/exit` injection as the fallback
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-1; decision-topic=restart-mechanism; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=restart sends SIGTERM to the verified agent pid and falls back to injected /exit; applicability=scope:restart-session; outcome-id=kill-primary-exit-fallback; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The ask names the kill as the better mechanism and keeps injection, so (a) keeps both. The first
Con is mitigated by D-3: the idle gate is the background-work check, it fails closed on missing
evidence, and it runs again immediately before the signal. The second Con is mitigated by
refusing any pane whose start command is not the `ai c` session script (AC-2.4), and by the
`mechanism = "exit"` setting, which uses the injection command instead.

---

<a id="d-2"></a>

#### D-2: How a target is named — ✅ Resolved by Opus orchestrator

**Context.** The ask wants an explicit "ai session ID or custom title", and also the option with
no value "using the convention that launches the cc session", for example
`ai c 1 -R -p myproject --restart-session`.

##### (a) Three forms, each resolving to exactly one registry record

- `ai --restart-session <TARGET>`: TARGET is a custom title, a Claude Code session id, or a tmux
  session name.
- `ai c N [-R] -p P --restart-session` with no value: the session that `ai c` with the same
  arguments would attach to.
- `ai --restart-session` with no value, run inside a managed pane: that pane's own session,
  found through `TMUX_PANE` and the registry's `tmux` field.

**Pros:**

- Covers every form the ask names.
- A self-restart is a normal target, so the same gates apply.

**Cons:**

- A self-restart is mid-turn by definition (the shell running the command is a tool call), so it
  can never pass the idle gate synchronously.

##### (b) Explicit TARGET only

**Pros:**

- Smaller.

**Cons:**

- Drops the no-value form the ask names.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) Three forms, each resolving to exactly one registry record
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-2; decision-topic=restart-target-forms; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=restart target is an explicit title or id, the ai c launch convention, or the current pane; applicability=scope:restart-session; outcome-id=three-target-forms; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(b) would drop a named form, so (a). The self-restart Con is mitigated by detaching: when the
target is the caller's own session, the command starts a detached copy of itself, returns
immediately, and the copy waits up to `compaction_wait_seconds` for the session to become idle
before running every gate. More than one matching record, or none, is a refusal that names the
candidates.

---

<a id="d-3"></a>

#### D-3: What counts as a live, idle session — ✅ Resolved by Opus orchestrator

**Context.** The ask requires "an active ai session process" with "no agents or background shell
processes running" and "completely idle". The research found no single complete registry of
background work.

##### (a) All of these, re-checked immediately before the signal; any unknown refuses

- Live: a registry record whose pid is alive with a matching start time, whose `tmux` field names
  a pane whose start command is the `ai c` session script.
- No in-flight turn: registry `status` is `idle` (not `busy`, not `shell`, no `waitingFor`) and has
  been for `settle_seconds`; the transcript's last main-chain record ends a turn. A completed
  compaction also ends one: a `compact_boundary` followed only by its compaction summary and the
  local-command records of the `/compact` that ran it (measured on a session compacted at the
  prompt, which writes no `turn_duration` afterwards).
- No background work: no background launch in the transcript (a `backgroundTaskId`, an
  `async_launched` status) without a matching completion, the latest main-chain
  `{"type":"system","subtype":"turn_duration"}` record present, with `pendingBackgroundAgentCount`
  absent or 0, and no live descendant of the agent pid other than the MCP servers its process tree
  started with. Claude Code writes that field only when it is non-zero: of 103 main-chain turn-end
  records in one session, 82 carried no field, 21 carried 1 or 2, and none carried 0. A missing
  turn-end record, not a missing field, is the unknown that refuses. A transcript is read from
  its end in bounded chunks; they reach hundreds of megabytes.

**Pros:**

- Every signal the research found counts, and the strictest wins.

**Cons:**

- Some idle sessions will be refused because one signal is missing, so restarts happen less often
  than they could.
- The descendant check needs the MCP server list, which the command must derive from the process
  tree at the time of the check.

##### (b) Registry `status` only

**Pros:**

- Simple and cheap.

**Cons:**

- `idle` is set while background shells and subagents keep running, so it does not meet "no
  agents or background shell processes running".

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) All of these, re-checked immediately before the signal; any unknown refuses
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-3; decision-topic=restart-idle-gate; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=a session is restartable only when live, idle with no turn, and showing no background work by any signal; applicability=scope:restart-session; outcome-id=strict-idle-gate; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The ask makes idleness absolute, so the strict reading wins. The first Con is accepted, since a
refused restart costs only a later retry and the triggers retry on their interval. The second
is mitigated by classifying descendants against the MCP servers in the session's configuration,
and treating any unclassified descendant as background work. A configured server often starts
through a wrapper that execs a different program (measured: a configured `<bin>/notifier
mcp-adapter` runs as `python ... notifier.py mcp-adapter`), so a direct child counts as that
server when its argv ends with the server's configured args, or its argv[0] resolves to the
configured command, and it started within a minute of the agent itself.

---

<a id="d-4"></a>

#### D-4: The context gate — ✅ Resolved by Opus orchestrator

**Context.** The ask: "either be at auto-compaction ... or under 20% context window (should be
configurable %)". The research gives the formula and warns that usage is null right after a
compaction.

##### (a) Pass when either holds; compute the percentage in the package

- Just auto-compacted: the latest `compact_boundary` record has `compactMetadata.trigger` `auto`,
  and at most `compaction_fresh_turns` assistant turns follow it. A manual compaction, such as
  one the pre-step's `/save-state` leads to, does not satisfy this arm; the session then passes
  only through the threshold arm, which a fresh compaction normally meets.
- Under threshold: the latest main-chain `usage` gives
  `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`, divided by the model's
  window (from the model id, or `context_window_tokens`), is at or below `context_threshold_pct`.
- When no usage record follows the latest `compact_boundary`, the threshold arm uses that
  boundary's `compactMetadata.postTokens` as the current context, never zero percent. Measured: a
  session compacted at the prompt recorded `postTokens: 15397` and no usage afterwards. This is
  how a session the pre-step compacted passes, since its compaction is manual.

**Pros:**

- Needs no private tool, so it works on any host.
- Both arms of the ask are kept and the threshold is configurable.

**Cons:**

- The window for a model the table does not know is a guess unless configured.

##### (b) Call an operator-supplied context command

**Pros:**

- Reuses an existing tool where one exists.

**Cons:**

- The public package would have no context gate without it.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) Pass when either holds; compute the percentage in the package
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-4; decision-topic=restart-context-gate; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=the context gate passes just after an auto-compaction or at or below a configurable percentage, default 20; applicability=scope:restart-session; outcome-id=compaction-or-threshold; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(a) keeps both arms and the default. The Con is mitigated by assuming the smallest window in the
table for a model the table does not know and `context_window_tokens` does not set. That
overstates the percentage, so the gate can only become stricter, never looser, and a session the
pre-step has just compacted still passes. The first version failed the gate outright for an
unknown model; a live check on a session running a model the table lacked showed that this
refuses every such session even after its compaction, because a manual compaction passes only
through the threshold arm.

---

<a id="d-5"></a>

#### D-5: How the pre-step reaches the shared injection library — ✅ Resolved by Opus orchestrator

**Context.** The ask names the shared prompt-injection library for `/save-state`. That library is
private, and this package is public, so it cannot import it by name.

##### (a) A configured injection command in `restart-session.d/inject.toml`, installed by the private harness

**Pros:**

- The pre-step uses the shared library on every host that has it, through its own CLI and its
  readiness, landing and verification checks.
- Follows the existing `session-migrate.d` precedent for reaching private tooling.

**Cons:**

- A host without the drop-in file has no pre-step, so a session over the threshold is refused.
- Needs a change in the private harness to write the file at install.

##### (b) Type `/save-state` with `tmux send-keys` from this package

**Pros:**

- No configuration.

**Cons:**

- Replaces the shared library the ask names, and loses its landing checks.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) A configured injection command in `restart-session.d/inject.toml`, installed by the private harness
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-5; decision-topic=pre-step-injection-route; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=the save-state pre-step and the exit fallback inject through the shared library via a configured command; applicability=scope:restart-session; outcome-id=configured-injector; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(b) would drop the named library, so (a). The first Con is the documented behaviour on a host
without the harness, and the refusal says why. The second is task T-03b, which lands through its
own PR in the harness repository and writes the file idempotently on every install.

---

<a id="d-6"></a>

#### D-6: Command shape and short flags — ✅ Resolved by Opus orchestrator

**Context.** The ask suggests `ai --restart-session` "and whatever short option flag would be
available". The repo requires a short and a long form for every option. Top-level short options
in use: `-h`, `-V`. On `ai c`: `-h -r -o -b -n -s -W -D -q -v -d -F -k -H -R -m -p`.

##### (a) `-X/--restart-session [TARGET]` and `-Y/--restart-check [TARGET]`, on the top level and on `ai c`

`--restart-check` runs every gate and trigger, acts on nothing, and prints the verdict. The
automatic evaluator is the hidden internal command `ai internal restart-auto <tmux session>`.

**Pros:**

- Both letters are free in both scopes, so the same flag works in both places the ask uses it.
- A check mode lets the rollout and the operator see why a session would be refused.

**Cons:**

- Neither letter is a mnemonic.

##### (b) A subcommand `ai restart-session`

**Pros:**

- Conventional click shape.

**Cons:**

- Not the option form the ask names, and it cannot ride on `ai c N -p P`.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) `-X/--restart-session [TARGET]` and `-Y/--restart-check [TARGET]`, on the top level and on `ai c`
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-6; decision-topic=restart-cli-shape; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=the command is the option -X/--restart-session with optional value, plus -Y/--restart-check; applicability=scope:restart-session; outcome-id=x-and-y-flags; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(a) is the option form the ask names. The Con is mitigated by the help text and the README entry.

---

<a id="d-7"></a>

#### D-7: What runs the automatic triggers — ✅ Resolved by Opus orchestrator

**Context.** Child .4 asks for event-driven wiring "where possible (hook/supervisor), not cron".
A lost Remote Control connection produces no Claude Code event, so some periodic look is
unavoidable.

##### (a) The pane's own supervisor, through the heartbeat it already runs

The supervisor's 30 s heartbeat already calls `ai internal publish-heartbeat`, which runs the
installed package. That call starts `ai internal restart-auto <tmux session>` detached when
`auto_interval_seconds` have passed since the last evaluation of that session. The evaluator
checks the triggers, and when one holds it runs the same command path as `--restart-session`,
including the pre-step.

**Pros:**

- No new scheduler, and nothing to install in Claude Code's settings.
- New code reaches running sessions on the next `ai update`, because the heartbeat runs the
  installed package rather than code baked into the session script.
- Each supervisor evaluates only its own session, so a host-wide sweep never happens by accident.

**Cons:**

- Detection lags by up to `auto_interval_seconds`.

##### (b) Claude Code `Stop` and `SessionStart(compact)` hooks

**Pros:**

- Fires at the exact moment a turn ends or a compaction finishes.

**Cons:**

- Hooks live in Claude Code's settings, which this package does not own.
- An idle session whose connection drops fires no hook at all, so (a) is still needed.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) The pane's own supervisor, through the heartbeat it already runs
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-7; decision-topic=auto-trigger-runner; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=each supervisor evaluates its own session from its heartbeat on a configurable interval; applicability=scope:restart-session; outcome-id=supervisor-heartbeat; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(b) cannot catch the main failure. The lag Con is accepted at a 10-minute default and is
configurable down to the heartbeat's 30 s.

---

<a id="d-8"></a>

#### D-8: Trigger definitions — ✅ Resolved by Opus orchestrator

**Context.** The ask names two triggers: the update notice, and `/rc` no longer "de facto
connected and visible" from the mobile app or to peers under the expected title. The research
found the notice lives only in memory, and found no probe on the session's host that proves
mobile visibility.

##### (a) Version comparison for trigger 1; positive reachability failure for trigger 2

- Trigger 1: the registry `version` is older than the installed version, with a verified installed
  version source for the platform. The footer text `Update installed · Restart to apply` also
  counts. An unreadable version is unknown, not an update.
- Trigger 2: the research probe establishes `UNREACHABLE`: a positively failed local socket or
  complete listing, null bridge id, wrong expected user title, or a fresh, complete remote
  observer listing that establishes absence or wrong identity/title. A process-namespace or
  probe-access failure is `UNKNOWN`, never proof that the target is unreachable, and so is a remote
  listing that carries the "sessions beyond the first pages are missing" notice (research doc
  section 3: stale registrations can push a live session past the fetched pages).
- `LOCAL_ONLY` and `UNKNOWN` never trigger, regardless of duration. Timers schedule probes and
  enforce cooldown; elapsed time does not establish a connectivity failure.
- A trigger only proposes a restart. Every prerequisite and gate still applies, including
  activation, the exact-conversation pin of D-10, the lock, D-3 and D-4, with the pre-step when the
  context gate fails.

**Pros:**

- Uses positive evidence for each trigger and preserves the ask's condition for automation.
- An observer can detect the case where local registration looks healthy but peers miss it.

**Cons:**

- Without a working remote observer, remote-only failures may remain undetected.

##### (b) Footer text for trigger 1; local tier only for trigger 2

**Pros:**

- Smaller.

**Cons:**

- Misses the case the ask describes, where `/rc` says connected and peers cannot find it.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) Version comparison for trigger 1; positive reachability failure for trigger 2
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-8; decision-topic=auto-trigger-definitions; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=trigger on positive update or reachability-failure evidence; unknown and local-only verdicts never trigger; applicability=scope:restart-session; outcome-id=positive-evidence-triggers; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Round 1 of the doc audit found that the first version's age bound restarted a healthy session
only because nobody had observed it, which the ask rules out: the automatic restart happens only
when a trigger is detected. (a) is the corrected definition and keeps that condition. (b) misses
the failure where `/rc` reports connected while peers miss the session. The Con of (a), that a
failure only the mobile app shows goes undetected without an observer, is mitigated by the
observer hook, by naming the unobserved surface in every restart report, and by D-11, which puts
the remaining gap to the operator.

---

<a id="d-9"></a>

#### D-9: Coexisting with the harness's update-restart pass — ✅ Resolved by Opus orchestrator

**Context.** On hosts with the private harness, an update-restart pass already restarts idle
sessions onto an installed update by injecting `/exit`. Two restarters could act on one session
at the same moment.

##### (a) One per-session lock and cooldown inside this command, honoured by the harness pass

Both actors must atomically acquire the same exclusive OS-backed lock at
`<state dir>/restart-session/<tmux session>.lock` before revalidation, injection or signalling.
They retain ownership through any pre-step, the action and the replacement verification or failure.
Checking lock-file existence or observing that it is unlocked does not grant ownership.
The lock file is stable and is never unlinked/replaced while contenders may use it. Both actors
read and atomically update a separate durable cooldown/result file under the lock, recording
`last_restart` when action is attempted even if verification fails. A contender that cannot
acquire ownership or finds cooldown active refuses without injecting or signalling. Both use
the same canonical session key and state directory. A detached helper owns the lock itself;
the caller's lock must not disappear while its helper acts.

**Pros:**

- Both restarters keep working, and they can never act on one session together.

**Cons:**

- A second small change in the private harness.

##### (b) Turn the harness pass off

**Pros:**

- No race.

**Cons:**

- Loses its extra gates (pending wake-ups, context-supervisor work in flight) for no gain.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) One per-session lock and cooldown inside this command, honoured by the harness pass
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-9; decision-topic=restarter-coexistence; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=the restart command holds a per-session lock and cooldown that the harness update-restart pass honours; applicability=scope:restart-session; outcome-id=shared-lock; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(a) keeps both. The Con is task T-04b, landed through its own harness PR alongside T-03b.

---

<a id="d-10"></a>

#### D-10: Making sure the relaunch resumes the same conversation — ✅ Resolved by Opus orchestrator

**Context.** The supervisor resolves mutable titles again on every launch. A pre-check followed
by a post-launch UUID comparison cannot prevent a newer same-title conversation from running.
The running supervisor need not implement a new pre-signal acknowledgement: it already starts a
replaceable child from the stable script after the old child exits. This compatibility claim
must be proven against the running version, not inferred from the installed package alone.

##### (a) Prepare the replacement child and pin the exact conversation through launch

Under D-9's lock, read the existing stable script before changing it, capture its metadata and
verify the running supervisor/child version supports the existing stable-child reload path.
Atomically replace the stable script using `_write_launch_script_if_changed`; never rewrite the
inode a shell is executing. Verify the installed script's digest and that the old child's
post-agent path must exit to the supervisor before another `run_agent` call. If that route cannot
be established for this running version, refuse with `supervisor-upgrade-required` before either
restart mechanism; do not kill the supervisor or respawn the pane to bypass the refusal.

Before acting, durably publish a per-pane restart request containing the exact UUID and transcript
path, canonical cwd/title, tmux identity, supervisor generation, old-child pid/start identity and
request id. Revalidate the request, script identity and all gates immediately before the action.
The prepared child checks this request before any title resolver or prompt-file consumption. It
validates generation/ownership and the old child's exit, then uses `--resume <exact UUID> --name
<title>` in both prompt and no-prompt launch branches. No title lookup or fresh-conversation
fallback is permitted for a requested restart, including when validation or `--resume` fails.
A malformed, expired, stale-generation or missing-transcript request blocks agent launch and
records a failure. The request remains a durable fence through replacement verification or
failure; it is not consumed by a resolver query or silently discarded by a failed launch. The
lock owner finalizes it after success; on failure it retains a blocking result until explicit
operator recovery validates the target and clears the request. Cooldown expiry cannot clear it.

**Pros:**

- The UUID is fixed before action, so a newer same-title transcript cannot win the relaunch.
- Existing supervisors that support replaceable children can load the prepared child without
  being replaced themselves or requiring a new acknowledgement before the first restart.

**Cons:**

- Requires a child-script change and compatibility coverage for the running versions.
- Unknown or incompatible running versions must refuse rather than receive an unsafe restart.

##### (b) Check the title resolver before the signal and compare UUIDs afterwards

**Pros:**

- Requires no child-script change.

**Cons:**

- A newer same-title transcript between check and launch can run the wrong conversation.
  Detecting the mismatch afterwards does not undo that launch.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) Prepare the replacement child and pin the exact conversation through launch
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-10; decision-topic=resume-target-safety; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=a requested restart pins the exact conversation id through the prepared child launch with no title or fresh fallback; applicability=scope:restart-session; outcome-id=pinned-child-launch; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Audit round 3 showed that the pre-check of the coordinator's earlier option, now (b), leaves a
window in which a newer same-title transcript can run, and that the running supervisor already
re-reads its stable child script on every child launch, so pinning needs no supervisor change and
does not reopen N-1. The coordinator adopts (a) on that evidence.

Use (a) to restore the existing same-conversation safety requirement. Its compatibility cost is
covered by AC-5.7: exercise the pre-feature supervisor with its old child and a refreshed stable
script, then prove the first restart uses the pin without a pane/supervisor replacement. The
running supervisor's stable-child reload is reused, not changed. Unsupported versions remain
reported rollout blockers requiring a separately reviewed migration, not silently skipped
successes. Each running host is certified compatible by AC-5.7 before its rollout.

---

<a id="d-11"></a>

#### D-11: Restarting for a failure no probe on the host can see — `⏳ PENDING (human)`

**Context.** The ask's second trigger is a session no longer "de facto connected and visible"
from the mobile app or to other sessions under its expected title. The research found no way for
a process on the session's host to see what the mobile app lists, and the headless `ListAgents`
view there shows only local peers. D-8 detects every failure a probe can observe. A failure that
only the mobile app would show stays undetected unless an observer on another host is configured.
Leaving that gap open would narrow an explicit trigger, so it goes to the operator.

##### (a) Detect only what a probe observes; the operator restarts by hand when the app shows a problem

**Pros:**

- Never restarts a healthy session, matching "doesn't necessary auto-restart unless it detects".
- `ai -X <title>` from any shell, or `ai -X` inside the session, covers the case in one command.

**Cons:**

- A session the app cannot see, while every host-side probe passes, stays that way until
  someone notices.

##### (b) Also restart an idle session whose reachability has gone unobserved for N hours

**Pros:**

- Bounds how long an app-only failure can last, at the price of a resume.

**Cons:**

- Restarts healthy sessions about once per N hours, which the audit read as contrary to the ask.

##### (c) Configure a cross-host observer on each host pair

**Pros:**

- Turns part of the gap into a detected trigger with positive evidence.

**Cons:**

- Needs a command on the other host that lists sessions as that host sees them, which does not
  exist yet, and it still does not observe the mobile app itself.

##### Recommendation

> **Decision:** `PENDING` — recommendation (a) now, with (c) added once a cross-host listing exists
<!-- decision-record: chosen-option=PENDING; ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-11; decision-topic=undetectable-reachability-gap; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=how to handle a reachability failure that no probe on the host can observe; applicability=scope:restart-session; outcome-id=pending; relation=different-question; related-decision-id=AI-CLI-nyz8/D-8; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

D-11 blocks completion of the visibility requirement, plan sign-off and epic closure until
evidence satisfies the requirement or the operator explicitly approves a re-scope in the Approval
Log. The recommendation is not authorization to ship a narrowed trigger. Implementation and
local testing can proceed, but rollout cannot claim full reachability from local evidence alone.
Option (b) would broaden the trigger beyond positive evidence and also needs an explicit owner
decision; no age-only restart is authorized. Reports retain separate local, peer, message-delivery
and mobile verdicts, and missing observations stay pending.

---

## Open Questions

1. Does the mobile app's Code tab list a session whose registry carries a `bridgeSessionId`
   after a `--resume` relaunch under the title passed with `--name`? The rollout observes this
   on real sessions and records the answer in the research doc; no API exists to check it from
   the host.

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

<!-- Decisions are recorded in the overview region above, beside the configuration they govern. -->

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

## Task Breakdown

### T-01: Research (AI-CLI-nyz8.1)

Done in [the research doc](../research/claude-code-session-identity-and-reachability.md).

- [x] AC-1.1 The research doc records, with evidence, how Remote Control and `ListAgents` identify
  a session.
- [x] AC-1.2 The research doc names the probe that tells true reachability apart from the `/rc`
  status screen, and its limits.

### T-02: The restart command (AI-CLI-nyz8.2)

Implements D-1, D-2, D-3, D-4, D-6, D-10 and the lock half of D-9. New module
`src/ai_cli/session_restart.py`, options wired in `src/ai_cli/main.py`, the `[restart_session]`
section in `src/ai_cli/config.py`, the pinned-restart child path in
`src/ai_cli/session_script.py`, tests in `tests/test_session_restart.py`, a README section.

- [ ] AC-2.1 When `ai -X <TARGET>` names a custom title, a Claude Code session id or a tmux session
  that matches exactly one live registry record, the command shall act on that session only.
- [ ] AC-2.2 When `ai c N [-R] -p P -X` is given with no value, the command shall target the session
  `ai c N [-R] -p P` would attach to, and shall not launch a new one.
- [ ] AC-2.3 When `ai -X` is run with no value inside a managed pane, the command shall start a
  detached copy that waits for that session to become idle, return 0 at once, and the copy shall
  then apply every gate.
- [ ] AC-2.4 If the target matches no live registry record, matches more than one, has a dead pid
  or a pid whose start time differs from `procStart`, or sits in a pane whose start command is not
  the `ai c` session script, then the command shall exit non-zero, name the reason, and signal
  nothing.
- [ ] AC-2.5 If the registry `status` is not `idle`, or has been `idle` for less than
  `settle_seconds`, or the transcript shows an unfinished turn, then the command shall refuse
  with reason `busy` and signal nothing.
- [ ] AC-2.6 If any background-work signal of D-3 is positive or cannot be read, then the command
  shall refuse with reason `background-work` and signal nothing.
- [ ] AC-2.7 If the context gate of D-4 fails and the pre-step cannot run or does not complete,
  then the command shall refuse with reason `context` and signal nothing; the threshold shall be
  read from the config file on that call.
- [ ] AC-2.8 When every prerequisite and gate passes under the lock, the command shall re-check
  them, send `SIGTERM` to the one verified pid (and `SIGKILL` to that same pid only after
  `term_grace_seconds` and a fresh start-time match), and wait for a replacement in the same pane
  with the same `sessionId`, the expected `name`, `nameSource` `user` and a non-null
  `bridgeSessionId`, whose messaging socket accepts a passive connection and which a complete
  `claude agents --json` listing shows under the expected title. The command shall run a
  configured observer against the replacement and require fresh, complete, identity-correlated
  peer presence for reachable success. Missing or unknown peer evidence shall return non-zero
  `verification-incomplete`; observed absence or wrong identity shall fail verification. Local
  or bridge failure inside `relaunch_timeout_seconds` shall also exit non-zero. The report shall
  separate local, peer and mobile verdicts, leaving mobile `UNVERIFIED` until independently
  observed during rollout. Tests cover local success with no observer, unknown/failed peer
  evidence and a verified replacement; no incomplete result may be labelled reachable success.
- [ ] AC-2.9 Where `mechanism = "exit"`, or the platform cannot signal the pid, the command shall
  inject `/exit` through the configured injection command instead of signalling; with no injection
  command it shall refuse with reason `no-mechanism`.
- [ ] AC-2.10 While a restart of a session is in progress, or within `cooldown_seconds` of the last
  one, a second `-X` for that session shall refuse with reason `locked` or `cooldown`.
- [ ] AC-2.11 `-Y/--restart-check` shall print every gate and trigger verdict, as text or with
  `--json`, and shall never signal or inject.
- [ ] AC-2.12 The tests drive the CLI entry point with fake registry files, transcripts, tmux and
  process probes, and cover every refusal above plus one full restart; no test signals a real
  process other than one it started itself.
- [ ] AC-2.13 On Windows the kill path is recorded as unsupported in the code and the README; the
  command uses the injection route there or refuses.
- [ ] AC-2.14 If `remoteControlAtStartup` is missing, false or unreadable for the target, then the
  command shall refuse with `rc-activation` before either mechanism and leave every settings file
  unchanged; fixture tests cover each case.
- [ ] AC-2.15 When a restart is requested, the command shall prepare D-10's compatible child
  script and durable request before acting, fencing supervisor generation and old-child birth
  identity. The replacement shall resume the exact UUID in both launch branches, with no title
  lookup or fresh-conversation fallback; invalid requests or failed resume shall block launch.
  If compatibility cannot be proven, it shall refuse with `supervisor-upgrade-required` before
  action. Replacement UUID mismatch shall report `resume-mismatch` and retain the failure fence.
- [ ] AC-2.16 When a restart fails its verification, automatic retries for that conversation shall
  stay suppressed until a passive probe passes or the operator retries by hand; a test shows that
  cooldown expiry alone causes no second restart.
- [ ] AC-2.17 When two restart actors contend, both shall acquire D-9's exclusive lock and hold it
  through verification; tests force simultaneous attempts and a contender arriving during the
  pre-step and during the relaunch, and assert exactly one action and a durable cooldown record.

- [ ] AC-2.18 When a test introduces a newer same-title transcript after the final pre-check,
  both prompt and no-prompt branches shall still launch only the pinned UUID. Tests shall also
  cover missing transcripts, stale requests, changed generation, old-child identity mismatch,
  failed resume and a crash before verification, proving zero title/fresh fallback and retained
  failure fencing. Ordinary launches without a request shall preserve existing resolver behaviour.

### T-03: The save-state pre-step (AI-CLI-nyz8.3)

Implements D-5.

T-03a, this repo:

- [ ] AC-3.1 When the context gate fails, `pre_step` is true and an injection command is configured,
  the command shall inject `/save-state` into the target's pane through it, then wait up to
  `compaction_wait_seconds` for a new `compact_boundary` record in the target's transcript.
- [ ] AC-3.2 When that compaction appears, the command shall wait for `settle_seconds` of `idle`,
  re-run every gate of D-3 and D-4, and restart only if they all pass.
- [ ] AC-3.3 If the injection command exits non-zero, reports that it held or queued the injection,
  or the wait times out, then the command shall refuse with reason `context` and the injection
  command's own outcome, and signal nothing.
- [ ] AC-3.4 If background work appears after the compaction, then the command shall refuse with
  reason `background-work`.

T-03b, private harness repository, its own PR:

- [ ] AC-3.5 The harness install writes `<ai-cli-utils config dir>/restart-session.d/inject.toml`
  naming its prompt-injection CLI with an argv that types the given slash command into the given
  pane and returns its outcome, and a second install leaves the file unchanged.
- [ ] AC-3.6 The injection library accepts `/save-state` and `/exit` for this use case, and its
  `/exit` route keeps the session when Claude Code reports background work.

### T-04: Automatic triggers (AI-CLI-nyz8.4)

Implements D-7, D-8 and the harness half of D-9.

T-04a, this repo:

- [ ] AC-4.1 When `ai internal publish-heartbeat` runs for a session, `auto` is true and
  `auto_interval_seconds` have passed since that session's last evaluation, it shall start
  `ai internal restart-auto <tmux session>` detached and return without waiting.
- [ ] AC-4.2 When trigger 1 or trigger 2 of D-8 holds, the evaluator shall run the `-X` path for
  that session, pre-step included; when neither holds it shall do nothing and record why.
- [ ] AC-4.3 If `auto` is false, then the heartbeat shall start no evaluator.
- [ ] AC-4.4 Each evaluation shall append one line to `<state dir>/restart-session/auto.log` with
  the session, the trigger verdicts and the outcome.
- [ ] AC-4.5 The tests shall cover each positive trigger alone, neither trigger, a failing gate,
  interval throttle, and old `LOCAL_ONLY`/`UNKNOWN` verdicts producing no action. Observer timeout,
  malformed/truncated listings and non-zero exit shall remain unknown, not absence evidence.

T-04b, private harness repository, its own PR:

- [ ] AC-4.6 When the external update-restart pass considers a session, it shall atomically acquire
  the same exclusive lock as the command and retain it through revalidation, injection and
  replacement verification, reading/updating cooldown under ownership as D-9 specifies. Failed
  acquisition or active cooldown shall skip without action and log why. A controlled-interleaving
  integration test shall prove it cannot inject after the command acquires ownership. Automatic
  mode rollout is blocked until both actors use this protocol; a check-only adapter is insufficient.

### T-05: Rollout (AI-CLI-nyz8.5)

- [ ] AC-5.1 After T-02 to T-04 merge, the package is reinstalled on `host-a` and the harness
  installed, so `ai -X --help` works and `restart-session.d/inject.toml` exists.
- [ ] AC-5.2 `ai -X <title>` runs for every live session on `host-a` except the launching session
  and any session already known to be running work; each result is one line:
  locally restarted with separate peer/mobile verdicts, refused and why, or skipped and why.
  Unverified peer evidence shall return non-zero `verification-incomplete`, never reachable success.
- [ ] AC-5.3 Each restarted session is checked afterwards for its title in `ListAgents`, its
  `bridgeSessionId`, and its running version.
- [ ] AC-5.4 The operator's session on `host-b` is sent the instruction to update, install and run
  the same command for its own host's sessions.
- [ ] AC-5.5 For each restarted session the rollout records its title in a complete local
  `ListAgents` listing, its `bridgeSessionId` and running version, and a fresh complete cross-host
  observer verdict tied to the replacement identity. An independent peer shall also exercise
  SendMessage to the replacement using a benign correlation token and record confirmed receipt
  and reply, not merely local socket acceptance. The operator shall record the mobile Code-tab
  title and successful connection per session. Missing peer/message/mobile observations remain
  pending, never inferred from a bridge id; D-11 gates completion if mobile evidence is unavailable.
- [ ] AC-5.6 The rollout records, for every task PR, its merge and content proof on the target,
  the main-tree sync, and the worktree reap verdict verbatim.

- [ ] AC-5.7 Before rollout, a fixture test shall run a pre-feature supervisor and old child,
  prepare the new stable child script atomically while the old agent is idle, and complete the
  first pinned restart without replacing the pane or supervisor. The test shall introduce a
  same-title competitor after pre-check and assert that only the original UUID reaches the fake
  agent, in both launch branches. Unknown running versions shall refuse before action and be
  recorded as migration blockers; tests shall prove no supervisor signal or pane respawn occurs.

### T-06: Feedback delivery

Owner: the launching session, with delivery tracked here even when its report lives elsewhere.
No receipt is asserted by this plan. This fix pass prepares the specification only; it sends no
feedback, issues or comments. Execution uses the reporting workflow requested in A002 and records
route-specific outcomes for the Remote Control discovery defect: `/rc` can claim connected while
the mobile Code tab or ListAgents/SendMessage cannot find or reach the session under its expected
custom title. A002 pre-approves this reporting. Remote Control reporting does not require reconfirmation.
Any prompt-cutoff work is separate and cannot substitute for these delivery obligations.

- [ ] AC-6.1 When preparing the requested report, the owner shall use the requested reporting
  workflow to describe the Remote Control discovery defect, including false connected status,
  mobile Code-tab access, ListAgents/SendMessage visibility and expected-title mismatches, record
  reproduction evidence and probe limits, and link the report from the epic after privacy review.
- [ ] AC-6.2 When submitting the Remote Control discovery defect via SendFeedback, the owner shall
  record its actual submission outcome and receipt; locally queued feedback remains pending until
  confirmed, not delivered.
- [ ] AC-6.3 When filing the public GitHub issue for the Remote Control discovery defect, the owner
  shall record the issue URL and confirm that the filed content concerns that defect; a draft is
  not delivery.
- [ ] AC-6.4 When searching for issues related to the Remote Control discovery defect, the owner
  shall record related-issue search results and relevance, then post appropriate comments linking
  to the filed issue and record each comment's URL; if no comment is appropriate, it shall
  record the evidence and reason rather than post unrelated comments.
- [ ] AC-6.5 While any requested delivery route is pending or blocked, epic closure shall remain
  blocked and the owner shall record the blocker and next action. Existing work can satisfy these
  ACs only with matching report/submission/issue/comment receipts. Dropping a route requires an
  explicit operator re-scope in the Approval Log; no completion is inferred from another owner.

## Verification

- Specification regression guard: `uv run --no-sync pytest -q -n 0 tests/test_restart_plan_contract.py`
  checks the three restored contracts; this is not runtime feature certification. Implementation
  tests for the interleaving and pre-feature supervisor remain required by AC-2.18 and AC-5.7.
- The repository gate: `ruff format --check src/ tests/ scripts/`, `ruff check src/ tests/ scripts/`,
  `pytest`.
- One live end-to-end restart before the rollout: a disposable `ai c` session started for the
  purpose, with activation enabled, idle and below the context threshold, allowed to reach
  `min_uptime_seconds` before `ai -X <title>`; separately verify refusal below that minimum. The
  output shows the pid change in the same pane, the supervisor relaunch, the title in
  local and remote `ListAgents`, and a non-null `bridgeSessionId`. Record mobile observation
  separately per AC-5.5. A second run inside `cooldown_seconds` refuses.
- One live refusal: the same session given a long-running background shell, then `ai -Y` shows
  `background-work`.

Audit backlog and the AC that covers each finding:

| Finding | Covered by |
|---|---|
| DV-1 (no Remote Control at startup) | AC-2.14 |
| F-1 (lock checked, not owned) | D-9, AC-2.17, AC-4.6 |
| F-2 and N-1 (relaunch picks another conversation; running supervisors) | D-10, AC-2.15, AC-2.18, AC-5.7 |
| JA-1 (restart of a healthy unobserved session) | D-8, AC-4.5; the remaining gap is D-11 |
| JA-2 (feedback and issue delivery) | T-06, AC-6.1 to AC-6.5; receipts pending |
| JA-3 (local registration claimed as reachability) | AC-2.8, AC-5.2, AC-5.5; D-11 completion gate |
| IC-1 (fresh test session refused by the uptime gate) | Verification above |

## Risks

| Risk | Mitigation |
|---|---|
| A restart drops a background shell or subagent the gates did not see | Strict gate (D-3), re-check right before the signal, unknown refuses |
| A pid is reused between check and kill | Start-time match against `procStart` immediately before each signal |
| A restart loop trips the supervisor's short-run breaker | `min_uptime_seconds` and `cooldown_seconds` |
| Two restarters act on one session | Lock and cooldown (D-9) |
| The pre-step types into a session that just started a turn | The injection library's readiness checks, then every gate again after the compaction |
| A failure only the mobile app shows goes undetected | Observer hook, unobserved surfaces named in every report, D-11 |
| The relaunch resumes another conversation | Durable exact-UUID pin with no fallback and compatible-child preparation (D-10) |
| Remote Control does not start on relaunch | Refuse unless `remoteControlAtStartup` is true (AC-2.14) |

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

(none yet; APPEND_ONLY: prior rounds frozen byte-for-byte)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|

<!-- /doc:region name="approval_log" -->
