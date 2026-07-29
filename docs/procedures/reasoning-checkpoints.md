---
title: "Reasoning Checkpoints"
category: procedures
tags: [reasoning, checkpoints, quality, agents, claude-code]
status: active
source: sergei — design doc docs/designs/reasoning-checkpoints.md
---

> Design doc: `docs/designs/reasoning-checkpoints.md` | Research: `~/projects/aido/docs/research/reasoning-enhancement-synthesis.md` (aido R-17 — canonical home for prompt-engineering research)

Domain-specific reasoning checkpoints for high-stakes operations in Claude Code and Gemini sessions. Based on the finding that domain-specific guidance amplifies reasoning quality on medium-complexity, multi-step constrained tasks (Tau-bench +54% with optimized prompt; SWE-agent ACI ablation showing structured constraints beat prose).

**When to use:** Only activate for the specific scenarios listed below. Routine operations (doc updates, propagation, roadmap maintenance, single-file edits) do not require checkpoints — adding overhead there is counterproductive.

---

## Universal Verification Core

Applied before any high-stakes operation triggered by a CLAUDE.md pointer:

```xml
<reasoning_checkpoint>
BEFORE executing this operation:
1. SCOPE: State exactly what you are about to change and what you are NOT changing.
   Verify the change matches the task spec — not broader, not narrower.
2. CONSTRAINTS: List all constraints that apply from CLAUDE.md, the plan doc,
   or architecture doc. Verify your planned change satisfies every one of them.
3. VERIFY: After completing the operation, check the result against the original
   intent. If tests exist, run them. If not, state explicitly what you verified and how.
</reasoning_checkpoint>
```text

---

## Scenario Checkpoints

### 1. DB Migrations

Run the universal core first, then extend with:

```xml
<db_migration_checkpoint>
- Read the current schema in full before writing any SQL
- Grep the codebase for all callers of the affected table or column
- Write the rollback migration before the forward migration
- Run the full test suite after applying the migration, not just migration tests
</db_migration_checkpoint>
```text

### 2. Architecture Decisions

```xml
<architecture_checkpoint>
- Read the relevant section of docs/designs/architecture.md before proposing
- Check at least 2 adjacent systems for integration impact
- List concrete failure modes of the proposed approach
- Verify the decision aligns with the platform design philosophy in CLAUDE.md
</architecture_checkpoint>
```text

### 3. Test Derivation from Plan ACs

```xml
<test_derivation_checkpoint>
- Read the FULL plan doc AC list before writing any test
- Map each AC to at least one test — verify one-to-one coverage
- Write at least one failure-path test per public function
- Confirm each test would fail if the function body were replaced with `pass` or `return None`
</test_derivation_checkpoint>
```text

### 4. Complex Multi-File Implementation

Use when a task touches 3+ files or involves shared state across modules:

```xml
<implementation_checkpoint>
- Map the dependency chain between all files being changed
- Identify shared state and verify no conflicting mutations across files
- Sequence edits so the codebase is in a valid state after each commit
- Run the full test suite after each logical unit of work, not just at the end
</implementation_checkpoint>
```text

### 5. Research Synthesis to Design Doc

```xml
<synthesis_checkpoint>
- Verify the top 3 factual claims by checking the cited source documents
- Classify each recommendation as evidence-backed or inferred
- Check for contradictions between source documents
- Ensure every design decision traces to a research finding
</synthesis_checkpoint>
```text

### 6. Agent Spawn

Spawning an agent — especially an Opus or otherwise metered/long-running one — is expensive; verify the work is actually needed before paying for it.

```xml
<agent_spawn_checkpoint>
- Validate before implementing: for an IMPLEMENTATION agent, confirm the work isn't ALREADY done — `git log`/`git show` the target files + grep the code. A plan/design doc's `status:` / "not yet built" line is NOT ground truth (docs go stale); the code + git history are. (Real incident: an Opus agent burned ~118k tokens re-confirming work committed days earlier because the plan doc still said "not built" — a 2-second `git log` would have caught it.)
- Verify what an agent REPORTS before asserting/acting on it — especially **absence/negative claims** ("not found", "no such file", "doesn't exist") and "is this already handled elsewhere?". A search/discovery sub-agent produces false negatives easily (wrong glob, checked one repo not all, tool truncation); **failure-to-find ≠ absence**. Before repeating a sub-agent's load-bearing claim to the user, acting on it, or filing a task from it, spot-check against ground truth (one `ls`/`grep`/`git log`, or a look at the live task list). (Real incident 2026-07-06: a discovery sub-agent reported `orchestration.md` "not found" → it was repeated as fact in a recommendation → the file actually existed in two repos; a 1-line `ls` would have caught it. Same session: nearly filed a duplicate DB-allocator task before checking that an in-flight task already covered it.)
- Summarize only the context the agent needs — no more, no less
- State explicitly what the agent should NOT do (scope boundaries)
- Confirm there are no file conflicts with other running agents or the parent session
- Specify the expected output format and success criteria
</agent_spawn_checkpoint>
```text

### 7. Post-Implementation Architecture Doc Update

Trigger: completing work that adds or changes a service, MCP tool, data model, subsystem, or cross-machine integration.

```xml
<architecture_update_checkpoint>
- Determine whether the completed work changes any external interface, component, or system integration
- If yes: identify which sections of docs/designs/architecture.md need updating and make the update
- If no change needed: state explicitly why (e.g., "internal refactor, no interface or component changes")
- Ship the update in the same commit as implementation, or immediately after — never batch it for later
</architecture_update_checkpoint>
```text

### 8. Roadmap Coherence Audit

Trigger: any session that runs a coherence audit or claims open roadmap tasks.

```xml
<coherence_audit_checkpoint>
- For every open roadmap task with a linked plan doc: read the plan doc's Approval Log in full
  (it is always at the bottom). "Approval Log has completion date" → task is stale open → mark [x].
- For every open roadmap task with a linked design doc: check the frontmatter status field AND
  the **Status:** line near the top. "status: active" on a shipped feature = stale.
- Do NOT rely on frontmatter status: alone — authors forget to update it. Always read the Approval Log.
- Verify implementation exists in code before marking any task [x], even if plan doc says done.
- Full procedure: docs/procedures/roadmap-coherence-audit.md
</coherence_audit_checkpoint>
```text
