# Process Hygiene (`ai ps`) — Implementation Plan

**Status:** SUPERSEDED IN PART
**Created:** 2026-04-04

<!-- COMP-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
  with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
  they navigate in-window (incl. VS Code Remote-SSH). `companion toc check` validates this
  once COMP-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
  `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
  (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
  Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
  `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

## Table of Contents

- [Overview](#overview)
- [Process Catalog](#process-catalog)
- [Orphan Detection and Scoring](#orphan-detection-and-scoring)
- [Options](#options)
- [CLI Design](#cli-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

ai-cli manages several long-running background processes (mosh-server, signal-watch, autossh tunnels, circus workers) across two machines. When sessions end abnormally — network drops, mosh client disconnects, CC crashes — server-side processes are left running indefinitely with no owner. Today this requires manual inspection and killing. Five orphaned `mosh-server` processes from March 2026 were found on Mac and killed manually, triggering this feature.

Goal: `ai ps` shows a live inventory of all managed processes with age and status. Scores are advisory. Only the explicit `ai ps clean` command may terminate a process, and only after revalidating the PID and creation time captured during inventory. Background launch and cron paths are non-destructive.

> **Safety supersession (2026-09-10):** The approved score-triggered auto-kill design below was
> withdrawn after it terminated unrelated live transports. Historical feedback is retained as a
> decision record, but any statement authorizing launch-time or cron-driven kills is superseded by
> this note and the corrected T-03 contract. A score, process name, PID, or tmux name is not proof
> of ownership.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - Scope: managed processes only for now. Include mobile-client connections (those are mosh-server processes already in scope).
> - Auto-hygiene: historical score-gated auto-kill request (**superseded by the safety note above**).
> - Remote: cache remote-host process state locally and poll periodically to avoid latency.
> - New module: yes.
>
> **AI Response Round 1:**
> - Scope confirmed: managed processes (mosh-server, signal-watch, autossh, circus, memory/sync watch). Mobile-client connections create mosh-server processes already tracked.
> - Auto-hygiene: historical score-gated auto-kill response (**superseded**); all scores are now advisory.
> - Remote caching: remote-host process state cached at `~/.local/state/ai-cli/remote-ps-cache.json` with 30-minute TTL. Refreshed async in background. `ai ps` reads cache instantly; `ai ps --refresh` forces SSH re-check.
> - New module: `src/ai_cli/process_hygiene.py`.

---

## Process Catalog

Processes tracked by `ai ps`:

| Process | Where | Orphan risk | Notes |
|---------|-------|-------------|-------|
| `mosh-server` | Local + remote host | **High** — client disconnect leaves server running | Main motivator; mobile sessions also create these |
| `signal-watch` | Local + remote host | Medium — tied to tmux session that may be gone | Has PID file at `~/.local/state/ai-cli/signal-watch-<project>.pid` |
| `autossh` tunnels | Mac | Low — managed by `ai tunnel`, rarely stale | 2 persistent tunnels currently |
| `circusd` | Mac | Low — intentionally persistent daemon | Report status only; don't auto-kill |
| `nats-server` | Remote host | Low — intentionally persistent | Report status only; don't kill |
| `ai memory watch` / `ai sync watch` | Mac | Medium — launched per tmux session | Orphaned if parent tmux session is gone |

**Not tracked:** pytest/uv test processes (transient), claude CLI itself (the main process).

## Orphan Detection and Scoring

Each process gets a **staleness score** (0–100). Score determines action:

| Score | Verdict | Auto-behavior |
|-------|---------|---------------|
| ≥ 80 | **orphaned** | Flagged in `ai ps`; eligible for explicit identity-checked cleanup |
| 40–79 | **suspect** | Flagged in `ai ps`; explicit `ai ps clean --force` required |
| < 40 | **active** | No action |

### mosh-server scoring

| Signal | Points |
|--------|--------|
| No `mosh-client` with matching UDP port (`lsof -i UDP`) | +50 |
| Age > 24h | +20 |
| Age > 6h | +10 |
| Matching tmux session unattached for > 2h | +5 |
| No matching tmux session at all | +10 |
| Matching tmux session **currently attached** | -10 |
| `lsof` unavailable and age > 48h | +60 (fallback) |

**Corrected safety note:** Killing a live mosh-server is disruptive and is never safe merely because
the tmux session may persist. Scoring can prioritize operator review, but cannot authorize a signal.

Example: orphaned mosh-server from March with no client, no tmux = 50+20+10+10 = **90 → review**.
Example: disconnected client, no local peer, tmux unattached 8h, age 26h = 50+20+10+5 = **85 → review**.
Example: mosh-server 1h old, client connected, tmux attached = 0-10 = **0 → active**.

### signal-watch scoring

| Signal | Points |
|--------|--------|
| PID in pidfile no longer running | +70 |
| Tmux session it watches no longer exists | +30 |
| Age > 12h with no pidfile update | +20 |

### memory watch / sync watch scoring

| Signal | Points |
|--------|--------|
| Parent tmux session no longer exists | +60 |
| Age > 24h | +20 |

### autossh / circusd / nats-server
Score always < 40 (these are intentionally persistent). `ai ps` reports them as informational only; `ai ps clean` never touches them.

### Remote-host cache

- Cache stored at: `~/.local/state/ai-cli/remote-ps-cache.json`
- TTL: 30 minutes (configurable in `[process_hygiene]` config section)
- Refresh: async background SSH call on first `ai ps` run after TTL expires
- `ai ps --refresh` forces immediate SSH re-check
- Cache includes: pid, name, age_seconds, score, args for each remote process

## Options

### Option A: `ai ps` as standalone subcommand + `process_hygiene.py` module ✓ **Selected**

New `process_hygiene.py` module, wired into `main.py` as `ai ps [clean] [--remote] [--refresh] [--force]`.

**Pros:**
- Discoverable (`ai --help` lists it)
- Consistent with existing `ai tunnel`, `ai signal-watch` pattern
- Clean module boundary; testable in isolation
- Easy to call from session-start hook and cron

**Cons:**
- New module to maintain

### Option B: Integrate into existing commands

Spread across `ai signal-watch status`, `ai tunnel status`, etc.

**Pros:** No new top-level command

**Cons:** Cross-domain processes (mosh + circus + watchers) have no single home; `ai ps clean` has nowhere to live; harder to automate.

### Recommendation

**Option A** selected per feedback.

## CLI Design

```bash
ai ps                    # list all managed processes (local + cached remote host)
ai ps --refresh          # force SSH re-check of remote host (updates cache)
ai ps clean              # show suspect/orphaned, prompt to kill
ai ps clean --force      # kill all orphaned (score ≥ 80) without prompting
```text

**`ai ps` output:**
```text
LOCAL (mac)
  mosh-server   pid=9414   age=27d  score=90  ⚠ orphaned (no client)
  mosh-server   pid=24205  age=0h   score=0   ✓ active (client: artelier)
  signal-watch  pid=33021  age=2h   score=0   ✓ active (project: sw)
  autossh       pid=29914  age=20h  score=0   ✓ active (tunnel: R:9222)
  autossh       pid=62702  age=6d   score=0   ✓ active (tunnel: L:4222)
  circusd       pid=75058  age=5d   score=0   ✓ active

HETZNER (cached 8m ago — run `ai ps --refresh` to update)
  mosh-server   pid=518783 age=0h   score=0   ✓ active (art session)
  nats-server   pid=29579  age=4d   score=0   ✓ active
```text

**`ai ps clean` output:**
```text
Orphaned (score ≥ 80):
  LOCAL  mosh-server  pid=9414   age=27d  score=90  (no active client)
  LOCAL  mosh-server  pid=25170  age=10d  score=85  (no active client)

Suspect (score 40–79):
  LOCAL  sync-watch   pid=44201  age=18h  score=55  (tmux session gone?)

Kill 2 orphan(s)? Suspects require --force to include. [y/N]
```text

**Background hygiene (session start + daily cron):**
- Scores and reports processes without signalling them
- Refreshes stale transport bookkeeping and the remote inventory cache
- Leaves termination exclusively to the explicit `ai ps clean` command after identity revalidation

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `companion validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes each task from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
  gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
  structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
  review against the ACs), never self-declared "done". -->

### T-01: `ProcessInfo` dataclass + scoring engine

**Size:** M
**Batch:** 1

`ProcessInfo(pid, name, age_seconds, score, verdict, detail, machine)` and the scoring functions per process type.

**Deliverables:**
- `src/ai_cli/process_hygiene.py`
- `ProcessInfo` dataclass
- `score_mosh_server()`, `score_signal_watch()`, `score_watcher()` functions
- `collect_local_processes() → list[ProcessInfo]`
- `collect_remote_processes(use_cache=True) → list[ProcessInfo]` with cache read/write

**Acceptance criteria:**
- [ ] Orphaned mosh-server (no client, age > 6h) scores ≥ 80
- [ ] Active mosh-server (client connected) scores < 40
- [ ] `lsof` unavailable falls back to age-based scoring
- [ ] Remote cache read/write works; stale cache (> TTL) triggers async refresh
- [ ] All scoring functions testable with mocked `ps`/`lsof` output

**Dependencies:** None

### T-02: `ai ps` and `ai ps clean` CLI commands

**Size:** S
**Batch:** 1

**Deliverables:**
- `ai ps` subcommand in `main.py` wired to `process_hygiene.py`
- Formatted table output with score + verdict column
- `--refresh`, `--force` flags
- Confirmation prompt distinguishing orphaned vs suspect

**Acceptance criteria:**
- [ ] `ai ps` lists all processes with age, score, verdict
- [ ] Remote-host results shown from cache with cache age
- [ ] `ai ps clean` prompts before killing orphaned; requires `--force` for suspect
- [ ] `ai ps clean --force` kills orphaned + suspect without prompting
- [ ] `ai ps --refresh` forces SSH re-check and updates cache

**Dependencies:** T-01

### T-03: Auto-hygiene integration

**Size:** S
**Batch:** 1

This task's destructive behavior is superseded. Wire non-destructive bookkeeping into session
start and daily cron; never call process termination from either implicit path.

**Deliverables:**
- Keep `ai c` session start free of process-signal authority
- Keep `ai ps cron` non-destructive while refreshing transport bookkeeping and the remote cache
- Retain thresholds for advisory classification only

**Acceptance criteria:**
- [ ] When `ai c` launches, the system shall not signal a process based on its hygiene score
- [ ] When `ai ps cron` runs, the system shall refresh bookkeeping/cache state without signalling a process
- [ ] When explicit cleanup targets a local process, the system shall revalidate its captured PID and creation time immediately before termination
- [ ] If explicit-cleanup identity revalidation fails, then the system shall report the mismatch and preserve the live process

**Dependencies:** T-01, T-02

### T-04: Tests

**Size:** S
**Batch:** 1

**Deliverables:**
- `tests/test_process_hygiene.py`
- Scoring unit tests with mocked `ps`/`lsof` output
- Cache read/write tests
- CLI dispatch tests

**Acceptance criteria:**
- [ ] Scoring functions tested for all verdict boundaries (orphaned/suspect/active)
- [ ] `lsof` unavailable fallback tested
- [ ] Cache TTL expiry tested
- [ ] `ai ps clean --force` tests verify matching process identities are terminated and mismatches survive
- [ ] Remote path mocked (no actual SSH in tests)

**Dependencies:** T-01, T-02, T-03

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03, T-04 | Full feature | Human UAT |

Single batch — all tasks are tightly coupled; no value in shipping detection without clean, or clean without auto.

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Score thresholds, CLI design, auto-hygiene behavior |
| UAT | After Batch 1 | Run `ai ps` live, verify scoring accuracy on real processes |

## Open Questions

1. **Score thresholds:** orphaned at ≥ 80, suspect at 40–79 — do these feel right? Too aggressive or too conservative? Can tune after UAT.

2. **Auto-clean scope (resolved):** implicit auto-kill is prohibited for local and remote launches.

3. **Mobile sessions:** mosh-server processes from mobile clients are tracked like any other mosh-server. If mobile sessions are often left open, the age signal needs tuning because they can legitimately run for days. Is 24h a reasonable threshold for the age signal, or should it be higher (e.g., 7d)?

4. **Cache location:** `~/.local/state/ai-cli/remote-ps-cache.json` — does this fit with existing XDG state dir usage, or should it go elsewhere?

> **Feedback Round 2:**
> 1. Thresholds fine. How would suspect flagging actually surface to the user?
> 2. Unsure.
> 3. Mobile sessions are fleeting — the user closes the client when done. Managed mobile sessions are rare; the user resumes from a desktop. Orphaned mobile mosh-servers with persisting tmux sessions are fine to leave as suspect (manual clean).
> 4. (no feedback)
>
> **AI Response Round 2:**
>
> **1 — Suspect flagging (superseded):** Session start may report advisory scores but cannot terminate processes.
>
> **2 — Remote auto-clean (superseded):** Remote and local launch paths are both non-destructive.
>
> **3 — Client scoring (superseded):** The score remains useful for review but never authorizes launch-time termination.
>
> **4 — Cache location:** `~/.local/state/ai-cli/remote-ps-cache.json` consistent with existing XDG usage. Proceeding as specified.
>
> **Feedback Round 3:**
> 3. Mobile sessions are fleeting — the user closes the client when done. Is there any harm in eventually auto-killing stale mobile mosh-server orphans if they appear abandoned?
>
> **AI Response Round 3:**
>
> **3 — Auto-kill response (superseded):** Dropping a live transport is disruptive even when tmux state persists. The scoring adjustment remains advisory; it cannot prove ownership or authorize a signal.

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-04 | Round 1 approved | Connection-based detection; score-gated auto-clean; remote cache with 30m TTL; new `process_hygiene.py` module; ai-managed scope only |
| 2026-04-04 | Round 2 approved | Suspect flagging: one-line nudge at session start; historical remote auto-clean; mobile-session scoring; cache location confirmed |
| 2026-04-04 | Round 3 approved | Historical score-triggered auto-kill decision; superseded below |
| 2026-09-10 | Safety remediation | Score-triggered launch/cron kills withdrawn; scoring is advisory and termination is explicit plus identity-checked |
