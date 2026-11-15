---
title: Codex session naming and resume by name
category: design
tags: [design, codex, pi, session-names, resume]
status: ACTIVE
template_version: "design-1.0.0"
task: AI-CLI-kbvf
---
<!-- Full structure: see TEMPLATE.md in this directory -->

# Codex session naming and resume by name

A Codex session launched with `ai cx` gets the launcher's session name as its Codex
session name the first time it starts, and every later `ai cx <name>` resumes that same
Codex thread by name. The same relaunch defect made `ai p <name>` start a new Pi
session, and that is fixed here too.

## Founding Ask Coverage

The owner's words are reproduced from the two issues (AI-CLI-kbvf, AI-CLI-6iiu) with one
kind of redaction: private session, host and peer names are replaced by `[session]`,
`[host]` and `[peer]`, because this repository is a public package. Nothing else changes.

```text
i don't think codex cli cc sessions launched via `ai cx ...` have the name of the codex
session (e.g. [session] which i just created). [...] there might be a way to name a codex
session (not just the worktree name) via the codex session launch command [...] i also know
you can use `/rename <codex-session-name` command in the codex session that's launched. [...]
if we need to use the shared prompt injeciton library to prompt injection `/rename
<codex-session-name>` at the beginning when the session is first launched, then let's do
that. or a codex hook or whatever is the proper and most elegant solution.

[...] it should work for both local and remote codex sessions launched via `ai cx` and all
machine profiles / hosts / operating systems etc [...] if it can be done by assigning the
name at codex session launch / creation command, then do that. otherwise we can use shared
prompt injection library to run `/rename <codex-session-name>` on session startup the first
time [...] (ideally on just the first codex session launch, as it shouldn't need to do prompt
injection every session startup once it has the right name [...])

[...] i had already run `/rename [session]` and then when i relaunched into the cc session
just now, it made me rename it again. also, did it resume into the same / existing codex
session by name or did it create a new codex session transcript / artifacts [...] we need it
to always check and try resume / continue into an existing codex session worktree /
transcript by name or id [...] just like it does for `ai c` command and `ai g` command and
`ai p` command (i think it actually might be broken for ai p` [...])

actually, it shows that there's no codex sessions for some reason. idk why. i typed `/resume`
and there's none. [...] we should figure out why it didn't create an [session] codex session /
transcript the first time when i ran just `/rename [session]` and then exited [...]
```

| Raw entry | Disposition |
|---|---|
| "name ... via the codex session launch command" | Not available: Codex has no launch-time name flag or config key (F-1). Recorded, not narrowed: the owner's fallback order applies. |
| "or a codex hook" | Not available: a `SessionStart` hook can only add model context (F-2). |
| "prompt injection `/rename <codex-session-name>` ... the first time" | D-2: one `/rename` typed into the new session, through tmux `send-keys` or the bare launch's pty submission FIFO. |
| "ideally on just the first codex session launch" | D-1: the injection runs only when Codex's own session index has no resumable thread under that name, which is exactly the first launch. |
| "always check and try resume / continue ... by name or id" | D-1: every launch resolves the name to a thread id and runs `codex resume <id>`. |
| "it made me rename it again" | D-1 root cause: a fresh tmux session always ran a bare `codex` (R-1). |
| "there's no codex sessions ... `/resume` and there's none" | F-4: Codex's own picker and by-name lookup hide a thread with no turn; resuming by id does not. D-1 resolves the id itself. |
| "local and remote ... all machine profiles / hosts / operating systems" | D-3: one resolver and one injector run on the owning host for local, `-R`, tmux, `--once` and bare-pty launches. |
| "might be broken for ai p" | R-2 and D-4: confirmed and fixed in the same lines. |

> **Post-build review:** founding-ask-narrowed: Founding ask "all machine profiles / hosts / operating systems": cannot be built: a native Windows bare launch with no tmux has no harness-owned input path (`os.openpty` is POSIX-only; a ConPTY host is the open AI-CLI-ysht), so nothing can type `/rename` there. That launch still resumes by name once the session has been named, and it prints a one-line hint to run `/rename <name>` once.

## Codex facts (measured 2026-11-15, Codex CLI 0.169.0, Linux x86_64)

Measured with a throwaway `CODEX_HOME`, a dummy local model provider (no credentials and no
model call), and a private tmux socket.

**F-1. No launch-time name.** `codex --help` and `codex resume --help` list no name option,
and no config key sets a thread name. `codex resume` takes a name to look up, not to set:

```text
Usage: codex resume [OPTIONS] [SESSION_ID] [PROMPT]
  [SESSION_ID]
          Session id (UUID) or session name. UUIDs take precedence if it parses.
```

**F-2. Hooks cannot name a thread.** The `SessionStart` hook's output schema
(`codex-rs/hooks/src/schema.rs` at `rust-v0.169.0`) carries only `hookEventName` and
`additionalContext`, and unknown fields are rejected (`deny_unknown_fields`).

**F-3. Where names live.** `/rename` appends `{"id", "thread_name", "updated_at"}` to
`$CODEX_HOME/session_index.jsonl` (default `~/.codex`); the newest non-empty entry for an
id wins (`codex-rs/rollout/src/session_index.rs`). Rollouts are
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl`, compressed to
`.jsonl.zst` after seven days; archived ones move to `archived_sessions/`. On 0.169.0 a
`/rename` in a session with no turn materialises the rollout at once ("Naming saves a new
thread even before its first turn", upstream #49785 and #55380, October 2026). Measured:

```text
$ /rename session-1   (typed into a fresh TUI, no prompt sent)
session_index.jsonl: {"id":"01a1e138-...","thread_name":"session-1","updated_at":"2026-11-15T21:58:52Z"}
sessions/2026/11/15/rollout-2026-11-15T16-58-43-01a1e138-....jsonl   (1 line: session_meta)
```

**F-4. A thread with no turn is invisible to Codex's own lookups.** After that rename and
an exit:

```text
$ codex resume session-1
ERROR: No saved session found with ID session-1. Run `codex resume` without an ID to choose from existing sessions.
$ codex resume            (picker)
  No sessions yet
$ codex resume 01a1e138-42c6-7643-af6a-48768d55c843
  ... dummy-model default · /tmp/.../wt · session-1      (resumed, name in the footer)
```

After one turn, `codex resume session-2` resumes by name. This is the owner's "no codex
sessions" observation: Codex behaviour, not a launcher bug. On a Codex older than the
October 2026 change the rollout is not written at all until the first turn, so nothing could
be resumed by any route. The launcher therefore resolves the id from the index itself.

**F-5. The initial prompt is not a slash command.** `codex '/rename session-2'` sends the
text to the model as a user message. The name must be typed into the composer.

**F-6. Readiness.** The `›` composer line is drawn while Codex is still loading, and keys
typed then are lost or queued; the idle footer `? for shortcuts` appears once the composer
takes input. A `/rename` submitted during a running turn is queued until the turn ends.

## Root causes

**R-1 (`ai cx`).** The session script ran `codex` when `first_run` was true and
`codex resume --last` only for an in-loop restart. Every `ai cx <name>` relaunch starts a
new tmux session, so `first_run` is true and a new Codex thread was created each time. The
bare path did the same unless `-r` was given, and the `--once` path always ran `codex`.
Nothing ever named the thread.

**R-2 (`ai p`, AI-CLI-tc25).** The same `first_run` gate dropped `--continue` on every
relaunch, so `ai p <name>` started a new Pi session each time. The bare path continued only
with `-r`, and `--once` never did. Pi 1.5.2's `--continue` is scoped to the current
directory (the session worktree) and starts a fresh session when there is none (measured),
so it is safe on every launch.

## Decisions

**D-1. Resolve the name to a thread id on every launch.** `ai internal
resolve-codex-session <name>` prints the id of the newest thread whose latest non-empty name
in `session_index.jsonl` equals the session name and whose rollout (plain or `.zst`) is
still under `sessions/`. The launcher runs `codex resume <id>`, which works whether or not
the thread has had a turn (F-4). With no such thread it starts `codex` and names it (D-2).
An in-loop restart that still finds nothing keeps the old `codex resume --last`. Codex's
own index is the registry: a second copy of the mapping would drift from `/rename`s the
owner types later. The name, not the working directory, is the key, which is how the owner
asked to resume; the session name already carries the project and host.

**D-2. Name a new thread once, by typing `/rename <name>`.** `ai internal
name-codex-session` waits for the idle footer (F-6, up to 30 s), types the command, presses
Enter, and confirms by watching the index grow with an entry for that name, for up to 120 s:
a `/rename` typed during a first start waits behind Codex's "Waiting for startup" (measured:
it landed 28 s after launch). On tmux it uses
`send-keys` (as the auto-compact and resume-prompt deliveries do); on a bare launch hosted
on a launcher-owned pty it writes to that session's submission FIFO. A resume prompt waiting
for a fresh thread is pasted only after the name is confirmed, so the `/rename` is not
queued behind a turn (F-6). Failure is reported in the launch log, never in the pane.

**D-3. One path for every launch shape.** The resolver and the injector run where Codex
runs: in the session script for local and `-R` tmux launches (the remote script runs on the
owning host), and in the launcher for `--once` and bare launches. Codex's home is
`$CODEX_HOME` or `~/.codex` on every OS.

**D-4. Pi continues on every launch.** Every Pi launch (tmux, `--once`, bare) passes
`--continue`.

## Not changed here

- `ai exit-session` resolves a session only by its registry key (`cx-<name>`,
  `cx-r-<name>`), and with no name it can verify only a Claude Code session record. The
  cx launch is registered; the lookup is the gap. Filed separately (AI-CLI-8ur2).
- Pi 1.5.2 refuses `--provider` without `--model` ("Error: --provider requires --model"),
  so `ai p` with the default provider exits at once on that Pi. Recorded on AI-CLI-tc25.
- A second Ctrl+C that lands while the wrapper writes the exit prints "Session exit
  persistence failed" although the exit was recorded. A resumed Codex quits on the first
  Ctrl+C, so a double press hits this more often. Filed separately (AI-CLI-1m8b).

## Verification

- Behaviour tests run the generated session script's Codex and Pi dispatch with stubbed
  `codex`, `pi` and `ai`, and run the resolver and the injector against a real index and a
  scripted tmux.
- Live, 2026-11-15, Codex 0.169.0 under `ai cx -t -W` on Linux with a throwaway
  `CODEX_HOME`. First launch: the namer typed `/rename ai-cli-ec2-probe-1`, the footer showed
  `· ai-cli-ec2-probe-1` and the index gained one entry for thread `01a1e157-...`. Exit, then
  `ai cx -t -W 1` in a new tmux session: the process was `codex resume 01a1e157-...`, the same
  name was in the footer, and there was still one index entry and one rollout. The same
  relaunch with the base code ran a bare `codex` with no name.
