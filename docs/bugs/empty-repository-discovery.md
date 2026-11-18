---
title: "Empty repository discovery output"
category: bug
tags: [bug, git, session]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Empty repository discovery output

## Symptoms

A Git wrapper returning success without a common-directory path caused session
launch preflight to invent the current directory's parent as the owning repo,
or raise the nested-worktree guard when that invented path contained `.worktrees`.

## Root Cause Analysis

`session.detect_repo_root` checked the exit status but passed empty output to
`Path`. An empty path denotes the current directory, not an unknown owner.

## Fix

Return no owner when Git supplies no path. This preserves the existing failure
result and prevents fabricated repository attribution.

## Verification

The focused regression fails against the unchanged base implementation with
`RuntimeError: detect_repo_root() resolved to ...`, then passes with the fix.
It asserts that a successful empty response cannot produce an owning path.

## Lessons Learned

A successful discovery command must also provide the value being discovered.

<!-- /doc:region name="summary" -->
