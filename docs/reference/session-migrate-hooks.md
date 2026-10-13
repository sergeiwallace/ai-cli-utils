---
title: session-migrate external rekey hooks
category: reference
tags: [sessions, session-migrate, hooks, configuration]
status: current
source: AI-CLI-6xyr
---

# `ai session-migrate` external rekey hooks

`ai session-migrate OLD NEW` moves everything this package knows is keyed by a
session's name. Other tools can keep their own name-keyed state (a notes store, a
message board identity, a tracker assignee). A hook lets such a tool join the
migration: it is planned in the same dry run, refuses under the same rules, and
runs in the same command, without this package knowing anything about the tool.

## Where hooks live

One TOML file per hook in the `session-migrate.d/` directory under the
ai-cli-utils config directory:

| OS | Directory |
|---|---|
| Linux, macOS | `$XDG_CONFIG_HOME/ai-cli-utils/session-migrate.d/` (default `~/.config/ai-cli-utils/session-migrate.d/`) |
| Windows | `%APPDATA%\ai-cli-utils\session-migrate.d\` |

Files are read when the command runs, never cached, and run in filename order, so
a numeric prefix (`10-notes.toml`, `20-board.toml`) sets the order. Only `*.toml`
files are read. An absent or empty directory means no hooks, and the migration
behaves exactly as it does without this feature.

## File format

```toml
name = "notes"
argv = ["python3", "~/bin/notes_rekey.py", "--store", "~/notes"]
```

| Field | Type | Meaning |
|---|---|---|
| `name` | non-empty string | label used in every output line and error |
| `argv` | non-empty list of strings | the program and its leading arguments, never a shell string |

A leading `~` in the program (the first element) is expanded to the home
directory; later arguments are passed as written. A program whose name ends in
`.py` is run with the Python interpreter that runs `ai`, so the same file works
on Windows, where a `.py` file cannot be executed directly.

A file that is not valid TOML, or whose `name` or `argv` does not match the table,
refuses the migration during planning with the file path and the field named.

## Invocation

Each hook runs with the repository root (the directory holding `.worktrees/`) as
its working directory:

```text
<argv...> --old OLD --new NEW --dry-run   # while planning (every run, -n included)
<argv...> --old OLD --new NEW             # after every core move (real run only)
```

The hook prints one line per artifact it would move (dry run) or moved (real run)
on standard output. `session-migrate` relays each non-blank line as:

```text
hook notes: moved notes/myrepo-3 -> notes/myrepo-box-3
```

## Exit status

| Phase | Exit 0 | Non-zero |
|---|---|---|
| Planning (`--dry-run`) | the hook can do its part | the whole migration is refused before anything is written; the hook's stderr is quoted |
| Real run | done | a `FAILED` warning naming the hook and quoting its stderr; the remaining hooks still run and the command exits 1 |

A program that cannot be started counts as a non-zero exit.

Planning runs every hook before the first write, so a hook that cannot do its part
stops the migration while nothing has moved, and a hook extends the refuse-if-live
guard to its own state. The core moves are not rolled back after a real-run
failure: they are already durable. Hooks must therefore be idempotent, so that
running the failed hook alone (the same argv, without `--dry-run`) completes the
migration.

## `ai session-adopt`

An adoption that retitles the session (`-c retitle` with `-T` or `-I`) changes its
name, so it runs the same hooks under the same contract, with `--old` the title
the session had and `--new` the title it is adopted under:

- every hook is planned with `--dry-run` after the adoption's own refusals (live
  session, duplicate title, free space) and before its first write, and a
  non-zero exit refuses the adoption with nothing moved;
- `-n` lists each planned line as `hook <name>: <line>` and writes nothing;
- the real run invokes each hook once without `--dry-run`, after the transcript,
  tasks, memory and resume hand-off have moved; a non-zero exit is a `FAILED`
  warning and the command exits 1.

An adoption that keeps the session's title runs no hook: nothing keyed by the
name has changed. `ai session-audit` adopts through the same code path.

## Writing a hook

- Accept `--old`, `--new` and an optional `--dry-run`; ignore nothing else
  silently.
- With `--dry-run`, write nothing. Exit non-zero to refuse (for example, the
  state is in use or the destination already exists), with the reason on stderr.
- Without `--dry-run`, perform the move and print what moved. A second run after
  success should find nothing to do and exit 0.
- Never prompt: standard input is not connected to the operator.

Implementation: `src/ai_cli/session_migrate_hooks.py`, called from
`src/ai_cli/session_migrate.py` and `src/ai_cli/session_adopt.py`; tests in
`tests/test_session_migrate_hooks.py` and `tests/test_session_adopt_hooks.py`.
