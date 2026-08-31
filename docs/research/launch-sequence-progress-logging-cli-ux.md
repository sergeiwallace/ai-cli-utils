---
title: CLI Launch-Sequence Progress Logging — Best Practices and Package Survey
category: research
tags: [research, cli-ux, progress-logging, python, rich, structlog]
status: complete
source: "codex-2026-08-31"
template_version: "research-1.2.0"
delegation_provenance:
  version: 2
  contributors: []
---

# CLI Launch-Sequence Progress Logging — Best Practices and Package Survey

**Status:** complete

**Created:** 2026-08-31

## Table of Contents

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. Dominant UX patterns](#1-dominant-ux-patterns)
  - [Persistent phase lines](#persistent-phase-lines)
  - [Ephemeral spinners](#ephemeral-spinners)
  - [Persistent multi-line renderers](#persistent-multi-line-renderers)
  - [A practical no-silent-gap rule](#a-practical-no-silent-gap-rule)
- [2. Repeating conventions in real CLIs](#2-repeating-conventions-in-real-clis)
  - [Package managers and build tools](#package-managers-and-build-tools)
  - [Containers, VMs, deploy, and bootstrap tools](#containers-vms-deploy-and-bootstrap-tools)
  - [Cloud and environment tools](#cloud-and-environment-tools)
- [3. Python package survey](#3-python-package-survey)
  - [Rich](#rich)
  - [Click and Typer](#click-and-typer)
  - [structlog](#structlog)
  - [tqdm, alive-progress, yaspin, Halo, and progress](#tqdm-alive-progress-yaspin-halo-and-progress)
- [4. Install-source reporting](#4-install-source-reporting)
- [5. Verbosity, streams, accessibility, and non-TTY behavior](#5-verbosity-streams-accessibility-and-non-tty-behavior)
  - [Verbosity contract](#verbosity-contract)
  - [Streams and capture](#streams-and-capture)
  - [Accessibility and terminal compatibility](#accessibility-and-terminal-compatibility)
- [6. Launcher-specific step model](#6-launcher-specific-step-model)
- [7. Gaps, blindspots & emergent findings](#7-gaps-blindspots--emergent-findings)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
  - [Recommended output contract](#recommended-output-contract)
  - [Example: local editable launch](#example-local-editable-launch)
  - [Example: remote published-package launch](#example-remote-published-package-launch)
  - [Implementation boundaries](#implementation-boundaries)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

<!-- doc:region name="context" kind="immutable" -->

## Context

ai-cli-utils launches Claude Code, Pi, Gemini, and Codex sessions through a conditional bootstrap
sequence. Depending on mode, that sequence may check or reinstall the launcher, wait for another
updater, prepare `direnv`, resolve a local or remote project, allocate a session, create or reuse a
worktree, choose SSH or mosh, prepare an iTerm2 profile, build or attach to tmux state, and finally
replace the launcher process with the interactive engine. The current working tree contains some
step-specific messages but no single progress contract spanning the sequence; early returns in the
update check can produce a silent opening interval. [VERIFIABLE][^1]

The project is Python 3.11+, already depends on Click, and does not declare Typer, Rich, structlog,
tqdm, or another terminal-UI framework as a runtime dependency. [VERIFIABLE][^2]

**Primary period:** 2024–2026
**Source weighting:** 2026 primary, then 2025 and 2024; older sources are used only when they remain
the foundational specification or convention.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

The survey emphasizes documentation accessible on 2026-08-31 and current 2026 releases where a
project publishes them. The CLI Guidelines and Python packaging specifications predate 2024 but
remain the clearest primary statements of the underlying stream, TTY, and install-origin contracts.
[VERIFIABLE][^3]

For user-facing announcements of “editable checkout” versus “PyPI package,” no significant
post-2024 general convention was found. Packaging metadata supports origin inspection, but routine
launch-time narration of that origin remains a dev-tooling-specific need. [NO SOURCE]

## Executive Summary

1. **Use persistent, single-line phase announcements, not a spinner or live dashboard.** The launcher
   has conditional, mostly indeterminate phases and immediately hands the terminal to another
   interactive process. Persistent lines leave a useful audit trail through tmux/SSH/mosh and
   captured logs; a spinner communicates activity but not the decisions made. [INFERENCE]
2. **Print before work can block, then print its meaningful outcome.** Across package, build,
   container, and deploy CLIs, the repeating convention is named phases, durable completion
   summaries, and elapsed heartbeats for long waits. [VERIFIABLE][^3][^4][^5][^6]
3. **Use the Click dependency already present and write announcements to stderr.**
   `click.echo(..., err=True)` supplies portable text output without a new terminal UI framework;
   stdout remains available for machine-readable results and the launched process.
   [VERIFIABLE][^3][^16]
4. **Report install origin as a typed fact, not a binary guess.** Prefer `editable checkout`,
   `direct URL/VCS`, `package index`, `local build`, and `unknown`; say “PyPI” only when evidence
   establishes PyPI specifically. [INFERENCE]
5. **Keep default output small and add paired `-q/--quiet` and `-v/--verbose` controls.** Default
   output covers consequential work and decisions; verbose adds skipped steps, timings, commands,
   and diagnostics. Quiet suppresses progress, never warnings or errors. [HEURISTIC]

## 1. Dominant UX patterns

### Persistent phase lines

Persistent sequential lines fit conditional phases whose outcomes matter later. `uv` prints compact
“Resolved,” “Prepared,” and “Installed” summaries with timings; Cargo uses aligned verbs such as
“Compiling,” “Finished,” and “Running”; Terraform emits “Creating,” periodic “Still creating,”
“Creation complete,” and a final apply summary. [VERIFIABLE][^4][^6][^8]

Their strength is semantic persistence: after failure or handoff, the user can tell which phase ran,
which branch it selected, and how far it got. Their weakness is scrollback cost. The right unit is a
meaningful state transition, not every helper or fast check. [INFERENCE]

Timestamps are unnecessary by default. Durations on slow completions and heartbeats during waits
answer the operational question without turning every interactive launch into a log file.
[HEURISTIC]

### Ephemeral spinners

Spinners suit one indeterminate foreground operation whose internal phases do not matter. They reduce
scrollback and disprove “frozen,” but erase context when replaced and depend on cursor control.
GitHub CLI exposes a spinner-disable setting that substitutes textual progress, showing that
animation needs an accessible fallback rather than being the only status channel.
[VERIFIABLE][^11]

For this launcher, a spinner would need changing labels across install, network, worktree, terminal,
and tmux phases. That becomes an ephemeral event log while still losing the history needed to
diagnose a remote or re-exec failure. [INFERENCE]

### Persistent multi-line renderers

Rich `Progress`/`Live` displays are compelling for concurrent tasks, meaningful totals/rates, or
comparative task state. Rich supports multiple tasks, indeterminate tasks, transient displays, and
configurable refresh. [VERIFIABLE][^19]

This launcher is mostly a conditional serial state machine, has no stable total, and gives terminal
ownership away at the end. A multi-line display adds redraw and teardown obligations without adding
decision information. [INFERENCE]

### A practical no-silent-gap rule

The foundational CLI guidance is direct: silence for minutes makes users wonder whether a command is
broken, while pages of debug output are equally unclear. It also recommends printing before network
work and showing progress for long operations. [VERIFIABLE][^3]

For this launcher:

1. Announce engine and launch mode before configuration, install, or network work.
2. Before potentially blocking, remote, or mutating work, print what is about to happen.
3. On completion, print the decision or outcome only if it changes user understanding.
4. If one phase produces no output for 10 seconds, emit a persistent, restrained heartbeat with
   phase name and elapsed time.
5. Immediately before `exec` or attach, print `Ready: handing off ...`; do not claim that the child
   already launched successfully.

Items 1–3 and 5 are state-machine design conclusions. [INFERENCE] Ten seconds is a starting
heuristic inspired by Terraform’s elapsed-time heartbeats, not a universal standard; measure real
p95 phase latency and tune it. [HEURISTIC]

## 2. Repeating conventions in real CLIs

### Package managers and build tools

- **uv:** concise persistent phase summaries and timings; repeatable `-q`, `-v`, and
  `--no-progress` separate outcomes, diagnostics, and animation. [VERIFIABLE][^4]
- **pip:** progress modes are explicit (`auto`, `on`, `off`, `raw`), and quiet suppresses automatic
  progress. Transfer progress is distinct from install decisions. [VERIFIABLE][^9]
- **npm:** current configuration distinguishes several log levels and separately controls progress,
  so verbosity and animation are not one switch. [VERIFIABLE][^10]
- **Cargo:** quiet, verbose, color, and progress are separately configurable; progress may be
  `auto`, `always`, or `never`. Normal builds use persistent phase verbs and a final summary.
  [VERIFIABLE][^8]

The repeated convention is not “always use a spinner.” It is: retain durable phase/outcome text,
make dynamic progress TTY-aware, and give users independent controls for noise and rendering.
[INFERENCE]

### Containers, VMs, deploy, and bootstrap tools

- **Docker Buildx/Compose:** progress defaults to a TTY renderer interactively and plain persistent
  output otherwise; plain, JSON/raw, and quiet modes are first-class. Build steps have stable IDs and
  end in `DONE` with elapsed time. [VERIFIABLE][^5]
- **Terraform:** long resource operations get persistent periodic heartbeats with elapsed time and a
  durable completion line. This is the closest prior art for a launcher step that can block without a
  meaningful percentage. [VERIFIABLE][^6]
- **Dev Container CLI:** documented examples prefix subprocess work with elapsed milliseconds and
  `Start: Run:` before a durable success result; its implementation supports text/JSON and log
  levels. [VERIFIABLE][^7]
- **Colima:** the root command exposes `--verbose` and `--very-verbose` levels. This supports the
  broader “brief default, diagnostics on demand” split. [VERIFIABLE][^15]

### Cloud and environment tools

- **GitHub CLI:** debug output goes to stderr, `NO_COLOR` is recognized, and accessible settings can
  disable the spinner in favor of text. [VERIFIABLE][^11]
- **gcloud:** user output, structured-log format, screen-reader behavior, and verbosity are separate
  properties. Its `--quiet` primarily controls prompting, warning against assuming that every
  ecosystem gives `quiet` identical semantics. [VERIFIABLE][^12]
- **direnv:** durable `loading`, `reloading`, `unloading`, and environment-diff summaries work because
  they are terse state transitions in a frequently executed tool. [VERIFIABLE][^13]
- **mise:** output style is independent of verbosity; task prefixes can remain while mise’s own
  messages are quieted, and deeper logs can go to a file. [VERIFIABLE][^14]

No distinctive, well-documented progress convention was found in current official AWS CLI,
OrbStack, or asdf material that would alter the synthesis. That is not evidence that they never emit
progress; no load-bearing claim about their behavior is made here. [NO SOURCE]

## 3. Python package survey

### Rich

Rich is mature and capable. `Console` supports stderr and test capture, strips control codes for
non-terminal output, disables animations for `TERM=dumb`/unknown terminals, and recognizes
`NO_COLOR`. [VERIFIABLE][^18] `Status`, `Progress`, and `Live` cover spinners, concurrent tasks,
indeterminate work, transient displays, and custom columns. [VERIFIABLE][^19]

The current PyPI release is production-classified and its wheel is roughly 311 kB; its required
dependencies include `markdown-it-py` and Pygments. [VERIFIABLE][^20]

No audited installed-user count was found. GitHub stars and download counters are popularity
proxies, not adoption measurements, so this report does not turn them into a factual user count.
[NO SOURCE]

This rebuts two simplistic objections: Rich is neither immature nor inherently untestable. The
objection is fit. A UI framework plus transitive dependencies is poor value for roughly six to
twelve serial announcements, and refresh rendering adds terminal state across tmux, SSH, mosh,
capture, and immediate `exec`. [INFERENCE]

Use Rich later if launch becomes genuinely concurrent, exposes quantitative download/build progress,
or develops a broader styled-output system. If adopted, start with `Console` plus a TTY-only
`status()` and plain-line fallback, not `Live`. [HEURISTIC]

### Click and Typer

Click’s `echo()` supports stderr, Unicode handling, ANSI stripping for non-terminal output, and
testing-friendly streams. Its progress bar is iterable-oriented, and Click points users to tqdm for
needs beyond that scope. [VERIFIABLE][^16]

Typer recommends Rich progress and offers a simpler built-in progress bar without Rich.
[VERIFIABLE][^17] ai-cli-utils does not currently depend on Typer. [VERIFIABLE][^2]

A small wrapper over `click.echo(message, err=True)` is therefore the narrowest portable solution.
Direct `print(..., file=sys.stderr, flush=True)` is viable but duplicates behavior already supplied
by a runtime dependency. [INFERENCE]

### structlog

structlog is a production structured-logging pipeline that turns events into dictionaries and can
render JSON, logfmt, or human-readable console output. Its console renderer is oriented primarily
toward development output. [VERIFIABLE][^21]

It is suitable for backend observability, diagnostic files, or a future `--log-format=json`
contract—not for deciding which human-facing phases deserve terminal space. Progress is product UI;
diagnostic logging is an operator/developer data stream. They may share an internal event model but
should not share default rendering policy. [INFERENCE]

### tqdm, alive-progress, yaspin, Halo, and progress

- **tqdm** is active, small, dependency-free, and excellent for iterable rate/ETA meters; it does not
  solve semantic phase narration. [VERIFIABLE][^22]
- **alive-progress** provides polished animation, rate, ETA, and unknown-total modes, but adds
  animation machinery and dependencies for a problem without a useful unit count.
  [VERIFIABLE][^23]
- **yaspin** is an active, small spinner package with pipe/redirect safety. It is the credible
  lightweight spinner-only candidate if animation later becomes a requirement. [VERIFIABLE][^24]
- **Halo** has not released since 2020 and brings several dependencies; it should not be introduced
  into a new 2026 design. [VERIFIABLE][^25]
- **progress** is aimed at iterative bars/spinners. It offers no advantage over plain lines for this
  state machine. [INFERENCE]

## 4. Install-source reporting

Python has prior art for representing origin. The Direct URL Data Structure records VCS, archive,
and local-directory origins in `direct_url.json`; local-directory installs can carry
`dir_info.editable: true`. It is not written for ordinary name/version resolution from an index.
[VERIFIABLE][^26]

pip exposes editable project locations in `pip list`, and uv documents that editable installs link
the environment to source while regular project installs build and install a wheel.
[VERIFIABLE][^27][^28]

Little prior art was found for narrating origin on every interactive launch. Package managers usually
report it during install or inspection, not every downstream command. [NO SOURCE] This launcher is
different because it may reinstall itself before an interactive handoff, so origin changes expected
behavior. [INFERENCE]

| Classification | Evidence | Default announcement |
|---|---|---|
| Editable checkout | Local-directory `direct_url.json` with `editable=true`, corroborated by import path where practical | `Install: editable checkout <path> (<version>); current` |
| Direct URL/VCS | `direct_url.json` records URL/VCS origin | `Install: direct source <sanitized-origin> (<version>); current` |
| Package index | Installer evidence establishes an index; selected index is PyPI if naming PyPI | `Install: PyPI package (<version>); current` |
| Local non-editable build | Known local-directory or installer action without editable mode | `Install: local package build (<version>); current` |
| Unknown | Metadata missing, ambiguous, or from an older installer | `Install: source unknown (<version>); continuing without reinstall` |

Treating “not editable” as “PyPI” is unsound: a local wheel, non-editable checkout, private index, or
older installer may all lack editable state. [INFERENCE] Announce only what the detector proves and
retain `unknown` as a normal, nonfatal state. [HEURISTIC]

The version-check line should always appear in default mode because it closes the initial silent gap
and explains why reinstall did or did not happen. Existing update/install output should remain the
detail source when work occurs; the progress layer should frame rather than duplicate it.
[INFERENCE]

## 5. Verbosity, streams, accessibility, and non-TTY behavior

### Verbosity contract

Default output should answer only: what is launching, which potentially slow or mutating phase is
active, which important branch was selected, and when control is handed off. [HEURISTIC]

- **default:** start, install decision, each performed slow/mutating/remote phase, session/worktree/
  transport outcomes, final handoff;
- **`-q/--quiet`:** suppress progress and completion, preserve warnings and errors;
- **`-v/--verbose`:** add skipped-step reasons, all timings, sanitized subprocess commands, resolved
  paths, and existing install/update transcript;
- **debug facility:** developer internals and tracebacks, if a consumer later requires them.

This mirrors the broad separation in uv, Cargo, Colima, mise, and gcloud, though exact `quiet`
semantics vary. [VERIFIABLE][^4][^8][^12][^14][^15]

Noise fatigue is real: repeated “checking ... OK” lines for millisecond local checks teach users to
ignore the preamble. Announce blocking boundaries and consequential decisions, not implementation
detail. [HEURISTIC]

### Streams and capture

Progress belongs on stderr because it is user messaging, not primary data output. This preserves
stdout for machine-readable subcommands and avoids contaminating pipes. [VERIFIABLE][^3] Flush before
blocking work or `exec`; buffering can otherwise recreate the gap the line was meant to close.
[HEURISTIC]

Tests should capture stderr and assert semantic substrings or reporter events, not ANSI bytes, timing
precision, or every absolute path. Click and Rich both support capture, but plain lines have the
smallest snapshot surface. [VERIFIABLE][^16][^18]

### Accessibility and terminal compatibility

Default output should use ASCII text and not rely on color, emoji, cursor motion, or animation. If
color is added later, honor `NO_COLOR`; the convention is that a present, non-empty variable
suppresses ANSI color unless explicit user configuration overrides it. [VERIFIABLE][^30]

For non-TTY stderr, retain the same persistent lines and disable dynamic rendering. Docker, Rich,
pip, and Cargo all distinguish interactive/auto rendering from plain or disabled progress.
[VERIFIABLE][^5][^8][^9][^18]

tmux’s FAQ attributes many display problems to incorrect `TERM` configuration.
[VERIFIABLE][^29] No authoritative evidence was found that Rich specifically and generally fails
over mosh; isolated issues cannot establish that claim. [NO SOURCE] Avoiding cursor control still
reduces compatibility surface across tmux, SSH, mosh, CI capture, and `TERM=dumb`. [INFERENCE]

## 6. Launcher-specific step model

Instrument the launch as one state machine, not scattered `print()` calls. Working-tree inspection
shows update/re-exec, local or remote setup, transport selection, session allocation/resume,
worktree setup, iTerm2/tmux preparation, and final `exec` or attach. [VERIFIABLE][^1]

| # | Phase | Default policy | Outcome |
|---:|---|---|---|
| 1 | Request accepted | Always, before config/network work | Engine plus local/remote and bare/tmux intent |
| 2 | Install/version | Always | Version, proven source, current/reinstall/wait decision |
| 3 | Peer updater/re-exec | When applicable | Heartbeat; `Continuing after reinstall` after re-exec |
| 4 | Environment preflight | If it installs, mutates, or waits | `direnv` installed/ready; fast no-op verbose-only |
| 5 | Project/remote resolution | Remote or ambiguous paths | Target resolved; no secrets |
| 6 | Remote readiness | Before probe/allocation | Host/session allocated or reused |
| 7 | Transport | Remote only | mosh/SSH plus user-relevant reason; retain retry/switch lines |
| 8 | Session resolution | Once known | Allocated, resumed, or reused identifier |
| 9 | Worktree | When enabled | Creating or reusing path; preserve current message meaning |
| 10 | Terminal integration | Enabled or slow | iTerm2 profile/slot ready |
| 11 | tmux/process preparation | Before work that can wait | Created, reused, or attaching |
| 12 | Handoff | Immediately before `exec`/attach | `Ready: handing off to <engine> (<session>)` |

“Every meaningful step” does not require two lines per row. Sub-100 ms work with no user-relevant
outcome can be folded into the following outcome or shown only in verbose mode. [HEURISTIC]

On failure, name the active phase, elapsed time, and actionable error before the existing diagnostic
transcript. A phase context manager can guarantee attribution for exceptions and interrupts.
[INFERENCE]

## 7. Gaps, blindspots & emergent findings

1. **Re-exec duplication:** successful self-reinstall restarts the launcher, so an unconditional
   start line can appear twice. Carry an environment marker and render the second line as
   `Continuing launch after reinstall`. [INFERENCE]
2. **Origin is not mode:** editable proves one category; its absence does not prove PyPI. Preserve
   uncertainty in detector and copy. [INFERENCE]
3. **Handoff is the completion event:** successful `exec` never returns, so emit readiness
   immediately beforehand, not a false claim that the child is healthy. [INFERENCE]
4. **Heartbeat ownership:** say `Remote probe: still waiting (20s)` rather than merely animate. That
   turns elapsed time into diagnostic information. [HEURISTIC]
5. **Verbose output can leak:** transport and engine command lines may expose hostnames, paths,
   tokens, or sensitive arguments. Sanitize before rendering. [HEURISTIC]
6. **Cancellation is progress UX:** long waits must remain interruptible and name the canceled phase.
   [HEURISTIC]
7. **Emerging terminal-native progress:** iTerm2 documents OSC 9;4 progress reporting. It can surface
   state outside scrollback but should complement, not replace, persistent text and is premature for
   a cross-transport launcher; iTerm2 also warns that its proprietary escape codes may not work
   properly in tmux or screen. [VERIFIABLE][^31]
8. **Unresolved blindspot—latency distribution:** no timing sample was supplied, so the 10-second
   threshold and qualifying phases require p50/p95 measurement. [NO SOURCE]
9. **Unresolved blindspot—assistive-terminal UAT:** no project-specific screen-reader or remote
   terminal test results were available. [NO SOURCE]
10. **Anchor-bias check:** Docker, Terraform, devcontainer, and environment-manager contracts—not the
    named Python package list—led to the small state-reporter recommendation. [INFERENCE]

## Comparison

| # | Approach | Durable trail | Non-TTY/remote fit | Conditional phases | New dependency | Fit here |
|---:|---|---|---|---|---|---|
| 1 | Plain persistent lines via Click | High | High | High | None | **Best** [INFERENCE] |
| 2 | TTY spinner + plain fallback | Low on TTY | Medium | Medium | None/manual or yaspin | Optional later [INFERENCE] |
| 3 | Rich `Console` + `status()` | Medium | High if configured | Medium | Rich + transitive deps | Capable, unnecessary [INFERENCE] |
| 4 | Rich `Progress`/`Live` | Medium | Medium | Low without artificial totals | Rich + transitive deps | Poor fit [INFERENCE] |
| 5 | structlog renderer | High | High | High as events, weak as UI | structlog | Observability only [INFERENCE] |
| 6 | tqdm/alive/progress bar | Low for decisions | Medium | Low | Zero to several | Wrong problem shape [INFERENCE] |

This ranks fitness for this launcher, not general library quality. Rich and tqdm are stronger than
plain lines for genuinely concurrent or quantitatively measurable work. [HEURISTIC]

## Recommendation

**Adopt a no-new-dependency, persistent phase reporter built on the existing Click runtime. Do not
add Rich, structlog, tqdm, alive-progress, yaspin, Halo, or another progress package for this
change.** [INFERENCE]

The problem is missing semantic state, not missing animation. A small reporter can provide prefixes,
stderr routing, flushing, elapsed time, quiet/verbose policy, heartbeats, and failure attribution
while preserving existing update/install and transport output. It remains legible in tmux, SSH,
mosh, CI, redirected logs, and `TERM=dumb`, with no renderer to tear down before `exec`.
[INFERENCE]

### Recommended output contract

- Prefix launcher-owned lines with `[launch]`.
- Use stable `Phase: outcome` grammar; no default timestamp, color, emoji, or step total.
- Flush initial, pre-blocking, heartbeat, failure, and handoff lines.
- Send progress to stderr.
- Persist starts only for work that may block, mutate, perform I/O, or cross a process/machine
  boundary.
- Keep existing install/update output when work runs; frame it with start and one-line outcome.
- Add paired `-q/--quiet` and `-v/--verbose`.
- Carry a re-exec marker so self-update says `Continuing`, not another `Starting`.
- Model origin as an enum with evidence; never map failed detection to PyPI.
- Print `Ready: handing off ...` immediately before every terminal-owning `exec` or attach.

These recommendations derive from the source evidence and inspected state machine. [INFERENCE]

### Example: local editable launch

```text
[launch] Starting Claude Code session (local, tmux)
[launch] Install: editable checkout /home/user/src/ai-cli-utils (0.8.0); current
[launch] Session: allocated c-myproject-3
[launch] Worktree: reusing /home/user/src/myproject/.worktrees/c-myproject-3
[launch] Terminal: iTerm2 profile ready
[launch] Ready: handing off to Claude Code (c-myproject-3)
```

Long phase:

```text
[launch] Worktree: creating /home/user/src/myproject/.worktrees/c-myproject-3
[launch] Worktree: still creating (10s elapsed)
[launch] Worktree: ready (12.4s)
```

### Example: remote published-package launch

```text
[launch] Starting Gemini session (remote, tmux)
[launch] Install: PyPI package ai-cli-utils 0.8.0; current
[launch] Remote: probing configured host
[launch] Remote: session g-myproject-2 allocated
[launch] Transport: mosh selected (VPN inactive)
[launch] Worktree: created /home/user/src/myproject/.worktrees/g-myproject-2
[launch] Ready: handing off to Gemini (g-myproject-2)
```

`PyPI package` is permitted only when evidence establishes PyPI; otherwise say `package index`,
`local package build`, or `source unknown`. [INFERENCE]

### Implementation boundaries

Conceptual API:

```python
reporter.start(engine="Claude Code", mode="local, tmux")
with reporter.phase("Install") as phase:
    phase.outcome("editable checkout ...; current")
reporter.handoff(engine="Claude Code", session="c-myproject-3")
```

Use a monotonic clock, emit heartbeats only after threshold, and convert uncaught exceptions or
interrupts into one attributed failure line before re-raising. [HEURISTIC] Do not add a general
logging framework, JSON mode, metrics pipeline, or live renderer without a demonstrated consumer.
[HEURISTIC]

## Open Questions

1. Which uv-tool metadata reliably distinguishes PyPI, another index, a local wheel, and an older
   install with no `direct_url.json`?
2. Should quiet suppress final handoff, or retain one boundary line? The recommendation uses
   conventional suppression, but UAT should decide.
3. What are p50/p95 durations for update, peer-lock wait, SSH probe, worktree creation, iTerm2 setup,
   and tmux allocation?
4. Should the existing update-verbosity environment control be unified with `-v/--verbose`?
5. Which path/host details are safe by default, and which require sanitization or basename reduction?
6. Does Windows bare-mode need different handoff wording because process replacement differs?
7. Do screen-reader, `TERM=dumb`, redirected-stderr, tmux, SSH, and mosh UAT confirm every long gap
   has a persistent textual owner?

## Sources

[^1]: ai-cli-utils contributors. (2026). [ai-cli-utils package overview and source distribution](https://pypi.org/project/ai-cli-utils/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Published tmux, worktree, remote-transport, and launch features; working-tree-only update paths were also inspected locally.)
[^2]: ai-cli-utils contributors. (2026). [ai-cli-utils package metadata](https://pypi.org/pypi/ai-cli-utils/json). PyPI JSON API. Verified accessible (HTTP 200) 2026-08-31. (Published Python and dependency metadata, cross-checked against the current working tree.)
[^3]: Prasad, A., Firshman, B., Tashian, C., & Parish, E. (2020). [Command Line Interface Guidelines](https://clig.dev/). Verified accessible (HTTP 200) 2026-08-31. (Streams, responsiveness, progress, quiet, color, and TTY guidance.)
[^4]: Astral. (2026). [uv command reference](https://docs.astral.sh/uv/reference/cli/). Astral Docs. Verified accessible (HTTP 200) 2026-08-31. (Phase summaries and quiet/verbose/no-progress controls.)
[^5]: Docker. (2026). [docker buildx build](https://docs.docker.com/reference/cli/docker/buildx/build/). Docker Docs. Verified accessible (HTTP 200) 2026-08-31. (TTY/plain/raw JSON/quiet progress.)
[^6]: HashiCorp. (2026). [Apply Terraform configuration](https://developer.hashicorp.com/terraform/tutorials/cli/apply). HashiCorp Developer. Verified accessible (HTTP 200) 2026-08-31. (Elapsed heartbeats and completion output.)
[^7]: Dev Container CLI contributors. (2026). [Dev Container CLI](https://github.com/devcontainers/cli). GitHub. Verified accessible (HTTP 200) 2026-08-31. (Elapsed start messages, log levels, and text/JSON.)
[^8]: Rust Project. (2026). [Cargo configuration](https://doc.rust-lang.org/stable/cargo/reference/config.html). The Cargo Book. Verified accessible (HTTP 200) 2026-08-31. (Quiet/verbose/color/progress controls.)
[^9]: Python Packaging Authority. (2026). [pip install](https://pip.pypa.io/en/stable/cli/pip_install/). pip 26.2.1 Documentation. Verified accessible (HTTP 200) 2026-08-31. (Progress modes and quiet interaction.)
[^10]: npm, Inc. (2026). [npm configuration](https://docs.npmjs.com/cli/v11/using-npm/config/). npm CLI v11 Documentation. Verified accessible (HTTP 200) 2026-08-31. (Log levels and progress.)
[^11]: GitHub. (2026). [GitHub CLI environment variables](https://cli.github.com/manual/gh_help_environment). GitHub CLI Manual. Verified accessible (HTTP 200) 2026-08-31. (stderr debug, `NO_COLOR`, textual spinner fallback.)
[^12]: Google. (2026). [gcloud CLI configurations](https://docs.cloud.google.com/sdk/gcloud/reference/config). Google Cloud SDK Documentation. Verified accessible (HTTP 200) 2026-08-31. (User output, logs, verbosity, accessibility.)
[^13]: direnv contributors. (2026). [direnv manual](https://github.com/direnv/direnv/blob/master/man/direnv.1.md). GitHub. Verified accessible (HTTP 200) 2026-08-31. (State-transition announcements.)
[^14]: mise contributors. (2026). [Running tasks](https://mise.jdx.dev/tasks/running-tasks.html). mise Documentation. Verified accessible (HTTP 200) 2026-08-31. (Output-style and verbosity separation.)
[^15]: Colima contributors. (2026). [Colima root command](https://github.com/abiosoft/colima/blob/main/cmd/root/root.go). GitHub. Verified accessible (HTTP 200) 2026-08-31. (Verbose controls.)
[^16]: Pallets. (2026). [Utilities](https://click.palletsprojects.com/en/stable/utils/). Click Documentation. Verified accessible (HTTP 200) 2026-08-31. (`echo`, stderr, ANSI stripping, progress scope.)
[^17]: FastAPI project. (2026). [Progress bar](https://typer.tiangolo.com/tutorial/progressbar/). Typer Documentation. Verified accessible (HTTP 200) 2026-08-31. (Rich and fallback progress.)
[^18]: Textualize. (2026). [Console API](https://rich.readthedocs.io/en/latest/console.html). Rich Documentation. Verified accessible (HTTP 200) 2026-08-31. (Capture, non-terminal, `TERM`, `NO_COLOR`.)
[^19]: Textualize. (2026). [Progress display](https://rich.readthedocs.io/en/latest/progress.html). Rich Documentation. Verified accessible (HTTP 200) 2026-08-31. (Multiple and indeterminate tasks, transient output.)
[^20]: McGugan, W. (2026). [Rich 15.0.0](https://pypi.org/project/rich/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Maturity, size, and dependencies.)
[^21]: Schlawack, H. (2026). [structlog documentation](https://www.structlog.org/en/stable/). structlog Documentation. Verified accessible (HTTP 200) 2026-08-31. (Structured event pipeline and console output.)
[^22]: tqdm contributors. (2026). [tqdm 4.70.0](https://pypi.org/project/tqdm/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Dependency-free iterable progress.)
[^23]: Lemos, R. (2025). [alive-progress 3.3.0](https://pypi.org/project/alive-progress/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Animation, throughput, ETA, dependencies.)
[^24]: Pavlenko, M. (2025). [yaspin 3.4.0](https://pypi.org/project/yaspin/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Spinner and redirect safety.)
[^25]: Grover, M. (2020). [Halo 0.0.31](https://pypi.org/project/halo/). PyPI. Verified accessible (HTTP 200) 2026-08-31. (Release date and dependencies.)
[^26]: Python Packaging Authority. (2026). [Recording the direct URL origin of installed distributions](https://packaging.python.org/en/latest/specifications/direct-url-data-structure/). Python Packaging User Guide. Verified accessible (HTTP 200) 2026-08-31. (Direct, VCS, local, and editable origin.)
[^27]: Python Packaging Authority. (2026). [pip list](https://pip.pypa.io/en/stable/cli/pip_list/). pip Documentation. Verified accessible (HTTP 200) 2026-08-31. (Editable location reporting.)
[^28]: Astral. (2026). [Managing packages](https://docs.astral.sh/uv/pip/packages/). uv Documentation. Verified accessible (HTTP 200) 2026-08-31. (Editable versus regular installs.)
[^29]: tmux contributors. (2026). [tmux FAQ](https://github.com/tmux/tmux/wiki/FAQ). GitHub Wiki. Verified accessible (HTTP 200) 2026-08-31. (`TERM` and display troubleshooting.)
[^30]: NO_COLOR contributors. (2026). [NO_COLOR](https://no-color.org/). Verified accessible (HTTP 200) 2026-08-31. (ANSI color opt-out convention.)
[^31]: Nachman, G. (2026). [iTerm2 proprietary escape codes: progress](https://iterm2.com/documentation-escape-codes.html). iTerm2 Documentation. Verified accessible (HTTP 200) 2026-08-31. (OSC 9;4 progress.)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none — no auto-remediation was run)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** ad hoc research request (no registry ID supplied)
**Model:** Codex flagship (exact concrete build ID not exposed by the runtime)
**Date:** 2026-08-31

```text
You are a principal developer-experience engineer who has designed and shipped CLI tooling used
by hundreds of engineers daily (session launchers, deploy wrappers, dev-environment bootstrap
tools). You have strong, evidence-backed opinions about what makes a multi-step CLI feel
trustworthy versus "is this hung?" You cite real tools and real docs, and when you cannot find a
source you say so explicitly.

## Research topic

Research best-practice CLI progress/status logging conventions for a multi-step launch sequence:
session/process bootstrap covering (1) an initial "starting launch of X" announcement naming
which engine/session type is being launched, (2) a version-check/install-or-reinstall decision
step that must distinguish and announce a **local editable/dev install** vs a **PyPI install**
source, (3) the existing update/install print output, (4) every other meaningful step in the
sequence (worktree setup/reuse, session allocation, tmux/mosh/ssh transport selection, iTerm2
profile setup, etc.) so there is no silent gap a user could reasonably read as "is this hung?",
and (5) a clear completion line when the launch sequence finishes and hands off to the actual
interactive engine process.

Survey industry-standard patterns for this exact shape of problem — structured step logging vs.
spinners vs. plain sequential print lines, verbosity levels/flags, "no silent gap" UX
conventions, how popular CLIs (package managers, deploy tools, dev-environment bootstrappers,
container/VM launchers) solve this — and relevant popular open-source Python packages (e.g.
`rich`, `structlog`, plain `click`/Typer echo patterns, `tqdm`, `alive-progress`, `halo`, and any
others the research surfaces) that a Python Typer/argparse-based CLI launcher (ai-cli-utils, which
launches Claude Code / Pi / Gemini / Codex interactive sessions via `ai c`/`ai g`) could adopt.
The target codebase currently has no heavy UI framework dependency and a print()-based launch
sequence.

## Scope note — questions, examples, and named references are a starting point, not a checklist

The questions, topics, and named examples below are illustrative anchors and a FLOOR for this
research — not an exhaustive list to answer only or evaluate only. Reason independently: survey the
landscape broadly, follow the evidence where it leads, expand scope where warranted, and surface
relevant work, factors, and failure modes not named here. Actively resist answering only the listed
questions or evaluating only the named approaches — an output that merely fills in the listed items
has NOT met the research goal.

## Independent exploration (gaps, blindspots, emergent threads) — required

Treat the question list as a FLOOR, not a ceiling. As you research, actively surface what this
framing may be missing and pursue each promising thread to a logical conclusion:
- Adjacent or upstream factors the questions don't capture.
- Contrarian / disconfirming evidence — report it even when it challenges the premise.
- Emerging 2025–2026 practices, tools, or research not anchored by the named examples.
- Known failure modes and second-order effects.
Whenever a load-bearing thread surfaces mid-research, follow it to its conclusion and report it in a
dedicated "Gaps, blindspots & emergent findings" subsection. Explicitly NAME any blindspot you
suspect but cannot resolve (and why) rather than omitting it. Anchor bias — over-fitting to the
listed questions and example approaches — is a known failure mode; counter it deliberately and say
where you did.

## Specific questions to cover (floor, not ceiling)

1. What are the dominant UX patterns (2024-2026) for CLI tools that must run several meaningful,
   potentially slow steps before handing off to an interactive process? Compare sequential
   timestamped/step-labeled print lines vs. ephemeral spinners vs. persistent multi-line progress
   renderers (e.g. Rich's `Progress`/`Live` displays).
2. How do popular real-world CLIs solve exactly this "silent gap during bootstrap" problem — e.g.
   package managers (`uv`, `pip`, `npm`, `cargo`), container/VM tooling (`docker`, `colima`,
   `orbstack`), cloud CLIs (`gh`, `aws`, `gcloud`), dev-environment bootstrappers (`devcontainer
   CLI`, `direnv`, `mise`/`asdf`)? What specific conventions repeat across them?
3. Survey `rich` (Console, Progress, status/spinner, logging handler) as a candidate: adoption,
   maturity, dependency weight, whether it fits a CLI that currently has zero UI-framework
   dependencies, and any downsides (terminal compatibility, mosh/ssh/tmux rendering issues,
   testability of captured stdout).
4. Survey `structlog` and plain `click.echo`/Typer patterns for this use case — are they the right
   tool for user-facing sequential progress announcements as opposed to structured backend/
   observability logging? Where is the line?
5. What conventions exist for a CLI to explicitly announce which install source is active (local
   editable/dev checkout vs. a published PyPI package) as part of a version-check step — any prior
   art, or is this a niche need specific to dev-tooling launchers?
6. What are the failure modes of over-verbose step logging (noise fatigue, clutter before a
   long-running interactive session takes over the terminal) and how do well-regarded CLIs balance
   verbosity levels / `-q`/`-v` flags / `--quiet` defaults against a "no silent gap" goal?
7. Any accessibility or non-interactive-terminal (CI, log-capture, `TERM=dumb`, piped stdout)
   considerations for a step-logging design — does the answer differ if stdout isn't a TTY?
8. Concrete recommendation: given ai-cli-utils' current stack (Python, Typer/argparse mix, no
   existing UI framework dependency, launches into tmux/mosh/ssh sessions), what specific pattern
   and package(s) — or plain stdlib/no-new-dependency approach — should it adopt for this launch-
   sequence logging, and why? Include a rough sketch of announced steps in practice.

<grounding_instructions>
You are a principal developer-experience engineer who has designed and shipped CLI tooling used
by hundreds of engineers daily. You have strong opinions backed by evidence. When you cannot find
a source, you say so explicitly.

Temporal scope: Weight sources by recency — 2026 (primary) → 2025 → 2024, with 2020-2023
acceptable for foundational patterns and library origin stories (e.g. `rich`'s early design,
`click`/Typer conventions) since CLI UX conventions and library maturity in this space predate
2024. Pre-2020 sources are background context only unless foundational to the topic. If
post-2024 literature is genuinely sparse for a subtopic, state "[subtopic]: no significant
post-2024 developments found" rather than backfilling with older sources. Backfilling is a
failure mode, not a hedge.

Before generating your final output, execute a Chain-of-Verification (CoVe)
to ensure factual fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because
   the prompt implied you should, rather than because you verified it.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale
before appending the tag — writing the tag first causes post-hoc rationalization.
Rationale → evidence check → tag.

- [VERIFIABLE]: backed by documentation, peer-reviewed research, or official
  tech blogs (2024–2026). Carry an inline footnote ref to the source: [VERIFIABLE][^N].
- [HEURISTIC]: widely accepted best practice without a specific citation.
- [INFERENCE]: a logical conclusion drawn from context. Provide your reasoning
  in-text. Do not fabricate a source. Tier tag only — NO footnote ref.
- [NO SOURCE]: explicitly state when you cannot find verifiable data. Tier tag
  only — NO footnote ref.

Citation format (mandatory for every externally-sourced claim):
- Inline: append the GFM footnote ref directly after the tier tag — [VERIFIABLE][^N].
  A claim citing multiple sources carries ascending separate refs — [VERIFIABLE][^3][^7]
  (never grouped [^3,^7]; never out of order).
- Footnote definitions live once, under ## Sources, in APA form with a clickable
  URL or DOI and an access-verification stamp. Worked example:
    [^1]: LangChain. (2026). [Threads](https://docs.langchain.com/langsmith/threads).
    LangSmith Documentation. Verified accessible (HTTP 200) 2026-06-03. (Scope note.)
- URL-or-DOI ALWAYS: every source entry carries a clickable URL or a doi.org link —
  paywalled/gated is fine (link it anyway; stamp the access status). Only the
  truly-irreducible case (no online catalog presence anywhere) gets an explicit
  [no online source located] marker with a one-line justification.
- Integrity: footnote refs are contiguous from [^1], every [^N] ref has a matching
  definition, and every definition is referenced — no gaps, no orphans.
- [INFERENCE] / [NO SOURCE] claims carry the tier tag with NO footnote ref.

Hard constraint (overrides all formatting preferences): never invent a citation
to satisfy a formatting instruction. Accuracy > completeness.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX. NEVER generate binary images.
</grounding_instructions>

## Deliverable

Write the full findings into the research document at the write-target path, conforming to canonical
research-1.2.0: Table of Contents, Context, Executive Summary with 3-5 numbered findings, numbered
topic sections, Comparison table, Recommendation, Open Questions, Sources as GFM footnotes,
Appendix: Research Prompt, Appendix: Provenance Ledger, Run History. End with a concrete,
opinionated Recommendation for ai-cli-utils' `ai c`/`ai g` launch logging.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

| Claim | Source | Verbatim support excerpt | Verdict |
|---|---|---|---|
| C1 — the launcher covers tmux, worktrees, and remote transport | [^1] | “Each session gets its own worktree” | Entailed |
| C2 — Click is present and Rich/structlog are absent | [^2] | `"click>=8.1"` | Entailed |
| C3 — silence and debug floods both reduce clarity | [^3] | “A command is saying too little when it hangs” | Entailed |
| C4 — uv separates verbosity and progress | [^4] | “Do not show progress output” | Entailed |
| C5 — Docker supports TTY and plain progress | [^5] | “Set type of progress output” | Entailed |
| C6 — Terraform provides elapsed heartbeats | [^6] | “Still creating... [10s elapsed]” | Entailed |
| C7 — Dev Container CLI prints start events | [^7] | “Start: Run: docker” | Entailed |
| C8 — Cargo makes progress configurable | [^8] | “term.progress.when” | Entailed |
| C9 — pip has explicit progress modes | [^9] | “Specify type of progress to be displayed” | Entailed |
| C10 — npm separates log levels and progress | [^10] | “The log level to use” | Entailed |
| C11 — GitHub CLI has textual spinner fallback | [^11] | “use a textual progress indicator instead” | Entailed |
| C12 — gcloud separates output/accessibility | [^12] | “user_output_enabled” | Entailed |
| C13 — direnv announces transitions | [^13] | “direnv: loading .envrc” | Entailed |
| C14 — mise separates style and verbosity | [^14] | “output style ... is independent of verbosity” | Entailed |
| C15 — Colima has two verbose levels | [^15] | “enable more verbose log” | Entailed |
| C16 — Click echo supports stderr | [^16] | “supports writing to stderr” | Entailed |
| C17 — Typer has a simple fallback bar | [^17] | “built-in, simple progress bar” | Entailed |
| C18 — Rich adapts to dumb/non-terminals | [^18] | “animations will be disabled” | Entailed |
| C19 — Rich supports indeterminate tasks | [^19] | “total=None” | Entailed |
| C20 — Rich is production-classified | [^20] | “Production/Stable” | Entailed |
| C21 — structlog emits structured events | [^21] | “log entries are dictionaries” | Entailed |
| C22 — tqdm has no dependencies | [^22] | “No dependencies” | Entailed |
| C23 — alive-progress provides ETA/throughput | [^23] | “ETA and throughput” | Entailed |
| C24 — yaspin handles redirects | [^24] | “Safe pipes and redirects” | Entailed |
| C25 — Halo's release is from 2020 | [^25] | “Nov 25, 2020” | Entailed |
| C26 — direct URL metadata represents editable installs | [^26] | `"editable": true` | Entailed |
| C27 — pip reports editable locations | [^27] | “Editable project location” | Entailed |
| C28 — uv distinguishes editable behavior | [^28] | “Changes ... are immediately active” | Entailed |
| C29 — tmux display problems often involve TERM | [^29] | “Most display problems are due to incorrect TERM” | Entailed |
| C30 — NO_COLOR suppresses ANSI color | [^30] | “prevent the addition of ANSI color” | Entailed |
| C31 — iTerm2 exposes progress state | [^31] | “The progress bar can be controlled using OSC 9” | Entailed |

The ledger records the shortest excerpts needed to establish sourced propositions. Recommendations
are inference or heuristic and are not represented as externally entailed claims.

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- **2026-08-31 — initial research pass:** inspected the designated artifact and current launcher
  working tree; verified dependency and execution-path claims; retrieved current primary
  documentation; synthesized and hostile-checked claims; wrote only this designated artifact. The
  repository-local canonical research templates were absent, so the verified harness canonical
  prompt and artifact templates were used. The template-resolver helper could not run in the
  restricted environment because its cached runtime was unavailable and the fallback interpreter
  lacked its CLI dependency; canonical files were read directly. No subagents or auto-remediation
  were used.

<!-- /doc:region name="run_history" -->
