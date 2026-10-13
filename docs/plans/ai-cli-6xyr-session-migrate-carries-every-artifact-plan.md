---
title: "ai session-migrate carries every session-keyed artifact"
category: plan
tags: [plan, session-migrate, worktrees, held-docs]
status: draft
source: "None"
template_version: "plan-1.0.0"
task: AI-CLI-6xyr
stub: false
---

<!-- DOC-LINK CONVENTION: link other docs as PLAIN RELATIVE Markdown links [📄 <repo>/<relpath>](<relative-path>)
     with plain link text, NO backticks. Cross-repo links carry a local relative link AND a GitHub URL.
     Never use the vscode://file scheme. -->

<!-- doc:region name="overview" kind="replaceable" -->

# ai session-migrate carries every session-keyed artifact — Implementation Plan

## Founding Ask Coverage

`canonical_root_json`: N/A (no ask-fidelity root was minted for this task). `founding_ask_ref_sha256`: N/A.
The operator's verbatim ask is captured on the owning Beads issue `AI-CLI-6xyr` (its description quotes
the prompt in full); this doc generalises the private session names in it to `session-1` / `session-2`.
Coverage index (non-authoritative):

| Ask entry | Where covered |
|-----------|---------------|
| "migrate ... cc session / worktree / cc tasks / memory / resume message files" | Shipped by #253; parity preserved (T-01 inventory) |
| "any held doc files" | Gap 1 (T-01), gaps 2-3 (T-04, ai-harness side, reached through T-02) |
| "any other relevant artifacts as appropriate" | Gaps 4-6 (T-01, T-03), unmeasured classes (D-5) |
| "update ... migrate command tooling to also migrate any other artifacts ... that aren't migrated autonomously but should be" | Whole plan; D-5 records what is deliberately deferred and why |
| "add whatever commands or modules / code etc." | New modules T-02, T-03; new `held` verb T-04 |

**Status:** DRAFT

**Created:** 2026-10-12

**Task:** AI-CLI-6xyr (this repo); companion ai-harness task for the held-store verb.

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section.
  4. Append AI response as > **AI Response Round N:** below user feedback.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log.
-->

## Table of Contents

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
  - [T-01: Core artifacts the migration still leaves behind (gaps 1, 4, 6)](#t-01-core-artifacts-the-migration-still-leaves-behind-gaps-1-4-6)
  - [T-02: External rekey hooks (the public package reaches private tooling)](#t-02-external-rekey-hooks-the-public-package-reaches-private-tooling)
  - [T-03: Session-named agent worktrees and branches across every repo (gap 5)](#t-03-session-named-agent-worktrees-and-branches-across-every-repo-gap-5)
  - [T-04: held migrate verb in ai-harness (gaps 2 and 3)](#t-04-held-migrate-verb-in-ai-harness-gaps-2-and-3)
- [Batch Plan](#batch-plan)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  - [D-1: How the public package reaches private session-keyed tooling](#d-1)
  - [D-2: Renaming a branch a ref-deletion guard refuses](#d-2)
  - [D-3: Scope of gap 5 (agent worktrees and branches)](#d-3)
  - [D-4: How the held store moves (commit in the pad, ship separately)](#d-4)
  - [D-5: Unmeasured session-keyed classes: carry now or defer](#d-5)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

`ai session-migrate OLD NEW` (shipped in #253) moves a registered session's worktree, canonical
branch, transcript project directory, task namespace, resume tree, launcher state and iTerm2 record.
A live migration on 2026-10-12 measured six classes it left behind (the gap list on `AI-CLI-6xyr`):
held-output replay files in the main tree's memory directory, the git-tracked held store and its
landing-pad worktree (owned by ai-harness), the parked canonical branch when the worktree's HEAD is a
topic branch, session-named agent worktrees and `wt/<session>/*` branches across every repo, and the
emptied task namespace directory. This plan carries each of them, every one listed in `--dry-run`
and moved under the same refuse-if-live guard, and adds the one extension point (D-1) that lets
private, machine-specific tooling join the migration without the public package learning its names.
The migrate-vs-adopt split (two verbs over one planner) is a separate task; everything here is added
to the existing `PlannedMove` planner so that task can share it unchanged.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` in ai-harness is AUTHORITATIVE):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

Every test below drives the real CLI (`ai session-migrate ... -r <tmp repo>`) against a fake slot in
the test suite's isolated HOME / XDG dirs, the way `tests/test_session_migrate.py` already does.
Nothing touches a real session. Every guard added is falsified in the implementation audit: break it,
watch the test go red, restore it.

### T-01: Core artifacts the migration still leaves behind (gaps 1, 4, 6)

**Size:** M
**Batch:** 1

Extends `src/ai_cli/session_migrate.py` in place. Each artifact becomes `PlannedMove` rows in the
existing planner (listed in `--dry-run`, applied after the worktree move), so the adopt/migrate split
task inherits them.

**Existing behaviors (inventory, measured at `36cebb3`):** worktree move; `wt-<old>` rename only
when checked out; session-home marker rewrite; canonical registry; project dir + transcript
retitle/cwd rewrite; task namespace rekey with highwatermark carry; resume tree rekey; session meta,
wrapper, resume prompt, session maps, iTerm2 record; watcher lock left alone; refusals: same name,
bad name, unregistered slot, new slot exists, nested worktrees, live process, live tmux, new project
dir exists. Every one keeps a parity AC (the existing seven tests stay green unchanged).

**Deliverables:**

- Files created: `src/ai_cli/branch_rename.py`
- Files modified: `src/ai_cli/session_migrate.py`, `src/ai_cli/main.py` (`_print_launch_plan`), `README.md`
- Tests added: `tests/test_session_migrate.py` (new cases), `tests/test_branch_rename.py`, `tests/test_main.py` or the existing launch-plan test file (dry-run branch line)

**Acceptance criteria:**

- [ ] AC-1.1 (gap 1) When the main tree's Claude Code project directory or the slot's own project directory holds `memory/.held-output-<old>-NN-<slug>.md` files, the dry run shall list each as `held output: <path> -> <same dir>/.held-output-<new>-NN-<slug>.md` and the real run shall rename exactly the filename prefix, leaving every body byte-identical (replay is byte-for-byte).
- [ ] AC-1.2 (gap 1, failure path) If the destination filename already exists, then the system shall report it as `SKIPPED (target exists)`, leave both files untouched, and continue.
- [ ] AC-1.3 (gap 4) When `wt-<old>` exists but is not the slot's checked-out branch, the dry run shall list `branch: wt-<old> -> wt-<new>` and the real run shall rename it through `branch_rename.rename_branch_anchored`, which first writes `refs/preserved/session-migrate/<old branch>` at the tip and then runs `git branch -m`; after the run the new branch resolves to the old tip and the anchor ref still exists.
- [ ] AC-1.4 (gap 4, parity) While `wt-<old>` is the checked-out branch, the rename shall still happen and the session-home markers shall follow it, exactly as at `36cebb3`.
- [ ] AC-1.5 (gap 4, failure path) If `wt-<new>` already exists, then the system shall leave `wt-<old>` in place and warn, as at `36cebb3`; if `git branch -m` is refused (non-zero), then the warning shall quote git's stderr and the anchor ref shall remain, so the tip stays recoverable.
- [ ] AC-1.6 (gap 4b) When `ai c --dry-run` resolves to an already-registered slot worktree, the plan's `branch` line shall print that worktree's actual checked-out branch with the note `(reused worktree keeps its branch)`; when the slot does not exist it shall keep printing `wt-<name>`.
- [ ] AC-1.7 (gap 6) When the old task namespace directory is left holding only `.highwatermark` and/or `.lock` after the rekey, the system shall write `max(old, new, moved ids)` to the new `.highwatermark` (parity) and remove the two files and the emptied directory; the dry run shall list `task namespace: <old dir> (removed after rekey)`.
- [ ] AC-1.8 (gap 6, failure path) If the old namespace directory holds any other entry after the rekey, then the system shall leave the directory in place and warn naming the entries.
- [ ] AC-1.9 (guard parity) If a Claude Code process or tmux session still hosts the old or new name, then the system shall refuse before any write, including every artifact added by this task (snapshot of HOME, state dir, repo and git config unchanged).

**Dependencies:** None

### T-02: External rekey hooks (the public package reaches private tooling)

**Size:** S
**Batch:** 1

Implements D-1. A drop-in directory of small TOML files declares external commands that
`session-migrate` runs with the old and new names. Private tooling (the ai-harness `held migrate`
verb of T-04, and later Agent Mail / Buzz / issue-tracker rekeys) joins the migration by dropping a
file in, and the public package never names it.

Contract (`docs/reference/session-migrate-hooks.md`, new):

- Directory: `<ai-cli-utils config dir>/session-migrate.d/*.toml` (the config dir is
  `get_xdg_config_home()`, so `~/.config/ai-cli-utils/` on POSIX and `%APPDATA%\ai-cli-utils\` on
  Windows). Each file: `name = "<label>"`, `argv = ["<program>", "<arg>", ...]` (a list, never a shell
  string; `~` in the program path is expanded). Files are read at call time, sorted by filename.
- Invocation: `argv + ["--old", OLD, "--new", NEW]`, plus `--dry-run` during planning. The hook prints
  one line per artifact it would move / moved on stdout; `session-migrate` relays each as
  `hook <name>: <line>`.
- Exit status: during planning, non-zero refuses the whole migration before any write, quoting the
  hook's stderr (so a hook extends refuse-if-live). During the real run, non-zero is a `FAILED`
  warning (overall exit 1); the core moves are not rolled back, because they are already durable and
  a re-run of the hook alone is the repair.
- Order: planning runs every hook with `--dry-run` before the first write; the real run runs them
  after every core move, in the same order.

**Deliverables:**

- Files created: `src/ai_cli/session_migrate_hooks.py`, `docs/reference/session-migrate-hooks.md`, `tests/test_session_migrate_hooks.py`
- Files modified: `src/ai_cli/session_migrate.py` (plan + apply call sites), `README.md` (one paragraph under the session-migrate section)

**Acceptance criteria:**

- [ ] AC-2.1 When `session-migrate.d/` holds a conforming hook file, the dry run shall invoke it with `--old OLD --new NEW --dry-run` and list each stdout line as `hook <name>: <line>`, and nothing else in HOME, the state dir or the repo shall change.
- [ ] AC-2.2 When the real run proceeds, the hook shall be invoked once without `--dry-run`, after the worktree move and every core artifact (observable: the hook's recorded cwd listing shows the new slot present and the old absent).
- [ ] AC-2.3 If a hook exits non-zero during planning, then the system shall refuse the migration, print the hook's stderr, and leave every artifact unchanged.
- [ ] AC-2.4 If a hook exits non-zero during the real run, then the system shall complete the remaining hooks, emit a `FAILED` warning naming the hook, and exit 1.
- [ ] AC-2.5 If a hook file is malformed (missing `name`/`argv`, `argv` not a list of strings), then the system shall refuse during planning naming the file and the field.
- [ ] AC-2.6 While `session-migrate.d/` is absent or empty, the migration shall behave exactly as without this task (parity: the existing tests pass unchanged).

**Dependencies:** None

### T-03: Session-named agent worktrees and branches across every repo (gap 5)

**Size:** L
**Batch:** 2

Implements D-3 in a new module `src/ai_cli/session_migrate_agents.py` with one planner call and one
apply call from `session_migrate.py`. For every git repository directly under the projects dir
(`[project] projects_dir`, default `~/projects`; the slot's own repo included):

- every registered worktree under `<repo>/.worktrees/<old>-agents/` moves to
  `<repo>/.worktrees/<new>-agents/<leaf>` via `git worktree move`; the emptied container is removed;
- every local branch `wt/<old>/<leaf>` is renamed to `wt/<new>/<leaf>` through
  `branch_rename.rename_branch_anchored` (T-01), whether or not it is checked out;
- every `branch.<renamed>.aih-session-home` marker whose value names the old slot is rewritten to the
  new slot (the key follows the branch automatically under `git branch -m`).

A worktree in which a live Claude Code process has its cwd (`live_sessions`) is not moved; it is
listed as `left alone: held by pid <n>` and its branch is not renamed either, so the live process
keeps a consistent view. The migration does not refuse for it (it is not the session being renamed).

**Deliverables:**

- Files created: `src/ai_cli/session_migrate_agents.py`, `tests/test_session_migrate_agents.py`
- Files modified: `src/ai_cli/session_migrate.py` (two call sites), `README.md`

**Acceptance criteria:**

- [ ] AC-3.1 When a second repo under the projects dir holds `.worktrees/<old>-agents/leaf-a` on `wt/<old>/leaf-a` and a parked branch `wt/<old>/leaf-b`, the dry run shall list the worktree move, both branch renames and the marker rewrite, and the real run shall perform them: the worktree is registered at the new path, both branches resolve to their old tips under the new names, each has a `refs/preserved/session-migrate/...` anchor, the markers name the new slot, and the old container directory is gone.
- [ ] AC-3.2 If a live Claude Code process has its cwd inside one agent worktree, then the system shall leave that worktree and its branch in place, list them as held by that pid, and still migrate every other one.
- [ ] AC-3.3 If `git worktree move` or a rename is refused for one entry, then the system shall warn with git's stderr, continue with the rest, and exit 1.
- [ ] AC-3.4 If a destination worktree path or branch already exists, then the entry shall be listed `SKIPPED (target exists)` and left alone.
- [ ] AC-3.5 While a directory under the projects dir is not a git repository or has no `.worktrees/`, it shall be skipped silently (no error, no plan line).
- [ ] AC-3.6 (guard parity) The refuse-if-live check runs before any agent worktree is touched: with a live process in the slot, the second repo is unchanged.

**Dependencies:** T-01 (`branch_rename`)

### T-04: held migrate verb in ai-harness (gaps 2 and 3)

**Size:** M
**Batch:** 1 (ai-harness repo; lands through its own PR)

Adds `held_cli.py migrate --old <slug> --new <slug> [--machine <m>] [--dry-run] [--json]
[--provenance-file]` to ai-harness `scripts/held_cli.py`, implementing D-4. For every store
`held_cli stores` routes to:

1. If the old landing pad `<store>/.worktrees/<m>--<old>-agents/held-<old>` exists on
   `wt/<m>--<old>/held-<old>`: `git worktree move` it to the new pad path and rename its branch to
   `wt/<m>--<new>/held-<new>` (anchor first, D-2). Its unshipped commits ride along. Otherwise create
   the new pad through the existing `_ensure_pad`.
2. In the new pad: `git mv docs/held/<m>--<old>/ docs/held/<m>--<new>/`, rewrite each item's
   `session: <old>@<m>` line to `<new>@<m>` (nothing else in the file changes), `git rm` the old
   compiled `<m>--<old>-open-decisions.md` if present and regenerate the new session's compiled doc
   with the existing `_compile_store`, validate every moved item, commit once with the provenance
   block.
3. Print the exact `held_cli.py ship --session <new>@<m>` command as the follow-up (ship needs the
   forge CLI, which is absent on at least one fleet host).

The dry run prints the same plan with nothing written, and exits non-zero (refusing) when a pad is
dirty, the destination directory already exists, or any item fails validation after the rewrite in a
scratch check. Also add a guard test to `tests/test_ref_delete_guard.py`: with the branch arm
enabled, `git branch -m` of a packed, unintegrated branch is refused, and the same rename succeeds
after `refs/preserved/session-migrate/<branch>` is written at its tip (the premise of D-2).

**Deliverables (ai-harness):**

- Files modified: `scripts/held_cli.py`, `docs/held/README.md` (verb list), `tests/test_held_cli.py`, `tests/test_ref_delete_guard.py`, `install.sh` (drops the hook file of T-02 into `session-migrate.d/held.toml`, idempotent)

**Acceptance criteria:**

- [ ] AC-4.1 When items exist under `docs/held/<m>--<old>/` in two routed stores, `migrate` shall move them in each store's new pad, rewrite only the `session:` line, regenerate the compiled doc, and commit once per store with a validated provenance block; `validate` passes on every moved item.
- [ ] AC-4.2 When the old landing pad exists with an unshipped commit, `migrate` shall move the worktree and rename its branch, and the new pad's history shall contain that commit.
- [ ] AC-4.3 When `--dry-run` is given, `migrate` shall print every planned path and change nothing (store trees, pads and refs byte-identical).
- [ ] AC-4.4 If the new pad is dirty, the destination directory exists, or a rewritten item fails validation, then `migrate` shall refuse (exit 4 or 1 per the existing code table) and change nothing.
- [ ] AC-4.5 If a session is given in the board form or with an `@`, then `migrate` shall exit 2 naming the slug form it expects.
- [ ] AC-4.6 (guard premise) With `aih.refDeleteBranchArm enabled`, `git branch -m` of a packed branch that is neither integrated nor anchored is refused, and succeeds once `refs/preserved/session-migrate/<branch>` points at its tip.
- [ ] AC-4.7 `install.sh` writes `session-migrate.d/held.toml` naming `held_cli.py migrate` with the T-02 argv shape, and a second run leaves it unchanged.

**Dependencies:** T-02 contract (the argv shape), not its code.

## Batch Plan

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01, T-02, T-04 | Core gaps, the hook contract, the ai-harness verb (parallel; T-04 in its own repo) | All batch ACs checked; ai-cli-utils `ruff check`, `ruff format --check`, `pyright src/`, `pytest` green; ai-harness `held_cli` + guard tests green; both PRs merged and content-verified on `origin/main` |
| 2 | T-03 | Agent worktrees and branches across repos | All T-03 ACs checked; same ai-cli-utils gate green; PR merged and content-verified |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Implementation Audit

> **Step 14 gate**: complete before updating docs or presenting UAT. Verify every AC against the
> actual codebase; any unmet AC restarts from implementation. For each guard: break it, watch red.

### T-01

- [x] AC-1.1 … AC-1.9: shipped in #261 (squash `6802902`), each with a behaviour test through
  `ai session-migrate` in `tests/test_session_migrate.py`, `tests/test_branch_rename.py` and
  `tests/test_session_dry_run.py`. One recorded deviation from the AC-1.1 text: for the slot's own
  replay files the dry run prints the destination inside the *new* project directory (where the file
  ends up after the directory moves), not "same dir". Every guard was falsified (anchor write,
  parked-branch rename, reused-branch print, held-output conflict, namespace leftover check).

### T-02

- [x] AC-2.1 … AC-2.6: shipped in #261; `tests/test_session_migrate_hooks.py` covers the dry-run
  plan, the live run after the core moves, the planning refusal, the live `FAILED` path, five
  malformed-file cases and the empty-directory parity. Guards falsified: planning-hook refusal,
  argv type validation, hooks-before-live-check ordering.

### T-03

- [ ] AC-3.1 … AC-3.6

### T-04

- [x] AC-4.1 … AC-4.7: shipped in ai-harness #3138 (squash `2d5f464ff`): `held_cli.py migrate`
  with its tests in `tests/test_held_cli.py`, the guard-premise test in
  `tests/test_ref_delete_guard.py` (a packed, unanchored branch's `git branch -m` is refused until
  `refs/preserved/session-migrate/<branch>` holds its tip) and `install.sh` Step 5q writing
  `session-migrate.d/held.toml` idempotently (`tests/test_install_session_migrate_held_hook.py`).
  Serial gate on the merge base: `223 passed`, `115 passed, 1 skipped`, ruff clean.

**Audit completed:** <!-- YYYY-MM-DD -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| None | — | Operator pre-approved the run end to end ("/delegate fable /orchestrate /auto-flow"); every decision below passed the Authority test and is resolved by the coordinating AI |

## Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | How the public package reaches private session-keyed tooling | (a) drop-in hook directory, (b) hard-coded call to the ai-harness verb, (c) print the command only | (a) | (a) | No | Public package stays generic; hooks extend refuse-if-live; config over code | `Resolved` |
| D-2 | Renaming a branch a ref-deletion guard refuses | (a) anchor under `refs/preserved/session-migrate/` then `git branch -m`, (b) `git branch -c` + guarded delete, (c) leave unrenamed and report | (a) | (a) | No | Only sanctioned by-hand anchor namespace; also the crash-safe shape | `Resolved` |
| D-3 | Scope of gap 5 | (a) move worktrees and rename every `wt/<old>/*` branch, skipping live-held, (b) worktrees only, (c) report only | (a) | (a) | No | Ownership is inferred from the names; a half rename is a wrong owner | `Resolved` |
| D-4 | How the held store moves | (a) commit in the new pad, print the ship command, (b) commit and ship | (a) | (a) | No | `ship` needs a forge CLI absent on a fleet host; a half-ship is worse than a durable pad commit | `Resolved` |
| D-5 | Unmeasured session-keyed classes | (a) carry Agent Mail, Buzz, issue-tracker assignee, ledgers now, (b) defer to a follow-up through the D-1 hook; ledgers never rewritten | (b) | (b) | No | Identity form is mid-change under another owner; ledgers are history | `Resolved` |

### Decision Details

<a id="d-1"></a>

#### D-1: How the public package reaches private session-keyed tooling — ✅ Resolved: (a) drop-in hook directory

**Context.** The held store, its landing pad, Agent Mail and Buzz identities and tracker assignees are
all keyed by the session name, and all live in private tooling (ai-harness). `ai-cli-utils` is a
public package that must not name them. The migration must still list and move them in one run.

##### (a) Drop-in hook directory (`<config>/session-migrate.d/*.toml`)

**Pros:**

- The public package stays generic: it runs "external rekey hooks", nothing more.
- A hook that refuses in dry-run refuses the whole migration, so private artifacts join the
  refuse-if-live guard instead of trailing behind it.
- Config over code: ai-harness `install.sh` drops one file; adding a class later is one more file.
- A TOML `argv` list is OS-portable; no executable bits or shell strings.

**Cons:**

- A hook failure after the core moves cannot be rolled back; the repair is re-running the hook.
- Hooks run with the user's privileges on the user's say-so (a config file), like every tool here.

##### (b) Hard-code a call to the ai-harness verb

**Pros:**

- Nothing to configure.

**Cons:**

- Names a private tool in a public package, and breaks the day that tool moves.
- Every new class means a code change in both repos.

##### (c) Print the exact command for the operator

**Pros:**

- Zero coupling.

**Cons:**

- "Listed in --dry-run and moved" is the acceptance bar; a printed command moves nothing.

##### Recommendation

> **Decision:** ✅ Resolved — (a) drop-in hook directory
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1[1m]; ai-effort=high; ai-profile=architect/coordinator -->
<!-- decision-lineage: decision-id=AI-CLI-6xyr/D-1; decision-topic=public-package-reaches-private-rekey-tooling; governs=module:src/ai_cli/session_migrate_hooks.py; normalized-proposition=session-migrate runs config-declared external hooks in dry-run before any write and live after the core moves; applicability=repo:ai-cli-utils; outcome-id=hooks-shipped; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Chosen for the first two Pros. The rollback Con is mitigated by running every hook in dry-run before
the first write (a hook that cannot do its part refuses the run), by making hooks idempotent by
contract (a re-run after a live failure is the documented repair), and by exiting 1 with a `FAILED`
line so the operator never reads a half-done migration as clean.

---

<a id="d-2"></a>

#### D-2: Renaming a branch a ref-deletion guard refuses — ✅ Resolved: (a) anchor then rename

**Context.** `git branch -m` deletes the old ref, so a `reference-transaction` guard that refuses
destroying an unintegrated branch judges the rename as a deletion. The guard's rename carve-out needs
the old ref to be loose; a packed branch (any repo with hundreds of branches runs `pack-refs`) falls
through to the containment proofs, which pass only when a durable anchor reaches the tip. The guard
names three anchor namespaces, and `refs/preserved/` is the one documented as "what a session writes
by hand". The existing `create-ref` anchors only the checked-out branch, which gap 4 by definition
is not.

##### (a) Write `refs/preserved/session-migrate/<old branch>` at the tip, then `git branch -m`

**Pros:**

- Passes the guard's containment proof through its documented by-hand namespace.
- Generic crash safety: a rename is delete-then-create; the anchor makes an interrupted rename
  recoverable in any repo, guard or no guard.
- No deletion anywhere, so nothing else to prove.

**Cons:**

- Leaves one small ref per renamed branch (hundreds in a large repo).

##### (b) `git branch -c` then delete the old branch

**Pros:**

- No extra ref afterwards.

**Cons:**

- The delete is exactly what the guard refuses; it would need the same anchor anyway.

##### (c) Leave the branch unrenamed and report it

**Pros:**

- Nothing to get wrong.

**Cons:**

- The canonical branch keeps the dead name forever and the lifecycle pass reads a dead owner.

##### Recommendation

> **Decision:** ✅ Resolved — (a) anchor under `refs/preserved/session-migrate/` then `git branch -m`
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1[1m]; ai-effort=high; ai-profile=architect/coordinator -->
<!-- decision-lineage: decision-id=AI-CLI-6xyr/D-2; decision-topic=guarded-branch-rename-path; governs=module:src/ai_cli/branch_rename.py; normalized-proposition=every branch rename the migration performs writes a refs/preserved/session-migrate anchor at the tip first; applicability=repo:ai-cli-utils,ai-harness; outcome-id=anchored-rename; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The leftover-refs Con is accepted: a ref is forty bytes, the namespace is already the fleet's
by-hand anchor, and the anchors are listed in the dry run so their count is visible before the run.
The premise (an anchored packed branch renames under the enabled guard) is pinned by AC-4.6.

---

<a id="d-3"></a>

#### D-3: Scope of gap 5 (agent worktrees and branches) — ✅ Resolved: (a) move and rename all, skipping live-held

**Context.** A session's delegated work lives in `<repo>/.worktrees/<session>-agents/<leaf>` on
`wt/<session>/<leaf>` across every repo it touched (measured: hundreds of branches in one repo, a
dozen worktrees in another). The lifecycle tooling infers the owner from the session segment and
from `aih-session-home` markers; after a rename the old name is a dead owner.

##### (a) Move every agent worktree and rename every `wt/<old>/*` branch, in every repo under the projects dir, skipping any worktree a live process holds

**Pros:**

- Ownership stays correct everywhere the name is read.
- Each entry is independent: one refusal does not stop the rest, and the dry run lists all of them.

**Cons:**

- One command writes to many repos; runtime grows with branch count.
- Hundreds of anchors (D-2) in a large repo.

##### (b) Worktrees and their checked-out branches only

**Pros:**

- Smaller blast radius.

**Cons:**

- Parked branches keep the dead owner; the lifecycle pass then retires in-flight work.

##### (c) Report only

**Pros:**

- No cross-repo writes.

**Cons:**

- The acceptance bar is "moved", and the operator asked for exactly this class.

##### Recommendation

> **Decision:** ✅ Resolved — (a) move and rename all, skipping live-held
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1[1m]; ai-effort=high; ai-profile=architect/coordinator -->
<!-- decision-lineage: decision-id=AI-CLI-6xyr/D-3; decision-topic=gap5-agent-worktree-scope; governs=module:src/ai_cli/session_migrate_agents.py; normalized-proposition=session-migrate moves every agent worktree and renames every wt/<old>/* branch under the projects dir except those a live process holds; applicability=repo:ai-cli-utils; outcome-id=agents-migrated; relation=different-question; related-decision-id=AI-CLI-6xyr/D-2; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The cross-repo Con is mitigated by per-entry independence (every refusal is reported with git's
stderr and the run continues), by the dry run listing every write before it happens, and by the
refuse-if-live guard running first. Runtime is linear in branch count and each step is one git call.

---

<a id="d-4"></a>

#### D-4: How the held store moves — ✅ Resolved: (a) commit in the pad, print the ship command

**Context.** Held items are git-tracked under `docs/held/<host>--<session>/` in every routed store,
written only through the session's landing pad, and landed by `held_cli ship`, which needs the forge
CLI (absent on at least one fleet host).

##### (a) Commit the move in the new session's pad; print the exact `ship` command

**Pros:**

- Durable immediately; `held list --session` already reads the pad, so the view is current.
- Never half-ships; works where the forge CLI is missing.

**Cons:**

- The main tree shows the old directory until the operator ships.

##### (b) Commit and ship in one go

**Pros:**

- One step.

**Cons:**

- Fails on a host without the forge CLI after the commit, which looks like a broken migration.

##### Recommendation

> **Decision:** ✅ Resolved — (a) commit in the pad, print the ship command
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1[1m]; ai-effort=high; ai-profile=architect/coordinator -->
<!-- decision-lineage: decision-id=AI-CLI-6xyr/D-4; decision-topic=held-store-migration-landing; governs=script:ai-harness/scripts/held_cli.py#migrate; normalized-proposition=held migrate commits in the new landing pad and prints the ship command instead of shipping; applicability=repo:ai-harness; outcome-id=held-migrate-verb; relation=different-question; related-decision-id=AI-CLI-6xyr/D-1; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The stale-main-tree Con is mitigated by the printed command (relayed by the hook into the migration's
own output) and by the pad being the authoritative read for the session's items.

---

<a id="d-5"></a>

#### D-5: Unmeasured session-keyed classes: carry now or defer — ✅ Resolved: (b) defer through the hook; ledgers never rewritten

**Context.** Measured on the migrating host on 2026-10-12: Agent Mail `identities.json` holds a key
for both the old and the new name; `agent-mail/sessions/<slug>.*` and
`agent-mail/held/git-inbox-*-<slug>.notice.txt` are slug-keyed; Buzz has a key file and a pubkey
entry for both names (the new session minted its own); the issue tracker has five issues assigned
to the old name; the worktree-lifecycle ledgers do not exist on this host; goal-waker state is keyed
by transcript UUID, not by name. The identity form (`<host>--<slug>` vs `<slug>@<host>`) is being
changed by a separate owner.

##### (a) Carry all of them in this run

**Pros:**

- One migration, nothing left behind.

**Cons:**

- Targets an identity form in flux; a renamed Buzz key collides with the one already minted.
- Needs tracker writes and mail-board registration from a public package's run.

##### (b) Defer to a follow-up ai-harness hook (D-1); ledgers never rewritten

**Pros:**

- The extension point exists after this run; each class lands in its owner's repo, in its final form.
- Append-only history stays history.

**Cons:**

- Until the follow-up lands, a migration lists these classes nowhere.

##### Recommendation

> **Decision:** ✅ Resolved — (b) defer through the hook; ledgers never rewritten
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-fable-5-1[1m]; ai-effort=high; ai-profile=architect/coordinator -->
<!-- decision-lineage: decision-id=AI-CLI-6xyr/D-5; decision-topic=unmeasured-session-keyed-classes; governs=issue:ai-harness-follow-up; normalized-proposition=Agent Mail, Buzz and tracker assignee rekeys land as a later ai-harness hook; lifecycle ledgers are never rewritten; applicability=repo:ai-harness; outcome-id=follow-up-filed; relation=different-question; related-decision-id=AI-CLI-6xyr/D-1; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The Con is mitigated by filing the follow-up in ai-harness before this run closes (its id is recorded
in the Approval Log) and by the migration's output ending with the hook summary, so an operator sees
which hooks ran.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> 3. D-3: <approval or feedback>
> 4. D-4: <approval or feedback>
> 5. D-5: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. Should `ai session-adopt` run the D-1 hooks too once it shares the planner (the migrate/adopt
   split task)? Nothing here prevents it; the hook runner takes old and new names only.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> - <enter feedback here>

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-10-12 | D-1 … D-5 resolved | Coordinating AI (claude, us.anthropic.claude-fable-5-1[1m]) | Authority test passed for each: reversible, inside the operator's pre-approved run, no interface outside the two repos |
| 2026-10-12 | D-5 follow-up filed | Coordinating AI | The deferred classes (Agent Mail state, Buzz identity merge, tracker assignee; ledgers never rewritten) are an ai-harness issue filed the same day and linked from the owning ai-harness task |
| 2026-10-13 | Batch 1 exit gate met | Coordinating AI | #261 (this repo) and ai-harness #3138 merged, verified by content on `origin/main`, main trees synced, the T-01/T-02 child worktree reaped with proof |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

<!-- /doc:region name="approval_log" -->
