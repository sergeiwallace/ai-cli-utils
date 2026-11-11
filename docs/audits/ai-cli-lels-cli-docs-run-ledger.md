---
title: "AI-CLI-lels single-source CLI docs run: Run Ledger"
category: audit
tags: [audit, run-ledger, orchestration, cli-docs, wiki]
status: active
source: "claude-opus-5-5-2026-11-11"
template_version: "audit-1.0.0"
task: AI-CLI-lels
---

# AI-CLI-lels single-source CLI docs run: Run Ledger

**Target artifact:** the research doc first, then the design doc for single-source CLI help,
CLI reference and wiki generation. Paths are recorded per stage below as each artifact is created.

This file is the orchestration cursor and restart contract for the run. It is not an audit of
either document; each audit stage keeps its own audit document and receipt file.

## Run Ledger

ledger-version: 1

| Run ID | Contract | Decision classes | Push |
|---|---|---|---|
| `ai-cli-lels-cli-docs-2026-11-11` | `auto-flow/1#d-7` | `["commit-without-push", "adn:in-scope"]` | `false` |

### Resolved run plan

| Field | Value |
|---|---|
| Run ID | `ai-cli-lels-cli-docs-2026-11-11` |
| D-12 shape | `/delegate /orchestrate /auto-flow`: `orchestrate: true`; this coordinator is the orchestrator and every agent it launches is a leaf |
| Driver model | `us.anthropic.claude-opus-5-5` |
| Selected stages | `research`, `doc`, `doc-audit`, `implement`, `impl-audit` (preset `full`, source: explicit stage list in the launch brief) |
| Model family | `mixed` (omitted) |
| Route: research | legwork: claude / opus / n/a researcher leaves, read-only; final authorship: this coordinator (source: orchestrator authorship override, Claude-API profile) |
| Route: doc | authorship: this coordinator (source: orchestrator authorship override) |
| Route: doc-audit | codex / audit / high (source: default, Claude-API profile effort) |
| Route: implement | claude / opus / n/a, `engineer` persona (source: default, Claude-API profile) |
| Route: impl-audit | codex / audit / high (source: default, Claude-API profile effort) |
| Route: AD-N decider | claude / opus / n/a for items a Claude round raises; codex in-loop fix pass otherwise (source: default) |
| Max concurrent dispatched descendants | 2 (launch brief bound) |
| Decisions | `resolve_decisions: true` (source: default) |
| Doc approval | `require_doc_approval: false` (source: default) |
| Integration mode | this coordinator lands each stage on `main` by pull request |
| Founding ask | Tier 3. Record `docs/asks/AI-CLI-single-source-cli-help-usage-lels.md` in the operator's private harness repository at capture commit `1e65faf1fd58`; `founding_ask_ref_sha256` `cc266ebe307ece299aea75e1f3ed34acc3428467545b0a3e654850de2fc4bfca`; content sha256 `7e40d47e4e7a8cc4b38f1dc02d435f916c11926c3d59ef6f66cc4552708b8df9`; entry `A001`, generation 1 |
| Target commit (base) | `d301960c` |
| Orchestrator worktree | `.worktrees/<session>-agents/lels-docs-flow` |
| Orchestrator branch | `wt/agent/lels-docs-flow` |
| Stage cursor | `doc-audit audit-stabilized` (8 rounds, two spec freezes); boundary `doc-audit->implement` finalized and the implement gate passed for design sha256 `1c5c7a5a`; implement next |
| Review tier (doc-audit) | `none`: the Codex audit rounds and the Codex register review are the cross-family checks; an added Claude round would review a Claude-authored design with the same family |
| Last completed integration boundary | none |

## Transition verdicts

| Transition | Round | Verdict | Basis | Items | Recorded by | Commit |
|---|---:|---|---|---|---|---|
| `entry->research` | 0 | `research-run` | `explicit` | The owner asked to "see what open source packages / tools / repos etc there are" and "what existing documentaiton there is for auto-generating documentation programmatically and / or with agentic AI". R1 and R2 also fire. | `session` | `d301960c` |
| `research->doc` | 1 | `converged` | `rubric` | none | `session` | `e3aa819c` |
| `doc-audit->implement` | 8 | `advance` | `fold` | none | `session` | `HEAD` |

## Children dispatched

| # | Child issue | Launch name | Role | Family / tier / effort | Isolation | Deliverable | Outcome |
|---|---|---|---|---|---|---|---|
| 1 | AI-CLI-lels.2 | `lels-research-tools` | `researcher`, read-only | claude / opus / n/a | none, writes only a scratch evidence file | cited survey of Click help and Markdown generators, drift checks and wiki publishing | returned 68 cited sources; child closed |
| 2 | AI-CLI-lels.3 | `lels-research-agentic` | `researcher`, read-only | claude / opus / n/a | none, writes only a scratch evidence file | cited survey of agentic and LLM documentation generation | returned 51 cited sources; child closed |

<!-- doc:region name="run_history" kind="append_only" -->

- 2026-11-11 05:03Z: run opened by the coordinator; its own child issue filed and claimed.
- 2026-11-11 05:10Z: both research legs launched with rendered Tier 3 carriers. Host contention
  read LOOK at launch.

- 2026-11-11 ~05:45Z: both legs returned. The coordinator measured the Click mechanism, the
  command tree, doc coverage and the wiki's state, and wrote the research doc. Gap review over the
  whole doc: G1 to G4 and G6 clear (the one researchable platform fact, whether the workflow token
  can push to the wiki, is classified as settled by the first run); G5 has no critic round in this
  run, so no critic finding is open. Both legs reported that general web search was unavailable;
  the host's search service was not loadable from the coordinator either, and the remaining
  search gap does not bear on a decision. `research->doc` round 1 recorded `converged`.

- 2026-11-11 ~06:00Z: design doc authored by the coordinator (eight Decisions, each resolved
  through the Authority test; no Open Questions); `scripts/far_fidelity.py check` reported 0
  findings against the raw record. Doc-stage boundary run through `flow_dispatch_doc.py` prepare
  and complete with lineage root `docs/tools/ai-cli-usage.md` (the hand-written usage doc this
  design supersedes in part); a first prepare with the design itself as lineage root refused
  `pinned-target-absent`, correctly, since that file is new.

- 2026-11-11 ~06:10Z: doc-audit round 1 (codex audit, Sol, high) returned 7 MAJOR, 0 CRITICAL,
  among them a founding-ask fidelity point (root help must show complete descriptions, not a
  first sentence). The in-loop fix pass (codex implement, medium) applied all seven in `9f816f1d`,
  including a docs-scoped hygiene test for this design. Per-round sanity check: clear. Round 2
  (verify) returned one MAJOR, round2:N-1 (sync flags missing from the parity inventory), the same
  missing-parity-coverage class as round1:F-2, and the loop terminated `halted-recurrence`.
  Diagnosis under the Authority test: a bounded systematic fix (read the inventory from the parser
  source and list it in full, every row tested) is permitted to the coordinator; applied in
  `fa1886ff`; invariant register drafted for the freeze.

- 2026-11-11 ~06:20Z: the register's dimension challenge (codex review, Sol, medium) returned
  REJECT with one inventory error (repair-worktree arguments) and two omissions (caller
  compatibility; the two epics' disposition). All accepted and applied in `b28bb410`. Spec frozen
  (standard tier, no ratification needed); round 3 conformance returned no new finding and the
  loop terminated `audit-converged`. A hygiene failure on the scaffolded audit doc (a private
  tool name in its frontmatter) was fixed in `5681888f` and the scaffold defect filed in the
  harness task store.

- 2026-11-11 ~06:30Z: boundary alignment for `doc-audit->implement` (codex review, Sol, medium,
  non-author) returned MISMATCH: the wiki scope excluded design, plan, research and audit records,
  narrowing "everything about this repo", and the two epics' dispositions lacked evidence. Both
  fixed in `960e5fe8` (every document published; measured epic state recorded). Finalize recorded
  the artifact drift; rounds 4 to 6 re-audited it, and round 6 halted on fingerprint recurrence of
  the wiki-coverage finding (LICENSE outside the enumerated locations). Systematic fix: coverage
  by publish-unless-excluded with publication precedence (`bdf9a4c0`, `aca5af21`); register
  generation 2 reviewed (codex review, REJECT, all items applied) and frozen. The launcher
  confirmed the Discussions epic's task-panel entry exists.

- 2026-11-11 ~07:20Z: rounds 7 and 8 converged after one more fix pass (`f5461940`). The audit
  budget (8 rounds at the standard tier) is now spent, so any further design change would halt at
  the cap. Boundary alignment, second attempt: MISMATCH on a brief contradiction (the audit
  authority's `push: false` against the leaf's own push and PR) and on the deliberate rejection
  of unknown `ai ps` flags. The brief was clarified; the third attempt, given the scope definition
  and the recorded rationale for the `ps` change, returned ALIGNED. Its two scope-change `kind`
  values were lower-case; they were upper-cased to the schema's enum with no other edit. Finalize
  returned `audit-stabilized`; `check_auto_flow_implement_gate.py` passed.

<!-- /doc:region name="run_history" -->
