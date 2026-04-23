---
title: Implementation Plan — ai gemini Video Analysis (@filepath support, auto-split, output docs)
category: plan
tags: [ai-gemini, video-analysis, gemini-cli, filepath, ai-cli-54]
status: archived
source: claude-sonnet-4-6
date: 2026-04-22
related_tasks: [AI-CLI-54]
---

> **ARCHIVED 2026-04-23:** This plan has been migrated to aido as `AIDO-46`. Canonical location:
> `~/projects/aido/docs/plans/aido-video-analysis-plan.md`. R-52 research prompt migrated to aido research registry as R-23. Do not implement here.

# `ai gemini` Video Analysis — Implementation Plan

**Status:** PENDING REVIEW

**Task:** `[AI-CLI-54]`
**Created:** 2026-04-22
**Research:** R-51 (`docs/research/video-analysis-agentic-2026.md`)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

- [Overview](#overview)
- [Background and Research](#background-and-research)
- [Requirements](#requirements)
- [Open Questions — Resolved](#open-questions--resolved)
- [Open Questions — Deferred to R-52](#open-questions--deferred-to-r-52)
- [Options](#options)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Approval Log](#approval-log)

## Overview

Add `@filepath` video (and general file) analysis to `ai gemini`. When a prompt contains an `@filepath` reference to a video file, route through the bare `gemini` CLI (OAuth tier) which handles File API upload automatically. For videos exceeding the free-tier cap (~4m30s), auto-split with `ffmpeg`, analyze each segment, and combine results into a single output doc. Optionally support a fully local fallback backend via PySceneDetect + docTR OCR as an `ai-cli-utils[video-local]` optional extra.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Background and Research

The bare `gemini` CLI supports `@filepath` syntax — embedding `@demo.mp4` inside the `-p` prompt string causes the CLI to upload the file via the Gemini File API and wait for "Active" state before executing the prompt. This is the **only** correct invocation path; passing `@file` as a separate CLI argument causes an "unrecognized arguments" error.

**Key finding from R-51:** `_try_gemini_cli` in `gemini.py` already calls `gemini -p <prompt> -m <model> --yolo`, which means if the prompt string contains `@filepath`, the binary receives it correctly. The main implementation gaps are:

1. **Tier routing:** REST API tiers 2 and 3 do not understand `@filepath` — we must force OAuth tier (tier 1) for video calls.
2. **Duration limit:** Free-tier Flash caps at ~5 minutes per call. Videos longer than 4m30s need auto-splitting.
3. **Result combination:** Multi-segment outputs need timestamp offsetting (JSON) or structured concatenation (prose).
4. **Output doc:** Structured markdown output to a configurable directory.
5. **Local backend:** Optional PySceneDetect + docTR fallback for fully offline analysis.

**R-52 proposed:** R-51 is light on the computer vision landscape, frontier model File API details, and mlx-vlm Python integration patterns. A deep-research follow-up (R-52, registered in the research prompt registry) is proposed to fill these gaps — primarily relevant before implementing the local backend (T-05). R-52 does NOT block T-01 through T-04.

## Requirements

### R1 — `@filepath` passthrough in `ai gemini`

Support `@filepath` in prompt strings. Route such calls through OAuth CLI tier only (REST tiers don't support the file upload protocol). Any supported Gemini file type (video, image, audio, PDF) should work via the same mechanism.

### R2 — Auto-split for videos exceeding the free-tier duration cap

When an `@filepath` video exceeds the configurable threshold (default: **4m30s**):
1. Detect duration via `ffprobe`
2. Split into segments using `ffmpeg` at time-based boundaries
3. Process each segment with the same prompt
4. Combine outputs (R5)

Google AI Ultra users have a 1-hour cap; the threshold must be model-aware (Flash: 4m30s default) or user-overridable via `--split-threshold`.

### R3 — Configurable analysis prompt

Use the existing `-p/--prompt` mechanism. The `@filepath` reference is embedded in the prompt string by the caller. No separate `--video-prompt` flag needed — the existing prompt arg handles it.

### R4 — Output to markdown doc

- **Default directory:** `~/.local/state/ai-cli-utils/video-analysis/`
- **Filename:** `{timestamp}-{video-slug}.md`
- **Custom path:** `-o/--output` flag (existing flag, same behavior as text output)
- **Frontmatter:** title, date, source video path, model used, segments processed, prompt used
- **Stdout:** print output file path on completion

### R5 — Segment result combination

For multi-segment analysis:
- **JSON output:** merge arrays, offset timestamps by sum of prior segment durations; wrap in unified JSON with per-segment metadata
- **Prose output:** concatenate with clear `## Segment N (0:00–4:30)` headers; optional synthesis pass via `--synthesize` (second Gemini call)
- Per-segment time ranges included in the output doc

### R6 — Model and auth compatibility

- `@filepath` forces `start_tier=1` (OAuth CLI). Tiers 2/3 silently don't support this and would fail opaquely — better to fail fast with a clear message.
- Flash model: 4m30s threshold (configurable)
- Pro/deep-think (Ultra): 55-minute threshold or `--no-split` to disable

### R7 — Error handling

- `ffprobe` not found: warn and attempt without splitting (let Gemini reject if too long)
- Segment upload fail: retry once, then fail with clear error identifying which segment
- Unparseable Gemini response: include raw response in output doc with a warning

### R8 — Local backend (`[video-local]` optional extra)

`--backend local`: PySceneDetect + docTR OCR pipeline. Fully offline, no Gemini required.
- `--backend gemini` (default): OAuth CLI; fail if unavailable
- `--backend auto`: try Gemini; fall back to local on auth/rate-limit failure only (not transient errors)

Dependency gate: check for PySceneDetect + docTR before attempting local pipeline; print clear install hint if absent: `pip install ai-cli-utils[video-local]`

## Open Questions — Resolved

1. **`@filepath` mechanism** — R-51 confirms: embedding `@file` in the `-p` prompt string causes the bare `gemini` binary to handle File API upload automatically. `_try_gemini_cli` already uses `-p <prompt>`, so no change to the binary invocation — only tier routing needs updating.

2. **Inline vs. File API for short videos** — R-51 does not address inline base64 encoding. For simplicity, always use the `@filepath` (File API) path in v1. Inline encoding is an optimization for small files that can be added in v2 with information from R-52.

3. **Split boundary detection** — Use time-based splits (simpler). PySceneDetect for boundary detection adds complexity without clear benefit for the primary use case (scene-validation on demo recordings where a few extra seconds per segment is acceptable).

4. **Default output dir** — `~/.local/state/ai-cli-utils/video-analysis/` (user-global, consistent with other `ai-cli-utils` state dirs). Project-relative dirs require knowing the current project, which is out of scope for a general-purpose CLI tool.

5. **Synthesis pass for multi-segment prose** — Opt-in via `--synthesize`. Adds cost and latency; most use cases want per-segment output plus concatenation, not an extra LLM call.

6. **`--video-template` built-in prompts** — Defer to v2.

7. **`--backend auto` fallback trigger** — Only on auth/rate-limit failures (HTTP 401, 429, `RESOURCE_EXHAUSTED`). Do NOT fall back on transient network errors — that would mask real connectivity problems.

## Open Questions — Deferred to R-52

R-52 (`docs/research/prompts/research-prompt-registry.md` § R-52) is registered as a **Pending / Ready** deep-research prompt. It fills gaps in R-51 around:

- Frontier model video File APIs (GPT-4o, Claude API native video, Gemini 1.5 REST vs. CLI)
- Open-source VLMs for UI screen recordings (LLaVA-NeXT-Video, VideoLLaMA, InternVideo)
- mlx-vlm Python API patterns for structured JSON output
- Classical CV accuracy benchmarks on HiDPI/Retina browser recordings
- CLI design patterns for multi-backend video analysis tools

**R-52 is required before implementing T-05 (local backend).** It does NOT block T-01 through T-04.

## Options

### Option A: Shell through bare `gemini` binary with `@filepath` in prompt ✅ Recommended

The existing `_try_gemini_cli` already calls `gemini -p <prompt> -m <model> --yolo`. When the prompt contains `@filepath`, the binary handles File API upload automatically. Implementation requires only: (a) detect `@filepath` in the prompt and force `start_tier=1`, (b) `ffprobe`/`ffmpeg` for auto-split, (c) result combination.

**Pros:**
- Inherits all binary auth, retry, and Gemini API behavior — no reimplementation
- R-51 confirms this path works reliably for free-tier OAuth
- Minimal new code and no new runtime dependencies for the Gemini backend
- Auto-split and combination logic is independent of which Gemini invocation method is used

**Cons:**
- Tightly coupled to the `gemini` binary being installed and in PATH
- Cannot do inline base64 encoding for small files (File API always used)
- Streaming/chunked responses not possible via subprocess

### Option B: Direct Gemini File API via `google-genai` Python library

Upload the file directly using the `google-genai` SDK, poll for "Active" state, pass `file_uri` to the model via the Python API. Full control over the upload lifecycle.

**Pros:**
- Direct control over upload, polling, and retry
- Can choose inline vs. File API based on file size
- Streaming response support
- Does not require the `gemini` binary

**Cons:**
- Adds `google-genai` as a dependency (partially — it may already be a transitive dep via aido)
- Must reimplement auth fallback logic that the binary already handles
- More code; more to maintain; more to test

### Recommendation

**Option A** for v1. The binary path is confirmed working (R-51), requires the least new code, and defers File API complexity to when there's a concrete need. Option B becomes relevant if we need streaming output or want to drop the binary dependency — both of which are v2+ concerns.

## Task Breakdown

### T-01: `@filepath` detection and forced OAuth routing

**Size:** S  
**Batch:** 1

Detect `@filepath` patterns in the prompt string. When found, automatically set `start_tier=1` before calling `run_gemini()` to ensure the call goes through `_try_gemini_cli` (bare binary with OAuth), which is the only tier that supports file uploads. If a video file is detected and tier 2 or 3 would otherwise be attempted, emit a clear warning and fail rather than silently producing a wrong result.

**Deliverables:**
- `src/ai_cli/gemini.py` — `_has_filepath_in_prompt(prompt)` helper, `start_tier` override in `run_gemini()` when `@filepath` detected
- `tests/test_gemini.py` — tests for the detection and routing logic

**Acceptance criteria:**
- [ ] `run_gemini("analyze @demo.mp4", model="flash")` internally calls with `start_tier=1` when `@filepath` is present
- [ ] `run_gemini("analyze @demo.mp4", model="flash", start_tier=2)` raises a clear error (not a silent wrong-result)
- [ ] Prompts without `@filepath` are unaffected
- [ ] Test: `@filepath` detection regex matches `@file.mp4`, `@/abs/path/video.MOV`, `@./relative.mp4`; does not match `@username` or `@mention` patterns

**Dependencies:** None

---

### T-02: `ffprobe` duration detection and auto-split

**Size:** M  
**Batch:** 2

When an `@filepath` references a video file (detected by extension: `.mp4`, `.mov`, `.webm`, `.avi`) and `ffprobe` is available, check video duration. If it exceeds the split threshold, use `ffmpeg` to split into N time-based segments. Each segment is stored in a temporary directory, processed independently, and the temp dir is cleaned up after combination.

Config keys (in `~/.config/ai-cli/config.toml`):
```toml
[gemini.video]
split_threshold_s = 270  # 4m30s default for Flash
no_split = false         # set true to disable auto-split
```

**Deliverables:**
- `src/ai_cli/video.py` — new module: `probe_duration()`, `split_video()`, `video_extension()` helpers
- `src/ai_cli/gemini.py` — call `split_video()` when `@filepath` video detected and duration exceeds threshold
- `tests/test_video.py` — unit tests with mocked `ffprobe`/`ffmpeg` subprocess calls

**Acceptance criteria:**
- [ ] `probe_duration()` returns duration in seconds from `ffprobe` JSON output; returns `None` if `ffprobe` not found (no crash)
- [ ] `split_video()` creates N correctly-named segment files in a temp dir; segment count = `ceil(duration / threshold)`
- [ ] If `ffprobe` is missing, `run_gemini()` logs a warning and proceeds without splitting
- [ ] Configuration keys are read from `config.toml`; `split_threshold_s` and `no_split` work correctly
- [ ] Test: 8-minute video with 4m30s threshold → 2 segments; 4-minute video → no split

**Dependencies:** T-01

---

### T-03: Per-segment result combination

**Size:** M  
**Batch:** 2

Combine per-segment `GeminiResult` objects into a single output. Two modes:

- **JSON mode** (auto-detected when all segments return valid JSON): parse each segment's JSON, offset timestamps by `sum(prior_segment_durations)`, merge arrays into a unified structure. Each merged item includes a `segment` field indicating which segment it came from.
- **Prose mode** (default): concatenate outputs with `## Segment N (start–end)` headers. With `--synthesize`, run a second Gemini call on the combined prose to produce a unified summary.

**Deliverables:**
- `src/ai_cli/video.py` — `combine_results(results, segment_durations, synthesize=False)` function
- `tests/test_video.py` — JSON merge tests with timestamp offsets; prose concatenation tests

**Acceptance criteria:**
- [ ] JSON mode: timestamps in segment 2's output are offset by segment 1's duration
- [ ] JSON mode: `segment` field added to each array item
- [ ] JSON mode: if any segment returns invalid JSON, fall back to prose mode with a warning
- [ ] Prose mode: headers include start/end timestamps for each segment
- [ ] `--synthesize`: second `run_gemini()` call receives combined prose; result replaces concatenated output
- [ ] Test: 2-segment JSON with timestamps `[0, 45, 90]` and `[0, 30]` → combined `[0, 45, 90, 270, 300]` (segment 2 duration = 270s)

**Dependencies:** T-01, T-02

---

### T-04: Output doc generation

**Size:** S  
**Batch:** 1

Write the combined analysis to a markdown output file. Default directory: `~/.local/state/ai-cli-utils/video-analysis/`. Filename: `{YYYYMMDD-HHMMSS}-{video-slug}.md`. The `-o/--output` flag (already exists in `run_gemini`) handles custom path.

Frontmatter fields:
```yaml
title: Video analysis — {video filename}
date: {ISO timestamp}
source_video: {absolute path}
model: {model name}
backend: gemini|local
segments: {N}
prompt: {prompt text}
```

If JSON output: pretty-print in a fenced block with a prose summary header. If local backend used: include accuracy caveat (text-only detection, misses graphical content).

**Deliverables:**
- `src/ai_cli/video.py` — `write_video_analysis_doc(result, meta)` function
- `src/ai_cli/gemini.py` — call `write_video_analysis_doc` when `@filepath` video detected
- `tests/test_video.py` — output file exists, frontmatter keys present, JSON block formatted

**Acceptance criteria:**
- [ ] Output file created in default dir with correct naming pattern
- [ ] All frontmatter fields populated
- [ ] `-o/--output` custom path respected
- [ ] JSON output is pretty-printed in a fenced block
- [ ] Test: `write_video_analysis_doc` creates file with all required frontmatter keys

**Dependencies:** T-01

---

### T-05: Local backend (`--backend local`, `[video-local]` extra)

**Size:** L  
**Batch:** 3  
**Gate before start:** R-52 approved and results integrated into this plan

PySceneDetect + docTR OCR pipeline. Fully offline.

1. `scenedetect detect-adaptive -t 15 --crop` → keyframe images
2. docTR reads text from each keyframe
3. Keyword assertion: check extracted text against prompt-specified keyword list
4. Output: PASS/FAIL per scene with detected text

`--backend auto`: fall through to local only on HTTP 401/429/`RESOURCE_EXHAUSTED` from Gemini.

`pyproject.toml` optional extra:
```toml
[project.optional-dependencies]
video-local = ["scenedetect>=0.6.7", "doctr>=0.9.0"]
```

**Deliverables:**
- `src/ai_cli/video_local.py` — `run_local_backend(video_path, keywords)` function
- `src/ai_cli/gemini.py` — `--backend` flag wired to routing logic
- `pyproject.toml` — `[video-local]` optional extra
- `tests/test_video_local.py`

**Acceptance criteria:**
- [ ] `--backend local` without `[video-local]` installed: clear error with `pip install ai-cli-utils[video-local]` hint
- [ ] `--backend local` with deps installed: runs PySceneDetect + docTR, outputs PASS/FAIL per scene
- [ ] `--backend auto`: falls through to local only on auth/rate-limit errors, not transient network errors
- [ ] Local backend output doc includes accuracy caveat: "text-only detection — misses scenes with purely graphical content"
- [ ] Tests use mocked PySceneDetect and docTR outputs

**Dependencies:** T-01, T-03, T-04; R-52 results

---

### T-06: Docs update

**Size:** S  
**Batch:** 4

Update `docs/tools/ai-cli-usage.md` and `README.md` with the new video analysis feature.

**Deliverables:**
- `README.md` — add video analysis to the features table and usage section
- `docs/tools/ai-cli-usage.md` — add `ai gemini @filepath` usage examples

**Dependencies:** T-01–T-04 (at minimum)

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-04 | Foundation: `@filepath` routing + output doc | Human gate: test with a real video before proceeding |
| 2 | T-02, T-03 | Auto-split and segment combination | Human gate: verify split output on a 6+ minute video |
| 3 | T-05 | Local backend | **R-52 must be approved and integrated first** |
| 4 | T-06 | Docs | UAT |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before any coding | Approve scope, options recommendation, and batching |
| R-52 approval | Before T-05 | Approve deep-research run; integrate findings into local backend design |
| Batch 1 smoke test | After T-01 + T-04 | Manually run `ai gemini "describe @demo.mp4" -m flash` and verify output doc is created |
| Batch 2 smoke test | After T-02 + T-03 | Test with a video > 4m30s; verify correct segment count and combined output |
| UAT | After T-04 (min) | All ACs passing; output doc quality acceptable |

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-22 | Requirements stub created | Initial scope from user. |
| 2026-04-22 | Full plan written | Options A/B analyzed; Option A recommended. Open Questions resolved. R-52 registered as prerequisite for T-05. |
