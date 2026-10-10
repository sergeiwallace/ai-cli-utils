# iTerm2 session persistence

Quitting iTerm2 does not end an `ai` session. A local session keeps running in its tmux server, and a remote `ai c -R` session keeps running in the remote host's tmux. What a quit loses is the map of which session was in which window, tab and pane. `ai` keeps that map in a small machine-local registry so the sessions can be found and re-attached afterwards.

This page covers the configuration section and the registry. Restoring sessions into iTerm2 is a separate, later step and is not described here yet.

## Configuration

The settings live in `~/.config/ai-cli-utils/iterm2.toml` (on Windows, the same file under the ai-cli-utils config directory), in the `[iterm2.persistence]` tables. The file is read on every `ai` invocation, so an edit takes effect on the next command without restarting anything.

Every key has a built-in default equal to the value below, so a file without these tables behaves exactly as if it contained them. Tracking is on by default because it only writes one owner-only file. Restore is off by default because it opens windows and runs commands, so each machine has to opt in.

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

These keys are read and validated now so a typo is caught early. They take effect once restore ships.

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
| `launcher_pid` | The launching process. Locally it becomes the tmux client; for a remote session it is the process holding the ssh connection. |
| `ended_at` | Set when the remote ssh connection ended with exit status 0, which means the remote session was exited on purpose. |

A record is removed only when its session is proved gone:

- **Local:** `tmux has-session` reports the session does not exist. A detached session is still alive and is kept.
- **Remote:** the ssh connection ended with exit status 0 (`ended_at` is set), or `--probe-remote` asked the remote tmux and it has no such session. A hang-up, ssh's own failure code 255, or a reconnect loop that gave up all leave the record in place, because the remote session is most likely still running. A session launched over mosh is only removed by `--probe-remote`.

## `ai iterm2 sessions`

```text
ai iterm2 sessions [-p|--prune [-P|--probe-remote]] [-r|--refresh] [-j|--json]
```

- With no option, lists the recorded sessions.
- `-p`, `--prune` removes records whose session is proved gone and prints each one with its reason.
- `-P`, `--probe-remote` (with `--prune`) also asks each remote host over ssh whether its tmux session still exists. It is opt-in because each probe dials the host.
- `-r`, `--refresh` (macOS) recomputes every record's window, tab and pane in one AppleScript pass. A local session's pane is the one its tmux client is attached to; a remote session's is the pane of the still-running process that holds its ssh connection. A record whose pane is not found keeps its last known position. If iTerm2 does not answer within 5 seconds, it prints `refresh skipped (iTerm2 did not answer in 5s)`, leaves the registry unchanged and exits 0. On other platforms the refresh is skipped with a note.
- `-j`, `--json` prints the registry as JSON, unredacted (it is your own file), after any prune or refresh. Status lines go to stderr so stdout stays valid JSON.

Prune runs before refresh when both are given.
