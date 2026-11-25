---
title: "A positional reattach refuses a session this machine recorded under a former host id"
category: bug
tags: [session, naming, registry, host-alias]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A positional reattach refuses a session this machine recorded under a former host id

## Founding Ask Coverage

N/A: this public record covers one defect class (AI-CLI-zqda6). The issue tracks the
request it came from. This coverage index is non-authoritative.

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-22

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

On a machine whose host id changed after it wrote name-registry rows, `ai c cleo -p
myproject` refuses the session the name refers to:

```text
cleo is held by myproject-boxo-cleo@host-a-old.
  A first name is chosen with --name, never positionally: to launch a NEW session
  under it, run with `--name cleo`.
```

The row is this machine's own. It was written under the machine's former host id
(`host-a-old`) and former host alias (`boxo`); the machine now reports `host-a`, whose
alias is `box`. A session started outside the launcher has no tmux session, so the
live-session check cannot rescue the reattach either.

## Environment

Any machine whose host id is renamed (a marker file rewritten by an installer, a new
hostname, an `AI_HOST` change) while it holds human-named sessions, together with a
host alias change. Three spellings of one machine can be live at once: the one a row
was written under, the one the installer now writes, and a newer one on the way.

## Reproduction Steps

1. Reserve `cleo` with a row `{machine: host-a-old, host_alias: boxo, title:
   myproject-boxo-cleo}`.
2. Make this machine's host id `host-a` (alias `box`).
3. Run `ai c cleo`.

Frozen as tests in `tests/test_session_title.py` and `tests/test_session_name_launch.py`
(names containing `former_host_id` / `former_id`); at the base commit three of them
fail with the refusal above.

## Root Cause Analysis

Defect class: machine identity compared as raw strings, where one machine has several
spellings.

Every site that asks "is this row (or record, or receipt) this machine's?" compared the
recorded value with this host's id using `==` or `!=`. A row written under a former
spelling never matched, so the reattach fell through to the holder refusal. The title
had the same split one level down: the reattach composed `{repo-alias}-{current
alias}-{name}` and required the row's title to start with that stem, so a title under
the machine's former alias never matched even once the machine did.

The alias registry (`machine-host-aliases.toml`) had no way to say two host ids are one
machine, so there was nothing to fold against.

### The search

Haystack: every tracked Python file under `src/` (107 files at the base commit), read
with `git grep` so the whole haystack is listed, not a truncated page.

Queries:

```text
git grep -nE '("machine"|"owning_host"|"host_alias"|"host")\)? ?\]? ?(==|!=)|(==|!=) ?\(?(host|machine|local_host|owner|slot\.machine|host_id\(\))\b|\.machine ?(==|!=)|\bin \((None, )?machine' -- 'src/**/*.py'
git grep -nEi "machine ?= ?\?|owning_host ?= ?\?|where[^\"]*machine" -- 'src/**'
git grep -nE 'owning_host|\.machine\b' -- 'src/ai_cli/data/**'
git grep -nE 'partition\("@"\)|split\("@"|\.holder\(' -- 'src/**/*.py'
```

The second and third found no SQL filter and no shell comparison on a recorded host.
The fourth found only `holder()` used to word a refusal, never parsed and compared.

### Instances routed through the identity

| File | Site |
|------|------|
| `main.py` | `_title_held_here`, `_held_title`, `_reattach_title` (row match and the closest-titles pool), `_reserve_label_title`, adoption release, migrate pending record, over-limit migrate inventory |
| `session_naming.py` | `validate_artifact_name` name-distance check |
| `name_registry.py` | `Snapshot.rows_of`, host-scoped verdicts (two), own-spare check, spool publish, reconcile candidates and release (three), spare release, `release_name`, migration complete, failed-migration release (two), mark incomplete |
| `name_commands.py` | `running()` in the names listing |
| `migrate_names.py` | intent records settled on this machine |
| `session_audit.py` | local rows, container owner row, unregistered-container check, name-distance pairs |
| `session_locate.py` | local reading versus remote liveness |
| `session_enrol.py` | owning-host checks (two) |
| `session_run_state.py` | begin, boot veto, recovery ownership, exit transition |
| `session_reconcile.py` | liveness proof, boot settle candidates |
| `session_exit.py` | cleanup, current-session resolve (two), owning receipt, remote receipt |
| `session_commands.py` | owned record, relaunch default repair, boot check, earlier boot run, boot decision |
| `session_list_view.py` | host sort and host grouping |

### Compares deliberately left raw

| File | Site | Why |
|------|------|-----|
| `name_registry.py` | `reassign` build | Compare-and-swap: the row's machine must be the exact value read before the write, or a concurrent writer changed it. |
| `session_state_publish.py` | published doc `host` | Integrity of a document stored under its own host key, not a question about this machine. |
| `session_audit.py` | row `host_alias` against the registry's alias for the row's machine | An audit finding that reports alias drift; folding would hide what it exists to report. |
| `session_naming.py` | inherited `AI_HOST` against the refreshed id | Only words an error message. |
| `main.py`, `tunnel.py` | VPN host and SSH alias compares | SSH aliases, not host ids. |
| `session_script.py` | `AI_HOST` change notice in the launch script | Detects that the environment variable changed; it decides nothing about ownership. |

## Prior Fix Attempts

None.

## Fix

- `machine-host-aliases.toml` gains an optional `[former_host_ids]` table: `host-a =
  ["host-a-old"]`. Every id in one entry is the same machine, whichever is current, and
  entries that share an id join, across every alias file the launcher reads.
- `session_naming.machine_identity(host=None)` returns a `MachineIdentity` holding every
  spelling of the machine; `matches(value)` and `holds(row)` are the only comparison
  every site above uses. A malformed table folds nothing there, and
  `resolve_host_alias` (run by every launch) refuses it, naming the file.
- `session_naming.row_stem_is_this_repo(row, naming_prefix)` accepts a row's title under
  this repository's stem or under the row's own recorded `repo_alias` and `host_alias`,
  so a title written under a former alias reattaches by its own title.

A session the launcher did not start (no tmux session) reattaches by relaunching its
recorded title, which resumes that title's worktree and newest conversation the same way
as any stopped title; the launch-level tests cover both the positional and `-r` paths.

To make an existing machine whose id changed reattach its old rows, add its former ids
to `[former_host_ids]` in the alias registry it reads.

## Verification

- RED at the base commit: three reproductions fail with the refusal; the guard that a
  genuinely different machine (and a former id of another machine) still refuses
  passes, as does the `-r` resume path.
- After the fix: the new tests pass, and the neighbouring suites listed in the commit
  pass.

## Lessons Learned

A host id is a name for a machine, and names change. Any question "is this mine?" needs
one function that knows every name the machine has had; a raw `==` on a recorded host
works only until the first rename. A first search capped at 80 lines of output missed
about half of the sites; read the whole haystack.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

2026-11-22: Opus engineer leaf froze RED tests for a row under a former host id, added
the `[former_host_ids]` table and one machine-identity function, and routed every
recorded-host comparison in `src/` through it.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
