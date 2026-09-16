---
title: "AI-CLI-5abz design run — Run Ledger"
category: audit
tags: [audit, run-ledger, orchestration]
status: active
source: "claude-fable-5-1-2026-09-16"
template_version: "audit-1.0.0"
task: AI-CLI-5abz
---

# AI-CLI-5abz design run — Run Ledger

**Target artifact:** `docs/designs/vscode-terminal-tab-naming.md`

This is the orchestration cursor and restart contract for the AI-CLI-5abz research + design run. It
is not an audit of the design; no audit stage ran.

## Run Ledger

`ledger-version: 1`

| Field | Value |
|---|---|
| Run ID | `ai-cli-5abz-design-2026-09-16` |
| Contract | `auto-flow/1 (stages: research, doc)` |
| Selected stages | `research`, `doc` — **only**. `doc-audit`, `implement`, `impl-audit` deliberately NOT run: the design is the deliverable and implementation is a separately authorised run. |
| Target commit (base) | `56ef9c1` |
| Worktree | `.worktrees/aicli5abz-design` |
| Branch | `wt/aicli5abz-design` |
| Push | `false` — commit only; integration belongs to the launching session |
| Decision classes | design-internal only, per the decision framework's Authority test gates A–D; anything the test escalates stays `⏳ PENDING (human)` |
| Max concurrent dispatched descendants | 3 (actual peak: 2) |
| Stage cursor | `doc done` |
| Last completed integration boundary | none — nothing merged or pushed by this run, by design |

## Authority

| Run ID | Contract | Decision classes | Push |
|---|---|---|---|
| `ai-cli-5abz-design-2026-09-16` | `auto-flow/1` | `["commit-without-push", "adn:in-scope"]` | `false` |

## Stage boundaries

| Stage | Terminal | Artifact | Notes |
|---|---|---|---|
| `entry->research` | `research-run` | — | Basis: explicit instruction. R1 fired (a current external-platform fact — how the VS Code integrated terminal takes a tab name — was load-bearing and no existing repository research covered it; confirmed by a read-only sweep of the five candidate docs). |
| `research` | `converged` | `docs/research/vscode-integrated-terminal-tab-naming.md` | Round 1. Gap review G1–G6: G1–G3 clear (every ask question has a mapped finding with a citation or an explicit null result); G4 — three factual questions remain open, each classified with the reason it is unanswerable from here rather than left silent; G5 no critic finding unresolved; G6 — two seeded premises were **contradicted** by measurement and the doc says so explicitly rather than reconciling them. |
| `research->doc` | `converged` | — | No refine round needed. |
| `doc` | `doc done` | `docs/designs/vscode-terminal-tab-naming.md` | 5 decisions resolved, 3 open questions recorded (1 gating, 2 non-blocking). Doc-structure validation: OK (4 regions, `design-1.0.0`). |

## Children dispatched

| # | Role | Family / tier / model / effort | Isolation | Deliverable | Outcome |
|---|---|---|---|---|---|
| 1 | `analyst` — prior-art documentation sweep | claude / sonnet / `claude-sonnet-5` / n/a | none needed — read-only, wrote no file | bounded findings list | Returned. Key result: no existing document in this repository addresses VS Code integrated-terminal titles, and no test covers the VS Code detection input. |
| 2 | `Explore` — title emitter call-chain trace | claude / sonnet / `claude-sonnet-5` / n/a | none needed — read-only, wrote no file | line-cited call-chain map | Returned. Two findings contradicted the run's own framing (the tmux helper is unguarded; the bare path emits nothing) and both changed the design. |
| 3 | `general-purpose` — fresh-context verifier | claude / sonnet / `claude-sonnet-5` / n/a | none needed — read-only, wrote no file | per-claim verdicts | See the verification section below. |

Both write-capable-looking legs were in fact read-only and wrote nothing, so no child worktree was
created and no child branch needed merging — the isolation-collapse check is trivially satisfied
(`git log` on this branch contains only this orchestrator's own commits).

## Verification

A fresh-context verifier re-opened every cited file and line and checked the public-repository
constraint. Recorded outcome and any corrections applied: see the Findings section.

## Findings

| # | Finding | Disposition |
|---|---|---|
| F-1 | The seeded premise "tmux sets the tab name to `tmux`, clobbering ours" is **false** on the measured host: `set-titles` is off, so tmux emits no title at all; VS Code falls back to its own `${process}` template. | Corrected in both docs; the design's mechanism changed as a result. |
| F-2 | The seeded premise "`TERM_PROGRAM=vscode` remains readable inside tmux" is **false** as stated: tmux overwrites it in every pane with `tmux`. It survives only in the tmux session environment. | Corrected; D-3 of the design turns on this fact. |
| F-3 | `_configure_tmux_for_iterm2()` is called unguarded for every tmux session, so `allow-passthrough all` and `automatic-rename off` are already set for VS Code sessions. | Recorded; removes two thirds of the expected work and creates a naming-debt risk (design Risk 3). |
| F-4 | The bare (non-tmux) path in this package emits no title sequence; the working bare case is VS Code's own `allowAgentCliTitle` behaviour. | Recorded; changes what "must not regress" means for AC-2. |
| F-5 | Pre-existing, unrelated: the committed `.beads/issues.jsonl` in this public repository contains the requester's employer name in 11 issue records, and `tests/test_public_repo_hygiene.py` does not scan for it. | **Not repaired here** — reported. It is a public-git-history question, not a code fix, and repairing it is out of proportion to this run. Named in the run report for filing. |
| F-6 | Pre-existing, unrelated: the canonical design stub injects a private tool name into any design doc scaffolded in this public repository, which fails this repo's own hygiene gate on a freshly scaffolded file. | **Fixed locally** by removing the injected line from this document; the upstream stub fix is named in the run report for filing, since it lives in another repository this run may not write to. |

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-09-16 — run created and completed through stage `doc`. Orchestrator
  `us.anthropic.claude-fable-5-1`, effort high, 2 read-only sweep children + 1 fresh-context
  verifier, peak concurrency 2 of a cap of 3. Stopped at `doc done` as instructed; no audit,
  no implementation, no push, no PR.

<!-- /doc:region name="run_history" -->
