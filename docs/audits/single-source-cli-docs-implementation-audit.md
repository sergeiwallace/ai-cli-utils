---
title: single-source-cli-docs audit
category: audit
tags: [audit]
status: stub
date: 2026-11-11
source: "aido-stub"
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
     Hand-maintained: the not-yet-shipped `aido toc check` tool. Verify with
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
| Stage cursor | `impl-audit active; round 1` |
| Promotion | `blocked` |
<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Loop state:** active — NOT promotion ready

| Loop | Round | Target digest | New CRITICAL | New MAJOR | New MINOR | Open blocking | Scope | Stable |
|---|---:|---|---:|---:|---:|---|---|---|
| impl-audit | 1 | sha256:75bb65ec5f2465ac95e326f5068319e751044d37abe674dbca57534b6b3acce3 | 0 | 4 | 1 | round1:F-1, round1:F-2, round1:F-3, round1:JA-1, round1:JA-2 | full | no |
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

### R1 Summary

**Auditor:** GPT-5 (Codex), independent implementation audit

**Date:** 2026-11-11

**Target:** `docs/designs/single-source-cli-docs.md` at
`sha256:75bb65ec5f2465ac95e326f5068319e751044d37abe674dbca57534b6b3acce3`; implementation at
`def9b989bb54dce7bc90db1228dc38e731cd5465`

**Verdict:** **NOT READY.** Four MAJOR and one MINOR findings remain open. The shared Click source,
generated CLI reference, complete command descriptions, deterministic generation, and main-only
publication guards are present. Dynamic layout-name dispatch, Markdown fence parsing, public-content
hygiene, root-option invariant coverage, and output-tree reconciliation are not yet correct.

The driver supplied no backlog findings for this full round, so there are no prior finding keys to
restate. The focused implementation suite passed (`183 passed`), but the five independent probes in
the verification matrix reproduce behavior outside the current regression coverage.

#### Founding-ask and alignment verdicts

The authoritative carrier and its content digest were verified from the invocation. To avoid
republishing private carrier metadata in this public package, this projection records the safe
reference only: `founding_ask_ref_sha256: cc266ebe307ece299aea75e1f3ed34acc3428467545b0a3e654850de2fc4bfca`,
entry `A001`.

| A001 requirement | Verdict | Ground-truth evidence |
|---|---|---|
| “`ai -h`” shows full descriptions | PASS | `src/ai_cli/cli_help.py` supplies `command_description()` and `HelpGroup`; the focused help tests passed, and `docs/designs/single-source-cli-docs.md:129-138,679-680` identifies the implemented tree-wide guard. |
| Options have both short and long names | PARTIAL — **JA-2** | The current command tree is compliant (361 visible option records, including generated help options, with zero missing either form), but `tests/test_cli_option_forms.py:8-14` omits the root command, so the stated invariant is not fully enforced. |
| Help and canonical CLI usage share one source and cannot drift | PASS | `scripts/generate_docs.py:116-125,193-212`, `tests/test_cli_docs.py`, and the generated-docs hook derive and compare the reference and README region from Click's command tree. |
| The wiki is generated from repository sources “where possible” and covers the repository documentation | FAIL — **F-2, JA-1, F-3** | `docs/designs/single-source-cli-docs.md:269-307` requires comprehensive publication. The builder mishandles valid nested fences, publishes material forbidden by the public-package standard, and does not reconcile stale pages in a reused output directory. |
| Generation is programmatic first; an LLM is used only for reviewed source prose | PASS | `docs/designs/single-source-cli-docs.md:77-86,687` and the inspected generator/workflow contain no model call; rendering is offline and deterministic. |
| Research open-source and agentic documentation approaches | PASS | `docs/research/cli-docs-generation-tools.md` records the researched alternatives, licenses, and primary-source evidence; the implemented path uses Click and repository-owned scripts. |
| A hosted documentation website is later work, but this design can feed it | PASS | `docs/designs/single-source-cli-docs.md:85-86` keeps the website out of scope while retaining portable generated Markdown. |
| Task/issue mirroring and public-repository Discussions are separate follow-up work | OUT OF IMPLEMENTATION SCOPE | The design records the boundary at `docs/designs/single-source-cli-docs.md:29-30`. The external task-store assertions were not used as evidence for this implementation verdict. |

#### Implementation-audit checklist verdicts

| Design row | Verdict | Evidence |
|---|---|---|
| 1 — Concrete Design Overview | PASS | `docs/designs/single-source-cli-docs.md:123-181` names the implemented modules, behavior, and deviations. |
| 2 — Complete descriptions; no ellipsis | PASS | `tests/test_cli_help.py` passed its tree and width cases; the implementation uses the shared `HelpGroup`. |
| 3 — Every visible command has a summary | PASS | The tree-walk coverage in `tests/test_cli_help.py` passed. |
| 3a — `ps`, `layout`, and `sync` parsing parity | FAIL — **F-1** | The hidden `_apply` command is accidentally treated as a fourth reserved layout action, contrary to `docs/designs/single-source-cli-docs.md:522-523`. |
| 4 — Generated deterministic CLI reference matches help | PASS | `tests/test_cli_docs.py` passed, and the renderer uses Click contexts and help records. |
| 5 — Stale reference/README fails checks | PASS | Generator check-mode and drift tests passed; the pre-commit hook invokes the generator check. |
| 6 — Narrative guide has no hand-written flag table | PASS | The guide links to the generated reference; its focused assertion passed. |
| 7 — Wiki inventory, sections, links, sidebar, footer | FAIL — **F-2, JA-1, F-3** | Inventory coverage passes, but valid nested-fence structure is parsed incorrectly, forbidden public content is included, and reused output directories retain obsolete pages. |
| 8 — Main-only publication and live token capability | UNVERIFIED AS DESIGNED | Static workflow guards passed inspection. `docs/designs/single-source-cli-docs.md:686-689` explicitly leaves the first live post-merge publication open; no live-state claim is made here. |
| 9 — No LLM in generation or CI | PASS | `scripts/generate_docs.py`, `scripts/wiki_build.py`, and `.github/workflows/wiki.yml` contain a deterministic local build with no model call. |

### R1 Findings

| ID | Severity | Disposition | Summary |
|---|---|---|---|
| F-1 | MAJOR | open-blocking | A layout file named `_apply` cannot be applied because the hidden dispatch command is also reserved. |
| F-2 | MAJOR | open-blocking | The wiki parser loses the opening fence length and misclassifies valid nested fenced content. |
| JA-1 | MAJOR | open-blocking | Comprehensive wiki publication republishes documentation that violates the package's public-content rules. |
| JA-2 | MAJOR | open-blocking | The short/long option invariant test never checks root-command options. |
| F-3 | MINOR | open-blocking | Rebuilding into an existing wiki output directory leaves obsolete generated pages behind. |

#### F-1 — MAJOR — Hidden `_apply` dispatch name breaks a valid layout name

**Location:** `src/ai_cli/main.py:6911-6939`; contract at
`docs/designs/single-source-cli-docs.md:142-144,522-523`.

**Exact evidence:** The contract says, “When `ai layout <name>` is used and `<name>` is not a
reserved action, the system shall call `cmd_layout_apply(name)`,” and identifies the reserved names
as exactly `list`, `validate`, and `profiles`. The resolver instead checks
`args[0] not in self.commands`, while the same group registers
`@cmd_layout.command("_apply", hidden=True)`. Hidden commands remain resolvable by Click, so `_apply`
is an unintended fourth reserved token.

**Impact:** A reachable, otherwise valid layout name fails with usage exit 2 instead of being
applied. This violates the design's explicit parser-parity invariant and makes an internal dispatch
name user-visible through behavior.

**Verification command:**

```bash
PYTHONPATH=src uv run python - <<'PY'
from click.testing import CliRunner
from ai_cli.main import _cli_group

apply = _cli_group.commands["layout"].commands["_apply"]
seen = []
old = apply.callback
apply.callback = lambda name: seen.append(name)
try:
    bad = CliRunner().invoke(_cli_group, ["layout", "_apply"])
    good = CliRunner().invoke(_cli_group, ["layout", "example-layout"])
finally:
    apply.callback = old
print({"reserved_hidden_exit": bad.exit_code, "reserved_hidden_called": seen == ["_apply"],
       "ordinary_exit": good.exit_code, "ordinary_names": seen})
PY
```

**Observed:** `{'reserved_hidden_exit': 2, 'reserved_hidden_called': False, 'ordinary_exit': 0,
'ordinary_names': ['example-layout']}`.

**Verification note:** **CONFIRMED** against the implementation and an isolated `CliRunner` probe.

**Recommendation:** In `_LayoutGroup.resolve_command`, reserve only the three public action names;
route every other non-option first token, including `_apply`, to the hidden callback. Add an explicit
`_apply` layout-name regression alongside the existing parity rows.

#### F-2 — MAJOR — Wiki fence parser discards the opening delimiter length

**Location:** `scripts/wiki_build.py:105-118`; masked link test at
`tests/test_wiki_build.py:214-241`; section contract at
`docs/designs/single-source-cli-docs.md:627-637`.

**Exact evidence:** On every opening fence, the parser executes
`fence = match.group(1)[0] * 3`. It therefore reduces a four-or-more-character opening fence to
three characters. Its close test then accepts a shorter inner fence as the close. The relative-link
test calls the same `_lines_outside_fences()` helper, so the oracle shares the defect it is intended
to detect.

**Impact:** Valid CommonMark that uses a longer outer fence to contain a shorter fenced example is
split incorrectly. Headings inside code can become wiki sections, and real headings or links after
the true close can remain classified as code; generated pages can silently omit or rewrite the
wrong material.

**Verification command:**

`````bash
PYTHONPATH=. uv run python - <<'PY'
from scripts.wiki_build import split_sections

text = "````text\ninside\n```\n## still code\n````\n## Real\nbody\n"
print({"section_names": list(split_sections(text)[1])})
PY
`````

**Observed:** `{'section_names': ['still code']}`. The code-block heading is incorrectly exposed,
and the real `Real` section is lost.

**Verification note:** **CONFIRMED** with a minimal valid nested-fence document and direct inspection
of the parser/test oracle.

**Recommendation:** Track the opening delimiter character and its exact length. Close only on the
same character with a run at least as long as the opener and no non-whitespace suffix. Add a
nested-fence fixture whose inner heading and links stay code while the post-close heading and links
remain visible; parse expected links independently in the regression.

#### JA-1 — MAJOR — Wiki publication violates the public-package content boundary

**Location:** `AGENTS.md:3-13`; `docs/designs/single-source-cli-docs.md:269-307`;
`docs/wiki/pages.toml`; exclusions in `tests/test_public_repo_hygiene.py:265-270,297-335`.

**Exact evidence:** The binding package rule says, “All code, docs, comments, tests, and commit
messages must be written for a general audience” and forbids proprietary and private project names.
The design requires “one wiki page for every other tracked Markdown file anywhere in the
repository.” The existing hygiene guard explicitly says, “Everything the name guard scans except
`docs`.” Its
provenance guard grandfathers a set of historical design/audit files unless changed.

**Measured evidence:** The manifest plan currently publishes 215 Markdown sources. A content-only
scan (printing counts, never values) found 173 non-exempt private-tracker lines across 33 published
files and 200 private-environment lines across 41 published files. These sets overlap; the counts
are evidence of reachable publication, not a claim about unique lines.

**Impact:** On the first successful main-branch wiki run, a public surface will copy internal-only
workflow and environment references that repository policy explicitly forbids. The implementation's
“publish everything” requirement makes this systematic rather than an isolated stale page.

**Verification command:**

```bash
PYTHONPATH=. uv run python - <<'PY'
import importlib.util
import tomllib
from pathlib import Path
from scripts.wiki_build import plan

root = Path.cwd()
spec = importlib.util.spec_from_file_location("hygiene", root / "tests/test_public_repo_hygiene.py")
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)
data = tomllib.loads((root / "docs/wiki/pages.toml").read_text())
result = plan(data, root, {"cli-reference": ("docs/reference/cli.md", "")})
sources = {page.source for page in result.pages}
for _, members in result.groups:
    sources.update(page.source for page in members)
markdown = sorted(source for source in sources if source.endswith(".md"))
tracker_lines, tracker_files, environment_lines, environment_files = 0, set(), 0, set()
for source in markdown:
    relative = Path(source)
    for line in (root / source).read_text(errors="ignore").splitlines():
        if hygiene._PRIVATE_TRACKER_ID.search(line) and not hygiene._line_is_exempted(relative, line):
            tracker_lines += 1
            tracker_files.add(source)
        if hygiene._line_has_environment_name(line) and not hygiene._line_is_exempted(relative, line):
            environment_lines += 1
            environment_files.add(source)
print({"published_markdown": len(markdown), "private_tracker_lines": tracker_lines,
       "private_tracker_files": len(tracker_files), "private_environment_lines": environment_lines,
       "private_environment_files": len(environment_files)})
PY
```

The probe loads `docs/wiki/pages.toml` through `scripts.wiki_build.plan`, intersects its published
Markdown sources with the repository hygiene predicates, and prints only aggregate line and file
counts. It produced:

```text
{'published_markdown': 215, 'private_tracker_lines': 173, 'private_tracker_files': 33,
 'private_environment_lines': 200, 'private_environment_files': 41}
```

**Verification note:** **CONFIRMED** against the actual manifest plan, tracked files, and the
repository's own hygiene predicates. No matched content was printed or copied into this audit.

**Recommendation:** Do not weaken the founding ask by excluding documentation from the wiki.
Instead, scrub or generalize the source documents before publication and extend the public-hygiene
gate to every wiki-published text source. Make the wiki build fail with safe path/count diagnostics
when a source violates the gate, and add a synthetic forbidden-content regression that contains no
real private value.

#### JA-2 — MAJOR — Option-form invariant omits the root command

**Location:** `AGENTS.md:15-19`; `tests/test_cli_option_forms.py:8-24`; generator traversal at
`scripts/generate_docs.py:116-125`.

**Exact evidence:** The binding convention says, “All options must have both short and long forms.”
The test helper starts with `for name, command in group.commands.items()` and inspects only
`command.params`; it never inspects the `group` passed to it. Consequently `_cli_group`'s own
options are outside the purported tree-wide invariant.

**Impact:** A future long-only root option—the exact screen in the founding question—passes the
dedicated invariant test. Current compliance does not make the enforcement claim true; the gap is
reachable through an ordinary decorator change.

**Verification command:**

```bash
PYTHONPATH=src uv run python - <<'PY'
import click
from ai_cli.main import _cli_group
from tests.test_cli_option_forms import _visible_options

probe = click.Option(["--probe-only"])
_cli_group.params.append(probe)
try:
    reported = [(path, param.opts) for path, param in _visible_options(_cli_group, [])
                if not any(opt.startswith("-") and not opt.startswith("--")
                           for opt in param.opts + param.secondary_opts)]
finally:
    _cli_group.params.remove(probe)
print({"root_long_only_detected": any("--probe-only" in opts for _, opts in reported),
       "reported_count": len(reported)})
PY
```

**Observed:** `{'root_long_only_detected': False, 'reported_count': 0}`.

**Verification note:** **CONFIRMED** by mutating the in-memory root only; no repository file or
external state was changed. An independent full-tree measurement found the current 361 visible
option records compliant.

**Recommendation:** Traverse the root and every descendant with a correctly constructed Click
context, using visible `command.get_params(ctx)` rather than raw child `command.params`. Assert both
a short and a long form for every visible option and add a fixture proving a long-only root option
fails.

#### F-3 — MINOR — Wiki builds do not remove obsolete pages

**Location:** `scripts/wiki_build.py:425-430`; deterministic-tree contract at
`docs/designs/single-source-cli-docs.md:627-651`.

**Exact evidence:** `build_wiki()` calls `out_dir.mkdir(..., exist_ok=True)` and then writes only the
current `files.items()`. It never enumerates or removes an obsolete generated `.md` file already in
the output directory.

**Impact:** A local or programmatic rebuild into a reused directory can return a tree that is not a
function of the current manifest/commit. The checked-in workflow currently builds into a fresh
runner directory and later uses `rsync --delete`, which limits immediate publication impact and
keeps this MINOR.

**Verification command:**

```bash
tmp_dir=$(mktemp -d)
PYTHONPATH=. uv run python - "$tmp_dir" <<'PY'
from pathlib import Path
import sys
from scripts.wiki_build import build_wiki

root = Path.cwd()
out = Path(sys.argv[1])
stale = out / "Obsolete.md"
stale.write_text("old")
build_wiki(root / "docs/wiki/pages.toml", root, out, "example/project",
           {"cli-reference": lambda: ("docs/reference/cli.md",
                                      (root / "docs/reference/cli.md").read_text())})
print({"obsolete_exists_after_build": stale.exists()})
PY
rm -r "$tmp_dir"
```

**Observed:** `{'obsolete_exists_after_build': True}`.

**Verification note:** **CONFIRMED** in an isolated temporary directory. No repository output was
modified.

**Recommendation:** Build into a fresh staging directory and atomically hand off that complete tree,
or reconcile only validated generated `.md` targets against the expected set. Add a regression that
seeds an obsolete page and asserts the resulting tree contains exactly the expected files.

### R1 Resolution Pass

| Finding | Classification | Current resolution | Re-audit proof required |
|---|---|---|---|
| F-1 | Auditor-applicable | OPEN — implementation change required | `_apply` is routed as a layout name and a focused regression passes. |
| F-2 | Auditor-applicable | OPEN — implementation change required | Nested backtick and tilde fences preserve code boundaries; an independent expected-link assertion passes. |
| JA-1 | Auditor-applicable | OPEN — source/gate remediation required before publication | Aggregate scan of every published text source reports zero forbidden values without excluding documentation. |
| JA-2 | Auditor-applicable | OPEN — test change required | A long-only root option fails the invariant test; the real tree remains clean. |
| F-3 | Auditor-applicable | OPEN — implementation change required | A seeded obsolete page is absent after rebuild and expected files remain byte-identical. |

No finding requires a product-policy choice: the design and repository rules determine the fixes.

### R1 Verification Matrix

| Finding | Reproduced command/probe | Actual result | Status |
|---|---|---|---|
| F-1 | In-memory `CliRunner`: `layout _apply` and ordinary layout name | Exit 2/no callback for `_apply`; exit 0/callback for `example-layout` | CONFIRMED |
| F-2 | `split_sections()` on a four-backtick outer block containing a three-backtick block | Returned `['still code']`; omitted the real post-close section | CONFIRMED |
| JA-1 | Manifest-plan sources intersected with repository hygiene predicates | 215 Markdown sources; non-zero forbidden-content aggregates in 33 and 41 files | CONFIRMED |
| JA-2 | Append a temporary long-only option to `_cli_group`, run current helper | `root_long_only_detected: False`; helper reported zero violations | CONFIRMED |
| F-3 | Seed `Obsolete.md`, run `build_wiki()` into the same temporary directory | `obsolete_exists_after_build: True` | CONFIRMED |

All five findings meet the template's five-to-ten-finding self-check mandate. The focused suite was
also rerun with:

```bash
UV_CACHE_DIR=/tmp/cx-uv-cache uv run pytest -q tests/test_cli_help.py tests/test_cli_docs.py \
  tests/test_wiki_build.py tests/test_cli_option_forms.py tests/test_cli_self_parsing.py
```

Result: `183 passed, 1 warning in 7.18s`; the warning was pytest's inability to write its cache in
the read-only checkout and is not an implementation finding.

### Decisions Requiring Team Input

None.

### Outstanding Issues

- **MUST:** Fix F-1, F-2, JA-1, and JA-2 before merge or any wiki publication.
- **SHOULD:** Fix F-3 in the same builder change so the documented output is a complete tree in
  local as well as CI use.
- **POST-MERGE GATE:** Complete the already-declared live wiki capability check; this is not evidence
  that any of the five implementation findings is resolved.

### Already-Correct Items

- Click declarations are the shared source for runtime help and the generated CLI reference.
- Root/group help uses the common `HelpGroup`; focused full-description and no-ellipsis tests pass.
- The committed CLI reference and README region match deterministic generator output.
- Visible commands/options and narrative-guide links are covered by focused tests; the current tree's
  visible options have both forms.
- Wiki publication is guarded to trusted `main`, and the write-token job does not execute repository
  builder code.
- The generator and workflow contain no LLM call; generated Markdown can feed a future site.

### Anti-Patterns

- Do not use the production parser as the sole test oracle for the same parser's boundary rules.
- Do not claim a tree-wide invariant from a traversal that begins at the root's children.
- Do not publish “all documentation” without applying the public-content policy to that exact output
  universe.
- Do not equate overwriting expected files with reconciling a generated directory.
- Do not treat passing focused tests as proof against independently reachable inputs those tests omit.

### Sign-Off Checklist

- [x] Target digest and implementation commit recorded.
- [x] Authoritative A001 requirements accounted for without reproducing private carrier content.
- [x] Load-bearing claims checked against code, tests, manifest, workflow, git history, or probes.
- [x] Each finding has a stable ID, severity, citation, exact evidence, impact, verification command,
  verification note, and concrete recommendation.
- [x] Five-finding verification matrix rerun and actual output recorded.
- [x] Focused implementation suite passed: 183 tests.
- [ ] Four MAJOR findings resolved and independently re-audited.
- [ ] One MINOR finding resolved or explicitly dispositioned.
- [ ] First trusted-main wiki publication verified live after merge.

### Files Read

**Audit contract and repository policy**

- External canonical audit `STUB.md` and `TEMPLATE.md` (location omitted under the public-package
  naming rule)
- `AGENTS.md`, `CLAUDE.md`, `CLAUDE-full.md`
- `docs/audits/single-source-cli-docs-implementation-audit.md`

**Design and research**

- `docs/designs/single-source-cli-docs.md`
- `docs/research/cli-docs-generation-tools.md`

**Implementation and generated artifacts**

- `src/ai_cli/main.py`, `src/ai_cli/cli_help.py`
- `scripts/generate_docs.py`, `scripts/wiki_build.py`
- `docs/wiki/pages.toml`, `docs/reference/cli.md`, `docs/tools/ai-cli-usage.md`
- `README.md`, `CONTRIBUTING.md`, `.pre-commit-config.yaml`
- `.github/workflows/wiki.yml`

**Tests**

- `tests/test_cli_help.py`, `tests/test_cli_docs.py`, `tests/test_cli_option_forms.py`
- `tests/test_cli_self_parsing.py`, `tests/test_wiki_build.py`
- `tests/test_public_repo_hygiene.py`

### Commands Run

- `git rev-parse HEAD`; `sha256sum docs/designs/single-source-cli-docs.md`
- `git log --oneline -- docs/designs/single-source-cli-docs.md src/ai_cli/main.py scripts/wiki_build.py tests/test_cli_option_forms.py`
- `rg`/`sed`/`nl` reads over the target, implementation, tests, manifest, workflow, and policy files.
- Focused pytest command recorded under the verification matrix.
- Five isolated reproduction probes recorded under F-1, F-2, JA-1, JA-2, and F-3.

### R1 Receipt Payload Projection

```json
{
  "findings": [
    {
      "cluster": {
        "invariant_id": "layout-dynamic-name-routing",
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-layout",
        "violation_kind": "incorrect-behavior"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-1",
      "fingerprint": "fp:layout-hidden-apply-reserved",
      "id": "F-1",
      "reachable_behavior": "Invoking the layout command with the valid name _apply exits with usage status instead of applying that layout.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": "markdown-fence-boundaries",
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "wiki-builder",
        "violation_kind": "incorrect-behavior"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-2",
      "fingerprint": "fp:wiki-fence-opening-length-lost",
      "id": "F-2",
      "reachable_behavior": "A valid longer outer Markdown fence containing a shorter fence exposes code headings and hides real post-fence sections.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": "public-package-content-hygiene",
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "wiki-publication",
        "violation_kind": "requirement-violation"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:JA-1",
      "fingerprint": "fp:wiki-publishes-private-doc-metadata",
      "id": "JA-1",
      "reachable_behavior": "The main-branch wiki publisher includes tracked documentation containing content forbidden on this public package's surfaces.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": "option-short-long-parity",
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "cli-invariant-tests",
        "violation_kind": "missing-enforcement"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:JA-2",
      "fingerprint": "fp:root-options-omitted-from-form-test",
      "id": "JA-2",
      "reachable_behavior": "A long-only root option passes the test that claims to enforce short and long forms across the command tree.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {
        "invariant_id": "wiki-output-tree-equivalence",
        "spec_version": "audit-loop@schema-v1",
        "subsystem": "wiki-builder",
        "violation_kind": "stale-output"
      },
      "disposition": "open-blocking",
      "finding_key": "round1:F-3",
      "fingerprint": "fp:wiki-build-leaves-stale-pages",
      "id": "F-3",
      "reachable_behavior": "Rebuilding into an existing output directory leaves an obsolete Markdown page in the generated tree.",
      "severity": "minor",
      "submitted_severity": "minor"
    }
  ],
  "major_nonblocking_justifications": {
    "round1:F-1": "The explicit layout-routing invariant fails on a reachable name; no critical safety or data-loss consequence was reproduced.",
    "round1:F-2": "The parser corrupts reachable valid Markdown structure; no critical safety or data-loss consequence was reproduced.",
    "round1:JA-1": "The public-content rule is unambiguous and publication is reachable after merge; the issue does not meet the critical rubric.",
    "round1:JA-2": "A required command-tree invariant is not enforced at the root; the current tree remains compliant, keeping severity below critical."
  },
  "new_by_severity": {
    "critical": 0,
    "major": 4,
    "minor": 1
  },
  "scope": {
    "backlog": [],
    "diff_since": null,
    "diff_summary": {
      "changed": false,
      "from": null,
      "to": "sha256:75bb65ec5f2465ac95e326f5068319e751044d37abe674dbca57534b6b3acce3"
    },
    "kind": "full"
  },
  "source_event_id": "round-1-audit"
}
```
