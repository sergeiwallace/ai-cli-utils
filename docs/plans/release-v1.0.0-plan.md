# Release v1.0.0 — Plan

**Status:** DRAFT
**Created:** 2026-04-04
**Target version:** `1.0.0`
**Context:** Follows `docs/plans/pre-release-v0.2.0-plan.md`. v1.0.0 is a stability and feature-completeness declaration — the point at which the public API is stable, the core workflows are reliable and tested, and a new user on any platform can install and use the tool without manual configuration.

## Table of Contents

- [Overview](#overview)
- [v1.0.0 Readiness Criteria](#v100-readiness-criteria)
- [Version Milestone Map](#version-milestone-map)
- [Milestone Breakdown](#milestone-breakdown)
- [Honest Advisories](#honest-advisories)
- [Out of Scope for v1.0.0](#out-of-scope-for-v100)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

v1.0.0 is not a feature dump — it's a quality gate. The package should be cross-platform, have all core workflows tested and reliable, have no personal or proprietary references in the codebase, and be something a developer unfamiliar with your setup could clone and get running from the README alone. The gap from 0.2.0 to 1.0.0 is roughly 4–5 focused milestones, each shipping independently.

> **Feedback Round 1:**
> - <enter feedback here>

## v1.0.0 Readiness Criteria

All of the following must be true before tagging v1.0.0:

| # | Criterion | Tracking |
|---|-----------|---------|
| C1 | Test coverage ≥95%, no lazily-added `# pragma: no cover` | `[AI-CLI-17]` |
| C2 | Handoff queue: same-machine and cross-machine scenarios tested and reliable | `[AI-CLI-16]`, `[AI-CLI-21]` |
| C3 | Claude usage telemetry working end-to-end (quota scrape → DB → Slack alert) | `[AI-CLI-25]` |
| C4 | Windows out-of-box: installs and runs without manual env var config | `[AI-CLI-29]` |
| C5 | Privacy clean: zero proprietary/personal identifiers in src/ and tests/ | `[AI-CLI-30]` |
| C6 | CLI interface stable — no known planned breaking changes post-1.0 | — |
| C7 | All commands documented in README / usage reference | `[AI-CLI-3]` area |
| C8 | Demo GIF in README | `[AI-CLI-3]` |
| C9 | No known P0/P1 bugs | — |
| C10 | Security hardening complete | `[AI-CLI-13]` ✅ |

## Version Milestone Map

```
0.1.1 (current)
  ↓
0.2.0  Test quality · Process hygiene · Privacy audit · Git squash
         [AI-CLI-17] [AI-CLI-28] [AI-CLI-30]
  ↓
0.3.0  Handoff queue reliability + testing
         [AI-CLI-16] [AI-CLI-21]
  ↓
0.4.0  Usage telemetry complete
         [AI-CLI-25] [AI-CLI-23]
  ↓
0.5.0  Windows + cross-platform
         [AI-CLI-29]
  ↓
0.6.0  Polish + docs
         [AI-CLI-3] [AI-CLI-11] CLI ergonomics audit
  ↓
1.0.0  Stability declaration — all criteria met
         [AI-CLI-31] CLAUDE/GEMINI alignment enforcement
```

## Milestone Breakdown

### v0.2.0 — Foundations
**Plan:** `docs/plans/pre-release-v0.2.0-plan.md`

Test quality recovery (T-00–T-07), process hygiene (`ai ps`), privacy audit (scrub all personal/proprietary references, rename `_is_managed_platform()`), git history squash, PyPI publish.

**Criteria unlocked:** C1 (partial), C5, C10 (already done)

---

### v0.3.0 — Handoff Queue Reliability
**Tasks:** `[AI-CLI-16]`, `[AI-CLI-21]`

End-to-end testing and hardening of the 5-layer handoff pickup system. Unit and integration tests for same-machine and cross-machine scenarios. All pickup layers verified with layer attribution in `handoff-events.jsonl`. Fix any gaps found during testing.

This is the most operationally critical milestone — the handoff queue is foundational to how you work across sessions and machines. It should not be declared "working" without a proper test suite behind it.

**Criteria unlocked:** C2

---

### v0.4.0 — Usage Telemetry
**Tasks:** `[AI-CLI-25]`, `[AI-CLI-23]`

Complete the quota telemetry loop: live UAT of `ai quota scrape`, Slack webhook alert, `ai quota status/history` reading from local SQLite. Investigate native CC usage API (AI-CLI-23) — if a stable local cache or IPC exists, replace tmux scraping before v1.0.0. See advisory below.

**Criteria unlocked:** C3

---

### v0.5.0 — Cross-Platform
**Tasks:** `[AI-CLI-29]`

Windows out-of-box support: OS-aware default paths, Windows-compatible subprocess calls, no hardcoded POSIX assumptions, CI matrix expanded to include Windows. `ai setup` should work on a fresh Windows machine with no manual env var config.

**Criteria unlocked:** C4

---

### v0.6.0 — Polish and Docs
**Tasks:** `[AI-CLI-3]`, `[AI-CLI-11]`

- Demo GIF: update `demo/demo.tape` to show current feature set, human runs `vhs` and embeds in README
- Logo polish: increase stroke weight so logo holds up at small sizes
- CLI ergonomics audit: walk through the tool as a new user, identify any confusing command names, missing help text, or awkward flows
- Usage reference (`docs/tools/ai-cli-usage.md`) fully current with all commands

**Criteria unlocked:** C7, C8

---

### v1.0.0 — Stability Declaration
**Tasks:** `[AI-CLI-31]`

All criteria C1–C10 verified. CLAUDE.md/GEMINI.md alignment enforcement shipped. Explicit API stability commitment added to README ("v1.0.0+ CLI interface is stable — no breaking changes without a major version bump"). Final review pass on all public-facing docs.

---

## Honest Advisories

**1. Quota scraping is fragile — resolve before v1.0.0.**
The current implementation scrapes `/usage` output from a hidden tmux pane. This is inherently brittle: it depends on CC's output format, pane timing, and tmux availability. Before declaring v1.0.0, AI-CLI-23 (investigate native CC usage API) must be resolved. If a stable local cache or IPC exists, replace the scraper. If not, the tmux approach should at minimum have a timeout + retry strategy and clear failure modes documented. Shipping a fragile telemetry story as v1.0.0 is a credibility risk.

**2. Handoff queue needs a real test suite, not just manual validation.**
The current state is "it mostly works in happy-path testing." That's not v1.0.0 quality. AI-CLI-16 is the most important milestone between here and 1.0.0 — don't skip it or treat it as optional polish.

**3. First-run experience needs an audit before v1.0.0.**
`ai setup` is a good start but I'd want to verify end-to-end: fresh macOS, Linux, and Windows installs from `pip install ai-cli-utils` → `ai setup` → working. Any manual steps that survive that flow are regressions against C4/C6. Add this as part of the v0.6.0 docs/polish milestone.

**4. CLAUDE.md/GEMINI.md alignment (AI-CLI-31) belongs at v1.0.0, not post.**
This is a dev-workflow task, but it's also a signal of platform maturity. A project where the two agent config files drift silently is a maintenance liability. Put it at v1.0.0 as the final gate, not post.

**5. Release Drafter (AI-CLI-7) is post-1.0.**
It's only useful when there are regular external PRs. Not a v1.0.0 blocker.

---

## Out of Scope for v1.0.0

- `[AI-CLI-7]` Release Drafter — post-1.0, needs external contributor volume
- Web dashboard — not part of this package's scope
- Major new features not currently in roadmap

---

## Open Questions

1. **AI-CLI-23 resolution gate** — should confirming/ruling out a native CC usage API be a hard gate before shipping v0.4.0 telemetry? Or ship the tmux approach with documented limitations and revisit post-1.0?

> **Feedback Round 1:**
> - <enter feedback here>

## Approval Log

| Date | Round | Decision |
|------|-------|----------|
