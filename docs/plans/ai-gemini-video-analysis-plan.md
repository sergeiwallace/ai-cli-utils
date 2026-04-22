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

- **R-18** (`docs/research/video-analysis-agentic-2026.md` in the artelier repo, or ask
  the session for a copy): Gemini CLI OAuth free-tier video ingestion, supported formats,
  free-tier constraints (Flash model, ~5min cap), and open-source fallback options.
- **Gemini video understanding API docs:** https://ai.google.dev/gemini-api/docs/video-understanding
  — review before finalizing the design. Covers: supported MIME types, File API upload flow,
  inline vs. file-API video, timestamp-based prompting, token counts for video, and model
  support matrix.

Both sources must be reviewed and synthesized into the design section of the full plan doc
before any implementation tasks are written.

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
2. **OCR:** `docTR` (the recommended 2026 default per R-18 — fastest, most robust to video
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

## Instructions for the ai-cli-utils CC Session

This is a requirements stub. Before implementation can begin, the CC session must:

1. **Read and synthesize the reference material:**
   - R-18: `docs/research/video-analysis-agentic-2026.md` (in the artelier repo, or ask
     the user for the content) — covers Gemini CLI `@filepath` syntax, free-tier limits,
     and open-source fallback options
   - Gemini video understanding API docs: https://ai.google.dev/gemini-api/docs/video-understanding
     — covers File API upload flow, inline vs. file-API, timestamp prompting, token limits

2. **Flesh out this stub into a full plan doc** conforming to `docs/plans/TEMPLATE.md`:
   - Add Options section (at least 2 implementation approaches with pros/cons + recommendation)
   - Expand Task Breakdown into concrete T-01/T-02/... tasks with sizes, deliverables, and ACs
   - Add Batch Plan table
   - Add Human Gates
   - Resolve or document the Open Questions above (some can be answered by reading the docs)
   - Update frontmatter: `status: pending_review`

3. **Ship git changes** — commit and push the updated plan doc to `origin/main`

4. **Present to user for review** — paste a summary of the plan and key decisions, then wait
   for approval before starting implementation

5. **Once the plan is approved:**
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
