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
| Stage cursor | `research converged (round 1)`; research doc `docs/research/cli-docs-generation-tools.md`; doc stage next |
| Last completed integration boundary | none |

## Transition verdicts

| Transition | Round | Verdict | Basis | Items | Recorded by | Commit |
|---|---:|---|---|---|---|---|
| `entry->research` | 0 | `research-run` | `explicit` | The owner asked to "see what open source packages / tools / repos etc there are" and "what existing documentaiton there is for auto-generating documentation programmatically and / or with agentic AI". R1 and R2 also fire. | `session` | `d301960c` |
| `research->doc` | 1 | `converged` | `rubric` | none | `session` | `e3aa819c` |

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

<!-- /doc:region name="run_history" -->
