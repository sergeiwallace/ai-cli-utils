---
title: "[Research Topic]"
category: research
tags: [research]
status: complete
source: "[model]-[date]"
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

- <!-- [Title](URL) — source type -->

## Appendix: Research Prompt

**Registry ID:** R-N / DT-N / A-N
**Model:** `opus researcher` / `gemini-3.1-pro-preview` / `deep-think` / `aido`
**Date:** YYYY-MM-DD

```
[Full prompt text here]
```

<!-- If this research doc predates the prompt appendix convention, note:
"This research doc predates the prompt appendix template. The original prompt
and model information are not available." -->

<!-- NOTE for aido research runs:
aido's commit_report_node auto-appends a "## Run History" section with
detailed provenance: aido version, config, mode, query, loop count,
models used (Claude brief/search/analysis/compile + Gemini model),
full token usage per backend, estimated API cost, and errors.
The Appendix: Research Prompt section above still holds the original prompt,
but aido adds its own runtime metadata automatically — no manual work needed.
Do NOT duplicate what aido already appends. -->
