---
title: "[BUG-007] Seven LLM-merge tests skipped on every machine and in CI — the module they need was never a declared dependency"
category: bugs
tags: [tests, skip, dependency, sync, llm-merge, coverage]
status: fix-deployed
severity: P2
related_docs:
  - CHANGELOG.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-007] Seven LLM-merge tests skipped on every machine and in CI — the module they need was never a declared dependency

**Status:** fix-deployed

**Severity:** P2 — no user-visible breakage, but seven tests guarding the LLM
memory-merge path (including the marker-rejection safety check that prevents a
failed merge from corrupting a memory file) have never executed in CI or on any
dev machine. The suite reported them as environment-conditional skips, which
read as benign.

**Created:** 2026-07-30

**Task:** AI-CLI-146

## Symptoms

Every full-suite run ends `… passed, 7 skipped`. All seven skips share one
reason string:

```
SKIPPED [7] tests/test_sync.py: google-genai not installed
```

The affected tests cover `_llm_merge_memory_conflict` (the Gemini-backed git
conflict-marker resolver in `src/ai_cli/sync.py`) and the `apply_pull_files`
auto-merge path that calls it.

## Root cause

The tests guard themselves with
`pytest.importorskip("google.genai", reason="google-genai not installed")`,
but `google-genai` appears **nowhere** in `pyproject.toml` — not in
`dependencies`, not in `[project.optional-dependencies]`, not in
`[dependency-groups] dev`. CI installs with `uv sync --dev`, which resolves
exactly those groups, so the module is absent in every environment that runs
the suite. An `importorskip` on a package no environment installs is not a
conditional skip — it is an unconditional one that *reports* as conditional.

The skip guard was also unnecessary for six of the seven tests: they mock
`google.genai.Client` and never make a network call. The only thing they need
is for the module to be importable so `unittest.mock.patch` can resolve the
target. The seventh (`…llm_fails_then_conflict_file_written`) clears the API
key env vars and exercises the no-key fallback, which needs nothing at all.

The runtime code path is genuinely optional by design: `sync.py` imports
`google.genai` lazily inside a `try`/`except` and degrades to writing a
`.conflict` file when it (or the API key) is missing. That design is correct
and unchanged — the defect was only that the *tests* for the path could never
run.

## Fix

Two changes, both in this repo:

1. **`pyproject.toml`** — add `google-genai>=1.0` to `[dependency-groups] dev`.
   It is a test-only dependency: the runtime import stays lazy and optional, so
   installing the package remains the user's choice and the published wheel's
   dependency set is untouched.
2. **`tests/test_sync.py`** — remove all seven `pytest.importorskip` guards.
   With the module guaranteed present in every dev/CI environment, a missing
   module should now *fail* the suite (as any other missing dev dependency
   would), not silently skip the coverage.

## Verification

- Before: `1976 passed, 7 skipped`. After: `2002 passed, 0 skipped` (the suite
  also grew by upstream tests since the baseline was recorded; the 7 formerly
  skipped tests all run and pass, and `--collect-only` counts are identical
  before/after the guard removal — nothing was lost or duplicated).
- The skips cannot silently return: with the guards deleted, an environment
  missing `google-genai` now fails at import/patch time instead of skipping.

## Lesson

`importorskip` is for dependencies that are *legitimately absent in some
supported environment* (platform-specific packages, heavyweight optionals).
When no supported environment installs the package, the guard converts "we
never test this feature" into a green checkmark. When auditing skips, ask: is
there any environment in which this test actually runs? If the answer is no,
the skip is masking a coverage gap, not handling an environment difference.

<!-- /doc:region name="summary" -->
