# Launcher Output Contract — Implementation Plan

**Status:** DRAFT
**Created:** 2026-09-27
**Task:** AI-CLI-1o9b
**Research:** [📄 docs/research/launch-sequence-progress-logging-cli-ux.md](../research/launch-sequence-progress-logging-cli-ux.md)

## Founding Ask Coverage

The ask, verbatim (coverage index below is non-authoritative; no `canonical_root_json` exists for
this ask, so `founding_ask_ref_sha256` is `N/A`):

> standardize the output logging. i see some have `[...]` tags before like `[Launch]` and some output
> lines don't. also, is there a better output logging library that we can use. what are the best
> practice industry standards output logging libraries? like ones with colors and formatting and
> load bars etc? [...] i want the output logging for the ai session launcher to be standardized and
> have aesthetically pleasing and rich text formatting etc features. all the best practices and
> industry standards.

| Ask fragment | Where it is answered |
|---|---|
| some lines have `[launch]`, some don't | T-02, T-03: every launcher-owned line goes through one reporter |
| a better output logging library | D-3 (no new dependency; the research already surveyed and ranked them) |
| colors and formatting | D-1: yes, TTY-gated, via Click which is already a dependency |
| load bars | D-2: no; elapsed time and heartbeats instead, with the reason |
| aesthetically pleasing and rich text formatting | T-01 grammar + styling; the "rich text" reading is addressed in D-3 |
| best practices and industry standards | the research doc's Comparison and Recommendation are the decision record |

## Table of Contents

- [Overview](#overview)
- [Measured Starting Point](#measured-starting-point)
- [Output Contract](#output-contract)
  - [Grammar](#grammar)
  - [Styling](#styling)
  - [Verbosity](#verbosity)
  - [Scope: what is converted and what is not](#scope-what-is-converted-and-what-is-not)
- [Task Breakdown](#task-breakdown)
  - [T-01: Reporter: styling, heartbeat, elapsed, warning/error, active reporter](#t-01-reporter-styling-heartbeat-elapsed-warningerror-active-reporter)
  - [T-02: Route every launcher-owned line in `main.py` through the reporter](#t-02-route-every-launcher-owned-line-in-mainpy-through-the-reporter)
  - [T-03: Route the launch-adjacent module lines through the active reporter](#t-03-route-the-launch-adjacent-module-lines-through-the-active-reporter)
  - [T-04: README documentation](#t-04-readme-documentation)
- [Batch Plan](#batch-plan)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  - [D-1: Colour](#d-1)
  - [D-2: Progress bars](#d-2)
  - [D-3: Output library and live renderer](#d-3)
  - [D-4: How launch-adjacent modules reach the reporter](#d-4)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

The `ai c` / `ai g` / `ai p` / `ai cx` launcher already has a phase reporter (`launch_reporter.py`,
shipped 2026-09 as AI-CLI-itba) that emits `[launch] Phase: outcome` lines for about half of the
launch sequence. The other half still reaches the terminal through `print(..., file=sys.stderr)` in
three further vocabularies (`ai-cli:`, `ai-cli-utils:`, and bare `Error:`/`Warning:`/`WARNING:`/
`Info:`), so the operator sees a mix. This plan makes the existing reporter the single emitter for
every launcher-owned line, gives it the styling the ask wants where that is safe, and adds the
heartbeat the research specified but the first implementation left out.

The research doc is the decision record for the library question. It is not reopened here; the
three decisions below only record where this plan follows it, where it departs from the operator's
literal words, and why.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Measured Starting Point

Measured at base commit `843811f` (`origin/main` at launch), 2026-09-27:

| Measurement | Command | Result |
|---|---|---|
| Print-family calls in the launch function `_do_session_launch` + `_session_command` (`main.py:2494-3777`) | `awk 'NR>=2494 && NR<=3777 && /print\(\|click\.echo\|click\.secho/' src/ai_cli/main.py \| wc -l` | 43 |
| Of those, reporter calls | `grep -c 'reporter\.' ` over the same range | 26 (with 20 `if reporter is not None` guards) |
| Launch-adjacent module print sites | `grep -n 'print(' src/ai_cli/{session,direnv_setup,tmux_setup}.py` | session.py 4, direnv_setup.py 2, tmux_setup.py 4 (+ `report_lines` returns 6 `ai-cli:` strings) |
| Prefix vocabularies on the launch path | grep for `"[launch]"`, `"ai-cli: "`, `"ai-cli-utils: "` | 3, plus unprefixed `Error:`/`Warning:`/`WARNING:`/`Info:` |
| `-q/--quiet`, `-v/--verbose` on session commands | `main.py:3808-3809` | already present; `-V/--version` is on the group (`main.py:3785`), so `-v` does not collide |
| Click version | `importlib.metadata.version("click")` | 8.5.0; `click.echo` strips ANSI when the target stream is not a TTY; it does **not** read `NO_COLOR` |

## Output Contract

### Grammar

Every launcher-owned line is one of these shapes, all on stderr, all flushed:

```text
[launch] <Phase>: <outcome>                          # instantaneous fact or decision
[launch] <Phase>: <verb-ing ...>                     # START, before work that may block/mutate/cross a boundary
[launch] <Phase>: still <verb-ing ...> (10s elapsed) # HEARTBEAT, every 10s while a started phase has no outcome
[launch] <Phase>: <outcome> (12.4s)                  # OUTCOME of a started phase; elapsed shown when >= 2s (always with -v)
[launch] <Phase>: failed after 3.2s: <error>         # an exception escaped a started phase
[launch] <Phase>: interrupted after 3.2s             # Ctrl-C inside a started phase
[launch] Warning: <text>                             # never suppressed by -q
[launch] Error: <text>                               # never suppressed by -q
[launch] Ready: handing off to <Engine> (<session>)  # the last line before every terminal-owning exec/attach
```

Rules, carried over from the research and now enforced by the reporter rather than by convention:

1. A phase that may block, mutate, do I/O, or cross a process/machine boundary gets a START line
   and an OUTCOME line. A started phase that exits cleanly with no outcome recorded prints `done`
   so the contract violation is visible, never silent.
2. Sub-100 ms work with no user-relevant outcome is folded into the next outcome or shown only with
   `-v` (`reporter.detail`).
3. The heartbeat is a persistent line, not an animation. Ten seconds is the research's starting
   heuristic (Terraform's elapsed heartbeat); it is one constant.
4. `Ready:` is emitted immediately before `os.execvp` / attach and never claims the child is up.
5. Multi-line remediation blocks (direnv, tmux) keep their body; only their one-line events route
   through the reporter. They are notices, not progress, and are already visually distinct.

### Styling

TTY-gated colour via `click.style`, applied per token so plain text is untouched when stripped:

| Token | Style |
|---|---|
| `[launch]` | dim |
| `<Phase>` | bold cyan |
| heartbeat outcome (`still ...`) | dim |
| `Ready` | bold green |
| `Warning` | bold yellow |
| `Error`, `failed after`, `interrupted after` | bold red |
| outcome text | default |

Colour is emitted only when all three hold: the target stream is a TTY (Click's own check), `NO_COLOR`
is unset or empty (the no-color.org convention Click does not implement, so the reporter does), and
`TERM` is not `dumb`. The negative control is a test that writes to a non-TTY stream and asserts no
`\x1b` byte reaches it. The launch log file (`launch_logging.py`) receives the plain text via the
logger, never the styled string.

### Verbosity

| Level | Flag | Shows |
|---|---|---|
| quiet | `-q/--quiet` | `Warning:` and `Error:` only |
| default | | start, install decision, each started phase, session/worktree/transport outcomes, handoff |
| verbose | `-v/--verbose` | default + `detail()` lines (skipped steps, resolved paths) + elapsed on every outcome |

Both flags already exist with paired short/long forms. `-v` does not collide with the version flag,
which is `-V/--version` on the `ai` group.

### Scope: what is converted and what is not

| Site | Decision | Reason |
|---|---|---|
| `_do_session_launch` / `_session_command` progress, `Error:`, `Warning:`, `Info:` lines | **convert** | the launcher's own user-facing output |
| `tmux_setup.report_lines` (`ai-cli:` block) | **convert** to `(phase, outcome)` pairs the launcher reports | same block, third vocabulary |
| `tmux_setup.ensure_tmux` / `direnv_setup.ensure_direnv` one-line events (`ai-cli-utils: installed ...`) | **convert** via the active reporter | launch-time machine mutations |
| `session.py` `[launch]`-hardcoded prints (relocated worktree, orphan recovered, direnv authorized) and its `Warning:` | **convert** via the active reporter | copied prefix strings, the exact anti-pattern |
| `_auto_update_if_stale` `Warning:`/`Error:` lines | **convert** via the active reporter | inside the `Install` phase |
| `_print_launch_plan` / `_print_remote_launch_plan` (`--dry-run`) | **keep** | a stdout report of resolved values, not progress; stdout is the right stream for it |
| direnv / tmux multi-line remediation blocks | **keep body**, reporter emits the event | notices with their own ruler formatting |
| `transport.py` reconnect-loop lines | **keep**, out of scope | emitted after the handoff by the transport supervisor while mosh/ssh owns the terminal; `[launch]` would be the wrong word |
| `[memory-watch]`, `[sync-watch]`, `[quota-watch]`, `[telemetry-writer]` | **keep**, out of scope | separate long-running processes with their own lifecycles; their tags stay. Whether they should converge on the reporter is Open Question 1 |
| `iterm2._emit_iterm2_profile_setup` | **keep** | terminal control sequences on stdout, not log lines |

## Task Breakdown

### T-01: Reporter: styling, heartbeat, elapsed, warning/error, active reporter

**Size:** M
**Batch:** 1

Extend `src/ai_cli/launch_reporter.py`. No new dependency.

**Deliverables:**

- Files modified: `src/ai_cli/launch_reporter.py`
- Tests added: `tests/test_launch_reporter.py`

**Existing behaviors (inventory):**

- `start(engine, mode, continuing)` emits `Starting|Continuing <engine> session: <mode>`
- `phase(name, start=None)` context manager; `outcome()`; exception exit emits `failed after Xs: exc`
- `detail(name, outcome)` verbose-only, logged at DEBUG
- `handoff(engine, session)` emits `Ready: handing off to <engine> (<session>)`
- `quiet` suppresses every line; `logger` mirrors plain text; `stream` overrides the target

**Acceptance criteria:**

- [ ] Parity: every inventory behavior above still holds (existing tests pass unchanged except where
      the new `Warning:`/`Error:` never-quiet rule is asserted).
- [ ] When the target stream `isatty()` is true and `NO_COLOR` is unset, the emitted line contains
      ANSI style sequences around `[launch]`, the phase label, and `Ready`/`Warning`/`Error`.
- [ ] When the target stream is not a TTY, the emitted bytes contain no `\x1b` (negative control).
- [ ] If `NO_COLOR` is set non-empty, or `TERM=dumb`, then no `\x1b` is emitted even on a TTY.
- [ ] When a started phase records no outcome within the heartbeat threshold, the reporter emits
      `<Phase>: still <start> (Ns elapsed)` and repeats every threshold until the phase ends; a phase
      created without a start line never emits a heartbeat.
- [ ] When a started phase records its outcome after >= 2 s, the outcome line carries ` (X.Ys)`;
      under 2 s it does not unless `verbose` is set.
- [ ] If a started phase exits cleanly without an outcome, then `done` is emitted as its outcome.
- [ ] If `KeyboardInterrupt` escapes a started phase, then `interrupted after X.Ys` is emitted and the
      exception propagates; `SystemExit` emits nothing extra (the error line was already printed).
- [ ] `warning(text)` and `error(text)` emit `Warning: text` / `Error: text` on stderr even when
      `quiet` is set, and log at WARNING / ERROR.
- [ ] `active()` returns the reporter installed by `activate()`, or a plain default reporter when
      none is installed; `activate()` returns the previously active reporter so a test can restore it.
- [ ] The logger receives the plain, unstyled text.

**Dependencies:** None

### T-02: Route every launcher-owned line in `main.py` through the reporter

**Size:** L
**Batch:** 1

`_do_session_launch` takes `reporter` as an optional parameter guarded by twenty
`if reporter is not None` checks. Default it to `LaunchReporter()` at entry, delete the guards, and
move every `print(..., file=sys.stderr)` on the launch path onto `reporter.error` / `reporter.warning`
/ `reporter.phase(...).outcome(...)`. Restructure the `with reporter.phase(...)` blocks so the outcome
is recorded inside the block (so elapsed time and the heartbeat cover the real work). Convert
`tmux_setup.report_lines` to return `(phase, outcome)` pairs and report them. Delete
`_announce_worktree_isolation` (superseded by the `Worktree:` outcome line).

**Deliverables:**

- Files modified: `src/ai_cli/main.py`, `src/ai_cli/tmux_setup.py`
- Tests modified: `tests/test_launch_reporter.py`, `tests/test_tmux_launch_report.py`,
  `tests/test_session_launch_locality.py`, any test pinning the old text

**Acceptance criteria:**

- [ ] `grep -c 'print(' ` over `_do_session_launch` + `_session_command` is 0 after the change,
      excluding `_print_launch_plan` / `_print_remote_launch_plan` calls (dry-run stdout report).
- [ ] `grep -c 'if reporter is not None'` over `main.py` is 0.
- [ ] Every launch `Error:` exit path emits `[launch] Error: ...` on stderr and exits 1 (existing
      tests asserting `"Error: ..." in err` keep passing because the substring is preserved).
- [ ] The tmux block appears as `[launch] tmux: ...` / `[launch] Mode: launching inside tmux (...)`
      lines; the `launching inside tmux` / `launching bare` markers existing tests rely on are kept.
- [ ] `Remote`, `Update`, `Session` (remote), `Worktree` creating and synchronizing phases record their
      outcome inside the `with` block.
- [ ] Every `os.execvp` / attach / `run_ssh_with_reconnect` / `_run_transport_loop` on the launch path
      is immediately preceded by `reporter.handoff(...)`, and nothing between the handoff and the exec
      requires teardown (no thread, no renderer, no terminal mode change).
- [ ] If the reporter is omitted by a caller, then the launch still reports through a default reporter
      (no `None` path remains).

**Dependencies:** T-01

### T-03: Route the launch-adjacent module lines through the active reporter

**Size:** S
**Batch:** 1

`session.py` (relocated worktree, orphan recovered, direnv authorized, no-upstream warning),
`direnv_setup.ensure_direnv`, `tmux_setup.ensure_tmux`, and `_auto_update_if_stale` emit their one-line
events via `launch_reporter.active()`. The remediation blocks keep their body and are emitted as a
single `warning` so quiet mode still shows them.

**Deliverables:**

- Files modified: `src/ai_cli/session.py`, `src/ai_cli/direnv_setup.py`, `src/ai_cli/tmux_setup.py`,
  `src/ai_cli/main.py`
- Tests modified: `tests/test_session.py`, `tests/test_worktree_envrc_approval.py`,
  `tests/test_tmux_loader_repair.py`, `tests/test_tmux_setup.py`, `tests/test_direnv_setup.py`

**Acceptance criteria:**

- [ ] `grep -n '"\[launch\]' src/ai_cli/session.py` returns nothing: no hand-copied prefix remains.
- [ ] `grep -n 'ai-cli-utils: \|ai-cli: ' src/ai_cli/{tmux_setup,direnv_setup}.py` returns nothing.
- [ ] When `_session_command` runs, the reporter it builds is the one `active()` returns, so a
      worktree relocation inside `create_worktree` is printed with the launch's quiet/verbose policy
      and mirrored into the launch log.
- [ ] If no launch is in progress (for example `create_worktree` called from another command), then
      `active()` still prints the line through a default reporter, so nothing goes silent.

**Dependencies:** T-01

### T-04: README documentation

**Size:** S
**Batch:** 2

Document the output contract and the `-q/-v` flags in `README.md`. Separate, last commit: PR #211
touches `README.md`, so a conflict there stays cheap.

**Acceptance criteria:**

- [ ] README has a "Launch output" section showing the grammar and the `-q`/`-v`/`NO_COLOR` controls.

**Dependencies:** T-02, T-03

## Batch Plan

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01, T-02, T-03 | Reporter + conversion | all ACs checked; `ruff check .`, `ruff format --check .`, `uv run pyright src/`, `pytest` green |
| 2 | T-04 | README | same gate; committed last |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT.

### T-01

- [ ] Parity with inventory
- [ ] TTY colour on
- [ ] Non-TTY: no `\x1b`
- [ ] `NO_COLOR` / `TERM=dumb`: no `\x1b`
- [ ] Heartbeat after threshold, only for started phases
- [ ] Elapsed on slow outcome / always with verbose
- [ ] `done` fallback
- [ ] Interrupt / SystemExit handling
- [ ] `warning`/`error` never quiet
- [ ] `active()` / `activate()`
- [ ] Logger gets plain text

### T-02

- [ ] No `print(` on the launch path (dry-run report excepted)
- [ ] No `if reporter is not None`
- [ ] `Error:` exits through the reporter
- [ ] tmux block as reporter lines with markers kept
- [ ] Outcomes inside `with` blocks
- [ ] Handoff immediately before every exec/attach, nothing needing teardown
- [ ] Default reporter when omitted

### T-03

- [ ] No `"[launch]` in session.py
- [ ] No `ai-cli-utils: ` / `ai-cli: ` in tmux_setup/direnv_setup
- [ ] `activate()` called by `_session_command`
- [ ] Default reporter outside a launch

### T-04

- [ ] README section present

**Audit completed:** <!-- YYYY-MM-DD -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan review | Before merging the implementation commits | The launching session reviews D-1..D-4 against the operator's words |
| UAT | After implementation | Approve for merge; Open Question 2 (quiet and the handoff line) is best decided by looking at a real launch |

## Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Colour | (a) none, (b) TTY-gated via Click, (c) always | (b) | | | Costs no dependency; Click strips for non-TTY; `NO_COLOR` honoured by the reporter | `✅ Resolved by engineer (Claude)` |
| D-2 | Progress bars | (a) bar per phase, (b) elapsed + heartbeat | (b) | | | No phase has a real denominator; a bar would invent one | `✅ Resolved by engineer (Claude)` |
| D-3 | Output library / live renderer | (a) Rich `Console`+`status`, (b) Rich `Live`/`Progress`, (c) Click-only reporter | (c) | | | Research ranked (c) best; (b) is a hard no because the launcher hands the terminal to another process | `✅ Resolved by engineer (Claude)` |
| D-4 | How modules reach the reporter | (a) thread `reporter` through signatures, (b) module-level active reporter | (b) | | | Three modules, four call depths; (a) changes public helper signatures for a logging concern | `✅ Resolved by engineer (Claude)` |

### Decision Details

<a id="d-1"></a>

#### D-1: Colour — `✅ Resolved by engineer (Claude): (b) TTY-gated colour via Click`

**Context.** The operator asked for "colors and formatting". The research's contract says "no default
color", written for a plain-lines design and before any styling was on the table. Both are in scope;
the human words win where they truly conflict, so this records which reading is taken.

##### (a) No colour, plain lines only

**Pros:**

- Literal reading of the research contract; smallest snapshot surface in tests.

**Cons:**

- Ignores half of the operator's explicit ask with no evidence that colour is harmful here.

##### (b) TTY-gated colour via `click.style`, `NO_COLOR` and `TERM=dumb` honoured

**Pros:**

- Zero new dependency: Click is already required.
- Click strips ANSI when the stream is not a TTY, so redirected logs, CI, and the launch-log mirror
  stay plain. The reporter adds the `NO_COLOR` check Click lacks.
- Colour is per token, so the plain text is byte-identical to today's grammar when stripped.

**Cons:**

- Tests must assert on stripped text, or on a non-TTY stream; one negative-control test is needed.
- The research's "no default color" line is read as "no colour in the plain-lines fallback", not as a
  prohibition. That reading is stated here so a reviewer can disagree with it in one place.

##### (c) Always colour

**Pros:**

- Simplest code.

**Cons:**

- Escape bytes in redirected stderr, CI logs and the launch-log mirror. Disqualifying.

##### Recommendation

> **Decision:** ✅ Resolved by engineer (Claude) — (b) TTY-gated colour via Click
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=default; ai-profile=engineer -->
<!-- decision-lineage: decision-id=AI-CLI-1o9b/D-1; decision-topic=launcher-output-colour; governs=src/ai_cli/launch_reporter.py; normalized-proposition=colour-is-tty-gated-and-no-color-aware; applicability=repo:ai-cli-utils; outcome-id=launch-output-contract; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The Con (test surface) is mitigated by having every existing test keep reading a non-TTY capture,
which is stripped, and by one explicit negative-control test on an `io.StringIO` stream.

---

<a id="d-2"></a>

#### D-2: Progress bars — `✅ Resolved by engineer (Claude): (b) elapsed time plus heartbeat`

**Context.** The operator asked for "load bars". The research ranks bars last for this launcher
(Comparison row 6) because no phase has a measurable total.

##### (a) A progress bar per phase

**Pros:**

- Matches the literal ask.

**Cons:**

- Every launch phase (install check, SSH probe, worktree create, pull, tmux allocation) is a single
  indeterminate operation. A bar needs a denominator; here it would be invented, so it would report a
  fraction of nothing. The only candidate with a real total is `ai update`'s package download, and
  that is `uv`'s output, not the launcher's.
- Bars are cursor-control animation: the class of terminal state the launcher must not leave behind
  at the exec boundary (see D-3).

##### (b) Elapsed time on slow outcomes plus a persistent heartbeat after 10 s

**Pros:**

- Answers the operational question ("is this hung?") with information (phase name, elapsed) rather
  than motion. Terraform's `Still creating... [10s elapsed]` is the prior art.
- No cursor control, works identically over tmux, SSH, mosh, redirected stderr and `TERM=dumb`.

**Cons:**

- Less visually lively than a bar. Accepted: the launcher's output is on screen for a few seconds
  before another process owns the terminal.

##### Recommendation

> **Decision:** ✅ Resolved by engineer (Claude) — (b) elapsed time plus heartbeat
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=default; ai-profile=engineer -->
<!-- decision-lineage: decision-id=AI-CLI-1o9b/D-2; decision-topic=launcher-progress-indicator; governs=src/ai_cli/launch_reporter.py; normalized-proposition=heartbeat-not-bar; applicability=repo:ai-cli-utils; outcome-id=launch-output-contract; relation=different-question; related-decision-id=AI-CLI-1o9b/D-3; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

This departs from the operator's literal word "load bars". The evidence is the phase table in the
research (section 6): twelve phases, none with a stable total. If a future phase gains one (for
example a first-run download the launcher itself performs), it can carry a bar then, named with its
real denominator.

---

<a id="d-3"></a>

#### D-3: Output library and live renderer — `✅ Resolved by engineer (Claude): (c) Click-only reporter`

**Context.** The operator asked whether there is "a better output logging library" and for "rich text
formatting". The research surveyed Rich, structlog, tqdm, alive-progress, yaspin, Halo and progress
and recommended none for this change.

##### (a) Rich `Console` with a TTY-only `status()` spinner

**Pros:**

- Mature; strips for non-TTY; honours `NO_COLOR`; the research names it the acceptable Rich shape if
  Rich were ever adopted.

**Cons:**

- Adds Rich plus `markdown-it-py` and Pygments for six to twelve serial lines. Requires a relock
  (`uv sync --locked` is asserted in CI).
- A spinner erases the line it replaces, losing the durable trail the launch log and tmux scrollback
  depend on.

##### (b) Rich `Live` / `Progress` renderer

**Pros:**

- The most "rich text" of the options.

**Cons:**

- Hard no, and not a preference. The launcher ends by handing the terminal to another process via
  `os.execvp` or attach. A renderer owning screen state must be torn down before that handoff, and a
  dropped or abnormal exit skips the teardown. AI-CLI-w679 shipped this week to fix exactly this
  class: a terminal left emitting SGR mouse reports because mode state survived a boundary the remote
  never cleanly crossed. `transport.restore_terminal` exists for that class; this plan adds no second
  instance of it.

##### (c) The existing Click-based reporter, extended

**Pros:**

- No dependency, no relock, no teardown obligation, persistent lines everywhere.
- Colour and consistent formatting (the aesthetic half of the ask) are available from `click.style`.

**Cons:**

- No animation. Accepted per D-2.
- "Rich text" in the sense of tables, panels and markup is not delivered. The launcher emits a short
  sequence of state transitions; there is nothing tabular to render before the exec.

##### Recommendation

> **Decision:** ✅ Resolved by engineer (Claude) — (c) Click-only reporter, extended
<!-- decision-record: chosen-option=(c); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=default; ai-profile=engineer -->
<!-- decision-lineage: decision-id=AI-CLI-1o9b/D-3; decision-topic=launcher-output-library; governs=pyproject.toml:dependencies; normalized-proposition=no-new-output-dependency; applicability=repo:ai-cli-utils; outcome-id=launch-output-contract; relation=different-question; related-decision-id=AI-CLI-1o9b/D-2; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Answer to the operator's library question, in one line: the industry-standard Python choices are
Rich (styling, spinners, progress, live displays), tqdm (iterable progress bars) and structlog
(structured diagnostics). All three are good libraries; none fits a six-line serial launcher that
execs into another process, which is why the repo's own research picked Click, already a dependency.

---

<a id="d-4"></a>

#### D-4: How launch-adjacent modules reach the reporter — `✅ Resolved by engineer (Claude): (b) module-level active reporter`

**Context.** `session.create_worktree`, `direnv_setup.ensure_direnv`, `tmux_setup.ensure_tmux` and
`_auto_update_if_stale` print launch events from two to four call levels below the launcher.

##### (a) Thread a `reporter` parameter through every signature

**Pros:**

- Explicit; no process-level state.

**Cons:**

- Changes the signatures of public helpers (`create_worktree` has many call sites and test patches)
  for a logging concern; internals like `_relocate_holder` and orphan recovery would take it too.

##### (b) Module-level `active()` accessor set by the launch command

**Pros:**

- Same shape as `logging.getLogger`; one `activate()` in `_session_command`; helpers call
  `launch_reporter.active().phase("Worktree").outcome(...)`.
- Outside a launch (another command calling `create_worktree`), `active()` returns a plain default
  reporter, so nothing goes silent.

**Cons:**

- Process-level state. Mitigated: `activate()` returns the previous reporter so tests restore it, and
  the launcher is a one-launch-per-process program.

##### Recommendation

> **Decision:** ✅ Resolved by engineer (Claude) — (b) module-level active reporter
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1; ai-effort=default; ai-profile=engineer -->
<!-- decision-lineage: decision-id=AI-CLI-1o9b/D-4; decision-topic=reporter-access-from-helpers; governs=src/ai_cli/launch_reporter.py; normalized-proposition=active-reporter-accessor; applicability=repo:ai-cli-utils; outcome-id=launch-output-contract; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> 3. D-3: <approval or feedback>
> 4. D-4: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. Should the watcher daemons (`[memory-watch]`, `[sync-watch]`, `[quota-watch]`, `[telemetry-writer]`)
   converge on the same reporter grammar later? They are separate processes whose output is usually
   read from a log, not a terminal, so the case is weaker; left for a follow-up issue rather than
   folded in here.
2. Should `-q/--quiet` suppress the `Ready:` handoff line, or keep that one boundary line? The research
   uses conventional suppression and defers to UAT; this plan keeps suppression.
3. Should `AI_CLI_UPDATE_VERBOSE` be unified with `-v/--verbose`? (Research Open Question 4.) Not
   changed here.

> **Feedback Round 1:** Your thoughts on the open questions:
> - <enter feedback here>

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-09-27 | D-1..D-4 | engineer (Claude, Fable 5.1) | Resolved under the Authority test: no new dependency, no interface change outside the launcher, reversible. Approval not implied; the launching session reviews before merge. |
