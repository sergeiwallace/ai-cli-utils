# Audits

Audit reports — structured reviews of code, docs, plans, or systems against a
defined scope, producing falsifiable findings and a sign-off checklist.

## What belongs here

- Plan / design / research doc audits (verifying AC coverage, parity, gaps)
- Codebase audits (silent-degradation patterns, dead code, state-management
  invariants, security review sweeps)
- Implementation audits (step 14 of the dev workflow — verify every AC against
  actual code)
- Post-incident audits (re-check the area that broke for related issues)
- Follow the `TEMPLATE.md` in this directory

## What does NOT belong here

- Bug investigations with root cause analysis -> use `bugs/` instead
- Implementation plans for fixes uncovered by an audit -> use `plans/` instead
  (the audit doc references the fix plan via `resolved_by`)
- Architecture decisions or design rationale -> use `designs/` instead
- Ad-hoc tradeoff analyses -> use `analysis/` instead
- Procedure documents (how to audit) -> use `procedures/` instead
  (the procedure tells you HOW; the audit doc is the OUTPUT of running it)

## Audit lifecycle (multi-round is the norm, not the exception)

Real audits almost always go more than one round. The TEMPLATE.md treats
each round as a first-class section. The gold-standard pattern (from
`CLD-Workspace/.../cld-hypothesis-update-audit.md`, 3 rounds in one session):

1. **Trigger** — a roadmap task, a step-14 gate, a teammate request, or a
   pattern observed in production.
2. **Scope** — fill `## Scope` (in / out) before any inspection. Resist creep.
3. **Methodology** — record the exact commands / files / queries used.
   Reproducibility is the audit's credibility.
4. **Round 1 — Main audit** — find issues. Tag them IC-N / JA-N / DV-N / F-N
   per the audit kind. Apply unambiguous fixes inline (record the commit).
   Move team-input decisions to AD-N.
5. **Round 2 — Verification pass (append-only)** — ideally a different agent
   / model verifies that every Round 1 finding's fix is actually in the
   target. Surface N-N for new issues introduced by Round 1 fixes. **No
   target edits in this round** — the point is so Round 3 can tell what was
   actually applied vs what the verifier patched.
6. **Round 3+ — Resolution pass** — apply Round 2 findings. Re-grep the
   target to confirm fixes landed. Repeat verification rounds if needed.
7. **Sign-off** — all rounds clear, fill the checklist. The "Anti-Patterns
   to Watch For" section of the doc should reflect any methodology lessons
   this audit revealed.

A one-round audit is appropriate only when the target is trivial OR Round 1
found nothing. Otherwise, default to at least Round 1 + Round 2.

## Severity tags

- `P0` — blocking. Ship-stopper, security hole, data loss risk.
- `P1` — high. Will cause user-visible failure or significant rework if shipped.
- `P2` — medium. Quality / clarity / tech-debt; deferrable but file a task.
- `P3` — cosmetic. Style, naming, formatting.

## Naming

`<scope-or-id>-audit.md` — e.g.:

- `companion-107-silent-degradation-audit.md`
- `rsmcld-149-plan-audit.md`
- `cld-48-hypothesis-agent-audit.md`
- `q2-2026-security-audit.md`
