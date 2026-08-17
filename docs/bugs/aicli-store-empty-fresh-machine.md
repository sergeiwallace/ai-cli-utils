---
title: "ai-cli-utils bd store empty on this machine — 0 issues despite 230 tracked in issues.jsonl"
category: bug
tags: [bug, beads, dolt, embedded-dolt, worktree]
status: applied
template_version: "bug-1.0.0"
---

# ai-cli-utils bd store empty on this machine — 0 issues despite 230 tracked in issues.jsonl

**Status:** applied

**Created:** 2026-08-17

<!-- doc:region name="summary" kind="replaceable" -->

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Summary

On this machine (`framework-fedora`), the `ai-cli-utils` bd store (embedded Dolt database
`AI_CLI`) was empty in both the main tree and a fresh worktree — `bd list` returned "No issues
found" and `bd config get issue_prefix` returned "(not set)" — even though the git-tracked
`.beads/issues.jsonl` held 230 real, well-formed issues matching commit history. The physical
local Dolt database was genuinely present but essentially a bare/uninitialized shell (~1.3M,
consistent with an empty repo, not 230 issues of real history), sharing the same underlying store
across the main tree and its worktrees. This is the same class of chronic
committed-export-vs-live-store divergence documented in
[`aicli145-store-divergence-reconciliation-proposal.md`](aicli145-store-divergence-reconciliation-proposal.md),
but total (0 live issues) rather than partial.

Related, not identical: this repo's `.beads/config.yaml` `sync.remote` was pinned to an SSH
GitHub URL (`git+ssh://git@github.com/...`), which fails outright on this machine — per this
repo's own fleet convention, `framework-fedora` git remotes are HTTPS + `gh auth`, not SSH. The
live `origin` Dolt remote also turned out to have **no** pushed Dolt history at all
(`refs/dolt/data` absent), so recovery could not adopt remote history and had to rebuild purely
from the tracked JSONL.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

1. On `framework-fedora`, in `~/projects/ai-cli-utils` (or any worktree under it), run `bd list`.
2. Observe `No issues found`, despite `.beads/issues.jsonl` containing 230 lines of real issues.
3. `bd config get issue_prefix` returns `(not set)`.
4. `bd bootstrap` fails: `Error 1007: can't create database AI_CLI; database exists`.
5. `bd dolt pull` fails: `Error 1105: no remote`.
6. `bd import` fails: `database not initialized: issue_prefix config is missing`.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

Two independent, compounding issues on this machine:

1. **Local Dolt store was a bare/never-hydrated shell.** `.beads/embeddeddolt/AI_CLI/.dolt`
   existed on disk (main tree) but held no real commit history and no `issue_prefix` config row —
   consistent with a partially-completed `bd init` that never finished importing/configuring. A
   git worktree under the same repo shares this same physical store rather than creating its own,
   so the worktree exhibited the identical empty state.
2. **The configured Dolt remote had no real history to adopt**, and the configured `sync.remote`
   URL used SSH, which cannot authenticate on this machine (no `ssh-askpass`, no loaded key; this
   machine's git access is HTTPS via `gh auth git-credential`). `bd bootstrap`'s "clone from
   remote" plan therefore could not be used for recovery even after fixing the local blocker.

The only genuinely intact source of truth was the git-tracked `.beads/issues.jsonl` (230 issues,
zero diff against `HEAD`), so recovery had to rebuild the local Dolt store from that file rather
than from any Dolt-native remote history.

A secondary finding, not itself a defect but worth recording: `bd init` (invoked here via
`bd init --from-jsonl --reinit-local --discard-remote`) unconditionally re-runs its full
first-time scaffolding — it rewrote `CLAUDE.md`/`AGENTS.md` with its own managed block, added
`.claude/settings.json` entries, installed `.beads/hooks/*`, `.codex/*`, and
`.agents/skills/beads/*`, and auto-committed all of it to the current branch. This repo's fleet
convention is store-only bd init (`bd_repo_setup.py --skip-agents --skip-hooks`); the extra
scaffolding and the unrequested commit had to be reverted (see Fix Log) after recovery completed.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
|------|--------|-------|
| 2026-08-17 | — | Backed up the existing (empty) `embeddeddolt` directory and the tracked `.beads/*.jsonl`/`config.yaml`/`metadata.json` files to a scratch location before any repair attempt. |
| 2026-08-17 | — | Moved the blocking empty `~/projects/ai-cli-utils/.beads/embeddeddolt` directory aside (renamed, not deleted) to unblock `bd bootstrap`'s clone target. |
| 2026-08-17 | — | Fixed `.beads/config.yaml`'s `sync.remote`/`sync: remote:` from `git+ssh://...` to `git+https://...` in the worktree (matching an existing, previously uncommitted fix already present in the main tree), consistent with this machine's HTTPS-only git remotes. |
| 2026-08-17 | — | `bd bootstrap` still failed live (`Error 1105: clone failed; remote at that url contains no Dolt data`), confirming the `origin` Dolt remote has no pushed history to adopt. |
| 2026-08-17 | — | `bd init --from-jsonl --reinit-local --discard-remote --non-interactive --destroy-token=DESTROY-AI-CLI` failed once on `invalid issue type: research` — this repo uses fleet-custom issue types (`research`, `follow_up`) that bd's default schema rejects. Registered them via `bd config set types.custom "research,follow_up"`, then re-ran the same `bd init` command successfully: imported all 230 issues, `issue_prefix` now correctly `AI-CLI`. |
| 2026-08-17 | `f9a8182` (reverted, not pushed) | `bd init`'s scaffolding side effects (managed `CLAUDE.md`/`AGENTS.md` block, `.claude/settings.json`, `.beads/hooks/*`, `.codex/*`, `.agents/skills/beads/*`) were auto-committed. Since this commit was local-only (unpushed, tip of the branch), it was undone with `git reset --soft HEAD~1`, the fleet-managed files (`CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`) were restored to their pre-init content, and the added hook/codex/agents-skill files were removed. Only the legitimate `.beads/config.yaml` HTTPS-remote fix was kept, left uncommitted for human review alongside the same pre-existing fix already sitting uncommitted in the main tree. |
| 2026-08-17 | — | Verified: `bd count --json` reports 230 (matches `issues.jsonl`); `bd list --all -n 0 --json` returns 230 records; spot-checked `AI-CLI-fcl` shows full real history (a prior, related divergence incident, closed 2026-08-06). No issue lost. |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

- `bd list` / `bd config get issue_prefix` / `bd bootstrap` / `bd dolt pull` / `bd import` error
  output captured during Reproduction, above.
- `.beads/issues.jsonl` line count (230) and zero `git diff --stat HEAD -- .beads/issues.jsonl`
  confirmed before any repair action.
- Post-fix: `bd count --json` → `{"count": 230, "schema_version": 1}`;
  `bd config get issue_prefix` → `AI-CLI`.

<!-- /doc:region name="appendix_evidence" -->
