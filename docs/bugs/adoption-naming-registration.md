---
title: "Adoption bypasses naming checks in previews and leaves homes unregistered"
category: bug
tags: [bug, session, naming, adoption]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Adoption naming and registration

## Symptoms

An adoption preview could reuse a destination with the wrong repository alias.
An actual adoption created a canonical session home without a canonical-worktree
registry row, so a directory census found it as unregistered immediately after
successful adoption.

## Root Cause Analysis

`session_adopt._ensure_worktree` returned from its preview branch before reaching
`session.create_worktree`, where the shared naming check lives. `adopt_session`
created the home and moved the transcript but omitted the canonical registration
that the launch path performs.

## Fix

The preview calls the same naming validator before inspecting a destination.
Actual adoption registers the checked destination before moving any transcript.
A registration failure refuses the adoption with a remedy. Existing registries
remain authoritative; adding another registration format was rejected because
numeric and human session homes already share the canonical-worktree registry.

## Verification

Focused regressions exercise real temporary Git worktrees, verify that a wrong
alias preserves the source transcript in both modes, and check the resulting
registry row. A successful preview must leave the registry absent.

## Lessons Learned

Every path that creates a canonical home must record it. A preview must reach
the same naming check even when it does not execute the filesystem operation.

<!-- /doc:region name="summary" -->
