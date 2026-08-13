---
title: "ai session-adopt fails when target session ran in a worktree (wrong CC project slug)"
category: bug
tags: [bug, session-adopt, worktree, windows, cc-migrate]
status: open
template_version: "bug-1.0.0"
---

# ai session-adopt fails when target session ran in a worktree

**Status:** open — reproduction confirmed, root cause identified, fix deferred to post-compaction /bug-fix

**Task:** AI-CLI-8s1

**Created:** 2026-08-12

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

<!-- doc:region name="summary" kind="replaceable" -->

## Summary

`ai session-adopt <name>` finds CC transcripts by computing the Claude Code project-slug
directory for the repo root (e.g. `C--Users-…-acn-automation`) and searching there for a JSONL
file whose `customTitle` field matches `<name>`. When the CC session ran inside a
**worktree** (e.g. `.worktrees/acn-1`), CC stored its transcript under the slug for
**that worktree path** (e.g. `C--Users-…-acn-automation--worktrees-acn-1`), not the repo root.
The search fails with "no transcript titled '…'" even though the transcript exists.

Passing `-s/--source .worktrees/acn-1` should re-direct the search to the worktree slug, but
the NAME positional argument is still required and no auto-detection of the worktree name is
attempted. If the transcript `customTitle` doesn't match the name passed, the error is the same.

User impact: `ai session-adopt` is unusable for worktree-based sessions without manual
inspection of the CC project directory and exact title matching.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

Environment: `AI_HOST=acn-windows`, repo `acn-automation`, session `acn-1` that ran in
`.worktrees/acn-1`.

```
$ ai session-adopt acn-1
Error: no transcript titled 'acn-1' in C:\Users\sergei.wallace\.claude\projects\
C--Users-sergei-wallace-Projects-acn-automation — nothing to adopt
(check the title with `ai cc-migrate --dry-run`, or pass -s/--source)

$ ai session-adopt -s .worktrees/acn-1
Error: NAME is required (or use -a/--all)

$ ai session-adopt acn-1 -s .worktrees/acn-1
Error: no transcript titled 'acn-1' in C:\Users\sergei.wallace\.claude\projects\
C--Users-sergei-wallace-Projects-acn-automation--worktrees-acn-1 — nothing to adopt
(check the title with `ai cc-migrate --dry-run`, or pass -s/--source)
```

The third command shows the search is now in the right directory
(`--worktrees-acn-1` suffix present) but the transcript title doesn't match `acn-1`.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

Two compounding issues:

1. **Wrong default source path.** `adopt_session()` calls `cc_project_dir(source_root, home)`
   with `source_root = repo_root` by default (`session_adopt.py` ~line 721). When the session
   ran in a worktree, the transcript is stored under the worktree path's slug, not the repo
   root's slug. The `-s/--source` flag is the only override, but requires user knowledge of
   the worktree path.

2. **Transcript title mismatch.** Even with the correct source path, the CC transcript's
   `customTitle` may not equal the bare session name (`acn-1`). CC sets the title to the
   value passed via `--name`; if the session was started differently (e.g. without `--name`,
   or with a longer name), the JSONL `customTitle` will differ. The fix should use
   `ai cc-migrate --dry-run` output to discover the actual title, or fall back to `--all`.

**Causal chain:**
```
ai session-adopt acn-1
  → adopt_session(repo_root, "acn-1", source_root=repo_root)
  → cc_project_dir(repo_root, home)   # computes slug for repo root
  → find_transcript(source_dir, title="acn-1")
  → searches C--…-acn-automation/  (WRONG — session was in worktree)
  → returns None → AdoptionError
```

With `-s .worktrees/acn-1`, source_root is the worktree dir, slug is correct, but
`find_transcript` still fails if `customTitle != "acn-1"`.

**Fix direction:** `adopt_session` should:
1. Auto-detect worktree slug candidates when the repo-root search returns None (try known
   worktrees via `git worktree list`), OR
2. When `-s` is the worktree dir and title search fails, suggest `--dry-run` explicitly, OR
3. Support `-a/--all` to adopt the most-recent transcript from the source dir regardless of title.

Investigation deferred to /bug-fix post-compaction.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
|------|--------|-------|
| 2026-08-12 | — | Reproduction confirmed on acn-windows. Root cause identified (two issues: wrong default slug + title mismatch). No fix attempted yet — routed to post-compaction /bug-fix. |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

Three sequential reproduction attempts, captured 2026-08-12 on `acn-windows`:

```
$ ai session-adopt acn-1
Error: no transcript titled 'acn-1' in C:\Users\sergei.wallace\.claude\projects\
C--Users-sergei-wallace-Projects-acn-automation — nothing to adopt
(check the title with `ai cc-migrate --dry-run`, or pass -s/--source)

$ ai session-adopt -s .worktrees/acn-1
Error: NAME is required (or use -a/--all)

$ ai session-adopt acn-1 -s .worktrees/acn-1
Error: no transcript titled 'acn-1' in C:\Users\sergei.wallace\.claude\projects\
C--Users-sergei-wallace-Projects-acn-automation--worktrees-acn-1 — nothing to adopt
(check the title with `ai cc-migrate --dry-run`, or pass -s/--source)
```

Note: third attempt confirms the `-s` flag is being applied (slug now ends in
`--worktrees-acn-1`), so the source path redirection works; the failure is the title
mismatch, not the path lookup.

<!-- /doc:region name="appendix_evidence" -->
