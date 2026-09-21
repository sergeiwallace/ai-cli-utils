---
title: iTerm2 Cross-Machine VS Code File Routing
category: design
tags: [design, iterm2, vscode, remote-ssh, macos]
status: draft
source: "tier-1-founding-ask"
template_version: "design-1.0.0"
delegation_provenance:
  version: 2
  contributors:
    - role: design-and-implementation
      method: "Codex"
      date: "2026-09-21"
---

# iTerm2 Cross-Machine VS Code File Routing

## Founding Ask Coverage

`founding_ask_ref`: N/A — this Tier 1 dispatch supplied no canonical_root_json or founding_ask_ref_sha256.

The original literal ask is preserved below. The later re-scope is
transcribed with its private tracker identifier redacted because this repository is public; the
unredacted record remains authoritative in the dispatch.

```text
can you /delegate /codex /research-doc (first see if we have existing one that discusses this) to see how i can cmd + click a full path to a doc / file in iterm2 on macos and have it open that file in my ssh-connected vs code instance that has the file system on my framework machine? the problem is that right now, if I'm in a cc session that's hosted on my framework machine, it won't redirect to try to open that file on my vs code window ssh connected to framework machine. if possible, i'd like it to know whether the cc session im using cmd+click to open the file path is on macos and then if available, try to open it on my macos local vs code window and if its a framework cc session then iterm should route to try to open it in the framework ssh connected vs code window. add a /beads-task and cc task for this and set to in progress and ship changes and then see if we ahve any resarch and/or plan or design docs on this and if not then /delegate /codex /research-doc to see if this is possible in iterm2 on macos.

Follow-up build sequence: research whether and how this is possible in iTerm2 on macOS, then use
the research to produce an implementation design.

Current-cycle re-scope: determine current status, reuse completed research, create or update the
relevant design, resolve its decisions under the decision framework, investigate open questions
as needed, and continue autonomously when no human decision is required. [Private tracker
identifier redacted for public-repository hygiene.]
```

The following index is non-authoritative; the raw dispatch governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| FAR-1 | Original founding ask: the local/remote routing invariant is specified in Problem Statement, Integration, and the G2/G3 acceptance gates. Tracking was already established outside this document. |
| FAR-2 | Original build sequence: existing research was read first; this design records the resulting approach and implementation plan. |
| FAR-3 | Current-cycle follow-up: D-1 is self-resolved under the required authority test; implementation proceeds through Phase 1, while physical Cmd-click UAT remains blocked as G1. |

**Status:** DRAFT — Phase 1 scaffolding is implemented; G1 remains a blocking physical UAT.

**Created:** 2026-09-21

**Research:** [iTerm2 Cross-Machine VS Code File Routing](../research/iterm2-cross-machine-vscode-routing.md)

<!-- doc:region name="overview" kind="replaceable" -->

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Routing Components](#routing-components)
  - [Configuration](#configuration)
  - [Generated iTerm2 profile](#generated-iterm2-profile)
  - [Local VS Code helper](#local-vs-code-helper)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
  - [Phase 1: Opt-in routing scaffolding](#phase-1-opt-in-routing-scaffolding)
  - [Phase 2: Physical Cmd-click UAT](#phase-2-physical-cmd-click-uat)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  - [D-1: Host-routing mechanism](#d-1)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Executive Summary

Launcher-managed remote sessions will carry an explicit VS Code Remote-SSH authority in their
generated iTerm2 Dynamic Profile. Cmd-click routing for those opt-in profiles calls a local helper
that validates the remote path and invokes VS Code with a structured argument list; local profiles
remain unchanged and continue inheriting the default-app behavior. The launcher already knows the
remote machine, so the click path does not inspect processes, tmux state, or remote environment
variables. A real physical Cmd-click is still required to prove that the installed iTerm2 passes a
remote-only path to the helper. That test is G1 and blocks declaring the feature operational; a
profile-specific Make Hyperlink trigger is documented only as the fallback if G1 fails.

## Problem Statement

A path printed in a local iTerm2 session can open in local VS Code, but the same interaction in a
remote launcher-managed pane cannot select the VS Code Remote-SSH authority that owns the path.
The design must route remote paths to the configured authority without changing local behavior or
treating terminal text as shell syntax.

## Design Overview

The implementation adds an optional `vscode_authority` value to each remote-machine configuration.
Only an explicitly configured remote launch threads that value through the pre-transport iTerm2
profile setup into the Dynamic Profile generator. The generated remote profile invokes
`ai internal open-vscode-remote` with the baked authority and iTerm2's file and line substitutions;
profiles without an authority omit the `Semantic History` key exactly as before.

The local helper validates an allowlisted SSH-alias-shaped authority, a control-character-free
absolute POSIX path, and an optional positive integer line. It then runs `code --remote
ssh-remote+<authority> --goto <path>` or the line-qualified form as an argument vector with
`shell=False`. Version 1 intentionally supports launcher-managed sessions only. Nested SSH keeps
routing to the outer launch authority, and Remote-SSH window selection remains whatever VS Code's
documented `--remote` behavior provides.

## Routing Components

### Configuration

Each `[remote.machines.<alias>]` table may define `vscode_authority`. The value is an explicit
Remote-SSH/SSH config alias and is never inferred from a display label, transport endpoint, or
hostname. Absence preserves current behavior.

### Generated iTerm2 profile

The local launcher resolves the chosen machine before starting SSH or mosh and passes the optional
authority into `_emit_iterm2_profile_setup`, which forwards it to `generate_dynamic_profile`.
Remote opt-in profiles receive a session-specific Semantic History command. Local sessions and
remote sessions without the field receive no Semantic History override, preserving inheritance
from the parent profile.

The profile is code-ready for G1, not UAT-approved. G1 must establish that current iTerm2 supplies
the clicked path byte-for-byte for a path absent from the Mac. If it does not, the command must not
be presented as operational and Phase 2 moves to the documented trigger spike.

### Local VS Code helper

The helper owns validation and command construction. It rejects relative paths, control
characters, malformed authorities, zero/negative/non-integer lines, and never invokes a shell.
It reports a missing VS Code launcher or a non-zero VS Code result through the internal command's
exit status instead of opening the same path locally.

## Data Model

| Field | Location | Type | Required | Meaning |
|---|---|---:|---:|---|
| `vscode_authority` | `[remote.machines.<alias>]` or legacy `[remote]` | string | No | Exact VS Code Remote-SSH/SSH config alias baked into that session's iTerm2 profile. |

The repository uses dictionaries for remote-machine configuration rather than a schema dataclass,
so the field requires documentation, selection-path plumbing, and focused tests rather than a
model migration.

## Integration

1. `_do_session_launch` selects the configured remote machine and obtains `vscode_authority`.
2. `_emit_iterm2_profile_setup` generates the profile locally before SSH or mosh takes over.
3. `generate_dynamic_profile` adds remote Semantic History only when the authority is present.
4. iTerm2 substitutes the clicked path and optional line and starts the local internal helper.
5. The helper validates inputs and invokes local VS Code for `ssh-remote+<authority>`.

No remote binary participates in the file open. Local sessions keep their existing parent-profile
and macOS default-app path. The helper omits `--reuse-window` because VS Code documents that flag as
targeting the last active window, not the matching remote authority.

## Implementation Phases

### Phase 1: Opt-in routing scaffolding

- **Scope:** Land the configuration field, remote-only profile plumbing, local helper, and tests.
- **Deliverables:**
  - Files modified: `src/ai_cli/config.py`, `src/ai_cli/main.py`, `src/ai_cli/iterm2.py`,
    `src/ai_cli/icon_generator.py`, and focused existing tests.
  - Files created: `src/ai_cli/vscode.py`, `tests/test_vscode.py`.
- **Tasks + acceptance criteria:**
  - **T-1.1 Validate and open a remote path.**
    - [x] When given a valid authority, absolute path, and optional positive line, the helper shall
      invoke `code --remote ssh-remote+<authority> --goto <path>[:<line>]` as an argument vector.
    - [x] If any input is malformed, then the helper shall reject it before spawning VS Code.
    - [x] When paths contain spaces, quotes, dollar expressions, backticks, semicolons, or Unicode,
      the helper shall preserve them as one argument and shall not interpret them as shell syntax.
  - **T-1.2 Generate remote-only profile behavior.**
    - [x] When a selected remote machine has `vscode_authority`, the launcher shall bake that
      authority into its generated profile command.
    - [x] When a local session or unconfigured remote session generates a profile, the generator
      shall omit `Semantic History`, preserving inherited default-app behavior.
- **Exit gate:** Focused tests pass, the full `pytest` suite passes, and `ruff check` plus
  `ruff format --check` pass for every touched Python file.

### Phase 2: Physical Cmd-click UAT

- **Scope:** Perform the real iTerm2/VS Code runtime gates; no automation may substitute for the
  physical Cmd-click required by G1.
- **Tasks + acceptance criteria:**
  - **G1 — parser (BLOCKING OPEN UAT):** When a user physically Cmd-clicks a printed absolute
    remote path that is absent locally, iTerm2 shall deliver that path byte-for-byte to the helper.
  - **G2 — local parity:** When a user Cmd-clicks a local path, the existing local default-app
    behavior shall remain unchanged.
  - **G3 — host isolation:** When two remote authorities have simultaneous panes, each pane shall
    open only through its baked authority.
  - **G4 — file syntax/security:** When paths cover extensionless names, spaces, Unicode, no-line,
    line-number, and shell metacharacters, the open shall preserve data without shell execution.
  - **G5 — window behavior:** When zero, one, or multiple matching windows exist, observed VS Code
    selection behavior shall be recorded without claiming stronger reuse guarantees.
  - **G6 — transport:** When SSH and mosh/tmux sessions reconnect, their generated profile shall
    remain assigned.
  - **G7 — failure UX:** If authority, `code`, authentication, or connection state is invalid,
    then the system shall fail visibly without opening a wrong local file.
- **Exit gate:** A human records bounded evidence for G1-G7. If G1 fails, stop Approach 1 rollout
  and design a profile-specific Make Hyperlink trigger spike; do not implement that fallback
  speculatively.

## Implementation Audit

| # | Phase | Section / Decision | Verified | Notes |
|---|---|---|---|---|
| 1 | Phase 1 | Design Overview reflects the shipped scaffolding | - [x] | Re-read against the implementation on 2026-09-21. |
| 2 | Phase 1 | Configuration, profile plumbing, and helper match D-1 | - [x] | Focused tests pass. |
| 3 | Phase 1 | Local profiles remain byte-for-byte free of a Semantic History override | - [x] | Regression test passes. |
| 4 | Phase 2 | G1 physical Cmd-click parser gate | - [ ] | Blocking; no available tool can perform this UAT. |

**Audit completed:** 2026-09-21 for Phase 1. The whole design remains pending Phase 2 physical UAT.

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | iTerm2 omits the remote-only path substitution. | Approach 1 cannot route a click. | Keep G1 blocking; if it fails, stop rollout and spike the documented Make Hyperlink fallback. |
| 2 | A configured authority does not match VS Code's Remote-SSH identity. | VS Code opens a new connection or fails. | Require an explicit alias and reject values outside the documented allowlist. |
| 3 | Terminal text reaches a shell before helper validation. | Crafted output could affect command parsing. | Keep routing opt-in pending G1, quote generated static values, reject control characters in the helper, and include metacharacter cases in UAT; residual iTerm2 substitution behavior remains a G1/G4 concern. |
| 4 | Nested SSH changes the effective host. | A path may be sent to the outer host. | Define launcher authority as version 1's invariant and document nested SSH as unsupported. |
| 5 | VS Code selects an unexpected window. | The file may not appear in the desired existing window. | Omit `--reuse-window` and record actual zero/one/multiple-window behavior in G5. |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|---|---|---|---|---|---|---|
| D-1 | How should a Cmd-click select the file's host? | (a) launch-time Dynamic Profile, (b) global introspecting wrapper, (c) iTerm2 API/profile switching, (d) Make Hyperlink trigger, (e) SSHFS | (a) | (a) | No | Criterion 2: the launcher already owns the cross-boundary host fact; baking it once avoids unreliable click-time inference. | `✅ Resolved by Codex` |

### Decision Details

<a id="d-1"></a>

#### D-1: Host-routing mechanism — `✅ Resolved by Codex: (a) launch-time Dynamic Profile`

**Context.** The launcher knows the selected remote machine before it creates the pane, while
iTerm2's documented click substitutions do not expose a trustworthy live hostname. The decision
must preserve local behavior and support SSH plus mosh/tmux sessions.

##### (a) Launch-time Dynamic Profile with a baked authority

**Pros:**

- Uses the launcher's authoritative machine selection.
- Fits the existing per-session profile lifecycle and mosh pre-transport setup.
- Keeps local profiles unchanged and routes with a small local helper.

**Cons:**

- Depends on current iTerm2 passing a remote-only path to Semantic History.
- Requires users to configure the exact VS Code Remote-SSH authority.
- Cannot follow nested SSH to a second host.

##### (b) One global wrapper with click-time host introspection

**Pros:**

- Centralizes routing in one command.
- Could cover panes not launched by this package.

**Cons:**

- Process trees, remote environment variables, tmux, nested SSH, and mosh do not provide a
  dependable pane-to-host mapping to a local click handler.
- A wrong inference can open a valid path on the wrong machine.

##### (c) iTerm2 Python API or Automatic Profile Switching

**Pros:**

- Can mutate already-running or unmanaged sessions.
- Can use session-local profile properties when shell integration is available.

**Cons:**

- Adds a resident automation/shell-integration dependency.
- Regular tmux sessions limit the shell-integration route.

##### (d) Profile-specific Make Hyperlink trigger

**Pros:**

- May bypass Semantic History's local file-existence recognition.
- Retains a profile-specific authority.

**Cons:**

- Requires a safe, sufficiently precise absolute-path regex and URI encoding.
- False matches and path-boundary behavior require a separate spike.

##### (e) Mount the remote file system locally

**Pros:**

- Makes remote paths locally addressable after mapping.
- Decouples opening from Remote-SSH CLI routing.

**Cons:**

- Introduces mount lifecycle, latency, consistency, and path-identity concerns.
- Solves a much broader filesystem problem than Cmd-click routing requires.

##### Recommendation

> **Decision:** ✅ Resolved by Codex (2026-09-21); confidence: high — (a) Launch-time Dynamic Profile with a baked authority.
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-sol; ai-effort=xhigh; ai-profile=architect -->
<!-- decision-lineage: decision-id=iterm2-cross-machine-vscode-routing/D-1; decision-topic=host-routing-mechanism; governs=design:iterm2-cross-machine-vscode-routing; normalized-proposition=bake-remote-authority-into-launch-time-dynamic-profile; applicability=launcher-managed-sessions; outcome-id=dynamic-profile-primary; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

**Authority test.** Gate A does not fire because recording this choice performs no destructive or
outward-facing action. Gate B does not fire because this is fresh authoring of an explicitly open
decision, not protected or human-pending text. Gate C does not fire because the founding ask
explicitly authorizes this new design and asks this decision to be resolved; no existing ratified
requirement is changed. Gate D permits self-resolution with high confidence: criterion 2 decides
the call because the launcher already owns the cross-boundary host selection, and the official
iTerm2 and VS Code interfaces are independently corroborated in the linked research.

The choice is a two-way configuration door but crosses the launcher/profile/editor boundary. Its
first Con is mitigated by keeping G1 blocking and promoting option (d) only after a failed live
test. Its second is mitigated by an explicit validated `vscode_authority` rather than inference.
Its third is bounded by making launcher authority the documented version 1 invariant and listing
nested SSH as unsupported. The research's official documentation checks provide external-source
corroboration; no third-party package or unmaintained pattern is adopted.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. On the installed iTerm2 version, does a physical Cmd-click populate the file substitution for
   a full absolute remote path that does not exist locally?
2. What exact zero/one/multiple-window behavior does the installed VS Code build exhibit for
   `--remote ... --goto`?
3. How do first-time authentication and stale Remote-SSH connections report failures to the user?

These are Phase 2 UAT observations rather than unresolved design choices. Question 1 is G1 and
blocks declaring the routing operational; questions 2 and 3 bound claims and failure UX but do not
change the selected architecture.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <G1 evidence>
> 2. <window-routing evidence>
> 3. <failure evidence>
> - <enter feedback here>

## Approval Log

| Date | Decision | Actor | Notes |
|---|---|---|---|
| 2026-09-21 | D-1 | Codex | Self-resolved at the flagship tier under the authority test; no human choice recorded. |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

<!-- /doc:region name="decisions" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

<!-- /doc:region name="approval_log" -->
