---
title: "Session artifact naming validation"
category: bug
tags: [bug, session, naming, validation]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session artifact naming validation

## Symptoms

A launch silently changed punctuation, case and Unicode into another session
identity. A hidden repo-prefix override could bypass the owning repository's
registration. Migration targets were checked only against a general regex, and
launcher recipes could disagree with their tmux or worktree identity. A
hand-chosen human name could equal a reserved alias even though automatic picks
excluded it. Transcript-only surveys missed worktrees with no transcript.

## Root Cause Analysis

`session._slot_name` normalizes free-form input rather than validating it.
`main._do_session_launch` previously checked only the override's task-prefix
syntax. `session_migrate.migrate_session` held separate numeric/human regexes;
neither checked ownership. `main._engine_script_from_meta` trusted stored identity fields. `name_registry.check_hand_chosen` omitted the automatic picker's
reserved-alias exclusion. `session_audit.survey` enumerates transcripts, which
cannot establish that every filesystem slot has a registry row.

## Enforcement inventory

The references below describe the updated implementation. Creation and resume
compatibility are distinct: configured legacy names remain reachable.

| Artifact | Create / launch | Rename / migrate | Read-only audit |
| --- | --- | --- | --- |
| Session / custom title | `main.py:4132`, `:4809`; shared qualified-input check and owner override check at `:4790`; remote allocation at `:2117` | `session_migrate.py:1082` calls shared owner validator | `session_audit.py:658` checks transcript titles, including titles without slots |
| Session home | `session.py:1278` validates before repair, mkdir or Git writes; launch enrols at `main.py:4998` | `session_migrate.py:1082` checks before the move | `session_audit.py:577` directory census is independent of transcripts and rows |
| Home branch | `session.py:1345` derives `wt-<session>` after validation | `session_migrate.py:1124` derives checked rename targets | `session_audit.py:648` checks the attached branch |
| Agent container / branch | External creator remains responsible for `-agents/<leaf>` and public branch exceptions | Existing migration planner derives names from the checked session | `session_audit.py:593` excludes containers from homes and checks the owning row; external audit must inspect leaf branches |
| tmux | `main.py:4808`, `:5353` check requests and launcher identity | Derived from the checked target | `session_audit.py:625`, `:784` compare stored identities and join live names to slots and rows |
| Launcher script | `main.py:1510`, `:5353` validate at creation/rewrite; `:1654` checks the target filename | Regenerated from checked migration target | `session_audit.py:723`, `:748` compare literal assignments and metadata and report orphans |
| Task namespace | `main.py:5195` and the generated script derive it from `ai_name` | Existing rekey follows checked target | `session_audit.py:791` reports namespaces without a slot/row; linked native namespaces are excluded |
| Human-name registry | `name_registry.py:1070` retains distance/length policy and now checks reserved aliases | Existing reservation transaction, then shared target check | `session_audit.py:680`, `:707` check row aliases, worktree paths and per-machine edit-distance pairs |
| Adopted home / title | `session_adopt.py:998` checks previews; `:1156` registers actual homes before moving transcripts | Retitles pass the same destination check | The independent directory/registry census also covers adopted homes |
| Locks | Existing slot locking in `session.py:1287` remains unchanged | Existing migration lock handling remains unchanged | `session_audit.py:653`, `:743` report orphan locks without testing/removing ownership |

## Acceptance and preserved behavior

* WHEN a new canonical artifact is requested, the boundary SHALL refuse a wrong
  owning-repo alias, wrong configured host alias or malformed name before creating it.
* WHEN a launcher is regenerated, the writer SHALL refuse inconsistent title,
  tmux, home or target-filename identities while preserving the existing script.
* WHEN adoption creates a home, it SHALL register that home before moving its
  transcript; a preview SHALL validate the same destination without registering it.
* WHEN a naming census runs, it SHALL inspect directories independently of
  transcripts and registries and SHALL make no config, registry or artifact changes.
* WHILE legacy reachability is configured, existing legacy homes and the numeric
  legacy migration API SHALL remain reachable; strict census findings SHALL expose
  their naming violations. A strict creation/reachability cutover remains open.
* WHEN the pure script-rendering API is used, it SHALL remain usable without an
  on-disk checkout; durable writers SHALL perform ownership checks instead.

## Fix

`session_naming` now owns structural validation, qualified launch-input checks,
read-only owner lookup and artifact agreement. The existing project-prefix resolver now supports the inspection context, so
alias-resolution precedence is not copied into this validator. Existing name
lists, length policy and edit-distance implementation remain authoritative; no
second alias registry or distance algorithm was introduced. Refusals identify
the rule, offending value and remedy. Reusing these helpers was chosen over
adding more caller-specific regexes.

Canonical grammar: `{repo-alias}-{host-alias}-[{label}-]{index|human-name}`.
Input is lowercase ASCII letters/digits separated by single hyphens, up to 128
characters. Indices are positive decimal integers with no leading zeroes.
The repo alias is the current owner registration, using existing resolution
precedence; the host alias is the owner's registered machine alias. Human
suffixes are known registry names, with allocation enforcing the published
length policy (normally three to four letters), reserved aliases and distance
rules. Explicit registered label rows and names containing a `test` or `probe`
segment are noncanonical. `<session>-agents` is a container, never a home.
Artifact names derive from the one validated session identity. Validation sits
at launcher creation and rewrite boundaries. The pure `get_engine_script`
rendering API remains usable without an on-disk repository; rejecting its callers
was discarded because rendering alone creates no durable artifact. Remote
allocation validates the selected owning repository before reserving a name.
Stable-script rewrites also compare metadata with the target filename.

`ai audit-sessions -N/--naming` scans directories, local registry rows,
transcripts, launcher scripts/metadata, task namespaces and live tmux names.
It never fetches, seeds a local registry, acquires registry locks, repairs,
adopts, renames or deletes. An inspection context prevents transitive config
reads from registering a machine profile or migrating XDG directories.

## Compatibility and remaining boundaries

The existing `naming.legacy_convention` API permits legacy names and fallback
without a host alias. Numeric migration targets under `full`, and existing
legacy homes, retain that behavior; strict audits report them. Turning off that
compatibility requires an explicit cutover decision. Migration cleanup in another
repository validates against the existing owning home rather than the containing
repository; the shared grammar check does not change its dry-run/resume machinery. Existing per-machine task
prefixes also retain their historical syntax; new fleet aliases use the
existing configured alias limits rather than a second parser.

Numeric slots are protected by the canonical-worktree registry on launch;
human sessions also have session-name/spare/label token rows. The census reads
both existing registries. Numeric identities also work when an older checkout explicitly disables human
names. An exact canonical-worktree registration counts for a numeric slot, and its recorded engine/session identity must agree with that
slot. The session-start hook records a bare title rather than a tmux name; the
census understands that existing producer format. This avoids a schema migration or falsely reporting registered numeric
homes as missing. A missing row never establishes that removal is safe.

A script recipe without an owning-repository worktree path can be checked for
syntax and tmux agreement, but cannot prove its repository's current alias.
Cross-repository agent containers likewise cannot be attributed from their
containing repository's alias alone. The external worktree creator, guard and
session-start hooks must consume this shared grammar with the actual session
owner. This package does not change those external components or their removal
safety decisions. Detached branches and unreadable sources are reported for
inspection; no destructive action follows from a naming violation.

Existing label-only launches and legacy naming configuration are public APIs.
The census classifies their titles as noncanonical; changing their creation or
reachability policy requires a cutover decision. New canonical launch requests
containing a `probe` or `test` segment are refused before identity allocation.

## External integration follow-up

These components live outside this package; the package changes do not close
their enforcement gaps. Use `session_naming.validate_artifact_name` with the
actual owning repository and machine, rather than another shape regex.

| Integration | Current gap | Required change |
| --- | --- | --- |
| `agent_worktree_preflight.py:1031`, `:1207` | Composition/provenance do not check semantic owner aliases | Validate the owning session before composing the container or branch |
| `worktree_creation_guard.py:372` | A declared matching canonical leaf admits the creator without checking aliases | Check owner and host before accepting the declared leaf |
| `worktree_audit.py:973`, `:6440` | A broad numeric regex or human-name shape establishes canonical status | Add shared grammar observations without weakening deletion vetoes or changing cleanup proofs |
| `worktree_create_naming.py:489`, `:1169` | Title lookup and legal-ref checks do not prove owner aliases | Validate the resolved owning title before descriptive creation; preserve public `wt/agent/<leaf>` branches |
| `canonical_worktree_session_start.py:322` | Witnesses may agree on the same semantically wrong title | Validate the title's owning repo and host before admitting its task namespace |

## Verification

The new entry-point and census tests were run against the local base with the
implementation temporarily restored and then restored to the patch. The base
accepted malformed input and wrong qualified aliases, and launcher regeneration did
not raise for inconsistent identities. The reserved-alias regression failed
with `Failed: DID NOT RAISE HandChosenRefusedError`. A separate read-only
regression reproduced `AssertionError: read-only audit migrated legacy state`.
The seven-slot fixture uses public generic aliases, including one stale alias,
and has no transcript or registry row for any slot.

Focused checks include the new validation/census modules, session naming,
registry, migration, audit and launcher integration tests. Run them with isolated
test state directories; use `ruff format --check src/ tests/ scripts/` and
`ruff check src/ tests/ scripts/` for repository-wide static checks.

## Lessons Learned

Validate user intent before normalization. Inspect independent inventories:
absence from a transcript census does not establish absence of a worktree.
Read-only inspection must extend through transitive config and registry reads.

<!-- /doc:region name="summary" -->
