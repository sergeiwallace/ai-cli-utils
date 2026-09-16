---
title: VS Code integrated-terminal tab naming
category: design
tags: [design, terminal, tmux, vscode, tab-title]
status: DRAFT
source: "claude-fable-5-1-2026-09-16"
template_version: "design-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
task: AI-CLI-5abz
---
<!-- Full structure: see TEMPLATE.md in this directory -->

# VS Code integrated-terminal tab naming

## Founding Ask Coverage

`founding_ask_ref`: N/A — Tier 1 dispatch with no `canonical_root_json`. The literal ask is
reproduced below with **one redaction**: the requester's employer name is replaced by `[work]`,
because this repository is a public package and an employer name is a personal identifier. Nothing
else is changed.

```text
also, can you adda a /beads-task and cc task and set to in progress for ai-cli-utils `ai` launcher
to check if its in the integrated terminal and vs code and if so, then it updates the integrated
terminal tab name to be the cc session name e.g. `ai-cli-1`. right now, if i am launching the cc
session in a tmux process (which is default), then it sets the tab name to just default to `tmux`.
if im in a bare cc session, it does work automatically, though, at least on this [work] machine
(which is the only place i use vs code integrated terminal,  i use macos iterm2 terminal app which
has its own functioanl setup for naming tabs and panes etc which is separate). so go ahead and
/implement this and when its done, we can test with a test cc session to check if its working.
```

The index below is non-authoritative; the ask above governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| "check if its in the integrated terminal and vs code" | D-3 — detection happens in the launcher process, because the obvious signal is destroyed inside tmux. |
| "updates the integrated terminal tab name to be the cc session name e.g. `ai-cli-1`" | D-1 + D-5 — tmux emits the title outward; the string is the tmux window name, which already equals `ai-cli-1`. |
| "in a tmux process (which is default) … it sets the tab name to just default to `tmux`" | Diagnosed and **re-attributed** in [Problem Statement](#problem-statement); the stated cause was falsified by measurement. |
| "if im in a bare cc session, it does work automatically" | Explained in [Problem Statement](#problem-statement); protected by AC-2 and by D-3's decision to add no emitter to the bare path. |
| "i use macos iterm2 … which is separate" | Treated as a frozen constraint: D-2 adds a new module rather than editing the iTerm2 path, and AC-3 is a parity criterion. |
| "so go ahead and /implement this" | **Not executed in this run.** This document is the deliverable; implementation is a separately authorised run, gated on Phase 0. |
| "when its done, we can test with a test cc session to check if its working" | Phase 2 exit gate is exactly that live check; it is a human gate and cannot be self-certified. |

**Status:** DRAFT

**Created:** 2026-09-16

**Task:** AI-CLI-5abz

**Research:** [📄 ai-cli-utils/docs/research/vscode-integrated-terminal-tab-naming.md](../research/vscode-integrated-terminal-tab-naming.md)

<!-- doc:region name="overview" kind="replaceable" -->

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Core Component: outer-terminal title configuration](#core-component-outer-terminal-title-configuration)
  - [New module](#new-module)
  - [Call-site integration](#call-site-integration)
  - [Why this satisfies the no-stray-escape requirement by construction](#why-this-satisfies-the-no-stray-escape-requirement-by-construction)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
  - [Phase 0: resolve the one unobservable fact (human gate)](#phase-0-resolve-the-one-unobservable-fact-human-gate)
  - [Phase 1: detection, tmux title configuration, tests](#phase-1-detection-tmux-title-configuration-tests)
  - [Phase 2: live verification and documentation](#phase-2-live-verification-and-documentation)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  - [D-1: Which mechanism sets the tab name](#d-1)
  - [D-2: Where the VS Code branch lives](#d-2)
  - [D-3: Who detects and emits, and when](#d-3)
  - [D-4: Whether to touch tmux automatic-rename, and at what scope](#d-4)
  - [D-5: What string the tab shows](#d-5)
- [Feedback Rounds](#feedback-rounds)
- [Approval Log](#approval-log)

## Executive Summary

The `ai` launcher will tell tmux to forward a window title to the VS Code integrated terminal, so a
session launched as `ai-cli-1` shows `ai-cli-1` on its tab instead of `tmux`. The change is one new
small module and two call-site additions; it emits no escape sequences of its own and does not touch
the iTerm2 code path at all. The two most consequential things a reader should know are, first, that
the originally reported cause was wrong — tmux is not overwriting a title, it is **absorbing** one
and never re-emitting it, after which VS Code falls back to naming the tab after the foreground
process (`tmux`) — and second, that the obvious detection signal `TERM_PROGRAM=vscode` is
**overwritten by tmux inside every pane**, so detection must happen in the launcher process and never
in the generated session script. Out of scope: any change to the iTerm2 path, any change to the bare
(non-tmux) path, tab icons and colours, and editing the user's VS Code settings file (which is not
even reachable from the machine `ai` runs on). One fact could not be established from inside a
terminal and is a hard gate on Phase 1: whether VS Code honours a sequence-set title while `tmux` is
the foreground process. A probe is already emitted and needs one human glance.

## Problem Statement

A session launched under tmux in the VS Code integrated terminal shows `tmux` on its tab instead of
the session name. The same launch without tmux already shows the session name, so the outer terminal
demonstrably accepts a title — the capability is not in question.

The measured causal chain (full evidence, with commands and outputs, in the research doc) is:

1. The agent CLI emits its own OSC title. tmux **absorbs** it into `pane_title` — measured live as
   `pane_title=✳ ai-cli-1` — rather than passing it outward.
2. tmux only emits a title to its client when `set-titles` is on. It is off, which is tmux's own
   default; a grep of `src/` for `set-titles` returns zero matches, so nothing here ever changed it.
3. With no title arriving, VS Code applies its default tab-title template, which is `${process}`.
   The detected process is `tmux`.

Two corrections to the premises this work started from, both of which change the fix:

- **`automatic-rename off` is not the remedy.** It is already applied — and applied to *every*
  session, because `_configure_tmux_for_iterm2()` is called at `src/ai_cli/main.py:3312` and `:3426`
  with no `_is_iterm2()` guard at either call site and none inside the function, despite its name.
  Measured live: `automatic-rename=0`, `allow-passthrough=all`. Two of the three plumbing pieces are
  therefore already in place for VS Code; the missing one is `set-titles`.
- **The working bare case is not this package's behaviour.** The bare launch path emits no title
  sequence at all (it reaches `_exec_with_direnv` and `os.execvp`, bypassing the pre-launch emitter
  at `main.py:3255`). It works because VS Code ships
  `terminal.integrated.tabs.allowAgentCliTitle`, default `true`, which permits agent CLIs to set the
  tab title by escape sequence. So AC-2's "must not regress" is satisfied by *not adding an emitter
  to the bare path* — a second emitter there would be the only way to break it.

## Design Overview

**Status:** stub — to be filled during/after implementation.

## Core Component: outer-terminal title configuration

### New module

A new module `src/ai_cli/vscode_terminal.py`. It holds exactly two public functions and no state.
Interfaces, as pseudocode:

```text
def is_vscode_terminal(env: Mapping[str, str] = os.environ) -> bool:
    """True when the LAUNCHER process runs in the VS Code integrated terminal.

    Valid only in the launcher, before tmux exists. tmux overwrites TERM_PROGRAM in
    every pane with the literal "tmux" (measured), so this must never be called from
    inside a pane or emitted into the generated session script.
    """
    if env.get("TERM_PROGRAM") == "vscode":
        return True
    if env.get("TMUX"):
        # The launcher is itself inside a pane, so TERM_PROGRAM was overwritten.
        # The forwarded value survives in the tmux SESSION environment.
        return tmux_show_environment("TERM_PROGRAM") == "vscode"
    return False


def configure_tmux_titles(session_id: str, *, enable: bool) -> None:
    """Turn tmux's OUTWARD title emission on for a VS Code client, or off otherwise.

    Emits no escape sequence itself: tmux formats and sends OSC 0 on its own, per its
    set-titles option. Both calls are best-effort and never raise -- an old tmux that
    does not know an option ignores it, matching the existing tmux-option call style.
    """
    run(["tmux", "set-option", "-t", session_id, "set-titles", "on" if enable else "off"])
    if enable:
        run(["tmux", "set-option", "-t", session_id, "set-titles-string", "#{window_name}"])
```

`set-titles` is a **session** option, so `-t <session_id>` scopes both writes to this session and
leaves the user's other tmux sessions and their global options untouched.

`enable=False` is not a no-op and is load-bearing: it is what restores today's behaviour when a
session created under VS Code is later re-attached from a different terminal (see Risk 1).

### Call-site integration

Two call sites, both of which already exist and already run in the launcher process where
`TERM_PROGRAM` is trustworthy:

| Site | Existing code | Addition |
|---|---|---|
| `src/ai_cli/main.py:3312-3313` (session create) | `_configure_tmux_for_iterm2(session_id)` then `_rename_tmux_window(session_id, ai_name)` | after the rename: `configure_tmux_titles(session_id, enable=is_vscode_terminal() and not _is_iterm2())` |
| `src/ai_cli/main.py:3426-3427` (attach to an existing session) | the same pair on `identity.session_id` | the same addition, so the decision is **re-taken on every attach** |

Ordering: after `_rename_tmux_window`, so the window already carries `ai-cli-1` and no transient
wrong title can be emitted. `is_vescode` and `_is_iterm2` are mutually exclusive by construction —
`_is_iterm2()` wins — which is what makes AC-3 a structural property rather than a hope.

### Why this satisfies the no-stray-escape requirement by construction

AC-4 requires that a terminal which is neither VS Code nor iTerm2 receives **no** title escape
sequence, and that nothing fails. This design emits no escape sequence in any branch: it sets a tmux
option, and tmux decides what to send to its own client. A plain tty, a pipe, `TERM=dumb`, or CI
therefore cannot be corrupted by this feature, because no bytes are ever written to stdout. That is a
material advantage over the escape-sequence alternative considered in D-1(b), which writes bytes and
would need its own guard to avoid polluting a captured stream.

## Data Model

No persistent state, no new configuration file, and no new configuration key. The only values
involved already exist:

| Value | Source | Shape for a session the user calls `ai-cli-1` |
|---|---|---|
| `ai_name` | `build_session_name()` (`src/ai_cli/session.py:644`) | `ai-cli-1` |
| `session_id` | `build_session_name()` (`src/ai_cli/session.py:584`) | `c-ai-cli-1` |
| tmux `window_name` | set by `_rename_tmux_window(session_id, ai_name)` | `ai-cli-1` — the string the tab will show |
| tmux `pane_title` | the agent CLI's own OSC title, absorbed by tmux | `✳ ai-cli-1` — the alternative rejected in D-5 |

## Integration

- **iTerm2 path** — untouched. No file under the iTerm2 path is edited; `set-titles` remains `off`
  for iTerm2 sessions, which is today's value, so the emitted byte stream for an iTerm2 launch is
  unchanged. Related design: [📄 ai-cli-utils/docs/designs/iterm2-title-color-system.md](iterm2-title-color-system.md).
- **tmux configuration** — this design adds a third tmux option alongside the two
  `_configure_tmux_for_iterm2()` already sets unconditionally. It does not modify that function; see
  D-2 and Risk 3 for the naming debt that creates and how it is handled.
- **`AI-CLI-4rg1`** (broader VS Code tab automation: icons, colours, context-driven changes) is
  related and deliberately not absorbed. This design ships the tab **name** only.

## Implementation Phases

### Phase 0: resolve the one unobservable fact (human gate)

- **Scope:** answer Open Question 1. Nothing else; no code.
- **Deliverables:** the answer recorded in this doc's Open Questions section.
- **Why it is a gate:** if VS Code's agent-CLI title allowance is keyed on the *detected foreground
  process* being a recognised agent, then under tmux the process is `tmux`, no sequence title is
  displayed, and both D-1(a) and D-1(b) fail — the remedy collapses to documenting a user setting,
  which is a different and much smaller deliverable. Implementing Phase 1 before this is answered
  risks building the wrong thing.
- **How:** the probe is already emitted (research doc §7). A human reads the tab label of the session
  that ran it. `VSCODE-TITLE-PROBE-DCS` → proceed. `VSCODE-TITLE-PROBE-DIRECT` → proceed, and expect
  D-1(a) to work while D-1(b) does not. Still `tmux` → stop and re-open D-1.
- **Exit gate:** Open Question 1 carries `**[RESOLVED]**` with the observed label.

### Phase 1: detection, tmux title configuration, tests

- **Scope:** the new module and the two call-site additions.
- **Deliverables:**
  - Files created: `src/ai_cli/vscode_terminal.py`, `tests/test_vscode_terminal.py`
  - Files modified: `src/ai_cli/main.py` (two call sites only)
  - Files NOT modified, deliberately: `src/ai_cli/iterm2.py`, `src/ai_cli/session_script.py`
  - Tests added: `tests/test_vscode_terminal.py` (detection matrix + argv assertions), plus one
    parity test asserting the iTerm2 branch is still selected for iTerm2 inputs
- **Tasks + acceptance criteria** (EARS; each independently testable and falsifiable):
  - **T-1.1 detection** — `is_vscode_terminal()`, launcher-side only.
    - [ ] `When TERM_PROGRAM is "vscode" and TMUX is unset, is_vscode_terminal shall return True.`
    - [ ] `When TERM_PROGRAM is "tmux" and TMUX is set and the tmux session environment reports TERM_PROGRAM=vscode, is_vscode_terminal shall return True.` (AC-5: this is the tmux combination that is the whole defect)
    - [ ] `Where the terminal is iTerm2 (LC_TERMINAL="iTerm2"), the launcher shall select the iTerm2 branch and shall not enable tmux title emission.` (AC-3)
    - [ ] `If TERM_PROGRAM is absent, empty, or any other value and TMUX is unset, then is_vscode_terminal shall return False.` (failure path)
    - [ ] `If the tmux show-environment lookup fails or returns no value, then is_vscode_terminal shall return False and shall not raise.` (failure path)
  - **T-1.2 tmux title configuration** — `configure_tmux_titles()`.
    - [ ] `When enable is True, the system shall run exactly "tmux set-option -t <session> set-titles on" and "tmux set-option -t <session> set-titles-string #{window_name}".`
    - [ ] `When enable is False, the system shall run exactly "tmux set-option -t <session> set-titles off" and shall not set set-titles-string.`
    - [ ] `If the tmux invocation exits non-zero, then the system shall continue without raising and shall not abort the launch.` (failure path)
    - [ ] `Where the option is written, the system shall pass -t <session_id> so the write is session-scoped.` (blast-radius criterion, D-4)
  - **T-1.3 call-site wiring** — both sites.
    - [ ] `When a session is created in the VS Code integrated terminal, the launcher shall enable tmux title emission after renaming the window.` (AC-1)
    - [ ] `When an existing session is attached from the VS Code integrated terminal, the launcher shall enable tmux title emission.` (AC-1, re-attach)
    - [ ] `Where the outer terminal is neither VS Code nor iTerm2, the launcher shall call configure_tmux_titles with enable False and shall emit no escape sequence to stdout.` (AC-4)
    - [ ] `Where the bare (non-tmux) path is taken, the launcher shall emit no title escape sequence, unchanged from today.` (AC-2 parity)
  - **T-1.4 iTerm2 parity** — inventory-and-parity for the frozen path.
    - [ ] `Where inputs identify iTerm2, the set of subprocess calls and stdout bytes produced by a launch shall be identical to the pre-change behaviour.` (AC-3; assert on the recorded call list, not on prose)
- **Exit gate:** every Phase-1 AC green; `ruff format --check src/ tests/ scripts/`, `ruff check src/ tests/ scripts/` and the full `pytest` suite pass; `git diff` shows zero changes under `src/ai_cli/iterm2.py` and `src/ai_cli/session_script.py`; fresh-context diff review against these ACs.

### Phase 2: live verification and documentation

- **Scope:** the live check the requester asked for, plus docs.
- **Deliverables:** a launched test session observed in the VS Code integrated terminal; the
  [Design Overview](#design-overview) section filled in; a short user-facing note recording the
  `terminal.integrated.tabs.title` / `allowAgentCliTitle` settings as the user-side remedy if a tab
  still shows a process name.
- **Exit gate — human, and not self-certifiable:** the requester launches a test session under tmux
  in the VS Code integrated terminal and confirms the tab reads the session name. AC-6 is satisfied
  by this recorded live check together with T-1.2's argv assertions. **No unit test in this
  repository can observe the outer terminal's tab, so green tests are not sufficient to close
  AI-CLI-5abz.**

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT. Any gap restarts from
> implementation, not from planning.

| # | Phase | Section / Decision | Verified | Notes |
|---|-------|--------------------|---------|-------|
| 1 | N/A | **Design Overview filled** with concrete implementation knowledge, not left as a stub | - [ ] | |
| 2 | Phase 0 | Open Question 1 resolved with an observed tab label, not an assumption | - [ ] | |
| 3 | Phase 1 | D-1: tmux `set-titles` is the mechanism; no escape sequence is emitted by this feature | - [ ] | |
| 4 | Phase 1 | D-2: the VS Code branch lives in a new module; `iterm2.py` diff is empty | - [ ] | |
| 5 | Phase 1 | D-3: detection is launcher-side only; no `$TERM_PROGRAM` test exists in the generated session script | - [ ] | |
| 6 | Phase 1 | D-4: no change to `automatic-rename`; every option write is `-t <session>` scoped | - [ ] | |
| 7 | Phase 1 | D-5: the emitted title string is the window name (`ai-cli-1`) | - [ ] | |
| 8 | Phase 1 | Risk 1 mitigation present: `enable=False` writes `set-titles off` on a non-VS-Code attach | - [ ] | |
| 9 | Phase 2 | Live check performed and recorded by the requester | - [ ] | |

**Audit completed:** <!-- YYYY-MM-DD -->

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | A session created under VS Code is later re-attached from iTerm2, carrying `set-titles on` into iTerm2 and emitting an OSC 0 that disturbs the iTerm2 Name field. | Medium — would violate AC-3 in a path unit tests would not exercise. | The attach call site (`main.py:3426`) re-decides on every attach and writes `set-titles off` when the outer terminal is not VS Code. Covered by a T-1.3 AC, not left as a note. |
| 2 | Phase 0 comes back negative: VS Code's title allowance is gated on the foreground process being a recognised agent CLI. | High — invalidates D-1(a) and (b). | Phase 0 is a gate before any code is written. The fallback is documentation of the user-side setting (D-1(c)), a smaller deliverable. Nothing is implemented speculatively. |
| 3 | `_configure_tmux_for_iterm2()` configures tmux for every terminal despite its name; a future reader assumes an iTerm2 guard that is not there and adds VS Code logic inside it. | Low now, medium over time. | This design deliberately does **not** rename it (out of scope, and it is on the frozen path's file). The new module's docstring records the measured fact, and the rename is filed as a separate follow-up issue rather than smuggled in. |
| 4 | `set-titles-string '#{window_name}'` also reaches any other client attached to the same session (for example a second terminal on another machine). | Low — the title is correct for any client; only the delivery is broader. | Accepted and documented. The string is the session's own name, so a second client receiving it is not a wrong result. |
| 5 | An older tmux does not support an option and the launch fails. | Low. | Both writes are best-effort with `check=False`, matching the existing tmux-option call style; a T-1.2 failure-path AC asserts the launch continues. |

## Open Questions

1. **Does VS Code display a sequence-set tab title while `tmux` is the detected foreground process,
   or is `terminal.integrated.tabs.allowAgentCliTitle` gated on recognising an agent process?** This
   is not observable from inside a terminal and it is the Phase 0 gate. Two probe titles were already
   emitted from a live tmux session in the VS Code integrated terminal on 2026-09-16 (research doc
   §7): a plain OSC 2 written straight to the client pty, then a DCS-wrapped OSC 2 written to the
   pane pty. The resulting tab label answers it in one glance —
   `VSCODE-TITLE-PROBE-DCS` = both passthrough and display work (D-1(a) and (b) both viable);
   `VSCODE-TITLE-PROBE-DIRECT` = VS Code honours sequence titles but tmux did not pass this one
   through (D-1(a) viable, (b) not); unchanged `tmux` = the allowance is process-gated and D-1 must
   be re-opened in favour of (c). **⏳ PENDING (human)** — a measurement request, not a decision.
2. **Is `terminal.integrated.tabs.allowAgentCliTitle` present in the VS Code release installed on
   the host (1.137.0), or only on upstream `main`?** Unanswered, and here is why: the setting is
   registered in the client-side workbench, so it is absent from the remote server bundle available
   on the host, and greps of that bundle for it correctly return nothing. The bare case working is
   strong indirect evidence that the behaviour exists on this setup, but the version boundary is
   unverified. It does not block the design: Phase 0 measures the *behaviour*, which is what the
   design depends on, rather than the setting's presence.
3. **Does VS Code set `TERM_PROGRAM=vscode` for a Windows PowerShell terminal profile as well as for
   POSIX shells?** Unanswered — not verified this session. It does not block: tmux is optional on
   Windows in this package (`src/ai_cli/main.py:2433`), so a Windows launch normally takes the bare
   path, which already works through VS Code's own allowance and is untouched by this design.

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

## Decisions

Every decision below was reached with the project's decision framework: default to the robust
option, then check reversibility (criterion 1), blast radius (criterion 2), expected lifetime
(criterion 3), a weighted cost pass (criterion 4), and the speculative-feature-versus-structural
-malleability test (criterion 5) in order, stopping at the first that resolves — and then state how
every listed Con of the chosen option is mitigated.
Each is **fresh authoring of an open Decision**, not modification of ratified content, so the
framework's fresh-authoring entry point applies and the scorer is used directly; the Authority-test
gates are recorded anyway because each decision was checked against them before being resolved.
`✅ Resolved by Fable orchestrator` closes an item for this run; the human-choice column is
intentionally empty and the requester may override any of them.

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Which mechanism sets the tab name | (a) tmux `set-titles`, (b) DCS-passthrough OSC from the session script, (c) VS Code `tabs.title` setting | (a) | | | Only (a) is implementable here **and** survives re-attach; (c) is unreachable because the settings file lives on the client machine. Criteria 1-3. | `✅ Resolved by Fable orchestrator` |
| D-2 | Where the VS Code branch lives | (a) extend `iterm2.py`, (b) new module, iTerm2 untouched, (c) new module plus rename the misnamed tmux helper | (b) | | | Zero diff on the frozen path; (c) is real cleanup but is scope creep on this change. Criterion 2. | `✅ Resolved by Fable orchestrator` |
| D-3 | Who detects and emits, and when | (a) launcher-side Python only, (b) session-script shell branch, (c) both | (a) | | | Measured: tmux overwrites `TERM_PROGRAM` in every pane, so a shell-side test cannot fire. Criterion 1. | `✅ Resolved by Fable orchestrator` |
| D-4 | Whether to touch tmux `automatic-rename`, and at what scope | (a) no change, session-scoped writes only, (b) set it explicitly for VS Code sessions, (c) change it globally | (a) | | | It is already off for every session; the premise that it was the cause was falsified. Criterion 2. | `✅ Resolved by Fable orchestrator` |
| D-5 | What string the tab shows | (a) `#{window_name}`, (b) `#{pane_title}`, (c) tmux's default format | (a) | | | The ask names the session name explicitly; (b) is richer but not what was asked and adds a moving target. Criterion 5. | `✅ Resolved by Fable orchestrator` |

### Decision Details

<a id="d-1"></a>

#### D-1: Which mechanism sets the tab name — `✅ Resolved`

**Context.** The tab currently shows `tmux` because nothing sends a title outward and VS Code falls
back to its `${process}` template. Three mechanisms could change that, and they differ along a named
axis: **who emits the title** — tmux itself, the session script, or nobody (the terminal's own
template is changed instead).

##### (a) tmux `set-titles on` + `set-titles-string`

**Pros:**

- Uses a stock tmux option; no new capability, and the value it needs already exists (`window_name`
  is already `ai-cli-1`).
- Persistent and re-derivable: tmux re-emits from the option, so the title survives a re-attach, a
  new client, or a VS Code window reload.
- Emits no bytes from this package, so a non-interactive or captured stream cannot be corrupted —
  AC-4 becomes structural.
- Session-scoped (`set-option -t <session>`), so blast radius stops at this session.

**Cons:**

- Depends on Open Question 1 (does VS Code display a sequence title while `tmux` is the foreground
  process).
- A session created under VS Code and re-attached from iTerm2 would carry the option into iTerm2.
- Reaches every client attached to the session, not only the VS Code one.

##### (b) DCS-passthrough OSC 0/2 from the generated session script

**Pros:**

- The wrapper already exists (`_it2()` at `src/ai_cli/session_script.py:648`) and passthrough is
  already enabled (`allow-passthrough=all`, measured), so this is pure reuse.
- Needs no tmux option change at all.

**Cons:**

- One-shot: nothing re-emits it, so the title is lost whenever the tab label is re-derived.
- Writes escape bytes to stdout, so it needs its own guard to avoid polluting a captured stream.
- Would have to live in the session script, where detection is unreliable (see D-3) — or depend on a
  new forwarded marker variable purely to work around that.
- Also depends on Open Question 1.

##### (c) Change the user's `terminal.integrated.tabs.title` to include `${sequence}`

**Pros:**

- Independent of Open Question 1 — it changes the template itself.
- Would also fix any other program's title in that terminal.

**Cons:**

- **Not implementable from where `ai` runs.** Measured: `~/.vscode-server/data/Machine/settings.json`
  and `.../User/settings.json` are both absent on the host, because for a remote workspace the User
  settings live on the client machine.
- Mutates a user-owned global setting, which this tool does not own.

##### Recommendation

> **Decision:** `✅ Resolved by Fable orchestrator` — (a) tmux `set-titles on` with an explicit
> `set-titles-string`, session-scoped, with (b) retained as a documented fallback and (c) demoted to
> user-facing documentation.
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=high; ai-profile=architect -->

Criteria 1-3 resolved it and the scorer was applied. Reversibility (criterion 1): a tmux option is a
true two-way door with no decay — undoing it is one line and nothing accretes dependents. Blast
radius (criterion 2): contained to one session by `-t`, and zero on the frozen iTerm2 path. Expected
lifetime (criterion 3): long-lived, which is what rules out (b)'s one-shot push. (c) is excluded on
implementability, not on preference. Confidence: **medium** — high on the mechanism given the
evidence, medium overall because Open Question 1 is unresolved; a contained, reversible call at
medium confidence self-resolves under Gate D.

Every listed Con of (a) is mitigated in implementation scope, not merely noted: the Open-Question-1
dependency becomes **Phase 0, a gate before any code**; the iTerm2 re-attach case becomes an
`enable=False` write at the attach call site with its own AC (T-1.3) and Risk 1; the
multiple-client reach is accepted explicitly in Risk 4 with its residual risk stated (the title is
still that session's own name, so a second client receives a correct value).

Authority test as run: Gate A — resolving this performs no destructive, irreversible or
outward-facing act. Gate B — the target is not protected: a new document, nothing ratified, nothing
`[PENDING]`. Gate C — no requirement change and no new cross-boundary design; the blast radius stays
inside one module of one repository. Gate D — contained and reversible at medium confidence →
self-resolve.

---

<a id="d-2"></a>

#### D-2: Where the VS Code branch lives — `✅ Resolved`

**Context.** The OSC/title machinery lives in `src/ai_cli/iterm2.py`, and the tmux-option helper
inside it (`_configure_tmux_for_iterm2`) is already misnamed: it is called unguarded for every tmux
session. The requester froze the iTerm2 path. The axis here is **file ownership versus honest
naming**.

##### (a) Extend `iterm2.py` with a VS Code branch

**Pros:**

- All terminal integration in one file; the existing helpers are right there.

**Cons:**

- Puts VS Code logic in a file named for a different terminal — a staleness trap that the misnamed
  helper already demonstrates.
- Edits a file on the frozen path, making "iTerm2 behaviour provably unchanged" harder to argue.

##### (b) New module `vscode_terminal.py`; `iterm2.py` untouched

**Pros:**

- Zero diff on the frozen path, so AC-3 parity is provable by `git diff` rather than by argument.
- The module name states what it is for.
- Nothing is shared, so nothing can be broken by sharing.

**Cons:**

- Two modules now run tmux `set-option` for overlapping purposes.
- Leaves the misleading `_configure_tmux_for_iterm2` name in place.

##### (c) New module plus rename the misnamed helper to something terminal-neutral

**Pros:**

- Fixes the naming debt at its source, while it is fresh in mind.

**Cons:**

- Touches the frozen path's file for a cosmetic gain, on the same change that must prove that path
  unchanged.
- Scope creep against a spec that asks only for the tab name.

##### Recommendation

> **Decision:** `✅ Resolved by Fable orchestrator` — (b) a new `vscode_terminal.py`, with
> `iterm2.py` and `session_script.py` untouched, and the rename filed as a separate follow-up.
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=high; ai-profile=architect -->

Criterion 2 decided it: the frozen path is the boundary that matters, and (b) is the only option
whose compliance is mechanically checkable ("the diff for those two files is empty"). Confidence:
**high**.

Mitigations for (b)'s Cons: the overlapping tmux writes are bounded by keeping the new module to
title options only and never re-setting `allow-passthrough` or `automatic-rename` (audit row 6); the
surviving misleading name is mitigated by recording the measured no-guard fact in the new module's
docstring and by **filing** the rename rather than deferring it silently — Risk 3 carries it, so the
debt is tracked rather than accepted invisibly.

Authority test: Gate A no act; Gate B not protected; Gate C contained to one repository's module
layout, no requirement change; Gate D self-resolve at high confidence.

---

<a id="d-3"></a>

#### D-3: Who detects and emits, and when — `✅ Resolved`

**Context.** This is where the measurement overturned the expected design. `TERM_PROGRAM=vscode` is
forwarded into the tmux environment already (`_TMUX_FORWARDED_VARS`, `main.py:1730`), which looked
like the detection input was plumbed. A controlled experiment shows it is not usable from inside a
pane: tmux **overwrites** `TERM_PROGRAM` with `tmux` and `TERM_PROGRAM_VERSION` with its own version,
while the control variable `LC_TERMINAL` survives untouched. The forwarded value remains readable
only via `tmux show-environment`.

##### (a) Launcher-side Python only

**Pros:**

- Detects where the signal is genuine: `ai c` runs in the outer shell before tmux exists.
- Needs no new forwarded variable and no inner-shell test.
- Handles the nested case (`ai c` run from inside an existing pane) by reading
  `tmux show-environment TERM_PROGRAM`, which measurement shows returns `vscode`.

**Cons:**

- The decision is taken once per launch/attach rather than continuously, so a title is not
  re-asserted mid-session.
- Adds a `tmux show-environment` subprocess call on the nested path.

##### (b) Session-script shell branch, mirroring `_iterm2_fleet_setup`

**Pros:**

- Symmetrical with the existing iTerm2 shell path; can re-assert on status changes.

**Cons:**

- **Its natural guard cannot fire.** A `[[ "$TERM_PROGRAM" == "vscode" ]]` test inside the pane reads
  `tmux`. An implementation built on it passes a bare-case test and ships the defect — exactly the
  failure AC-5 exists to prevent.
- Working around that needs a new forwarded marker variable, i.e. new mechanism for no new outcome.

##### (c) Both

**Pros:**

- Belt and braces.

**Cons:**

- Two emitters that can disagree, and the bare path is where a second emitter could break the
  currently-working case (AC-2).

##### Recommendation

> **Decision:** `✅ Resolved by Fable orchestrator` — (a) launcher-side only; no `$TERM_PROGRAM`
> test is emitted into the generated session script, and no new forwarded variable is introduced.
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=high; ai-profile=architect -->

Criterion 1 decided it, on measured evidence rather than preference: option (b)'s guard is provably
non-functional under the exact condition the feature targets. Confidence: **high**, because the
discriminating fact is a controlled measurement with a control variable.

Mitigations for (a)'s Cons: the once-per-launch limitation is bounded by wiring **both** the create
and attach call sites, so any re-attach re-asserts; the extra subprocess call is confined to the
nested case (`TMUX` set) and is best-effort with a failure-path AC (T-1.1) so it cannot break a
launch.

Authority test: Gate A no act; Gate B not protected; Gate C contained, no requirement change; Gate D
self-resolve at high confidence.

---

<a id="d-4"></a>

#### D-4: Whether to touch tmux `automatic-rename`, and at what scope — `✅ Resolved`

**Context.** The work was framed on the belief that tmux emits its own title from the process name
and clobbers ours, making `automatic-rename off` the remedy. Measurement falsified both halves:
`automatic-rename` is already `0`, `set-titles` is `0` so tmux emits nothing at all, and the window
name is already pinned to `ai-cli-1`. The live axis is therefore no longer "whether to disable it"
but **how widely any tmux write should reach**.

##### (a) No change to `automatic-rename`; every write session-scoped

**Pros:**

- Nothing to do — it is already off for every session, set unconditionally at `main.py:3312`/`:3426`.
- No global behaviour change to the user's tmux server, which was the original blast-radius worry.

**Cons:**

- Relies on a helper whose name implies it only runs for iTerm2, so the guarantee is not obvious to a
  reader.

##### (b) Set `automatic-rename off` explicitly for VS Code sessions too

**Pros:**

- Makes the dependency explicit and local instead of implicit in a misnamed helper.

**Cons:**

- A redundant write of a value that is already correct; two owners for one option.

##### (c) Change it globally (`set-option -wg`)

**Pros:**

- Guarantees the value regardless of call path.

**Cons:**

- Changes behaviour for the user's unrelated tmux windows — the blast radius the original framing
  correctly flagged as unacceptable.

##### Recommendation

> **Decision:** `✅ Resolved by Fable orchestrator` — (a) no change to `automatic-rename`; the new
> code writes only the two title options, and every write passes `-t <session_id>`.
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=high; ai-profile=architect -->

Criterion 2 (blast radius) decided it, and it is the rare case where the correct action is none: the
option is already in the required state, so writing it again buys nothing and (c)'s reach is exactly
what the requester's own framing warned against. Confidence: **high** — measured directly.

Mitigation for (a)'s Con: the implicit guarantee is made visible by an audit row (row 6) and by the
new module's docstring recording that the tmux options are set unconditionally elsewhere, so a reader
is not left to infer it. Risk 3 tracks the rename that would remove the ambiguity for good.

---

<a id="d-5"></a>

#### D-5: What string the tab shows — `✅ Resolved`

**Context.** `set-titles-string` is a tmux format, so the choice of format is a real product
decision. The axis is **stable identifier versus live agent-provided title**.

##### (a) `#{window_name}`

**Pros:**

- Yields exactly `ai-cli-1`, the string the ask names.
- Already set, unconditionally, by `_rename_tmux_window`; stable for the session's life.

**Cons:**

- Discards the agent's richer live title (status glyph), which the bare case currently shows.

##### (b) `#{pane_title}`

**Pros:**

- Forwards the agent's own title (measured live as `✳ ai-cli-1`) and keeps tracking its updates, so
  the tmux tab matches the bare-case tab more closely.

**Cons:**

- Not what the ask asked for, and it makes the tab a moving target.
- Depends on the agent continuing to emit a title, and on tmux's absorption of it — two upstream
  behaviours outside this package's control.

##### (c) tmux's default `set-titles-string`

**Pros:**

- Zero configuration.

**Cons:**

- Default is `#S:#I:#W - "#T" #{session_alerts}`, which is noisy and is not the session name.

##### Recommendation

> **Decision:** `✅ Resolved by Fable orchestrator` — (a) `#{window_name}`, with no configuration
> key added.
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=high; ai-profile=architect -->

Criterion 5 decided it: a configuration knob for the format would be a speculative feature nobody
asked for, and the ask states the wanted value explicitly. Confidence: **high**.

Mitigation for (a)'s Con — the discarded live title: `#{pane_title}` is recorded here as the drop-in
alternative (a one-token change to the same option) and named in the Phase 2 user-facing note, so a
reader who wants the richer title knows the exact change and does not have to rediscover the
mechanism. No knob is added until someone asks.

<!-- /doc:region name="decisions" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

> **Feedback Round 1:** Your approval/feedback on each decision, and on the phasing:
> 1. D-1 (mechanism: tmux `set-titles`): <approval or feedback>
> 2. D-2 (new module, `iterm2.py` untouched): <approval or feedback>
> 3. D-3 (launcher-side detection only): <approval or feedback>
> 4. D-4 (no `automatic-rename` change, session-scoped writes): <approval or feedback>
> 5. D-5 (tab shows `#{window_name}`): <approval or feedback>
> 6. Phasing — is Phase 0 as a hard gate right, or would you rather implement optimistically?
> - <enter feedback here>

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-09-16 | D-1 … D-5 | `✅ Resolved by Fable orchestrator` (`us.anthropic.claude-fable-5-1`, effort high). Contained, reversible, in-repository calls; Authority test recorded per decision. Human choice column intentionally empty — final for this run, overridable on review. |
| 2026-09-16 | Open Question 1 | Left `⏳ PENDING (human)` — a measurement no in-terminal probe can make. Gates Phase 1. |

<!-- /doc:region name="approval_log" -->
