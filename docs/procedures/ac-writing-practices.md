---
title: "AC Writing Practices"
category: procedures
tags: [ac, acceptance-criteria, plan-docs, quality, feature-parity]
status: active
source: AIDO-69
---

# AC Writing Practices

> How to write acceptance criteria that catch silent feature drops, not just verify new behavior.
>
> Last updated: 2026-05-25

## The Problem

When replacing or rewriting a module, ACs are typically written forward: "the new implementation does A, B, C." The behaviors the old module had that weren't named in the task description are silently dropped — and tests pass because no test ever covered the dropped behavior.

**The T-10/T-11 failure (2026):** The native Gemini client plan listed ACs for the new API path but did not require verifying parity with the Interactions API path it replaced. The Interactions path was silently dropped. Tests passed. The gap was only caught in a later audit.

---

## Rule: Feature-Parity Mandate

**Any task that replaces, refactors, or rewrites an existing module must include feature-parity ACs.**

A feature-parity AC is one of:
- `[ ] Behavior X from the old implementation is preserved in the new one` — with a test
- `[ ] Behavior X is intentionally dropped — [reason]` — documented, no test needed

The absence of parity ACs on a replacement task is an AC completeness failure, not a style preference.

---

## The Three-Step Workflow for Replacement Tasks

### Step 1 — Inventory Before Writing ACs

Before writing any AC for a replacement/refactor task, explicitly enumerate what the existing implementation does.

**How:**
- Read the full source of the module being replaced
- Check `git log -p -- <path>` for recent behavior changes not obvious from current code
- Grep for all call sites: `grep -r "old_function_name" src/ tests/`
- List each distinct behavior, code path, and edge case

**Output:** A bullet list of behaviors goes in the plan doc under the relevant task, before the AC list. Example:

```
**Existing behaviors (inventory):**
- Path A: Interactions API — called when GEMINI_INTERACTIONS=1
- Path B: Standard API — called otherwise
- Edge case: Falls back to Path B when Path A returns 429
- Side effect: Logs model used to token_usage_log
```

### Step 2 — Write Parity ACs for Each Behavior

For each item in the inventory, write an explicit AC:

```
**Acceptance criteria:**
- [ ] Path A (Interactions API) is preserved and called when GEMINI_INTERACTIONS=1
- [ ] Path B (Standard API) is called when GEMINI_INTERACTIONS is unset
- [ ] 429 fallback from Path A to Path B is preserved
- [ ] model is logged to token_usage_log for both paths
```

If a behavior is intentionally dropped, document it explicitly:
```
- [ ] Path A fallback is intentionally removed — new client handles 429 internally
```

### Step 3 — Step-14 Parity Audit

During the implementation audit (step 14 of the dev workflow):
- Re-read the original implementation (from git history if already deleted: `git show HEAD~N:path/to/old.py`)
- Check each behavior from the inventory against the new implementation
- Do not mark the audit complete until every inventory item is accounted for

---

## Headline Rules (canonical)

<!-- SYNC (AIDO-110 T-06): the rules between the canonical markers below are the
     single source of truth for the short AC reminder mirrored inline in the
     design / plan / audit TEMPLATE.md files. `aido validate-doc` fails any
     template whose `ac-rules:mirror` block diverges from this `ac-rules:canonical`
     block — so update both together (the check is the real enforcement; this
     comment + the projects-wide session-config rule are the advisory layers). -->

<!-- aido:ac-rules:canonical:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Phrase every AC with EARS keywords: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path).
- At least one failure-path AC — EARS `If <condition>, then the system shall …` — per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- aido:ac-rules:canonical:end -->

> The five canonical bullets above are the tight per-AC testability core mirrored into the
> design/plan/audit/spec `TEMPLATE.md` files. The **spec-rigor standards** below extend them with
> the broader task-specification best practices; they are reinforced at point-of-use by the
> "SPEC RIGOR" HTML comment in each template's Implementation-Phases / Task-Breakdown / Spec section.

## Spec-Rigor Standards (task-specification best practices)

Evidence-based practices for writing task specs that an AI coding agent executes reliably,
completely, and without judgment-call drift. Grounded in the task-specification-best-practices
research ([📄 aido/docs/research/task-specification-best-practices-ai-coding-agents.md](../../../aido/docs/research/task-specification-best-practices-ai-coding-agents.md),
registry `R-1780610095`); parity finding #5 is the Feature-Parity Mandate above.

### 1. Ship acceptance criteria as executable tests, not prose

Human-authored executable specs (a failing test the agent must turn green) beat agent-self-authored
ACs. Commit the failing tests first as a checkpoint so the agent cannot quietly rewrite them to pass.
*Direction only:* the research's headline magnitude (a ~25-point pass-rate swing, TDFlow) is
**large, single-study, and contamination-exposed** — cite it that way, never as a hard benchmark
number. The trustworthy claim is directional: executable specs > prose ACs.

### 2. Mandate non-mocked behavioral assertions; gate on mutation score, not line coverage

Agents over-mock and write hollow tests (assert a mock was called, tautological oracles) unless the
spec forbids it. Require **at least one non-mocked behavioral assertion per behavior** — do not mock
the primary inputs. Gate on **mutation score** on the changed public surface + parity-critical paths;
treat line/branch coverage as a **floor, never a target** (coverage-as-goal is reward hacking). Add
**property-based tests** for edge/boundary space on changed public functions.

### 3. Phrase every AC with EARS keyword templates

Write ACs in **EARS** (Easy Approach to Requirements Syntax) — keyword-constrained natural language
that forces testability and traceability without formal modeling. The five patterns:

- **Ubiquitous** (always-on): `The <system> shall <response>.`
- **Event-driven:** `When <trigger>, the <system> shall <response>.`
- **State-driven:** `While <in-state>, the <system> shall <response>.`
- **Optional-feature:** `Where <feature is included>, the <system> shall <response>.`
- **Unwanted-behavior (failure path):** `If <condition>, then the <system> shall <response>.`

The `If … then … shall` form is the native home for the "≥1 failure-path AC per public function"
rule — every empty input, duplicate, permission error, or boundary breach gets an explicit
`If … then … shall`, so the agent cannot invent its own failure logic. EARS reduces *surface*
ambiguity; it does not replace externalizing tacit assumptions (the deeper source of drift), so
still state I/O, edge cases, and non-goals explicitly. Keep phrasing behavior-anchored
("shall return 404", not "shall work"); ship each as a failing test where feasible.

### 4. Specificity has a dimensional sweet spot — spec the *what*, starve the *how*

Spend the specificity budget on **explicit I/O, edge cases, failure paths, parity, and exit gates**;
**do not** dictate internal implementation (data structures, algorithm choice, naming) — over-
constraining the *how* measurably degrades quality on open-ended work, while under-specification ~2x's
regression risk. Scale spec rigor with blast radius: **2+ files ⇒ a full plan** (or design-with-impl).

### 5. Replace "declared done" with harness-enforced exit gates + outcome-based verification

Agents emit completion language ("all tests passing") as a token pattern regardless of actual state,
so orchestrators that pattern-match transcripts are fooled. Prefer **gates the agent cannot assert
past** (the Claude Code plan-mode principle — enforced by the harness, not by prompting) and
**outcome-based verification**: state each exit gate as a runnable predicate (run the suite, mutation
score ≥ threshold on changed surface), and add a **fresh-context subagent diff review** against the
criteria before accepting "done."

## AC Completeness Checklist

Use this checklist when writing ACs for any non-trivial task:

**For all tasks:**
- [ ] Every AC is independently testable (can write a test that fails if only this AC is violated)
- [ ] Every AC is falsifiable — "works correctly" is not an AC
- [ ] At least one failure-path AC per public function changed
- [ ] No AC is implied — if it's not written down, it won't be verified

**For replacement/refactor tasks (additional):**
- [ ] Inventory of existing behaviors is documented in the plan doc
- [ ] Every inventory item has a corresponding parity AC (preserved or intentionally dropped)
- [ ] The inventory was built by reading source, not from memory or task description alone
- [ ] Step-14 audit includes re-checking the original implementation from git history

---

## Anti-Patterns

- **Forward-only ACs:** Writing only "the new module does X" without "and preserves Y from the old module." This is the most common failure mode.
- **Implicit parity:** Assuming that because the old behavior "obviously" should be preserved, it doesn't need an AC. It does.
- **Inventory from task description:** Using the task description as the behavior inventory. The task description names what was asked for, not everything the old module did. Always read the source.
- **Parity without tests:** Writing "old behavior X is preserved" as an AC without backing it with a test. An AC without a test is an assertion without verification.

---

## When This Applies

| Task type | Feature-parity ACs required? |
|-----------|------------------------------|
| Replacing a module/function with a new implementation | Yes |
| Refactoring internals without changing the public interface | Yes (parity = same public behavior) |
| Adding a new feature to an existing module | No (but document what's not changing) |
| Net-new implementation with no predecessor | No |
| Bug fix | No (but write a reproduction test first) |
