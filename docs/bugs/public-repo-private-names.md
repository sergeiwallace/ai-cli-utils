---
title: "[BUG-005] A private project name and two private repository names leak into this public package"
category: bugs
tags: [hygiene, public-package, naming, privacy, regression-guard]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/session-launch-non-mac-host.md
  - docs/plans/pre-release-v0.2.0-plan.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-005] A private project name and two private repository names leak into this public package

**Status:** fix-deployed

**Severity:** P1 — no functional impact, but this package publishes to PyPI and its source is
public. `CLAUDE.md`'s Public Open-Source Package Standards forbid private project names and
personal identifiers in code, docs, comments, tests, and commit messages. Every violation was
already published.

**Created:** 2026-07-28

**Task:** AI-CLI-143

## Symptoms

Two distinct classes, both visible to anyone reading the published repository.

1. **A private project name used as a stand-in project name.** In the shipped package and its
   suite: a `sync.py` docstring example, `project_name=` arguments threaded into generated
   launch scripts in four test files, a session-name environment variable, a project registry
   fixture, a comment citing commit hashes from an unrelated private repository, a hardcoded
   secrets-manager project name in `setup.sh`, a session-name mapping table in
   `scripts/cleanup_cc_sessions.py`, and three "per <name>'s explicit ask" comments in the
   statusline script. Roughly 20 further files under `docs/`.

2. **Two real private repository names, with absolute filesystem paths, in a bug document.**
   `docs/bugs/session-launch-non-mac-host.md` (BUG-003) named the two repositories the failure
   was observed in, 15 times, including inside its immutable evidence appendix.

The confounding factor, and the reason a naive check here is worthless: **the same token is
also the first half of the project's real GitHub account name**, which appears correctly in
the README badge URLs, the CI and coverage badge URLs, `pyproject.toml`'s project URLs, the
funding and issue-template configuration, the MIT copyright line, and the security contact
address. A substring search flags all of those, so "no hits" is unreachable and "hits" is
uninformative.

## Environment

- `ai-cli-utils` at `eed4056` (`main`), 2026-07-28
- Linux; violations are platform-independent (they are string literals and prose)
- Suite baseline before any change: 1965 passed, 7 skipped, 0 failed

## Reproduction Steps

1. Check out the repository at `eed4056`.
2. Run a search that discriminates the private project name from the real account name — the
   token *not* immediately followed by the surname (see Verification for the exact pattern):

   ```bash
   git grep -n -I -P -i '<private-project-name>' -- src/ tests/
   ```

3. Observe 12 matches across 6 files: 3 in `src/ai_cli/data/statusline-command.sh`, 1 in
   `src/ai_cli/sync.py`, 2 in `tests/test_config_watch_hash.py`, 1 in
   `tests/test_growthbook_launch_toggle.py`, 2 in `tests/test_runaway_loop_guards.py`, and 3
   in `tests/test_session.py`.
4. Confirm the check is not merely flagging the legitimate account name: the same pattern over
   `README.md` returns nothing, while a plain substring search over `README.md` returns 4 hits.

## Root Cause Analysis

The causal chain is about enforcement, not about any one author's oversight.

```text
a one-off scrub cleaned src/ + tests/ (2026-04-06)
  → no standing check was added in the same change
  → the rule survived only as prose in CLAUDE.md
  → four subsequent commits reintroduced the name, each passing review and the full gate
  → the violations are indistinguishable from correct code to every automated check that runs
```

**Evidence.** The 2026-04-06 privacy sweep (`15899d6`, "full privacy sweep") left `src/` and
`tests/` genuinely clean — the discriminating pattern returns nothing at that revision, while
the same pattern at that revision still returns dozens of hits under `docs/`, which proves the
probe runs there and is not vacuously empty. The violations then re-entered by commit:

| Date | Commit | What it added |
|------|--------|---------------|
| 2026-07-20 | `1ffd849` | `project_name=` and a private-repo commit-hash comment in `tests/test_config_watch_hash.py` |
| 2026-07-24 | `96cb394` | `project_name=` in `tests/test_growthbook_launch_toggle.py` |
| 2026-07-27 | `6cbaaef` | `project_name=` twice in `tests/test_runaway_loop_guards.py` |
| 2026-07-28 | `fb9d9fb`, `245206d` | session name and registry fixture in `tests/test_session.py`; the private repository names in BUG-003 |

Each of those passed `ruff check`, `ruff format --check`, and the full suite, because none of
those tools knows anything about naming policy. `.pre-commit-config.yaml` runs `check-yaml`,
`check-json`, `detect-private-key`, `markdownlint`, `ruff`, `shellcheck`, and four local
guards — none of which is a naming check either.

**Why the copy-paste kept happening.** The value flows through as a plausible-looking
argument. `get_engine_script(..., project_name="<name>")` needs *some* project name, and the
nearest example to copy was a previous test that already used the private one. Nothing about
the call site signals that this particular string is policy-relevant.

**Hypothesis ledger.**

| Hypothesis | Predicted observation | Check performed | Result |
|---|---|---|---|
| H1 — a one-off scrub with no standing enforcement; violations re-entered afterwards | `src/`+`tests/` clean at the sweep commit, dirty at `HEAD` | Ran the discriminating pattern against `15899d6` and `HEAD`, plus a control over `docs/` at `15899d6` to prove the probe was live at that revision | **Confirmed** — clean then, 12 hits now |
| H2 — the April scrub never actually finished `src/`+`tests/`; these are original residue | Hits present at `15899d6` too | Same probe at `15899d6` | **Rejected** — zero hits there |
| H3 — some occurrences are functionally required (a real registry key the code depends on) | Renaming would break a behavioural assertion | Read each test in full; renamed and re-ran the focused suites | **Rejected** — every occurrence is an arbitrary placeholder; all 128 tests in the five touched files pass after renaming |

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-04-06 (`15899d6`) | Manual full-repo privacy sweep | Correct as far as it went, and it genuinely cleaned `src/`+`tests/`. But it added no standing check, so the rule decayed back to prose and the name returned within four months. |
| 2 | 2026-07-28 (this fix, first attempt at the `docs/` half) | A substitution script without the account-name lookahead | **Rejected before commit.** It rewrote four legitimate `github.com/<account>/ai-harness` URLs in `docs/README.md` into a broken account. Reverted; the script was rewritten so every pattern carries the same lookahead the guard uses, plus a per-file assertion that legitimate account usage is byte-identical afterwards. This is the exact over-fix the discriminating pattern exists to prevent, and it happened anyway on the first pass. |

## Fix

- **`src/ai_cli/sync.py`** — the `_wt_name_from_bare_name` docstring example now reads
  `"myproject--worktrees-session-5" → "session-5"`.
- **`src/ai_cli/data/statusline-command.sh`** — three attribution comments rewritten to name
  the request, not the person ("by explicit request (2026-07-19)").
- **`tests/`** — `project_name="myproject"` in `test_config_watch_hash.py`,
  `test_growthbook_launch_toggle.py`, and `test_runaway_loop_guards.py` (both the argument and
  the session-metadata fixture); `AI_TMUX_SESSION` and the registry fixture in
  `test_session.py`. Each file was read in full first: these values thread through into
  generated shell scripts, so a half-applied rename would drift assertions rather than fail
  loudly. The private-repository commit-hash citation in `test_config_watch_hash.py`'s module
  docstring was removed — the paragraph explains the incident without it.
- **`setup.sh`** — the hardcoded secrets-manager project name became `<your-project>`. This
  one was also a functional defect: the instructions told every user to write to a project
  they do not own.
- **`scripts/cleanup_cc_sessions.py`** — the private name in the session-mapping table and in
  two path examples.
- **`docs/bugs/session-launch-non-mac-host.md`** — every repository and session name replaced
  with placeholders (`myapp`/`app-1`, `myworkspace`, `mylib`, `mysite`, `myservice`). The
  immutable evidence appendix was included: substituting a name is not revising evidence, and
  a fix-log entry records exactly what was substituted and that no finding, command, output,
  or count changed.
- **`docs/`** (20 files) — paths, filenames, registry names, and metadata values replaced. The
  three plans that quote the token *as the subject of the April sweep* use the labelled
  placeholder `<private-project-name>` instead of a generic project name, because substituting
  a real-looking name there would make the record incoherent ("change the default from
  `myproject` to `None`" — the value was never `myproject`).
- **`tests/test_public_repo_hygiene.py`** (new) — the standing check whose absence is the root
  cause.

**Deliberately not changed.** `README.md`, `pyproject.toml`, `LICENSE`, `SECURITY.md`,
`CHANGELOG.md`, `CONTRIBUTING.md`, `CLAUDE-full.md`, `.github/`, `assets/`, `demo/`, and
`.copier-answers.yml` — every occurrence there is the real GitHub account name in a real
repository URL, the author's name in the copyright line, or the published contact address.
Rewriting any of them breaks the badges or the package metadata.

## Verification

**The discriminating pattern.** The token, not immediately followed by the surname (with or
without an intervening space):

```text
<private-project-name>
```

where `<name>` is the private project name — written here as a placeholder so this document
does not itself carry the literal it describes.

Published metadata and documentation must use generic organization names, email addresses, and
repository URLs. The private repository names need no exception — they have no legitimate form
here.

**Two-way control test**, run before trusting any result:

- [x] **Flags a known-bad line.** `project_name="<name>"` and a bare private repository name,
      piped into the same pattern: both matched.
- [x] **Does not flag the real badge/URL lines.** The pattern over `README.md` returns nothing
      (exit 1), while a plain substring search over the same file returns 4 hits — so the
      clean result is a discrimination, not an empty file.
- [x] **Does not flag author/metadata files.** The pattern over `LICENSE`, `pyproject.toml`,
      `SECURITY.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CLAUDE-full.md`, `.github/`,
      `assets/`, `.copier-answers.yml`, `demo/`, `renovate.json5`: no hits.
- [x] **Legitimate URL count is unchanged.** `github.com/<account>/ai-harness` in
      `docs/README.md`: 4 at `HEAD`, 4 after the fix.

**Widening rings.**

- [x] The new guard fails on the unfixed tree, listing all 12 real violations, and passes
      after the fix.
- [x] Its own positive control (a violation in a temp tree) and negative control (badge,
      metadata and author lines in a temp tree) both behave correctly, so the guard is not
      trivially green.
- [x] Focused suites for all five touched test files plus the guard: 128 passed.
- [x] `src/` + `tests/` + `scripts/` + `setup.sh`: clean under the discriminating pattern.
- [x] Whole repository excluding archives: clean, with one known carve-out (below).
- [x] Private repository names, whole repository: clean.
- [x] Full suite: 1970 passed, 7 skipped, 0 failed — exactly the 1965-test baseline plus the
      5 new guard tests.
- [x] `ruff format --check src/ tests/`: clean (82 files). `ruff check src/ tests/`: the new
      file passes on its own; the repository reports 1075 pre-existing violations (1076 before
      this change), a separate tracked issue. The commit used `SKIP=ruff-check`, because that
      hook runs `--fix` and rewrote three unrelated files for those pre-existing violations.

**Known carve-out.** `.pre-commit-config.yaml:9` still contains the private name in a comment
citing a cross-repo plan document. It was left untouched on purpose: a concurrent change owns
that file, and touching it would collide. It is outside `src/` and `tests/`, so the standing
guard does not cover it.

**Archive directories — decided, not skipped.**

- `docs/plans/archive/` (3 files, 5 occurrences) — **scrubbed.** One occurrence was a personal
  email address, which the rule names explicitly; the rest were project-name references. The
  directory carries no do-not-edit marker.
- `docs/roadmap/archive/master-roadmap.DO-NOT-EDIT.md` (27 occurrences) — **left as-is.** The
  file's own first line reads "RETIRED — do not use, do not edit", it is named
  `DO-NOT-EDIT`, and it is a retired historical record under the fleet's 2026-07-25 cutover.
  Its occurrences are attributions in closed task entries ("Filed 2026-07-22 (<name>)"),
  cross-repo task-migration provenance, and — one entry — a verbatim quote of the April audit
  command, all of which lose their meaning as records if anonymised. There is also a live
  deferred issue (AI-CLI-119) asking whether this hygiene rule should apply to internal
  planning docs at all; pre-empting that decision by rewriting 27 lines of retired history
  would be the wrong order. Flagged for the maintainer rather than decided here.

## Lessons Learned

- **A rule that lives only in prose decays.** The April scrub was correct and thorough, and it
  still failed, because it shipped no check. Any repository-wide invariant asserted in a
  context file needs a test in the same change, or the next four commits will violate it while
  passing every gate. That is the root cause here, not inattention.
- **The rule was known and still missed, hours earlier, in this same repository.** BUG-003 was
  authored on 2026-07-28 by an author who had `CLAUDE.md` loaded, and it named two private
  repositories 15 times. Reproduction evidence is written by pasting real terminal output, and
  the paste is faithful by construction — that is what makes it good evidence and exactly what
  makes it leak. Anonymise reproduction output as you paste it, not in review.
- **A check that cannot fail is not a check.** The obvious verification here — grep for the
  token — is unusable, because the same string is a legitimate part of the repository's own
  URLs. Before reporting any search as clean, state what it would print if the conclusion were
  false; if that is indistinguishable from the clean result, the probe is broken, not the
  finding.
- **The over-fix is a real failure mode, not a hypothetical one.** The first pass at the
  `docs/` half of this very fix, written by an author who had just been warned about it,
  corrupted four legitimate GitHub URLs. Mechanical substitution over a token with two
  meanings needs the discriminator built into the script and an assertion that the legitimate
  form is byte-identical afterwards — not care.
- **Scope the guard so it can stay trustworthy.** The guard covers `src/` and `tests/` only.
  Extending it over `docs/` would make it fire on every legitimate repository URL and on
  retired historical records, and a guard that cries wolf gets suppressed. A narrow guard that
  holds beats a broad one that gets an exclusion list.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-07-28 — both violation classes fixed; standing guard added

Regression guard written and confirmed RED before any production edit (12 findings, matching
the inventory exactly), then frozen. Renames applied to `src/ai_cli/sync.py`,
`src/ai_cli/data/statusline-command.sh`, four test files, `setup.sh`,
`scripts/cleanup_cc_sessions.py`, `docs/bugs/session-launch-non-mac-host.md`, and 20 files
under `docs/` (including `docs/plans/archive/`). New `tests/test_public_repo_hygiene.py`.

The `docs/` substitution was reverted once and redone after the first version rewrote four
legitimate GitHub URLs; see Prior Fix Attempts.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

### Evidence — the guard, RED on the unfixed tree

```text
E       AssertionError: private project names in a public package:
E         src/ai_cli/data/statusline-command.sh:523: …
E         src/ai_cli/data/statusline-command.sh:537: …
E         src/ai_cli/data/statusline-command.sh:552: …
E         src/ai_cli/sync.py:211: …
E         tests/test_config_watch_hash.py:11: …
E         tests/test_config_watch_hash.py:60: …
E         tests/test_growthbook_launch_toggle.py:77: …
E         tests/test_runaway_loop_guards.py:45: …
E         tests/test_runaway_loop_guards.py:142: …
E         tests/test_session.py:1291: …
E         tests/test_session.py:1297: …
E         tests/test_session.py:1298: …
1 failed, 4 passed
```

The 4 that passed are the guard's own controls, which is what makes the 1 failure meaningful.

### Evidence — the pattern discriminates (both directions)

```text
CONTROL A (must flag)      → 1:project_name="<name>"
                             2:repo=<private-repo-name>          exit 0
CONTROL B (must not flag)  → (no output over README.md)          exit 1
   README.md does contain the token:  README.md:4
CONTROL C (must not flag)  → (no output over LICENSE, pyproject.toml, .github/, assets/, …)
                                                                 exit 1
```

### Evidence — the root cause, discriminated against its competing hypothesis

```text
at the privacy-sweep commit 15899d6 (2026-04-06), src/ + tests/:
   (no output)                                                   exit 1  → clean

at HEAD, src/ + tests/:
   src/ai_cli/data/statusline-command.sh:3
   src/ai_cli/sync.py:1
   tests/test_config_watch_hash.py:2
   tests/test_growthbook_launch_toggle.py:1
   tests/test_runaway_loop_guards.py:2
   tests/test_session.py:3

control — same probe at 15899d6 over docs/ (proves the probe ran at that revision):
   15899d6:docs/plans/going-public-plan.md:3
   15899d6:docs/plans/open-source-professionalization-plan.md:11
   …                                                             exit 0
```

<!-- /doc:region name="appendix_evidence" -->
