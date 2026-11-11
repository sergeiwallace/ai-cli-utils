---
title: migrate-session external rekey hooks
category: reference
tags: [sessions, migrate-session, hooks, configuration]
status: current
source: AI-CLI-6xyr
---

# `ai migrate-session` external rekey hooks

`ai migrate-session OLD NEW` moves everything this package knows is keyed by a
session's name. Other tools can keep their own name-keyed state (a notes store, a
message board identity, a tracker assignee). A hook lets such a tool join the
migration: it is planned in the same dry run, refuses under the same rules, and
runs in the same command, without this package knowing anything about the tool.

## Where hooks live

One TOML file per hook in the `migrate-session.d/` directory under the
ai-cli-utils config directory:

| OS | Directory |
|---|---|
| Linux, macOS | `$XDG_CONFIG_HOME/ai-cli-utils/migrate-session.d/` (default `~/.config/ai-cli-utils/migrate-session.d/`) |
| Windows | `%APPDATA%\ai-cli-utils\migrate-session.d\` |

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
on standard output. `migrate-session` relays each non-blank line as:

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
repeating the original `ai migrate-session` command completes the migration.

## `ai adopt-session`

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
name has changed. `ai audit-sessions` adopts through the same code path.

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

## Resumable completion status

The read-only `--dry-run` invocation may print exactly one machine-readable line:

```text
migrate-status: done
```

The other allowed values are `pending` and `partial`. `done` means all hook-owned
artifacts and any required shipping step are durably complete; the runner skips
that hook's applying invocation. `pending` and `partial` mean applying the same
OLD/NEW pair must finish only remaining work. Invalid or duplicate status lines
refuse the migration. A hook must derive status from its own durable state,
including unfinished publishing, and must be idempotent at every internal write
boundary. A completion stamp alone must not hide remaining source artifacts.

Exit 0 means complete (or a valid read-only plan); exit 3 means partial, repeat the
same command; other nonzero codes describe refusal or failure on stderr. The
runner treats every nonzero applying result as incomplete, continues the other
hooks, and exits 1. This also supports legacy hooks using exit 1 for partial work.
Repeat the original `ai migrate-session` command to finish the pipeline.

Legacy hooks without a status line still receive `--dry-run`; a named warning
says their completion cannot be checked separately, and their idempotent applying
invocation runs again. This package ships no external hook configurations.
Existing configurations remain compatible. Read-only preflights cannot guarantee
that a lease, snapshot, lock or checkout stays usable until execution; the
agent-mail snapshot warning calls out freshness checks explicitly. Hook authors
must recheck those conditions when applying, return nonzero if incomplete, and
never report `done` while a shipping step remains.

## Publishing a re-keyed Beads store

The Beads re-key runs `bd --sandbox`, so the issues it moves stay local to each
store until the store is published. A host names the command that publishes a
store in one file under the same config directory:

| OS | File |
|---|---|
| Linux, macOS | `$XDG_CONFIG_HOME/ai-cli-utils/migrate-session-publish.d/beads.toml` |
| Windows | `%APPDATA%\ai-cli-utils\migrate-session-publish.d\beads.toml` |

```toml
argv = ["python3", "~/bin/publish_store.py", "--repo", "{store}"]
```

`argv` is the program and its arguments, never a shell string. Every `{store}` is
replaced by the store's directory (the directory holding `.beads/`), and the
argv must name it at least once. A leading `~` in the program is expanded, and a
`.py` program runs under the interpreter that runs `ai`, as for hooks. The file is
read when the command runs.

After a run re-keys issues in a store, it runs the command once for that store,
with the store as its working directory, then reads the store again:

| Outcome | Store line | `beads store` part | Exit |
|---|---|---|---|
| Exit 0, no issue left on OLD | `(published: <n> re-keyed)` | `done now` | unchanged |
| Exit 0, an issue still on OLD | `(published: <n> re-keyed)` plus a `NOT converged` warning | `failed: ...` | 1 |
| Non-zero exit, or a timeout | `(publish failed: exit <code>)` plus a `FAILED` warning quoting stderr | `failed: ...` | 1 |
| Malformed file, or no `{store}` | `(publish failed: ...)` naming the file and the field | `failed: ...` | 1 |
| No file | `(publish needed: <n> re-keyed, local until the store is published)` | `skipped: publish needed, no publish command is configured` | unchanged |

A run that exits 1 here writes no `finished` row. The run records each publish
that succeeded in `session-migration-issues.jsonl`, so repeating the same
`ai migrate-session` or `--finish` command publishes again every store an earlier
run re-keyed and no publish has confirmed since, even when no issue is left to
re-key. The command must therefore be safe to repeat.
