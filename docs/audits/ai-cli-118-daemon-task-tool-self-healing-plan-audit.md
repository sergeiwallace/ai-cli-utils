---
title: "AI-CLI-118 daemon-restart Task* tool self-healing plan — audit"
category: audit
tags: [audit, ai-cli-118, daemon, task-tools]
status: draft
date: 2026-07-22
source: "aido-stub"
template_version: "audit-1.0.0"
---

# AI-CLI-118 daemon-restart Task* tool self-healing plan — audit

**Status:** draft

**Created:** 2026-07-22

**Auditor:** Codex review (`cx review`, effort: high) — findings incorporated by Claude

**Target commit:** (filled after commit, before launching Round 1)

<!-- aido:region name="scope" kind="replaceable" -->

## Scope

Target: `docs/plans/ai-cli-118-daemon-task-tool-self-healing-plan.md` (plan doc, `plan-1.0.0`,
task AI-CLI-118), authored in `--mode automated` by an Opus sub-agent. Scope: internal
consistency, AC-writing-practices + plan-template compliance, domain validity of the detection
heuristic (D-1..D-4) against the actual `ai-cli-utils` codebase it proposes to extend
(`quota.py`'s poll loop, `Notifier`, session/task-namespace file layout), and any independent
findings — missing prerequisites, false-positive/negative gaps in the heuristic, undocumented
assumptions about `~/.claude/daemon.log` / `~/.claude/sessions/*.json` / transcript JSONL formats.

<!-- /aido:region name="scope" -->

<!-- aido:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

(pending — Codex review in flight)

<!-- /aido:region name="round_1_findings" -->

<!-- aido:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|

<!-- /aido:region name="audit_log" -->
