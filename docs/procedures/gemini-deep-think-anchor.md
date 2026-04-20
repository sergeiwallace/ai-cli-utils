---
title: "Zero-Slop Anchor Text (Deep Think Grounding)"
category: procedures
tags: [prompt-engineering, deep-think, research, grounding, anti-hallucination]
status: active
source: sergei
---

# Zero-Slop Anchor Text (Deep Think Grounding)

When running deep research tasks using Reasoning Models with Extended Thinking (like Gemini 3.1 Pro Deep Think, OpenAI o1/o3, or Claude 3.7 Sonnet), the models are highly susceptible to **Compliance Pressure**.

If a prompt contains an overly rigid constraint (e.g., "Always cite an academic paper for every claim"), the model's internal reinforcement learning loop will prioritize *following the instruction* over *telling the truth*. If a paper doesn't exist for a niche industry fact, the model will hallucinate a fake arXiv title just to comply.

To prevent this, **ALL research prompts executed via Reasoning Models MUST append this standardized "Zero-Slop" Anchor Text.** 

It forces "Claim Classification" and provides an explicit "Abstention Pathway," relieving compliance pressure and keeping the model's scratchpad grounded.

## The Standard Anchor Text

Copy and append this exact block to the end of your research prompt. It utilizes XML boundaries, explicit abstention pathways, and a Chain-of-Verification (CoVe) to control the reasoning model's scratchpad.

```xml
<grounding_instructions>
You are the Chief Scientist and Principal Systems Architect of the leading frontier AI company. Before generating your final output, use your internal reasoning space to execute a Chain-of-Verification (CoVe) to ensure factual fidelity over compliance.

Inside your thought process, you must:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: Act as a hostile reviewer against your own draft. Flag any "Compliance Pressure." Ask yourself: "Am I only suggesting this architecture or citing this paper because the user's prompt implied I should?"
4. Strip away any claim that cannot be empirically verified.

When generating your final output, adhere to these epistemic boundaries:

1. CLAIM CLASSIFICATION: You MUST classify every major claim or recommendation you make using one of the following tags:
   - [VERIFIABLE FACT]: Facts backed by standard documentation, peer-reviewed research, or official tech blogs (2024-2026). You MUST provide the direct URL or DOI.
   - [INDUSTRY HEURISTIC]: A widely accepted best practice without a specific academic citation.
   - [SYNTHESIZED INFERENCE]: A logical jump, architectural proposal, or heuristic you are inventing based on the context. Do not attempt to cite a source for this; provide your logical rationale instead.

2. NO COMPLIANCE FABRICATION: Do not fabricate paper titles, arXiv IDs, or URLs to satisfy citation constraints. 

3. EXPLICIT ABSTENTION: I value the admission of ignorance. If a specific value, constant, or source cannot be found, you MUST explicitly state [NO SOURCE FOUND] or "I lack verifiable data on this" rather than guessing. 

4. FORMATTING: Format diagrams using Mermaid.js or ASCII. Format math using LaTeX. NEVER generate binary images.

5. OPERATIONAL REASONING (include only when the research touches technology evaluation, architecture comparison, or adoption recommendations):
<operational_reasoning>
- For each recommended approach, list at least one concrete failure mode
- For each benchmark or performance claim, classify as [SOURCED: {URL}] or [ESTIMATED: {rationale}]
- When comparing options, use a numbered table with explicit criteria — no prose-only comparison
- State what you would need to verify empirically before adopting the recommendation
</operational_reasoning>
</grounding_instructions>
```text

## Prompt Patterns

### Gap-Fill / Temporal-Scoping Prompts

When researching what's new in a specific time window (e.g., "what did this field produce in 2026?"), the standard `[NO SOURCE FOUND]` tag is insufficient — the model will backfill with older sources to avoid returning an empty answer. Name the failure mode explicitly.

**Inside `<grounding_instructions>`, add as a hard constraint:**

```text
Hard constraint: if [period] is genuinely thin on a topic, "[period]: no significant new
developments found" is the correct answer. Do not backfill with [earlier period] sources
already covered in prior research. Backfilling is a failure mode, not a hedge.
```text

Naming the specific failure ("backfill") is more effective than generic abstention instructions because it gives the model a label to apply during its CoVe hostile cross-examination step.

### Follow-Up / Sequential Research Runs

When a research run follows a prior run on the same topic, add a `## Background` section at the top of the prompt body (before your research questions, outside `<grounding_instructions>`). This prevents the model from re-surveying terrain already covered.

```markdown
## Background

Prior research ([registry ID], [date]) on [topic] found:
- [key finding 1]
- [key finding 2]
- [key finding 3]

This run should build on — not repeat — those findings. Assume all Background points
are established and do not re-derive them.
```text

The model uses the Background section during its planning step to scope what's already known, directing its reasoning toward the delta rather than a full re-survey.

---

## Historical Context
This anchor is a synthesis of two prior iterations:
1. **The Old Aido Prompt:** We previously commanded Deep Think to *"Cite peer-reviewed papers... Prioritize 2024-2026 sources. For each key claim, provide the specific citation."* This caused catastrophic compliance pressure, resulting in the model fabricating non-existent authors and rendering formulas as binary images to avoid textual constraints.
2. **The `aido-1` Fix:** The `aido-1` Gemini session introduced the first "Zero-Slop" anchor, which successfully stopped the binary image generation and introduced the `[PROPOSED HEURISTIC]` tag.
3. **The Current State-of-the-Art:** This current version incorporates 2026 industry best practices for Reasoning Models, explicitly commanding the model to classify its claims *before* answering, forcing it to organize its epistemic certainty in its hidden scratchpad.