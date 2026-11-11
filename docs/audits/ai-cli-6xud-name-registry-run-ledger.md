---
title: "AI-CLI-6xud session-name registry run: Run Ledger"
category: audit
tags: [audit, run-ledger, orchestration, name-registry]
status: active
source: "claude-opus-5-5-2026-11-11"
template_version: "audit-1.0.0"
task: AI-CLI-6xud
---

# AI-CLI-6xud session-name registry run: Run Ledger

**Target artifact:** the research doc first, then the design doc for the session-name registry's
storage migration. Paths are recorded per stage below as each artifact is created.

This file is the orchestration cursor and restart contract for the AI-CLI-6xud run. It is not an
audit of either document; each audit stage keeps its own audit document and receipt file.

## Run Ledger

ledger-version: 1

| Run ID | Contract | Decision classes | Push |
|---|---|---|---|
| `ai-cli-6xud-name-registry-2026-11-11` | `auto-flow/1#d-7` | `["commit-without-push", "adn:in-scope"]` | `false` |

### Resolved run plan

| Field | Value |
|---|---|
| Run ID | `ai-cli-6xud-name-registry-2026-11-11` |
| D-12 shape | orchestrated: `orchestrate: true`; this session is the orchestrator and every agent it launches is a leaf |
| Driver model | `us.anthropic.claude-opus-5-5` |
| Selected stages | `research`, `doc`, `doc-audit`, `implement`, `impl-audit` (preset `full`, source: inference from an issue target) |
| Model family | `mixed` (omitted) |
| Route: research | legwork: claude / opus / n/a researcher leaves, read-only; final authorship: this orchestrator (source: orchestrator authorship override, Claude-API profile) |
| Route: doc | authorship: this orchestrator (source: orchestrator authorship override) |
| Route: doc-audit | codex / audit / high (source: default, Claude-API profile effort) |
| Route: implement | claude / opus / n/a, `engineer` persona (source: default, Claude-API profile) |
| Route: impl-audit | codex / audit / high (source: default, Claude-API profile effort) |
| Route: AD-N decider | claude / opus / n/a for items a Claude round raises; codex in-loop fix pass otherwise (source: default) |
| Max concurrent dispatched descendants | 3 |
| Decisions | `resolve_decisions: true` (source: default) |
| Doc approval | `require_doc_approval: false` (source: default) |
| Integration mode | `parent` |
| Founding ask | Tier 3. Record `docs/asks/AI-CLI-6xud.md` in the operator's private harness repository, merged there as `9bbe49e048b`; `founding_ask_ref_sha256` `6b5df6dc0d9aef7bd45832af903ad5d8f4439221b051da5e1969a4f4d92dd9fb`; content sha256 `c68b87eddcba35b45a9a82b768a42dacd14f4c6b8cf2826318db02829b490646`; entry `A001`, generation 1 |
| Target commit (base) | `b05f3432`, rebased onto `5192e13e` at 02:40Z (picks up the resumable migrate-session change and the spare-release fix to `name_registry.py`) |
| Orchestrator worktree | the orchestrating session's own session worktree |
| Orchestrator branch | that worktree's session branch |
| Stage cursor | `doc done`; design doc `docs/designs/session-name-registry-dolt.md`; research doc on main at `aadd96ac` (PR #500) |
| Last completed integration boundary | research, landed on main as `aadd96ac` (PR #500) |

## Transition verdicts

| Transition | Round | Verdict | Basis | Items | Recorded by | Commit |
|---|---:|---|---|---|---|---|
| `entry->research` | 0 | `research-run` | `explicit` | The owner asked for a full-breadth and full-depth research doc on git-tracked storage before the design. R2 and R3 also fire. | `session` | `6da3d70e` |
| `research->doc` | 1 | `converged` | `rubric` | none | `session` | `35a96b40` |

## Children dispatched

| # | Child issue | Launch name | Role | Family / tier / effort | Isolation | Deliverable | Outcome |
|---|---|---|---|---|---|---|---|
| 1 | AI-CLI-6xud.1 | `6xud-dolt-deep-dive` | `researcher`, read-only | claude / opus / n/a | none, writes no file | cited findings on Dolt | returned; child closed |
| 2 | AI-CLI-6xud.2 | `6xud-storage-landscape` | `researcher`, read-only | claude / opus / n/a | none, writes no file | cited survey of alternatives | returned; child closed |
| 3 | AI-CLI-6xud.3 | `6xud-registry-inventory` | `Explore`, read-only | claude / opus / n/a | none, writes no file | line-cited inventory of the current registry and prior art | returned; child closed |
| 4 | AI-CLI-6xud.4 | `6xud-research-critic` | `researcher`, read-only, non-author | claude / opus / n/a | none, writes no file | five-perspective critic findings (gap G5) | returned 21 findings (2 high); all folded in at `35a96b40`; child closed |

<!-- doc:region name="run_history" kind="append_only" -->

- 2026-11-11: run opened by the orchestrating session after taking AI-CLI-6xud from the
  session inbox.
- 2026-11-11 02:0xZ: the first launch of all three legs was refused by the launch guard because
  their briefs carried the founding ask as plain text, not the canonical carrier. The run had
  tried to launch before the founding ask record was committed; the guard was right.
- 2026-11-11: the founding ask capture lost the push race on the harness repository's trunk
  twice (its pre-push gate ran about four minutes and trunk moved each time; the tool did not
  retry that refusal). The record was captured into a dedicated worktree branch instead and
  landed through a pull request. The retry gap is fixed separately.
- 2026-11-11 ~02:16Z: the three legs launched with rendered Tier 3 carriers.
- 2026-11-11 02:40Z: this worktree was rebased onto `5192e13e` while the inventory leg was
  reading it; that leg may cite either base, and the synthesis checks its line references
  against `5192e13e`.

- 2026-11-11: research doc and spike evidence committed (`47c910bb`). Gap review: G1 to G4 and
  G6 clear (G4 items classified in the doc's Open Questions; G6, the governing design's earlier
  rejection of Dolt, is answered in the doc). G5 waits on the critic leaf.

- 2026-11-11 ~03:15Z to 03:40Z: the critic's 21 findings were reviewed against round-2 spikes
  on the pinned Dolt version (a 20-trial concurrent race, the git-ref layout, key collation, a
  JSON cell merge, a hosted push) and a read of the pinned tag's source. Every high and medium
  finding was fixed, backed with new evidence, or corrected where the evidence disagreed (the
  history-cap setting is 2.4.1+, the lock-fallback issue closed by documentation, JSON keys merge
  cleanly). G1 to G6 re-run over the whole doc: none open. `research->doc` round 1 recorded
  `converged` at `35a96b40`.

- 2026-11-11 ~04:15Z: research landed on main through PR #500 (`aadd96ac`, squash tree
  verified). The doc stage's prepare ran against that snapshot, the orchestrator authored the
  design doc (nine decisions resolved in automated mode, none escalated; two open questions for
  the owner, neither blocking Phases 1 to 3), and complete passed. Validator, markdown lint,
  decision-coverage ToC and founding-ask fidelity checks all clean.

<!-- /doc:region name="run_history" -->
