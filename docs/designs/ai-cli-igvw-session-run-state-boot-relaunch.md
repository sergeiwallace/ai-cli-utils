---
title: "Session run state and boot relaunch on macOS and Linux"
category: design
tags: [design, sessions, reboot, relaunch, tmux, iterm2, launchd, systemd, git-inbox, machine-profile]
status: draft
source: "AI-CLI-igvw"
template_version: "design-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
task: AI-CLI-igvw
stub: false
---
<!-- Full structure: see TEMPLATE.md in this directory -->

# Session run state and boot relaunch on macOS and Linux

## Founding Ask Coverage

`founding_ask_ref`: N/A. Written inline by the driving session (aih-fw-firstmate-1, Opus 5.5) from
Sergei's chat turn. The verbatim ask is in the Beads issue `AI-CLI-igvw` and quoted here:

```text
also , i want it to remember which active cc sessions were still running in iterm2 / shell upon a shutdown / restart track that state and then (if configured via machine profile config) auto-relaunch them upon machine bootup, even if iterm2 isn't up and running. we can reattach to that process in an iterm2 pane / terminal. but i want them to be able to auto-launch upon machine reboot. and same thing for linux machines. we dont need to worry about windows for this. just add a separate /beads-task for enabling any windows OS specific implementation work needed for these capabilities. but for macos and linux, i want all this to work and be enabled and operational. however, if i had already exited a cc session manually back to the shell, it should also track that state and not auto-launch upon machine startup. so it needs to track session state as a part of ai-cli-utils. maybe a git tracked dolt database or sqlite database? what do you recommend. i like git tracked still though so its all visible and perhaps even configurable and launchable / modifiable from git remotely from other machines.
```

| Requirement in the ask | Coverage and disposition |
|---|---|
| Remember which sessions were running at shutdown or restart | [The run-state record](#the-run-state-record); the existing session registry and exit-cause classifier |
| Relaunch them at boot when the machine profile enables it, with no iTerm2 open | [D-2](#d-2), [D-3](#d-3); [The boot path](#the-boot-path) |
| Reattach later from an iTerm2 pane or a terminal | [The boot path](#the-boot-path) step 4 (detached tmux, the existing attach); iTerm2's own restore still works (AIH-krf96) |
| Linux as well as macOS | [D-2](#d-2): systemd user unit with linger, LaunchAgent at login |
| Windows as a separate task | AI-CLI-igvw.1, out of scope here |
| Enabled and operational on macOS and Linux | Phase 3 enables it on framework-fedora and mac; Phase 4 proves it |
| A session exited by hand is not relaunched | [D-3](#d-3): only `host_reboot` and `terminal_quit_or_crash` relaunch; `manual_exit` and `unknown` never do |
| State tracked as part of ai-cli-utils | [D-1](#d-1): the code is in ai-cli-utils; the store location is config |
| Storage recommendation; git-tracked preferred, visible, editable and launchable from other machines | [D-1](#d-1) (a plain-text file on a per-host git ref), [D-4](#d-4) (remote changes as git-inbox commands) |

**Status:** draft

**Created:** 2026-10-17

<!-- doc:region name="overview" kind="replaceable" -->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
  - [What already exists](#what-already-exists)
  - [The run-state record](#the-run-state-record)
  - [The boot path](#the-boot-path)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
  - [Phase 1: The run-state mirror on a per-host git ref](#phase-1-the-run-state-mirror-on-a-per-host-git-ref)
  - [Phase 2: ai sessions relaunch --boot](#phase-2-ai-sessions-relaunch---boot)
  - [Phase 3: Boot units per machine profile, enabled on framework-fedora and mac](#phase-3-boot-units-per-machine-profile-enabled-on-framework-fedora-and-mac)
  - [Phase 4: Remote commands and live proof](#phase-4-remote-commands-and-live-proof)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
- [Open Questions](#open-questions)
- [Feedback Rounds](#feedback-rounds)
- [Approval Log](#approval-log)

## Executive Summary

After a reboot, every Claude Code session that was running at shutdown comes back by itself in a
detached tmux session, with no terminal open, on macOS and Linux. A session Sergei exited to the
shell himself stays down. Most of the parts exist. ai-cli-utils keeps a machine-local registry of
live sessions. ai-harness classifies why each session last exited (`host_reboot`,
`terminal_quit_or_crash`, `manual_exit`, `unknown`; AIH-zhqnf.1). iTerm2 restore already relaunches
tracked sessions when iTerm2 reopens its arrangement (AIH-krf96, AIH-zhqnf.4). What is missing is a
boot path that runs without iTerm2, a git-visible copy of each host's run state, and the per-profile
switch turned on for framework-fedora and mac.

## Problem Statement

A reboot ends every session. Today they come back only when iTerm2 reopens its saved arrangement on
acn-mac, or when Sergei relaunches each one by hand. A Linux host, or a Mac where iTerm2 is not
opened, gets nothing back. Nobody can see from another machine which sessions a host had running.

## Design Overview

### What already exists

| Piece | Where | State |
|---|---|---|
| Live-session registry (session, launch, iTerm2 pane) | `src/ai_cli/session_registry.py` | Built |
| Host boot identity, reboot signal | ai-harness `src/ai_harness/host_boot.py` | Built (AIH-zhqnf.1) |
| Exit-cause classifier | ai-harness `src/ai_harness/exit_cause.py` | Built (AIH-zhqnf.1) |
| Launch-resume (resume prompt on launch, per profile) | ai-harness `launch_resume.py`, `launch_resume_gate.py` | Built (AIH-zhqnf.2-.5) |
| Relaunch when iTerm2 restores its arrangement, filtered by exit cause | `src/ai_cli/iterm2_restore.py`, `restore.relaunch.*` in `iterm2.toml` | Built (AIH-krf96, AIH-zhqnf.4); live test on acn-mac open (AIH-zhqnf.7) |
| Host resume and restore (`ai c -R`) | ai-cli-utils | Built, with open bugs AI-CLI-7fle, AI-CLI-nm7b, AI-CLI-tk76 |
| Boot-started machine sidecar | ai-harness notifier; Mac login item AIH-iz7t3.1 | Being enabled (AIH-iz7t3.2) |
| Per-host git ref written by its own host | ai-harness git-inbox `refs/heads/fleet-inbox/<host>` | Built (AIH-f4o46.3) |

Parity: the iTerm2 relaunch path and launch-resume are unchanged. The boot path is a second entry
point to the same relaunch decision, reading the same exit cause and the same scenario switches.

### The run-state record

The registry already knows each live session. This design adds a `run_state` to each record, kept
current on every change:

- `running`: set when `ai c` hands the session off.
- `exited`: set when the exit-cause classifier settles, with its `cause` (`manual_exit`,
  `terminal_quit_or_crash`, `host_reboot`, `unknown`) and evidence.
- `relaunched`: set by the boot path, with the boot id it ran under.

A session that was `running` when the host went down has no exit record yet. The boot path asks the
classifier, which sees a new boot identity and settles it as `host_reboot`.

### The boot path

1. At boot (Linux) or login (macOS), a one-shot unit runs `ai sessions relaunch --boot`.
2. It waits until the network and the session's repo are reachable, up to a configured timeout.
3. For each registry record it settles the exit cause, then relaunches the session if and only if
   [D-3](#d-3)'s rule allows it. It writes the decision and its reason to the log and the record.
4. A relaunch runs the session's recorded launch through `ai c` in a detached tmux session, the
   same way a background launch does today. Sergei reattaches with the existing attach command, or
   iTerm2's restore attaches it into its pane.
5. The relaunched session then gets launch-resume's resume prompt, as configured per profile.
6. Finally it publishes the host's run-state file ([D-1](#d-1)).

## Data Model

The host's run-state file, published on the host's own ref (`refs/heads/fleet-sessions/<host>` in
the configured repo, path `sessions/<host>.toml`):

```toml
host = "framework-fedora"
boot_id = "…"
published_at = "2026-10-17T23:20:00Z"

[sessions.aih-fw-2]
launch = ["ai", "c", "2", "-R", "-p", "aih"]
cwd = "~/projects/ai-harness/.worktrees/aih-fw-2"
run_state = "running"          # running | exited | relaunched
cause = ""                     # set when exited: manual_exit | terminal_quit_or_crash | host_reboot | unknown
changed_at = "2026-10-17T21:49:40Z"
relaunch = "auto"              # auto (follow D-3) | never | always; set by a remote command (D-4)
```

The machine profile switch, in ai-harness's machine table and mirrored in the ai-cli-utils config
template (off by default there):

```toml
[machine.framework-fedora.session_relaunch]
on_boot = true                       # run the boot path
causes = ["host_reboot", "terminal_quit_or_crash"]
network_wait_seconds = 120
publish_ref = "refs/heads/fleet-sessions/"
```

## Integration

- ai-cli-utils: the `run_state` field, the `ai sessions relaunch --boot` verb and the publisher.
- ai-harness `install.sh`: installs the boot unit when `on_boot = true`. On Linux that is a systemd
  user one-shot unit with linger. On macOS it is a LaunchAgent with `RunAtLoad`, attributed to the
  AI Harness app as AIH-iz7t3.1 does for the notifier.
- ai-harness git-inbox: a `session` command kind ([D-4](#d-4)).
- AIH-zhqnf: the boot path reads its exit cause and its `launch_resume` config.

<!-- /doc:region name="overview" -->

## Implementation Phases

### Phase 1: The run-state mirror on a per-host git ref

- T-1.1: add `run_state`, `cause`, `changed_at` and `relaunch` to registry records, written on
  handoff and on exit settlement.
- T-1.2: a publisher writes `sessions/<host>.toml` and commits it to the host's ref (debounced to at
  most once a minute), reusing the git-inbox sender's commit-to-own-ref code.
- AC-1.1: When a session starts, the system shall record `run_state = "running"` and publish it to
  the host's ref within one debounce interval.
- AC-1.2: When Sergei exits a session with `/exit` or Ctrl+C, the system shall record
  `run_state = "exited"` with `cause = "manual_exit"`.
- AC-1.3: If publishing fails (offline, push refused), then the system shall keep the local record,
  retry on the next change or tick, and never block the session.

### Phase 2: ai sessions relaunch --boot

- T-2.1: the verb from [The boot path](#the-boot-path), with `--dry-run` printing each decision.
- AC-2.1: When the host has rebooted and a record was `running`, the system shall classify it
  `host_reboot` and relaunch it detached in tmux.
- AC-2.2: When a record's cause is `manual_exit` or `unknown`, the system shall not relaunch it, and
  the log shall say why.
- AC-2.3: When a record's `relaunch` is `never`, the system shall not relaunch it whatever its cause.
  When it is `always`, the system shall relaunch it unless it is already live.
- AC-2.4: If the session's tmux session already exists, then the system shall not launch a second
  copy.
- AC-2.5: When no terminal application is running, the system shall still relaunch, and the session
  shall be attachable afterwards with the existing attach command.

### Phase 3: Boot units per machine profile, enabled on framework-fedora and mac

- T-3.1: `install.sh` installs the systemd user unit (Linux) or LaunchAgent (macOS) from
  `[machine.<host>.session_relaunch]`. Re-running with nothing changed rewrites nothing.
- T-3.2: set `on_boot = true` for framework-fedora and mac.
- AC-3.1: Where `on_boot = true`, after `install.sh` the boot unit is installed and enabled.
- AC-3.2: Where `on_boot = false`, the system shall leave no boot unit enabled.

### Phase 4: Remote commands and live proof

- T-4.1: a git-inbox `session` command (`relaunch <slug>`, `relaunch-policy <slug> auto|never|always`)
  that the owning host applies and records ([D-4](#d-4)).
- T-4.2: live proof on mac (a restart with two sessions running and one exited by hand) and on
  framework-fedora (a reboot, which needs Sergei at the disk-unlock prompt, AIH-vwsnf).
- AC-4.1: When a `relaunch-policy` command for a session arrives from a pair host, the system shall
  update that record's `relaunch` and publish it.
- AC-4.2: After a real reboot, every session that was running is running again in tmux, the one
  exited by hand is not, and the host's ref shows each state with its time.

## Implementation Audit

To be filled after implementation.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A relaunch loop if a session crashes at start | One boot relaunch per session per boot id; the record says `relaunched` with the boot id |
| A session relaunched before its repo or network is ready | `network_wait_seconds`, then a logged skip, never a half-started session |
| framework-fedora cannot reboot unattended (disk unlock) | The boot path runs whenever the machine does come up; the live test is done with Sergei present |
| Two hosts claim the same session name | Records are keyed per host; the boot path relaunches only its own host's records |

<!-- doc:region name="decisions" kind="replaceable" -->

## Design Decisions

### Decision Summary

| ID | Decision | Recommended (AI) | Chosen (Sergei) | Diverged? | Rationale |
|---|---|---|---|---|---|
| D-1 | Where run state is stored | (c) local registry plus a text file on a per-host git ref | | | Git-visible as asked, no merge conflicts, boot never waits on the network |
| D-2 | What runs the relaunch at boot | (b) a one-shot boot unit per OS | | | Runs before any session or terminal, and stays separate from the message sidecar |
| D-3 | Which sessions relaunch | (a) running at shutdown, cause host_reboot or terminal_quit_or_crash | | | Matches the ask and reuses the existing classifier |
| D-4 | How other machines change a host's sessions | (b) git-inbox commands the owning host applies | | | One writer per ref; reuses the signed message path |

### Decision Details

<a id="d-1"></a>

#### D-1: Where run state is stored — `✅ Resolved by Opus 5.5 driver`

**Context.** Sergei asked for a recommendation between a git-tracked Dolt database and SQLite, and
said he prefers git-tracked state that is visible and editable from other machines.

##### (a) A Dolt database

**Pros:**
- Queryable with SQL, versioned.

**Cons:**
- It is one shared server.
- A Mac writing at shutdown while away from that server loses the write.
- Not readable without Dolt tooling.

##### (b) SQLite committed to git

**Pros:**
- One file, easy to query locally.

**Cons:**
- Binary diffs show nothing in git.
- Two writers produce conflicts git cannot merge.

##### (c) The local registry as the source, published as a plain-text file on a per-host git ref

**Pros:**
- Readable with `git show` from any machine, diffable, and one writer per ref, so no conflicts.
- Boot reads the local registry, so a relaunch never waits on the network.
- The same per-host ref shape the git-inbox already uses.

**Cons:**
- The git copy can lag the local state by one debounce interval.

##### Recommendation

> **Decision:** ✅ Resolved by Opus 5.5 driver — (c) The local registry as the source, published as a plain-text file on a per-host git ref.
<!-- decision-record: chosen-option=(c); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=aih-fw-firstmate-1 (chief-of-staff driver) -->
<!-- decision-lineage: decision-id=AI-CLI-igvw/D-1; decision-topic=run-state-store; governs=doc:docs/designs/ai-cli-igvw-session-run-state-boot-relaunch.md; normalized-proposition=local-registry-plus-per-host-git-ref-text-file; applicability=scope:AI-CLI-igvw; outcome-id=registry-plus-git-ref; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The ask left the choice open ("what do you recommend"), so this is within AI discretion, and it
keeps the stated preference for git-tracked state. The lag is bounded at one debounce interval, and
the local registry, which boot reads, is never behind.

<a id="d-2"></a>

#### D-2: What runs the relaunch at boot — `✅ Resolved by Opus 5.5 driver`

**Context.** The relaunch has to run with no session and no terminal open.

##### (a) The machine notifier sidecar

**Pros:**
- Already starts at boot or login.

**Cons:**
- Mixes a one-time boot job into a long-running message daemon.

##### (b) A one-shot boot unit per OS (systemd user unit with linger; LaunchAgent with RunAtLoad)

**Pros:**
- Runs once, logs once, and is easy to switch on or off per profile.
- Independent of the notifier's health.

**Cons:**
- One more unit to install. install.sh installs it from the profile, idempotently.

##### Recommendation

> **Decision:** ✅ Resolved by Opus 5.5 driver — (b) A one-shot boot unit per OS (systemd user unit with linger; LaunchAgent with RunAtLoad).
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=aih-fw-firstmate-1 (chief-of-staff driver) -->
<!-- decision-lineage: decision-id=AI-CLI-igvw/D-2; decision-topic=boot-runner; governs=doc:docs/designs/ai-cli-igvw-session-run-state-boot-relaunch.md; normalized-proposition=one-shot-boot-unit-per-os; applicability=scope:AI-CLI-igvw; outcome-id=boot-unit; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

On macOS a LaunchAgent runs at login, not at boot. Sergei has said he will turn on the login item,
which is the same model AIH-iz7t3.1 uses for the notifier.

<a id="d-3"></a>

#### D-3: Which sessions relaunch — `✅ Resolved by Opus 5.5 driver`

**Context.** Sergei: relaunch what was running, never what he exited by hand.

##### (a) Records that were running, whose settled cause is host_reboot or terminal_quit_or_crash

**Pros:**
- Exactly the ask, using the classifier AIH-zhqnf already built and tested.

**Cons:**
- An `unknown` cause does not relaunch. The log names each one, and a remote `relaunch` command
  starts it.

##### (b) Every record not marked manual_exit, unknown included

**Pros:**
- Relaunches more often.

**Cons:**
- Could relaunch a session Sergei did exit, whenever the classifier could not tell.

##### Recommendation

> **Decision:** ✅ Resolved by Opus 5.5 driver — (a) Records that were running, whose settled cause is host_reboot or terminal_quit_or_crash.
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=aih-fw-firstmate-1 (chief-of-staff driver) -->
<!-- decision-lineage: decision-id=AI-CLI-igvw/D-3; decision-topic=relaunch-rule; governs=doc:docs/designs/ai-cli-igvw-session-run-state-boot-relaunch.md; normalized-proposition=running-and-cause-reboot-or-terminal-kill; applicability=scope:AI-CLI-igvw; outcome-id=reboot-or-terminal-kill; relation=refines; related-decision-id=AIH-zhqnf/D-7; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Gate 0 holds: "if i had already exited a cc session manually ... not auto-launch" is kept exactly.
The causes list is per-profile config, so widening it later is a one-line change.

<a id="d-4"></a>

#### D-4: How other machines change a host's sessions — `✅ Resolved by Opus 5.5 driver`

**Context.** Sergei wants the state "configurable and launchable / modifiable from git remotely from
other machines".

##### (a) Other machines write the host's ref directly

**Pros:**
- No message path needed.

**Cons:**
- Two writers on one ref conflict, and the host's own publish overwrites the edit.

##### (b) A git-inbox `session` command that the owning host applies and records

**Pros:**
- One writer per ref, so no conflicts.
- Reuses the git-inbox's addressing, sender checks and audit log.

**Cons:**
- The change lands when the owning host's sidecar next polls (60 s).

##### Recommendation

> **Decision:** ✅ Resolved by Opus 5.5 driver — (b) A git-inbox `session` command that the owning host applies and records.
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=medium; ai-profile=aih-fw-firstmate-1 (chief-of-staff driver) -->
<!-- decision-lineage: decision-id=AI-CLI-igvw/D-4; decision-topic=remote-session-changes; governs=doc:docs/designs/ai-cli-igvw-session-run-state-boot-relaunch.md; normalized-proposition=git-inbox-session-command; applicability=scope:AI-CLI-igvw; outcome-id=git-inbox-command; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

A 60 s delay is acceptable for a policy change. A launch that cannot wait uses the signed launch
envelope from AIH-iz7t3 D-3.

<!-- /doc:region name="decisions" -->

## Open Questions

None open. Windows is AI-CLI-igvw.1.

<!-- doc:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

(none yet — APPEND_ONLY: prior rounds frozen byte-for-byte)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|

<!-- /doc:region name="approval_log" -->
