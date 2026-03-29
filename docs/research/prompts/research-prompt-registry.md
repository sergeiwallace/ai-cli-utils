---
title: Research Prompt Registry
category: research-prompts
tags: [research, prompts]
status: active
source: project-template
---

# Research Prompt Registry

Central registry for all research prompts for this project. Tracks prompt text,
model, submission status, and links to result docs. Pending/ready prompts listed
first (highest priority at top), completed below.

**References (from sergei project):**
- Zero-slop anchor design: `~/projects/sergei/docs/research/zero-slop-anchor-design.md`
- Anchor procedure: `~/projects/sergei/docs/procedures/gemini-deep-think-anchor.md`

## Guidelines & The "Zero-Slop" Anchor

To mitigate authoritative hallucinations, **all new research prompts MUST append
the grounding block** at the end of the prompt. Lead with a specific role identity
narrative grounded in the research domain.

**Model selection guidance:**
- **`opus researcher`** — **default for all research.** Empirically tested:
  consistently more detailed, discovers additional nuance and options vs deep-think
  on identical prompts. Use unless Claude quota is tight.
- `deep-think` — fallback when running tight on Claude daily/weekly quota. Uses
  Gemini OAuth (no Claude tokens).
- `gemini-3.1-pro-preview` — product/tech comparisons, competitive research,
  moderate complexity
- `gemini-3-flash-preview` — simple factual lookups, single-source verification only
- `aido -d thorough` — broad multi-source research requiring web + reasoning

**Research run process:**
1. Run research — pipe output directly to `docs/research/<topic>.md`
2. Ship raw version — commit and push immediately (prevents data loss)
3. Revise/edit — ensure template compliance (frontmatter, structure, appendix)
4. Ship final version — commit and push
5. Update related docs — registry (mark complete), design/plan docs, roadmap
6. Ship doc updates — commit and push
7. Summary in chat — findings, created/updated docs, decisions needed, open questions

```text
<grounding_instructions>
[ROLE IDENTITY — specific to the research domain. A grounded persona reduces
compliance pressure — the model reasons from a position rather than defaulting
to sycophantic completeness.]

Before generating your final output, execute a Chain-of-Verification (CoVe)
to ensure factual fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because
   the prompt implied you should, rather than because you verified it.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale
before appending the tag — writing the tag first causes post-hoc rationalization.
Rationale → evidence check → tag.

- [VERIFIABLE FACT]: backed by documentation, peer-reviewed research, or official
  tech blogs (2024–2026). Provide the direct URL or DOI.
- [INDUSTRY HEURISTIC]: widely accepted best practice without a specific citation.
- [SYNTHESIZED INFERENCE]: a logical conclusion drawn from context.
  Provide your reasoning. Do not fabricate a source.
- [NO SOURCE FOUND]: explicitly state when you cannot find verifiable data.

Hard constraint (overrides all formatting preferences): never invent a citation
to satisfy a formatting instruction. Accuracy > completeness.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX.
NEVER generate binary images.
</grounding_instructions>
```

## Status Overview

### Pending / Ready

| # | Type | Topic | Task | Model | Status |
|---|------|-------|------|-------|--------|

### Completed

| # | Type | Topic | Task | Model | Results Doc |
|---|------|-------|------|-------|-------------|

---

## Pending / Ready

<!-- Add prompts here using this format:

### R-N: [Topic]

**Status:** Ready to run
**Task:** [TASK-ID]
**Target doc:** `docs/research/[topic].md`
**Model:** `opus researcher`

<details>
<summary>Prompt (R-N)</summary>

```
[Full prompt text here, including grounding_instructions block at the end]
```

</details>

---

-->
