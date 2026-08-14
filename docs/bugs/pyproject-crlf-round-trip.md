---
title: "pyproject.toml is committed as CRLF and every `ai update` re-dirties it"
category: bugs
tags: [update, git, line-endings, crlf, pyproject]
status: fix-deployed
severity: P2
related_docs:
  - CHANGELOG.md
  - .gitattributes
---

# pyproject.toml is committed as CRLF and every `ai update` re-dirties it

**Status:** fix-deployed

**Severity:** P2 — no data loss, but the checkout reports a permanent 172-line
phantom diff that has already been mistaken for another session's uncommitted
work, and a `git add -A` would commit the flip back the other way.

**Created:** 2026-08-14

**Task:** `AI-CLI-mbci` (`AI-CLI-pyproject-toml-committed-gdrw`)

## Symptoms

On a fresh checkout, with no edits made:

```text
git diff --stat                     172 insertions(+), 172 deletions(-)
git diff --ignore-all-space --stat  (empty)
```

The whole file reads as rewritten while its content is identical. The version
line is `version = "0.7.0"` on both sides, which rules out the other candidate
explanation — an `ai update` interrupted between the version bump and the
restore, which would leave a `.post<timestamp>` version behind.

## Root cause

Two independent causes, both required for the loop.

**1. The committed blob is CRLF while the rest of the repository is LF.**
`pyproject.toml` was LF for its entire history until one commit authored on a
Windows host. That commit's message claims `CRLF -> LF normalization (content
unchanged)`; decoding the blob at every commit that has ever touched the path
shows it did the inverse and committed the file as CRLF. Nothing caught it,
because the repository had no `.gitattributes` at all — `.editorconfig` declares
`end_of_line = lf`, but that binds editors, not git.

**2. `ai update` rewrote the line endings as a side effect.** The version bump
round-tripped the file through `Path.read_text()` and `Path.write_text()`.
`read_text()` applies universal-newline translation, so a CRLF file arrives as
LF in memory; the `finally:` restore then wrote that LF text back to disk. The
bump was reverted, the endings were not. Every update run therefore re-dirtied
the file, and the `git checkout -- pyproject.toml` that opens the next update
discarded the evidence — which is why this stayed invisible for so long.

## Fix

- `.gitattributes` pins every text file to `eol=lf` in the index and in every
  checkout, and `pyproject.toml` is normalized to LF in the same commit. The
  flip cannot recur on any platform.
- The version bump reads and writes **bytes** (`read_bytes()` / `write_bytes()`),
  so the restore is byte-identical whatever the file's endings are — including
  when the install between the two writes raises.

## Regression tests

`tests/test_update_pyproject_bytes.py`. Every assertion reads the file in binary
mode: the same assertions written with `read_text()` normalize the exact
difference under test and pass against the unfixed code. The suite covers a CRLF
file restored byte-for-byte, only the version bytes differing at install time, an
interrupted install, an LF control proving the fix is ending-agnostic, and the
repository invariant that the committed blob carries no CRLF.
