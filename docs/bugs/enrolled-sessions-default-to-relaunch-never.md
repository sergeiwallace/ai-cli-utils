---
title: "Enrolled and recovered sessions default to relaunch never"
category: bug
tags: [bug, sessions, boot-relaunch, registry]
status: fixed
severity: P1
template_version: "bug-1.0.0"
---

# Enrolled and recovered sessions default to relaunch never

## Summary

<!-- doc:region name="summary" kind="replaceable" -->

On one host, six of eleven running canonical sessions had boot relaunch policy `never`, so a
reboot would have left them down. None of them had been set that way by hand. Enrolment and
generation recovery wrote `never` for every session they registered, nothing ever changed it
back, and the record could not say whether a `never` was a default or an owner's choice.
`ai sessions policy` also rewrote `changed_at`, so the list showed a policy change as a
run-state change.

The fix gives enrolled and recovered sessions the same default as a fresh launch (`auto`),
records who set each policy (`relaunch_set_by`), keeps the policy time apart from the run-state
time, and adds a one-time repair for the records older releases wrote.

The canonical request is the owner's original prompt. It names private sessions and hosts, so
it stays outside this public repository. In short, it asked: what `auto` and `never` mean, why
some sessions have each, whether a manual exit changes the value (it should not), and that exit
status be tracked separately from the policy.

<!-- /doc:region name="summary" -->

## What the values mean

<!-- doc:region name="meanings" kind="replaceable" -->

`relaunch` is read only by boot replay (`ai sessions relaunch --boot`, run by the boot unit when
`session_relaunch.on_boot` is on). `session_run_state.boot_veto` applies its checks in a fixed
order, and the policy check comes after the exit-cause check:

| Policy | Boot replay after an interrupted run | Explicit `ai sessions relaunch <slug>` |
|---|---|---|
| `auto` | replayed | starts it |
| `always` | replayed (same as `auto` today) | starts it |
| `never` | refused: `policy never forbids boot relaunch` | starts it |

Whatever the policy, a session whose last exit cause is `manual_exit` or `unknown` stays down at
boot. The exit cause lives in `run_state`, `supervisor_exit`/`exit` and `changed_at`, which the
supervisor writes on every exit. No exit, stop or wind-down path writes `relaunch`, before or
after this fix, so exiting a session by hand never changed its policy.

<!-- /doc:region name="meanings" -->

## Reproduction

<!-- doc:region name="reproduction" kind="replaceable" -->

Six affected records shared `launcher_pid: null` and the same `launched_at`, the signature of a
record written by bulk enrolment (`ai sessions enrol --all`, which also runs automatically from
script refresh, generation acquisition and boot replay) rather than by an `ai c` launch. Every
record with `auto` had a launcher pid.

`tests/test_relaunch_policy_attribution.py` reproduces it through the real entry points against
a temporary registry: `ai sessions enrol <name>`, `ai sessions enrol --all`,
`ai sessions _begin --launch-metadata` and `session_run_state.register`. Before the fix each
recorded `never` with no attribution, a recovered session was refused by `boot_veto` with
`policy never forbids boot relaunch`, and `ai sessions policy` moved `changed_at`.

<!-- /doc:region name="reproduction" -->

## Root Cause

<!-- doc:region name="root_cause" kind="replaceable" -->

Five writers set `relaunch`:

| Writer | Before | After |
|---|---|---|
| `session_run_state.register` (every `ai c` launch) | `setdefault("auto")`; a probe forced to `never` | `default_relaunch`: `auto`, or `never` for a probe |
| `session_enrol.enrol_session` (named enrol) | `relaunch="never"`, overwriting any value | `default_relaunch` |
| `session_enrol._enrol_live` (bulk enrol) | `setdefault("never")` | `default_relaunch` |
| `session_run_state.begin` with a recovery recipe | `setdefault("never")` | `default_relaunch` |
| `ai sessions policy` | `relaunch` and `changed_at` | `relaunch`, `relaunch_set_by="owner"`, `relaunch_changed_at` |

Enrolment and recovery chose `never` on purpose, so that registering a pre-feature session
would not opt it into boot replay without its owner's say
([legacy-supervisor-generation-recovery.md](legacy-supervisor-generation-recovery.md)). The
gap was that nothing later promoted those records. `register` used `setdefault`, so a later
`ai c` launch of an enrolled session kept its `never`. The value carried no attribution, so no
later step could tell the default apart from an owner's choice and safely change it.

A second, separate defect: `ai sessions policy` wrote `changed_at`, the field the run-state
transitions own and the list shows as `CHANGED`.

<!-- /doc:region name="root_cause" -->

## Decisions

<!-- doc:region name="decisions" kind="replaceable" -->

Taken by the launching session (Claude Opus 5.5) and built here as given.

| # | Decision | Status |
|---|---|---|
| D-1 | Enrolment and recovery give a canonical (non-probe) session `auto`; probes stay `never` | Built |
| D-2 | Add `relaunch_set_by: "owner" \| "default"`; `ai sessions policy` writes `owner`, every automatic write writes `default`; an owner value is never overwritten automatically | Built |
| D-3 | `ai sessions policy` stops writing `changed_at` and writes `relaunch_changed_at` | Built |
| D-4 | An idempotent repair turns a non-probe `never` with no `relaunch_set_by` into `auto`/`default` | Built as `ai sessions repair-policy [-d]` |
| D-5 | Boot-safety order unchanged; `manual_exit` and `unknown` still veto | Kept; pinned by tests |

**D-2 detail.** `session_run_state.default_relaunch` is the one helper every automatic writer
calls. It writes only when the record has no policy or its policy is itself a `default`. It
leaves an `owner` value alone, and also any unattributed value written before the field existed
(an old `always` could only have come from `ai sessions policy`). The repair is the one place
that judges an unattributed `never`. `relaunch_set_by` and `relaunch_changed_at` are in
`session_registry.DURABLE_FIELDS`, so an iTerm2 tracking write keeps them. One edge case: an
ordinary launch that takes a probe's title used to reset the policy to `auto`. It still does,
unless the owner set that probe's policy.

**D-4 choice: a one-shot subcommand, not a registry-load migration.** Reasons:

- A migration in `load_registry` changes policy on every read, with no preview, on whichever
  process happens to load the registry first. The subcommand prints each record it would change
  under `-d/--dry-run` and writes only when asked.
- A migration would need a schema bump to run once. An older `ai` on the same host (another
  virtualenv or a stale tool install) refuses an unknown schema, so a mixed-version host would
  lose its registry reads. The subcommand changes no schema.
- The data is ambiguous (below), and that judgment is better made in a visible step whose
  output an operator can read than inside a read path.

Its scope: local records whose `owning_host` is this host, not probes, `relaunch == "never"` and
no `relaunch_set_by` key. It writes nothing when nothing matches, so a second run leaves the
registry bytes unchanged.

**Low confidence, declared:** a `never` an owner set with `ai sessions policy` before this
change carries no attribution, so it cannot be told apart from the old default, and the repair
turns it into `auto`. On the reporting host, the six unattributed `never` records all came from
one enrolment batch, and the owner asked for them to be `auto`. That is an inference from the
batch signature, not proof, and another host may hold a deliberate one. The repair prints every record it changes, and `-d/--dry-run` shows the list
first, so an owner can set any of them back with `ai sessions policy <slug> never`.

<!-- /doc:region name="decisions" -->

## Fix

<!-- doc:region name="fix" kind="replaceable" -->

- `src/ai_cli/session_run_state.py`: `default_relaunch`, used by `register` and by `begin`'s
  recovery path.
- `src/ai_cli/session_enrol.py`: named and bulk enrolment call `default_relaunch`.
- `src/ai_cli/session_registry.py`: the two new fields are durable across tracking writes.
- `src/ai_cli/session_commands.py`: `policy` records the owner and its own time;
  `repair-policy` is new; `list -j` includes `relaunch_set_by`.

Repair on each host after the fixed package is installed:

```sh
ai sessions repair-policy --dry-run
ai sessions repair-policy
ai sessions list -a -j
```

Not in this change: pruning the exited rows that renamed sessions leave behind (their exit
cause already vetoes boot replay), rewriting the legacy `relaunch_argv` of renamed sessions
(boot replay uses `boot_recipe.argv`), and rolling the repair out to each host.

<!-- /doc:region name="fix" -->

## Verification

<!-- doc:region name="verification" kind="replaceable" -->

`tests/test_relaunch_policy_attribution.py` (18 tests) failed 16 on the unfixed code and passes
after the fix. The two owner-`never` guards passed before the fix only because the old default
was also `never`; removing the owner check from `default_relaunch` makes all four owner tests
fail. Tests in the enrol, bulk-enrol, recovery and command suites that pinned the old `never`
default and the old `changed_at` write were updated to the new contract.

<!-- /doc:region name="verification" -->

## Fix Log

<!-- doc:region name="fix_log" kind="append_only" -->

| Date | Result |
|---|---|
| 2026-11-13 | Reproduced through the enrol, bulk-enrol, recovery, launch and policy entry points; added attribution, the default `auto` and `ai sessions repair-policy`. |

<!-- /doc:region name="fix_log" -->
