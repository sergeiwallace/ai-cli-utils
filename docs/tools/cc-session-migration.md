# Migrating a Claude Code session between project roots (`ai cc-migrate`)

> Move a Claude Code conversation that was started in one directory (typically a
> repo root) into another directory's session store (typically a `.worktrees/<name>`
> worktree), so `ai c <n>` launched there resumes it with full history.

## The problem

Claude Code keys all per-conversation state to the session's **working directory**.
Each transcript is a JSONL file under:

```
~/.claude/projects/<slug>/<session-uuid>.jsonl
```

where `<slug>` is the absolute cwd with every non-alphanumeric character replaced
by `-` (so `/home/user/projects/myproject` → `-home-user-projects-myproject`).
A session run at the repo root and a session run inside that repo's
`.worktrees/myproject-2` worktree therefore live in **two different project
directories**, and neither launch can see the other's history.

`ai c 2` (bare mode) resumes a session like this:

1. compute the worktree path (creating `.worktrees/myproject-2` if needed),
2. scan **that worktree's** project directory for a transcript whose first
   `customTitle` record equals the ai_name (`myproject-2`),
3. `touch` the match and launch `claude --continue` (which picks the most
   recently modified transcript in the project directory).

So if the conversation you want was actually run at the repo root — e.g. a plain
`claude --name myproject-2` after `ai c 2` failed to launch — its transcript sits
in the repo root's project directory where step 2 can never find it. The fix is
to migrate the transcript (and its sidecar data) into the worktree's project
directory, rewriting the recorded working-directory fields on the way.

## Usage

Run from the repo root the session was started in (or pass `-s/--source`):

```bash
# What would happen (recommended first):
ai cc-migrate .worktrees/myproject-2 --title myproject-2 --dry-run

# Do it (move semantics — source transcript is removed after verification):
ai cc-migrate .worktrees/myproject-2 --title myproject-2

# Then resume it:
ai c 2
```

### Options

| Option | Meaning |
|---|---|
| `DEST` (argument) | Destination project root — the worktree directory. Must already exist. |
| `-t/--title NAME` | Select the source session by its `customTitle` (the `--name` it was launched with). Newest match wins, same as resume. |
| `-u/--uuid UUID` | Select by session UUID instead (exact — it is the transcript filename). Wins over `--title` when both are given. |
| `-s/--source DIR` | Project root the session ran in. Default: current directory. |
| `-k/--keep-source` | Copy instead of move — leave the source transcript and sidecar in place. Note both copies then share a UUID; resuming both will fork histories. |
| `-p/--preserve-cwd` | Skip the cwd rewrite (see below). |
| `-d/--dry-run` | Report what would be migrated (file, line count, rewrite count) without writing. |
| `-f/--force` | Overwrite an existing destination transcript with the same UUID. |

## What migration does

1. **Locates the source transcript** in the source root's project directory, by
   `customTitle` (newest-first scan, identical to the resume matcher) or UUID.
2. **Rewrites recorded working directories**: each JSONL record's top-level
   `cwd` and `originalCwd` fields that equal the source root, or live under it,
   are rewritten to the destination root (subpaths keep their suffix — a
   sub-agent record with `cwd=<root>/docs` becomes `<dest>/docs`). Lines that
   don't parse as JSON, and paths outside the source root, are byte-preserved.
   Conversation *content* mentioning old paths is deliberately untouched.
3. **Writes the destination transcript** into the destination project directory
   (created `0700` if absent, matching Claude Code's layout), preserving the
   source file's mtime so `--continue`'s newest-first ordering stays honest.
4. **Verifies** the destination line count matches before touching the source.
5. **Moves the sidecar directory** `<uuid>/` (sub-agent transcripts,
   tool-results) alongside the transcript.
6. **Removes the source** (unless `--keep-source`).

### Why the cwd rewrite matters (and when to skip it)

Claude Code reads the transcript's recorded `cwd` on resume for context (the
session's shell still starts wherever you launched it, but hooks, statuslines,
and the session's own self-description read the recorded value). Migrating a
root session into a worktree without the rewrite leaves the conversation
believing it lives at the root — the exact confusion worktree isolation exists
to prevent. `--preserve-cwd` exists for the forensic case: archiving a
transcript into another directory while keeping it as a faithful record.

### What is deliberately NOT migrated

- **The project `memory/` directory** (auto-memory, `MEMORY.md`): it is shared
  by *all* sessions in the source project directory, including ones still
  running there. Moving it would strand them. If the destination should carry
  the memory too, copy the relevant files manually.
- **UUID-keyed state outside the project dir** (`~/.claude/todos/`,
  `~/.claude/teams/session-<uuid>/`, statusline state): keyed by session UUID,
  which migration does not change — they keep working wherever the transcript
  lives.
- **CC task namespaces** (`~/.claude/tasks/<namespace>/`): a session launched
  without a pinned task list writes tasks under a namespace derived from its
  UUID rather than its name, so `ai c` does not see them after a bare migration.

For the full job — worktree creation, the task-namespace merge, auto-memory, the
duplicate-title gate, and a post-move check that `ai c` really resolves the
result — use `ai session-adopt`, which reuses this module for the transcript
step: [cc-session-adoption.md](cc-session-adoption.md).

## Safety properties

- Selector required — the command never guesses which session you meant.
- Destination must exist; it never creates a worktree for you.
- An existing destination transcript with the same UUID is an error without
  `--force` (that means the session already lives there — migrating over it
  would destroy history).
- The source is deleted only after the destination is written and re-verified;
  any failure leaves the source untouched.
- A title/worktree-name mismatch (migrating `myproject-2` into
  `.worktrees/myproject-7`) is a warning, not an error — resume matches by
  title first, so the transcript may only be picked up via the mtime fallback.

## Verifying a migration

```bash
# 1. Transcript landed:
ls ~/.claude/projects/<dest-slug>/<uuid>.jsonl

# 2. Every line still parses and cwds point at the worktree:
python3 -c "
import json,sys
[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print('ok')" ~/.claude/projects/<dest-slug>/<uuid>.jsonl

# 3. Resume finds it:
ai c <n>       # then check the conversation history is present
```

Implementation: `src/ai_cli/cc_migrate.py` (library, stdlib-only) and the
`cc-migrate` command in `src/ai_cli/main.py`. Tests: `tests/test_cc_migrate.py`.
