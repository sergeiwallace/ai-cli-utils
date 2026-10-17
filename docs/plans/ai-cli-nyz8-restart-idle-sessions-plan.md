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
| A001: "merge it in and auto-reap worktree and /sync-git" | T-05, and every task's landing (integration section) |
| A001: create the epic and sub-tasks, hand off, delegate the run, start the goal | Done before this plan; recorded on the epic |
| A002: feedback report, issue filing and comments about the mobile prompt box | Out of scope for this plan (a separate request) |

**Status:** DRAFT

**Created:** 2026-10-17

**Task:** AI-CLI-nyz8 (epic) with children .1 to .5.

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
- [Open Questions](#open-questions)
- [Task Breakdown](#task-breakdown)
  - [T-01: Research (AI-CLI-nyz8.1)](#t-01-research-ai-cli-nyz81)
  - [T-02: The restart command (AI-CLI-nyz8.2)](#t-02-the-restart-command-ai-cli-nyz82)
  - [T-03: The save-state pre-step (AI-CLI-nyz8.3)](#t-03-the-save-state-pre-step-ai-cli-nyz83)
  - [T-04: Automatic triggers (AI-CLI-nyz8.4)](#t-04-automatic-triggers-ai-cli-nyz84)
  - [T-05: Rollout (AI-CLI-nyz8.5)](#t-05-rollout-ai-cli-nyz85)
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
it with `--resume <session id> --name <title>`. The command then verifies the session came back in
the same pane, under the same conversation and title, registered with Remote Control.

An automatic mode runs the same command when a trigger holds: the session is running an older
Claude Code than the one installed, or the reachability probe says it is not reachable. Timers and
thresholds come from the config file, read at call time. The automatic mode ships enabled.

Research behind every decision: [Claude Code session identity and reachability](../research/claude-code-session-identity-and-reachability.md).

## Existing behaviour this builds on

| Capability | Where | Reused how |
|---|---|---|
| Supervisor relaunches the agent on any child exit except 77 and 79, with `--resume "$session_id" --name "$ai_name"`; stops after a return within 3 s or three short runs under 60 s | `src/ai_cli/session_script.py` | The restart is a `SIGTERM` to the agent, which the supervisor turns into a relaunch. The restart refuses a session younger than `min_uptime_seconds` so it can never trip the short-run breaker. |
| Live Claude Code registry reader | `session_adopt.live_sessions()` | Target resolution reads the same `~/.claude/sessions/*.json` records, adding the `tmux`, `status`, `version`, `nameSource`, `bridgeSessionId` and `messagingSocketPath` fields. |
| Launch-convention session naming | `session.build_session_name()`, `_resolve_explicit_tmux_slot()` | `ai c N [-R] -p P --restart-session` resolves the session `ai c` would attach. |
| Process birth identity | `process_probe.capture_identity()`, `start_time_match()` | The kill is gated on the pid's start time matching the registry's `procStart`. |
| Config read at call time | `config.load_config()`, `DEFAULT_CONFIG`, the validated-section pattern of `read_reaper_config()` | New `[restart_session]` section. |
| External commands through drop-in files | `<config dir>/session-migrate.d/*.toml` | New `<config dir>/restart-session.d/*.toml` for the injection command and the optional reachability observer. |
| Supervisor heartbeat every 30 s through `ai internal publish-heartbeat` | `session_script.py` ticker | Runs the automatic evaluator on its own interval (D-7). |

Parity: nothing existing changes behaviour. The supervisor's relaunch contract, the heartbeat and
the registry readers are used as they are.

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
| `reachability_max_unverified_hours` | `24` | A `LOCAL_ONLY` session older than this counts as unreachable (D-8). |

`<config dir>/restart-session.d/*.toml` declares external commands, each an argv list with
`{tmux_pane}`, `{tmux_session}`, `{session_id}`, `{title}` and `{command}` placeholders:

```toml
[inject]            # used for /save-state (pre-step) and /exit (fallback mechanism)
argv = ["python3", "-m", "example_injector", "inject", "--target", "{tmux_pane}", "--command", "{command}", "--json"]

[observer]          # optional remote-side reachability check; exit 0 and the title on stdout means seen
argv = ["ssh", "host-b", "list-sessions-as-seen-from-here"]
```

No file means no injection command and no observer; the command still works, with the effects
D-4, D-5 and D-8 state.

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
  been for `settle_seconds`; the transcript's last main-chain record ends a turn.
- No background work: no background launch in the transcript (a `backgroundTaskId`, an
  `async_launched` status) without a matching completion, `pendingBackgroundAgentCount` zero or
  absent in the latest record, and no live descendant of the agent pid other than the MCP servers
  its process tree started with.

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
is mitigated by classifying descendants by their command line against the MCP server commands in
the session's configuration, and treating any unclassified descendant as background work.

---

<a id="d-4"></a>

#### D-4: The context gate — ✅ Resolved by Opus orchestrator

**Context.** The ask: "either be at auto-compaction ... or under 20% context window (should be
configurable %)". The research gives the formula and warns that usage is null right after a
compaction.

##### (a) Pass when either holds; compute the percentage in the package

- Just compacted: the latest `compact_boundary` record has `compactMetadata.trigger` `auto` (or
  `manual`), and at most `compaction_fresh_turns` assistant turns follow it.
- Under threshold: the latest main-chain `usage` gives
  `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`, divided by the model's
  window (from the model id, or `context_window_tokens`), is at or below `context_threshold_pct`.
- Null usage right after a compaction counts as just compacted, never as zero percent.

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

(a) keeps both arms and the default. The Con is mitigated by failing the gate (not passing it)
when the window is unknown and unconfigured, which routes the session to the pre-step rather than
restarting it on a guess.

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

##### (a) Version comparison for trigger 1; the tiered probe plus an age bound for trigger 2

- Trigger 1: the registry `version` is older than the version the native launcher now links to.
  The footer text `Update installed · Restart to apply` from `tmux capture-pane` also counts.
- Trigger 2: the research doc's probe returns `UNREACHABLE` (local tier fails, `bridgeSessionId`
  is null, or the registry `name` is not the expected title with `nameSource: "user"`), or an
  observer is configured and does not see the title, or the verdict has been `LOCAL_ONLY` for
  longer than `reachability_max_unverified_hours`.
- A trigger only proposes a restart. Every gate of D-3 and D-4 still decides, with the pre-step
  when the context gate fails.

**Pros:**

- Trigger 1 does not depend on the TUI.
- Trigger 2 uses every signal there is, and the age bound covers "connected but not findable",
  which no local signal shows.

**Cons:**

- The age bound restarts a healthy session about once a day when no observer is configured.

##### (b) Footer text for trigger 1; local tier only for trigger 2

**Pros:**

- Smaller.

**Cons:**

- Misses the case the ask describes, where `/rc` says connected and peers cannot find it.

##### Recommendation

> **Decision:** ✅ Resolved by Opus orchestrator — (a) Version comparison for trigger 1; the tiered probe plus an age bound for trigger 2
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-nyz8/D-8; decision-topic=auto-trigger-definitions; governs=plan:ai-cli-nyz8-restart-idle-sessions-plan; normalized-proposition=trigger on an older running version, or on an unreachable or long-unverified probe verdict; applicability=scope:restart-session; outcome-id=version-and-tiered-probe; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(b) narrows trigger 2 below the ask, so (a). The Con is bounded by the gates: the restart only
happens when the session is idle and its context passes, so the cost is a resume, and the hours
value is configurable or replaced by an observer.

---

<a id="d-9"></a>

#### D-9: Coexisting with the harness's update-restart pass — ✅ Resolved by Opus orchestrator

**Context.** On hosts with the private harness, an update-restart pass already restarts idle
sessions onto an installed update by injecting `/exit`. Two restarters could act on one session
at the same moment.

##### (a) One per-session lock and cooldown inside this command, honoured by the harness pass

The command holds `<state dir>/restart-session/<tmux session>.lock` for the whole act and writes
`last_restart` there. A small harness change makes its pass skip a session whose lock is held or
whose `last_restart` is inside its own retry window.

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

Implements D-1, D-2, D-3, D-4, D-6 and the lock half of D-9. New module
`src/ai_cli/session_restart.py`, options wired in `src/ai_cli/main.py`, the `[restart_session]`
section in `src/ai_cli/config.py`, tests in `tests/test_session_restart.py`, a README section.

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
- [ ] AC-2.8 When every gate passes, the command shall re-check the gates, send `SIGTERM` to the one
  verified pid (and `SIGKILL` to that same pid only after `term_grace_seconds`), wait for a new
  Claude Code process in the same pane with the same `sessionId`, `name` equal to the expected
  title, `nameSource` `user`, and a non-null `bridgeSessionId`, and report each check; a check that
  fails inside `relaunch_timeout_seconds` shall make the exit status non-zero.
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
- [ ] AC-4.5 The tests cover each trigger alone, neither trigger, a trigger with a failing gate,
  and the interval throttle.

T-04b, private harness repository, its own PR:

- [ ] AC-4.6 If the restart command's lock for a session is held, or its `last_restart` is inside the
  pass's retry window, then the harness update-restart pass shall skip that session and log why.

### T-05: Rollout (AI-CLI-nyz8.5)

- [ ] AC-5.1 After T-02 to T-04 merge, the package is reinstalled on `host-a` and the harness
  installed, so `ai -X --help` works and `restart-session.d/inject.toml` exists.
- [ ] AC-5.2 `ai -X <title>` runs for every live session on `host-a` except the launching session
  and any session already known to be running work; each result is one line: restarted and
  reachable, refused and why, or skipped and why.
- [ ] AC-5.3 Each restarted session is checked afterwards for its title in `ListAgents`, its
  `bridgeSessionId`, and its running version.
- [ ] AC-5.4 The operator's session on `host-b` is sent the instruction to update, install and run
  the same command for its own host's sessions.

## Verification

- The repository gate: `ruff format --check src/ tests/ scripts/`, `ruff check src/ tests/ scripts/`,
  `pytest`.
- One live end-to-end restart before the rollout: a disposable `ai c` session started for the
  purpose, idle and freshly started (below the threshold), restarted with `ai -X <title>`; the
  output shows the pid change in the same pane, the supervisor relaunch, the title in
  `ListAgents`, and a non-null `bridgeSessionId`. A second run inside `cooldown_seconds` refuses.
- One live refusal: the same session given a long-running background shell, then `ai -Y` shows
  `background-work`.

## Risks

| Risk | Mitigation |
|---|---|
| A restart drops a background shell or subagent the gates did not see | Strict gate (D-3), re-check right before the signal, unknown refuses |
| A pid is reused between check and kill | Start-time match against `procStart` immediately before each signal |
| A restart loop trips the supervisor's short-run breaker | `min_uptime_seconds` and `cooldown_seconds` |
| Two restarters act on one session | Lock and cooldown (D-9) |
| The pre-step types into a session that just started a turn | The injection library's readiness checks, then every gate again after the compaction |
| The automatic mode restarts healthy sessions daily | Only when idle and under the context gate; the hours value is configurable |

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
