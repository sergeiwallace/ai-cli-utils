---
title: AI-CLI-208 — new session/worktree/CC-title lowercasing fix — audit
category: audit
tags: [audit, ai-cli-208, session, worktree, casing]
status: draft
date: 2026-08-10
source: "aido-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  delegated_to: codex/audit
  tier: audit
  model: none
  effort: high
  persona: none
  worktree: /Users/sergeiwallace/projects/ai-cli-utils/.worktrees/ai-cli-1
  session: none
---

# AI-CLI-208 — new session/worktree/CC-title lowercasing fix — audit

**Status:** draft

**Created:** 2026-08-10

**Auditor:** Codex audit (`cx audit`, effort: high) — findings incorporated by Claude

**Target commit:** 2b3a6b2

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [Scope](#scope)
- [Round 1 — Main Audit](#round-1--main-audit)
- [Audit Log](#audit-log)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## Scope

The AI-CLI-208 fix (commit `2b3a6b2`, `src/ai_cli/session.py`,
`tests/test_session.py`, `tests/test_session_launch_integration.py`):
newly allocated tmux session names, `ai_name` (worktree directory name), and
the CC session's `customTitle` (iTerm2 tab/pane title) must always be
lowercase, regardless of the fleet-registry prefix's registered casing,
while `resolve_project_prefix()`/`get_project_prefix()` must keep returning
the registry's raw value unchanged for any other consumer, and AI-CLI-206's
case-insensitive resume-matching behavior (commit `2519721`, same file)
must be unaffected.

<!-- /doc:region name="scope" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

(none yet)

<!-- /doc:region name="round_1_findings" -->

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|

<!-- /doc:region name="audit_log" -->

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** Codex audit role (`cx audit`, effort: high)

**Date:** 2026-08-10

```text
You are a principal staff engineer specializing in developer-experience tooling and CLI session
management. You have shipped production systems in this domain and you know the gap between what
looks rigorous on paper and what actually holds up. You call out that gap directly. When you cannot
verify a claim, you say so explicitly rather than waving past it. Your judgment is the product, not
a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous.

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit the AI-CLI-208 fix (files: `src/ai_cli/session.py`, `tests/test_session.py`,
`tests/test_session_launch_integration.py`) at commit `2b3a6b2` in
`/Users/sergeiwallace/projects/ai-cli-utils/.worktrees/ai-cli-1` against the following scope on
these validation dimensions:

SCOPE: newly allocated tmux session names, `ai_name` (worktree directory name), and the CC
session's `customTitle` (iTerm2 tab/pane title, set from `ai_name`) must always be lowercase,
regardless of the fleet-registry prefix's registered casing (e.g. a registry prefix of "APP" must
never produce a new worktree/session/title containing "APP" — only "app"). Meanwhile
`resolve_project_prefix()` and `get_project_prefix()` must keep returning the registry's raw,
unmodified value for any other consumer (their own docstrings and any bd-task-id-prefix use are
out of scope for lowercasing). AI-CLI-206's case-insensitive RESUME matching (commit `2519721`,
same file: `_resolve_explicit_tmux_slot`, `_resolve_explicit_bare_slot`, `_matching_tmux_sessions`,
`_matching_worktrees`, `find_next_index`, `_find_next_index_from_worktrees`) — which intentionally
preserves whatever casing an *existing* worktree/session already has when reusing it — must be
completely unaffected by this fix.

  1. Internal Consistency (IC-N): does the target contradict itself? Cross-reference every section
     against every other.
  2. Spec / AC Compliance (JA-N): does it satisfy every AC below?
  3. Domain Validity (DV-N): are the engineering choices defensible against the live code (e.g. is
     lowercasing applied at every code path that constructs a NEW name, and nowhere it shouldn't
     be)?
  4. Independent Findings (F-N, open scope): surface anything else that matters — missing edge
     cases, undocumented assumptions, contradictions with existing code, test gaps.

Acceptance criteria to verify:
- A newly created worktree directory name is always lowercase, regardless of the fleet-registry
  prefix's registered casing.
- A newly created tmux session name is always lowercase (except the engine tag c/g and the -r-
  remote marker, which are already lowercase).
- The CC session's customTitle (iTerm2 tab/pane title, set from ai_name) is always lowercase.
- resolve_project_prefix()/get_project_prefix()'s return value is UNCHANGED (still returns the
  registry's literal casing).
- No regression to AI-CLI-206's fix (case-insensitive resume matching for a legacy
  uppercase-cased worktree/session must keep working exactly as it does today).
- A regression test exists: a fleet-registry prefix registered uppercase; a brand new session
  launch (no pre-existing worktree/session for that slot); asserts the resulting worktree dir,
  tmux session name, and ai_name/title are all lowercase.
- Check for any OTHER place in the codebase that constructs a worktree name, tmux session name, or
  CC title from a project prefix outside of `build_session_name()` — if one exists and was missed,
  that's a finding.
- Check `_prefix_from_session_name()`, `resolve_session()`, `find_recent_session()`, and any
  iterm2.py consumer of `ai_name` for a casing assumption that this fix's lowercasing could now
  violate (e.g. a comparison against a raw, still-uppercase `project_prefix` that no longer
  matches the now-lowercased `ai_name`).

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line).

For each finding, supply:
  - File:line reference.
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

You MUST run a Verification Matrix on at least 5-10 of your own findings (or all of them if fewer
than 5): re-run the verification command and record the actual output. A finding without a
reproduced verification command is a hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code and tests the target references, modifies, or makes claims about. This is a
completeness requirement, not a sampling exercise. Bias toward reading too much code rather than
too little.

For every symbol / function the target references, run `grep -r <symbol> <src-roots>` to surface
every call site. Add anything that surfaces to your read list before producing findings.

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

### Audit format (read FIRST — this is how to WRITE the audit)

0. `docs/audits/TEMPLATE.md` in this repo if present; otherwise
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`. Conform your output to it.

### Primary subject

1. `src/ai_cli/session.py` — read in full, especially `get_project_prefix()`,
   `build_session_name()`, `find_next_index()`, `_find_next_index_from_worktrees()`,
   `_resolve_explicit_tmux_slot()`, `_resolve_explicit_bare_slot()`, `_matching_tmux_sessions()`,
   `_matching_worktrees()`, `_prefix_from_session_name()`, `resolve_session()`.
2. `tests/test_session.py` — `TestGetProjectPrefix`, `TestFindNextIndex`, and every test touching
   `build_session_name`.
3. `tests/test_session_launch_integration.py` — every test using `build_session_name` /
   `_do_session_launch` with a registered prefix.
4. `tests/test_bare_worktree.py`, `tests/test_worktree_container_collision.py`,
   `tests/test_main.py` — AI-CLI-206's regression tests; verify none of them silently now assert
   stale (pre-lowercasing) expectations that happen to still pass for the wrong reason.

### Consumers of ai_name / session naming (pattern-consistency review)

5. `src/ai_cli/main.py` — every call site of `build_session_name`, and where `ai_name`/`session_id`
   flow into `_iterm2._emit_iterm2_profile_setup`, `create_worktree`, session-map writes.
6. `src/ai_cli/iterm2.py` — `_emit_iterm2_profile_setup`, `_resolve_iterm2_config`,
   `_assign_iterm2_color_slot`, and anywhere `ai_name` is used for a profile name, icon, or title.
7. `src/ai_cli/config.py` — `resolve_project_prefix()`, `get_project_prefix()`'s docstring claim
   ("Worktree names and custom session titles are both built from this value") — verify it's still
   accurate post-fix or needs updating.

### bd issue

8. AI-CLI-208 (external ref; hash id AI-CLI-ms2i) — full description and acceptance criteria (see
   Scope above; also `bd show AI-CLI-ms2i` if `bd` is reachable in your environment — otherwise
   rely on the Scope section above, which reproduces it).

## Output

Write findings into this audit doc following the Round 1 section structure:
  R1 Summary → R1 Findings (IC / JA / DV / F tables + detailed F-N subsections) → R1 Resolution
  Pass → R1 Verification Matrix → AD-N entries in "Decisions Requiring Team Input" if any →
  Already-Correct Items.

Append a row to the Audit Log when done. Update the Status Summary's cross-round counts and
ship-readiness verdict.

Never fabricate evidence to satisfy a section. Empty findings sections are honest if nothing was
found; faked findings are not. Cite file:line for every codebase claim.

## Anti-patterns (avoid)

- Code-only check that ignores test file changes.
- Skipping the iterm2.py / main.py consumer chain and trusting session.py alone.
- Inline fixes without commit hashes recorded in Resolution Pass.
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling / neighbor code for pattern-consistency.
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0. An UNFILLED template passes all four — grep for a finding heading, or run
  ai-harness/scripts/check_audit_doc_filled.py.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** Codex audit role (`cx audit`, effort: high — fresh invocation for independent
verification)

**Date:** 2026-08-10 (post-Round-1)

```text
You are a principal staff engineer specializing in developer-experience tooling and CLI session
management (same domain as Round 1, a fresh invocation for independent verification). You are
reading the Round 1 audit of the AI-CLI-208 fix — see the Round 1 Reviewer Prompt above in this
same audit doc. This is the Round 2 verification pass.

Your task is to verify that EVERY Round 1 finding (IC-N / JA-N / DV-N / F-N) and EVERY AD-N
decision has been correctly applied to the target. You will also surface NEW issues (N-N) that the
Round 1 fixes themselves introduced.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for the next round to apply.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each Round 1 finding:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option.

For NEW issues: re-read the target sections Round 1 modified. Look for stale cross-references
introduced by Round 1, Resolution Pass claims that didn't actually land, Round 1 fixes that
introduced new contradictions, and draft-author scaffolding left over from the Round 1 edit pass.

Also independently re-run: `uv run ruff check src/ tests/`, `uv run ruff format --check src/
tests/`, and `uv run pytest -q` (the FULL suite) in
`/Users/sergeiwallace/projects/ai-cli-utils/.worktrees/ai-cli-1`, and quote the final summary line.
If your sandbox cannot bind local tmux sockets, say so explicitly rather than reporting a false
pass or a false fail on tmux-dependent tests.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → R2.1 IC/JA/DV verification table (PASS/FAIL/PARTIAL + evidence) → R2.2 F-N
  verification table → R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. `docs/audits/TEMPLATE.md` in this repo, or (if not present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`.
1. `src/ai_cli/session.py`, `tests/test_session.py`, `tests/test_session_launch_integration.py` —
   the files Round 1 covered.
2. THIS AUDIT DOC — the Round 1 section is your verification checklist.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->
