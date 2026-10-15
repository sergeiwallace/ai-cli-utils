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
the **project slug** or the session's **name** rather than by the session UUID,
and those are exactly the ones that break. Adoption is the whole job.

## What is keyed by what

Per-session state is split across four keying schemes. Only the last is safe to
ignore: the other three change when the session's working directory or its name
changes, and adoption changes both.

| Keyed by | Paths | Adoption |
|---|---|---|
| **Project slug** (cwd with every non-alphanumeric character replaced by `-`) | `~/.claude/projects/<slug>/<uuid>.jsonl`, `~/.claude/projects/<slug>/<uuid>/` (sidecar), `~/.claude/projects/<slug>/memory/` | **Handled** — transcript + sidecar moved, memory copied |
| **Task namespace** (the ai_name when pinned; otherwise derived from the UUID) | `~/.claude/tasks/<namespace>/<n>.json` | **Handled** — merged into the pinned namespace, renumbering on collision |
| **Session name** | `~/.claude/resume/<subdir>/<name>…` — the resume hand-off tree | **Handled on a retitle** — re-keyed to the new name; see step 4b |
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
- **`~/.claude/sessions/<pid>.json`.** Written by a *running* process, but **not
  reliably removed by it** on kill or crash (see the pid check below). Adoption
  refuses to run against a live session, so any record it sees is stale and
  harmless.
- **`~/.claude/shell-snapshots/`, `~/.claude/paste-cache/`.** Content-addressed
  or timestamp-named, not keyed by session or project at all.
- **`~/.claude/resume/disarmed/<name>`.** A latch, not a record: its presence
  suppresses arming, and it expires with age. Re-keying it would carry a live
  suppression across the rename and could leave the adopted session's
  auto-compact disarmed under its new name. Leaving it behind fails in the safe
  direction — the latch stays under a name nothing derives any more.
- **`~/.claude/resume/wake-ready-<name>-<id>.marker`.** A pending wake owned by
  the context supervisor rather than a record of this session's hand-off, and it
  carries its name in the *middle* of the filename. Left alone deliberately.
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
| `-t/--transcript FILE` | Adopt this transcript file, for a session whose source directory no longer exists, so `-s` has nothing to point at. Refused together with `-s` or `-a`, and refused when the file does not carry NAME as its current title. |
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

The destination must **be** a worktree of that repository, not merely a directory
of the right name, and this is checked rather than assumed (`AI-CLI-200`).
`.worktrees/<name>` carries two incompatible meanings: `ai c <name>` wants that
path to be the session's own checkout, while per-task agent worktrees are nested
*inside* it as `<name>/<task>/<leaf>`. A session started from a repository root —
exactly what this command migrates — therefore finds that container sitting where
its own checkout must go, and adopting into it would rewrite the transcript's
recorded directories to a path holding none of the repository's content.

Adoption refuses in that case, in `-n/--dry-run` too, naming the collision.
Resolve it by hand, since relocating someone's unpushed work is not a decision a
tool should take:

```bash
# check what is actually there
git -C <repo> worktree list --porcelain | grep -Fx "worktree <repo>/.worktrees/myproject-2"

# move each nested worktree to a sibling container, then re-run the adoption
git -C <repo> worktree move <repo>/.worktrees/myproject-2/<task>/<leaf> \
    <repo>/.worktrees/myproject-2-agents/<leaf>
```

Use `git worktree move`, not `mv`: a plain move leaves git's registration pointing
at the old path.

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

### 4b. Re-key the resume hand-off tree

Only on a **retitle** — an adoption that keeps the session's name leaves a tree
that is already keyed correctly.

`~/.claude/resume/` holds the hand-off a session writes for itself so its next
window, the one that comes up after a compaction, starts with its state instead
of nothing. Every artifact is named after the session's **name**, so a retitle
orphaned the whole set: the next window derived the new name, found nothing, and
injected no hand-off at all. One measured rename stranded 19 files across 7
subdirectories.

The tree is *walked* and each filename tested, rather than globbed for a list of
known patterns, because the patterns are not uniform:

| Shape | Subdirectories |
|---|---|
| `<name>.…` | `outbox/`, `logs/`, `history/`, `notices/` (incl. `.delivered.md`) |
| `<name>-<id>.…` | `attempts/`, `injected/`, `confirmed/`, `failed/` |
| `<name>__<id>.…` | `pollers/` |

A fix matching only `<name>-` misses `pollers/` entirely — which is how the first
by-hand pass missed a whole subdirectory. Walking also means a subdirectory added
later is covered without changing the code, and that the hash-keyed
subdirectories (`consumed/`, `sensing/`, `state/`) and the UUID-named logs in
`logs/` are skipped because their names do not match a session name, not because
they are on an exclusion list.

Four rules, each of which is wrong in the other direction:

- **Move, never copy.** `history/<name>.jsonl` is append-only, so a copy leaves
  two partial series that both look authoritative and double-count.
- **Re-key the archival subdirectories too** (`confirmed/`, `failed/`,
  `history/`). The name is a lookup index, not a historical assertion; the
  session's identity never changed, only its title, and an audit trail no reader
  can find has failed its one purpose. The old name survives in the file bodies.
- **A lock is not a record.** Re-keying a stale `pollers/*.lock` hands the new
  name a lock nobody will release, turning a harmless orphan into an active block
  on the mechanism being repaired. The files are zero bytes, so holders are
  probed by open handle: no holder means the lock is **removed**, a live holder
  means it **follows the name**, and a probe that cannot run (Windows, or no
  `lsof`) leaves it **where it is** — not migrated, so it cannot block the new
  name, and not deleted, because nothing proved it dead.
- **Never overwrite.** An existing target belongs to whoever already holds the
  new name and may be its only copy, so it is reported and left alone.

`--dry-run` reports every rename it would make and renames nothing, including the
lock removals.

Manually — glob the tree rather than assuming the shapes, anchor the name at the
*start* of the basename, and never clobber:

```bash
old=oldname; new=newname
for f in ~/.claude/resume/*/"$old"*; do          # anchored: not */*"$old"*
  [ -e "$f" ] || continue
  d=$(dirname "$f"); b=$(basename "$f")
  case "$d" in */disarmed) continue ;; esac      # a latch, not a record
  case "$b" in *.lock)
    [ -n "$(lsof -t -- "$f" 2>/dev/null)" ] || { rm -f "$f"; continue ; } ;;
  esac
  t="$d/$new${b#"$old"}"
  [ -e "$t" ] && { echo "SKIP (exists): $t"; continue ; }
  mv "$f" "$t"
done
ls ~/.claude/resume/*/"$old"* 2>/dev/null        # must print nothing but skips
```

**Check the listing before running it.** Even anchored, this matches a *different*
session whose name merely extends yours — `oldname-generic-dragon` and `oldname-3`
both begin with `oldname`, and both were present in a real tree. The command
distinguishes them by requiring an id or timestamp after the separator; by hand,
eyeball the glob first and drop anything whose trailing text is a name rather than
a hex id or a 10-digit stamp.

**This is a bridge, not machinery to build on.** The key should never have been
the name: a title is mutable, while the session UUID naming the transcript is
stable, so keying the tree by UUID would make a rename free and delete this bug
class. That change spans every producer of a key, so this code is expected to be
**deleted** rather than extended once it lands. Tracked as `AI-CLI-p6tw`.

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

## Migrating a session that already has a slot (`ai session-migrate`)

Adoption brings in a session started **outside** `ai c`. A session that already
lives in a slot and needs a new name (for example `myrepo-3` to
`myrepo-box-3` when the host alias joined the
[naming template](../../README.md#session-names)) is moved with
`ai session-migrate OLD NEW` instead. Only discovery differs: migration reads the
registered slot `.worktrees/OLD`, where adoption searches for an unregistered
transcript. The task namespace and resume tree are moved by the same executor
(`session_adopt.rekey_name_artifacts`) for both.

```bash
ai session-migrate myrepo-3 myrepo-box-3 -n   # every planned move, nothing written
ai session-migrate myrepo-3 myrepo-box-3      # prompts, then migrates
ai session-migrate myrepo-3 myrepo-box-3 -y   # no prompt
ai c myrepo-box-3                             # resume under the new name
```

| What | How it follows the new name |
|---|---|
| Worktree `.worktrees/OLD` | `git worktree move` (uncommitted work included); a nested registered worktree is refused |
| Branch `wt-OLD` | renamed to `wt-NEW` when `wt-NEW` is free, whether it is checked out or parked behind a topic branch (a checked-out topic branch stays checked out); its tip is first anchored at `refs/preserved/session-migrate/wt-OLD`, which stays, so a refused rename never loses it |
| `branch.*.aih-session-home` markers naming the old path | repointed at the new path |
| Canonical-session registry entry | path and session name rewritten, first-registration time kept |
| `~/.claude/projects/<old-slug>/` (transcripts, sidecars, slot memory) | renamed to the new slug; each transcript's top-level cwd fields repointed (history text untouched), `customTitle` OLD rewritten to NEW, stale worktree binding cleared (step 2b) |
| `memory/.held-output-OLD-NN-<slug>.md` replay files in the main tree's and the slot's project directories | renamed to `.held-output-NEW-NN-<slug>.md`, bodies byte-identical; an existing target is skipped |
| `~/.claude/tasks/OLD/` | merged into `tasks/NEW/` (renumbered on collision), `.highwatermark` carried; the old directory is removed when only `.highwatermark`/`.lock` remain, else left with a warning naming what it holds |
| `~/.claude/resume/` artifacts keyed OLD | re-keyed to NEW (step 4b rules) |
| `session-meta-<engine>-OLD.json` | fields rewritten, renamed |
| `sessions/<engine>-OLD.sh` wrapper | regenerated from the rewritten metadata, so `--name` and the pinned `CLAUDE_CODE_TASK_LIST_ID` are NEW |
| `cc-resume-prompt-`, `gg-*-`, `session-agent-exits-<engine>-OLD` | renamed |
| `~/.claude/cc-sessions.json`, `gemini_sessions.json` | key OLD becomes NEW |
| iTerm2 restore registry record | name and relaunch command point at NEW |
| Agent worktrees `<repo>/.worktrees/OLD-agents/<leaf>` and branches `wt/OLD/<leaf>`, in the slot's repo and every repo under `[project] projects_dir` | each worktree moved to `.worktrees/NEW-agents/<leaf>` and the emptied container removed; each branch renamed to `wt/NEW/<leaf>`, checked out or parked, its tip anchored first; markers naming the old slot or a moved worktree rewritten under the new branch name. One a live Claude Code process works in is left with its branch (`held by pid N`); a destination that exists is skipped; a git refusal warns, the rest still move, and the command exits 1 |
| An emptied container `.worktrees/OLD-agents/` (its last worktree reaped before the migration) | removed, with any empty subdirectory; anything still holding a file stays |
| `memory/<words>-OLD.md` and `memory/<words>-OLD-<N>.md` (`active-work-OLD.md`, `.resume-OLD-12.md`) in the main tree's and the slot's project directories | renamed to NEW, bodies byte-identical; a file whose name ends in a longer session's name (`active-work-kg-OLD.md` while `kg-OLD` exists as a task namespace or slot) is left alone; each `MEMORY.md` beside them has those file names and their `[[stem]]` links rewritten and nothing else, and is replaced only if no other session changed it meanwhile |
| `ai-watcher-lock-<engine>-OLD` | removed when the pid it names is not running; kept when that pid runs or the file names none. Never carried to NEW |
| `.worktrees/.OLD.lock` (the launcher's slot lock) | removed when a non-blocking exclusive lock on it succeeds, kept while a process holds it <!-- gitignored-path-ok: names the file the command removes, not evidence --> |
| State of other tools | each hook in `session-migrate.d/` is planned with `--dry-run` (a refusal refuses the migration) and run after the core moves ([hook contract](../reference/session-migrate-hooks.md)) |

`ai session-migrate OLD NEW --finish` completes a migration whose slot already moved
to NEW: a migration run before one of these steps existed, or one whose agent worktree
a live process held. It repeats every step keyed by the old name that is safe to
repeat (agent worktrees, branches, containers and markers; launcher state, maps and
locks; held-output and memory files; the task namespace and resume tree; every hook)
and refuses while `.worktrees/OLD` is still a registered slot or either session is
running. `-n` prints its plan with nothing written.

Left alone deliberately: the rest of the repo-root memory directory (shared by every session
of the repo, and keyed by neither name nor slot), the hashed `session-heartbeats/` and
`session-leases/` entries (recreated by the next launch), and conversation text
that mentions the old path.

Refused before anything is written, `-n` included: a slot that is not a
registered worktree (use `session-adopt`), an existing `.worktrees/NEW` or
project directory for it, a running Claude Code process named OLD/NEW or working
inside the slot, and a tmux session `c-OLD` (its wrapper would restart the agent:
exit the session and end the tmux session first). A destination file that exists
is reported and skipped, never overwritten.

A `.venv` inside the worktree moves with it, but its console scripts name the old
path; the command warns, and `uv sync --reinstall` in the new worktree fixes it.

## Retiring a dead session's slot (`ai session-retire`)

A session home is never reaped automatically: the reaper refuses it by design,
because only its owner knows the session is finished. `ai session-retire NAME`
is that owner's route for `.worktrees/NAME`.

```bash
ai session-retire myrepo-3 -n   # what would be removed and preserved, nothing written
ai session-retire myrepo-3      # prompts, then retires
ai session-retire myrepo-3 -y   # no prompt
```

Refused before anything is written, `-n` included, with every blocking item named:

| Refusal | Why |
|---|---|
| `.worktrees/NAME` is not a registered worktree, or another registered worktree lives inside it | there is no checkout to retire, or removing it would orphan a registration |
| a Claude Code process named NAME or working inside the slot; a tmux session `c-NAME`, `c-r-NAME` or any other engine's variant | the same liveness rule as `session-migrate` |
| a heartbeat in `session-heartbeats/` for one of those tmux sessions, written in this boot within the reaper's `[stale_session_reaper] stale_after_seconds` | the reaper's own freshness rule; an unreadable record or one that cannot be dated counts as fresh |
| an uncommitted change, or an untracked file that is not ignored | removing the checkout would lose it (ignored files are removed with it) |
| a checked-out commit (branch or detached HEAD) in neither the branch's upstream nor `origin/main`, whose content has not landed there either | the commit would survive only on a branch nobody pushed. A squash-merged branch passes on content: every line it added since its merge-base is on the target, was there in a commit since then, or replaced a line the target also rewrote. A binary file passes only when the target's blob is the same, and a branch that adds no line never passes this way |
| an existing `refs/preserved/session-retire/<branch>` at a different commit | it preserves an earlier tip and is never overwritten |

Otherwise, in order:

1. The tips of the checked-out branch and of `wt-NAME` are written to
   `refs/preserved/session-retire/<branch>` (`git update-ref <ref> <tip> ""`, so a
   ref that appeared meanwhile fails the write and nothing is removed).
2. `git worktree remove` without `--force` removes the checkout.
3. The canonical-registry entry for the slot is dropped under the registry's lock.
4. One JSON line is appended to `session-retirements.jsonl` in the XDG state
   directory: `time`, `name`, `repo`, `worktree`, `head`, `branch`, `anchors`
   (ref to tip) and `branch_tips` (branch to tip).

Left in place deliberately: the branches themselves (the anchor plus the branch
is the recovery path: `git worktree add .worktrees/NAME wt-NAME`), and the Claude
Code project directory with its transcripts and memory, whose path the command
prints.

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
`src/ai_cli/cc_migrate.py`, reused unchanged. `ai session-migrate` is
`src/ai_cli/session_migrate.py`, tested in `tests/test_session_migrate.py`.
`ai session-retire` is `src/ai_cli/session_retire.py`, tested in
`tests/test_session_retire.py`.
