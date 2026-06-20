---
title: "[Research Topic]"
category: research
tags: [research]
status: complete
source: "[model]-[date]"
template_version: "research-1.2.0"
# Optional fields:
# related_docs:
#   - docs/research/related-doc.md
# superseded_by: docs/research/newer-doc.md
---

# [Research Topic]

**Status:** complete

**Created:** YYYY-MM-DD

<!-- Optional fields — add as needed:
**Task:** SW-XXX
**Prompt:** R-N in `docs/research/prompts/research-prompt-registry.md`
**Superseded by:** `docs/research/...`
**Related docs:**
- `docs/research/...`
-->

## Table of Contents

<!-- AIDO-128 / D5 (c): research docs carry a ToC of EVERY `## ` and `### ` heading in the
     `body` region (Temporal Scope, Executive Summary, Findings by Question, Comparison,
     Recommendation, Gaps/Open Questions, Sources, …) + the appendices, with GitHub-style
     anchors. This lives in the static header area (outside the regions) so the synthesizer's
     body rewrite does not clobber it; `aido toc check` (AIDO-127) regenerates it from the
     body headings after each run. Fall back to D5 (a) "meaningful `###`" if all-`###` is noisy. -->

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- <!-- one entry per `## ` findings/option section + its `### ` sub-sections -->
- [Comparison](#comparison)
- [Recommendation](#recommendation)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

<!-- aido:region name="context" kind="immutable" -->

## Context

<!-- Original framing of the research question. Set once when the stub is
     created (or the first refine pass writes through legacy fallback); frozen
     thereafter. Subsequent refines must not rewrite this section. -->

**Primary period:** YYYY–YYYY
**Source weighting:** <!-- e.g., 2026 primary, 2025 secondary, pre-2024 background -->

<!-- /aido:region name="context" -->

<!-- aido:region name="body" kind="replaceable" -->

## Temporal Scope

<!-- State the time period this research covers and how sources were weighted.
     Example: "Primary focus: 2026 and late-2025 sources. Earlier work cited as
     background only. No significant post-2024 developments found for [subtopic]."
     For AI/ML research: weight 2026 (primary) → 2025 → 2024.
     Omit or broaden for topics where older literature is foundational. -->

## Executive Summary

<!-- Recommendation and key findings in 2-3 sentences -->

## 1. [Option / Approach A]

<!-- Deep exploration -->

### When to use

<!-- Practical boundaries, scale breakpoints -->

## 2. [Option / Approach B]

<!-- Deep exploration -->

### When to use <!-- 2 -->

## Comparison

| # | Criterion | Option A | Option B |
|---|-----------|----------|----------|
| 1 | | | |
| 2 | | | |

## Recommendation

<!-- Final guidance with rationale -->

## Open Questions

1. <!-- Question -->
2. <!-- Question -->

## Sources

<!-- AIDO-125 (research-1.2.0): source entries are GFM footnote DEFINITIONS in APA form,
     not bullets. Each definition is referenced inline from the claim it backs via an
     ascending footnote ref after the confidence tier — e.g. `... grouping by thread id
     [VERIFIABLE][^1].` Rules:
       - APA shape: `[^N]: Org. (Year). [Title](URL). Publisher/Site. Verified
         accessible (HTTP <code>) <date>. (Scope note.)`
       - URL-or-DOI ALWAYS (paywalled is fine — link it anyway, stamp the access status);
         truly-irreducible case gets `[no online source located]` + one-line justification.
       - Contiguous from [^1], no gaps, no orphans (every ref defined, every def referenced).
       - Confidence tiers are SHORT: [VERIFIABLE] / [HEURISTIC] / [INFERENCE] / [NO SOURCE].
         Only [VERIFIABLE] claims carry a footnote ref; [INFERENCE]/[NO SOURCE] do not.
     Migration from research-1.1.0: the old bullet `- [Title](URL) — type` list converts to
     `[^N]:` footnote defs; run `aido citations reformat <path>` (AIDO-125) to migrate.

     Worked example of one footnote definition (place real defs below as `[^1]:`, `[^2]:`, …,
     each referenced by an inline `[^N]` in the body):
       [^1]: Org. (Year). [Title](https://example.com/page). Publisher/Site. Verified
       accessible (HTTP 200) YYYY-MM-DD. (One-line scope note.) -->

(footnote definitions populated as sources are cited — see the worked example above)

<!-- /aido:region name="body" -->

<!-- aido:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

<!-- This region is auto-populated by the verify_and_remediate node during
     aido research --refine runs. Items captured here are findings where
     the per-class auto-fix rule applied but evidence was unclear — the
     node applied its best-guess default to the body AND flagged the
     item for human review.

     Post-run review flow:
       1. After the aido run completes (NOT mid-run), CC monitoring
          greps for verify_and_remediate_ambiguous telemetry events and
          surfaces the items via chat.
       2. Sergei reviews; resolved items are removed from this section
          (via CC Edit or a future `aido resolve-ambiguous` CLI command).
       3. Unresolved items remain across runs as a persistent TODO;
          new runs append new ambiguous items without disturbing prior
          unresolved ones.

     Format per item:
       ### YYYY-MM-DDTHH:MM:SS (Run N, mode, model)
       - **AMBIGUOUS [class]** § Location. Default applied: kept_new | restored_prior.
         - **Concern:** <one-sentence reason it's ambiguous>
         - **Recommended action:** <one-sentence what to check>

     This region's kind is `replaceable` (not `append_only`) so unresolved
     items can be removed when resolved. The verify_and_remediate node's
     prompt-level discipline is to PRESERVE prior unresolved items AND
     APPEND new ones — never wholesale rewrite. The validator will warn if
     prior items are dropped without explicit resolution.
-->

(none yet — auto-populated by verify_and_remediate node)

<!-- /aido:region name="ambiguous_items" -->

<!-- aido:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** R-N / DT-N / A-N
**Model:** `opus researcher` / `gemini-3.1-pro-preview` / `deep-think`
**Date:** YYYY-MM-DD

```text
[Full prompt text here — see prompt-engineering checklist below before drafting]
```

<!-- Prompt-engineering checklist (before pasting the prompt above):

  Canonical reference: ~/projects/aido/docs/research/prompts/TEMPLATE.md

  Required components (every research prompt):
    [ ] Lead with a domain-specific role identity (not "you are a helpful assistant")
    [ ] State the temporal scope (e.g., 2026 primary → 2025 → 2024) — once,
        inside the grounding block; do not duplicate as a separate section
    [ ] Append the full <grounding_instructions> CoVe anchor verbatim from the
        aido template — includes role identity, temporal scope, Chain-of-Verification
        4-step inner process, claim classification tags ([VERIFIABLE] /
        [HEURISTIC] / [INFERENCE] / [NO SOURCE]) with inline [VERIFIABLE][^N]
        footnote refs (AIDO-125), and the never-invent-citations hard constraint
    [ ] Independent exploration — the prompt is a FLOOR, not a ceiling. The
        questions, topics, named examples, vendors, packages, and tickets are
        illustrative anchors and a floor for the research — never an exhaustive
        checklist to answer only and then stop. Anchor bias (over-fitting to the
        listed questions/examples) is a known failure mode; counter it deliberately.
        Paste both blocks below into the prompt body (adapt the domain specifics):

        ## Scope note — questions, examples, and named references are a starting point, not a checklist
        The questions, topics, and named examples below are illustrative anchors and a FLOOR for this
        research — not an exhaustive list to answer only or evaluate only. Reason independently: survey the
        landscape broadly, follow the evidence where it leads, expand scope where warranted, and surface
        relevant work, factors, and failure modes not named here. Actively resist answering only the listed
        questions or evaluating only the named approaches — an output that merely fills in the listed items
        has NOT met the research goal.

        ## Independent exploration (gaps, blindspots, emergent threads) — required
        Treat the question list as a FLOOR, not a ceiling. As you research, actively surface what this
        framing may be missing and pursue each promising thread to a logical conclusion:
        - Adjacent or upstream factors the questions don't capture.
        - Contrarian / disconfirming evidence — report it even when it challenges the premise.
        - Emerging 2025–2026 practices, tools, or research not anchored by the named examples.
        - Known failure modes and second-order effects.
        Whenever a load-bearing thread surfaces mid-research, follow it to its conclusion and report it in a
        dedicated "Gaps, blindspots & emergent findings" subsection. Explicitly NAME any blindspot you
        suspect but cannot resolve (and why) rather than omitting it.

  For refine / gap-closure prompts (where live retrieval is the point):
    [ ] Include an explicit retrieval-enforcement clause — forbid "satisfied"
        exits without tool use, require at least one search per claim, mandate
        that [VERIFIABLE] tags carry inline [^N] footnote refs to URLs the model
        actually fetched in-session
    [ ] State that cached prior-URLs are not sufficient evidence on their own
        for refine — the model must re-fetch claims it intends to upgrade from
        [INFERENCE] to [VERIFIABLE]

  aido enforces the retrieval requirement at runtime via researcher_node's
  silent-no-retrieval guard (fails loud if web_search tools were offered but
  zero retrieval activity fired). Opt out via researcher.require_retrieval=false
  only when training-data synthesis is genuinely intentional.

If this research doc predates the prompt appendix convention, note:
"This research doc predates the prompt appendix template. The original prompt
and model information are not available." -->

<!-- /aido:region name="appendix_research_prompt" -->

<!-- aido:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

<!-- AIDO-121: auto-populated at commit from the validated provenance ledger.
     Each row ties a claim to the verbatim quote from the fetched source that
     supports it, plus the NLI/LLM verdict. Links to the
     `<doc>.provenance.jsonl` sidecar (the full ledger). -->

(none yet — auto-populated at commit from the validated provenance ledger)

<!-- /aido:region name="appendix_provenance" -->

<!-- aido:region name="run_history" kind="append_only" -->

## Run History

<!-- NOTE for aido research runs:
aido's commit_report_node auto-appends entries to this region with
detailed provenance: aido version, config, mode, query, loop count,
models used (Claude brief/search/analysis/compile + Gemini model),
full token usage per backend, estimated API cost, and errors.
Append-only — prior entries are frozen byte-for-byte. -->

<!-- /aido:region name="run_history" -->
