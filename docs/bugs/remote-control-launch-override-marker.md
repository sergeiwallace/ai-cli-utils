---
title: "[BUG-009] Remote Control startup override was still gated by an obsolete marker"
category: bugs
tags: [session, remote-control, growthbook, launcher, regression]
status: fix-deployed
severity: P1
task: AI-CLI-165
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-009] Remote Control startup override was still gated by an obsolete marker

**Status:** fix-deployed

**Severity:** P1 — previously paired Claude Code sessions did not receive the launch-time
override required for their automatic Remote Control reconnect.

**Created:** 2026-08-01

**Task:** `AI-CLI-165` (AIH-352 follow-up)

## Symptoms

Launching a previously Remote-Control-paired Claude Code session with `ai c` did not reconnect
automatically. The user had to use the manual pairing flow for each new or restarted session.

## Reproduction

1. Generate the Claude launch template for a worktree that has a `.claude` directory but no
   `.claude/.ai-cli-growthbook-toggle` file.
2. Execute the template's real pre-launch override block in that worktree.
3. Observe that `.claude/settings.local.json` is not created, so its
   `env.DISABLE_GROWTHBOOK` override is absent before Claude Code starts.

The frozen regression test reproduced this deterministically before the fix:

```text
tests/test_growthbook_launch_toggle.py::test_given_no_marker_when_toggle_runs_then_settings_local_json_gets_the_override
AssertionError: assert False
```

The failed assertion was `settings_path.exists()`, which is the missing pre-launch filesystem
effect, not a mock expectation.

## Root Cause Analysis

The generated launcher checked both that the engine was Claude Code and that the worktree carried
the marker file before writing the override. The marker was absent from all checked project
directories, so the launcher skipped the write for every ordinary launch.

That causes the visible failure directly:

```text
launch without marker
  -> marker-gated branch skips settings.local.json write
  -> DISABLE_GROWTHBOOK override is absent before Claude Code starts
  -> Remote Control's startup reconnect is ineligible
```

History shows that the marker gate was restored by a direct revert of an earlier unconditional
write. The repository's commit message, notes, changelog, and matching history provide no
explanation for the revert beyond naming the reverted commit. The related design record documents
the reason outside this repository: it was a temporary safety rollback until automatic restoration
of the override was available, to avoid leaving task tools disabled. That automatic restoration is
now documented as fixed, so the temporary marker gate no longer matches the intended launch
contract.

The suspicious generic commit identity is recurring across nearby history, not unique to either
the unconditional change or its revert. It therefore does not establish that those two commits
were accidental or automated test artifacts; no local history evidence establishes the author's
intent.

## Fix

Removed the marker-file predicate from the Claude Code branch in the generated session template.
The write remains worktree-scoped and remains excluded for the Gemini engine. It still merges only
`env.DISABLE_GROWTHBOOK` into `.claude/settings.local.json`.

## Verification

- Frozen RED before the production edit: `.venv/bin/python -m pytest
  tests/test_growthbook_launch_toggle.py -q` — 1 failed, 4 passed; the no-marker settings file
  was absent.
- GREEN after the production edit: the same command — 5 passed.
- The test executes the extracted generated Bash and Python snippet in a real subprocess against a
  temporary worktree; it asserts the resulting JSON file and value.

## 2026-08-15 Follow-up Investigation

**Status:** the original marker-gate fix remains deployed. No ai-cli-utils production change was
made in this follow-up because the remaining automatic-reconnect report could not be reproduced in
the available non-interactive environment.

### Reproduction and environment

- Revision investigated: `d6bbb77`.
- Installed Claude Code: `2.1.233`; the prior investigation used `2.1.226`.
- A generated Claude launch template was traced and its actual pre-launch Bash/Python block was
  executed in a temporary worktree. Before any `run_agent claude` call, it created
  `.claude/settings.local.json` containing `env.DISABLE_GROWTHBOOK: ""`.
- The isolated-worktree initializer symlinks the repository's `.claude` directory when it exists,
  so the normal `ai c` worktree path supplies the directory required by that write.
- The launcher history after `3254431` contains no change to the pre-launch override block. The
  SessionStart restorer remains registered in the installed user settings and in the hook manifest;
  it starts only after Claude Code has started, so it cannot prevent the pre-launch write.
- The focused pytest suite could not be run in this sandbox: its missing development environment
  required a package download, and DNS/network access is unavailable. The existing test remains a
  real subprocess/filesystem test of the generated block, rather than a mock or a copied snippet.

### Manual `/rc` root cause

The reported long-running process had no worktree-local override and inherited the user-global
`DISABLE_GROWTHBOOK=1` setting. Static inspection of the installed 2.1.233 Claude Code client
contains the explicit eligibility error: Remote Control requires feature-flag evaluation and is
unavailable when `DISABLE_GROWTHBOOK` is set. Therefore `/rc` and `/remote-control` are correctly
rejected in that process; this is not an ai-cli-utils slash-command implementation failure.

```text
global DISABLE_GROWTHBOOK=1 + no local empty override in the running process
  -> Claude Code disables GrowthBook feature-flag evaluation
  -> Claude Code rejects Remote Control eligibility
  -> manual /rc fails
```

The pre-launch override cannot retroactively repair an already-running Claude process. A new
process must start with the local empty override already present.

### Automatic reconnect finding

The reproducible launcher-level portion is correct: the current generated template writes the
override before every normal tmux-path Claude invocation, including `--continue` invocations.
The available evidence therefore rejects a reintroduction of the old marker-gate root cause and
does not support a launcher patch.

The residual report is outside what this sandbox can verify: pairing a device, invoking the
interactive slash command, and starting a real attached `ai c` session are unavailable here. The
2.1.233 binary does contain a reconnect diagnostic that instructs the user to retry or start a
fresh session without `--resume`; ai-cli-utils normally uses `--continue` when resuming a matching
conversation. That makes the Claude Code resume/reconnect path a concrete external hypothesis,
not a confirmed release regression. No release-note or live-UAT evidence in this repository
identifies when that behavior changed.

```text
fresh launcher process for an existing conversation
  -> ai-cli-utils writes the GrowthBook override before Claude starts
  -> ai-cli-utils may invoke Claude Code with --continue
  -> remaining reconnect failure, if reproduced, is in Claude Code's resume/Remote-Control path
```

Required live UAT, outside this sandbox: launch a genuinely new `ai c` process for a previously
paired conversation, confirm the local override exists before the Claude process begins, then
confirm the phone reconnects. If it does not, capture the Claude Code version and the client error
while comparing a non-resumed fresh conversation with the `--continue` launch. That discriminates
the remaining resume-path hypothesis without changing the launcher.

## 2026-08-15 Deployment and Pairing-State Investigation

**Status:** no new ai-cli-utils source defect was found. The active session templates were generated
from a checkout that predates the deployed launcher fix, so this is a local deployment/version-skew
finding. No production change is appropriate in this repository until that deployed template is
refreshed and live behavior is retested.

### On-disk comparison

The four numbered Claude sessions have persisted templates under the local ai-cli-utils state
directory. All four were written within seconds of each other and embed the same earlier source
commit. That commit precedes `3254431`, the commit that made the pre-launch
`DISABLE_GROWTHBOOK` write unconditional. Accordingly, none of the four persisted templates
contains the override block, even though the current `session_script.py` source does and the
existing regression test covers it.

This is load-bearing: the wrapper's automatic process restart reuses its existing generated
template. A Claude Code restart inside that wrapper is therefore not proof that the current
ai-cli-utils source was used. Reattaching with a launcher deployment that contains the fix must
regenerate the stable template before the reported automatic-reconnect behavior can be attributed
to the current launcher.

The worktree-local settings files were also not uniform at inspection time: one had an empty
`DISABLE_GROWTHBOOK` override and three had no local override file. These files are mutable
runtime state and do not prove the environment at process creation; the stale generated templates
are the reliable explanation for why three sessions would start without the pre-launch write.

The Claude Code session registry contained a `bridgeSessionId` and a messaging socket for the
reconnecting session and for one non-reconnecting session. The other two non-reconnecting sessions
had neither field. This rejects the strong form of the "only the reconnecting session has ever
paired" hypothesis: prior bridge state is not sufficient to explain successful auto-reconnect.
The registry is not a documented Remote Control status API, so its fields must not be treated as
proof of a currently live phone connection.

### Launcher-path comparison

The normal tmux launcher has no branch on the numeric session suffix. It computes the display name
and worktree path from that suffix, then calls the same `get_engine_script()` template generator.
For Claude Code, the current template writes the local empty GrowthBook override before each normal
agent launch, including `--continue` restarts. The only relevant alternate paths are explicit
bare or one-shot modes, neither of which was part of the reported ordinary `ai c N` launches.

### Claude Code evidence and scope boundary

Claude Code 2.1.233 exposes `--remote-control` but neither `claude --help` nor `claude doctor
--help` exposes a pairing-state or Remote Control status command. Static inspection of that client
also retains the diagnostic, "Couldn't reconnect to your Remote Control session. Retry, or start a
fresh session without --resume." It is an actionable Claude Code resume-path lead, but it does not
establish that `--continue` has the same failure condition.

The official Remote Control and settings documentation describe `remoteControlAtStartup` as an
auto-connect request subject to feature-flag eligibility. They do not document a dependency on a
previously paired device, a pairing-history cache, or a user-manageable bridge-registration file.
The 2.1.229--2.1.232 changelog/guide material does document recovery fixes for recorded Remote
Control sessions, but not a launcher-side way to create or copy that state. Consequently, there is
no safe ai-cli-utils workaround for the remaining bridge lifecycle behavior.

The current sandbox cannot re-fetch the primary sites because DNS access is unavailable. The source
links below are the same primary sources verified by the companion 2026-08-15 investigation; they
should be rechecked during live UAT before filing an upstream report.

- [Remote Control documentation](https://code.claude.com/docs/en/remote-control)
- [Settings reference](https://code.claude.com/docs/en/settings)
- [Claude Code changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md)
- [Remote Control bridge re-initialization report](https://github.com/anthropics/claude-code/issues/86084)

### Required next check

Refresh the launcher deployment, then reattach each affected session so its stable template is
regenerated. Before Claude Code starts, verify that the worktree-local settings file contains an
empty `env.DISABLE_GROWTHBOOK` value. Test a fresh conversation and the ordinary `--continue`
conversation separately. If the fresh process has the override and the session still fails while
another session succeeds, capture the exact Claude Code footer/diagnostic and report the remaining
asymmetry to Anthropic as a Remote Control bridge lifecycle issue. Do not copy registry or pairing
state between sessions.

### 2026-08-15 late correction (independent verification, orchestrating Claude session)

Direct inspection of the live state directory
(`~/.local/state/ai-cli-utils/sessions/c-sw-{1,2,4,6}.sh`) does **not** support the "stale
template predates commit `3254431`" framing above as the *current* state: all four stable session
scripts are present, same size, same `mtime` (regenerated together, well after `3254431`), and
**none of the four** — including `c-sw-4.sh`, the session where RC reportedly works — contains a
`DISABLE_GROWTHBOOK` block at all. So whatever currently distinguishes sw-4's working auto-reconnect
from sw-1/sw-2/sw-6's failure, it is not explained by a template-version skew at this snapshot; the
templates are already uniform. This does not necessarily mean the investigation above was wrong at
the moment it ran — the state directory may have been refreshed between that pass and this
verification (unclear by which mechanism/actor) — but the specific root-cause claim should not be
carried forward as settled without live re-verification. The asymmetry remains **unexplained**.
The research grounding (no documented Claude Code pairing-history/device-trust dependency; no
programmatic RC status API) still stands as useful context for any upstream report. Next step is a
live test: with all four templates now confirmed current and identical, have each of sw-1/sw-2/sw-6
restart and manually attempt `/rc`, and record whether the asymmetry persists — that is the only
way to distinguish "fixed by the incidental template refresh" from "a real, still-unexplained
per-session difference."

## 2026-08-16 Cross-Repo Auto-Reconnect Asymmetry Investigation

**Status:** root cause found and launcher fix added. The observed cross-repository split was a
deployment-time template skew, not evidence that the Agent Teams setting controls Remote Control.

### Agent Teams hypothesis

The only known successful comparison session had
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="0"`; the four unsuccessful sessions and this repository
used `"1"`. The broader settings inventory does not add independent outcome data: every checked
worktree inherits its repository's committed value, with one comparison repository using `"0"`
and the other checked repositories using `"1"`. It therefore remains a single correlated
configuration difference, not a causal sample.

Static inspection of the installed Claude Code 2.1.233 client resolves this variable only in its
Agent Teams/agent-swarms gate (also gated by its own GrowthBook experiment). The same client has
separate Remote Control policy, startup, and bridge symbols, but no discovered call, setting
description, or diagnostic connects Agent Teams to Remote Control eligibility or automatic
reconnect. The client does explicitly connect Remote Control eligibility to GrowthBook and to
account, policy, trusted-device, and network conditions. This weakens the Agent Teams hypothesis;
it does not prove a closed-source client cannot contain an undocumented interaction.

The official documentation, changelog, and public issue were re-requested, but this environment
could not resolve their hosts. The primary-source links recorded in the preceding investigation
remain the references to recheck during live UAT; this pass makes no claim that their current
contents were fetched.

### Template-version finding and root cause

The successful session's stable template was 27,035 bytes and carried
`_template_version="0.7.0.post20260815224300"`. Each unsuccessful session's template was 23,184
bytes and carried `_template_version="0.7.0"`. All five carried the same source-commit stamp, but
that stamp is not a content identity: the old templates lacked the pre-launch
`DISABLE_GROWTHBOOK` write, the `run_agent` wrapper, and other generator changes that the newer
template contained. The old template body predates the commit that introduced the pre-launch
override.

The launcher explains how this occurs. On `ai c`/`ai g`, `_auto_update_if_stale()` installs the
updated tool in a child process, then the still-running parent continued into session generation
with its already-imported, older template generator. It also wrote the current source stamp before
checking whether the child update succeeded. Thus a session launched during an update can start
without the override even though its template claims the latest source stamp; a later session
started from the newly installed entry point receives the new template. The newer template's
`direnv exec` wrapper is coincidental: it was introduced in the same later generator generation,
but no evidence ties direnv to Remote Control eligibility.

### Fix and verification

`_auto_update_if_stale()` now writes the source stamp only after a successful update and returns
whether it installed one. The `ai c`/`ai g` command re-execs the requested invocation through the
updated entry point before generating a stable template. Focused regression coverage verifies both
the failed-update stamp behavior and the re-exec path. This preserves normal launches when no
update occurs.

Required live UAT: install this change, start a new Claude Code process for an affected session,
and verify before process creation that its generated template contains the empty
`DISABLE_GROWTHBOOK` override. Then test automatic Remote Control reconnect. If it still fails
while the successful comparison session reconnects, report that remaining difference upstream
with the Claude Code version and Remote Control diagnostics; the launcher no longer has the
identified stale-generator path.

## Lessons Learned

A temporary safety rollback needs a tracked re-land condition and a test for the intended default
state. The prior test suite encoded the marker-gated behavior, so it could not detect that the
marker was absent fleet-wide or that the launch contract had changed.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Notes |
|---|---|
| 2026-08-01 | Removed the marker predicate after freezing a no-marker regression test RED. |
| 2026-08-15 | Re-traced the generated launcher, inspected the installed SessionStart wiring and Claude Code 2.1.233, and made no speculative production change. Confirmed the manual `/rc` failure is the inherited `DISABLE_GROWTHBOOK=1` eligibility gate; automatic reconnect still requires live UAT to attribute beyond the launcher. |
| 2026-08-15 | Found that the four active stable templates predate the unconditional override fix. Pairing/bridge registry state is mixed and does not prove the warm-pairing hypothesis; refresh the deployed template before escalating any remaining automatic-reconnect asymmetry as an upstream Claude Code issue. |
| 2026-08-16 | Root-caused the cross-repository split to an auto-update launch race: an old parent generated a template after its child installed a newer tool. The launcher now re-execs after a successful auto-update and records its update stamp only on success; Agent Teams remains an unsupported correlation. |

<!-- /doc:region name="fix_log" -->
