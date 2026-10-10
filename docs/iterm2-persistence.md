# iTerm2 session persistence

Quitting iTerm2 does not end an `ai` session. A local session keeps running in its tmux server, and a remote `ai c -R` session keeps running in the remote host's tmux. What a quit loses is the map of which session was in which window, tab and pane. `ai` keeps that map in a small machine-local registry so the sessions can be found and re-attached afterwards.

This page covers the configuration section, the registry, and `ai iterm2 restore`, which brings the recorded sessions back into iTerm2 after a restart.

## Configuration

The settings live in `~/.config/ai-cli-utils/iterm2.toml` (on Windows, the same file under the ai-cli-utils config directory), in the `[iterm2.persistence]` tables. The file is read on every `ai` invocation, so an edit takes effect on the next command without restarting anything.

Every key has a built-in default equal to the value below, so a file without these tables behaves exactly as if it contained them. Tracking is on by default because it only writes one owner-only file. Restore is off by default because it opens windows and runs commands, so each machine has to opt in.

The shipped file carries these tables commented out, as a template: TOML allows a table to be defined only once, so the live definition is either an installer-managed block or your own uncommented copy.

A key with the wrong type (for example `include = "c-*"` instead of `include = ["c-*"]`), an unknown key, or a `mode` outside the allowed values is an error that names the key and the expected type. `ai` never falls back to the default for a key it could not read. At launch, such an error is printed as a single warning and the session launches untracked.

### `[iterm2.persistence]`

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | Master switch for tracking and restore. `false` records nothing and restores nothing. |

### `[iterm2.persistence.tracking]`

| Key | Default | Effect |
|---|---|---|
| `enabled` | `true` | Record each `ai` launch in the registry. `false` writes no registry file and creates no directory. |
| `include_local` | `true` | Record local tmux sessions. |
| `include_remote` | `true` | Record `ai c -R` sessions. |
| `include` | `[]` | Session-name globs (`fnmatch` syntax, case-sensitive). Empty records every session; otherwise only a session matching one glob is recorded. |
| `exclude` | `[]` | Session-name globs applied after `include`; a match is not recorded. |
| `refresh_on_launch` | `true` | After each launch from an iTerm2 pane on macOS, re-read every other record's window, tab and pane in one AppleScript pass. |

A launch that a rule filters out is logged at debug level with the rule that filtered it.

### `[iterm2.persistence.restore]`

What each key does to a run is described under [Restore keys](#restore-keys).

| Key | Default | Effect |
|---|---|---|
| `enabled` | `false` | Opt in to restore on this machine. |
| `on_startup` | `false` | Restore when iTerm2 starts (needs a startup hook). |
| `on_demand` | `true` | Restore when you run the restore command. |
| `mode` | `"arrangement+sessions"` | One of `"arrangement"`, `"sessions"`, `"arrangement+sessions"`. |
| `default_arrangement` | `""` | Saved iTerm2 arrangement to open first; empty means none. |
| `use_it2` | `true` | On demand only: open the arrangement with iTerm2's `it2` utility; `false` prints the menu path instead. |
| `fill_arrangement` | `true` | Put a session back into its recorded pane of the open arrangement when that pane is an idle shell. |
| `include_local` | `true` | Restore local sessions. |
| `include_remote` | `true` | Restore remote sessions. |
| `include` | `[]` | Session-name globs to restore; empty means all. |
| `exclude` | `[]` | Session-name globs not to restore. |
| `remote_hosts` | `[]` | Remote-machine aliases allowed to re-dial; empty means any recorded alias. |
| `max_sessions` | `0` | `0` restores every selected session; `N` restores the N most recently refreshed. |
| `stagger_seconds` | `1.0` | Pause between launches. |
| `confirm` | `false` | Print the plan and ask before acting (never at startup). |

`[iterm2] enabled = false`, the switch for titles, colours and profiles, does not turn off the registry; use `[iterm2.persistence] enabled = false` for that.

## The registry

Path: `$XDG_STATE_HOME/ai-cli-utils/iterm2-sessions.json`, which is `~/.local/state/ai-cli-utils/iterm2-sessions.json` by default (`%LOCALAPPDATA%\ai-cli-utils\` on Windows). The file is mode 0600, replaced atomically (temp file and rename) under a lock, so two launches at once both keep their records. It names your sessions and remote-machine aliases, so it is never committed anywhere, but it holds no host address, user name, key path or secret.

```json
{
  "schema": 1,
  "machine": "<hostname>",
  "sessions": [
    {
      "id": "<uuid4>",
      "kind": "local",
      "name": "c-myproject-1",
      "relaunch_argv": ["ai", "c", "1"],
      "cwd": "/home/user/projects/myproject",
      "remote": null,
      "iterm2": {"session_uuid": "<UUID>", "window": 0, "tab": 3, "pane": 0, "tty": "/dev/ttys012"},
      "launched_at": "2026-01-01T09:00:00Z",
      "refreshed_at": "2026-01-01T10:00:00Z",
      "launcher_pid": 48213,
      "ended_at": null
    },
    {
      "id": "<uuid4>",
      "kind": "remote",
      "name": "c-r-myproject-2",
      "relaunch_argv": ["ai", "c", "-R", "-m", "devbox", "2"],
      "cwd": "/home/user/projects/myproject",
      "remote": {"alias": "devbox", "session": "c-r-myproject-2"},
      "iterm2": null,
      "launched_at": "2026-01-01T09:05:00Z",
      "refreshed_at": "2026-01-01T09:05:00Z",
      "launcher_pid": 48377,
      "ended_at": null
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `kind` | `local` (a tmux session on this machine) or `remote` (an `ai c -R` session). |
| `name` | The tmux session name: local on this machine, or the name the remote host gives it. |
| `relaunch_argv` | The `ai` command that brings this exact session back: the launching command line, with the resolved session name added (or substituted for a non-numeric name) when the command line alone would allocate a new slot. Never an ssh command. |
| `cwd` | The directory the launch ran from. Re-run `relaunch_argv` there, because a launch without `-p` takes its project from the directory. |
| `remote` | For remote sessions: `alias` is the `[remote.machines.<alias>]` name from `config.toml` (empty for a legacy single `[remote]` table), and `session` is the remote tmux session. |
| `iterm2` | The pane: `window`, `tab` and `pane` (0-based) with the iTerm2 `session_uuid`, and the pane's `tty`. `null` when the launch was not inside iTerm2. At launch the position comes from `ITERM_SESSION_ID`; a refresh takes it from iTerm2's window list, front to back. |
| `launched_at`, `refreshed_at` | UTC timestamps of the launch and of the last time the position was confirmed. |
| `launcher_pid` | The launching process. Locally it becomes the tmux client; for a remote session it is the process holding the ssh connection. `null` for a local session recorded by `--adopt`, which has no launcher. |
| `ended_at` | Set when the remote ssh connection ended with exit status 0, which means the remote session was exited on purpose. |

A record is removed only when its session is proved gone:

- **Local:** `tmux has-session` reports the session does not exist. A detached session is still alive and is kept.
- **Remote:** the ssh connection ended with exit status 0 (`ended_at` is set), or `--probe-remote` asked the remote tmux and it has no such session. A hang-up, ssh's own failure code 255, or a reconnect loop that gave up all leave the record in place, because the remote session is most likely still running. A session launched over mosh is only removed by `--probe-remote`.

## `ai iterm2 sessions`

```text
ai iterm2 sessions [-p|--prune [-P|--probe-remote]] [-r|--refresh] [-j|--json]
ai iterm2 sessions -a|--adopt [-d|--dry-run]
```

- With no option, lists the recorded sessions.
- `-p`, `--prune` removes records whose session is proved gone and prints each one with its reason.
- `-P`, `--probe-remote` (with `--prune`) also asks each remote host over ssh whether its tmux session still exists. It is opt-in because each probe dials the host.
- `-r`, `--refresh` (macOS) recomputes every record's window, tab and pane in one AppleScript pass. A local session's pane is the one its tmux client is attached to; a remote session's is the pane of the still-running process that holds its ssh connection. A record whose pane is not found keeps its last known position. If iTerm2 does not answer within 5 seconds, it prints `refresh skipped (iTerm2 did not answer in 5s)`, leaves the registry unchanged and exits 0. On other platforms the refresh is skipped with a note.
- `-j`, `--json` prints the registry as JSON, unredacted (it is your own file), after any prune or refresh. Status lines go to stderr so stdout stays valid JSON.

Prune runs before refresh when both are given.

### Adopting sessions that predate the registry: `--adopt`

A record is written when `ai c` launches a session, so a session that was already running when you installed this version, or one started some other way than `ai c` (a `tmux new-session` by hand, say), has no record and `ai iterm2 restore` cannot bring it back. `ai iterm2 sessions --adopt` records what is live now. Run it once after installing, and again after starting any session outside `ai c`. It only adds records, so re-running it is harmless; it is a separate option rather than part of `--refresh` so that nothing is ever recorded without you asking.

- **Local sessions:** every tmux session named `c-<prefix>-<n>`. Prefixes and slot names can both contain hyphens, so the name is split against your registered project prefixes rather than by position; the record's `relaunch_argv` is `ai c -p <project> <slot>`, after checking that `-p <project>` resolves back to that prefix. A name that no registered prefix splits, or that more than one splits, is skipped with the reason. A `c-r-` session was launched on this machine by another one, and that machine re-attaches it, so it is skipped too. Other tmux sessions are not candidates.
- **Remote sessions:** every running `ai c -R` launcher process, matched on its own command line (the ssh process it starts is not a candidate, and neither is any other ssh client). `relaunch_argv` is that command line, `remote.alias` is its `-m` value or your configured default, and `remote.session` is the remote tmux name, derived the way the launcher derives it, when the command line names both `-p` and a numeric slot. Otherwise `remote.session` is `null` and the line says why: the remote host chose the slot, and re-running the command line opens a new remote session rather than re-attaching the old one. `cwd` is the launcher's working directory when it can be read.
- **Position:** a local session's pane is the one its tmux client is attached to, and a remote session's is the pane of its launcher; both come from one AppleScript pass (macOS). A detached session, or one whose pane iTerm2 does not list, is recorded with `iterm2: null`. If iTerm2 does not answer, sessions are still adopted without positions and `--refresh` fills them in later.

A session that already has a record (same kind and name, or a remote launcher whose own launch was recorded) is left unchanged. A launcher whose command line cannot be read, or that `ai c` itself would not parse, is skipped with its pid and the reason; a command is never guessed. Each candidate gets one line, `adopted`, `already recorded` or `skipped (<reason>)`, and a count line follows:

```text
adopted c-myproject-1  local  window 0 tab 0 pane 0  relaunch: ai c -p myproject 1
adopted c-r-myproject-2  remote on devbox  pid 48377  window 0 tab 1 pane 0  relaunch: ai c 2 -R -p myproject
skipped pid 48410 (argv does not parse as ai c -R (Option '-p' requires an argument.))
2 adopted, 0 already recorded, 1 skipped
```

`-d`, `--dry-run` prints the same plan with `would adopt` and writes and creates nothing. The include and exclude rules of `[iterm2.persistence.tracking]` apply exactly as at launch, and with `enabled = false` there (or in `[iterm2.persistence]`) `--adopt` refuses, naming the key. `--adopt` cannot be combined with `--prune`, `--refresh` or `--json`.

## Restore: `ai iterm2 restore`

```text
ai iterm2 restore [-s|--startup] [-d|--dry-run] [-o|--only local|remote] [-a|--arrangement NAME]
```

Restore brings the recorded sessions back into iTerm2 after a restart. It types each session's `relaunch_argv` (preceded by `cd <cwd> &&`) into a pane, so a local session re-attaches to its tmux session and a remote one re-dials through the usual `ai c -R` reconnect ladder, which reports a dead remote session on its own. Restore is macOS-only; elsewhere it prints `restore is macOS/iTerm2 only` and exits 0.

- `-s`, `--startup` marks the run as iTerm2's startup hook (see below).
- `-d`, `--dry-run` prints the plan and changes nothing: the arrangement line, then each session in launch order with the pane it would go to and the exact command, then every skipped or dead record with its reason. It reads iTerm2's pane list and the process table to make the plan, but types nothing, opens nothing and does not prune the registry.
- `-o`, `--only local|remote` restores one kind only.
- `-a`, `--arrangement NAME` opens this saved arrangement instead of `default_arrangement`.

### What a run does, in order

1. Reads `[iterm2.persistence]` from `iterm2.toml`. A switched-off run names the key that switched it off and exits 0: `restore disabled by [iterm2.persistence] enabled=false`, `... [iterm2.persistence.restore] enabled=false`, or, on demand, `... on_demand=false`. A `--startup` run with `on_startup = false` exits 0 without output.
2. Prunes the registry by the liveness rules above (a dry run only reports what it would remove; with `confirm = true` nothing is removed until you answer yes). Remote hosts are not probed, so a remote session is re-dialled unless it ended cleanly.
3. Selects records: `include_local`/`include_remote`, `--only`, `include`/`exclude`, `remote_hosts`, then `max_sessions` (the most recently refreshed first). Selected sessions launch in their recorded window, tab and pane order, so tabs come back in the order they had.
4. Opens the arrangement, on demand only, when `mode` includes `"arrangement"` and a name is set (`--arrangement` or `default_arrangement`). With `use_it2 = true` it asks iTerm2's bundled `it2` utility for the saved arrangements and restores the named one; a name that is not saved is reported and the run continues with sessions only. When `it2` is absent, raises a permission prompt that is not answered within 15 seconds, or `use_it2 = false`, it prints `arrangement: open it via Window > Restore Window Arrangement > <name>` and continues. On `--startup` the arrangement is not opened again, because iTerm2's own "open default arrangement at startup" preference already opened it.
5. Reads every iTerm2 pane once (one AppleScript pass). At startup an empty answer is retried once a second for up to 15 seconds while iTerm2 finishes opening its windows.
6. Places each session:
   - A session that already has a client (a local session attached in some terminal, or a remote session whose ssh connection is still running) is skipped as `already open`, because re-attaching it would pull it out of the pane showing it.
   - With `fill_arrangement = true`, a session whose recorded pane still exists and is an idle shell is typed into that pane. The pane is found by its iTerm2 session UUID, or, when that UUID is gone (an arrangement restore assigns new ones), by its recorded window, tab and pane, which is the last refreshed position.
   - Every other session opens a new tab in the frontmost window (a new window when there is none).
   - `stagger_seconds` passes between launches.
7. Prints one line per record: `<name>: restored (window W tab T pane P)` or `restored (new tab)`, `<name>: skipped (<rule>)`, or `<name>: dead (<reason>)`.

A pane counts as an idle shell when the only process in its terminal's foreground process group is a shell (`zsh`, `bash`, `fish` and similar), read from one `ps` pass. iTerm2's own `is at shell prompt` needs shell integration and `is processing` is false for a quiet Claude Code session, so neither is used. A pane whose state cannot be read is treated as busy.

If iTerm2 stops answering an AppleScript call (10 seconds), restore stops, prints `stopped: ...; the sessions below stay in the registry` and one `not restored` line for each session it had not yet launched, and exits 1. Restore never edits a record after its prune, so those sessions are still there for the next run. If only the pane list times out, the run continues with new tabs.

### Startup vs on demand

Both are the same command. On demand (no flag) it honours `on_demand`, opens the arrangement itself and, with `confirm = true`, prints the plan and asks before acting. At startup (`--startup`, run by an iTerm2 `AutoLaunch.scpt` that the machine's installer sets up) it honours `on_startup`, leaves the arrangement to iTerm2 and never asks.

### Restore keys

| Key | Effect on `ai iterm2 restore` |
|---|---|
| `enabled` | `false`: every run prints the key and exits 0. |
| `on_startup` | `false`: a `--startup` run exits 0 silently. |
| `on_demand` | `false`: a run without `--startup` prints the key and exits 0. |
| `mode` | `"sessions"` never opens an arrangement; `"arrangement"` opens it and skips every session; `"arrangement+sessions"` does both. |
| `default_arrangement` | The saved arrangement opened on demand; `--arrangement` overrides it. Empty opens none. |
| `use_it2` | `true` opens the arrangement with `it2`; `false` prints the menu path instead. Never used at startup. |
| `fill_arrangement` | `true` types a session into its recorded pane when that pane is an idle shell; `false` always opens a new tab. |
| `include_local`, `include_remote` | `false` skips that kind (`skipped (include_local=false)`). |
| `include`, `exclude` | Session-name globs; a session failing `include` or matching `exclude` is skipped with the glob. |
| `remote_hosts` | Non-empty: a remote session whose alias is not listed is skipped (`remote_hosts does not list '<alias>'`). |
| `max_sessions` | `N > 0`: only the N most recently refreshed sessions are restored; the rest are `skipped (max_sessions=N)`. |
| `stagger_seconds` | Pause between launches. |
| `confirm` | `true`: on demand, print the plan and ask before touching iTerm2. Never asks at startup. |
