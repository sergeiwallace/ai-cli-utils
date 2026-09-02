---
title: "Remote Claude Code session leaks terminal input escape sequences"
category: bugs
tags: [session, tmux, terminal, mosh, remote, claude-code]
status: diagnosed-upstream
severity: P1
template_version: "bug-1.0.0"
---

# Remote Claude Code session leaks terminal input escape sequences

**Status:** diagnosed-upstream; no repository code change

**Created:** 2026-09-02

## Founding Ask Coverage

This investigation covers the reported managed remote session becoming unusable after an
accidental left-arrow transition into Claude Code's agents view and a return to the main view. It
accounts for the requested process/session check, escape-sequence diagnosis, upstream issue search,
local wrapper audit, recovery guidance, and local-fix decision.

The founding ask is preserved semantically rather than reproduced verbatim because this repository
is public and the original includes a private machine address, account name, session name, and
internal tracking identifiers. That publication constraint conflicts with literal reproduction;
none of those identifiers is needed to preserve the technical requirements or evidence.

The affected tmux session had already been killed before this investigation began. Its live process
tree, pane state, Claude Code version, and terminal modes therefore could not be inspected. The
pre-kill pane capture is the primary incident artifact. No evidence in that capture establishes
that another tmux session, pane, or Claude transcript was forked. The local launcher also reattaches
to a live pane instead of creating a second tmux session; see [Local code audit](#local-code-audit).

The request also asked for task-note updates and shipping. This research worker was restricted to
this one document, so it did not mutate task-tracking or Git state. The document is the complete
research and diagnosis handoff for those follow-up actions.

## Symptoms

After the user pressed left arrow at an empty Claude Code prompt, Claude Code opened its agents
view. Returning to the main conversation left the pane unable to accept ordinary editing,
navigation, submission, or Ctrl+C. Pointer movement and Ctrl+C instead appeared as printable text.

The captured pane tail contained five copies of:

```text
^[[27;5;99~
```

followed by a long run shaped like:

```text
^[[<35;183;9M^[[<35;182;8M^[[<35;179;6M ... ^[[<35;1;1M
```

This is materially different from the separate
[double-interrupt exit bug](remote-session-double-interrupt-exit.md). That bug begins after the
shell receives a raw `0x03` Ctrl+C byte. Here, Ctrl+C was encoded as an escape sequence, so the
terminal line discipline never had a raw interrupt byte to turn into `SIGINT`.

## Environment and reproduction

The observed stack was:

```text
terminal emulator -> mosh -> tmux -> managed session wrapper -> Claude Code TUI
```

Known incident facts:

1. The session was a managed remote Claude Code session reached over mosh and attached to tmux.
2. tmux mouse support and extended-key support were enabled.
3. The visible failure began immediately after navigation into and back from Claude Code's agents
   view.
4. The pane capture was taken before the session was killed.
5. The Claude Code, tmux, mosh, and terminal-emulator versions were not preserved.

A deterministic local reproduction was not obtained. Public reports independently reproduce both
the agents-view transition defect and raw mouse reports leaking into a Claude Code prompt, but no
single public report was found with this exact combined stack and byte stream.

## Root cause analysis

### Byte-level decode

| Captured text | Protocol meaning | Consequence |
|---|---|---|
| `^[[27;5;99~` | xterm-format extended key: ASCII code point `99` (`c`) with modifier value `5` | Ctrl+C reached the foreground application as an escape sequence rather than raw `0x03` |
| `^[[<35;X;YM` | SGR mouse report: button field `35`, cell coordinates `X`,`Y`, final `M` | Pointer motion reached an input path that rendered it instead of consuming it |

The first sequence is often loosely called “CSI-u,” but that label is not exact. tmux documents two
extended-key formats: xterm format renders a modified key as `CSI 27;modifier;codepoint~`, while
CSI-u renders it as `CSI codepoint;modifier u`. The capture is therefore xterm
`modifyOtherKeys` format. [VERIFIABLE][^1]

Modifier values are encoded as one plus a bit mask. Ctrl has bit value `4`, so `5` means Ctrl-only;
Ctrl+Shift would be `6`. Code point `99` is `c`. When an application asks for disambiguated
keyboard events, Ctrl+C is delivered as an escape sequence and no longer generates `SIGINT` by
itself. [VERIFIABLE][^2]

For the mouse stream, SGR mode is enabled by DECSET 1006 and uses
`CSI < button;column;row M/m`. Any-event tracking is enabled by DECSET 1003. In the SGR button
field, `35` is `32` (motion) plus `3` (no button), matching the changing coordinates in the
capture. [VERIFIABLE][^3]

### Causal finding

The capture proves that extended-key and SGR mouse reports traversed the active input path and were
rendered as prompt content instead of being interpreted. tmux is documented to pass application-
requested extended-key modes when `extended-keys` is on, and Claude Code's fullscreen renderer is
documented to capture mouse input. [VERIFIABLE][^1][^4]

The best-supported diagnosis is an in-process Claude Code TUI input-state desynchronization during
the agents-view transition: the terminal/tmux side continued producing the negotiated protocols,
while the restored view no longer had the matching decoder active. [INFERENCE] This localizes the
failure to the consumer/state transition rather than proving which closed-source function omitted
a mode pop, decoder handoff, or cleanup call.

The exact internal defect is not proven. The pane was destroyed before its modes and process tree
could be queried, the incident version is unknown, and the closed-source process could not be
instrumented. A stale mode with the wrong parser, a still-active parser attached to the wrong view,
or a view-transition exception would all produce the same observed boundary behavior. [INFERENCE]

### Known upstream issue class

This is a known Claude Code defect class, although the exact combined incident is not published as
a single confirmed issue:

- An agent-transcript-view report shows raw SGR mouse reports entering the prompt rather than being
  consumed. [VERIFIABLE][^5]
- A separate report, marked reproduced by maintainers, matches the exact left-arrow transition into
  the agents screen and a degraded main view after returning. [VERIFIABLE][^6]
- A tmux-specific report records raw SGR mouse sequences entering the Claude Code input prompt with
  tmux mouse mode enabled. [VERIFIABLE][^7]
- A currently open report, also marked reproduced, shows a Claude Code failure leaving mouse
  tracking enabled so later pointer movement writes coordinates into the terminal. Its crash path
  differs from this live view-transition incident. [VERIFIABLE][^8]
- Anthropic's changelog records multiple fixes in this family: mouse reports leaking after exit,
  raw key sequences over SSH, enhanced keyboard mode remaining active after exit, agent-view exit
  hangs, and the left-arrow agents transition dropping session UI state. [VERIFIABLE][^9]

These reports span direct terminals, tmux, SSH, and non-mosh sessions. Therefore mosh is not a
necessary cause of the bug. [INFERENCE]

### Local code audit

The generated wrapper in
[`session_script.py`](../../src/ai_cli/session_script.py) was read in full.

- It does not call `stty`, use Python `termios`, enable or disable DEC mouse modes, or negotiate
  xterm/Kitty extended-key modes.
- Its foreground-terminal restoration only calls `tcsetpgrp`; that restores process-group
  ownership, not terminal-emulator private modes.
- Its only direct escape output is DCS-wrapped iTerm2 OSC title/profile/color output. OSC 1 and OSC
  1337 do not enable mouse tracking or extended-key input.
- Its hot-reload path can replace the wrapper after the Claude Code child exits, but it does not
  reset protocol modes between children. No evidence shows that a hot reload or child replacement
  occurred during this incident; the reported trigger was an in-process view transition.

The launcher in [`main.py`](../../src/ai_cli/main.py) explicitly sets `mouse on` for each new
managed tmux session. The repository's
[`iterm2-setup.md`](../tools/iterm2-setup.md) also documents enabling tmux extended keys. Those
settings make the two captured protocol families available, but they are not anomalous by
themselves: Claude Code's official fullscreen guidance tells tmux users to enable mouse mode for
wheel forwarding. [VERIFIABLE][^4]

The generated iTerm2 profile in
[`icon_generator.py`](../../src/ai_cli/icon_generator.py) maps only Shift+Enter to `CSI 13;2u`; it
does not map Ctrl+C. The remote transport loop starts mosh or SSH subprocesses but emits no keyboard
or mouse mode sequences.

For an existing live tmux pane, [`main.py`](../../src/ai_cli/main.py) runs `tmux attach-session -d`
after `tmux has-session`; it creates a session only when the named session is absent or every pane
is dead. The launcher therefore did not fork a second tmux session merely because the user left and
returned to Claude Code's agents view. Claude Code may internally background or reattach a
conversation, but the destroyed process tree and transcript registry prevent a post hoc answer
about that separate internal behavior. [INFERENCE]

### Mosh assessment

Mosh is a terminal-state synchronization system rather than an SSH-like transparent byte stream,
and it has supported mouse modes since version 1.2.5. [VERIFIABLE][^10] A historical mosh issue also
shows that a modifier-plus-mouse gesture behaved differently under mosh+tmux than SSH+tmux, so an
A/B transport test remains worthwhile. [VERIFIABLE][^11]

No current primary source was found tying mosh to this exact Claude Code agents-view/extended-key
leak. Because equivalent Claude Code leaks are reported without mosh, treating mosh as the root
cause would overstate the evidence. [NO SOURCE]

## Hypothesis ledger

| Hypothesis | Evidence for | Evidence against | Disposition |
|---|---|---|---|
| Claude Code restored a view without its matching input decoder or mode state | Exact agents-view trigger; both protocol families became printable; closely matching upstream reports | Closed-source internals and destroyed pane prevent direct confirmation | **Leading; high confidence at component boundary, medium confidence in exact mechanism** |
| The local wrapper enabled the broken modes | Launcher enables tmux mouse; setup guide enables extended keys | Wrapper emits no mouse/keyboard enable sequences; both settings are valid supported features | **Contributing precondition, not root cause** |
| Wrapper hot reload left stale modes after replacing Claude Code | Wrapper has no explicit protocol reset between child runs | Incident occurred while returning between views; no child exit/reload evidence | **Not supported for this incident** |
| mosh corrupted or replayed input state | Mosh synchronizes terminal state; historical tmux/mouse interaction exists | Same Claude Code symptoms occur without mosh | **Possible amplifier; not necessary cause** |
| tmux forked the managed session | User perceived a background/return transition | Launcher reattaches to an existing live session; capture contains no process evidence | **No supporting evidence** |
| Shell double-Ctrl+C handling failed | Ctrl+C could not interrupt | Captured Ctrl+C was not raw `0x03`, so the shell trap never received it | **Rejected; different bug boundary** |

## Scope-of-fix decision

No production code change was made.

The repository does contribute two trigger-surface settings—tmux mouse mode and documented extended
keys—but disabling them globally would remove supported functionality and would not repair Claude
Code's live view-state transition. Adding `stty sane` would reset the PTY line discipline but not
DEC mouse modes or terminal keyboard-protocol stacks. Sending a blind full terminal reset on every
attach risks corrupting an active TUI and racing the application that owns those modes.

A narrow reset after a Claude Code child has exited, before the wrapper launches its replacement or
recovery shell, may be a useful defense against abnormal-exit leakage. It would not fix the observed
failure while the Claude Code process remains alive. Under the regression-first requirement, that
change needs a real PTY/tmux failing test before implementation; no deterministic failing boundary
test was available here. Forcing a speculative patch would make the repository responsible for
guessing at another application's live terminal ownership.

## Fix and mitigations

### Immediate recovery

If the pane still accepts the tmux prefix, detach with `Ctrl-b d`. From a normal shell in the same
terminal tab, run:

```sh
reset
```

If `reset` is unavailable, the following narrower sequence disables the common xterm mouse modes,
resets xterm `modifyOtherKeys`, pops one Kitty keyboard-protocol mode, and repairs the PTY line
discipline. Run it only from the normal shell after detaching or exiting the TUI, not by injecting
it into a live application:

```sh
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[>4;0m\033[<u'
stty sane
```

If the terminal still emits enhanced-key or mouse reports, close that terminal tab or reset the
terminal emulator, then reconnect. Killing the affected tmux session from a separate shell is a
last resort when the application cannot be interrupted; preserve `tmux capture-pane -p` and the
diagnostics listed below first.

Avoid using `printf '\033c'` as the first remedy. RIS is a broad terminal reset, and using it through
several terminal layers can discard more state than required. Likewise, `stty sane` alone does not
disable DECSET mouse tracking or pop a terminal keyboard-mode stack.

### Preventive mitigations

1. Record `claude --version` and update if it predates the relevant upstream fixes. As of the
   research date, the public changelog's newest entry is 2.1.258. [VERIFIABLE][^9]
2. On the remote host, start Claude Code with `CLAUDE_CODE_DISABLE_MOUSE=1` when mouse capture is not
   needed. This is the official opt-out; keyboard scrolling remains available, while click and
   wheel features are lost. [VERIFIABLE][^4]
3. As a narrower diagnostic, disable mouse for only the affected tmux session from another shell:

   ```sh
   tmux set-option -t <session-name> mouse off
   ```

   The launcher sets a per-session `mouse on`, so a global `set -g mouse off` alone does not override
   a managed session after creation. Disabling mouse prevents tmux mouse functionality but does not
   address extended Ctrl+C encoding.
4. Prefer keyboard scrolling or tmux copy mode (`Ctrl-b [`, then `q` to leave) when a prompt starts
   showing mouse reports. That workaround is also recorded in the tmux-specific upstream report.
   [VERIFIABLE][^7]
5. `/tui default` or `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` can reduce fullscreen mode use, but
   attached background sessions still use fullscreen, so this is not a complete agents-view
   workaround. [VERIFIABLE][^4]

Do not globally disable tmux `extended-keys` as the first mitigation. It is a server option, can
affect unrelated sessions, and removes modified-key functionality. It is useful only as an A/B
diagnostic on a separate tmux server or isolated test environment.

### Diagnostics to capture before killing a recurrence

Run these from a second shell, substituting the affected session name:

```sh
tmux display-message -p -t <session-name> \
  'pid=#{pane_pid} dead=#{pane_dead} mode=#{pane_key_mode} tty=#{pane_tty}'
tmux list-panes -a -F \
  '#{session_name}:#{window_index}.#{pane_index} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command}'
tmux show-options -t <session-name> mouse
tmux show-options -s extended-keys
tmux show-options -s extended-keys-format
tmux capture-pane -p -e -t <session-name> -S -200
pgrep -af 'claude|mosh|tmux'
claude --version
tmux -V
mosh --version
```

Also record terminal emulator/version, `$TERM`, `$TERM_PROGRAM`, whether `/tui` reports fullscreen,
and whether the same transition reproduces over SSH instead of mosh. Process listings should be
reviewed for secrets or private paths before sharing publicly.

## Verification

### Completed checks

- Decoded both captured protocol families against current xterm, tmux, and Kitty protocol
  documentation.
- Fetched every external source cited below on 2026-09-02.
- Searched the Claude Code issue tracker and changelog for mouse-mode leakage, extended-key leakage,
  tmux/SSH behavior, and agents-view transitions.
- Audited the generated session wrapper, tmux creation/reattach path, remote transport loop, and
  generated terminal key mapping.
- Re-read all repository facts in this document from the current working tree rather than accepting
  the task description's snapshot.

### Missing verification

- No live pane remained to inspect.
- No incident version matrix was captured.
- No deterministic reproduction or frozen failing regression test exists.
- No mosh-versus-SSH A/B replay was performed.
- No closed-source Claude Code internal state was observable.

### Adversarial review

Five explicit challenge passes were applied:

| Perspective | Challenge | Resolution |
|---|---|---|
| Conventional | Is this consistent with documented terminal behavior and known Claude Code reports? | Yes; protocol docs and several reports align at the observable boundary. |
| Contrarian | Could local `mouse on`, extended keys, or wrapper restart be the root cause? | They expand the trigger surface, but valid negotiated input should be consumed; no restart evidence exists. |
| Historical | Is this already fixed? | Several related fixes shipped, but the incident version is unknown and later reports show recurrence across nearby paths. |
| Adjacent | Could mosh state synchronization explain it? | It remains testable, but non-mosh reports show it is not required. |
| Skeptic | Does the capture prove the exact internal cleanup omission or a fork? | No. The report limits its conclusion to the consumer/state boundary and records both claims as unproven. |

No high-severity contradiction remained after narrowing the diagnosis from a specific cleanup call
to an input-state desynchronization at the Claude Code view boundary.

## Lessons learned

1. Terminal input protocols must be decoded by final byte shape, not by family nickname. A sequence
   ending in `~` is not CSI-u merely because it represents the same modified key.
2. “Ctrl+C did nothing” can occur upstream of signal handling. Capture the actual bytes before
   changing shell traps.
3. A TUI that enables mouse or enhanced keyboard input owns balanced enable/disable and view-level
   decoder handoff. Multiplexers correctly forwarding negotiated events are not sufficient evidence
   that the multiplexer caused a leak.
4. Before killing a broken pane, preserve version, `pane_key_mode`, process tree, tmux options, and a
   second-transport reproduction. The missing live evidence is the main limit on this diagnosis.
5. Defensive terminal cleanup belongs at an ownership boundary proven by a PTY regression test, not
   as an unconditional escape sequence injected into every attach.

## Fix log

| Date | Change | Result |
|---|---|---|
| 2026-09-02 | Decoded captured input, researched upstream reports and protocol specifications, audited local launcher/wrapper/transport code | Diagnosed as an upstream Claude Code input-state defect with local feature settings as preconditions; documented mitigations; no code change |

## Sources

[^1]: OpenBSD. (2026). [tmux(1) manual](https://man.openbsd.org/tmux.1). OpenBSD Manual Pages. Verified accessible (HTTP 200) 2026-09-02. (Extended-key modes and xterm/CSI-u formats.)
[^2]: Kitty project. (2026). [Comprehensive keyboard handling in terminals](https://sw.kovidgoyal.net/kitty/keyboard-protocol/). Kitty Documentation. Verified accessible (HTTP 200) 2026-09-02. (Modifier encoding, Ctrl+C semantics, and keyboard-mode stack.)
[^3]: XTerm project. (2026). [XTerm Control Sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.pdf). Invisible Island. Verified accessible (HTTP 200) 2026-09-02. (Mouse tracking modes and SGR encoding.)
[^4]: Anthropic. (2026). [Fullscreen rendering](https://code.claude.com/docs/en/fullscreen). Claude Code Documentation. Verified accessible (HTTP 200) 2026-09-02. (Mouse capture, tmux configuration, opt-outs, and renderer behavior.)
[^5]: Anthropic. (2026). [Mouse-tracking escape sequences leak into prompt input from new agent view](https://github.com/anthropics/claude-code/issues/58653). GitHub issue tracker. Verified accessible (HTTP 200) 2026-09-02. (Public user report with reproduction.)
[^6]: Anthropic. (2026). [Left arrow navigates to agents screen and breaks main session view on return](https://github.com/anthropics/claude-code/issues/75899). GitHub issue tracker. Verified accessible (HTTP 200) 2026-09-02. (Maintainer-reproduced public bug report.)
[^7]: Anthropic. (2026). [Mouse escape sequences leak into input prompt in tmux with SGR mouse mode](https://github.com/anthropics/claude-code/issues/30644). GitHub issue tracker. Verified accessible (HTTP 200) 2026-09-02. (Public tmux reproduction and workaround.)
[^8]: Anthropic. (2026). [Crash leaves the terminal in mouse-tracking mode](https://github.com/anthropics/claude-code/issues/84029). GitHub issue tracker. Verified accessible (HTTP 200) 2026-09-02. (Open, maintainer-reproduced report on abnormal-exit cleanup.)
[^9]: Anthropic. (2026). [Claude Code changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md). GitHub. Verified accessible (HTTP 200) 2026-09-02. (Current version and related shipped fixes.)
[^10]: Mobile Shell. (2026). [Mosh: the mobile shell](https://mosh.org/). Mosh Project. Verified accessible (HTTP 200) 2026-09-02. (Architecture and mouse-mode support.)
[^11]: Mobile Shell. (2014). [Mouse mode gets triggered with Shift+Mouse in mosh+tmux but not SSH](https://github.com/mobile-shell/mosh/issues/566). GitHub issue tracker. Verified accessible (HTTP 200) 2026-09-02. (Historical public user report; not evidence of the present root cause.)

## Appendix: Provenance ledger

| Claim | Source URL | Verbatim quote | Verdict | Live? |
|---|---|---|---|---|
| tmux supports distinct xterm and CSI-u extended-key formats | [tmux manual](https://man.openbsd.org/tmux.1) | “Selects one of the two possible formats for reporting modified keys” | SUPPORTED | Yes, 2026-09-02 |
| Ctrl+C can be delivered as an escape code instead of SIGINT | [Kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/) | “ctrl+c will no longer generate the SIGINT signal” | SUPPORTED | Yes, 2026-09-02 |
| DECSET 1003 enables any-event motion tracking | [XTerm control sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.pdf) | “all motion events are reported, even if no mouse button is down” | SUPPORTED | Yes, 2026-09-02 |
| DECSET 1006 selects SGR mouse encoding | [XTerm control sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.pdf) | “SGR (1006) The normal mouse response is altered” | SUPPORTED | Yes, 2026-09-02 |
| Claude Code officially supports disabling mouse capture | [Claude Code fullscreen docs](https://code.claude.com/docs/en/fullscreen) | “set `CLAUDE_CODE_DISABLE_MOUSE=1` to opt out of mouse capture” | SUPPORTED | Yes, 2026-09-02 |
| Claude Code's fullscreen renderer needs tmux mouse mode for wheel forwarding | [Claude Code fullscreen docs](https://code.claude.com/docs/en/fullscreen) | “Mouse wheel scrolling requires tmux’s mouse mode.” | SUPPORTED | Yes, 2026-09-02 |
| Attached background sessions use fullscreen rendering | [Claude Code fullscreen docs](https://code.claude.com/docs/en/fullscreen) | “Attached background sessions render fullscreen” | SUPPORTED | Yes, 2026-09-02 |
| Raw SGR reports have leaked in Claude Code's agent transcript view | [Claude Code issue 58653](https://github.com/anthropics/claude-code/issues/58653) | “scrolling with the mouse wheel inserts raw SGR mouse-tracking escape sequences” | SUPPORTED | Yes, 2026-09-02 |
| The left-arrow agents transition and degraded return state were reproduced | [Claude Code issue 75899](https://github.com/anthropics/claude-code/issues/75899) | “Returning from the agents screen leaves the main session in a degraded state” | SUPPORTED | Yes, 2026-09-02 |
| A tmux-specific prompt leak has a keyboard-copy-mode workaround | [Claude Code issue 30644](https://github.com/anthropics/claude-code/issues/30644) | “Use `Ctrl-b [` to enter tmux copy mode for scrolling” | SUPPORTED | Yes, 2026-09-02 |
| Claude Code has a reproduced abnormal-exit mouse-mode leak | [Claude Code issue 84029](https://github.com/anthropics/claude-code/issues/84029) | “Every subsequent mouse movement injects raw escape sequences into the shell prompt.” | SUPPORTED | Yes, 2026-09-02 |
| Anthropic shipped a fix for mouse tracking leaking after exit | [Claude Code changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md) | “Fixed mouse tracking escape sequences leaking to shell prompt after exit” | SUPPORTED | Yes, 2026-09-02 |
| Anthropic shipped a fix for raw keys over SSH | [Claude Code changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md) | “Fixed raw key sequences appearing in the prompt when running over SSH” | SUPPORTED | Yes, 2026-09-02 |
| The changelog's newest entry on the research date is 2.1.258 | [Claude Code changelog](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md) | “## 2.1.258” | SUPPORTED | Yes, 2026-09-02 |
| Mosh synchronizes terminal screen state rather than transporting only a byte stream | [Mosh](https://mosh.org/) | “The problem becomes one of state-synchronization” | SUPPORTED | Yes, 2026-09-02 |
| Mosh has supported mouse modes since version 1.2.5 | [Mosh](https://mosh.org/) | “New features include support for mouse modes” | SUPPORTED | Yes, 2026-09-02 |
| A historical report found a mosh+tmux mouse difference absent under SSH+tmux | [Mosh issue 566](https://github.com/mobile-shell/mosh/issues/566) | “When using mosh + tmux, this does not work - but it does with ssh + tmux.” | SUPPORTED | Yes, 2026-09-02 |
