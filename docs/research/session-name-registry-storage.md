---
title: "Git-tracked storage for the session-name registry"
category: research
tags: [research, name-registry, dolt, sqlite, git, storage, concurrency]
status: stub
source: "claude-adhoc-2026-11-11"
template_version: "research-1.2.0"
task: AI-CLI-6xud
delegation_provenance:
  version: 2
  contributors: []
---

# Git-tracked storage for the session-name registry

## Founding Ask Coverage

`founding_ask_ref`: pending capture.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| pending | pending |

**Status:** stub

**Created:** 2026-11-11

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Context](#context)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

`ai c` and `migrate-session` give every coding-agent session a short human name, and the registry
behind those names must keep each name unique across every machine that runs sessions. Today the
registry is one JSON file per name on a dedicated git branch, written by fast-forward push. The
owner asked for a git-tracked database with a robust schema instead: a general approval status per
name, approvals and rejections scoped to a repository, a host or a session, reserved, in-use and
migrated state, and a history of changes, all searchable. He leans to Dolt and asked for a
full-breadth comparison of git-tracked storage options, including ones nobody had named, before
the design commits to one.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

(synthesis will populate this region)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none yet; auto-populated by verify_and_remediate node)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

```text
[Full prompt text here]
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

(none yet; auto-populated at commit from the validated provenance ledger)

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

<!-- /doc:region name="run_history" -->
