---
title: "[BUG-011] Claude Code wipes a task namespace once every task in it is complete, and keys that namespace by the team name — not the transcript"
category: bugs
tags: [claude-code, tasks, namespace, persistence, upstream, task-panel, recovery]
status: diagnosed-upstream
severity: P1
related_docs:
  - tools/ai-cli-usage.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-011] Claude Code wipes a task namespace once every task in it is complete, and keys that namespace by the team name — not the transcript

**Status:** diagnosed-upstream — **not an `ai-cli-utils` defect.** This package's
own behaviour (pinning `CLAUDE_CODE_TASK_LIST_ID` at launch) is confirmed correct
and is in fact the complete mitigation. The defect is Claude Code harness
behaviour plus a false premise in an external orchestration skill.

**Severity:** P1 — an unpinned session silently loses its entire CC task list,
and the documented orphan-recovery procedure does not merely fail, it selects a
*different live session's* namespace and would import that session's work.

**Created:** 2026-08-05

**Task:** AI-CLI-150

## Symptoms

Two symptoms were reported together and assumed to share a cause. They do not.

**(a) Completed tasks appear to be pruned from disk.** A session that finishes
all its tasks leaves a namespace directory containing no `*.json` files at all.

**(b) A namespace's name does not correspond to the session writing it.** The
reporting session (transcript UUID `f5b6cafa-…`, `cwd` = this repo) has a
namespace `~/.claude/tasks/session-f5b6cafa/` that exists but is empty, while its
task writes land in `~/.claude/tasks/session-07190fa3/` — an ID that appears
nowhere in `~/.claude/sessions/<pid>.json`.

Observed scale: of **814** namespace directories, **794** hold zero `*.json`
files and only **20** hold any (136 task files total).

## Root cause

Two independent mechanisms, both in Claude Code (2.1.222), neither in this
package.

### Mechanism 1 — an all-completed namespace is deliberately wiped (symptom a)

The bundled implementation contains a debounced sweeper. Extracted verbatim from
the 2.1.222 binary (minified names preserved):

```js
#y(e){this.#o=null;let t=aV();if(t!==e)return;
  Bne(t).then(async(r)=>{
    if(r.length>0&&r.every((o)=>o.status==="completed"))
      await Fkd(t),this.#e=[],this.#t=!0;
    this.#d()})}
```

It is armed by `setTimeout(this.#y.bind(this,e),PQb)` where `PQb=5000`. So: five
seconds after a task write, if **every** task in the namespace is `completed`,
`Fkd` deletes every non-dotfile `*.json` in the directory.

Crucially `Fkd` first records what it is about to destroy:

```js
async function Fkd(e,t){ … let i=await Bkd(e,t);
  if(i>0){let s=await Y2s(e); if(i>s) await Nkd(e,i)} … }
```

`Bkd` = highest numeric task id present; `Nkd` writes it to `.highwatermark`.
That file is therefore a **deletion ledger**, and it is the probe that
disambiguates "never written" from "written then deleted".

The task schema itself confirms the design: `status` is
`w.enum(["pending","in_progress","completed"])` — there is no `deleted` state.
A `TaskUpdate` to `status: "deleted"` routes to `JPo`, which `unlink`s the file
and likewise bumps `.highwatermark`.

So completion does **not** delete a task file. Completion of the *last open task
in a namespace* deletes **all** of them.

### Mechanism 2 — the namespace key is the team name, not the transcript (symptom b)

Also verbatim from the binary — this is the entire resolver:

```js
function aV(){
  if(te.CLAUDE_CODE_TASK_LIST_ID) return te.CLAUDE_CODE_TASK_LIST_ID;
  let e=bU(); if(e) return e.teamName;
  return Km()||K2s||Lt() }
```

Precedence, highest first:

1. `CLAUDE_CODE_TASK_LIST_ID` if set.
2. **the active team's `teamName`** — when agent-teams is enabled, every session
   implicitly creates a team, whose name is `session-<first 8 hex of the
   process's own freshly-generated session id>`.
3. only then a session/transcript-derived fallback.

This host sets `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = 1` in
`~/.claude/settings.json`, so branch 2 wins for every unpinned session. The
per-process id is **not** the transcript UUID and is regenerated on every
process start, including `--continue` resumes.

### How the two mechanisms compose into the reported failure

`~/.claude/sessions/2923882.json` reports `sessionId: f5b6cafa-…` (the
transcript). `~/.claude/teams/session-07190fa3/config.json` reports
`leadSessionId: 07190fa3-…` for the same process. One process, two IDs; tasks
follow the second.

The reporting transcript `f5b6cafa` (one transcript, `sessionId` never rotates
across all 2590 lines) contains **four** distinct per-process UUIDs across its
lifetime — `f5b6cafa`, `051f5c02`, `75048b0e`, `07190fa3`. Each became a
separate namespace as the process restarted. Three of those four are now empty
directories carrying a `.highwatermark`, i.e. provably wiped:

| namespace | `.lock` created | `.highwatermark` written | value | last `TaskUpdate → completed` in transcript |
|---|---|---|---|---|
| `session-f5b6cafa` | 07-28 17:58:26 | 07-28 20:46:43 | `4` | 20:46:38 |
| `session-051f5c02` | 07-30 16:36:05 | 07-30 17:07:57 | `1` | 17:07:52 |
| `session-75048b0e` | 07-30 21:55:52 | 07-30 22:21:30 | `1` | 21:55:52 → completed 22:21:25 |

Every wipe lands **+5 s** after the transcript's last
`TaskUpdate {status: "completed"}` — matching `PQb=5000` to the second, three
times independently. `session-f5b6cafa`'s `.highwatermark` of `4` matches
exactly the 4 tasks that transcript created before 20:46.

The tasks are not lost *today* only because the current process (`07190fa3`)
created six fresh tasks at 17:31 and they are still open.

## Fix

**No code change in this package.** `ai-cli-utils` already does the one correct
thing: `session_script.py` exports `CLAUDE_CODE_TASK_LIST_ID="$ai_name"` and
`main.py` sets it on the bare path. Against the extracted resolver that is
branch 1 — the highest-precedence branch — so an `ai c <n>` session is immune to
mechanism 2 by construction. The evidence for that is on disk: the only two
namespaces on the examined host with human-readable session names (rather than
hex-derived ones) are also among the few holding a long-lived accumulated task
set (10 and 9 files). The reporting session was **not** launched via `ai c`,
which is exactly why it was exposed.

Recommendations, in priority order:

1. **Correct the external `/task-panel` skill's documented premise.** It states
   the key is "the first 8 hex chars of the transcript UUID (older CC) or the
   full UUID". Both halves are wrong when agent-teams is on: it is the team name,
   built from a per-process id that changes on every restart. That skill lives
   outside this repo (`~/.claude/skills/task-panel/SKILL.md`); it needs an
   owning-repo issue.
2. **Rewrite that skill's recovery steps 3a–3f** (see Verification — they are
   actively unsafe, not merely ineffective). Correct live-namespace resolution
   reads `leadSessionId`/`name` from `~/.claude/teams/*/config.json`, not
   `sessionId` from `~/.claude/sessions/*.json`.
3. **Always pin `CLAUDE_CODE_TASK_LIST_ID`,** including for sessions not started
   by `ai c` — it defeats both mechanisms at once, since a pinned namespace is
   stable across restarts *and* is the namespace the sweeper is evaluated
   against.
4. **File upstream** that an all-completed namespace is destroyed with no
   tombstone beyond a bare integer, so a completed task list cannot be reviewed
   after the fact. Whether this is intended is unknown — see Lesson.

## Verification

- **Positive control (calibrates "working").** 15 `completed` task files are
  present on disk right now across 8 namespaces. Completion alone therefore does
  not delete a file — symptom (a) as originally stated is refuted.
- **Negative control (the discriminating prediction).** If mechanism 1 is real,
  **no** namespace should ever retain an all-completed set. Across all 814
  directories: zero all-completed survivors. Every one of the 15 surviving
  `completed` files sits in a namespace that also holds at least one
  pending/in_progress task, which is precisely the condition
  `r.every(o=>o.status==="completed")` fails on.
- **Probe adequacy for the empty directories.** An empty listing cannot
  distinguish never-written from written-then-deleted, so the `.highwatermark`
  ledger was used instead. Across all 814 dirs the correlation is exact:
  `.highwatermark` exists **iff** the namespace has a gap in its task-id
  sequence, and its value equals the highest missing id — 10/10 cases, no
  counterexamples. Had the files never been written, there would be no
  `.highwatermark` and no id gaps; had they been written elsewhere, the ids would
  be contiguous. The 789 *completely* empty dirs (no `.lock` either) are a
  different, benign population: sessions that started and never created a task.
- **Namespace-key derivation.** For all 20 non-empty namespaces, a
  `session-XXXXXXXX` name has a matching `~/.claude/teams/<same-name>/` directory
  in every case where teams was active, and 802 of 814 namespace names match a
  per-process UUID in `~/.claude/session-env/` (1120 entries). The 12
  non-matching names include both pinned ones — i.e. the exceptions are exactly
  the sessions taking branch 1 of the resolver.
- **`/task-panel` recovery is structurally broken — confirmed by running it.**
  Step 3a, run verbatim, emits the namespaces to *exclude* as
  `session-f5b6cafa`, `session-6e58fe01` (plus full UUIDs). Neither is a live
  namespace. The two actually-live namespaces are `session-07190fa3` (this repo)
  and `session-c00b86c4` (the other repo), and step 3a does not exclude either.
  Step 3b, reproduced verbatim, therefore returns
  `SKILL WOULD RECOVER FROM: session-07190fa3` — a **live** session's own
  namespace. Had a session with no open tasks run it, the same walk would have
  reached `session-c00b86c4` and offered to import the *other* repo's four
  `KG-*` tasks. Only step 3c's subject-plausibility eyeball stands between the
  procedure and cross-session contamination, and it is advisory prose, not a
  check.
- Repo hard gate: `ruff check` and `ruff format --check` clean;
  **2017 passed, 0 failed, 0 skipped.** No source change was needed for the
  diagnosis itself — the four suite failures fixed along the way were
  pre-existing and unrelated (see below).

### Pre-existing failures fixed in passing

The gate did not start clean on this host. All four failures reproduce on
unmodified `origin/main` (verified by stashing this work and re-running), so none
were introduced here, and none relate to task namespaces. Per the
fix-all-failures rule they are fixed rather than annotated:

1. **`test_cli_when_upgrade_then_calls_execvp`** and
   **`test_trigger_update_when_stale_then_runs_upgrade`** — both asserted the
   literal string `"uv"`, but the source resolves uv absolutely via
   `shutil.which`. They passed only where the ambient `PATH` happened to resolve
   uv to a bare name; here uv is at a conda path. Fixed by pinning
   `shutil.which` in the test and asserting the resolved path — the tests now
   verify the command shape rather than the developer's `PATH`.
2. **`test_given_stale_quota_cache_when_run_then_ai_statusline_part_called`** —
   the statusline script's quota block runs only on the subscription branch, and
   the test inherited an ambient `CC_BILLING_MODE=api`, routing the script down
   the cache-hit branch so the quota code never executed. An initial guess that
   this was a sleep race, or the script's 120s lock file, was **refuted** by a
   pristine-`TMPDIR` probe that still showed the refresh suppressed; that probe
   is what surfaced the real cause. Fixed by pinning
   `CC_BILLING_MODE=subscription` in the test's environment.
3. **`test_..._no_private_names_remain`** — a genuine public-repo hygiene
   violation: a private repository name was hardcoded into
   `src/ai_cli/config.py`'s prefix-override map and into four test call sites.
   Fixed by moving the map into configuration — a new `[project_prefixes]` table
   in `config.toml`, read by `config.get_project_prefix_overrides()`. Project
   names are user-specific data, so this is the config-over-code fix as well as
   the hygiene one, and it removes the class of leak rather than renaming one
   instance. The three replacement tests fail if the new function's body is
   replaced with `return {}` (mechanical check verified).

## Lesson

`.highwatermark` is what made this diagnosable, and it was sitting in plain sight
in directory listings that had already been read as "empty". An empty directory
is the classic ambiguous observation — never-written, written-then-deleted, and
written-elsewhere are indistinguishable from the listing alone — and the fix is
not to reason harder about the absence but to find the *side effect* the deleting
code left behind. Deletion paths almost always leave one, because they have to
maintain an invariant (here: never reissue a task id).

The second lesson is about premise inheritance. The recovery procedure was not
merely wrong; it was wrong in a way that *looked* careful — it had an explicit
step for excluding live sessions, and that step read the wrong file. Reading
`sessionId` from `~/.claude/sessions/<pid>.json` looks authoritative and is
authoritative about the transcript; it just has nothing to do with where tasks
go. A safety check derived from an unverified premise is worse than no check,
because it converts "I don't know which namespace is mine" into false confidence.

<!-- /doc:region name="summary" -->
