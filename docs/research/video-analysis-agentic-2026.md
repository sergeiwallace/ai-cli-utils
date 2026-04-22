---
title: Video Content Analysis for Agentic AI — Claude Code, Gemini CLI OAuth, Open-Source Tools (2024–2026)
category: research
tags: [video-analysis, gemini-cli, claude-code, ocr, ai-gemini, filepath, ai-cli-54]
status: complete
source: gemini-deep-think (OAuth), 2026-04-22
---

State-of-the-art for local, zero-cost video understanding for agentic demo recording pipelines.

*Migrated from artelier repo (was R-18 there). Canonical location: ai-cli-utils R-51.*

---

## 1. Claude Code Native Video Understanding (2026)

*Rationale: Anthropic's issue trackers and community workflows in 2025–2026 confirm that native video ingestion (where the model "watches" an MP4 directly) is not yet supported in the CLI. It handles images via clipboard/drag-and-drop, but relies on workarounds for video.*

**[VERIFIABLE FACT]** Claude Code does **not** natively support direct MP4 file ingestion as of early 2026. Native video analysis remains a high-priority tracked feature (e.g., Issue #32130).

*Rationale: The standard community approach for analyzing video with Claude Code is extracting frames at a set interval.*

**[INDUSTRY HEURISTIC]** **The Workflow:** The standard workaround is to have Claude Code use its bash execution tool to run `ffmpeg` (e.g., `ffmpeg -i video.mp4 -vf fps=1 frame_%04d.png`), and then pass the extracted frames to Claude's vision capabilities. For a 2-minute web app walkthrough, a sampling rate of 1 frame per second (1 fps) is standard.

*Rationale: Frame-by-frame analysis loses temporal data like smooth animations or audio cues, though Claude 3.7 Sonnet is exceptional at static UI debugging.*

**[SYNTHESIZED INFERENCE]** **Limitations:** Claude's vision excels at static UI debugging (CSS alignment, reading diagrams), but this frame-extraction workflow completely loses audio context and struggles to interpret smooth CSS transitions or fast pop-up modals that might fall between the 1-second extraction gaps.

---

## 2. Gemini CLI OAuth (Free Tier) Video Understanding (2026)

*Rationale: Google's official documentation for the Gemini CLI confirms the use of the `@` syntax for local file inclusion, which automatically utilizes the Gemini File API for files up to 2GB.*

**[VERIFIABLE FACT]** **Direct Video Input:** Yes, the Gemini CLI supports direct video ingestion. The working syntax is **`@filepath`** embedded in the prompt (e.g., `gemini --yolo -p "Summarize this UI flow @demo.mp4"`). For videos, the CLI automatically uploads the MP4 to the Gemini File API in the background, waits for the "Active" processing state, and then executes the prompt.

**Important:** The `@filepath` syntax works in the **bare `gemini` binary** only. Wrapper scripts that parse arguments before passing to `gemini` (e.g. `ai gemini`) will error with "unrecognized arguments" if `@file` is passed as a separate CLI argument. The `@` must be embedded within the prompt string itself.

*Rationale: The Gemini File API supports standard web video formats.*

**[VERIFIABLE FACT]** **Supported Formats:** MP4, WEBM, MOV, and AVI.

*Rationale: Google AI Ultra documentation explicitly details increased daily CLI limits, longer video duration windows, and access to "Pro" models which were removed from the API free tier in 2026.*

**[VERIFIABLE FACT]** **Google AI Ultra vs. Free Tier:** Both tiers support video via the `@` syntax. However, the Free tier restricts video duration (capped at ~5 minutes per prompt) and limits you to the "Flash" models. A Google AI Ultra subscription increases the daily CLI request limit to 2,000, extends the video processing window to 1 hour, and unlocks the "Pro" and "Deep Think" models.

---

## 3. Open-Source Python Tools for Video Content (2024–2026)

### Vision Language Models (VLMs) on macOS CPU

*Rationale: 2025/2026 benchmarks for UI navigation (like ScreenSpot) highlight Qwen2.5-VL-3B as the top agentic model, MiniCPM-V 2.6 for high-res OCR, and Moondream (April 2025 update) for raw efficiency.*

**[VERIFIABLE FACT]**

* **Qwen2.5-VL-3B (or Qwen3-VL-A3B):** The 2026 gold standard for UI/agentic tasks. Excels at coordinate detection and complex layouts. Highly optimized for Apple Silicon via `mlx-vlm`.
* **MiniCPM-V 2.6 (8B):** Best for high-resolution OCR due to its dynamic "Pan & Scan" image splitting, but heavier on CPU/RAM than Qwen.
* **Moondream (1.6B):** The efficiency option. The April 2025 release added structured outputs and significantly improved UI navigation. Runs near-instantly on base M-series Macs with a <2GB footprint.

### OCR Approaches

*Rationale: docTR is widely benchmarked as the fastest production-ready OCR pipeline compared to Surya's heavy layout focus and EasyOCR's legacy CRAFT architecture.*

**[VERIFIABLE FACT]**

* **docTR:** The "Smart Default" for 2026. Fastest, highly robust to video compression artifacts, production-ready for frame-by-frame analysis.
* **Surya:** The layout specialist. Excellent for understanding complex UI structures (tables, sidebars) but slower.
* **EasyOCR:** Considered legacy by 2026 standards; trails in character error rate (CER) for dense UIs.

### Scene Detection

*Rationale: Testing screen recordings with PySceneDetect often fails because the background is static; users must lower thresholds or crop to detect small UI changes.*

**[INDUSTRY HEURISTIC]** **PySceneDetect (v0.6.7+):** Default accuracy for UI transitions is poor because UI changes (like a modal appearing) don't trigger large enough pixel-color shifts against a static browser background. To make it work, use the `detect-adaptive` algorithm, lower the threshold (`-t 15`), and use `--crop` to isolate the changing content area.

---

## 4. Practical Recommendation for the Demo Pipeline

Given the constraints (macOS, no paid API, 2-minute 2MB MP4, agentic pipeline via Claude Code):

### Option A — Gemini CLI `@` Workflow (Best Overall)

*Rationale: Combining Claude Code's bash execution with the Gemini CLI's free OAuth video ingestion provides a zero-cost, multi-modal pipeline without managing local weights or frame extraction overhead.*

**[SYNTHESIZED INFERENCE]**

* **The Workflow:** Claude Code writes/executes a bash script that calls the bare `gemini` binary with the video file embedded in the prompt:

  ```bash
  VALIDATION_JSON=$(gemini --yolo -p "Validate this recording. Return JSON with scene list. @$RECORDING" 2>&1)
  ```

  Claude Code reads the CLI output to confirm pipeline success.

* **Tools needed:** Existing `gemini` CLI binary — no additional installs.
* **Runtime:** ~15–30 seconds (bottlenecked by upload speed and Gemini's server queue).
* **Downloads:** None.
* **Accuracy:** Exceptional for UI form detection — far superior to OCR alone.
* **Note:** Do NOT use `ai gemini ... @file` — the wrapper strips `@` args. Use bare `gemini --yolo -p "prompt @file"`.

### Option B — Fully Local Qwen2.5-VL-3B via MLX (Best Offline Fallback)

*Rationale: Qwen2.5-VL-3B running on MLX is the most capable open-source model that fits comfortably on a Mac CPU for UI tasks — best if Gemini CLI rate limits become an issue.*

**[SYNTHESIZED INFERENCE]**

* **The Workflow:**
  1. `ffmpeg -i demo.mp4 -vf fps=1 /tmp/frames/frame_%04d.jpg`
  2. Python script using `mlx-vlm` loads Qwen2.5-VL-3B
  3. Loop through frames, prompt the VLM to classify UI state
* **Tools needed:** `ffmpeg`, `mlx-vlm` package
* **Runtime:** ~45–60 seconds (~120 frames on Apple Silicon CPU)
* **Downloads:** ~2–3GB model download required
* **Accuracy:** High for UI element recognition

### Option C — PySceneDetect + docTR (Fastest Non-LLM Approach)

*Rationale: If VLMs are too slow or memory-intensive, classic computer vision (scene detection + OCR) is the fastest deterministic method, provided the UI has distinct text labels.*

**[SYNTHESIZED INFERENCE]**

* **The Workflow:**
  1. `scenedetect -i demo.mp4 detect-adaptive -t 15 save-images`
  2. Python script with `doctr` reads OCR text from each keyframe
  3. Assert extracted text contains required keywords ("Airflow", "Flask", "Gradio")
* **Tools needed:** `scenedetect`, `doctr` (PyTorch backend)
* **Runtime:** <10 seconds
* **Downloads:** ~100MB for OCR weights
* **Accuracy:** Moderate — fails if the visible UI doesn't contain explicit readable text labels

---

## Appendix: Research Prompt

**Registry ID:** R-51 (ai-cli-utils) / R-18 (artelier — original)
**Model:** `deep-think` (Gemini CLI OAuth, free tier)
**Date:** 2026-04-22

Full prompt text: see `docs/research/prompts/research-prompt-registry.md` § R-51
