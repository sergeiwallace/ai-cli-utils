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
| R-2 | Research | GitHub repo automation & ecosystem tooling | AI-CLI-3 | opus researcher | Complete |

### Completed

| # | Type | Topic | Task | Model | Results Doc |
|---|------|-------|------|-------|-------------|

---

## Pending / Ready

### R-2: GitHub Repository Automation & Ecosystem Tooling

**Status:** Complete
**Task:** AI-CLI-3 (extended to cover full remaining professionalization gaps)
**Target doc:** `docs/research/github-repo-automation.md`
**Model:** `opus researcher`

<details>
<summary>Prompt (R-2)</summary>

```
You are a senior open-source maintainer who has shipped and maintained Python CLI
packages with 5K-50K stars on GitHub. You know the difference between "looks
professional" and "runs itself."

Research the GitHub ecosystem tooling, automation, and repository configuration
best practices for a solo-maintained Python CLI project that already has the basics
(CI, PyPI publish, Dependabot, badges, README, CONTRIBUTING.md, SECURITY.md).

The project is `ai-cli-utils` — a Python 3.11+ CLI tool installed via
`uv tool install` / `pipx install`. It uses ruff for linting, pytest for testing,
hatchling for builds, and uv for dependency management. Repo is currently private,
will go public.

Cover these areas:

1. **GitHub Apps & Bots** — Which GitHub Apps/bots are worth installing for a
   solo-maintained project? Evaluate: Renovate (vs Dependabot), Release Drafter,
   Stale bot, All Contributors, CodeQL/security scanning, Codecov, auto-merge
   bots, label bots. For each: what it does, maintenance burden, whether it's
   worth it for a solo maintainer.

2. **GitHub Release Automation** — Best practice for creating GitHub Releases
   automatically from tags. Compare: release-drafter, gh-action-tag,
   softprops/action-gh-release, manual `gh release create`. Include changelog
   extraction patterns.

3. **Branch Protection & Merge Settings** — What rules to set on main for a
   solo-maintained repo that still wants quality gates. Required status checks,
   linear history, auto-delete branches, merge method preferences.

4. **CI Enhancements** — Python version matrix testing (3.11/3.12/3.13), coverage
   reporting and badge thresholds, caching strategies for uv, artifact retention
   policies. Should we add pyright/mypy type checking to CI?

5. **Dependabot vs Renovate** — Detailed comparison for this specific project.
   Grouping strategies, automerge for patch/minor, security-only mode, config
   examples for both.

6. **Pre-commit & Contributor Tooling** — Should a solo project use
   .pre-commit-config.yaml? What hooks? Does it add friction for a project
   that already has ruff + CI?

7. **Going Public Checklist** — What to do before flipping a private repo to
   public. Secret scanning, credential audit, license audit, README review,
   topic/description verification, initial GitHub Release, social preview image.

8. **Issue & PR Templates** — Minimal effective templates for bug reports,
   feature requests, and PRs. Show actual YAML/markdown. Keep them short —
   long templates discourage contributions.

For each recommendation, classify as:
- **Do now** — clear ROI, low effort
- **Do before going public** — necessary for public perception
- **Do when community grows** — premature for a solo maintainer
- **Skip** — not worth it for this project type

<grounding_instructions>
You are a senior open-source maintainer with 10+ years of experience shipping
Python developer tools. You've personally managed repos from 0 to 10K+ stars
and know exactly which automation pays for itself and which creates maintenance
busywork. You are deeply skeptical of "best practice" cargo-culting — you only
recommend what you'd actually set up on your own projects.

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

</details>

---

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
