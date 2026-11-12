---
title: Adopting a Claude Code session into a session slot
category: tools
tags: [claude-code, sessions, worktrees, migration]
status: current
source: AI-CLI-168
---

# Adopting a Claude Code session into a session slot (`ai adopt-session`)

> Take a Claude Code conversation that was started **without** `ai c` — a plain
> `claude` in a repo root — and make it fully resumable by `ai c <n>` from the
> right worktree, losing nothing. This document is also the **manual procedure**:
> every step is written so it can be carried out by hand if the command is
> unavailable.

`ai migrate-transcript` ([cc-session-migration.md](cc-session-migration.md)) moves the
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
ai adopt-session myproject-2

# 2. Having chosen, confirm it explicitly — by title or by index:
ai adopt-session myproject-2 -c retitle -T myproject-5
ai adopt-session myproject-2 -c retitle -I 5
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
ai adopt-session myproject-2 -n

# Adopt it (prompts for confirmation, showing the plan):
ai adopt-session myproject-2

# Adopt without the prompt (does NOT cover a title collision):
ai adopt-session myproject-2 -y

# Adopt every titled session found in the source root:
ai adopt-session -a -n
ai adopt-session -a -y

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

Delegated to `ai migrate-transcript` — see
[cc-session-migration.md](cc-session-migration.md) for the manual procedure. In
summary: the recorded `cwd`/`originalCwd` fields are rewritten from the old root
to the worktree, the sidecar `<uuid>/` directory moves alongside, the destination
is re-parsed line-for-line **before** the source is removed, and the source mtime
is preserved so newest-first resume ordering stays honest.

```bash
ai migrate-transcript .worktrees/myproject-2 --title myproject-2
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
session** — every session that ever ran from that directory shares it. `migrate-transcript`
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
| `<name>-<id>-<reason>.…` | `evidence/` |
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
  new name and may be its only copy, so it is reported and left alone. The one
  exception is a poller marker, `pollers/<name>__<id>.notified`, when both copies
  hold one integer epoch. The marker only records that the poller's fault was
  announced, so the two copies agree, and the rename keeps the later timestamp
  under the new name and removes the old one (`... merged (keeps the later
  timestamp N)`). A marker holding anything else is reported and left alone.

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
the destination project directory for the first transcript whose current
(last) `customTitle` matches — and reports the file it found:

```
resolve probe: `ai c` finds ~/.claude/projects/<dest-slug>/<uuid>.jsonl
```

If that is not the file adoption just wrote, the command prints
`post-adopt check FAILED` and **exits non-zero**. The probe reports failure
whenever adoption did nothing at all, landed the transcript in the wrong project
directory, or left it under the wrong title — the three ways adoption can appear
to succeed while resume still misses. A transcript whose current `agentName` names
another session is a miss too: Claude Code restores the session's name from its last
`agent-name` record on resume, so the session would come back under that name.
`ai migrate-session` runs the same probe and prints `post-migrate check FAILED`.

Manually:

```bash
python3 -c "
from ai_cli.cc_migrate import cc_project_dir, find_transcript
from pathlib import Path
print(find_transcript(cc_project_dir(Path('<repo>/.worktrees/myproject-2')), title='myproject-2'))"
```

Then the real end-to-end check, which no automated probe can substitute for:
run `ai c 2` and confirm the conversation history is present.

## Migrating a session to a human name (`ai migrate-session --to-name`)

```bash
ai migrate-session myrepo-box-3 --to-name           # reserve the shortest free name, then migrate
ai migrate-session myrepo-box-3 --to-name cleo      # reserve cleo, then migrate
ai migrate-session myrepo-box-3 -t -L 3             # a picked name of at most 3 letters
ai migrate-session --all --to-names                 # list every index-named session here; write nothing
ai migrate-session --all --to-names -y              # migrate each stopped one, refuse each running one
ai migrate-session --all --to-names --over-limit -y # only the names longer than max_name_length
```

Every refusal check of the plain migration runs first, and nothing is reserved when one
fails. The name is then reserved in the name registry together with an `intent` record of
the rename (`migrations/<old title>.json`), the session is migrated exactly as
`ai migrate-session OLD NEW` would, and the record becomes `complete` in the commit that
also releases a human-named old name. `-L/--max-length N` must lie between the configured
limits, because a longer name would fail the next install's check.

A dry run (`-n/--dry-run`) on a registry with no published name list
(`available/generation.json`) still prints its plan. It plans with the name list this
version ships, under the policy `ai sync-names` would publish, says so, and names
`ai sync-names` as the step a real run needs first; it writes nothing. A real run with no
published list refuses until `ai sync-names` publishes one. When the shipped list was
generated under a different policy, the plan says the published names may differ.

A migration that fails after its name is reserved is checked for anything left under the
new title (the worktree, a tmux session, a client record). When nothing is, the name and
its intent record are released together. When something is, the name stays held as
`migration-incomplete` with the survivors listed, `ai reconcile-names` and
`ai release-name` leave it alone, and running the same command again resumes the rename.
A rename that finished but whose completion was never published completes itself at the
next `ai reconcile-names` or `ai migrate-session`.

The host sweep inventories, for one repository, every index-named worktree, tmux session
and resumable record in each client's session store (Claude Code, Codex, Pi, Gemini CLI),
including sessions whose worktree was already retired. Such a transcript-only session is
named by retitling its client records; no worktree is made for it. A record the sweep
cannot read stops it with the record named, so a sweep never reports a host as finished
while something on it was skipped.

## Migrating a session that already has a slot (`ai migrate-session`)

Adoption brings in a session started **outside** `ai c`. A session that already
lives in a slot and needs a new name (for example `myrepo-3` to
`myrepo-box-3` when the host alias joined the
[naming template](../../README.md#session-names)) is moved with
`ai migrate-session OLD NEW` instead. Only discovery differs: migration reads the
registered slot `.worktrees/OLD`, where adoption searches for an unregistered
transcript. The task namespace and resume tree are moved by the same executor
(`session_adopt.rekey_name_artifacts`) for both.

```bash
ai migrate-session myrepo-3 myrepo-box-3 -n   # every planned move, nothing written
ai migrate-session myrepo-3 myrepo-box-3      # prompts, then migrates
ai migrate-session myrepo-3 myrepo-box-3 -y   # no prompt
ai c myrepo-box-3                             # resume under the new name
```

| What | How it follows the new name |
|---|---|
| Worktree `.worktrees/OLD` | `git worktree move` (uncommitted work included); a nested registered worktree is refused |
| Branch `wt-OLD` | renamed to `wt-NEW` when `wt-NEW` is free, whether it is checked out or parked behind a topic branch (a checked-out topic branch stays checked out); its tip is first anchored at `refs/preserved/migrate-session/wt-OLD`, which stays, so a refused rename never loses it |
| `branch.*.app-session-home` markers naming the old path | repointed at the new path |
| Canonical-session registry entry | path and session name rewritten, first-registration time kept |
| `~/.claude/projects/<old-slug>/` (transcripts, sidecars, slot memory) | renamed to the new slug; each transcript's top-level cwd fields repointed (history text untouched), `customTitle` and `agentName` OLD rewritten to NEW, stale worktree binding cleared (step 2b); `--finish` and a re-run find and rewrite a transcript still carrying either |
| `~/.claude/jobs/<first 8 characters of a moved transcript's session id>/state.json` | `name` OLD (or a numbered copy, `OLD (N)`) rewritten to NEW, only when the record's `sessionId` or `resumeSessionId` is that transcript's; Claude Code renames a resumed session to this record's user-set name whatever `--name` the launch passed, so a record left naming OLD brings the session back as OLD. `--finish` and a re-run find and rewrite a record still naming OLD |
| `memory/.held-output-OLD-NN-<slug>.md` replay files in the main tree's and the slot's project directories | renamed to `.held-output-NEW-NN-<slug>.md`, bodies byte-identical; an existing target is skipped |
| `~/.claude/tasks/OLD/` | merged into `tasks/NEW/` (renumbered on collision), `.highwatermark` carried; the old directory is removed when only `.highwatermark`/`.lock` remain, else left with a warning naming what it holds |
| `~/.claude/resume/` artifacts keyed OLD | re-keyed to NEW (step 4b rules) |
| `session-meta-<engine>-OLD.json` | fields rewritten, renamed |
| `sessions/<engine>-OLD.sh` wrapper | regenerated from the rewritten metadata, so `--name` and the pinned `CLAUDE_CODE_TASK_LIST_ID` are NEW |
| `cc-resume-prompt-`, `gg-*-`, `session-agent-exits-<engine>-OLD` | renamed |
| `~/.claude/cc-sessions.json`, `gemini_sessions.json` | key OLD becomes NEW |
| iTerm2 restore registry record | name and relaunch command point at NEW |
| `launch-logs/<name>-<stamp>-<pid>.log` for OLD and each of its tmux names (`c-OLD`, `c-r-OLD`, ...) | renamed for NEW, so NEW's 20-per-name retention prunes them; a log under the slot number alone (`3-<stamp>`) names no session and stays |
| The chief-of-staff registration `ai cos` wrote, when it names OLD (`<chief home>/state/chief-session.json`, and `registry.json`'s `chief_of_staff_session` and `chief_routes.native_agent_name`) | rewritten for NEW, tmux target included; `vp_sessions` is left to its hook. Kept and named, exiting 1, while its tmux target is live; a file that cannot be read refuses the migration |
| Agent worktrees `<repo>/.worktrees/OLD-agents/<leaf>` and branches `wt/OLD/<leaf>`, in the slot's repo and every repo under `[project] projects_dir` | each worktree moved to `.worktrees/NEW-agents/<leaf>` and the emptied container removed; each branch renamed to `wt/NEW/<leaf>`, checked out or parked, its tip anchored first; markers naming the old slot or a moved worktree rewritten under the new branch name. One a live Claude Code process works in is left with its branch (`held by pid N`); a destination that exists is skipped; a git refusal warns, the rest still move, and the command exits 1 |
| An emptied container `.worktrees/OLD-agents/` (its last worktree reaped before the migration) | removed, with any empty subdirectory; one still holding a file stays and is named in the report (`agent container: <path> (kept: holds <entries>)`), and the command exits 1. `-n` names the same fate (`would be removed: empty` or `kept: ...`), counting the agent worktrees the run moves out as gone, and removes nothing |
| A slot `.worktrees/OLD` in another repo under `[project] projects_dir` (a cross-repo worktree the session worked in) | retired with `ai retire-session`'s own checks and steps when it is clean and its commit is contained in, or landed on, its upstream or `origin/main`: tips anchored under `refs/preserved/retire-session/`, branches kept, checkout removed, ledger row written. Otherwise kept and named with the reason (`leftover slot: <path> (kept: <reason>)`), and the command exits 1. A slot with its own Claude Code project directory is a session home and is always kept |
| `memory/<words>-OLD.md` and `memory/<words>-OLD-<N>.md` (`active-work-OLD.md`, `.resume-OLD-12.md`) in the main tree's and the slot's project directories | renamed to NEW, bodies byte-identical; a file whose name ends in a longer session's name (`active-work-kg-OLD.md` while `kg-OLD` exists as a task namespace or slot) is left alone; each `MEMORY.md` beside them has those file names and their `[[stem]]` links rewritten and nothing else, and is replaced only if no other session changed it meanwhile |
| `ai-watcher-lock-<engine>-OLD` | removed when the pid it names is not running; kept when that pid runs or the file names none. Never carried to NEW |
| `.worktrees/.OLD.lock` (the launcher's slot lock) | removed when a non-blocking exclusive lock on it succeeds, kept while a process holds it <!-- gitignored-path-ok: names the file the command removes, not evidence --> |
| Beads issues assigned to OLD, in the slot's repo and every directory under `[project] projects_dir` that has a `.beads/` store | each issue whose assignee is exactly OLD and whose status is not `closed` is re-keyed with `bd --sandbox --actor OLD update <id> --assignee NEW --if-assignee OLD`, then given a comment naming the migration (`beads issue: <id> [OLD] (<store>) -> NEW`). Acting as OLD hands OLD's own in-progress claim over without `--force`, which bd refuses beside `--if-assignee`; `--if-assignee` leaves an issue someone else picked up meanwhile. A similar name (`OLD3`) is never matched. `--sandbox` keeps bd from pushing: each store it changed is named `publish needed`, and publishing it is a separate step. A store bd cannot read (an error or a timeout) is named, never counted as holding none, and the command exits 1. Each completed migration is recorded in `session-migrations.jsonl` in the state directory |
| State of other tools | each hook in `migrate-session.d/` is planned with `--dry-run` (a refusal refuses the migration; under `--finish` it only skips that hook, reported as pending) and run after the core moves ([hook contract](../reference/migrate-session-hooks.md)) |

### Per-session state found left under OLD (AI-CLI-2oes)

Each class an earlier migration left keyed by the old name, where it lives, who owns it,
and what a migration now does with it. The automation framework classes run as `migrate-session.d/` hooks
(`scripts/session_rekey_cli.py <class>`, installed by automation framework's held-hook step); each one
lists what it plans in `--dry-run`, runs again in `--finish`, names a file it cannot read and
exits non-zero, and leaves a live claim in place, named, exiting 1 so `--finish` is re-run once
it is free.

| Class | Location | Key | Owner | Disposition |
|---|---|---|---|---|
| Chief-of-staff registration | `$XDG_STATE_HOME/coordinator/chief-of-staff/<machine key>/state/chief-session.json`, and `registry.json`'s chief fields beside it | `native_agent_name`, `session` (`<name>@<host>`), `tmux_target` | ai-cli-utils (`ai cos`) | **migrate** (core). Left while its tmux target is live |
| Chief registry VP row | the same `registry.json`, `vp_sessions[]` | `vp_id` `<name>@<host>` | automation framework (`chief_of_staff.merge_inventory`) | **migrate** with its enrolment (hook `chief-of-staff`), so the chief does not enrol the session twice. **Keep as history** when NEW already has its own row: the chief ages the old one out as `gone`. Left while the chief that writes the file runs, because it rewrites the file without a lock |
| Worktree determinations | `$XDG_STATE_HOME/automation framework/worktree-lifecycle/index.json` and the per-repo reap ledgers it indexes | `worktree_path` / `candidate_id` inside `.worktrees/OLD` or `.worktrees/OLD-agents` | automation framework (`worktree_determinations.py`) | **keep as history**, each named (hook `worktree-determinations`): a determination judged one path at a proof digest that includes the branch the migration renames, so it cannot carry, and the reconciler asks once at the new path. The ledgers are append-only |
| Work-queue session files | `~/.claude/state/wq/session-<host>_OLD.json`, `heartbeat-<host>_OLD`, `goal-cleared-<host>_OLD` (and `unknown_OLD`, the id used when no machine resolved) | wq id `<host>/<name>` | automation framework (`wq_hooks.py`, `queue_waker.py`) | **remove when proved stale** (hook `work-queue`): the record is rewritten at every start and names a transcript path the migration moved; the heartbeat is a throttle stamp; the goal-cleared mark is a byte offset into a transcript the migration rewrote. Left while the heartbeat is inside the waker's three-window lease |
| Peer-relay cursors | `$XDG_STATE_HOME/automation framework/peer-relay/<project slug>__<session id>.{offset,seen.json}` | project slug of `.worktrees/OLD` or `OLD-agents/<leaf>` | automation framework (`peer_relay_verify.py`) | seen set **migrated** to the new slug (hook `peer-relay`); offset **removed** (it no longer lands on a line once the transcript's cwd fields are rewritten; first sight starts at end of file); both removed when NEW has its own cursor or no transcript remains. Left while the old transcript has not moved |
| Launch logs | `$XDG_STATE_HOME/ai-cli-utils/launch-logs/<name>-<stamp>-<pid>.log` | the name the launch was given | ai-cli-utils (`launch_logging.py`) | **migrate** (core) |
| Resume evidence | `~/.claude/resume/evidence/<key>-<attempt id>-<reason>.txt` | resume key | ai-cli-utils resume rekey (written by automation framework's `EvidenceObserver`) | **migrate** (core): the matcher now accepts a hyphenated suffix after the id |
| Resume attempt records | `~/.claude/resume/attempts/<key>-<id>.json`, body field `key` | resume key, read from the body | automation framework (`context_supervisor.ResumeStore`) | **migrate** (hook `compact-lifecycle`): the core renames the file but not the body, and the supervisor reads the key from the body, so a renamed record was invisible to NEW while the supervisor kept logging `CC-REAUTH-REFUSED key=OLD`; the hook sets `key` to NEW and repoints `evidence_path` at the moved capture |
| Buzz keys `OLD-<2 hex>@<host>.key` | `$XDG_CONFIG_HOME/automation framework/buzz/` | a sub-agent's own identity | automation framework (`buzz_channel.py`) | **keep**, each named (hook `buzz`): a different identity from OLD's, retired by the notifier with its agent; only OLD's own key `OLD@<host>.key` is carried |

`ai migrate-session OLD NEW --finish` completes a migration whose slot already moved
to NEW: a migration run before one of these steps existed, or one whose agent worktree
a live process held. It repeats every step keyed by the old name that is safe to
repeat (agent worktrees, branches, containers and markers; launcher state, maps and
locks; held-output and memory files; the task namespace and resume tree; every hook)
and refuses while `.worktrees/OLD` is still a registered slot or either session is
running; stop a running one with `ai sessions stop NAME` first. A hook that refuses its
`--dry-run` does not stop it: that hook's part reads `failed: ... NOT run ... pending`, the
other parts and hooks still run, and the command exits 1 so it is re-run.

When NEW already holds its own copy of something OLD still holds, NEW's copy is kept,
since it belongs to the session now running under NEW, and OLD's is neither merged
into it nor deleted. It moves into a dated archive under the state directory,
`session-migration-archive/<UTC date>/OLD-to-NEW/<part>/`, never over a file already
archived there, and a ledger row with `"archive_recorded": true` lists each archived
file as `{part, source, archive, kept}`. The part then counts as done, its line reading
`archived OLD, NEW kept`, so a second `--finish` finds nothing left. The core does this
for a resume artifact (a `logs/NEW.log` NEW wrote while `logs/OLD.log` was still
there); a hook does it for its own state and names each file it archived on a
`migrate-archived: <path>` line (`docs/reference/migrate-session-hooks.md`), which the
same ledger row records. `-n` plans each archive as `archive OLD, keep NEW` and moves
nothing. `-n` prints its
plan with nothing written, and runs while either session is live: its first line,
`live session: ...`, names the running session and says a writing run refuses until it
is stopped with `ai sessions stop NAME`. The writing run keeps that refusal and exits 2
before its first write.

`--finish` needs only one name. `ai migrate-session NEW --finish`, the old name,
NEW's first name (`cleo` in `myrepo-box-cleo`) or `ai migrate-session --finish -t
cleo` looks the name up in this host's `session-migrations.jsonl` (in the state
directory), as the OLD or NEW of a recorded pair or the first name of its NEW, and
finishes that pair in the repo the row records, whatever the current directory;
`-r` overrides the recorded repo. A positional name and `-t` together must both
match the same pair. A name matching no recorded pair exits 2 naming the ledger it
searched; one matching several exits 2 and lists `ai migrate-session OLD NEW
--finish -r <repo>` for each. Both names given positionally also take the recorded
repo, and keep the current directory's repo when no row names them.

The migrated session relaunches under the `ai` on `PATH`, not the build running the
migration, so before writing anything every migration (`--finish` and `--to-name`
included; `--beads-only` excepted) asks that `ai` for the migration record format it
reads (`ai internal get-migration-format`). A build from before that probe answers
none, read as 0. When the number is lower than the format this build writes, it runs
`ai update --force` and asks again; when the update fails, cannot run, or still
leaves an older build, or when the probe cannot tell (no `ai` on `PATH`, a timeout),
the command exits 2 naming the build and `ai update --force`, with nothing written.
`-n` names the skew and updates nothing. Each ledger row carries the format it was
written in as `"format"`.

`--finish` also re-keys the Beads issues still assigned to OLD, but only when this
host's `session-migrations.jsonl` (in the state directory) has a row naming
exactly that pair. An assignee carries no host and other machines share the
stores, so without the row the issues are listed as `left alone` with the
reason, and nothing is changed. Session metadata or a registry entry binding NEW
to its slot never names OLD, so neither counts. For a migration run before the
ledger existed, append the row by hand from the migrating session's own record,
one JSON object per line: `{"old": "OLD", "new": "NEW", "repo": "<repo>",
"source": "<where the pair is recorded>"}`, then run `--finish -n` to check the
plan. `--finish` refuses while either session runs; `ai migrate-session OLD NEW
--beads-only` does only this re-key, under the same ledger rule, and runs while
NEW is live, so a migrated session already back at work need not exit for it.

The migration's row lists the session ids of the transcripts it moved
(`"transcripts"`), and a `--finish` on that row appends a
`"transcripts_recorded": true` row for each transcript that no row names yet and
that it retitles or that began (its first timestamped record) before the pair's
first migration row, on a finished pair too. `ai c NEW` resumes a listed transcript ahead of a
more recently active one also titled NEW, such as one a relaunch started before the
migration finished.

Every writing run, a full migration, `--finish` or `--to-name`, ends with a read-only
re-plan of the pair: the `--finish -n` plan, read part by part. A part is done when
it reads `done earlier`, or a skip that finds nothing to carry (`no matching
artifact`, `informational`, `unconfigured`, `missing`, and the `direct rename` of
the name registry); a hook is done only when its `--dry-run` prints
`migrate-status: done`. Anything else, an unreadable store, a hook without a status
line, or a plan that refuses, is a part left. When nothing is left and this host's
ledger records the pair, the run appends a row with `"finished": true` and
`"verified": true` for it. Otherwise it appends nothing, marks each part left
`failed` in the table, warns `NOT finished` naming each one and the `--finish`
command, and exits 1; `--to-name` then marks the name registry entry incomplete. A
`finished` row written before this rule carries no `verified` and counts as
unverified. The ledger is append-only, so the migration's own
row is never rewritten. Every `ai c NEW` launch (and `ai g`/`ai p`/`ai cx`, the
remote side of `-R`, and a detached replay of NEW's recorded recipe by
`ai sessions relaunch`) checks the ledger once the session name is resolved and,
before the worktree or the session exists, judges every pair migrated to NEW once.
A pair with no later `finished` row is finished: the same planner, hooks and Beads
rule as `--finish -y`, verified by that run's own re-plan. That covers a migration
made while NEW was live, which `--finish` itself could never complete. A pair the
ledger records finished, verified or not, is re-planned read-only, so one a later
process dirtied is caught. If any pair is still partial, because its finish refused,
raised or left a part, the launch is refused: it starts nothing, prints one line per
pair naming the parts left and the `ai migrate-session OLD NEW --finish -r REPO`
command, records nothing, and exits 1. `ai sessions relaunch` refuses each such
session the same way, records the refusal as its relaunch reason, still relaunches
the others, and exits 1. A ledger row naming NEW in a migration record format newer
than the running `ai` reads refuses the launch with `ai update --force`.

`-P/--allow-partial-migration` on `ai c` (and `ai g`/`ai p`/`ai cx`, forwarded to
the remote side of `-R`) and on `ai sessions relaunch` starts the session anyway.
It is read only from that invocation's command line, never from config, the
environment or a boot recipe. The launch prints one warning per partial pair naming
the parts left, which also goes into the launch log, and records nothing, so the
next `--status` still lists the pair as partial. It does not override the
newer-format refusal. A dry run (`-d`) finishes and re-plans nothing.

`ai migrate-session -s/--status` lists, without writing anything, every pair this
host's ledger records whose latest row is not a verified `finished`, so every
migration recorded before the re-plan rule is listed. It re-plans each one as
`--finish -n` would and prints one row per pair:

```text
old | new | repo | state | parts left | finish with
myrepo-3 | myrepo-box-3 | /home/user/projects/myrepo | partial | slot lock (would do); memory file (would do) | ai migrate-session myrepo-3 myrepo-box-3 --finish -r /home/user/projects/myrepo
```

A pair reads `complete` when its re-plan finds nothing left, and `partial` with each
part left otherwise; a pair whose repo or `.worktrees/NEW` is gone reads `partial`
with `slot (missing: ...)`, unless the ledger records NEW's own later migration in the
same repo to a slot that exists (followed through each later rename). Such a pair reads
`complete`, its parts column naming the slot it moved on to and the ledger that says so;
its OLD-keyed parts are not re-planned, since there is no NEW slot to plan them in. `-a/--all` re-plans the verified pairs too. A NAME
(`--status NAME`, or OLD and NEW) narrows the listing to the pairs it names, matched
as `--finish` matches one name: the pair's OLD, its NEW or NEW's first name; a NAME
matching no pair exits 2. It runs while NEW is live, since its plan is a `--finish`
dry run, and it writes nothing, the ledger and the hooks' state included (each hook
gets only `--dry-run`). It exits 1 if any listed pair is partial, else 0, and 2 on a
usage error: `-s` takes only NAME, `-a` and `-R`.

`-R/--remote` runs the same `ai migrate-session --status` on the default remote
machine (`[remote]`, or `[remote] default` among `[remote.machines]`) over the
non-interactive ssh route `ai c -R -X` (restart) uses (`BatchMode=yes`, the remote
login shell), and prints its rows and exit code. An unreachable host, or one whose
`ai` exits with any other code, exits 2 saying so.

`ai migrate-session --finish -a/--all` finishes every partial migration this host
records whose sessions are stopped, so a migration recorded before a fix landed, or
one a hook refused before the hook was fixed, needs no hand run. It re-plans every
recorded pair the way `--status -a` does, verified or not, so a pair whose only
`finished` row carries no `verified` is re-planned like an unfinished one. Each
partial pair whose sessions are stopped is finished by the same writing run as
`--finish -y` for that pair, verified-finished rule included, and then re-planned
again for its row. A pair with a live session is skipped, and its row's state names
the session; so is a pair that goes live between the plan and its finish. A pair it
cannot plan (its slot gone, or a plan that raises) reads `skipped: could not plan`.
The installed `ai` is checked once for the whole sweep. Without `-y` it prints the
rows, each partial stopped pair reading `would finish`, and asks; `-n/--dry-run`
prints the same rows and writes nothing. It ends with one row per pair, in the
`--status` columns:

```text
old | new | repo | state | parts left | finish with
myrepo-3 | myrepo-box-3 | /home/user/projects/myrepo | complete | - | -
other-1 | other-box-1 | /home/user/projects/other | skipped: live (tmux session c-other-box-1 hosts other-box-1; ...) | slot lock (would do) | ai migrate-session other-1 other-box-1 --finish -r /home/user/projects/other
```

It exits 0 when every pair reads `complete`, 1 when any is left partial or skipped
(or the prompt is declined), and 2 on a usage error: `-f -a` takes only `-n` and `-y`.

Run with `-r <repo>` on a repo whose `.worktrees/OLD` is a migrated session's
leftover (its branch already starts `wt/NEW/`, it has no Claude Code project
directory, and another repo holds the registered `.worktrees/NEW`), the command,
with or without `--finish`, does only the cleanup: it retires every old-name slot
proved landed and removes every empty `OLD-agents` container, in every repo, and
keeps and names the rest. Because it touches nothing NEW could be using, it runs
while NEW is live in its own slot. It still refuses while a process named OLD runs
or any process works inside that slot; its `--finish -n` plans anyway and names that
process on a `live session:` line.

Left alone deliberately: the rest of the repo-root memory directory (shared by every session
of the repo, and keyed by neither name nor slot), the hashed `session-heartbeats/` and
`session-leases/` entries (recreated by the next launch), and conversation text
that mentions the old path.

Refused before anything is written, `-n` included (a `--finish -n` excepted, as above): a slot that is not a
registered worktree (use `adopt-session`), an existing `.worktrees/NEW` or
project directory for it, a running Claude Code process named OLD/NEW or working
inside the slot, and a tmux session `c-OLD` (its wrapper would restart the agent).
Both refusals name `ai sessions stop OLD`, which stops the session for good: a plain
`/exit` inside a managed session is a restart. A destination file that exists
is reported and skipped, never overwritten.

A `.venv` inside the worktree moves with it, and its console scripts, `pyvenv.cfg`,
`.pth` files and `direct_url.json` name the old path. The command rewrites that path
to the new one in those text files (offline, atomic, and a second run changes
nothing); binary files and a sibling slot whose name merely starts with the old one
are left alone. A reinstall is not used because it fails whenever the project pins
a machine-specific source path. If the rewrite fails, the command warns and
`uv sync --reinstall` in the new worktree is the fallback.

## Retiring a dead session's slot (`ai retire-session`)

A session home is never reaped automatically: the reaper refuses it by design,
because only its owner knows the session is finished. `ai retire-session NAME`
is that owner's route for `.worktrees/NAME`.

```bash
ai retire-session myrepo-3 -n   # what would be removed and preserved, nothing written
ai retire-session myrepo-3      # prompts, then retires
ai retire-session myrepo-3 -y   # no prompt
```

Refused before anything is written, `-n` included, with every blocking item named:

| Refusal | Why |
|---|---|
| `.worktrees/NAME` is not a registered worktree, or another registered worktree lives inside it | there is no checkout to retire, or removing it would orphan a registration |
| a Claude Code process named NAME or working inside the slot; a tmux session `c-NAME`, `c-r-NAME` or any other engine's variant | the same liveness rule as `migrate-session` |
| a heartbeat in `session-heartbeats/` for one of those tmux sessions, written in this boot within the reaper's `[stale_session_reaper] stale_after_seconds` | the reaper's own freshness rule; an unreadable record or one that cannot be dated counts as fresh |
| an uncommitted change, or an untracked file that is not ignored | removing the checkout would lose it (ignored files are removed with it) |
| a checked-out commit (branch or detached HEAD) in neither the branch's upstream nor `origin/main`, whose content has not landed there either | the commit would survive only on a branch nobody pushed. A squash-merged branch passes on content: every line it added since its merge-base is on the target, was there in a commit since then, or replaced a line the target also rewrote. A binary file passes only when the target's blob is the same, and a branch that adds no line never passes this way |
| an existing `refs/preserved/retire-session/<branch>` at a different commit | it preserves an earlier tip and is never overwritten |

Otherwise, in order:

1. The tips of the checked-out branch and of `wt-NAME` are written to
   `refs/preserved/retire-session/<branch>` (`git update-ref <ref> <tip> ""`, so a
   ref that appeared meanwhile fails the write and nothing is removed). When a
   reissued name reuses a branch name whose anchor already preserves an earlier
   holder's tip, that anchor is kept and the new tip goes to
   `refs/preserved/retire-session/<branch>--<first 12 hex of the tip>` beside it.
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
worktree) and the `adopt-session` command in `src/ai_cli/main.py`. Tests:
`tests/test_session_adopt.py`. The transcript move itself is
`src/ai_cli/cc_migrate.py`, reused unchanged. `ai migrate-session` is
`src/ai_cli/session_migrate.py`, tested in `tests/test_session_migrate.py`.
`ai retire-session` is `src/ai_cli/session_retire.py`, tested in
`tests/test_session_retire.py`.

### Repeating the original migration command

Repeat `ai migrate-session OLD -t NAME -y` (or `OLD NEW -y`) after a failed,
interrupted or completed attempt. The name registry's recorded pair is used even
when its naming record is complete: naming completion alone does not prove that
hooks finished. A different requested target is refused. No new reservation is
made for a recorded pair. `-n` validates the requested name, spare ownership,
word policy, worktree and registry rules, and every hook's read-only preflight.
A preview never publishes a naming record.

When OLD has gone, recovery requires the local migration ledger to bind the pair
and repository, or OLD's original branch still checked out at NEW. An unrelated
NEW is refused. Both project directories are refused rather than merged. Recovery
checks and repairs the main branch, ownership and canonical registry, virtualenv
paths, project directory and incomplete transcript rewrites as well as the
old-name artifacts handled by `--finish`. Completed files are left untouched.
The append-only ledger is evidence of intent; it never substitutes for inspecting
current artifacts. If evidence is missing or ambiguous, the command names it and
refuses rather than guessing.

Every result ends with a `part | status` table. Statuses are `done earlier`,
`done now`, `would do`, `skipped: reason` and `failed: reason`. Optional absent
artifacts are skipped; on recovery an absent old-name artifact has no remaining
work. Detailed artifact lines precede the table.

| Exit | Meaning |
|---|---|
| 0 | The migration completed, or the read-only plan passed all available checks |
| 1 | Partial migration; repeat the original command after resolving the named failure |
| 2 | Refused before applying this attempt, or invalid command usage |

Scripts that previously treated a named migration's exit 0 as success must now
handle exit 1 when a hook fails: the naming row remains incomplete until the
original command succeeds. Scripts that distinguished refusals by exit 1 must
accept exit 2. `--finish` remains available, and launches still finish pending
ledger pairs, refusing to start while one stays partial. No migration automatically
publishes changed issue stores.

A hook's time-sensitive checks can expire between planning and execution. The
agent-mail snapshot warning explicitly names this limitation. See the
[resumable hook contract](../reference/migrate-session-hooks.md) for completion
status and legacy-hook limitations.

Recovery also covers interruptions within individual parts:

- Task IDs and dependency remapping are recorded once, before any task copy, in
  the append-only `session-migration-tasks.jsonl` under the same state directory.
  Every remaining source and existing destination is checked against its saved
  hash. A task copied before a stop is verified and its old copy removed; its ID
  is never allocated again. A missing journal with tasks in both namespaces is
  ambiguous and is refused with both paths named. A changed or missing artifact
  is refused rather than overwritten. The journal records intent, not completion.
- A renamed memory file whose index rewrite was interrupted supplies the verified
  new filename for finishing only the stale links in `MEMORY.md`.
- Matching old/new metadata copies finish source removal; differing copies remain
  a conflict. New metadata, task and wrapper files are published atomically.
- A moved agent worktree's old branch and ownership are repaired independently.
  Flat `.worktrees/OLD-<task>` worktrees follow NEW when their branch proves the
  OLD or NEW owner; a matching filename alone never grants ownership.
- The chief registration and its registry fields are checked independently, so
  stopping between those writes does not hide the second write.

Named dry runs never publish offline activations or fetch the name-registry ref;
they inspect its cached snapshot. A missing published generation retains the
existing shipped-list warning. Registry availability and cached policy can change
before a real reservation, whose transaction rechecks current naming rules.
Without `-y`, a named migration asks for confirmation before reserving its name.
The pair intent and task-ID mapping are appended before moving a worktree, so a stop before the move
receipt can also recover a checkout that was using a topic branch. A completed
hook's read-only output is informational and does not count as a new write.

Issue assignee updates and their migration comments are recovered separately.
The append-only `session-migration-issues.jsonl` records the exact store and issue
before its update; retry verifies the current assignee and existing comment before
adding only a missing comment. An unrelated issue already held by NEW is never
included just because its assignee matches. A changed assignee or unreadable store
is named as incomplete. Store publishing remains a separate operation.

Tasks whose source has already gone are left untouched after later task edits;
hash checks protect copies that still have a source to remove. Recorded naming
pairs can prove recovery even if the local pair ledger is lost. The final table
includes the naming registry, and an incomplete dry-run plan returns 1.
Liveness checks include either name's tmux session and processes below either
worktree, including a process with a different title inside NEW.
Recovery streams transcript records and preserves malformed lines verbatim, as
the original transcript rewriters do. A file that cannot be read is a named
preflight refusal with the same parts table; a parser traceback is not completion.
Moved task worktrees are also inspected at their new flat or container paths.
Their ownership and both marker types can finish independently of branch rename.
Virtualenv relocation applies to task worktrees too, including a stop immediately
after their move; scripts that already use the new path are left untouched.
