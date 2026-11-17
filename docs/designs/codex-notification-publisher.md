---
title: Codex notification publisher, launcher side
category: design
tags: [design, codex, notifications, session-script]
status: ACTIVE
template_version: "design-1.0.0"
---

# Codex notification publisher, launcher side

A supervised `ai cx` session can let a separate tool deliver notices into its Codex thread.
That tool, the notification publisher, is not part of this package. The launcher only calls it,
through a small closed command-line contract, when `[codex.publisher] command` is set. With no
command the session launches Codex exactly as before and no phase is ever called.

Scope: supervised (tmux) `ai cx` sessions on Linux and macOS. Bare launches build their own
command and never call the publisher. Windows is skipped because the publisher's transport is a
Unix socket. Any further narrowing (which hosts the publisher accepts) is the publisher's
decision, reported back as a refusal.

## The contract

Each phase runs `<command> <phase>` as a child of the session script. The child gets one UTF-8
JSON object on stdin, at most 16384 bytes encoded; a larger request is never sent. It starts
in its own process group. When `timeout_ms` passes, the launcher kills that whole group and
reaps the child. The child prints one JSON object on stdout with exactly these keys:
`schemaVersion`, `phase`, `status`, `reason`, `possiblyPersisted`, `recordId`, `socketPath`.
Diagnostics on stderr go to the launch log, not the terminal.

| Exit | Meaning | Launcher action |
| --- | --- | --- |
| 0 | accepted (`status` must be `accepted`) | continue |
| 2 | invalid request | one notice naming a launcher defect; no further phase for the generation |
| 3 | known unavailable or refused | unavailable; after begin, finish |
| 4 | outcome uncertain | as below for each phase |

A timeout, an exit code outside these, or stdout that does not parse against the seven keys
(an unknown, missing or duplicate key, two values, a non-integer `schemaVersion`, a `phase`
other than the one asked) is handled as exit 4.

Requests, all with `schemaVersion` 1:

| Phase | Fields |
| --- | --- |
| begin | `sessionName`, `launcherRunGeneration`, `wrapperPid`, `worktreeRoot`, `cwd`, `repoRoot`, `tmuxSession`, `paneId` |
| running, enroll, refresh | `recordId`, `enginePid` |
| finish | `recordId` |
| reconcile | `launcherRunGeneration`, `wrapperPid` |

`launcherRunGeneration` is the supervisor's run generation (`AI_CLI_RUN_GENERATION`).
`wrapperPid` is the pid of the session script's child body, the shell that starts the engine;
every phase child and the engine descend from it. `tmuxSession` and `paneId` are empty outside
tmux. `worktreeRoot` is the checkout's top level and `repoRoot` the main checkout (the parent
of git's common directory); outside git both are the working directory.

## The sequence

1. **begin.** On acceptance the response carries `recordId` and `socketPath`.
2. **Engine.** The script starts `codex app-server --listen unix://<socketPath>` and waits up
   to `timeout_ms` for the socket to accept a connection.
3. **running** proves the engine (`enginePid` is the app-server's pid). It does not need a
   thread: a fresh app-server has none until the TUI starts one.
4. **TUI.** Codex starts as before (fresh, `resume <thread>` or `resume --last`), with
   `<remote_flag> unix://<socketPath>` in place of `--no-daemon`.
5. **enroll.** A background monitor ticks every `min(30, refresh_seconds)` seconds, where 30 s
   is the session heartbeat's interval. It calls enroll on each tick until enroll is accepted.
   An exit-3 answer with status `pending` and reason `thread-not-loaded` means the TUI has not
   loaded its thread yet, so the monitor keeps ticking. When `enroll_window_seconds` passes
   first, the monitor calls finish and prints the notice.
6. **refresh** every `refresh_seconds` once enrolled. Any refusal, exit 3 or exit 4 stops
   refreshing for the generation and calls finish.
7. **finish** when the TUI exits: the script sends the monitor SIGTERM, the monitor calls
   finish, and then the script stops the engine. If the session script dies, the monitor sees
   it gone, calls finish and stops the engine itself.

Failure paths. Every one of them leaves the user's session running.

- begin exit 3, or a publisher that cannot be started: Codex launches as before with no
  app-server, and one notice.
- begin exit 2, or a request over the stdin bound: the same, and no later begin for the
  generation.
- begin exit 4, a timeout, or an unreadable answer: `reconcile` runs once. begin is never
  retried for the generation and running is never called. Codex launches as before.
- running refused, or the app-server not answering: finish, stop the engine, launch as before.

The "Codex notifications are unavailable for this session" notice is printed at most once per
run generation, across the agent restarts one supervisor makes. The launcher never deletes a
socket; socket cleanup is the publisher's, and only for its own generation. A refusal on
stdout never revokes delivery by itself. Revocation is the publisher's job, and the launcher
only stops calling refresh and calls finish.

An attached TUI runs its tools under the session's app-server, not under the TUI process the
launcher attests. Its agent identity is therefore recorded as `shared-daemon`, the same as
a Codex without `--no-daemon`. So `ai exit-session` from inside such a session needs the
session name passed.

Per-generation state (the live record, whether begin is closed, whether the notice was shown)
lives in `$XDG_STATE_HOME/ai-cli-utils/codex-publisher/`, one small file per generation, and
files older than seven days are pruned when a new begin runs.

## Configuration

`[codex.publisher]` in `config.toml`, read on every call:

| Key | Default | Meaning |
| --- | --- | --- |
| `command` | `[]` (off) | the publisher's argv |
| `timeout_ms` | 15000 | outer bound on each phase, 1 to 600000; keep it above the publisher's own bound |
| `refresh_seconds` | 60 | refresh cadence once enrolled, 1 to 86400 |
| `enroll_window_seconds` | 600 | how long to keep asking to enroll, 1 to 86400 |
| `remote_flag` | `--remote` | the Codex TUI option that attaches it to the app-server |

Each number must be a whole number in range. Any invalid value turns the publisher off for
that launch, with one message naming the key, and the session still starts.

`remote_flag` is configurable because the launcher has not confirmed the option against a
Codex release; `codex app-server --listen unix://PATH` was measured on codex-cli 0.169.0.
