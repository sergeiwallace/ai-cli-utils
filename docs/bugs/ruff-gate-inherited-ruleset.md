---
title: "[BUG-006] The hard gate inherited its ruleset from ruff's default, so a tool upgrade silently redefined what passing means"
category: bugs
tags: [ruff, lint, gate, tooling, pin, pre-commit, config, regression-guard]
status: fix-deployed
severity: P1
related_docs:
  - CONTRIBUTING.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-006] The hard gate inherited its ruleset from ruff's default, so a tool upgrade silently redefined what passing means

**Status:** fix-deployed

**Severity:** P1 — silent, and it invalidated the gate rather than any single commit. Two
different verdicts were reachable for the same tree with the same nominal command, and the
gate is currently failing outright on `main` with 1075 findings, which is being routed
around with `SKIP=ruff-check`.

**Created:** 2026-07-28

**Task:** AI-CLI-141

## Symptoms

Three symptoms, one cause.

1. `ruff check src/ tests/` reported `All checks passed!` while
   `pre-commit run ruff-check --all-files` reported over a thousand errors on the same tree.
2. After the environment was brought up to the pin, the *documented hard gate itself* began
   failing on `main`: exit 1, 1075 findings. Every "ruff clean" claim recorded earlier that
   day was therefore void.
3. Multiple contributors committed with `SKIP=ruff-check`, because the hook runs `--fix` and
   rewrites unrelated files. A bypass had become routine within a day of the pin landing.

## Environment

- Repo: ai-cli-utils, `origin/main` at `3d44e22`
- Linux, Python 3.13.14, uv 0.11.24, ruff pinned `0.16.0`
- Every measurement below names the exact binary used, because two readings that drove
  earlier conclusions came from probes that could not distinguish the hypothesis from its
  negation (see Prior Fix Attempts #4).

## Reproduction Steps

Against `origin/main` (which does not carry this fix), using the project venv's own ruff:

1. `.venv/bin/python -m ruff --version` -> `ruff 0.16.0` (establishes which binary)
2. `.venv/bin/python -m ruff check src/ tests/` -> exit **1**, `Found 1075 errors.`
3. `grep 'select' pyproject.toml` -> no match; `[tool.ruff.lint]` declares only `ignore`
4. Same binary, same tree, with `select = ["E4","E7","E9","F"]` added -> exit **0**,
   `All checks passed!`

```yaml
reproduction:
  revision: origin/main 3d44e22
  environment: Linux, Python 3.13.14, uv 0.11.24, ruff 0.16.0 (.venv/bin/python -m ruff)
  command_or_steps: ruff check src/ tests/ with an inherited vs. a declared rule set
  expected: the gate enforces the rule set this repo adopted, with a verdict stable across
    tool upgrades
  observed: 1075 findings, none in a rule family the repo had ever selected
  exit_status: 1 (inherited default) vs 0 (declared select)
  reproducibility: deterministic
  baseline_failures: none — 1965 passed / 7 skipped before any edit
  evidence: see Root Cause Analysis and the Appendix
```

## Root Cause Analysis

**The enforced rule set was inherited, not declared.** `[tool.ruff.lint]` set only `ignore`.
With no `select`, the gate enforces whatever ruff's *default* happens to be — and a tool
upgrade is free to redefine that. 0.16.0 did:

| ruff | stock default rules enabled (`--isolated`, empty file) |
|---|---|
| 0.15.11 | 61 |
| 0.16.0 | 415 |

The decisive measurement, and the one separating "pre-existing debt this repo signed up for"
from "rules that arrived on their own": of the 1075 findings under 0.16.0's default, **zero**
fall in the `E4`/`E7`/`E9`/`F` families this repo had ever selected. Every one comes from a
family that appeared only because the default moved — `SIM` (369), `PLW` (151), `BLE` (147),
`UP` (107), `S` (83), `I` (77), plus `ASYNC B C DTZ FLY FURB ISC PIE PLR RUF TRY`.

The config was self-evidently written against the old default: all three `ignore` entries
(`E701`, `E702`, `E741`) are `E7` codes, which only makes sense under
`select = ["E4","E7","E9","F"]`.

Proof that this reconstructs the historical rule set exactly — identical sets, not merely
comparable counts:

```text
0.15.11 stock defaults          -> 59 rule codes
0.16.0 with select=E4,E7,E9,F   -> 59 rule codes
diff                            -> IDENTICAL
```

**A contributing skew: the pin bump did not reach the environment.** `df345b8` changed
`pyproject.toml`, `.pre-commit-config.yaml`, and `uv.lock` together at 2026-07-27 22:01. A
pin is a declaration; it does not touch an already-provisioned venv, and nothing in the repo
re-synced or checked one. For roughly a day the venv served ruff 0.15.11 to a repo that
declared 0.16.0 in three places.

That skew was real, and the *guard* against it is part of this fix, but it is no longer live:
the environment was brought up to the pin during this investigation (`uv sync --dev`), which
is what made symptom 2 visible. Evidence captured before the sync:

```text
$ uv sync --dry-run
Found up-to-date lockfile at: uv.lock
 - ruff==0.15.11        # uv naming what was actually installed
 + ruff==0.16.0
```

The lockfile was already correct; the environment had simply never been synced against it.

This is not a `uv` defect. `uv run <cmd>` syncs before running, so `uv run ruff check` was
always right. CONTRIBUTING.md documented the gate as bare `ruff check src/ tests/`, which has
no sync step and resolves through `PATH` — and that is the command people actually run.

**Causal chain:**

```text
[tool.ruff.lint] declared `ignore` but never `select`
  -> the gate's rule set was whichever default the resolved ruff binary supplied
  -> ruff 0.16.0 changed that default from 61 rules to 415
  -> AND the pin bump never re-synced .venv, so two binaries were reachable
  -> stale binary + old default = "All checks passed!"; pinned binary + new default = 1075
  -> once the venv was synced, the documented gate began failing outright on main
  -> contributors bypassed with SKIP=ruff-check, and the gate stopped meaning anything
```

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-07-27 | `df345b8` added the `ruff-version-sync` hook, comparing the pre-commit rev to the pyproject pin | Correct but insufficient. It compares two *declarations*; both were already `0.16.0`. The installed binary is a third, independent value it never read, so it passed throughout the defect. |
| 2 | 2026-07-27 | Bulk `ruff check --fix`, run exploratorily | Reverted. Rewrote 60 files / 465 mechanical edits across production modules in one shot — unreviewable, and it would have "fixed" findings from rule families this repo never adopted. Ruled out on AI-CLI-141. |
| 3 | 2026-07-28 | First cut of the new guard resolved the running interpreter's `sysconfig` scripts dir *first* | Rejected by its own test. That measures whichever environment is running rather than the repo being checked — the same conflation as the original defect. Reordered so the repo's `.venv` is authoritative. |
| 4 | 2026-07-28 | `stat` on `.venv/bin/ruff` used to date the install, and thereby to conclude the skew had never existed | Rejected: **`.venv/bin/ruff` is a hardlink into the uv cache** (`links=5`), so its mtime is the *cache download* time, not the install time. It reads identically whether the venv was provisioned an hour ago or a minute ago, so it cannot discriminate. The venv-local `ruff-0.16.0.dist-info/RECORD` (`links=1`) is the correct artifact. Recorded because this is a plausible-looking liar that will be reached for again. |

## Fix

1. **`pyproject.toml`** — declare `select = ["E4", "E7", "E9", "F"]` under `[tool.ruff.lint]`.
   Pins the enforced rule set to the repo's own config instead of inheriting a tool default
   that moves underneath it. Measured identical to the rule set this repo was actually linted
   against, so it changes no verdict on existing code.
2. **`scripts/check_ruff_version_sync.py`** — added `check_installed_version()`, wired into
   `main()`. Compares the pin against the ruff actually installed in the project environment,
   failing with a `uv sync --dev` instruction on mismatch or absence. Resolves the venv
   explicitly (the repo's `.venv`, then the main worktree's, then the running interpreter)
   rather than through `PATH`: the hook runs under whatever `python3` the committing shell
   provides, which on at least one supported workstation has no ruff at all.
3. **`tests/test_ruff_pin_integrity.py`** — asserts the pin/installed match and the declared
   rule set **at test time**, so neither can be satisfied by an earlier reading that has since
   gone stale. This is the property that actually failed: the environment changed between a
   measurement and the conclusion drawn from it.
4. **`CONTRIBUTING.md`** — the documented gate is now `uv run ruff check …`, with the reason
   stated. `uv run` syncs first, so the gate and the hook cannot disagree.

### The gate decision, and why this is not a weakening

Once the venv matched the pin, the gate had three possible resolutions:

| Option | Effect | Assessment |
|---|---|---|
| Leave the gate on ruff 0.16.0's inherited default | Honest about the 1075, but red on every commit, and already bypassed with `SKIP=ruff-check` | Rejected. An unenforced gate everyone routes around is strictly worse than an accurate narrower one: the bypass generalises to the *other* hooks in the same config, and it teaches contributors that red means "skip". |
| Scope the gate to changed files | Matches what pre-commit actually enforces (staged files) | Rejected as the primary fix. It leaves the underlying question — *which rules does this repo enforce?* — undefined, so the answer keeps moving with the installed version, and the verdict varies by what you happened to touch. |
| Declare the rule set the repo actually adopted | Gate enforces exactly what it enforced before, stated rather than inherited | **Adopted.** |

The third is not a baseline, a suppression, or an ignore list. Nothing previously caught is
now allowed: the declared set is **identical** to the set enforced before the upgrade (59
codes, empty diff), and 0 of the 1075 findings lie inside it. The gate can still fail — proven
by construction in Verification — and it now fails for a reason the repo chose.

**Deliberately NOT done:** the rule families 0.16.0 added to its default were neither adopted
nor auto-fixed. Adopting `SIM`/`BLE`/`PLW`/`UP`/`S`/`I` is a real decision about this
codebase's style, tracked on AI-CLI-141. 417 of the 1075 are auto-fixable, and applying them
is explicitly not this change's call.

**Open for the maintainer:** whether to adopt any of those families, in what order, and
whether to raise the gate to `--all-files` afterwards. That decision needs a human; this
change only stops the tool from making it silently.

## Verification

Every command names the binary used. `env -u AI_HOST` is required for the suite.

- [x] Regression tests confirmed RED on the unfixed revision (7 of 10 failing, for the
      expected assertions) **before** any production edit
- [x] Ring 2: deleting the `select` line returns 4 tests to RED; restoring it returns them to
      GREEN
- [x] Guard proven able to fail against a **real** mismatched venv, not an injected string:
      two throwaway venvs, identical repo files — ruff 0.16.0 -> exit 0; ruff 0.15.11 ->
      exit 1 with the skew message; no ruff -> exit 1 "not installed"
- [x] Negative control: disabling the guard's main-worktree fallback returns the stale-venv
      worktree test to RED
- [x] Hook runs clean from a linked worktree with no local `.venv`, and still goes red there
      when the main tree's venv is genuinely stale (verified by temporarily downgrading it)
- [x] `ruff check src/ tests/` and `ruff format --check src/ tests/` pass under the pinned
      0.16.0 binary (`.venv/bin/python -m ruff`, version printed from that same binary)
- [x] The same binary against `origin/main`'s tree still reports 1075 — the fix is what
      changes the verdict, not the environment
- [x] Full suite green against the post-merge baseline

## Lessons Learned

- **Never inherit a tool's default rule set into a hard gate.** An inherited default makes the
  gate's meaning a property of the installed version, so an upgrade silently redefines
  "passing" — in either direction.
- **Two agreeing declarations are not a verified environment.** The existing hook compared a
  pin to a rev; both were already correct. The value that decided the verdict was the
  installed binary, which nothing read.
- **Assert the invariant at test time, not from a remembered reading.** The concrete failure
  was not only "wrong version installed" but "measured, then it changed underneath" —
  including twice while diagnosing this bug. Any human or agent reading is stale the moment
  it is written down; only a check that runs can be trusted.
- **A gate that cannot fail is worse than no gate**, because it emits false assurance.
- **Watch for probes that read the same whether or not the hypothesis holds.** `stat` on a
  hardlinked binary cannot date an install; `cmd | head` returns head's exit status. Both were
  reached for during this investigation.
- **A guard that degrades to inconclusive exactly where the work happens will be bypassed.**
  The first cut reported "not installed" for every commit made from a worktree, since linked
  worktrees carry no `.venv`.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| # | Date | Commit | What changed | Result |
|---|------|--------|--------------|--------|
| 1 | 2026-07-28 | this commit | Declared `[tool.ruff.lint] select`; added `check_installed_version()` to the `ruff-version-sync` hook plus test-time assertions; corrected CONTRIBUTING.md's gate to `uv run` | Gate enforces a declared rule set under the pinned binary; version skew now fails the commit |

Diagnosed and fixed in-session, no Codex delegation: the defect was a configuration and
provisioning skew requiring live measurement of several binaries in this specific
environment, cheaper to measure directly than to brief.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

Rule-set measurements (`--show-settings`, `linter.rules.enabled`, counted by rule code):

```text
0.15.11  stock defaults (--isolated)            61 entries / 59 codes
0.16.0   stock defaults (--isolated)           415 entries / 413 codes
0.16.0   with select=["E4","E7","E9","F"]       61 entries / 59 codes
diff(0.15.11 stock, 0.16.0 select)             IDENTICAL
```

Preview mode ruled out: `linter.preview = disabled` in both, and `0.16.0 --no-preview` still
enables 415 while `0.15.11 --preview` enables 409 — so the expansion is the default select
itself, not preview gating.

Findings by rule family under 0.16.0's inherited default (1075 total, `src/` + `tests/`):

```text
SIM117 369   PLW1510 151   BLE001 147   UP017 107   S110 83   I001 77
UP045 44   RUF059 16   FURB162 11   RUF100 8   C408 8   B017 6   ...
findings inside E4/E7/E9/F (the families this repo selected):  0
```

Guard failure proof — identical repo files, two real venvs, the guard's own exit status:

```text
RUN 1  venv ruff 0.16.0 (== pin)    -> GUARD_EXIT= 0
RUN 2  venv ruff 0.15.11 (!= pin)   -> GUARD_EXIT= 1  "pins ruff 0.16.0 but the project
                                        environment has ruff 0.15.11 installed"
RUN 3  venv with no ruff            -> GUARD_EXIT= 1  "ruff is not installed"
```

Why `stat .venv/bin/ruff` cannot date an install (Prior Fix Attempts #4):

```text
.venv/bin/ruff                            links=5   mtime = uv cache download time
.venv/.../ruff-0.16.0.dist-info/RECORD    links=1   mtime = actual install time
```

<!-- /doc:region name="appendix_evidence" -->
