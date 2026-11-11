---
title: "Migration targets bypass repository and host aliases"
category: bug
tags: [bug, session, naming, migration]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Migration target naming

## Symptoms

A syntactically plausible migration target could use a stale repository alias,
another host's alias or a zero index. Renaming artifacts then made the wrong name
look authoritative.

## Root Cause Analysis

`session_migrate.migrate_session` used separate numeric and human-name regexes.
Neither consulted the owning repository's current registration or the host-alias
registry. Copying those regexes to launch and audit would perpetuate the gap.

## Fix

`session_naming` supplies the shared grammar and owner/artifact checks. Repository
aliases use the existing project-prefix resolver in an inspection context; its
precedence is not copied. The context prevents config initialization, metadata
registration, interactive prompts and legacy XDG moves during reads. An explicitly
index-only registry remains usable without a human-name registry.

Migration validates text before lookup and checks the target's semantic owner
before any move. Cleanup of a leftover in another repository validates against
the existing owning home. Numeric legacy targets retain the configured cutover
policy. The dry-run and resume machinery is unchanged.

## Verification

Real temporary Git repositories and worktrees reproduce acceptance of wrong
repo/host aliases and a zero index on the original source. A repository whose
alias changes accepts the current alias and refuses its stale alias. Both cases
leave the original worktree intact during preview. Shared-context tests also
check numeric naming with human names disabled and inspection without writes.

## Lessons Learned

A name's shape cannot establish ownership. Resolve the actual owning home when
an operation runs in a different repository, and reuse existing alias precedence.

<!-- /doc:region name="summary" -->
