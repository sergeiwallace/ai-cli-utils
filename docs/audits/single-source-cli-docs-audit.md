---
title: single-source-cli-docs audit
category: audit
tags: [audit]
status: stub
date: 2026-11-11
source: "audit-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---
<!-- Canonical Jinja source: STUB.md.jinja. This direct-copy stub is retained for consumers that have not migrated to rendering it. -->

# single-source-cli-docs audit

**Status:** stub

**Created:** 2026-11-11

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

<!-- FILL THIS IN as you write. The entries below are this stub's own placeholder sections, so
     they already resolve; as you add, rename, or remove a heading, update this list in the same
     edit. A ToC that lists headings the doc does not have, or omits ones it does, is worse than
     no ToC because it looks like working navigation.
     The doc's D5 (c) decision: list EVERY `## ` and EVERY `### ` heading in the real doc, with
     GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate
     in-window (incl. VS Code Remote-SSH). If all-`###` proves too noisy, fall back to D5 (a)
     "meaningful `###`", a deterministic OR-rule: include a `###` when it (1) has child `####`,
     (2) its body ≥ ~8-10 lines, (3) its parent `##` is allowlisted, or (4) matches a pattern.
     Hand-maintained. Verify with
     `scripts/check_doc_toc.py <this-doc>`, which fails on an anchor that does not resolve.
     Manual `<!-- toc:skip -->` / `<!-- toc:include -->` override. -->
  <!-- AIH-52: every AD-N detail heading requires a ToC row; generate with
       `python3 scripts/check_doc_toc.py --fix-decision-rows <doc>`, linking each
       decision as `  - [AD-N: Name](#ad-N)`, the STABLE short id, NOT the heading auto-slug.
       AD-N headings carry a mutable status suffix (enumerated once, at TEMPLATE.md's AD-1
       skeleton) that CHANGES the auto-slug on resolution; the `<a id="ad-N"></a>` anchor is the
       durable jump target. -->

- [Scope](#scope)
- [Status Summary](#status-summary)
- [Audit Log](#audit-log)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## Scope

### Founding ask and alignment

`founding_ask_ref`: [FILL: exact `canonical_root_json` and `founding_ask_ref_sha256` | N/A with reason]

[FILL: When this `founding_ask_ref` is `N/A` for a Tier 1 dispatch with no `canonical_root_json`, paste the literal `verbatim_exact_ask` from the worker brief directly below in a fenced `text` block. Never replace it with a summary or a reference to the transient brief. Do not add this block for Tier 0 or Tier 2/3.]

Read every authoritative raw FAR entry, including each re-scope entry. The requirements index is
non-authoritative; report any omitted, dropped, narrowed, broadened, rejected, deferred, or
inverted entry as a canonical ordinary `JA-N` `open-blocking` finding. Compare raw ask and target
artifact during every round, including every AI-resolved Decision and AD-N: an AI decision never
drops, narrows or contradicts an explicit founding-ask requirement, so a Decision that does is a
`JA-N` `open-blocking` finding even when its coverage row names it (decision-framework.md § Gate 0;
run `scripts/far_fidelity.py check --doc <target>`). Exact successor-brief authorization occurs only after convergence in
the driver-owned boundary finalizer; this document's rendering never authorizes a transition.

| Authoritative raw entry | Audit coverage |
|---|---|
| [FILL: one row per entry id] | [FILL: finding/evidence or explicit disposition] |

[What this audit is targeting: design doc, plan doc, shipped code, etc.]

<!-- /doc:region name="scope" -->

## Run Ledger

ledger-version: 1

| Field | Value |
|---|---|
| Stage cursor | `doc-audit done: halted-recurrence; round 2` |
| Promotion | `blocked` |
<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Loop state:** halted-recurrence — NOT promotion ready

| Loop | Round | Target digest | New CRITICAL | New MAJOR | New MINOR | Open blocking | Scope | Stable |
|---|---:|---|---:|---:|---:|---|---|---|
| doc-audit | 2 | sha256:5803fee5dc2a68e87af93f25f95f37aca7cbc6b2ec4df12f7a19f0471f8d9f11 | 0 | 1 | 0 | round2:N-1 | verify | no |
<!-- /doc:region name="loop_receipt" -->
<!-- doc:region name="round_1_findings" kind="replaceable" -->

<!-- ModeDependentKind: REPLACEABLE while round 1 is active; IMMUTABLE once
     the round is committed (validator infers from doc body state).
     Deliberately carries NO `## Round N` heading: the audit-loop driver owns
     round headings and refuses a carrier that already has one
     (scripts/audit_loop.py::_prepare_round). -->

(none yet)

<!-- /doc:region name="round_1_findings" -->

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|

<!-- /doc:region name="audit_log" -->

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

<!-- The reviewer prompt(s) below are the canonical scaffold (kept in sync with
     docs/audits/TEMPLATE.md). Replace the ALL-CAPS placeholders before launching the audit
     agent, and delete the Round 2 appendix if the audit will be single-round. Frozen at
     stub creation. -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** <!-- family and model: a family other than the author's (Codex for a Claude-written target, Claude for a Codex-written one); the same family only when no other route works, recorded as the weaker check; scripts/audit_loop.py and /audit-review route only to Codex today, so a Codex-written target reviewed through them is that weaker check -->

**Date:** 2026-11-11

```text
You are a ROLE-DOMAIN-EXPERT (e.g. "senior data-platform architect with deep hands-on
expertise in distributed systems, data-pipeline design, and reliability engineering" /
"principal staff engineer specializing in developer experience and AI-augmented software
development at scale"). You have shipped production systems in this domain
and you know the gap between what looks rigorous on paper and what actually holds up. You call out
that gap directly. When you cannot verify a claim, you say so explicitly rather than waving past
it. Your judgment is the product, not a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous. (If the orchestrating workflow
instead wants findings reported-not-applied, follow that override: write only the audit doc and
tag each finding [AUTO-FIX] / [NEEDS-DECISION].)

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit TARGET-ARTIFACT at TARGET-COMMIT against SCOPE-DEFINITION on the following validation
dimensions:

  1. Internal Consistency (IC-N): does the target contradict itself? Cross-reference every section
     against every other.
  2. Spec / AC Compliance (JA-N): does it satisfy every authoritative requirement (spec, Jira ACs,
     template conformance, AC writing practices)?
  3. Domain Validity (DV-N): are the algorithmic / scientific / engineering choices defensible
     against the cited research / the live code?
  4. Independent Findings (F-N, open scope): surface anything else that matters — missing
     prerequisites, undocumented assumptions, contradictions with existing code, regulatory gaps.
     Use your own senior judgment and follow any lead the audit turns up.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line). Every AD-N Decision line
must be followed by TEMPLATE.md's fixed-key `decision-record` HTML comment: chosen-option and,
when AI-resolved, family, concrete model ID, effort, and profile/persona.
When a finding or AD-N recommends an externally sourced idea, tool, repo, or pattern, apply
`docs/procedures/source-vetting-and-corroboration.md` and record the evidence route or exploratory
status; a repository name alone is not proof of a best practice.

For each finding, supply:
  - File:line reference (or doc-section).
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

When the brief names a rubric, return one verdict per rubric row in scope. Give every verdict, a
pass included, the verbatim quote, screenshot path or measurement it rests on. A verdict with no
evidence is not a verdict.

Length is not a merit. Do not credit a document, a section or a finding for its length, and do not
credit prose for reading like your own. Model judges favour longer answers and their own family's
writing (Zheng et al. 2023), so judge each row on its evidence alone.

For driver-owned rounds, the prompt includes a JSON example generated directly from
`scripts.audit_receipt.EVENT_PAYLOADS` and its finding validator. Use that injected example as
the only payload contract; this canonical document intentionally carries no hand-maintained JSON
copy. The driver overwrites `scope` and `source_event_id` with mechanically
derived values before validation.

Append exactly one `### R<N> Receipt Payload Projection` heading followed by one fenced `json`
object. Do not emit an `intent_alignment` projection during a round: it cannot authorize a
transition until the exact successor brief exists. Findings use stable keys, lowercase severity, a
non-empty reachable behavior, and no per-member digest. When non-null, `cluster.spec_version`
and `cluster.invariant_id` must match `^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$`; for example,
`"job-feedback-taxonomy-curation-design@27a0571"` is a valid `spec_version`.

You MUST run a Verification Matrix on at least 5-10 of your own findings: re-run the verification
command and record the actual output. A finding without a reproduced verification command is a
hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code, schemas, configuration, prompts, and tests that the target artifact
references, modifies, replaces, extends, makes claims about, or proposes new behavior next to.
This is a completeness requirement, not a sampling exercise.

**Bias toward reading too much code rather than too little.** It is much better to read code that
turns out to be irrelevant than to miss code that contains a finding the audit should have
surfaced. If you are uncertain whether a file is relevant: read it.

For every symbol / function / class / module / config key / CLI command the target references, run
`grep -r <symbol> <src-roots>` to surface every call site and every related file. Add anything
that surfaces to your read list before producing findings. Repeat for sibling / neighbor code that
implements the same pattern the target proposes (existing graph nodes if it proposes a new node;
existing CLI commands if it proposes a new command; existing prompts in the same directory).

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

The list below is a starting set. During the audit, expand it with anything that turns up from the
grep / pattern-consistency reads above. Better to add 10 files that turn out irrelevant than miss
1 file that contained a finding.

If a file is missing from the active worktree, search sibling worktrees at `.claude/worktrees/*/`
and read it from there before concluding it is missing.

### Audit format (read FIRST — this is how to WRITE the audit)

0. The audit template itself. Read it before writing anything so your output matches the required
   structure: the multi-round append-only model, the finding-ID taxonomy (IC-N / JA-N / DV-N /
   F-N / N-N / AD-N), the severity terminology, and the Decisions / Outstanding Issues /
   Already-Correct / Sign-Off / Verification-Matrix / Audit-Log / Appendix sections. **Look for
   `docs/audits/TEMPLATE.md` in this repo first; if it is not present (not every repo has merged
   the latest template to its main yet), read the canonical copy at
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`.** Conform your output to it.

### Primary subject

1. TARGET-PATH — the artifact under audit (read in full, first).

### Authoritative requirements

2. PATH — spec / standard / source-of-truth sections to read.

### Research / evidence base

3. PATH — what to look for (read findings + citations, not just conclusions).

### Plan doc / design doc (parent context)

4. PATH — what to verify.

### Existing code / schemas / prompts / configs / tests (lean toward over-listing)

List every source file, schema, config, prompt, or test the target references, modifies, replaces,
extends, or makes claims about — plus sibling / neighbor files for pattern-consistency review.
When in doubt: include it. Expand this list during the run.

5. PATH — what the target claims about it (or "sibling pattern for X").
... — extend as needed; no upper bound

### Jira issues (read issue AND all comments)

N. [JIRA-KEY](URL) — AC source (use the next number after the last code entry).

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

- Code-only check that ignores Approval Logs in linked plan docs.
- Frontmatter-only status check that ignores doc-body completion signals.
- Skipping linked docs and trusting the parent-doc description alone.
- Partial read of long docs — Approval Logs and sign-off are at the bottom.
- Inline fixes without commit hashes recorded in Resolution Pass.
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling / neighbor files for pattern-consistency, not just the
  named symbols. A missed finding because you didn't read a relevant file is the audit's most
  serious failure mode.
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0. An UNFILLED template passes all four (AIH-762: 42110 bytes, fresh, rc=0, and larger
  than several real audits) - grep for a finding heading, or run
  ai-harness/scripts/check_audit_doc_filled.py.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** <!-- family and model: a family other than the author's (Codex for a Claude-written target, Claude for a Codex-written one); the same family only when no other route works, recorded as the weaker check; scripts/audit_loop.py and /audit-review route only to Codex today, so a Codex-written target reviewed through them is that weaker check; ideally a fresh agent for independent verification -->

**Date:** 2026-11-11 (post-Round-1)

```text
You are a ROLE-DOMAIN-EXPERT (same domain as the prior rounds, ideally a fresh agent / model for
independent verification). You are reading the audit history of TARGET-ARTIFACT. This is a
later-round verification pass.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

## Scope guard — full open MUST-fix backlog

`OPEN-MUST-FIX-BACKLOG` must be the full current list of every unresolved item marked **MUST be
fixed before merge** anywhere in the prior audit history, with its ID and latest verification
status. Your task is to verify that EVERY item in that backlog, including every applicable finding
(IC-N / JA-N / DV-N / F-N) and AD-N decision, has been correctly applied to the target. If the
filled list omits an older open MUST-fix item you find in the audit history, add it to your output;
do not accept a narrowed scope. The immediately preceding round's new findings are additive only,
never a replacement for the full backlog.

You will also surface NEW issues (N-N) that prior fixes themselves introduced. Any FAIL or PARTIAL
backlog item remains in the open MUST-fix backlog and must be carried into every subsequent
re-verification and fix scope until it passes.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for Round 3 to apply. The point of an append-only verification round
  is so the NEXT round can tell what was actually applied vs what the verifier patched.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each open MUST-fix backlog item:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections prior fixes modified. Look for stale cross-references
introduced by those fixes, Resolution Pass claims that didn't actually land, contradictions those
fixes introduced, and draft-author scaffolding left over from the edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → full open MUST-fix backlog verification table (PASS/FAIL/PARTIAL + evidence) →
  R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

For a driver-owned round, use the schema-generated payload example injected into the live prompt
and append exactly one findings projection. The driver supplies the current round, whole-target
digest, derived scope, complete backlog, and the validated founding-ask carrier. When non-null,
`cluster.spec_version` and `cluster.invariant_id` must match
`^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$`; for example,
`"job-feedback-taxonomy-curation-design@27a0571"` is a valid `spec_version`.

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. The audit template — `docs/audits/TEMPLATE.md` in this repo, or (if not yet present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md` — so your Round 2 section follows
   the required append-only structure.
1. TARGET-PATH — the artifact being verified. Read every section prior fixes touched.
2. THIS AUDIT DOC — its full history is your verification checklist; derive and check the complete
   `OPEN-MUST-FIX-BACKLOG`, not only the preceding round.
3. RELATED-DOC-OR-CODE — consult if a backlog claim about it might be contradicted by what the
   code/doc actually says.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->

## Round 1 — Full Audit

**Round 1 auditor:** `gpt-6.1-sol`

**Round 1 date:** 2026-11-11

**Round 1 scope:** Full independent audit of `docs/designs/single-source-cli-docs.md` against
A001, the repository's public-package and CLI conventions, the cited research, the current Click
command tree, parser implementations, tests, hooks, and CI configuration.

### R1 Summary

Seven `MAJOR` findings are open and blocking. The design narrows "full descriptions" to first-sentence
summaries, exposes a write-capable wiki workflow to non-main manual-dispatch refs, contains mutually
incompatible parser-parity criteria, omits the dynamic named-layout behavior from its replacement
inventory, would reject valid pass-through CLI examples, treats an unresolved wiki-token capability
as a fixed invariant, and embeds private operational metadata in a public document. No target edits
were authorized or made. Implementation is not ready to start.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|---|---|---|
| IC-1 | FAIL — `MAJOR`, open-blocking | T-1.3 requires parity for every form the hand parsers accept, then requires unknown flags to fail (`docs/designs/single-source-cli-docs.md:349-353`); `ai ps` currently accepts and ignores unknown flags (`src/ai_cli/process_hygiene.py:790-795`). |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|---|---|---|
| JA-1 | FAIL — `MAJOR`, open-blocking | A001 asks for "full descriptions for each command"; the design renders only the first sentence of the first paragraph in `ai -h` (`docs/designs/single-source-cli-docs.md:164-166`). |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|---|---|---|
| DV-1 | FAIL — `MAJOR`, open-blocking | The design requires wiki publication using only `GITHUB_TOKEN` (`docs/designs/single-source-cli-docs.md:448-453`), while its own evidence says support is disputed and only a live run settles it (`docs/research/cli-docs-generation-tools.md:327-332,409-413`). |

#### Independent Findings (F-N)

| ID | Verdict | Evidence |
|---|---|---|
| F-1 | FAIL — `MAJOR`, open-blocking | Manual dispatch is allowed for a write-capable publication job without a main-ref guard (`docs/designs/single-source-cli-docs.md:220-225,482-489`). |
| F-2 | FAIL — `MAJOR`, open-blocking | The replacement inventory omits `ai layout <name>`, although the current parser treats every non-reserved first argument as a layout name (`src/ai_cli/layout.py:393-425`; design at lines 959-969). |
| F-3 | FAIL — `MAJOR`, open-blocking | T-2.4 requires every documented option to be declared by Click, but session launch commands intentionally accept and forward undeclared provider options (`src/ai_cli/main.py:5606-5610,6381-6404`). |
| F-4 | FAIL — `MAJOR`, open-blocking | The public design embeds populated private-repository/task fields and links an operational run ledger (`docs/designs/single-source-cli-docs.md:16-18,41`), contrary to the binding public-package rule. |

#### JA-1: Root help still does not show each command's full description — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:82-90,159-175`

**Evidence:**

A001 asks why root help does not show the "full descriptions for each command." The design instead
states:

> "The summary of a command is computed by one function, `command_summary(command)` ... otherwise
> the first sentence of the help's first paragraph, never shortened."

It then changes `format_commands` to print that summary. For example, the current `ai c` help is a
multi-sentence description (`src/ai_cli/main.py:6381-6388`), so the proposed root row still omits
all but its first sentence.

**Why it matters:** The central user-visible requirement remains unmet even if implementation
exactly follows the design. Calling an untruncated first sentence a "full description" is an
unauthorized narrowing of A001.

**Verification command:**

```bash
sed -n '82,90p;159,175p' docs/designs/single-source-cli-docs.md
sed -n '6381,6388p' src/ai_cli/main.py
```

**Verification note:** `CONFIRMED` against the target and current command declaration at
`f7b3ccc580a369d0a2226a07d56adc6c427783f3`.

**Recommendation:** Specify and test a root-help representation that includes each visible
command's complete Click help description, not only `command_summary()`. If the intended product is
only untruncated summaries, obtain an explicit human re-scope before implementation.

#### F-1: Manual dispatch can run branch-selected code with the wiki write token — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:220-225,448-455,482-489`

**Evidence:**

The design says the workflow runs "on manual dispatch," grants `contents: write`, builds the wiki
with repository code, and "replaces its contents." The risk table asserts the job "runs only on
`main` and dispatch" but supplies no condition requiring `github.ref == 'refs/heads/main'`.
GitHub's official `workflow_dispatch` event table states that `GITHUB_REF` is the "Branch or tag
that received dispatch," and official context documentation recommends a job-level `if` check for
`refs/heads/main`.

Source: <https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>

**Why it matters:** A maintainer can dispatch a non-main ref, causing unmerged generator or shell
code from that ref to execute with a token that can replace the live wiki. The stated mitigation
("runs no code from pull requests") does not prevent this reachable path.

**Verification command:**

```bash
sed -n '220,225p;448,455p;482,489p' docs/designs/single-source-cli-docs.md
```

**Verification note:** `CONFIRMED`; the branch-selectable dispatch semantics were checked against
the cited official GitHub Actions documentation.

**Recommendation:** Add an acceptance criterion and workflow design requiring the publication job
to run only when `github.ref == 'refs/heads/main'`; explicitly check out the trusted main commit
before executing the builder. If manual dispatch is retained, it must be a main-only retry trigger.

#### IC-1: Parser parity and unknown-token rejection contradict each other — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:346-355`;
`src/ai_cli/process_hygiene.py:775-795`

**Evidence:**

T-1.3 first requires:

> "When any invocation form the current hand parsers accept is used ... the system shall call the
> same implementation with the same values as before."

The next AC requires:

> "If an unknown action or flag is given, then the system shall exit non-zero ... rather than
> ignoring it."

The current `ai ps` parser loops over all remaining tokens, handles only four known spellings, and
has no `else`; therefore `ai ps --unknown` is accepted, ignored, and proceeds to list processes.

**Why it matters:** No implementation can satisfy both literal ACs for the same invocation. A parity
test can require the old success behavior while the rejection test requires failure.

**Verification command:**

```bash
sed -n '346,355p' docs/designs/single-source-cli-docs.md
sed -n '775,796p' src/ai_cli/process_hygiene.py
```

**Verification note:** `CONFIRMED` by control-flow inspection; no absence inference is involved.

**Recommendation:** Inventory accepted-valid behavior separately from accidentally tolerated input.
Mark unknown-token acceptance as intentionally dropped, with the reason, then limit parity ACs to an
enumerated table of valid forms.

#### F-2: The replacement inventory drops named layout application — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:346-355,959-969`;
`src/ai_cli/layout.py:393-425`

**Evidence:**

D-8 inventories layout as:

> "`layout list|validate|profiles`"

and proposes "one subcommand per action." The current parser's public surface is documented in its
own usage as `ai layout <name|list|validate <name>|profiles <name>>`; any first token other than the
three reserved actions calls `cmd_layout_apply(sub)`.

**Why it matters:** An implementation and parity suite derived from the stated inventory can convert
only the three named actions and make `ai layout my-layout` an unknown Click subcommand, breaking the
command's primary behavior.

**Verification command:**

```bash
sed -n '959,970p' docs/designs/single-source-cli-docs.md
sed -n '393,425p' src/ai_cli/layout.py
```

**Verification note:** `CONFIRMED` against the replacement design and current dispatcher.

**Recommendation:** Add `<name> -> cmd_layout_apply(name)` to the behavior inventory and a dedicated
parity AC. Specify the Click shape that preserves a dynamic name alongside the reserved operations,
including ambiguity and missing-argument behavior.

#### F-3: The documentation example gate rejects supported pass-through options — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:405-413`;
`src/ai_cli/main.py:5606-5610,6381-6433`

**Evidence:**

T-2.4 requires:

> "every option token ... shall be a declared option of that command"

but `c`, `g`, `p`, and `cx` use `ignore_unknown_options=True` and `allow_extra_args=True`. This is an
intentional provider-argument forwarding surface; existing regression-test documentation also says
engine flags are forwarded (`tests/test_session_dry_run.py:3-7`).

**Why it matters:** A valid guide example that passes a provider-specific option through a launcher
must fail the proposed gate or be exempted. Exempting all pass-through examples makes the claimed
narrative-drift enforcement blind exactly where spelling validation is most useful.

**Verification command:**

```bash
sed -n '5603,5610p;6381,6433p' src/ai_cli/main.py
sed -n '405,413p' docs/designs/single-source-cli-docs.md
sed -n '1,16p' tests/test_session_dry_run.py
```

**Verification note:** `CONFIRMED` against Click context settings and the existing regression test.

**Recommendation:** Define a token classifier with at least three outcomes: locally declared,
intentional provider pass-through, and invalid. Validate local options mechanically, preserve and
record pass-through tokens without calling them invalid, and reserve exemptions for deliberately
invalid examples.

#### DV-1: Wiki-token capability is an unresolved premise, not a design invariant — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:220-225,448-453`;
`docs/research/cli-docs-generation-tools.md:327-332,409-413`

**Evidence:**

The Phase 3 AC requires publication "using only the workflow's `GITHUB_TOKEN`." The cited research
says:

> "One smaller action says the default token does not work. The first real run settles it for this
> repository."

and identifies a fine-grained token as the fallback. The design neither represents the capability
as unverified nor defines the authorization gate if the fallback is needed.

**Why it matters:** Implemented literally, Phase 3 can reach a permanent red workflow with no
in-scope recovery path. Quietly adding a personal token would exceed the task's credential and
outward-action authority.

**Verification command:**

```bash
sed -n '220,225p;448,453p' docs/designs/single-source-cli-docs.md
sed -n '327,332p;409,413p' docs/research/cli-docs-generation-tools.md
```

**Verification note:** `CONFIRMED` that the load-bearing capability is unresolved; whether the token
will fail for this repository remains `PLAUSIBLE`, not asserted as fact.

**Recommendation:** Mark default-token publication as a hypothesis with a live verification gate.
Specify that failure stops implementation and requests owner direction; do not silently provision a
personal token. Define the verified fallback only after authorization.

#### F-4: The public design contains private operational metadata — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:16-18,41`;
`tests/test_public_repo_hygiene.py:264-269,337-361`

**Evidence:**

The canonical-root JSON in the public document contains populated `"repository"` and `"task_id"`
fields for a private environment, and the document links an internal run-ledger filename. The
repository policy forbids private project names, prefixes, and internal tracking identifiers in
public documentation. The current hygiene guard explicitly excludes `docs` from its private-
environment and tracker-ID scans, so a green guard does not verify this document class.

**Why it matters:** Merging publishes internal workflow identifiers that have no value to an
open-source reader and violates a binding release rule. Existing tests provide false reassurance
because this exact path is outside their enforcement scope.

**Verification command:**

```bash
rg -n '"repository"|"task_id"|\*\*Run Ledger:\*\*' docs/designs/single-source-cli-docs.md
sed -n '264,269p;337,361p' tests/test_public_repo_hygiene.py
```

**Verification note:** `CONFIRMED` by direct inspection of the public artifact and the guard's
scanned-path definitions. Forbidden values are intentionally not duplicated into this audit.

**Recommendation:** Keep private carrier and run provenance in the private system of record. In the
public design, retain only an opaque content digest or a generic statement of provenance, and add a
docs-scoped public-hygiene check that can fail on future leaks.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---|---|---|
| JA-1 | OPEN — target author required | Restore literal full-description coverage or obtain explicit human re-scope. |
| F-1 | OPEN — target author required | Add a main-ref publication guard and trusted checkout AC. |
| IC-1 | OPEN — target author required | Separate preserved valid behavior from intentionally rejected tolerated input. |
| F-2 | OPEN — target author required | Inventory and preserve dynamic named-layout application. |
| F-3 | OPEN — target author required | Model provider pass-through tokens separately from invalid options. |
| DV-1 | OPEN — target author required | Add an unverified-capability/live-gate path for wiki authentication. |
| F-4 | OPEN — target author required | Remove private operational metadata and close the documentation guard gap. |

No inline fixes were made; this round was read-only.

### R1 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---|---|---|---|---|
| JA-1 | `sed -n '82,90p;159,175p' docs/designs/single-source-cli-docs.md` | Root output uses full descriptions | It explicitly derives and renders first-sentence summaries | ✅ reproduced |
| F-1 | `sed -n '220,225p;448,455p;482,489p' docs/designs/single-source-cli-docs.md` | Main-only guard is specified | Manual dispatch and write permission are specified; no ref guard is present | ✅ reproduced |
| IC-1 | `sed -n '346,355p' ...; sed -n '775,796p' src/ai_cli/process_hygiene.py` | Parity and rejection requirements can both hold | Target requires both; parser has no unknown-flag rejection branch | ✅ reproduced |
| F-2 | `sed -n '959,970p' ...; sed -n '393,425p' src/ai_cli/layout.py` | Inventory includes named layouts | Design lists three actions; source has a dynamic apply fallback | ✅ reproduced |
| F-3 | `sed -n '5603,5610p;6381,6433p' src/ai_cli/main.py` | Every valid option is declared locally | Four launchers deliberately allow and forward unknown options | ✅ reproduced |
| DV-1 | `sed -n '327,332p;409,413p' docs/research/cli-docs-generation-tools.md` | Token capability is verified | Research labels it unsettled pending the first live run | ✅ reproduced |
| F-4 | `rg -n '"repository"|"task_id"|Run Ledger' docs/designs/single-source-cli-docs.md` | No private operational metadata | Populated carrier fields and an internal ledger link are present | ✅ reproduced |

**Verified: 7/7 findings reproduce on commit `f7b3ccc580a369d0a2226a07d56adc6c427783f3`.**

### Decisions Requiring Team Input

None. JA-1 requires either literal compliance or a new human-authored re-scope; it is not an AI
tradeoff decision. DV-1 requires a stop and owner direction only if the live token probe fails.

### Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|---|---|---|---|---|---|
| I-01 | P1 | Restore full-description root help and make its AC falsifiable | JA-1 | Design author | Before implementation |
| I-02 | P1 | Constrain wiki publication to trusted main code | F-1 | Design author | Before implementation |
| I-03 | P1 | Repair parser parity inventory and dynamic layout coverage | IC-1, F-2 | Design author | Before implementation |
| I-04 | P1 | Make example validation aware of intentional pass-through | F-3 | Design author | Before implementation |
| I-05 | P1 | Represent and gate unresolved wiki authentication | DV-1 | Design author | Before Phase 3 |
| I-06 | P1 | Remove private metadata from the public design and enforce the rule | F-4 | Design author | Before merge |

### Already-Correct Items

- ✅ The current tree measurements reproduce: 52 visible top-level commands, 96 visible commands,
  227 visible options, zero explicit `short_help` values, and nine visible commands without help.
- ✅ Root options and all currently visible command options have both short and long forms; the
  direct tree walk reproduced the documented count.
- ✅ The design uses Click declarations as the command-text source and derives the reference from
  the same command objects (`docs/designs/single-source-cli-docs.md:159-190`).
- ✅ Hidden commands and options are explicitly excluded from generated reference output, matching
  Click help visibility (`docs/designs/single-source-cli-docs.md:177-185`).
- ✅ Deterministic LF output is specified, and `.gitattributes:13` enforces LF for text checkouts.
- ✅ Existing CI really runs pytest on Linux, Windows, and macOS (`.github/workflows/ci.yml:74-101,
  119-220,220-270`).
- ✅ The design keeps LLM calls out of generation and requires prose to land in reviewed committed
  sources (`docs/designs/single-source-cli-docs.md:239-247`).

### Anti-Patterns to Watch For

- Do not equate "untruncated" with "full": truncation removal can still omit later sentences.
- Do not treat a parser's tolerated inputs as intended parity behavior; replacement inventories
  must label preserved behavior and intentionally dropped behavior separately.
- Do not use a pull-request trigger exclusion as proof that `workflow_dispatch` executes trusted
  main-branch code.
- Do not promote conflicting third-party reports into a verified platform capability.
- Do not accept a green hygiene test without checking whether its scanned paths include the
  artifact under audit.

### Sign-Off Checklist

- [ ] All seven `MAJOR` findings are fixed and re-audited.
- [x] Verification Matrix run on seven findings; 7/7 reproduced.
- [x] Already-Correct Items populated with grounded evidence.
- [x] No inline target edits were made.
- [ ] At least one append-only verification round confirms the fixes.
- [ ] User reviews and approves final sign-off.

### Appendix: Files Read

**Primary subject:**

- `docs/designs/single-source-cli-docs.md` — full, 1,033 lines.

**Authoritative requirements and standards:**

- `AGENTS.md` — full; public-package, CLI, and test conventions.
- `CLAUDE.md` — full; CI and public-package conventions.
- Canonical audit `TEMPLATE.md` and `STUB.md` — full; taxonomy, structure, decisions, matrix.
- A001 full verified record supplied inline in the audit invocation.

**Research and run context:**

- `docs/research/cli-docs-generation-tools.md` — full, 555 lines.
- The run ledger named by the target — provenance and scope checks.

**Existing code, tests, and configuration:**

- `src/ai_cli/main.py:5603-6460,6880-6940,8195-8320` — Click contexts, root/group
  declarations, session launchers, and self-parsing commands.
- `src/ai_cli/session_commands.py` — group declaration inventory via tree walk and targeted reads.
- `src/ai_cli/process_hygiene.py:1-80,720-875` — `ps` behavior and parser.
- `src/ai_cli/layout.py:1-80,360-425` — layout dispatcher and dynamic-name fallback.
- `tests/test_cli_option_forms.py` — full; option-pair enforcement scope.
- `tests/test_session_dry_run.py:1-100` — intentional provider pass-through behavior.
- `tests/test_public_repo_hygiene.py:1-300,337-560` — public-hygiene rules and scan gaps.
- `.gitattributes` — full; LF policy.
- `.pre-commit-config.yaml` — hook structure and filename-passing conventions.
- `.github/workflows/ci.yml` — three-platform test jobs.
- Official GitHub Actions event and context documentation — manual-dispatch ref semantics.

### Appendix: Commands Run

```bash
git status --short
git rev-parse HEAD
git log --oneline -- docs/designs/single-source-cli-docs.md docs/research/cli-docs-generation-tools.md
rg -n '_ChiefAliasGroup|format_commands|help_option_names|ignore_unknown_options|allow_extra_args' src/ai_cli tests
sed -n '<audited ranges>' docs/designs/single-source-cli-docs.md
sed -n '<audited ranges>' docs/research/cli-docs-generation-tools.md
sed -n '<audited ranges>' src/ai_cli/main.py src/ai_cli/process_hygiene.py src/ai_cli/layout.py
rg -n '"repository"|"task_id"|Run Ledger' docs/designs/single-source-cli-docs.md
```

An in-memory Python tree walk (with no filesystem writes) measured visible commands, groups,
options, missing help, explicit `short_help`, and group classes from `ai_cli.main._cli_group`.

### R1 Receipt Payload Projection

```json
{
  "findings": [
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "ask-drift"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:JA-1",
      "fingerprint": "fp:full-description-narrowed-to-summary",
      "id": "JA-1",
      "reachable_behavior": "Root help omits every sentence after a command help paragraph's first sentence.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "unsafe-workflow"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-1",
      "fingerprint": "fp:wiki-dispatch-non-main-write",
      "id": "F-1",
      "reachable_behavior": "A manual dispatch can execute a selected non-main ref with wiki write authority.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "contradictory-requirements"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:IC-1",
      "fingerprint": "fp:parser-parity-versus-rejection",
      "id": "IC-1",
      "reachable_behavior": "The same tolerated unknown ps flag is required both to preserve success and to fail.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "missing-parity-coverage"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-2",
      "fingerprint": "fp:layout-dynamic-name-omitted",
      "id": "F-2",
      "reachable_behavior": "A named layout can become an unknown command after the Click migration.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "incorrect-behavior"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-3",
      "fingerprint": "fp:docs-validator-rejects-passthrough",
      "id": "F-3",
      "reachable_behavior": "A valid provider pass-through option in documentation fails the proposed drift gate.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "unverified-precondition"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:DV-1",
      "fingerprint": "fp:wiki-token-capability-unverified",
      "id": "DV-1",
      "reachable_behavior": "The Phase 3 workflow can remain unable to publish with no authorized fallback path.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "public-metadata-exposure"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-4",
      "fingerprint": "fp:public-design-private-metadata",
      "id": "F-4",
      "reachable_behavior": "Merging the design publishes private operational identifiers in the public repository.",
      "severity": "major",
      "submitted_severity": "major"
    }
  ],
  "major_nonblocking_justifications": {},
  "new_by_severity": {
    "critical": 0,
    "major": 7,
    "minor": 0
  },
  "scope": {
    "backlog": [],
    "diff_since": null,
    "diff_summary": {
      "changed": false,
      "from": null,
      "to": "sha256:3a33745810c7b8e536380cc3798119c55f64a8475147104dbad0d2569ea0c367"
    },
    "kind": "full"
  },
  "source_event_id": "round-1-audit"
}
```

## Round 2 — Verification

**Round 2 auditor:** `gpt-5`

**Round 2 date:** 2026-11-11

**Round 2 scope:** Verify all seven Round 1 blocking findings against target digest
`sha256:5803fee5dc2a68e87af93f25f95f37aca7cbc6b2ec4df12f7a19f0471f8d9f11`, inspect the
related parser and hygiene code, and surface gaps introduced or missed by the fix. Append-only; no
target edits.

### R2 Summary

All seven Round 1 findings are fixed in the revised design and related hygiene guard. One new
`MAJOR` parity gap, N-1, remains open-blocking: the supposedly explicit self-parser inventory still
omits meaningful `ai sync` flags that the current implementation parses and uses. The design is not
ready for implementation until that inventory and its parity ACs are complete.

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Disposition | Evidence and verification note |
|---|---|---|---|
| IC-1 | PASS | fixed | `docs/designs/single-source-cli-docs.md:357-370` now enumerates valid forms, separately says the old unknown-`ps` and ignored-trailing-token tolerance is intentionally dropped, and gives a reason. This removes the literal parity/rejection contradiction. `CONFIRMED`; N-1 is a separate incompleteness in the revised inventory. |
| JA-1 | PASS | fixed | The source is now `command_description(command, context)` and root/group help must print every sentence and paragraph (`docs/designs/single-source-cli-docs.md:159-169,335-342`), while the reference uses the same full description (`:175-183`). `CONFIRMED` against Click 8.5.0's installed `Group.format_commands`, `Command.format_help_text`, and `HelpFormatter.write_dl`. |
| DV-1 | PASS | fixed | The design now calls default-token wiki push an unverified capability and requires failure to stop for maintainer direction without adding a credential (`docs/designs/single-source-cli-docs.md:225-227,478-481,855-858,925-929`). This matches the unresolved evidence in `docs/research/cli-docs-generation-tools.md:327-332,409-413`. `CONFIRMED`. |

### R2.2 Round 1 F-N verification

| ID | Verdict | Disposition | Evidence and verification note |
|---|---|---|---|
| F-1 | PASS | fixed | The publication job must require `github.ref == 'refs/heads/main'`, check out trusted main before repository code, and skip a non-main dispatch (`docs/designs/single-source-cli-docs.md:218-227,471-485`). `CONFIRMED`. |
| F-2 | PASS | fixed | The valid-form inventory explicitly includes `layout <name>`, and a dedicated AC routes each non-reserved name to `cmd_layout_apply(name)` (`docs/designs/single-source-cli-docs.md:357-364`), matching `src/ai_cli/layout.py:393-425`. `CONFIRMED`. |
| F-3 | PASS | fixed | T-2.4 now classifies tokens as local, intentional pass-through, or invalid and preserves pass-through on `c`, `g`, `p`, and `cx` (`docs/designs/single-source-cli-docs.md:423-434`), matching `SESSION_CONTEXT` and the four launch commands (`src/ai_cli/main.py:5606-5610,6381-6433`). `CONFIRMED`. |
| F-4 | PASS | fixed | The carrier fields and run-ledger link are absent from the design, and the focused guard returns zero findings while retaining a positive control (`tests/test_public_repo_hygiene.py:264-333`). `CONFIRMED` by executing `scan_public_docs_for_private_workflow_metadata(Path.cwd())`, which printed `public provenance findings: 0`. |

### R2.3 AD-N decisions verification

No AD-N decisions were created in Round 1, so there is nothing to verify.

### R2.4 NEW issues surfaced

#### N-1: The revised self-parser inventory still drops meaningful sync flags — `MAJOR`

**Location:** `docs/designs/single-source-cli-docs.md:357-372,1033-1043`;
`src/ai_cli/sync.py:345-353,2493-2496,2560-2569,2856-2873`;
`src/ai_cli/session_script.py:1170-1174`

**What the Round 1 fix claims:**

> "The parsing Con is mitigated by an explicit valid-form inventory and parity ACs for every listed
> action, dynamic layout name, option spelling, no-argument behavior, and required-argument failure."

D-8 also says the change "keeps every accepted form."

**Actual state:** T-1.3 names the sync actions but delegates their surface to "each action's
currently documented short and long options." The current parser explicitly recognizes
`--prefer-remote`, passes its boolean to `apply_pull_files`, and tests it; the revised inventory
never names it. The parser also gives `sync watch --auto` distinct behavior and a shipped session
script invokes it. These are meaningful recognized forms, not the unknown `ps` tolerance the design
explicitly drops.

**Why it matters:** Implemented literally with the new unknown-option rejection, `ai sync pull
--prefer-remote` can become a usage error and lose its requested conflict-resolution behavior. The
design would therefore repeat the replacement-parity omission that its revised inventory claims to
eliminate.

**Verification command:**

```bash
sed -n '354,372p;1027,1043p' docs/designs/single-source-cli-docs.md
sed -n '345,353p;2493,2497p;2560,2570p;2856,2873p' src/ai_cli/sync.py
sed -n '1168,1176p' src/ai_cli/session_script.py
rg -n -- '--prefer-remote|--auto' src/ai_cli/sync.py src/ai_cli/session_script.py tests/test_sync.py
```

**Verification note:** `CONFIRMED`. The command reproduced the omission and the two behaviors;
`--prefer-remote` is parsed at `src/ai_cli/sync.py:352`, consumed at `:2495,2568`, and covered at
`tests/test_sync.py:1787`; `--auto` gates watch startup at `src/ai_cli/sync.py:2869-2873` and is
invoked at `src/ai_cli/session_script.py:1174`.

**Recommended fix (Round 3):** Replace "currently documented" with an action-by-action option
matrix derived from the parsers. Preserve `--prefer-remote` on `pull` with an unused short form such
as `-P`, declare `--auto` as a hidden internal Click option under the repository's long-only hidden
flag exception, and add CLI-entry parity tests for both semantic paths. Explicitly classify any
remaining accepted token as preserved or intentionally dropped with a reason.

### R2 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---|---|---|---|---|
| JA-1 | `sed -n '154,183p;322,350p' docs/designs/single-source-cli-docs.md` | Shared full description feeds help and reference | `command_description()` is shared; AC requires every sentence and paragraph | ✅ |
| F-1 | `sed -n '218,227p;471,485p' docs/designs/single-source-cli-docs.md` | Main-only write job and trusted checkout | Exact ref guard, trusted-main checkout, and non-main skip are required | ✅ |
| IC-1 | `sed -n '354,372p' docs/designs/single-source-cli-docs.md` plus `sed -n '775,796p' src/ai_cli/process_hygiene.py` | Valid parity separated from accidental tolerance | Valid forms are bounded; old unknown-flag tolerance is explicitly dropped | ✅ |
| F-2 | `sed -n '357,364p' docs/designs/single-source-cli-docs.md; sed -n '393,425p' src/ai_cli/layout.py` | Dynamic named layout preserved | AC routes a non-reserved name to `cmd_layout_apply(name)` | ✅ |
| F-3 | `sed -n '423,434p' docs/designs/single-source-cli-docs.md; sed -n '5606,5610p;6381,6433p' src/ai_cli/main.py` | Pass-through is a valid distinct class | Four launcher contexts and the classifier agree | ✅ |
| DV-1 | `sed -n '225,227p;478,481p;855,858p;925,929p' docs/designs/single-source-cli-docs.md` | Unverified capability has a stop gate | Design stops and requests direction without credential fallback | ✅ |
| F-4 | `PYTHONDONTWRITEBYTECODE=1 python` calling `scan_public_docs_for_private_workflow_metadata(Path.cwd())` | Zero target findings | `public provenance findings: 0` | ✅ |
| N-1 | `rg -n -- '--prefer-remote|--auto' src/ai_cli/sync.py src/ai_cli/session_script.py tests/test_sync.py` plus the T-1.3 read | Every meaningful current form is inventoried | Source and tests contain both flags; the design inventory names neither | ✅ reproduced |

**Verified: 8/8 assertions reproduce on target digest
`sha256:5803fee5dc2a68e87af93f25f95f37aca7cbc6b2ec4df12f7a19f0471f8d9f11`.**

### R2 Recommendations

**MUST be fixed before implementation starts:**

- N-1: complete the self-parser option inventory and parity ACs.

**SHOULD be fixed before the design-doc PR merges:** none beyond the blocking item.

**Can be folded into a follow-up:** none.

### R2 Already-Correct Items

- ✅ The literal full-description requirement now governs root and nested group help.
- ✅ Wiki publication is main-ref-only and the unresolved token capability is a live stop gate.
- ✅ Dynamic layouts and provider pass-through tokens now have explicit parity behavior.
- ✅ The public design no longer contains the private workflow carrier, and a positive-control guard
  protects this document.

### R2 Files Read and Commands Run

Read in full: the canonical audit `STUB.md` and `TEMPLATE.md`,
`docs/designs/single-source-cli-docs.md`, and this audit's Round 1 history. Targeted ground-truth
reads covered `docs/research/cli-docs-generation-tools.md:300-430`,
`src/ai_cli/main.py:5588-5620,6368-6445,8180-8310`,
`src/ai_cli/process_hygiene.py:720-825`, `src/ai_cli/layout.py:380-425`,
`src/ai_cli/sync.py:330-360,2470-2575,2850-2880`,
`src/ai_cli/session_script.py:1168-1176`, `tests/test_session_dry_run.py:1-30`, and
`tests/test_public_repo_hygiene.py:1-400`. Commands were the `sed`, `rg`, `git diff`, `git log`,
`git blame`, `sha256sum`, and read-only Python inspections recorded in the matrix; no target edits
or live outward actions were performed.

### R2 Audit Log Entry

| Date | Round | Notes |
|---|---|---|
| 2026-11-11 | 2 | Seven Round 1 findings fixed; one new `MAJOR` open-blocking parity finding (N-1); target unchanged. |

### R2 Receipt Payload Projection

```json
{
  "findings": [
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "unverified-precondition"
      },
      "disposition": "fixed",
      "finding_key": "round1:DV-1",
      "fingerprint": "fp:wiki-token-capability-unverified",
      "id": "DV-1",
      "reachable_behavior": "The design now stops wiki publication and requests maintainer direction when default-token capability fails.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "unsafe-workflow"
      },
      "disposition": "fixed",
      "finding_key": "round1:F-1",
      "fingerprint": "fp:wiki-dispatch-non-main-write",
      "id": "F-1",
      "reachable_behavior": "The write-capable wiki job now requires the main ref and checks out trusted main before repository code runs.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "missing-parity-coverage"
      },
      "disposition": "fixed",
      "finding_key": "round1:F-2",
      "fingerprint": "fp:layout-dynamic-name-omitted",
      "id": "F-2",
      "reachable_behavior": "The revised AC preserves dynamic named-layout application through cmd_layout_apply.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "incorrect-behavior"
      },
      "disposition": "fixed",
      "finding_key": "round1:F-3",
      "fingerprint": "fp:docs-validator-rejects-passthrough",
      "id": "F-3",
      "reachable_behavior": "The revised validator classifies provider pass-through tokens separately and does not reject them as local-option errors.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "public-metadata-exposure"
      },
      "disposition": "fixed",
      "finding_key": "round1:F-4",
      "fingerprint": "fp:public-design-private-metadata",
      "id": "F-4",
      "reachable_behavior": "The public design no longer carries private workflow metadata and a focused positive-control guard protects it.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "contradictory-requirements"
      },
      "disposition": "fixed",
      "finding_key": "round1:IC-1",
      "fingerprint": "fp:parser-parity-versus-rejection",
      "id": "IC-1",
      "reachable_behavior": "The revised AC separates enumerated valid forms from accidental unknown-token tolerance that is intentionally dropped.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "ask-drift"
      },
      "disposition": "fixed",
      "finding_key": "round1:JA-1",
      "fingerprint": "fp:full-description-narrowed-to-summary",
      "id": "JA-1",
      "reachable_behavior": "Root and nested group help are now required to show every command's complete formatted help description.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": null,
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-docs-design",
        "violation_kind": "missing-parity-coverage"
      },
      "disposition": "open-blocking",
      "finding_key": "round2:N-1",
      "fingerprint": "fp:sync-meaningful-flags-omitted",
      "id": "N-1",
      "reachable_behavior": "A user invoking ai sync pull --prefer-remote can receive a usage error and lose the requested conflict-resolution behavior after migration.",
      "severity": "major",
      "submitted_severity": "major"
    }
  ],
  "major_nonblocking_justifications": {},
  "new_by_severity": {
    "critical": 0,
    "major": 1,
    "minor": 0
  },
  "scope": {
    "backlog": [
      "round1:DV-1",
      "round1:F-1",
      "round1:F-2",
      "round1:F-3",
      "round1:F-4",
      "round1:IC-1",
      "round1:JA-1"
    ],
    "diff_since": "sha256:3a33745810c7b8e536380cc3798119c55f64a8475147104dbad0d2569ea0c367",
    "diff_summary": {
      "changed": true,
      "from": "sha256:3a33745810c7b8e536380cc3798119c55f64a8475147104dbad0d2569ea0c367",
      "to": "sha256:5803fee5dc2a68e87af93f25f95f37aca7cbc6b2ec4df12f7a19f0471f8d9f11"
    },
    "kind": "verify"
  },
  "source_event_id": "round-2-audit"
}
```
