---
title: "Remote launch project and session identity disagree across stored sources"
category: bug
tags: [bug, remote, sessions, migration]
status: investigating
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Remote launch project and session identity disagree across stored sources

## Founding Ask Coverage

The implementation dispatch retains the original request. This public, non-authoritative
coverage index uses neutral examples:

- Refuse ambiguous project prefixes and a destination's conflicting project selection.
- After migration, keep saved launch directories and session arguments aligned with the new slot.
- Preserve remote origin in stopped-session names and launch reporting; document replay policy.

The active machine profiles and the reported connected-worktree log were unavailable to this
worker. Their ownership and exact causal connection remain unverified. No live remote launch
or operator-session repair was attempted.

**Status:** investigating — reproducible code defects fixed; reported live incident not verified

**Severity:** P1

**Created:** 2026-11-12

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Table of Contents](#table-of-contents)
- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

A remote launch can silently select the wrong project for a registered prefix. A migration
can leave its terminal restore record pointing at the old worktree while its boot recipe
points at the new one. Resolving a stopped worktree for a remote launch can return `c-`
instead of `c-r-`. The destination also describes a remote-origin launch as local.

## Environment

Linux, Python 3.14.7, base `147f7c97`. Reproductions use temporary TOML registries,
fixture Git repositories, the actual CLI parser and fake tmux/process boundaries.

## Reproduction Steps

1. Register `pa` for `project-a` in one registry and `project-b` in another. Read the
   launcher's alias map: the first registry silently wins. A root-keyed registration
   is ignored altogether. The remote allocator also accepts a mismatched project/prefix pair.
2. Save a launch record with the old worktree as `cwd` and a session argument followed
   by options. Migrate the slot: `cwd` stays old and the last option is overwritten.
3. Resolve an existing stopped worktree with remote origin and tmux disabled. The returned
   identity has no `r` segment, for both index and human-name lookup.

## Root Cause Analysis

Project aliases used `setdefault` across registries, silently selecting the first project.
The root-keyed registry participated in prefix resolution but not alias lookup. The remote
allocator trusted the launcher's project/prefix pair without comparing its own registry.

Migration updated the boot recipe's directory but omitted the restore record's `cwd`.
It assumed the session argument was last in `relaunch_argv`, despite options commonly
following it. Finish recognized old record names and recipes, but missed already-renamed
records with stale launch fields. The launcher itself reports the worktree returned by
`create_worktree`; no literal connected-worktree announcement was found. The incident's
exact printed line is therefore not attributed to this mechanism without further evidence.

Stopped-worktree lookup unconditionally constructed an engine-local name. Numeric lookup,
human-name reattach and held-label reattach all used it. Live tmux lookup deliberately keeps
the running identity. The destination's start banner and host report checked the outward
remote request rather than the separate remote-origin flag; those banners did not prove
that the flag was lost. Fresh remote launch and explicit stopped-session resume already
constructed remote names; migration and boot replay preserve stored identities.

## Prior Fix Attempts

| Attempt | Outcome |
|---|---|
| Prefer one project registry over another | Rejected: silently hides a conflicting project registration. |
| Replace only full session names in restore commands | Numeric positional arguments before options stayed stale; regression expanded and confirmed failing again. |
| Rename a live session to add remote origin | Rejected: attaching must preserve the existing running identity. |

## Fix

Alias lookup and session adoption now share checked registrations. Conflicting aliases are
reported with both project names and a remedy. The destination validates the requested pair
before allocation and again on direct remote-origin launch. Prefix matching ignores case
while directory spelling is preserved. A destination-only mismatch requires its SSH allocator
query: without a local copy of that profile it cannot be detected before any SSH connection.

Migration rewrites the stored directory and uses the existing CLI parser to identify the
session argument, preserving options and their values on either side. Finish detects stale
launch fields in already-renamed records. Git branch, task namespace, transcript title,
wrapper metadata and boot-recipe migration already existed and remain in place.

Stopped-worktree lookup now receives remote origin at all three callers. Start and host
reports honor the same flag. Existing live names stay unchanged; no kill or rename is added.
Boot recipes keep their exact session identity. The sessions-list reference describes local
and destination `auto` defaults, imported/probe `never` defaults, and why a transport record
cannot boot-launch the agent. Existing explicit policies survive migration and reattach.

These are contained, reversible changes inside launcher identity handling, with no new
dependency or public option. A profile synchronization system and live-session renaming were
the larger alternatives and are outside this fix.

## Verification

- Frozen regressions failed on the unchanged base for ambiguous aliases, stale migration
  directories and missing remote prefixes. Local-origin and existing-live-name controls pass.
- Project/config/remote/missing-directory neighboring files: 188 passed.
- Migration/finish-sweep/migration-resume/boot neighboring files: 217 passed before the added
  remote boot-recipe control.
- Naming/resume/remote/locality neighboring files: 277 passed, 1 skipped.
- Remote identity, boot, naming, resume and probe files after replay controls: 199 passed.
- The same frozen selection run against an exported `147f7c97` source tree: 22 failed,
  11 passed. These are expected behavioral failures, not collection or fixture failures.
- Final complete affected test files and neighbors (including all 33 regression cases):
  1133 passed, 1 skipped. The command covers project/config/remote selection, all
  `test_session_migrate*.py` modules, boot replay and units, naming, resume, probes,
  launch locality/integration, sessions listing, terminal adoption, enrolment and adoption.
- `uv run pytest -q --no-cov --tb=short`: 7168 passed, 21 skipped, 11 warnings in 252 seconds.
  This preceded the final legacy duplicate-error wording change, numeric-only finish repair
  and migration-origin control; the final 1133-test run covers all three. Warnings concern dependency deprecation
  and existing threaded fork tests.
- `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/` and
  `uv run pyright src/`: passed. CLI documentation generation produced no changes.
  The bug document passes document validation. The sessions-list reference passes TOML
  parsing; the Markdown document validator does not accept TOML files.

Final affected-file command:

```bash
uv run pytest -q tests/test_project.py tests/test_config.py \
  tests/test_remote_project_identity.py tests/test_remote_session_naming.py \
  tests/test_session_migrate*.py tests/test_session_boot_relaunch.py tests/test_session.py \
  tests/test_remote.py tests/test_resume_by_name.py tests/test_probe_launch.py \
  tests/test_session_name_launch.py tests/test_session_launch_locality.py \
  tests/test_session_launch_integration.py tests/test_session_boot_unit.py \
  tests/test_sessions_list_table.py tests/test_iterm2_sessions_adopt.py \
  tests/test_session_enrol.py tests/test_session_adopt.py --no-cov --tb=short
```

Live machine-profile ownership, the exact reported connected-worktree announcement, and
macOS/Windows runtime behavior remain unverified. No operator registry or running session
was changed. A wrongly named live session is found by existing lookup and attached as-is;
an explicit migration remains the existing route for a stopped session's title change.

## Lessons Learned

Treat the worktree as evidence of a session title, not of launch origin. Validate project
identity at both ends of a remote launch. Keep the restore directory and command in the same
migration inventory as the boot recipe, and parse positional arguments instead of assuming
they are the last token. Reporting labels must use the same origin signal as naming.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->
<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
