---
title: "Reasoning Checkpoints"
category: procedures
tags: [reasoning, checkpoints, quality, agents, claude-code]
status: active
source: internal
---

> Design doc: `docs/designs/reasoning-checkpoints.md` | Research: `docs/research/reasoning-enhancement-synthesis.md`

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

```xml
<agent_spawn_checkpoint>
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
