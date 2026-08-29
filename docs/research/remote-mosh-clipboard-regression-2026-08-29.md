---
title: "Remote mosh/tmux copy-on-selection classification"
category: research
tags: [research, claude-code, clipboard, tmux, mosh, remote]
status: complete
source: "gpt-5-codex-2026-08-29"
template_version: "research-1.2.0"
---

# Remote mosh/tmux copy-on-selection classification

**Status:** complete

**Created:** 2026-08-29

## Table of Contents

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. Claude Code's classification and copy paths](#1-claude-codes-classification-and-copy-paths)
  - [Installed-binary evidence](#installed-binary-evidence)
  - [What the toast does and does not prove](#what-the-toast-does-and-does-not-prove)
  - [Historical boundary](#historical-boundary)
- [2. What the launcher controls](#2-what-the-launcher-controls)
  - [Transport and topology](#transport-and-topology)
  - [tmux configuration applied by the package](#tmux-configuration-applied-by-the-package)
  - [Bare mode is not an equivalent fix](#bare-mode-is-not-an-equivalent-fix)
- [3. Workaround assessment](#3-workaround-assessment)
  - [What tmux 3.7b supports](#what-tmux-37b-supports)
  - [What was established for mosh 1.4.0](#what-was-established-for-mosh-140)
  - [Candidate configuration, not a verified fix](#candidate-configuration-not-a-verified-fix)
  - [Required end-to-end UAT](#required-end-to-end-uat)
- [4. Adversarial review](#4-adversarial-review)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
  - [Anthropic feedback reproduction](#anthropic-feedback-reproduction)
  - [Repository decision](#repository-decision)
- [Gaps, Blindspots and Emergent Findings](#gaps-blindspots-and-emergent-findings)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

<!-- doc:region name="context" kind="immutable" -->

## Context

A macOS terminal connects to a headless Fedora host through mosh, then attaches to a
tmux session in which Claude Code runs. Claude Code's native copy-on-selection UI now
reports `copied N chars to tmux buffer · paste with prefix + ]`; a local session reports
`copied N chars to clipboard`. The investigation must identify who selects that message,
separate message classification from actual clipboard delivery, determine whether this
package controls the choice, and avoid claiming an untested workaround.

**Primary period:** 2026-08-25 through 2026-08-29
**Source weighting:** the installed Claude Code 2.1.251 bundle and retained 2.1.246–2.1.250
bundles are primary for Claude behavior; the current repository at commit
`21c649375277391799786c9467d0cf2e5c32a7cc` is primary for launcher behavior; installed
tmux 3.7b and mosh 1.4.0 artifacts are primary for mechanism availability. Public GitHub
material was excluded when it could not be re-fetched in-session.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

The active executable reported `2.1.251 (Claude Code)` and was installed on 2026-08-28.
Four retained executables, 2.1.246, 2.1.247, 2.1.248, and 2.1.250, provide a narrow local
history back to 2026-08-25. The host packages are `tmux-3.7b-2.fc44` and
`mosh-1.4.0-10.fc44`. Earlier Claude Code behavior and the exact version that introduced
the classification are not recoverable from the retained artifacts. [NO SOURCE]

Required public re-checks did not succeed in this confined run: `gh issue view` returned
`error connecting to api.github.com`, and `curl` returned `Could not resolve host:
raw.githubusercontent.com`. The previously cited issue and v2.1.77 changelog entry are
therefore not reused, quoted, or treated as evidence. [NO SOURCE]

## Executive Summary

1. **Root cause pinned down:** the installed Claude Code 2.1.251 bundle contains the
   actual classifier. It chooses native clipboard only when the session is not SSH and
   the platform has a native clipboard path; otherwise a usable tmux context returns
   `tmux-buffer`, and only the absence of tmux returns `osc52`. Its feedback formatter
   maps that value directly to the observed toast. [VERIFIABLE][^1]

2. **The toast is a route label, not proof that the system clipboard was skipped.** In
   the same bundle, the copy routine invokes `tmux load-buffer -w -`; when tmux is
   detected, it also emits both raw and DCS-wrapped OSC 52. The UI classifier does not
   consume those operations' success as its label input. [VERIFIABLE][^1]

3. **This package creates the topology but does not own the classification.** The remote
   launcher selects mosh or SSH, invokes the remote package, creates/attaches tmux, and
   starts `claude`; it neither emits the toast nor selects Claude Code's clipboard method.
   Its only relevant tmux customization is DCS passthrough plus automatic-rename control.
   [VERIFIABLE][^2][^3][^4]

4. **No working workaround was verified in this pass.** tmux 3.7b documents exactly the
   `load-buffer -w` and OSC 52 mechanisms Claude uses, and the installed xterm terminfo
   has `Ms`; both installed mosh 1.4.0 binaries contain the OSC 52 clipboard literal.
   However, the sandbox could neither connect to the live tmux socket nor create a local
   mosh UDP session, so actual macOS clipboard arrival remains untested. [VERIFIABLE][^5][^6]

5. **Recommendation:** do not implement a repository-side clipboard “fix.” Use
   `/report-feedback` to Anthropic with the precise reproduction below, asking Claude Code
   either to label the actual successful copy outcome, to explain the tmux label, or to
   expose a supported clipboard-strategy override. [INFERENCE]

## 1. Claude Code's classification and copy paths

### Installed-binary evidence

The active command resolves to the installed ELF bundle at
`$HOME/.local/share/claude/versions/2.1.251`; its SHA-256 is
`fd5f10ff0eb58daec04900466b143ea98aab50abf208a422bc008eaec13f61f7`.
The bundle identifies build time `2026-08-28T14:51:38Z` and source SHA
`37534ac596d80cefb02d272f036adba4ba055d2c`. [VERIFIABLE][^1]

The recovered bundled function, preserving its observed minified names, is:

```javascript
function vue(){if(!p())switch(P()){case"macos":case"windows":case"wsl":return"native";case"linux":if(typeof d().tool==="string")return"native";break}if(h())return"tmux-buffer";return"osc52"}
```

The immediately surrounding recovered source establishes the helpers:

- `p()` is true for attacher metadata marked SSH or for `SSH_CONNECTION`.
- `h()` returns a tmux invocation prefix when attacher metadata provides a valid tmux
  socket, or an empty prefix when `TMUX` is set; otherwise it returns null.
- On Linux, the native branch is available only after the clipboard probe selects a
  concrete tool (`wl-copy`, `xclip`, `xsel`, or the bundled addon) and only when the
  session is not SSH.

Thus the affected headless Linux + tmux topology deterministically classifies as
`tmux-buffer`; mosh is not a branch in this function. SSH is sufficient to skip native
copy, while headless Linux without a detected native tool reaches the same tmux branch
even if the SSH marker is absent. [VERIFIABLE][^1]

A second recovered function maps the classifier directly to the UI text:

```javascript
case"native":n=`copied ${o} ${r} to clipboard`;break;
case"tmux-buffer":n=`copied ${o} ${r} to tmux buffer \xB7 paste with prefix + ]`;break;
case"osc52":n=`sent ${o} ${r} via OSC 52 ...`;break;
```

This is the actual source of the observed message, not inferred pseudocode.
[VERIFIABLE][^1]

### What the toast does and does not prove

Claude Code's copy routine first attempts a native write only when the session is not
SSH, then calls its tmux helper. The tmux helper runs `tmux load-buffer -w -`, retries
without `-w` if that fails, and returns a boolean. The outer routine then emits OSC 52;
for tmux it returns a raw sequence plus a DCS-wrapped copy of that sequence.
[VERIFIABLE][^1]

The classification function that formats the toast is independent of the boolean returned
by `tmux load-buffer`. Therefore:

- `tmux-buffer` accurately describes the selected fallback category.
- It does **not** establish that only the tmux buffer changed.
- It does **not** establish that OSC 52 reached or failed to reach the macOS terminal.
- It does **not** establish that the macOS clipboard contains the selection.

The reported message regression is conclusively internal to Claude Code's classifier and
formatter. Whether there is also a clipboard-delivery regression is still inconclusive
without the end-to-end test in section 3. [INFERENCE]

The relevant settings code exposes `Copy on select` as a boolean. No strategy selector is
passed into the observed classifier, and `claude --help` exposes no clipboard/tmux/OSC
strategy option. This is strong local evidence that there is no supported user-facing
method override in 2.1.251, but it is not a proof that no undocumented internal override
exists. [INFERENCE]

### Historical boundary

The exact classifier pattern was also extracted from every retained binary. Versions
2.1.246 and 2.1.247 use `if(l.TMUX)return"tmux-buffer"`; 2.1.248 and 2.1.250 use the
same branch with renamed minified identifiers; 2.1.251 uses the socket-aware `h()` helper.
All five map non-native tmux sessions to `tmux-buffer`. [VERIFIABLE][^1]

Consequently, the change was not introduced between 2.1.246 and 2.1.251. The historical
report that the same remote session formerly said `clipboard` may be correct, but the
transition version and change rationale cannot be pinned down from the retained artifacts
or a live public changelog fetch. [NO SOURCE]

## 2. What the launcher controls

### Transport and topology

The remote path reads `transport` with default `mosh`, builds the remote command beginning
with `ai <engine> --is-remote`, and builds mosh as
`mosh --ssh <ssh command> user@host -- <remote shell> -l -c <remote command>` at
`src/ai_cli/main.py:2196-2307`. [VERIFIABLE][^2]

On the remote side, the default path creates a detached tmux session and then attaches to
it at `src/ai_cli/main.py:2680-2731`. The generated session script invokes Claude Code as
`claude ... --name ...` or `claude ... --resume ... --name ...` at
`src/ai_cli/session_script.py:690-735`. [VERIFIABLE][^2][^3]

This package therefore supplies the conditions under which Claude detects tmux, and the
remote shell normally carries SSH-origin context into the mosh-launched process. It does
not contain the strings `to tmux buffer`, `selection-copied`, or Claude's three-way method
classifier. [INFERENCE]

Switching this package's transport from mosh to SSH would not preserve a native
classification: the resulting process would still be SSH + tmux, which the installed
Claude classifier also maps to `tmux-buffer`. [INFERENCE]

### tmux configuration applied by the package

After creating or locating a tmux session, the launcher calls
`_configure_tmux_for_iterm2()`. That function runs only these relevant commands at
`src/ai_cli/iterm2.py:355-379`:

```text
tmux set-option -p -t <session> allow-passthrough all
tmux set-window-option -t <session> automatic-rename off
```

The first setting is favorable to Claude's DCS-wrapped OSC 52 attempt. The function does
not set `set-clipboard`, `terminal-features`, `terminal-overrides`, `DISPLAY`,
`WAYLAND_DISPLAY`, `SSH_CONNECTION`, or `TMUX`. [VERIFIABLE][^4]

Repository history also places mosh construction in the initial release and contains no
production-source commit matching `clipboard`, `set-clipboard`, or `OSC 52` under
`src/ai_cli`. That negative history narrows the search but does not prove when Claude Code
changed. [INFERENCE]

### Bare mode is not an equivalent fix

Removing tmux changes Claude's classifier and discards the feature this command is meant
to provide: detach/reattach and survival across transport drops. It is therefore not a
behavior-preserving fix. [INFERENCE]

Moreover, the current local remote-command builder does not append `--bare` to the remote
invocation at `src/ai_cli/main.py:2242-2263`, even though `-b/--bare` is accepted locally.
Accordingly, `ai c -R -b` is not established as a remote workaround from this source.
[VERIFIABLE][^2] The remote host's own `[session] use_tmux = false` can select bare mode,
but that intentionally gives up tmux and has not been end-to-end clipboard-tested here.

## 3. Workaround assessment

### What tmux 3.7b supports

The installed tmux 3.7b manual says `load-buffer -w` sends the loaded buffer to the
clipboard for the target client using the xterm escape sequence, if possible; `-` reads
the content from stdin. This exactly matches Claude Code's observed
`tmux load-buffer -w -`. [VERIFIABLE][^5]

The same manual says:

- `set-clipboard` may be `on`, `external`, or `off`; `on` accepts application OSC 52 and
  also attempts to set the terminal clipboard, while `external` attempts the terminal
  clipboard but does not let applications create tmux buffers.
- The `clipboard` terminal feature means the terminal can set the system clipboard.
- The `Ms` terminfo extension stores the current buffer in the host terminal selection.
- `allow-passthrough` accepts the `ESC P tmux; ... ESC \\` form Claude emits.

The installed `xterm-256color` and `tmux-256color` terminfo entries both contain `Ms`.
These facts make the route constructible for tmux 3.7b; they do not show that the next
mosh and terminal hops accepted the sequence. [VERIFIABLE][^5]

### What was established for mosh 1.4.0

The installed package reports mosh 1.4.0. Its manual confirms that mosh uses SSH for setup,
then runs its long-lived connection over UDP. [VERIFIABLE][^6] It is therefore not the
same transparent transport path as SSH. [INFERENCE]

`readelf -p .rodata` on both `/usr/bin/mosh-server` and `/usr/bin/mosh-client` retrieved
the exact literal `]52;c;`. This proves only that these exact Fedora binaries contain the
OSC 52 clipboard literal. [VERIFIABLE][^6] It does not prove which direction is accepted,
what payload constraints apply, or that the terminal-side clipboard update succeeds.
[INFERENCE]

A local `mosh --local` test was attempted in a pseudoterminal with a fixed OSC 52 payload,
but the sandbox rejected UDP socket binding with `Operation not permitted`. A live tmux
query was also rejected at the existing socket boundary. No end-to-end success is claimed.
[NO SOURCE]

### Candidate configuration, not a verified fix

The smallest standards-based candidate for an affected host is:

```tmux
set -s set-clipboard external
set -as terminal-features 'xterm*:clipboard'
set -g allow-passthrough on
```

This is a **candidate**, not a recommendation to edit configuration blindly. The first two
lines are directly constructible from tmux 3.7b's documented mechanisms; the package
already applies `allow-passthrough all` per pane. The host's xterm terminfo already has
`Ms`, so explicitly adding `clipboard` may be redundant and may not change behavior.
[INFERENCE]

Unsetting `TMUX` merely to force the `osc52` toast is not a sound workaround. It lies to
Claude Code about its nesting, disables the explicit tmux-buffer command path, and provides
no evidence that mosh will deliver the resulting sequence more reliably. [INFERENCE]

### Required end-to-end UAT

Run these checks inside the actual affected remote session, without changing config first:

```bash
claude --version
tmux -V
mosh --version | head -1
printf 'TMUX=%s SSH_CONNECTION=%s DISPLAY=%s WAYLAND_DISPLAY=%s\n' \
  "${TMUX:+set}" "${SSH_CONNECTION:+set}" "${DISPLAY:+set}" "${WAYLAND_DISPLAY:+set}"
tmux show-options -sv set-clipboard
tmux display-message -p '#{client_termname} | #{client_termfeatures}'
infocmp -x "$(tmux display-message -p '#{client_termname}')" | grep 'Ms='
```

Then use a unique harmless value and check the macOS clipboard manually:

```bash
printf 'remote-clipboard-uat-20260829' | tmux load-buffer -w -
```

If that reaches the macOS clipboard, tmux/mosh transport is working and the Claude toast
is misleading rather than evidence of copy failure. If it does not, temporarily apply the
candidate options with `tmux set-option`/`set-option -as`, repeat the exact probe, and record
`client_termname`, `client_termfeatures`, and the terminal application's clipboard-access
setting. Only that result can promote the candidate to a working workaround. [HEURISTIC]

## 4. Adversarial review

| Perspective | Severity | Challenge | Resolution |
|---|---|---|---|
| Conventional | low | Prefer public docs over reverse inspection. | The installed proprietary bundle is the primary artifact for the exact version; public claims were excluded after live retrieval failed. |
| Contrarian | high | The toast may not indicate failure because Claude also emits OSC 52. | Corrected the framing: root cause of the message is pinned; actual clipboard regression remains inconclusive. |
| Historical | medium | A current binary cannot date the regression. | Compared every retained 2.1.246–2.1.251 bundle and bounded, but did not invent, the transition date. |
| Adjacent | medium | tmux and mosh capabilities may make a workaround possible. | Read the exact installed tmux 3.7b manual and mosh 1.4.0 artifacts; supplied a candidate and mandatory UAT, not a success claim. |
| Skeptic | high | No live tmux/mosh path was exercised. | Explicitly withheld the “working workaround” conclusion and made end-to-end UAT an open gate. |

The high-severity challenges changed the conclusion materially. Another research round is
not justified without either network access to current public Claude sources/issues or an
interactive run through the actual macOS terminal, mosh connection, and tmux client.
[INFERENCE]

## Comparison

| # | Option | Changes the observed classification? | Preserves mosh + tmux? | Evidence | Verdict |
|---|---|---:|---:|---|---|
| 1 | Change mosh to SSH in this package | No | Yes | Claude classifies SSH + tmux the same way | Reject |
| 2 | Unset `TMUX` around Claude | Likely changes label to OSC 52 | Physically yes, semantically unsafe | Forces a false topology and drops the explicit tmux path | Reject |
| 3 | Run remote Claude bare | Removes tmux branch | No | Source-supported only through remote host config; `-R -b` is not forwarded | Optional diagnostic only |
| 4 | Configure tmux clipboard features | Does not change label | Yes | Exact tmux 3.7b mechanism; end-to-end result untested | Candidate/UAT |
| 5 | Anthropic changes classifier/feedback | Yes | Yes | Component that owns the label and method selection | Recommend |

## Recommendation

The root cause of the **message choice** is Claude Code 2.1.251's internal
native/tmux/OSC52 classifier, not a clipboard policy in this repository. The package creates
the supported remote topology, but there is no behavior-preserving repository change that
can make Claude Code call that topology `clipboard`. [INFERENCE]

Use `/report-feedback` to Anthropic. Do not file a repository implementation task for the
clipboard toast. Separately run the end-to-end tmux probe before claiming the macOS
clipboard itself is broken. [HEURISTIC]

### Anthropic feedback reproduction

```text
Claude Code: 2.1.251
Build SHA: 37534ac596d80cefb02d272f036adba4ba055d2c
Remote OS: Fedora
Transport: macOS terminal -> mosh 1.4.0 -> tmux 3.7b -> Claude Code
Environment: TMUX set; SSH_CONNECTION set; headless Linux clipboard environment

Steps:
1. Start a remote mosh+tmux Claude Code session.
2. Select rendered Claude output with copy-on-selection enabled.
3. Observe: "copied N chars to tmux buffer · paste with prefix + ]".
4. Control: repeat in a local macOS Claude Code session and observe
   "copied N chars to clipboard".
5. Independently test `printf 'unique-value' | tmux load-buffer -w -` and record
   whether the macOS clipboard changes.

Installed-bundle evidence:
- the method classifier returns native only for non-SSH native platforms/tools,
  then tmux-buffer for a usable tmux context, else osc52;
- the feedback formatter maps tmux-buffer directly to the observed toast;
- the copy routine still calls `tmux load-buffer -w -` and emits raw plus
  DCS-wrapped OSC 52 under tmux.

Request:
Please clarify whether the toast is intended to describe the predicted route or the
actual successful destination. If clipboard delivery succeeds, report clipboard success
rather than only "tmux buffer". If it fails, expose diagnostics and a supported strategy
override for tmux/OSC52. Please also document the behavior for remote Linux + tmux.
```

The installed bundle itself names
`https://github.com/anthropics/claude-code/issues` as its feedback channel.
[VERIFIABLE][^1]

### Repository decision

No code change is recommended for this diagnosis. If a future product requirement asks for
an explicit remote-bare diagnostic, propagating local `-b/--bare` into the remote command
would be a separate launcher consistency change with its own tests; it would not fix the
mosh+tmux behavior under investigation. [INFERENCE]

## Gaps, Blindspots and Emergent Findings

- **Actual clipboard outcome:** the report establishes the toast's cause, not whether the
  selected text reaches the macOS clipboard. [NO SOURCE]
- **Introduction version:** all retained builds already contain the tmux branch; no earlier
  bundle or live changelog was available. [NO SOURCE]
- **Attacher metadata:** 2.1.251 can use terminal-attacher metadata in preference to raw
  environment variables. The affected session's metadata object was not observable in this
  run, but plain `TMUX` is sufficient for the same classification. [INFERENCE]
- **Misleading-success blindspot:** `tmux-buffer` is computed before/independently of
  clipboard-delivery success. This design can report the same label whether `load-buffer -w`
  reaches the host clipboard or only loads the tmux buffer. [INFERENCE]
- **Remote bare flag:** the launcher accepts `-b` locally but does not propagate it into the
  remote command. This is adjacent behavior, not the cause of the toast. [VERIFIABLE][^2]

## Open Questions

1. Does `tmux load-buffer -w -` on the actual affected client update the macOS clipboard?
2. What do `#{client_termname}` and `#{client_termfeatures}` report for that mosh-attached
   tmux client?
3. Which Claude Code release first changed the historical remote message, and was that
   change intentional?
4. Does Anthropic intend the toast to report a predicted method, an attempted method, or a
   verified destination?

## Sources

[^1]: Anthropic. (2026). *Claude Code 2.1.251 installed ELF bundle* (`$HOME/.local/share/claude/versions/2.1.251`; build SHA `37534ac596d80cefb02d272f036adba4ba055d2c`). Local primary artifact. [No online source located for the proprietary bundle; its embedded feedback URL is the [Claude Code issue tracker](https://github.com/anthropics/claude-code/issues).] Verified locally 2026-08-29. (Classifier, toast formatter, copy routine, settings surface, build metadata, and retained-version comparison.)

[^2]: ai-cli-utils. (2026). [`src/ai_cli/main.py`](../../src/ai_cli/main.py) at `21c649375277391799786c9467d0cf2e5c32a7cc`. Local repository primary source. Verified locally 2026-08-29. (Remote command construction at lines 2196–2307; tmux creation/attach at 2680–2731; bare option at 2812–2832.)

[^3]: ai-cli-utils. (2026). [`src/ai_cli/session_script.py`](../../src/ai_cli/session_script.py) at `21c649375277391799786c9467d0cf2e5c32a7cc`. Local repository primary source. Verified locally 2026-08-29. (Claude invocation at lines 690–735.)

[^4]: ai-cli-utils. (2026). [`src/ai_cli/iterm2.py`](../../src/ai_cli/iterm2.py) at `21c649375277391799786c9467d0cf2e5c32a7cc`. Local repository primary source. Verified locally 2026-08-29. (`allow-passthrough` and `automatic-rename` configuration at lines 355–379.)

[^5]: tmux project. (2026). *tmux 3.7b manual and installed terminfo*. Fedora package `tmux-3.7b-2.fc44`; [upstream project](https://tmux.github.io/). Verified from the installed package and `man tmux` 2026-08-29. (`load-buffer -w`, `set-clipboard`, `terminal-features`, `Ms`, and `allow-passthrough`.)

[^6]: Mosh project. (2026). *mosh 1.4.0 manual and installed client/server binaries*. Fedora package `mosh-1.4.0-10.fc44`; [upstream project](https://mosh.mit.edu/). Verified from the installed package, `man mosh`, and ELF `.rodata` 2026-08-29. (SSH/UDP architecture, version, and OSC 52 literal presence.)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none — no auto-remediation run)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** cx-read-only-2026-08-29
**Model:** GPT-5 Codex
**Date:** 2026-08-29

````text
You are a principal terminal and CLI engineer specializing in terminal multiplexers,
remote terminal protocols, and clipboard escape-sequence behavior. Investigate why Claude
Code copy-on-selection in a macOS-to-Fedora mosh+tmux session reports a tmux buffer rather
than the clipboard. Inspect the actual installed Claude Code executable, the current
launcher source and history, and the exact installed tmux and mosh artifacts. Separate the
cause of the UI message from evidence of actual clipboard delivery. Do not implement code.

Required questions:
1. What exact observed Claude Code logic chooses native, tmux-buffer, or OSC52 feedback?
2. Which parts of the remote topology does this repository create or configure?
3. Is there a working workaround for the exact tmux 3.7b and mosh 1.4.0 environment?
4. If no workaround is end-to-end verified, say so and recommend precise product feedback.

Anti-fabrication rule: fetch every cited public source in-session and put the exact retrieved
supporting substring in the provenance ledger. For GitHub use `gh issue view`/`gh api`; for
raw GitHub files use `curl`. If retrieval fails, do not cite or quote that source. Local
binary and repository claims must likewise carry exact retrieved text, not reconstructed
pseudocode.

<grounding_instructions>
You are a principal terminal and CLI engineer who has debugged production terminal
multiplexer, remote terminal, and clipboard escape-sequence failures. You distinguish a
UI classifier from an end-to-end copy result and state explicitly when the latter was not
tested.

Temporal scope: Weight sources by recency — 2026 (primary) → 2025 → 2024.
Pre-2024 sources are background context only unless foundational to the topic.
If post-2024 literature is genuinely sparse for a subtopic, state
"[subtopic]: no significant post-2024 developments found" rather than
backfilling with older sources. Backfilling is a failure mode, not a hedge.

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

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX.
NEVER generate binary images.
</grounding_instructions>

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

Retrieval enforcement: for every load-bearing claim, run at least one source retrieval or local
artifact inspection. Do not return [VERIFIABLE] without a source actually retrieved in-session.
Do not stop after one round. If no source is found for a sub-question, search alternate framings
before using [NO SOURCE]. Cached prior URLs are not evidence: re-fetch any public claim used in
this run. If a required public URL cannot be fetched with the mandated command, omit the citation
and preserve the gap.
````

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

Every code span below is copied from command output retrieved during this run. Where a
row contains multiple spans, each is a separate exact whitespace-normalized substring.

| Claim | Source | Actual retrieved text | Verdict | Live? |
|---|---|---|---|---|
| Claude 2.1.251 classifies non-native tmux as `tmux-buffer`; its helpers use SSH and tmux metadata/environment. | Installed bundle [^1] | `function vue(){if(!p())switch(P()){case"macos":case"windows":case"wsl":return"native";case"linux":if(typeof d().tool==="string")return"native";break}if(h())return"tmux-buffer";return"osc52"}`; `function p(){let t=vl();if(t)return t.ssh??!!a.SSH_CONNECTION}`; `function h(){let t=vl();if(t){if(t.mux!=="tmux"||!t.tmuxSocket)return null;return N(t.tmuxSocket)&&vR(t.tmuxSocket)?["-S",t.tmuxSocket]:null}return a.TMUX?[]:null}` | SUPPORTED | Local artifact readable 2026-08-29 |
| The observed toast is the formatter for that class. | Installed bundle [^1] | `case"tmux-buffer":n=\`copied ${o} ${r} to tmux buffer \xB7 paste with prefix + ]\`;break;` | SUPPORTED | Local artifact readable 2026-08-29 |
| Claude attempts `tmux load-buffer -w -` and retries without `-w`. | Installed bundle [^1] | `await Fe("tmux",[...e,"load-buffer","-w","-"],o)`; `await Fe("tmux",[...e,"load-buffer","-"],o)` | SUPPORTED | Local artifact readable 2026-08-29 |
| Under tmux Claude also returns raw and DCS-wrapped OSC 52. | Installed bundle [^1] | `if(o==="tmux"){let s=\`${ML}]52;c;${e}${JE}\`;return s+ZS(s)}` | SUPPORTED | Local artifact readable 2026-08-29 |
| The retained history already classified tmux this way in 2.1.246. | Installed 2.1.246 bundle [^1] | `function ft(){if(!x())switch(u()){case"macos":case"windows":case"wsl":return"native";case"linux":if(typeof S().tool==="string")return"native";break}if(l.TMUX)return"tmux-buffer";return"osc52"}` | SUPPORTED | Local artifact readable 2026-08-29 |
| The bundle identifies the exact product build and feedback channel. | Installed bundle [^1] | `VERSION:"2.1.251",FEEDBACK_CHANNEL:"https://github.com/anthropics/claude-code/issues",BUILD_TIME:"2026-08-28T14:51:38Z",GIT_SHA:"37534ac596d80cefb02d272f036adba4ba055d2c"` | SUPPORTED | Local artifact readable 2026-08-29 |
| The launcher builds a mosh remote command, not a clipboard policy. | `src/ai_cli/main.py:2242,2279-2287` [^2] | `mosh_args = ["mosh"]`; `mosh_args += ["--", remote_shell, "-l", "-c", remote_cmd]` | SUPPORTED | Local source readable 2026-08-29 |
| The remote path creates and attaches tmux. | `src/ai_cli/main.py:2703-2731` [^2] | `["tmux", "new-session", "-d", "-s", session_id, *_iterm_env_flags, "--", _session_shell, _script_path]`; `os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])` | SUPPORTED | Local source readable 2026-08-29 |
| The generated session invokes `claude`, with no clipboard strategy argument. | `src/ai_cli/session_script.py:725-735` [^3] | `run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name"`; `run_agent claude $claude_perms_flag --name "$ai_name"` | SUPPORTED | Local source readable 2026-08-29 |
| The package's relevant tmux customization is passthrough and rename control. | `src/ai_cli/iterm2.py:370-379` [^4] | `["tmux", "set-option", "-p", "-t", session_id, "allow-passthrough", "all"]`; `["tmux", "set-window-option", "-t", session_id, "automatic-rename", "off"]` | SUPPORTED | Local source readable 2026-08-29 |
| `load-buffer -w -` is an exact tmux 3.7b clipboard mechanism. | Installed tmux 3.7b manual [^5] | `If -w is given, the buffer is also sent to the clipboard for target-client using the xterm(1) escape sequence, if possible. If path is ‘-’, the contents are read from stdin.` | SUPPORTED | Local manual readable 2026-08-29 |
| tmux 3.7b documents the clipboard feature and `Ms`; the installed terminfo provides `Ms`. | Installed tmux 3.7b manual and terminfo [^5] | `clipboard Allows setting the system clipboard.`; `Ms Store the current buffer in the host terminal's selection (clipboard).`; `Ms=\E]52;%p1%s;%p2%s\007` | SUPPORTED | Local artifacts readable 2026-08-29 |
| Exact installed mosh binaries contain the OSC 52 literal. | Installed mosh 1.4.0 client/server [^6] | `]52;c;`; `mosh 1.4.0-10.fc44 https://mosh.mit.edu/` | SUPPORTED for literal presence only | Local artifacts readable 2026-08-29 |
| Mosh changes transport from SSH setup to UDP. | Installed mosh 1.4.0 manual [^6] | `mosh uses ssh to establish a connection to the remote host and authenticate with existing means (e.g., public-key authentication or a password). mosh executes the unprivileged mosh-server helper program on the server, then closes the SSH connection and starts the mosh-client, which establishes a long-lived datagram connection over UDP.` | SUPPORTED | Local manual readable 2026-08-29 |

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- **2026-08-29 — evidence-reset investigation.** Inspected the empty target, current
  repository source and history, active Claude Code 2.1.251 ELF bundle, retained
  2.1.246–2.1.250 bundles, tmux 3.7b manual/terminfo, and mosh 1.4.0 manual/ELF data.
  Public GitHub retrieval failed at DNS/API access and those sources were excluded.
  Live tmux and local-mosh UAT were blocked at Unix-socket/UDP boundaries, so no working
  workaround was claimed. Five-perspective adversarial review completed; high-severity
  challenges were incorporated into the conclusion.

<!-- /doc:region name="run_history" -->
