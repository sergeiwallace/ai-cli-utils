---
title: VS Code integrated-terminal tab naming under tmux
category: research
tags: [research, terminal, tmux, vscode, osc, tab-title]
status: complete
source: "claude-adhoc-2026-09-16"
template_version: "research-1.2.0"
delegation_provenance:
  version: 2
  contributors: []
task: AI-CLI-5abz
---

# VS Code integrated-terminal tab naming under tmux

## Founding Ask Coverage

`founding_ask_ref`: N/A — this was a Tier 1 dispatch with no `canonical_root_json`. The literal ask
is reproduced below, with **one redaction**: the requester's employer name is replaced by
`[work]`, because this repository is a public package and an employer name is a personal
identifier. Nothing else is changed.

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
| Detect the VS Code integrated terminal | Covered — [§4](#4-detecting-the-vs-code-integrated-terminal-and-the-variable-tmux-destroys). The naive signal does **not** survive tmux; §4 records what does. |
| Set the tab name to the session name | Covered — [§5](#5-the-three-candidate-mechanisms) enumerates three mechanisms and [Comparison](#comparison) ranks them. |
| The tmux case shows `tmux` today | Covered and **re-diagnosed** — [§3](#3-why-the-tab-reads-tmux-today-the-measured-causal-chain). The cause is not the one stated in the ask. |
| The bare case already works | Covered and **explained** — [§2](#2-why-the-bare-case-already-works-vs-codes-agentic-cli-title-allowance). The mechanism is VS Code's, not this package's. |
| iTerm2 is separate and must stay working | Treated as a frozen constraint throughout; [§6](#6-cross-platform-and-blast-radius) records the blast radius of each mechanism on the iTerm2 path. |
| Cross-platform (the requester also uses the integrated terminal on Windows) | Covered — [§6](#6-cross-platform-and-blast-radius). |

**Status:** complete

**Created:** 2026-09-16

**Task:** AI-CLI-5abz

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. How the VS Code integrated terminal decides a tab's name](#1-how-the-vs-code-integrated-terminal-decides-a-tabs-name)
  - [The title template](#the-title-template)
  - [Where an escape-sequence title goes](#where-an-escape-sequence-title-goes)
  - [OSC 633 does not set titles](#osc-633-does-not-set-titles)
- [2. Why the bare case already works: VS Code's agentic-CLI title allowance](#2-why-the-bare-case-already-works-vs-codes-agentic-cli-title-allowance)
- [3. Why the tab reads `tmux` today: the measured causal chain](#3-why-the-tab-reads-tmux-today-the-measured-causal-chain)
  - [What tmux does with an inner OSC title](#what-tmux-does-with-an-inner-osc-title)
  - [The four tmux options, pinned to the installed version](#the-four-tmux-options-pinned-to-the-installed-version)
- [4. Detecting the VS Code integrated terminal, and the variable tmux destroys](#4-detecting-the-vs-code-integrated-terminal-and-the-variable-tmux-destroys)
- [5. The three candidate mechanisms](#5-the-three-candidate-mechanisms)
  - [When to use](#when-to-use)
- [6. Cross-platform and blast radius](#6-cross-platform-and-blast-radius)
- [7. Gaps, blindspots and emergent findings](#7-gaps-blindspots-and-emergent-findings)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

Why this research exists: the `ai` launcher already drives terminal tab titles for one terminal
(iTerm2), through OSC escape sequences and a DCS passthrough wrapper for the case where tmux sits
in between. A second terminal — the VS Code integrated terminal — needs the same outcome, and the
reported symptom carried a built-in control: a bare session gets the right tab name, a tmux session
shows `tmux`. That asymmetry is the evidence this research had to explain before any design could be
trusted, because the two candidate explanations ("tmux overwrites our title" versus "VS Code names
the tab from the foreground process") point at completely different fixes.

**Primary period:** 2025–2026
**Source weighting:** 2026 vendor documentation and current upstream source primary; direct
measurement on the host takes precedence over both. No pre-2024 material is load-bearing here.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

Primary focus: 2026 VS Code documentation and the current `microsoft/vscode` `main` branch, plus
the tmux manual for the version actually installed on the host under study (3.7c). Terminal escape
sequence semantics (OSC 0/1/2, DCS passthrough) are older and stable, and are cited as foundational
background rather than as recent developments. Every claim tagged `[MEASURED]` was produced by a
command run on the host on 2026-09-16 and is quoted with its output.

Evidence is separated into four kinds throughout, and the distinction is load-bearing. The tags used
in the body, written here without their brackets so this legend is not itself read as a claim:

- `MEASURED` — a command was run on this host and its output is quoted. Strongest.
- `VERIFIABLE` — vendor documentation or upstream source, fetched this session, footnoted.
- `INFERENCE` — a conclusion drawn from the above, with the reasoning stated.
- `NO SOURCE` — nothing was found, or the question cannot be answered from inside a terminal.

## Executive Summary

1. **The stated cause is wrong, and the correction changes the fix.** The tab does not read `tmux`
   because tmux overwrote a title. It reads `tmux` because **nothing sends a title outward at all**:
   tmux *absorbs* the inner program's OSC title into its own pane title and, with `set-titles off`
   (its default, and unchanged by this package), never re-emits one. VS Code then falls back to its
   own tab-title template, whose default is `${process}` — and the foreground process is `tmux`.
2. **The bare case works because of a VS Code feature that names this exact use case.** VS Code
   ships `terminal.integrated.tabs.allowAgentCliTitle`, default `true`, described as controlling
   whether "agentic CLIs (such as Claude Code, …) are allowed to set the terminal tab title via
   escape sequences"[^3]. So the outer terminal does honour a sequence title, unprompted. The
   capability is proven, not hypothetical.
3. **The detection input the design was expected to use does not survive tmux.** A controlled
   experiment shows tmux **overwrites `TERM_PROGRAM` in every pane** with the literal string `tmux`,
   even when the session was created with `-e TERM_PROGRAM=vscode`. The forwarded value survives
   only in the tmux *session environment*, readable via `tmux show-environment`. `LC_TERMINAL`
   survives untouched — which is why the existing iTerm2 path works under tmux at all.
4. **Two of the three plumbing pieces are already in place, unguarded.** `allow-passthrough all` and
   `automatic-rename off` are set for **every** tmux session this package creates, not only iTerm2
   ones, and the tmux window is already renamed to the session name. The missing piece is a single
   option: `set-titles`.
5. **One question cannot be answered from inside a terminal, and it is the last gate.** Whether VS
   Code honours a sequence title *while tmux is the foreground process* — or restricts the allowance
   to a recognised agent process — is not observable from inside the pane. A one-glance human check
   is specified in [Open Questions](#open-questions) Q1 and should be the design's Phase 0.

## 1. How the VS Code integrated terminal decides a tab's name

### The title template

`[VERIFIABLE][^1]` The tab label is a **template**, not a passthrough. VS Code exposes
`terminal.integrated.tabs.title` ("Tab title") and `terminal.integrated.tabs.description` ("Text
that appears to the right of the title"), and the documentation states plainly that "by default, the
title displays what the shell's detected process name."

`[VERIFIABLE][^3]` Upstream source pins the defaults exactly:

```text
[TerminalSettingId.TerminalTitle]: {
	'type': 'string',
	'default': '${process}',
	'markdownDescription': terminalTitle
}
```

with `TerminalDescription` defaulting to `'${task}${separator}${local}${separator}${cwdFolder}'`.

`[VERIFIABLE][^1]` The substitution variables include `${process}` — "the name of the terminal
process" — and, separately, `${sequence}` — "the name provided to the terminal by the process".
They are two different variables. The default template names only the first.

### Where an escape-sequence title goes

`[VERIFIABLE][^4]` Upstream `terminalInstance.ts` distinguishes three title sources —
`TitleEventSource.Process`, `TitleEventSource.Sequence` (set via OSC 0/2), and
`TitleEventSource.Api` — and a sequence-sourced title is stored in its own field (`_sequence`),
separate from the process name. `[INFERENCE]` Read against the default template, that means a bare
OSC 0/2 title populates `${sequence}` and would **not** be displayed by a `${process}` template on
its own. This is exactly the reading that would make the whole feature impossible — and finding 2
below is why it is not the whole story. The two must be read together; either alone is misleading.

### OSC 633 does not set titles

`[VERIFIABLE][^2]` The documented OSC 633 shell-integration family is `A` (mark prompt start), `B`
(mark prompt end), `C` (mark pre-execution), `D` (mark execution finished, optional exit code), `E`
(explicitly set the command line), and `P` (set a known terminal property). **None of them sets a
title or tab name.** This is a deliberate null result: OSC 633 is the sequence family a reader would
reach for first, and it is the wrong tool. Shell integration does surface the working directory in
the tab, but that is derived from directory detection, not from a title sequence.

`[MEASURED]` The installed VS Code server's own bash shell-integration script contains exactly one
`TERM_PROGRAM` reference and no title-emitting sequence:

```text
$ grep -nE 'TERM_PROGRAM|\]0;|\]2;|title' \
    ~/.vscode-server/bin/<hash>/out/vs/workbench/contrib/terminal/common/scripts/shellIntegration-bash.sh
104:	if [ -n "${VSCODE_PYTHON_BASH_ACTIVATE:-}" ] && [ "$TERM_PROGRAM" = "vscode" ]; then
```

So shell integration is not the channel, on either the documentation or the shipped artifact.

## 2. Why the bare case already works: VS Code's agentic-CLI title allowance

`[VERIFIABLE][^3]` Current upstream registers a setting that names this scenario outright:

> Setting ID: `terminal.integrated.tabs.allowAgentCliTitle` — Type: `boolean` — Default: `true` —
> "Controls whether agentic CLIs (such as Claude Code, Codex, Command Code, GitHub Copilot CLI, and
> Gemini CLI) are allowed to set the terminal tab title via escape sequences. When disabled, the
> configured tab title template is used instead."

`[INFERENCE]` This resolves the reported asymmetry without any appeal to this package's code. In a
bare session the agent CLI emits its own OSC title; VS Code allows an agent CLI's sequence title to
override the configured template; the tab therefore shows the session name. Nothing in `ai` is
responsible for it — which the code trace confirms independently.

`[MEASURED]` The bare launch path in this package emits **no** title escape sequence at all. A
read-only trace of `src/ai_cli/main.py` found that the bare branch reaches `_exec_with_direnv` and
then `os.execvp` without passing the pre-launch emitter at `main.py:3255`. The corollary matters for
acceptance testing: the "currently-working case" that must not regress is **not this package's
behaviour**, so it cannot be regressed by changing this package's tmux path — only by adding a
second, competing emitter to the bare path.

`[MEASURED]` The absorbed agent title is directly observable in tmux's own state. In a live session
launched as `ai-cli-1`:

```text
$ tmux display -p 'window_name=#{window_name} pane_title=#{pane_title}'
window_name=ai-cli-1 pane_title=✳ ai-cli-1
```

`pane_title` is not a value this package sets; it is the inner agent's OSC title, captured by tmux.
The title *exists*, one layer too deep.

## 3. Why the tab reads `tmux` today: the measured causal chain

Every link below is measured on the host, not inferred.

1. `[MEASURED]` The agent emits an OSC title. tmux consumes it into `pane_title` (`✳ ai-cli-1`,
   above) rather than passing it outward — standard tmux behaviour for OSC 0/1/2 arriving from a
   pane.
2. `[MEASURED]` tmux will only emit a title to its own client when `set-titles` is on. In the live
   session it is off:

   ```text
   $ tmux display -p 'set-titles=#{set-titles} allow-passthrough=#{allow-passthrough}'
   set-titles=0 allow-passthrough=all
   ```

3. `[MEASURED]` Nothing in this package ever turns it on. A grep over `src/` for `set-titles` and
   `set-titles-string` returns **zero matches**. So `set-titles=0` is simply tmux's default,
   inherited:

   ```text
   $ tmux new-session -d -s _probe ...; tmux show-options -g set-titles
   set-titles off
   ```

4. `[INFERENCE]` With no title arriving from the pane's process tree, VS Code applies its own
   default template `${process}` (§1), and the process it detects is `tmux`. The literal string in
   the tab is therefore VS Code's own fallback, not tmux's output.

**Consequence for the design.** The originally stated cause — "tmux emits its own OSC 0/2 title and
clobbers ours" — is measurably not what happens here, because tmux is emitting nothing. The
originally proposed remedy (`automatic-rename off`, to stop tmux emitting its own title) is
therefore not the fix, and it is also **already applied**:

```text
$ tmux display -p 'automatic-rename=#{automatic-rename}'
automatic-rename=0
```

`[MEASURED]` And it is applied for *every* session: a read-only code trace found
`_configure_tmux_for_iterm2()` — which sets `allow-passthrough all` and `automatic-rename off` — is
called at `src/ai_cli/main.py:3312` and `:3426` with **no `_is_iterm2()` guard at the call site and
none inside the function**, despite its name. That is why a VS Code tmux session already has both
options set. The name of that function is now actively misleading.

### What tmux does with an inner OSC title

`[VERIFIABLE][^5]` The installed manual documents the two directions separately.
`allow-set-title [on | off]` governs whether a program in the pane may "change the title using the
terminal escape sequences" — the *inbound* direction, which is how `pane_title` got its value.
`set-titles` governs the *outbound* direction to the client terminal. They are independent, and only
the outbound one is off.

### The four tmux options, pinned to the installed version

`[MEASURED]` Version: `tmux 3.7c`. `[VERIFIABLE][^5]` Verbatim from that installation's manual:

| Option | Verbatim manual text | Default | Live value in a VS Code tmux session |
|---|---|---|---|
| `set-titles [on \| off]` | "Attempt to set the client terminal title using the tsl and fsl terminfo(5) entries if they exist. tmux automatically sets these to the \\e]0;...\\007 sequence if the terminal appears to be xterm(1). This option is off by default." | off | `0` |
| `set-titles-string string` | "String used to set the client terminal title if set-titles is on. Formats are expanded, see the FORMATS section." | `#S:#I:#W - "#T" #{session_alerts}` | default |
| `allow-passthrough [on \| off \| all]` | "Allow programs in the pane to bypass tmux using a terminal escape sequence (\\ePtmux;...\\e\\\\). If set to on, passthrough sequences will be allowed only if the pane is visible. If set to all, they will be allowed even if the pane is invisible." | off | `all` (set by this package) |
| `automatic-rename [on \| off]` | "Control automatic window renaming. When this setting is enabled, tmux will rename the window automatically using the format specified by automatic-rename-format." | on | `0` (set by this package) |

Two consequences worth stating explicitly:

- `[MEASURED]` The `set-titles` mechanism will actually fire for a VS Code client, because the
  terminfo condition in that manual entry is satisfied — VS Code presents as xterm:

  ```text
  $ tmux display -p 'client_termname=#{client_termname}'
  client_termname=xterm-256color
  ```

- `[MEASURED]` The DCS passthrough route is *already unblocked*: `allow-passthrough` is `all`, set
  unconditionally by the function above. No tmux change is needed to use it.

## 4. Detecting the VS Code integrated terminal, and the variable tmux destroys

This is the section that changes the shape of any implementation.

`[MEASURED]` **tmux overwrites `TERM_PROGRAM` in every pane.** Controlled experiment, with the
variable deliberately forwarded in and a second variable as the control:

```text
$ tmux new-session -d -s _probe_tp -e TERM_PROGRAM=vscode -e LC_TERMINAL=iTerm2 'sleep 30'
$ tmux show-environment -t _probe_tp TERM_PROGRAM
TERM_PROGRAM=vscode                       <- the forwarded value IS in the session environment
$ # what a pane's own shell actually sees:
TERM_PROGRAM=tmux                          <- overwritten
TERM_PROGRAM_VERSION=3.7c                  <- overwritten, with tmux's own version
LC_TERMINAL=iTerm2                         <- control: survives untouched
```

`[MEASURED]` The tmux binary itself is the writer: `strings $(command -v tmux)` contains both
`TERM_PROGRAM` and `TERM_PROGRAM_VERSION`. `[NO SOURCE]` The installed manual does not document this
behaviour — a grep for `TERM_PROGRAM` across all 4047 lines of `man tmux` returns nothing. So the
only available evidence for it is the measurement above, which is why the experiment was run with a
control rather than trusted from documentation.

Three findings follow, and each one invalidates an approach that would otherwise look obvious:

1. **A shell-level `[[ "$TERM_PROGRAM" == "vscode" ]]` test inside the session script can never
   fire under tmux.** It would read `tmux`. An implementation built on it would pass a bare-case
   test and ship the actual defect — which is precisely the failure mode AC-5 of the task exists to
   prevent.
2. **Forwarding `TERM_PROGRAM` through `tmux new-session -e` is not sufficient**, even though this
   package already does it (`_TMUX_FORWARDED_VARS` at `src/ai_cli/main.py:1730` includes
   `TERM_PROGRAM`). The forwarded value lands in the session environment and is then shadowed in
   every pane. It is reachable only via `tmux show-environment`, never via `$TERM_PROGRAM`.
3. **`LC_TERMINAL` is untouched, and that explains the existing iTerm2 path.** iTerm2 sets
   `LC_TERMINAL=iTerm2`, tmux passes it through, and the inline shell guard at
   `src/ai_cli/session_script.py:672` keeps working under tmux for that reason. The guard's second
   clause (`TERM_PROGRAM != "iTerm.app"`) is dead code inside tmux. `[INFERENCE]` The iTerm2 path
   works under tmux by a property of `LC_TERMINAL`, not by design intent recorded anywhere.

`[MEASURED]` Secondary VS Code signals do survive into a pane, but by an accident that should not be
relied on. In the live session `VSCODE_IPC_HOOK_CLI`, `VSCODE_GIT_IPC_HANDLE` and
`VSCODE_GIT_ASKPASS_NODE` are all set, even though none of them appears in `_TMUX_FORWARDED_VARS`.
`[INFERENCE]` They are inherited from the **tmux server's** environment, because that server
happened to be started from a VS Code terminal. A server started from an SSH login or a systemd unit
would not have them, and every later pane would then lack them regardless of where it is displayed.
Treating them as a detection signal makes correctness depend on which terminal first started the
tmux server — a property no code can see.

`[INFERENCE]` The robust shape is therefore: **detect in the launcher process, where the signal is
genuine, then carry the verdict explicitly**. `ai c` runs in the outer shell before tmux exists, so
`TERM_PROGRAM=vscode` is trustworthy there. A dedicated marker variable forwarded into the tmux
environment (or the decision applied entirely in the launcher, needing no inner-shell test at all)
avoids depending on a value tmux is known to overwrite.

## 5. The three candidate mechanisms

**(A) `set-titles on` + a `set-titles-string` format.** Ask tmux to emit the title outward. Uses
tmux's own OSC 0 emission (per the manual entry in §3), so nothing has to be smuggled past it.
`[MEASURED]` The needed value already exists: `window_name` is already `ai-cli-1`, set
unconditionally by `_rename_tmux_window()` at `main.py:3313`/`:3427`, so
`set-titles-string '#{window_name}'` yields exactly the session name the ask asks for.
`#{pane_title}` is an alternative that forwards the agent's own live title (`✳ ai-cli-1`) and keeps
tracking it as the agent updates it. `[VERIFIABLE][^5]` `set-titles` is a session option, so
`set-option -t <session>` scopes it to one session rather than the user's whole tmux server.

**(B) DCS passthrough of OSC 0/2 from inside the session.** Wrap the sequence in `\ePtmux;…\e\\` so
tmux forwards it verbatim to the client. `[MEASURED]` Already unblocked (`allow-passthrough=all`),
and the wrapper already exists in the generated shell as `_it2()` at
`src/ai_cli/session_script.py:648`, which "wraps OSC sequences in DCS passthrough when inside tmux".
Reuse, not new capability. Its weakness is that it is a one-shot push: it sets a title at a moment
in time, and anything that later re-derives the tab label (a re-attach, a new client, a VS Code
window reload) has nothing to re-read it from.

**(C) Change the user's `terminal.integrated.tabs.title` to include `${sequence}`.** Makes the
template display sequence titles. `[MEASURED]` This package cannot reach that file on the host
studied: `~/.vscode-server/data/Machine/settings.json` and `.../User/settings.json` are both
**absent**, because for a remote workspace the User settings live on the *client* machine, not on
the host where `ai` runs. `[INFERENCE]` A tool running on the remote host therefore cannot edit the
setting that governs its own tab, which removes (C) as an implementable mechanism regardless of its
merits. It survives only as user-facing documentation ("if your tab shows the process name, set
this").

### When to use

- **(A)** when the title must survive re-attach and be re-derivable — it is a persistent tmux
  option, re-evaluated by tmux on its own, not a one-time write.
- **(B)** when a title must be set at a precise moment for a client that is attached *now*, or when
  no tmux option may be touched at all.
- **(C)** never as an automated action; only as a documented user remedy if the Q1 check comes back
  negative.

## 6. Cross-platform and blast radius

`[VERIFIABLE][^1]` The tab-title mechanism is a VS Code feature, identical on Windows, macOS and
Linux — the settings and their variables are not platform-scoped in any of the documentation
fetched. `[MEASURED]` tmux is optional on Windows in this package: `src/ai_cli/main.py:2433` reads
`tmux_report_wanted = not remote and not (sys.platform == "win32" and not _tmux_setup.tmux_present())`,
so a Windows launch commonly takes the bare path. `[INFERENCE]` On Windows and macOS with no tmux,
the bare case already works via the agentic-CLI allowance (§2) and needs no change; the tmux defect
is specific to the tmux path, which is where the design should be scoped. `[NO SOURCE]` No source
was found stating whether VS Code sets `TERM_PROGRAM=vscode` for a Windows PowerShell profile as
well as for POSIX shells; that was not verified this session, and any Windows-specific detection
claim would be unevidenced.

Blast radius of each mechanism on the frozen iTerm2 path:

| Mechanism | Effect on the iTerm2 path | Residual risk |
|---|---|---|
| (A) `set-titles` scoped per session | None while the option is applied only when the outer terminal is VS Code. iTerm2 sessions keep `set-titles off`, byte-identical to today. | A session created under VS Code and later **re-attached from iTerm2** would carry `set-titles on` into iTerm2, emitting an OSC 0 that could disturb the iTerm2 Name field. Mitigable: both the create and attach call sites already exist (`main.py:3312` and `:3426`), so the option can be re-decided on every attach. |
| (B) DCS passthrough from the session script | None if the new branch is `elif`-style, selected only when iTerm2 is not detected. | An added emitter on the **bare** path could fight the agent's own title (§2); scope it to the tmux path only. |
| (C) settings.json | None. | Not implementable (above). |

## 7. Gaps, blindspots and emergent findings

- **The one thing no in-pane probe can answer.** Whether VS Code displays a sequence-set title
  *while `tmux` is the detected foreground process* is not observable from inside the terminal.
  `allowAgentCliTitle` names *agentic CLIs*; if the implementation keys on the detected foreground
  process being a recognised agent, then under tmux the process is `tmux`, the allowance would not
  apply, and mechanisms (A) and (B) would both fail while (C) becomes the only route. Nothing in the
  fetched documentation or source settles which reading is correct, and this doc does not guess.
  A probe was emitted to make the answer a one-glance human observation — see Q1.
- **A probe was executed, and its result is only readable by a human.** `[MEASURED]` Two
  distinguishable titles were written on 2026-09-16, in this order, from inside a live tmux session
  in a VS Code integrated terminal:

  ```text
  $ printf '\033]2;VSCODE-TITLE-PROBE-DIRECT\007' > "$(tmux display -p '#{client_tty}')"
  $ printf '\033Ptmux;\033\033]2;VSCODE-TITLE-PROBE-DCS\007\033\\' > "$(tmux display -p '#{pane_tty}')"
  ```

  Both writes returned rc=0. The first bypasses tmux entirely (straight to the client pty); the
  second must be passed through by tmux. The resulting tab label is a three-way discriminator:
  `…-DCS` means passthrough **and** display both work (mechanism B proven, A near-certain); `…-DIRECT`
  means VS Code honours sequence titles but tmux did not pass the sequence through; an unchanged
  `tmux` means VS Code ignored both and the allowance is process-gated.
- **A misleading function name is now load-bearing.** `_configure_tmux_for_iterm2()` configures tmux
  for *every* terminal. Any design that adds a VS Code branch near it inherits the confusion, and a
  future reader will reasonably assume a guard that is not there.
- **A pre-existing public-hygiene violation, surfaced while working and not repaired here.**
  `[MEASURED]` The committed `.beads/issues.jsonl` in this public repository contains the
  requester's employer name in **11** issue records, and `tests/test_public_repo_hygiene.py` does
  not scan for it (its `_PRIVATE_REPO_NAMES` tuple lists only two private repository names).
  Repairing it is a public-git-history question, not a code fix, so it is reported rather than
  silently rewritten. It is unrelated to this feature and does not block it.

## Comparison

| # | Criterion | (A) tmux `set-titles` | (B) DCS passthrough | (C) `tabs.title` setting |
|---|---|---|---|---|
| 1 | Implementable by this package on the studied host | Yes — one `set-option` per session | Yes — `_it2()` wrapper already exists | **No** — settings file is on the client, absent on the host |
| 2 | New capability needed | None; `set-titles` is a stock option | None; `allow-passthrough=all` already set | n/a |
| 3 | Survives re-attach / new client | Yes — tmux re-emits from a persistent option | No — one-shot write | Yes |
| 4 | Tracks the agent's own live title | Yes with `#{pane_title}` | No | Yes |
| 5 | Blast radius beyond this feature | Contained: session-scoped option, VS Code sessions only | Contained: one emitter branch | Would change a user-owned global setting |
| 6 | Touches the frozen iTerm2 path | No | No, if added as a separate branch | No |
| 7 | Depends on the unresolved Q1 | Yes | Yes | No — this is the fallback if Q1 is negative |
| 8 | Testable in CI without a real terminal | Yes — assert the exact `tmux set-option` argv | Yes — assert the emitted byte string | Not applicable |

## Recommendation

Adopt **(A) `set-titles on` with an explicit `set-titles-string`, scoped to the session, applied only
when the launcher itself detected the VS Code integrated terminal** — and keep **(B)** as the
documented fallback, since it needs no tmux option at all. Reasons, in the order that decided it:

1. It is the only mechanism that is both implementable here and re-derivable after a re-attach
   (Comparison rows 1 and 3). A one-shot push loses the title exactly when a user reloads the
   window, which is a routine action.
2. It requires no new capability. Every other piece is already installed and already unconditional:
   the window is already named `ai-cli-1`, passthrough is already `all`, `automatic-rename` is
   already `off`. The remaining delta is one option.
3. It leaves the iTerm2 path byte-identical, because `set-titles` stays off for iTerm2 sessions.

Mitigations for its listed Cons, each of which must become implementation scope rather than a note:

- *Re-attach from a different terminal* (§6): re-decide the option at **both** the create and attach
  call sites, and explicitly set `set-titles off` when the detected outer terminal is not VS Code, so
  an iTerm2 re-attach restores today's behaviour rather than inheriting the VS Code setting.
- *Dependence on Q1*: gate the work behind the Q1 human check as Phase 0. If Q1 comes back negative,
  the mechanism changes to (C)-as-documentation and the scope shrinks to a documented user setting —
  that is a different, smaller deliverable and should not be implemented speculatively.
- *Detection fragility* (§4): perform detection in the launcher process only, and never emit a
  shell-level `$TERM_PROGRAM == "vscode"` test into the generated session script, which cannot fire.
- *Naming confusion* (§7): the VS Code branch must not be added inside a function named for iTerm2
  without renaming or clearly re-scoping it, or the next reader inherits a false guard assumption.

## Open Questions

1. **Does VS Code display a sequence-set tab title while `tmux` is the detected foreground process,
   or is `allowAgentCliTitle` gated on recognising an agent process?** This cannot be answered from
   inside a terminal, and it decides between mechanisms (A)/(B) and (C). The probe in §7 has already
   been emitted; answering it requires **one human glance at the tab label** of the session that ran
   it: `VSCODE-TITLE-PROBE-DCS` → (A) and (B) both viable; `VSCODE-TITLE-PROBE-DIRECT` → VS Code
   honours sequences but tmux passthrough failed; still `tmux` → the allowance is process-gated and
   only (C) remains.
2. **Is `terminal.integrated.tabs.allowAgentCliTitle` present in the VS Code version installed on
   the host (1.137.0), or only on upstream `main`?** The setting was read from upstream `main`[^3];
   it is registered in the client-side workbench, so it is not present in the remote server bundle
   available on this host and could not be measured there. The bare case working is strong indirect
   evidence that the behaviour exists on this user's setup, but the version boundary is unverified.
3. **Does VS Code set `TERM_PROGRAM=vscode` for a Windows PowerShell terminal profile as well as for
   POSIX shells?** Not verified this session (§6). It matters only if Windows detection is ever
   needed, which the tmux-scoped design above avoids.

## Sources

[^1]: Microsoft. (2026). [Terminal Appearance](https://code.visualstudio.com/docs/terminal/appearance). Visual Studio Code Documentation. Verified accessible (HTTP 200) 2026-09-16. (Source for the tab-title/description settings, the "detected process name" default, and the full variable list including `${process}` and `${sequence}`.)
[^2]: Microsoft. (2026). [Terminal Shell Integration](https://code.visualstudio.com/docs/terminal/shell-integration). Visual Studio Code Documentation. Verified accessible (HTTP 200) 2026-09-16. (Source for the complete OSC 633 sequence list and the null result that none of them sets a title.)
[^3]: Microsoft. (2026). [terminalConfiguration.ts](https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/workbench/contrib/terminal/common/terminalConfiguration.ts). microsoft/vscode, `main` branch. Verified accessible (HTTP 200) 2026-09-16. (Source for `terminal.integrated.tabs.allowAgentCliTitle` and the verbatim `TerminalTitle` / `TerminalDescription` defaults. Scope note: `main`, not the 1.137.0 release installed on the host — see Open Question 2.)
[^4]: Microsoft. (2026). [terminalInstance.ts](https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/workbench/contrib/terminal/browser/terminalInstance.ts). microsoft/vscode, `main` branch. Verified accessible (HTTP 200) 2026-09-16. (Source for the three `TitleEventSource` kinds and for a sequence title being stored separately from the process name. Scope note: the label-template resolver itself lives in another module, which returned HTTP 404 at the path tried this session — so the display-precedence question is answered from the settings default plus §2, not from that resolver.)
[^5]: Marriott, N., et al. (2026). [tmux(1) manual](https://man.openbsd.org/tmux.1). OpenBSD manual pages. **Online copy deliberately NOT fetched this session; access status therefore unverified.** Every quote attributed to this source was read from the tmux **3.7c** manual as installed on the host under study (`man tmux`, 4047 lines, 2026-09-16), because option defaults and passthrough behaviour have changed across tmux versions and these claims are pinned to the installed one — substituting the upstream page would have silently unpinned them. (Source for the verbatim `set-titles`, `set-titles-string`, `allow-passthrough`, `allow-set-title` and `automatic-rename` entries.)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none — this doc was hand-authored, not produced by an automated research pipeline)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** n/a — ad-hoc orchestrated research, no registry entry
**Model:** `us.anthropic.claude-fable-5-1` (orchestrator, own synthesis) with two read-only
`claude-sonnet-5` sub-agent legs for repository sweeps
**Date:** 2026-09-16

```text
Establish, with citations and separating MEASURED from VENDOR-DOCUMENTED from PRACTITIONER-CLAIMED,
how a terminal tab name is actually set in the VS Code integrated terminal, and how tmux interferes.

Temporal scope: weight sources by recency — 2026 (primary) -> 2025 -> 2024. Terminal escape-sequence
semantics predate that window and are foundational background. If evidence for a subtopic is
genuinely sparse, state "no significant post-2024 developments found" rather than backfilling with
older material. Backfilling is a failure mode, not a hedge. Prefer a measurement on the host under
study over any document, and say which kind of evidence each claim is.

Questions (a FLOOR, not a checklist — survey broadly, follow the evidence, and surface factors and
failure modes not named here):
  a. How does the VS Code integrated terminal decide a tab's name? Cover OSC 0/2 window/icon title,
     OSC 1, the terminal.integrated.tabs.title / .description settings and their variable
     substitutions, and any VS Code-specific sequence family (there is a documented OSC 633
     shell-integration family — establish whether any of it sets a title, and say plainly if it
     does not).
  b. Which of those survives tmux, and what must tmux be told (allow-passthrough, set-titles,
     automatic-rename, set-titles-string) for an inner title to reach the outer terminal? tmux's
     passthrough behaviour changed across versions and is off by default in some — pin the behaviour
     to the tmux version actually installed, and record that version.
  c. Does VS Code honour a title when the pane is a tmux client, and does it re-derive the tab name
     from the foreground process (which would explain `tmux` winning)? This is the crux. If the
     answer is that VS Code names the tab from the process, then no escape sequence fixes it and the
     remedy must go through a setting or a different emission moment. Do not assume either answer.
  d. Cross-platform: does the mechanism differ on Windows (where the integrated terminal may be
     PowerShell or Git Bash) and macOS? A Linux-only answer is incomplete.

Before generating your final output, execute a Chain-of-Verification (CoVe) to ensure factual
fidelity over compliance. Inside your thought process: isolate the core facts required; draft a
tentative response; hostile cross-examination — flag any claim you are citing because the prompt
implied you should rather than because you verified it; strip away any claim that cannot be
empirically verified.

Classify every major claim, writing the rationale before appending the tag:
  [MEASURED]     a command was run on this host; quote it and its output.
  [VERIFIABLE]   vendor documentation or upstream source fetched this session; carry [^N].
  [HEURISTIC]    widely accepted practice, no specific citation.
  [INFERENCE]    a logical conclusion from context; state the reasoning in-text; no footnote.
  [NO SOURCE]    explicit admission that nothing verifiable was found; no footnote.

Citation format: inline GFM footnote ref directly after the tier tag; definitions once under
## Sources in APA form with a clickable URL and an access-verification stamp; contiguous from [^1],
no gaps, no orphans. Hard constraint, overriding all formatting preferences: never invent a citation
to satisfy a formatting instruction. Accuracy over completeness.

A genuine, SHOWN null result is a pass. A padded list of manufactured findings is a fail. Where
evidence is absent, say so plainly and say what would settle it.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

| Claim | Source URL | Verbatim quote (from the fetched page) | Verdict | Live? |
|---|---|---|---|---|
| VS Code's default tab title is the detected process name, not an escape-sequence title | `code.visualstudio.com/docs/terminal/appearance` | "by default, the title displays what the shell's detected process name" | SUPPORTED | Yes (HTTP 200, 2026-09-16) |
| `${sequence}` and `${process}` are distinct variables | `code.visualstudio.com/docs/terminal/appearance` | "`${sequence}`: the name provided to the terminal by the process." / "`${process}`: the name of the terminal process." | SUPPORTED | Yes (HTTP 200, 2026-09-16) |
| The `tabs.title` default is literally `${process}` | `raw.githubusercontent.com/microsoft/vscode/main/.../terminalConfiguration.ts` | "'default': '${process}'" (in the `TerminalTitle` entry) | SUPPORTED | Yes (HTTP 200, 2026-09-16) |
| VS Code allows agent CLIs to set the tab title by escape sequence, by default | `raw.githubusercontent.com/microsoft/vscode/main/.../terminalConfiguration.ts` | "Controls whether agentic CLIs (such as Claude Code, Codex, Command Code, GitHub Copilot CLI, and Gemini CLI) are allowed to set the terminal tab title via escape sequences. When disabled, the configured tab title template is used instead." (Default: `true`) | SUPPORTED | Yes (HTTP 200, 2026-09-16) |
| No OSC 633 sequence sets a title | `code.visualstudio.com/docs/terminal/shell-integration` | "OSC 633 ; A ST — Mark prompt start." … "OSC 633 ; P ; <Property>=<Value> ST — Set a property on the terminal, only known properties will be handled." | SUPPORTED (null result) | Yes (HTTP 200, 2026-09-16) |
| A sequence-sourced title is stored separately from the process name | `raw.githubusercontent.com/microsoft/vscode/main/.../terminalInstance.ts` | "**Sequence source**: Store as `_sequence`" | PLAUSIBLE — supports the separation, not the display precedence | Yes (HTTP 200, 2026-09-16) |
| tmux `set-titles` is off by default and uses the OSC 0 sequence for xterm-like clients | installed `man tmux` (3.7c) | "tmux automatically sets these to the \\e]0;...\\007 sequence if the terminal appears to be xterm(1).  This option is off by default." | SUPPORTED | n/a — local manual, not an online fetch |
| tmux overwrites `TERM_PROGRAM` in a pane | measurement on the host, 2026-09-16 | `TERM_PROGRAM=vscode` from `tmux show-environment`; `TERM_PROGRAM=tmux` from the pane's own `printenv`, with `LC_TERMINAL=iTerm2` surviving as the control | SUPPORTED | n/a — measured, not cited |
| Nothing in this package sets `set-titles` | measurement on the host, 2026-09-16 | `grep -rn 'set-titles' src/` → zero matches | SUPPORTED | n/a — measured |

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-09-16 — created (AI-CLI-5abz). Orchestrated ad-hoc research, `us.anthropic.claude-fable-5-1`
  synthesising two read-only `claude-sonnet-5` repository-sweep legs plus four live document fetches
  and eight host measurements. Result: the reported cause was corrected (tmux absorbs rather than
  overwrites; VS Code falls back to `${process}`), the working bare case was explained by
  `terminal.integrated.tabs.allowAgentCliTitle`, and one question was left explicitly open because
  it is not observable from inside a terminal.

<!-- /doc:region name="run_history" -->
