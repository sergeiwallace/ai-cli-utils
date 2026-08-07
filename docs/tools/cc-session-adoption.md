---
title: Adopting a Claude Code session into a session slot
category: tools
tags: [claude-code, sessions, worktrees, migration]
status: current
source: AI-CLI-168
---

# Adopting a Claude Code session into a session slot (`ai session-adopt`)

> Take a Claude Code conversation that was started **without** `ai c` — a plain
> `claude` in a repo root — and make it fully resumable by `ai c <n>` from the
> right worktree, losing nothing. This document is also the **manual procedure**:
> every step is written so it can be carried out by hand if the command is
> unavailable.

`ai cc-migrate` ([cc-session-migration.md](cc-session-migration.md)) moves the
*transcript* and nothing else. That is the right scope for a transcript move, but
it is not enough to adopt a session: several other pieces of state are keyed by
the **project slug** rather than the session UUID, and those are exactly the ones
that break. Adoption is the whole job.

## What is keyed by what

Claude Code splits per-session state across three keying schemes. Only the first
is a problem, because it changes when the session's working directory changes.

| Keyed by | Paths | Adoption |
|---|---|---|
| **Project slug** (cwd with every non-alphanumeric character replaced by `-`) | `~/.claude/projects/<slug>/<uuid>.jsonl`, `~/.claude/projects/<slug>/<uuid>/` (sidecar), `~/.claude/projects/<slug>/memory/` | **Handled** — transcript + sidecar moved, memory copied |
| **Task namespace** (the ai_name when pinned; otherwise derived from the UUID) | `~/.claude/tasks/<namespace>/<n>.json` | **Handled** — merged into the pinned namespace, renumbering on collision |
| **Session UUID** (unchanged by adoption) | `~/.claude/teams/session-<hex8>/`, `~/.claude/session-env/<uuid>/`, `~/.claude/file-history/<uuid>/`, `~/.claude/state/*-<uuid>*`, `~/.claude/sessions/<pid>.json` | **Deliberately untouched** — the UUID does not change, so these keep working exactly as they did |

### What is intentionally left alone, and why

- **All UUID-keyed state** (the third row above). Adoption never rewrites a
  session UUID, so every path derived from it still resolves. Touching them would
  be pure risk for no benefit.
- **`~/.claude/history.jsonl`.** This is the up-arrow prompt history, and each
  record carries a `project` field holding the **absolute cwd** the prompt was
  typed in — so it is path-keyed, the same class as the project slug. It is
  deliberately not rewritten: it is a single append-only file shared by *every*
  session on the machine, so a rewrite would have to rewrite the whole file to
  fix one session's records, risking every other session's history to restore a
  convenience feature. Consequence to expect: after adoption, up-arrow recall in
  the worktree does not show prompts typed at the old root. Nothing else is
  affected.
- **`~/.claude/sessions/<pid>.json`.** Written by a *running* process and removed
  by it. Adoption refuses to run against a live session, so any record it sees is
  stale and harmless.
- **`~/.claude/shell-snapshots/`, `~/.claude/paste-cache/`.** Content-addressed
  or timestamp-named, not keyed by session or project at all.
- **The source project directory itself.** Left in place even when empty: other
  sessions may still be using that root, and an empty directory costs nothing.

## Before anything is written: three refusals

Each of these corrupts state if discovered halfway through an adoption rather
than up front, so all three are checked before the first write.

1. **A live session is refused.** `~/.claude/sessions/*.json` records the pid of
   each running session, but Claude Code does not reliably remove the file on
   exit — so the file's presence proves nothing and the **pid must be checked**.
   Adoption reads `/proc/<pid>` directly. Do **not** check with
   `ps aux | grep <pattern>`: that pipeline matches the `grep` in its own
   pipeline and has produced a backwards live/dead answer on a real machine.

   The refusal is **precise, not a blanket**: it fires when the live session is
   the one being adopted (matched by name *or* by transcript UUID, so a
   since-renamed session is still caught), or when a session is running in the
   destination worktree. It deliberately does **not** reject every session that
   merely shares the source root — memory is copied rather than moved and each
   transcript is its own file, so a sibling session there is unaffected. That
   distinction matters for diagnosis as much as for permissiveness: a
   root-wide refusal reported "still running" for *every* failure, including an
   unknown title, masking the real cause behind a plausible-looking message.

   Manually:

   ```bash
   for f in ~/.claude/sessions/*.json; do
     python3 -c "
   import json,os,sys
   d=json.load(open(sys.argv[1]))
   pid=d.get('pid',0)
   live=os.path.exists(f'/proc/{pid}')
   print(('LIVE ' if live else 'dead '), pid, d.get('name'), d.get('cwd'))" "$f"
   done
   ```

2. **A duplicate title is an unconditional human gate.** See the next section.

3. **Free space is checked.** Adoption copies the transcript, verifies it, and
   only then removes the source — so it transiently needs room for **two** copies,
   and transcripts reach tens of megabytes. An ENOSPC mid-write truncates the one
   file whose loss cannot be undone. Adoption demands the bytes it plans to write
   plus a 16 MiB margin and fails cleanly otherwise. Manually: `df -h ~/.claude`
   against `du -h` of the transcript.

## The duplicate-title gate

`ai c <n>` resolves a session by scanning the worktree's project directory
newest-first for the first transcript whose **first** `customTitle` record equals
the ai_name. If two transcripts claim the same title, resume is
**nondeterministic** — it silently picks one of two different conversations.

**This gate is always unconditional.** Adoption stops, exits non-zero, writes
nothing, and prints both candidates with enough detail to tell them apart (path,
title, size, line count, recorded cwd, mtime). It never resolves the collision
itself: choosing by size or mtime would silently discard a real conversation.
`-y/--yes` does **not** cover this case, and there is deliberately no
`force`/`auto` mode — the gate *is* the requirement.

The offered remedy is **retitle to a free index**. The proposed index is the
lowest one claimed by neither an existing `customTitle` **nor** an existing
worktree, so it collides with neither: an index whose worktree was cleaned up may
still be claimed by a transcript title (reusing it would recreate the very
collision), and an index whose transcript was deleted may still have a worktree
holding uncommitted work.

Applying the remedy requires the human's answer, supplied on a **second**
invocation:

```bash
# 1. See the candidates and the proposed index. Writes nothing, exits non-zero.
ai session-adopt myproject-2

# 2. Having chosen, confirm it explicitly — by title or by index:
ai session-adopt myproject-2 -c retitle -T myproject-5
ai session-adopt myproject-2 -c retitle -I 5
```

Retitling rewrites **every** `customTitle` record equal to the old title, not
just the first: resume matches the first, but a later record still claiming the
old name would make the file answer to both and reintroduce the ambiguity. The
rewrite goes to a sibling temp file and is `os.replace`d, so an interrupted
retitle leaves the original transcript intact. Afterwards adoption verifies
**both** directions — that `ai c <new>` resolves the retitled transcript, and
that `ai c <original>` still resolves the one that kept the original title.

In bulk mode a collision **pauses on that session, reports it, and continues**
with the rest: the collision is information about one conversation, and aborting
the batch would strand the others for no reason.

## Usage

```bash
# Preview everything, writing nothing (recommended first):
ai session-adopt myproject-2 -n

# Adopt it (prompts for confirmation, showing the plan):
ai session-adopt myproject-2

# Adopt without the prompt (does NOT cover a title collision):
ai session-adopt myproject-2 -y

# Adopt every titled session found in the source root:
ai session-adopt -a -n
ai session-adopt -a -y

# Then resume:
ai c 2
```

### Options

| Option | Meaning |
|---|---|
| `NAME` (argument) | The session's title / ai_name, e.g. `myproject-2`. Omit only with `-a/--all`. |
| `-s/--source DIR` | Project root the session ran in. Default: the repo root. |
| `-r/--repo DIR` | Repo root owning `.worktrees/`. Default: detected from the cwd. |
| `-c/--on-collision {gate,retitle}` | Duplicate-title handling. `gate` (default) stops for a human; `retitle` applies a human-supplied title. |
| `-T/--new-title TITLE` | With `-c retitle`: the confirmed new title. |
| `-I/--new-index N` | With `-c retitle`: the confirmed new index (title derived from the name's prefix). |
| `-N/--task-namespace NS` | Source CC task namespace. Default: derived from the session UUID. |
| `-a/--all` | Bulk mode — every titled session in the source, with per-session skip/continue. |
| `-n/--dry-run` | Show what would happen without writing anything. |
| `-y/--yes` | Skip the confirmation prompt. Never covers a title collision. |

## What adoption does, step by step

Each step is the manual equivalent, in order. Do not reorder them: the
verify-before-delete sequencing is what makes an interrupted run recoverable.

### 1. Ensure the worktree exists

An existing worktree is **reused as-is** and never clobbered — it may hold
uncommitted work. A missing one is created off fresh `origin/main` by the same
machinery `ai c` uses (branch `wt-<ai_name>`, upstream `origin/main`).

```bash
git -C <repo> worktree add <repo>/.worktrees/myproject-2 -b wt-myproject-2 origin/main
git -C <repo> branch --set-upstream-to=origin/main wt-myproject-2
```

### 2. Move the transcript

Delegated to `ai cc-migrate` — see
[cc-session-migration.md](cc-session-migration.md) for the manual procedure. In
summary: the recorded `cwd`/`originalCwd` fields are rewritten from the old root
to the worktree, the sidecar `<uuid>/` directory moves alongside, the destination
is re-parsed line-for-line **before** the source is removed, and the source mtime
is preserved so newest-first resume ordering stays honest.

```bash
ai cc-migrate .worktrees/myproject-2 --title myproject-2
```

### 2b. Clear the stale worktree binding

**Moving the transcript is necessary but not sufficient.** A session that ever
entered a worktree mid-conversation carries a `worktree-state` record holding an
absolute `originalCwd` — for an un-adopted session, the repo root:

```json
{"type":"worktree-state","worktreeSession":{
   "originalCwd":"<repo>","preEnterOriginalCwd":"<repo>",
   "worktreePath":"<repo>/.claude/worktrees/agent-abc123","worktreeBranch":"..."}}
```

Claude Code treats that record as authoritative. On resume it restores the
binding and moves the session into the recorded `worktreePath`; when that
worktree is later left — explicitly, or simply by exiting the session — it
returns the session to `originalCwd` **and renames the transcript into that
directory's project directory**. A transcript's location is a function of the
session's working directory, so the rename carries the file straight back out of
the slot, hours after a successful-looking adoption.

Adoption therefore cannot win a fight over the file's location — Claude Code
writes last. Instead the binding is neutralised: every `worktree-state` record is
rewritten to `worktreeSession: null` (exactly what Claude Code itself writes on a
clean exit, so resume reads it as "no worktree session active" and relocates
nothing), and every `relocated` stamp is repointed at the slot. Conversation
records are untouched, and the referenced worktree is transient anyway.

Rewriting `cwd` fields does *not* cover this: the binding lives inside
`worktreeSession`, which is not a top-level cwd field, so step 2's rewrite never
reached it. Reported as `worktree binding: N stale record(s) cleared`.

Sessions that never entered a worktree carry no such record and are left
byte-identical.

### 3. Merge the CC task namespace

A session launched without a pinned task list writes its tasks under a namespace
derived from its own UUID (`session-<first 8 hex>`, or the full UUID), not under
its name. `ai c` pins the namespace to the ai_name, so the tasks must move to
`~/.claude/tasks/<ai_name>/`.

**Task ids are namespace-scoped small integers**, so the same `1.json` routinely
exists in both namespaces holding entirely unrelated work. A merge must therefore
never overwrite: colliding files are **renumbered** to the next free id, each
moved file's own `id` field is rewritten to match its new filename, and `blocks`
references between moved tasks are remapped through the same mapping so a
renumbered dependency does not end up pointing at a stranger's task. Ids that do
not collide are reserved before replacements are allocated, so renumbering does
not cascade across the whole namespace.

Every renumbering is reported (`task 1 -> 4`).

### 4. Copy auto-memory

**The rule: copy, never move; and never overwrite.**

`~/.claude/projects/<slug>/memory/` is keyed by the **project slug, not by the
session** — every session that ever ran from that directory shares it. `cc-migrate`
refuses to touch it for exactly this reason: **moving it would steal it from other
sessions still running from the source root.** So adoption *copies*, leaving the
source byte-identical and any sibling session unaffected.

Nor does it overwrite: a destination worktree may already carry its own memory,
about the work that actually happened there, and clobbering it would destroy the
only copy. A file already present in the destination is reported as a conflict and
**left alone** for a human to merge:

```
memory: 0 copied, 1 left alone
  kept existing ~/.claude/projects/<dest-slug>/memory/MEMORY.md
```

The cost of this rule is a duplicate: the source root keeps a memory copy that is
now also in the worktree, and the two diverge from here. That is the right
trade — a stale duplicate is recoverable, a deleted or overwritten memory file is
not.

Manually:

```bash
cp -rn ~/.claude/projects/<source-slug>/memory/. \
       ~/.claude/projects/<dest-slug>/memory/     # -n: never clobber
```

### 5. Verify that resume actually resolves it

Adoption runs the **same lookup the launcher performs** — a newest-first scan of
the destination project directory for the first transcript whose first
`customTitle` matches — and reports the file it found:

```
resolve probe: `ai c` finds ~/.claude/projects/<dest-slug>/<uuid>.jsonl
```

If that is not the file adoption just wrote, the command prints
`post-adopt check FAILED` and **exits non-zero**. The probe reports failure
whenever adoption did nothing at all, landed the transcript in the wrong project
directory, or left it under the wrong title — the three ways adoption can appear
to succeed while resume still misses.

Manually:

```bash
python3 -c "
from ai_cli.cc_migrate import cc_project_dir, find_transcript
from pathlib import Path
print(find_transcript(cc_project_dir(Path('<repo>/.worktrees/myproject-2')), title='myproject-2'))"
```

Then the real end-to-end check, which no automated probe can substitute for:
run `ai c 2` and confirm the conversation history is present.

## Idempotence

Re-running an adoption adopts nothing. The second run finds no transcript left in
the source (the first moved it), confirms the destination already resolves, and
reports:

```
myproject-2: already adopted — <path> resolves in <worktree>
```

Nothing is written, and the exit status is zero.

## Implementation

`src/ai_cli/session_adopt.py` (library, stdlib-only apart from creating a
worktree) and the `session-adopt` command in `src/ai_cli/main.py`. Tests:
`tests/test_session_adopt.py`. The transcript move itself is
`src/ai_cli/cc_migrate.py`, reused unchanged.
