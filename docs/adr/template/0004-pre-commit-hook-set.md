# 0004. The pre-commit hook set and its exclusions

Governs: `.pre-commit-config.yaml`.

## Exclusions

- `.beads/` is excluded globally. bd writes everything under it (Dolt config, JSONL
  exports, audit log); its output does not satisfy the file-formatting hooks, and the
  pre-push stash, fix and restore cycle conflicts on the store files as they change.
- `docs/asks/` is excluded from `end-of-file-fixer` and `trailing-whitespace`. A founding
  ask record stores a person's exact words and binds them with a `body_sha256` and a
  `content_sha256` over the whole file, so a fixer that changes one byte breaks the record.
  ai-harness excludes the same path.
- `uv.lock` is exempt from `check-added-large-files`. It is a large, deterministic,
  generated file that every repository tracks. Rejected: raising the size ceiling for every
  file type.

## Shared hooks resolved through PATH

`validate-claude-settings`, `test-gate`, `bd-precommit-validate`, `bd-blocked-check` and
`public-repo-name-check` live once in ai-harness. Its `install.sh` publishes an executable
wrapper to `~/.claude/bin/`, which is on PATH, and each consumer names the entry point with
`language: system`. There is no per-repository copy, so a change to a hook body reaches
every repository without a copier update. A workstation that never ran ai-harness's
installer fails loudly with `Executable <name> not found`; it never passes silently. See
ai-harness's `docs/designs/aih-393-shared-hook-distribution-model.md`.

## Individual hooks

- `validate-claude-settings` fails when a `.claude/settings.json` hook command points at a
  script that no longer exists, for example one a copier exclusion removed. It runs on every
  commit, so it also catches a script deletion that does not touch `settings.json`.
- `ruff-version-sync` fails when the ruff-pre-commit `rev` and the `pyproject.toml` ruff pin
  differ. They are two independent pins provisioning two binaries, and nothing else keeps
  them equal; five different resolved versions were measured across the fleet before this.
- `bd-precommit-validate` rejects a commit whose staged `.beads/issues.jsonl` has an entry <!-- gitignored-path-ok: names the file the hook checks, not evidence -->
  without `external_ref`, or a duplicate (id prefix, `external_ref`) pair. It replaced
  `roadmap-guard`, which grepped an archived roadmap and had checked nothing since.
- `bd-blocked-comment` requires every blocked Beads issue to carry the timestamped
  `[blocked ...]` comment that `bd-block` writes. Issues already blocked when the hook
  arrived are exempt through `.beads/blocked-legacy.tsv`, written once by
  `bd-blocked-check --current-repo --capture-baseline`; `--stage` stages that file when a
  run shrinks it.
- `public-repo-name-check` (public repositories only) refuses employer infrastructure names.
  The name list lives in ai-harness, so a public repository never carries it. `docs/asks/`
  is skipped because a founding ask is recorded byte for byte.
- `docs-index-check` was removed. The generated `docs/README.md` index had no reader, yet
  the gate blocked every docs commit until it was regenerated, and it could not be
  regenerated correctly from a linked worktree, so the only remedy there was its own
  forbidden bypass.
