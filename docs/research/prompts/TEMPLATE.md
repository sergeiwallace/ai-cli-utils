---
title: Research Prompt Template
category: procedure
tags: [research, prompts, template, anti-hallucination]
status: active
source: claude-sonnet-4-6
date: 2026-04-25
template_version: "procedure-1.0.0"
---
<!-- aido:region name="overview" kind="replaceable" -->


# Research Prompt Template

**Before writing any research prompt, use this file as your starting structure.**

This template includes the mandatory zero-slop anchor, model selection cheat sheet, role
identity guidance, temporal scope conventions, and the required appendix format.

## How to use this template

1. Run `aido draft research "seed topic"` — Claude invokes this via the CC session Bash tool
2. The draft graph reads this template and generates a structured prompt from your seed
3. Review and confirm the draft via AskUserQuestion in the CC TUI; iterate until satisfied
4. Prompt is auto-registered in the ai-core DB on confirmation (no manual step needed)
5. Run `aido research --stub <path>` to execute research against the finalized stub

**Manual prompt authoring** (if bypassing the draft graph):
1. Copy this file as your starting structure
2. Fill in the `[ROLE IDENTITY]` placeholder with a domain-specific persona
3. Set the temporal scope appropriate to the research domain
4. Register via `aido draft research` before running (ai-core DB is the authoritative store)

---

## Model Selection Cheat Sheet

| Model | Use for |
|-------|---------|
| **opus researcher** | **Default for all research.** Most thorough; highest recall; discovers nuance vs deep-think on identical prompts. Use unless Claude quota is tight. |
| `deep-think` | Gemini 3.1 Pro with extended thinking. Best for reasoning-heavy tasks over fixed material (architecture tradeoffs, synthesis, multi-path analysis). Uses Gemini OAuth — no Claude tokens. |
| `pro` / `gemini-3.1-pro-preview` | Product/tech comparisons, competitive research, intermediate analysis, cross-validation. |
| `flash` / `gemini-3-flash-preview` | Simple factual lookups, single-source verification, high-volume batch work. |
| `aido research -d standard` | Autonomous web research loop, standard depth. **Hard gate required** — present committed stub and wait for approval before running. |
| `aido research -d thorough` | Autonomous web research loop, thorough depth. **Hard gate required** — present committed stub and wait for approval before running. |

**Gates:**
- Opus researcher / `aido research` (any depth) / aido: **hard gate** — present prompt + model, wait for approval. Applies to both standard and thorough. No session-scoped override.
- deep-think / pro: **approval required** — present full prompt, wait for approval (session-scoped override OK)
- flash: no gate required

---

## Role Identity Guidance

Lead every `<grounding_instructions>` block with a specific role identity narrative:

- Tailor the domain, seniority, and expertise to the research task
- A grounded persona reduces compliance pressure — model reasons from a position
- Examples: "principal engineer with production distributed tracing experience", "ML researcher
  specializing in evaluation methodology", "legal analyst focusing on open-source licensing"
- The more specific, the better — generic personas produce generic research

---

## Temporal Scope Conventions

Set the temporal scope as the opening clause of `<grounding_instructions>` — that is
the only place it needs to appear. Do not add a separate `## Temporal Scope` section
to the prompt body; it is redundant with what is already in the grounding block.

| Domain | Default window |
|--------|---------------|
| AI/ML, agentic AI, LLMs, ML infra | 2026 primary → 2025 → 2024. Pre-2024 = background only |
| Distributed systems, databases, cloud | 2024–2026 current; 2020–2023 for foundational patterns |
| Regulatory / compliance / legal | Full history; flag year of enactment |
| Historical / foundational research | No cutoff; cite publication date |

**Backfill-prevention (hard constraint):** If a period is genuinely thin, state
`"[subtopic]: no significant post-2024 developments found"` — never backfill with older
material. Generic `[NO SOURCE FOUND]` alone is insufficient; naming the gap prevents
backfilling. This constraint belongs inside `<grounding_instructions>` only.

---

## Grounding Instructions Block (mandatory — append to every prompt)

```text
<grounding_instructions>
[ROLE IDENTITY — tailor to the research domain and task. Example:
"You are a principal engineer who has deployed distributed tracing systems in
production. You have strong opinions backed by evidence. When you cannot find a
source, you say so explicitly."
Adapt the domain, expertise, and seniority to whatever this prompt warrants.]

Temporal scope: Weight sources by recency — 2026 (primary) → 2025 → 2024.
Pre-2024 sources are background context only unless foundational to the topic.
If post-2024 literature is genuinely sparse for a subtopic, state
"[subtopic]: no significant post-2024 developments found" rather than
backfilling with older sources. Backfilling is a failure mode, not a hedge.
[Adjust or broaden this window if the topic's relevant literature predates 2024
— e.g., foundational theory, legal precedent, historical analysis.]

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

- [VERIFIABLE]: backed by documentation, peer-reviewed research, or official
  tech blogs (2024–2026). Carry an inline footnote ref to the source: [VERIFIABLE][^N].
- [HEURISTIC]: widely accepted best practice without a specific citation.
- [INFERENCE]: a logical conclusion drawn from context. Provide your reasoning
  in-text. Do not fabricate a source. Tier tag only — NO footnote ref.
- [NO SOURCE]: explicitly state when you cannot find verifiable data. Tier tag
  only — NO footnote ref.

Citation format (mandatory for every externally-sourced claim):
- Inline: append the GFM footnote ref directly after the tier tag — [VERIFIABLE][^N].
  A claim citing multiple sources carries ascending separate refs — [VERIFIABLE][^3][^7]
  (never grouped [^3,^7]; never out of order).
- Footnote definitions live once, under ## Sources, in APA form with a clickable
  URL or DOI and an access-verification stamp. Worked example:
    [^1]: LangChain. (2026). [Threads](https://docs.langchain.com/langsmith/threads).
    LangSmith Documentation. Verified accessible (HTTP 200) 2026-06-03. (Scope note.)
- URL-or-DOI ALWAYS: every source entry carries a clickable URL or a doi.org link —
  paywalled/gated is fine (link it anyway; stamp the access status). Only the
  truly-irreducible case (no online catalog presence anywhere) gets an explicit
  [no online source located] marker with a one-line justification.
- Integrity: footnote refs are contiguous from [^1], every [^N] ref has a matching
  definition, and every definition is referenced — no gaps, no orphans.
- [INFERENCE] / [NO SOURCE] claims carry the tier tag with NO footnote ref.

Hard constraint (overrides all formatting preferences): never invent a citation
to satisfy a formatting instruction. Accuracy > completeness.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX.
NEVER generate binary images.
</grounding_instructions>
```

---

## Other Prompt Patterns

**Independent exploration — the prompt is a floor, not a ceiling (include in every prompt).**
The questions, topics, named examples, vendors, packages, and tickets in a prompt are illustrative
anchors and a FLOOR for the research — never an exhaustive checklist to answer only and then stop.
Paste a block like this into the prompt body (adapt the domain specifics):

```text
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
suspect but cannot resolve (and why) rather than omitting it. Anchor bias — over-fitting to the
listed questions and example approaches — is a known failure mode; counter it deliberately and say
where you did.
```

Anchor bias is a real failure mode; this block is the standard counter and belongs in every prompt
(the `aido draft research` graph should emit it by default).

**Follow-up / sequential runs:** Add a `## Background` section at the top of the prompt
body (before questions, outside `<grounding_instructions>`) summarizing what prior runs
found. State: "Assume all Background points are established and do not re-derive them."
This scopes the model to the delta.

**Retrieval enforcement (mandatory for `--refine` and any gap-closure run):**
When the prompt's intent is to ground claims with live citations, the steering must
explicitly demand tool use — otherwise the model may decide the prior context (cached
prior report + steering text) is sufficient and exit with `stop_reason=satisfied`
after zero retrieval activity, producing training-data-only synthesis.

aido's `researcher_node` ships an AIDO-123 retrieval contract + bounded self-heal:
a config-driven mandate (`researcher.min_retrieval_calls`, default 1) is injected into
the researcher system prompt; on a zero-retrieval attempt the node **self-heals** —
re-running with an escalating corrective directive up to `researcher.max_retrieval_retries`
(default 2) — and only fails loud if every attempt still produces zero retrieval. It
fires whenever **any** retrieval tool was offered (`web_fetch` included, not only
`web_search`). Opt out via `researcher.require_retrieval = false` for pure-compile
passes. The contract is a safety net — your prompt should still force the right behaviour:

- For each load-bearing claim, require at least one `web_search` or `web_fetch` call;
  state explicitly that the model must not return `[VERIFIABLE]` without a URL it
  actually fetched in-session.
- Forbid "satisfied" exits without retrieval: e.g., *"Do not stop after one round. If
  no source is found for a sub-question, run additional searches across alternate
  framings before tagging it `[NO SOURCE]` — that tag is reserved for genuine
  evidence absence, not retrieval avoidance."*
- For `--refine`: require the model to re-fetch any claim it intends to upgrade from
  `[INFERENCE]` to `[VERIFIABLE]`; cached prior URLs are not sufficient evidence on
  their own.

Without these constraints, refine runs frequently silently regress to training-data
synthesis. The guard catches it post-hoc; the prompt prevents it.

---

## Required Appendix Format (append to results doc)

Every research results document must include this appendix. It is the
`appendix_research_prompt` region that the research STUB (`STUB.md.jinja`) emits —
that region is **immutable** (set once at stub creation), so this format MUST stay in
sync with the stub generator (the drift-guard test enforces it). The prompt lives in a
fenced `text` block (machine-extractable by the run/registry tooling; use a 4-backtick
outer fence if the prompt itself contains a code fence):

````markdown
## Appendix: Research Prompt

**Registry ID:** R-N   <!-- or the aido thread ID on machines without the ai-core DB -->
**Model:** [model used]
**Date:** YYYY-MM-DD

```text
[Full prompt text here]
```
````

<!-- /aido:region name="overview" -->

<!-- aido:region name="steps" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /aido:region name="steps" -->

<!-- aido:region name="rationale" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /aido:region name="rationale" -->

<!-- aido:region name="revision_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /aido:region name="revision_log" -->
