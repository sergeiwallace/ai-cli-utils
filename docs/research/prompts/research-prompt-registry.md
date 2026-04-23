---
title: Research Prompt Registry
category: research-prompts
tags: [research, prompts]
status: active
source: project-template
---

Central registry for all research prompts for this project. Tracks prompt text,
model, submission status, and links to result docs. Pending/ready prompts listed
first (highest priority at top), completed below.

**References (project references):**
- Zero-slop anchor design: `docs/research/zero-slop-anchor-design.md`
- Anchor procedure: `docs/procedures/gemini-deep-think-anchor.md`

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
- `deep-research` — broad multi-source research requiring web + reasoning

**Prompt patterns:**
- **Gap-fill / temporal-scoping** — add a hard constraint inside `<grounding_instructions>`: "if [period] is genuinely thin, '[period]: no significant new developments found' is the correct answer. Do not backfill with [earlier period] sources. Backfilling is a failure mode, not a hedge." Generic `[NO SOURCE FOUND]` alone is insufficient — naming the failure prevents it.
- **Follow-up / sequential runs** — add a `## Background` section at the top of the prompt body (before questions, outside `<grounding_instructions>`) summarizing what prior runs found: "Assume all Background points are established and do not re-derive them." This scopes the model to the delta.

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
```text

## Table of Contents

- [Pending / Ready](#pending--ready)
- [Completed](#completed)
  - [R-1: Open-source Python CLI package best practices (SW-672)](#r-1-open-source-python-cli-package-best-practices--sw-672)
  - [R-2: GitHub repo automation & ecosystem tooling (AI-CLI-3)](#r-2-github-repository-automation--ecosystem-tooling)
  - [R-50: Terminal tab/pane title, color, and icon customization for AI fleet management (AI-CLI)](#r-50-terminal-tabpane-title-color-and-icon-customization-for-ai-fleet-management--ai-cli) ✅
  - [R-51: Video content analysis for agentic AI — Gemini CLI OAuth, open-source tools (AI-CLI-54)](#r-51-video-content-analysis-for-agentic-ai--gemini-cli-oauth-open-source-tools--ai-cli-54) ✅
- [Deprecated / Archived](#deprecated--archived)
  - [R-52: Computer vision and video analysis landscape for CLI integration (AI-CLI-54)](#r-52-computer-vision--video-analysis-landscape-for-cli-integration--ai-cli-54) 🗄️

## Status Overview

### Pending / Ready

| # | Type | Topic | Task | Model | Status |
|---|------|-------|------|-------|--------|
| — | — | (none pending) | — | — | — |

### Priority and Impact Notes

| # | Prompt | Priority | Model | Tasks Unlocked / Enhanced | Why Now |
|---|--------|----------|-------|--------------------------|---------|
| — | — | — | — | — | — |

### Completed

| # | Type | Topic | Task | Model | Results Doc |
|---|------|-------|------|-------|-------------|
| R-1 | opus | Open-source Python CLI package best practices | SW-672 | `opus researcher` | [`open-source-package-best-practices.md`](../open-source-package-best-practices.md) |
| R-2 | opus | GitHub repo automation & ecosystem tooling | AI-CLI-3 | `opus researcher` | [`github-repo-automation.md`](../github-repo-automation.md) |
| R-50 | deep-think | Terminal tab/pane title, color, and icon customization for AI fleet management | AI-CLI | `deep-think` | [`iterm2-terminal-customization-research.md`](../iterm2-terminal-customization-research.md) |
| R-51 | deep-think | Video content analysis for agentic AI — Gemini CLI OAuth, open-source tools (2024–2026) | AI-CLI-54 | `deep-think` | [`video-analysis-agentic-2026.md`](../video-analysis-agentic-2026.md) |

---

## Pending / Ready

---

## Completed

### R-51: Video Content Analysis for Agentic AI — Gemini CLI OAuth, Open-Source Tools — AI-CLI-54

**Status:** ✅ Complete — 2026-04-22
**Model:** `deep-think`
**Task:** `AI-CLI-54` (`ai gemini` video analysis feature)
**Output:** [`docs/research/video-analysis-agentic-2026.md`](../video-analysis-agentic-2026.md)
**Note:** Migrated from artelier repo (was R-18 there). Canonical location is ai-cli-utils.

<details>
<summary>Prompt (R-51)</summary>

```text
You are a senior AI engineer who builds agentic automation pipelines and has hands-on
experience integrating multimodal AI into CI/CD and QA tooling. You work primarily with
open-source tools, CLI-based workflows, and zero-cost or near-zero-cost solutions. You
have followed the evolution of video understanding capabilities across LLM providers,
CLI tools, and open-source Python libraries from 2024 through 2026. You distinguish
between what is documented versus what actually works in practice, and you call out
tool limitations and gotchas explicitly rather than glossing over them.

## Context

We have an agentic demo recording pipeline that:
1. Records a screen capture of a web app walkthrough (~2 min, ~2–5 MB MP4)
2. Burns in captions
3. Needs automated content validation — confirm that each scene in the recording shows
   the expected UI content (e.g., "frame at 0:10 shows an Airflow login form",
   "frame at 1:30 shows a Flask image comparison UI", "frame at 2:00 shows a Gradio app")

The constraint: **no paid API calls**. We have:
- Claude Code (the Anthropic CLI) running on macOS — with full tool use, bash execution,
  and the ability to run scripts and read/write files
- Gemini CLI (`gemini` command) authenticated via Google OAuth (free tier) — not the
  paid API. We use it as `ai gemini -m deep-think/pro/flash` via a wrapper.
- Standard macOS + Python 3.11 environment (ffmpeg available)
- Any open-source tools or libraries

## Research Questions

### 1. Claude Code native video understanding (2026 state-of-the-art)

- Can Claude Code read a local video file directly and understand its content?
- If not directly: can Claude Code extract frames using ffmpeg, then analyze those frames
  using its vision capability? What's the most effective workflow — how many frames, at
  what cadence, what prompt structure?
- What are the known limitations of Claude's vision on screen recording frames?
- Prioritize 2026 findings first, then 2025, then 2024.

### 2. Gemini CLI OAuth (free tier) video understanding (2026 state-of-the-art)

- Can the Gemini CLI (`gemini` binary, OAuth auth, free tier) accept a local video
  file as input? What's the exact invocation syntax if so?
- Is there a `@filepath` syntax, `--file` flag, or pipe mechanism that works for
  binary video files? What does the community report about actual working syntax vs
  documented syntax?
- Are there known GitHub issues, workarounds, or community patches (2025–2026) that
  enable video input in the Gemini CLI?
- What video/image file formats does free-tier Gemini CLI OAuth support?
- Prioritize 2026 findings first. Note any capability gaps vs the paid Gemini API.

### 3. Open-source Python tools for video content understanding (2024–2026)

**Frame extraction + vision model analysis:**
- What are the best open-source vision models for screen recording analysis
  (UI element recognition, text OCR, layout understanding)?
- Which models run efficiently on macOS CPU (no GPU required for a 2-min clip)?
- Compare: CLIP, LLaVA, Qwen-VL, InternVL, MiniCPM-V, Moondream, and any 2025–2026
  releases specifically optimized for UI/screenshot understanding.

**OCR-based approaches:**
- For screen recordings showing web app UIs: is OCR (e.g., pytesseract, EasyOCR,
  Surya, docTR) sufficient to detect UI scenes by reading text labels?
- What's the performance profile (speed, accuracy) of OCR-only vs vision model analysis?

**Scene detection:**
- PySceneDetect, scenedetect — can these identify when the active tab/URL changes
  in a browser recording? What's the detection accuracy for UI transitions?

### 4. Practical recommendation

Given the constraints (no paid API, macOS, ~2 MB MP4, agentic pipeline via bash/Claude Code),
provide a concrete ranked recommendation: Option A (best overall), Option B (fallback),
Option C (if nothing reliable exists). For each option: tools needed, runtime, downloads
required, accuracy expectations for detecting login forms, DAG graphs, Flask UIs, Gradio apps.

### 5. Gemini video processing: API-only or also available via Gemini CLI OAuth?

- Is Gemini's video/multimodal file understanding exclusively via paid REST API, or also
  accessible through Gemini CLI OAuth (free tier)?
- Does a Google AI Ultra subscription change what's available via Gemini CLI OAuth?
- Are there any 2025–2026 announcements, GitHub issues, or community reports confirming
  whether `gemini` CLI OAuth users can pass video files?
```

<grounding_instructions>
You are a senior AI engineer who builds agentic automation pipelines integrating
multimodal AI into CI/CD and QA tooling, with hands-on experience across open-source
video understanding tools, CLI-based LLM workflows, and screen recording analysis.
You have followed the field from 2024 through 2026 and distinguish documented behavior
from community-verified practical behavior.

Before generating your final output, execute a Chain-of-Verification (CoVe)
to ensure factual fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because
   the prompt implied you should, rather than because you verified it. Pay special
   attention to CLI tool capabilities — these change rapidly and documentation often
   lags behind or overstates what actually works.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale
before appending the tag — writing the tag first causes post-hoc rationalization.
Rationale → evidence check → tag.

- [VERIFIABLE FACT]: backed by documentation, GitHub commits/issues, release notes,
  or official announcements (2024–2026). Provide the direct URL or commit SHA.
- [INDUSTRY HEURISTIC]: widely accepted best practice without a specific citation.
- [SYNTHESIZED INFERENCE]: a logical conclusion drawn from context. Provide reasoning.
  Do not fabricate a source.
- [NO SOURCE FOUND]: explicitly state when you cannot find verifiable data for a
  specific year or capability. Do not backfill with older data without disclosure.

Hard constraint: never invent a citation to satisfy a formatting instruction.
Accuracy > completeness. A gap in the research is more valuable than a fabricated answer.

Temporal prioritization: search for 2026 developments first. If 2026 is thin,
explicitly state so, then report 2025 findings. If 2025 is also thin, report 2024.
Always disclose which year a finding comes from.
</grounding_instructions>

</details>

---

### R-1: Open-Source Python CLI Package Best Practices — SW-672

**Status:** ✅ Complete
**Task:** SW-672 (Extract ai-cli to standalone repo)
**Results:** [`docs/research/open-source-package-best-practices.md`](../open-source-package-best-practices.md)
**Model:** `opus researcher`
**Date:** 2026-03-29

<details>
<summary>Prompt (R-1)</summary>

```text
Research best practices for maintaining a professional open-source Python CLI
package on GitHub + PyPI. The package is `ai-cli-utils` (command: `ai`), a
unified CLI for managing Claude Code and Gemini CLI sessions with tmux, mosh,
git worktrees, and cross-machine sync. Cover: README structure and badges,
GitHub project configuration (CI, releases, community files, security), Python
packaging conventions (pyproject.toml, type hints, docs), and exemplary Python
CLI projects to emulate (1K-50K stars range).
```text

</details>

---

### R-50: Terminal Tab/Pane Title, Color, and Icon Customization for AI Fleet Management — AI-CLI

**Status:** ✅ Complete — 2026-04-02
**Model:** `deep-think` (gemini-2.5-pro with HIGH thinking)
**Task:** AI-CLI (iTerm2 tab title/color redesign)
**Results:** [`docs/research/iterm2-terminal-customization-research.md`](../iterm2-terminal-customization-research.md)

Key findings informed the iTerm2 Tab Title and Color System design (now implemented). Research covered OSC sequences for tab title/color control, iTerm2 DCS passthrough for tmux, pre-launch vs in-session layer architecture, mosh/remote session constraints, and profile-based color assignment.

---

### R-2: GitHub Repository Automation & Ecosystem Tooling

**Status:** ✅ Complete
**Task:** AI-CLI-3 (extended to cover full remaining professionalization gaps)
**Target doc:** `docs/research/github-repo-automation.md`
**Model:** `opus researcher`

<details>
<summary>Prompt (R-2)</summary>

```text
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
```text

</details>

---

<!-- Add prompts here using this format:

### R-N: [Topic] — [TASK-ID]

**Status:** Ready to run / Pending approval / Approved — run with opus researcher
**Model:** `opus researcher`
**Task:** [TASK-ID]
**Output:** `docs/research/[topic].md`

<details>
<summary>Prompt (R-N)</summary>

```text
[Full prompt text here. Must include grounding_instructions block at the end.]
```text

</details>

<details>
<summary>Context & questions</summary>

**Context:** [1-3 sentences explaining why this research is needed and what it unblocks]

**Questions to answer:**

1. **[Question title]**: [question text]
2. **[Question title]**: [question text]
...

</details>

> **Feedback:** <enter feedback here>

---

-->

## Completed

<!-- Completed prompt sections go here after being moved from Pending / Ready -->

## Deprecated / Archived

### R-52: Computer Vision & Video Analysis Landscape for CLI Integration — AI-CLI-54

**Status:** 🗄️ Archived 2026-04-23 — migrated to aido registry as R-23 (AIDO-46)
**Model:** `deep-research`
**Task:** `AI-CLI-54` (migrated to `AIDO-46` in aido repo)
**Note:** Full prompt text moved to aido research registry. Do not run here.
