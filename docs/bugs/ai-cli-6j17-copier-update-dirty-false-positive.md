---
title: "Copier Update Dirty False Positive"
category: bug
tags: [bug, copier, yaml]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Copier Update Dirty False Positive

## Founding Ask Coverage

**Status:** resolved

**Severity:** P2

**Created:** 2026-09-13

**Task:** tracked bug fix

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Fix Log](#fix-log)

## Symptoms

Updating a clean Copier destination could fail before Copier applied any template changes with
`Destination repository is dirty; cannot continue`.

## Environment

The failure requires a tracked `.copier-answers.yml` with a non-ASCII value and an already-resolved
`_src_path`.

## Reproduction Steps

1. Create and commit an answers file with a literal em dash in a string value.
2. Run the Copier-update preparation path with the same `_src_path` value.
3. On the unfixed code, inspect `git status --porcelain` immediately before the Copier subprocess.

The answers file is modified even though its parsed values are unchanged.

## Root Cause Analysis

The update path loaded and unconditionally serialized the tracked answers file before invoking
Copier. PyYAML's default serialization escaped non-ASCII text, producing byte-different YAML.
Copier's own initial dirty-worktree precondition then rejected the change made by the update path.

`allow_unicode=True` preserves literal Unicode but did not make every inspected real answers file
byte-identical: several files had different wrapped-scalar indentation or line wrapping after a
load-and-dump round trip. The narrow byte-stability guard is therefore required whenever `_src_path`
is already unchanged.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-09-13 | Suspected an uninitialized external component. | Rejected by a clean-clone reproduction. |
| 2 | 2026-09-13 | Tested Unicode-preserving serialization alone. | Insufficient for all inspected answers-file formatting. |

## Fix

When the resolved source equals the stored source, the update path writes the original answers-file
text verbatim. When the source really changes, and in the restoration fallback, serialization uses
`allow_unicode=True`.

## Verification

- [x] The frozen regression test failed on unfixed code because real `git status --porcelain`
  reported `.copier-answers.yml` modified before the Copier subprocess.
- [x] The same test passes with the fix and verifies the final file bytes are unchanged.
- [x] A read-only round-trip audit covered every discoverable project answers file; the guard was
  selected because Unicode serialization alone was not byte-stable for all of them.

## Lessons Learned

Before changing a tracked file for a downstream tool that requires a clean working tree, retain the
original bytes whenever the intended semantic content is unchanged. Parsed-value equality does not
guarantee serialization equality.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-09-13 | Added byte-stability guard and real-git regression coverage. | Regression test passes. |

<!-- /doc:region name="summary" -->
