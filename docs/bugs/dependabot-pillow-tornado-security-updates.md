---
title: "[BUG-010] Locked Pillow and Tornado versions were below Dependabot security fixes"
category: bugs
tags: [dependencies, security, dependabot, pillow, tornado, uv]
status: fix-deployed
severity: P1
task: AI-CLI-166
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-010] Locked Pillow and Tornado versions were below Dependabot security fixes

**Status:** fix-deployed

**Severity:** P1 — 17 open Dependabot alerts affected dependencies in the published lockfile.

**Created:** 2026-08-01

**Task:** AI-CLI-166

## Symptoms

Dependabot reported 13 alerts against Pillow and four against Tornado. The lockfile resolved
Pillow 12.2.0 and Tornado 6.5.5, below the versions containing the applicable security fixes.

## Reproduction

Inspect the package entries in `uv.lock`:

```text
Pillow  12.2.0  (fixed version: >=12.3.0)
Tornado 6.5.5  (fixed version: >=6.5.7)
```

## Root Cause Analysis

Pillow was a direct project dependency with a floor of `>=10.0`, and the retained lockfile
resolution remained at 12.2.0. Tornado was transitive: Circus 0.19.0 depends on Tornado, and
the lockfile retained version 6.5.5. Neither dependency had a declared patched minimum, so a
normal lock refresh preserved the previously selected vulnerable versions.

Pillow is exercised by the runtime icon generator and its tests. No project import of Tornado
was found; it remains a runtime dependency of Circus. Reachability does not change the need to
remove a vulnerable dependency from the lockfile.

## Fix

- Raised the direct Pillow floor to `>=12.3.0`.
- Added a uv transitive constraint, `tornado>=6.5.7`, so Circus continues to supply Tornado
  without making it a direct public dependency.
- Regenerated `uv.lock` after applying the two floors.

## Verification

- `uv lock` resolves Pillow and Tornado at or above their patched floors.
- `uv run pytest` exercises the regenerated environment. At the time of this fix it has one
  unrelated existing failure in `tests/test_quota.py::TestQuotaStatuslinePart::test_when_no_snapshot_then_shows_placeholder_and_triggers_scrape`:
  the patched `subprocess.Popen` is not called. The dependency update does not touch that code
  path; the focused Pillow tests pass.

## Lessons Learned

Lockfile preferences can retain a vulnerable version when the manifest has no patched lower
bound. Direct dependencies need an updated declared floor; transitive dependencies need a
constraint when their parent package does not declare a sufficient minimum.

<!-- /doc:region name="summary" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Notes |
|---|---|
| 2026-08-01 | Raised Pillow's direct floor and constrained transitive Tornado to patched versions. |

<!-- /doc:region name="fix_log" -->
