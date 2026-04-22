---
title: Implementation Plan — ai gemini Video Analysis (@filepath support + auto-split + output docs)
category: plan
tags: [ai-gemini, video-analysis, gemini-cli, filepath, ai-cli-54]
status: draft
source: claude-sonnet-4-6
date: 2026-04-22
related_tasks: [AI-CLI-54]
---

# `ai gemini` Video Analysis — Implementation Plan

**Status:** DRAFT — requirements stub. The ai-cli-utils CC session must flesh this out into a
full plan conforming to `docs/plans/TEMPLATE.md` before implementation begins.

**Task:** `[AI-CLI-54]`
**Created:** 2026-04-22

---

## Background / Motivation

The bare `gemini` CLI binary supports `@filepath` syntax for local file input (including MP4
video files), but the `ai gemini` wrapper does not. Any attempt to pass `@file.mp4` as an
argument to `ai gemini` currently errors with "unrecognized arguments".

This was discovered while building a post-recording validation step for the Artelier demo
pipeline (ART-67). The demo pipeline needed agentic video content analysis — confirm that
all required UI scenes appeared in the recording — but had no way to invoke it without
dropping out of the `ai gemini` abstraction layer.

**Research already done:**

- **R-51** (`docs/research/video-analysis-agentic-2026.md` in this repo — canonical): Gemini
  CLI OAuth free-tier video ingestion, supported formats, free-tier constraints (Flash model,
  ~5min cap), open-source fallback options (Qwen2.5-VL-3B, docTR, PySceneDetect).
- **Gemini video understanding API docs:** https://ai.google.dev/gemini-api/docs/video-understanding
  — review before finalizing the design. Covers: supported MIME types, File API upload flow,
  inline vs. file-API video, timestamp-based prompting, token counts for video, and model
  support matrix.

**Important:** R-51 is acknowledged to be light on the computer vision / local model landscape.
The CC session must read R-51 critically, identify gaps (see Research Follow-up section below),
and propose a deep-research follow-up prompt before writing the Options section of the plan.

Both R-51 and the Gemini video docs must be reviewed and synthesized into the design section of
the full plan doc before any implementation tasks are written.

---

## Requirements

### R1 — `@filepath` passthrough in `ai gemini`

The `ai gemini` wrapper must support `@filepath` syntax in the prompt, passing the file
through to the underlying Gemini CLI or File API as appropriate. The `@` syntax should work
for any file type the Gemini API supports (video, image, audio, PDF, etc.), not just MP4.

### R2 — Auto-split for videos exceeding the free-tier duration cap

Free-tier Gemini Flash caps video processing at approximately 5 minutes per call. If the
input video exceeds the cap, `ai gemini` must:

1. Detect video duration via `ffprobe` (or equivalent)
2. Auto-split the video into N segments at clean boundaries (no mid-scene cuts if avoidable)
3. Process each segment independently with the same prompt (or a segment-aware prompt variant)
4. Combine the per-segment analyses into a single coherent output
5. Make the split/combine behavior transparent to the caller — the output should read as if
   the full video was processed in one call

The duration threshold should be configurable (default: 4m30s to give headroom below the
5min cap). Google AI Ultra subscribers have a 1-hour cap; the threshold should be model-aware
or user-overridable.

### R3 — Configurable analysis prompt

The caller must be able to pass a custom analysis prompt. For video analysis, the prompt
typically describes what to look for and what format to return (e.g. JSON with scene list,
timestamps, pass/fail per scene). Options:

- Inline flag: `ai gemini -m flash --video-prompt "Describe what you see" @video.mp4`
- Or: reuse the existing `-p/--prompt` mechanism if it can accommodate file args cleanly
- A set of built-in prompt templates (e.g. `--video-template scene-validation`) would be
  a nice-to-have but not required for v1

### R4 — Output to doc file (default dir + custom path override)

Video analysis output should be written to a doc file, not just printed to stdout. Behavior:

- **Default output directory:** a configurable directory (e.g. `~/.local/state/ai-cli-utils/video-analysis/`
  or a project-relative `docs/video-analysis/`) with a unique auto-generated filename
  (timestamp + source video name slug)
- **Custom path override:** `--output /path/to/output.md` (or `-o`) to specify exact file
- **Output format:** Markdown doc with frontmatter (title, date, source video path, model used,
  segments processed, prompt used). Body: the combined Gemini analysis output. If JSON was
  returned by Gemini, it should be pretty-printed in a fenced block with a human-readable
  summary above it.
- **Stdout behavior:** print the output file path on completion (so callers can pipe/capture it)

### R5 — Segment result combination

When auto-split produces multiple per-segment analyses, the combination step must:

- Merge segment outputs into a coherent whole (not just concatenate raw JSON)
- For structured JSON output (scene lists, timestamps): merge arrays and re-index timestamps
  to the full video timeline (segment N's timestamps need offset by the sum of prior segment durations)
- For prose output: concatenate with clear segment headers and a final synthesis pass
  (optionally: run a second Gemini call to synthesize the combined segments into a unified summary)
- Preserve per-segment metadata in the output doc (which segment covered which time range)

### R6 — Model and auth compatibility

- Must work with all `ai gemini` auth tiers (OAuth, REST, etc.)
- Free tier: Flash model only, ~5min cap → auto-split kicks in
- Google AI Ultra: Pro/deep-think models available, 1-hour cap → fewer/no splits needed
- The wrapper should detect which model is in use and adjust the split threshold accordingly

### R8 — PySceneDetect + docTR OCR fallback (and explicit flag override)

When Gemini is unavailable (rate-limited, offline, auth failure) or the caller explicitly
wants a fully local analysis, `ai gemini` must support a fallback pipeline using open-source
tools only:

1. **Scene detection:** `PySceneDetect` (`scenedetect detect-adaptive -t 15`) to identify
   keyframe boundaries. Use `--crop` to isolate the changing content area — UI recordings
   have a static background that defeats the default threshold.
2. **OCR:** `docTR` (the recommended 2026 default per R-51 — fastest, most robust to video
   compression artifacts) reads text from each keyframe image.
3. **Keyword assertion:** extracted text is checked against a required-keyword list (passed
   via prompt or a dedicated `--keywords` flag). Outputs a PASS/FAIL report per scene.

**Invocation options:**

- `--backend local` — always use PySceneDetect + docTR (skip Gemini entirely)
- `--backend gemini` — always use Gemini (default); fail if unavailable rather than falling back
- `--backend auto` (default) — try Gemini first; fall back to local if Gemini fails or is unavailable

**Dependency handling:** PySceneDetect and docTR are optional extras — not installed by default.
`ai gemini` should check for their presence before attempting the local pipeline and print a
clear install hint if missing:
```
pip install ai-cli-utils[video-local]
```

**Accuracy note (from R-18):** The local pipeline accuracy is moderate — it only succeeds when
the visible UI contains readable text labels. It will miss scenes whose identifying content
is purely graphical (charts, images, icons with no labels). Document this limitation clearly
in the output doc when the local backend is used.

### R7 — Error handling

- If `ffprobe` is not available: warn and attempt to process without splitting (let Gemini
  reject if too long)
- If a segment upload fails: retry once, then fail with a clear error identifying which
  segment failed and what was completed
- If the Gemini response cannot be parsed as the expected format: include raw response in
  output doc and warn the caller

---

## Open Questions for the CC Session to Resolve

1. **`@filepath` mechanism:** Does the bare `gemini` CLI File API upload happen automatically
   when `@file` is used, or does the wrapper need to call the File API directly (upload →
   poll for "Active" state → pass `file_uri` to the model)? Check the Gemini video docs
   (https://ai.google.dev/gemini-api/docs/video-understanding) and R-18 for the answer.

2. **Inline vs. File API for short videos:** For videos under ~20MB, the Gemini API supports
   inline base64 encoding instead of File API upload. Should the wrapper auto-select based
   on file size? Or always use File API for consistency?

3. **Split boundary detection:** Should splits happen at exactly N-minute intervals, or use
   `ffprobe` scene detection to find natural boundaries? Scene detection adds complexity but
   produces cleaner splits for UI recording walkthroughs.

4. **Default output dir:** Project-relative (requires knowing the current project) vs.
   user-global (`~/.local/state/ai-cli-utils/video-analysis/`)? Which is more useful for the
   primary use case (agentic pipeline validation)?

5. **Synthesis pass for multi-segment prose:** Running a second Gemini call to synthesize
   combined segments adds cost/latency. Should this be opt-in (`--synthesize`) or default?

6. **`--video-template` built-in prompts:** Worth implementing in v1 or defer to v2?

7. **docTR vs. EasyOCR vs. Surya:** R-18 recommends docTR as the 2026 default (fastest,
   most robust to compression artifacts). Confirm this is still the right choice after
   reviewing the Gemini video docs and any updated benchmarks. Should the local backend
   allow selecting the OCR engine (e.g. `--ocr-engine doctr|surya`) or just hard-code docTR?

8. **`--backend auto` fallback trigger:** Should auto-fallback trigger on any Gemini error
   (including transient network errors), or only on auth/rate-limit failures? Triggering on
   transient errors could mask real problems.

---

## Research Follow-up — Proposed Deep-Research Prompt (R-52)

R-51 is acknowledged to be light on the computer vision and local model landscape. Specifically:

- **Gap 1:** R-51 says "Claude Code cannot ingest MP4 directly" but does not explore whether
  modern frontier models (GPT-4o, Gemini 1.5 Pro, Claude 3.5+ via API) can analyze video
  frames or full video natively via their APIs in 2026. Many frontier models now have native
  video understanding — this should have been covered.
- **Gap 2:** The open-source VLM section names Qwen2.5-VL-3B, MiniCPM-V, and Moondream but
  doesn't cover: LLaVA-NeXT-Video, VideoLLaMA, InternVideo, mPLUG-Owl3, or any model
  specifically trained on screen recordings / UI interactions. These are relevant to the
  local-backend option.
- **Gap 3:** No coverage of `mlx-vlm` Python API integration, how to prompt VLMs for
  structured JSON output (scene timestamps, pass/fail), or comparative accuracy benchmarks
  on UI screenshot tasks.
- **Gap 4:** The Gemini File API flow (upload → poll → query) is described but not the
  token cost implications, rate limits per video duration, or how the API handles silent
  processing errors.

**The CC session must draft the following deep-research follow-up prompt and present it to
the user before implementation begins. Do NOT run this prompt — present it for approval.**

---

### Proposed R-52: Computer Vision & Video Analysis Landscape for CLI Integration (2025–2026)

**Proposed model:** `deep-research`
**Task:** AI-CLI-54
**Gate:** Present to user for approval before running

```text
You are a senior AI infrastructure engineer designing a CLI feature for video content
analysis. The feature (`ai gemini --backend`) must choose between: (A) cloud LLM APIs
(Gemini, Claude, GPT-4o) via file upload, (B) local open-source VLMs running on Apple
Silicon, and (C) classical CV pipelines (scene detection + OCR). You have hands-on
experience shipping Python CLI tools and have followed the open-source VLM ecosystem
from 2024 through 2026.

## Context

We are building `ai gemini @filepath` — a CLI command that accepts a local video file
and returns a structured analysis (JSON scene list, timestamps, pass/fail per scene).
The primary use case is agentic demo validation: confirm that a screen recording of a
web app walkthrough shows the expected UI screens (login form, DAG graph, Flask UI,
Gradio app) with no error pages.

Prior research (R-51) established basic Gemini CLI @filepath support and named a few
open-source models, but was thin on depth. This follow-up fills the gaps.

## Research Questions

### 1. Frontier model video understanding (2025–2026 state of the art)

For each of the following, answer: can it analyze a local MP4 via API? What's the exact
upload mechanism? What are the free-tier / rate limits? What's the token cost for a 5-min
1080p recording? What structured output formats does it support?

- **Gemini 1.5 Flash / Pro** via File API (REST, not CLI)
- **GPT-4o** via OpenAI Files API
- **Claude 3.5+ Sonnet / Claude 4** via Anthropic API (vision on extracted frames vs
  native video — has this changed in 2026?)
- Any other frontier model with native video understanding announced 2025–2026

### 2. Open-source VLMs for screen recording / UI analysis (2025–2026)

Beyond Qwen2.5-VL-3B, MiniCPM-V, and Moondream (covered in R-51), research:

- **LLaVA-NeXT-Video**, **VideoLLaMA 2/3**, **InternVideo 2.5** — do these run on
  Apple Silicon CPU? What are the model sizes and MLX compatibility status?
- **mPLUG-Owl3**, **Aria**, or any 2025–2026 multimodal model specifically designed
  for long video or document/UI understanding
- **UI-specific models:** any VLMs fine-tuned on screen recordings, UI screenshots,
  or web app navigation tasks (e.g., models from the GUI Agent / WebVoyager space)
- For each: macOS CPU runtime estimate for a 2-min 1080p recording, model download size,
  `mlx-vlm` or `ollama` compatibility, structured JSON output support

### 3. mlx-vlm Python API integration patterns

For VLMs running via `mlx-vlm` on Apple Silicon:
- What is the correct Python API for batch frame analysis? (not just CLI invocation)
- How do you prompt for structured JSON output (scene classification, timestamp, pass/fail)?
- What is the practical throughput (frames/second) for Qwen2.5-VL-3B on M1/M2/M3?
- Are there known accuracy issues on Retina/HiDPI screen captures (2x pixel density)?

### 4. Classical CV pipeline accuracy benchmarks

For PySceneDetect + docTR OCR on UI screen recordings:
- Concrete accuracy numbers: what % of UI transitions does detect-adaptive catch at
  threshold 15 vs 25 vs 50 on browser recordings with static backgrounds?
- docTR character error rate on HiDPI browser text (Chrome, Retina display)?
- Are there 2025–2026 alternatives to PySceneDetect that handle static-background UI
  recordings better?

### 5. CLI design patterns for multi-backend video analysis tools

Survey existing CLI tools (2024–2026) that implement a --backend flag for AI video analysis:
- What flag/config patterns are established (auto/local/cloud, model selection, output format)?
- How do similar tools handle the auto-split + combine problem for long videos?
- Any prior art for streaming analysis results as segments complete?

### 6. Practical recommendation for CLI implementation

Given the constraints (Python CLI, macOS primary, Linux secondary, optional extras via pip,
no mandatory cloud API key):

Recommend the concrete backend stack for each tier:
- **Tier 1 (default, zero-install):** what to use when only `gemini` binary is available
- **Tier 2 (optional local):** best single open-source model + library combo for
  `ai-cli-utils[video-local]` extra — balance accuracy, download size, and M-series perf
- **Tier 3 (classical CV, ultra-fast):** PySceneDetect + what OCR engine for the fastest
  possible local analysis (no LLM weights required)

For each tier: exact package names, install command, Python import, and a 5-line code
sketch of how to invoke it for a single frame classification.
```

<grounding_instructions>
[ROLE: senior AI infrastructure engineer with hands-on experience shipping Python CLI tools
integrating multimodal AI, open-source VLMs on Apple Silicon, and classical CV pipelines.
You have followed the open-source video understanding ecosystem from 2024 through 2026 and
distinguish documented capabilities from community-verified practical behavior on macOS CPU.]

Before generating your final output, execute a Chain-of-Verification (CoVe).

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because the
   prompt implied you should, rather than because you verified it. Pay special attention
   to model capabilities and MLX compatibility — these change rapidly.
4. Strip away any claim that cannot be empirically verified.

Classify every major claim:
- [VERIFIABLE FACT]: backed by docs, GitHub commits/issues, release notes, or official
  announcements (2024–2026). Provide the direct URL or commit SHA.
- [INDUSTRY HEURISTIC]: widely accepted best practice without a specific citation.
- [SYNTHESIZED INFERENCE]: logical conclusion from context. Provide reasoning. No fabricated sources.
- [NO SOURCE FOUND]: explicitly state when you cannot find verifiable data.

Hard constraint: never invent a citation. Accuracy > completeness.
Temporal scope: 2026 first, then 2025, then 2024. Always disclose which year.
</grounding_instructions>

---

## Instructions for the ai-cli-utils CC Session

This is a requirements stub. Before implementation can begin, the CC session must:

1. **Read and synthesize the reference material:**
   - R-51: `docs/research/video-analysis-agentic-2026.md` (in this repo — canonical) — covers
     Gemini CLI `@filepath` syntax, free-tier limits, and open-source fallback options. **Read
     it critically — it is acknowledged to be light. Do not treat it as complete.**
   - Gemini video understanding API docs: https://ai.google.dev/gemini-api/docs/video-understanding
     — covers File API upload flow, inline vs. file-API, timestamp prompting, token limits

2. **Review R-51 gaps and present R-52 for approval:**
   - Read the `## Research Follow-up — Proposed Deep-Research Prompt (R-52)` section above
   - Assess whether R-51 is sufficient for the Options section, or whether R-52 is needed
     to fill the gaps (computer vision landscape, frontier model video APIs, mlx-vlm patterns)
   - Present the R-52 prompt to the user with your assessment. **Do NOT run it — present and wait.**
   - If user approves: register R-52 in the registry, run it, write output to
     `docs/research/cv-video-analysis-2026.md`, commit and push, then continue to step 3
   - If user defers: note in the plan doc which Options sections are uncertain pending R-52

3. **Flesh out this stub into a full plan doc** conforming to `docs/plans/TEMPLATE.md`:
   - Add Options section (at least 2 implementation approaches with pros/cons + recommendation)
   - Expand Task Breakdown into concrete T-01/T-02/... tasks with sizes, deliverables, and ACs
   - Add Batch Plan table
   - Add Human Gates
   - Resolve or document the Open Questions above (some can be answered by reading the docs)
   - Update frontmatter: `status: pending_review`

4. **Ship git changes** — commit and push the updated plan doc to `origin/main`

5. **Present to user for review** — paste a summary of the plan and key decisions, then wait
   for approval before starting implementation

6. **Once the plan is approved:**
   - Create internal CC tasks via `TaskCreate` for each T-N task in the plan so implementation
     progress can be tracked in the CC TUI
   - Ship another git commit with any plan doc updates from the approval round
   - Update the ai-cli-utils CC session memories as warranted (new design decisions, open
     questions resolved, etc.)
   - Only then begin implementation

---

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-22 | Requirements stub created | Initial scope from user. CC session to expand into full plan before implementation. |
