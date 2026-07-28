---
title: "Docket"
category: docket
tags: [docket, review-queue, autonomous-batch]
status: stub
source: "aido-stub"
template_version: "docket-1.0.0"
---

# Docket

**Repository:** [repository name]

**Last refreshed:** YYYY-MM-DD

**Ordering:** Within each queue, order ready work by priority (P0 → P3), then by
unblock impact. `priority_override` always wins over the derived score.

**Row fields:** `bucket`, `priority`/`value`, `task_id`, `title`,
`decision_needed`/`why_ungated`, `doc_path`, `unblock_links`,
`time_criticality`, `risk_opportunity`, `confidence`, `effort`/`size`
(1/2/3/5/8/13/20), `unblock_count`, `agent_ready`, `priority_override`, and
derived `score`.

<!-- doc:region name="review_queue" kind="replaceable" -->

## Review Queue

Items requiring Sergei's decision, review, or merge; ordered priority→impact.

(none yet)

<!-- /doc:region name="review_queue" -->

<!-- doc:region name="autonomous_batch" kind="replaceable" -->

## Autonomous Batch

Ready, agent-safe work; ordered priority→impact. Only `agent_ready: true` items
belong here.

(none yet)

<!-- /doc:region name="autonomous_batch" -->

<!-- doc:region name="provenance_log" kind="append_only" -->

## Provenance Log

| Date | Change | Source / notes |
|------|--------|----------------|
| YYYY-MM-DD | Created | Generated from `docs/docket/STUB.md` |

<!-- /doc:region name="provenance_log" -->
