---

<!-- Canonical Jinja source: STUB.md.jinja. -->
title: "ai-cli-utils v0.8.0 Release-Readiness Audit"
category: audit
tags: [audit, release-readiness]
status: findings-pending-fix
date: 2026-08-28
source: "independent-code-audit"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---

# ai-cli-utils v0.8.0 Release-Readiness Audit

**Status:** findings-pending-fix

**Created:** 2026-08-28

**Auditor:** gpt-5.6-sol

**Target artifact:** Round 1 working tree at `c9841e3eb6bfc5f0043c144655a8e77cc3bcb6c7`;
Round 2 re-verification at `6cf82b5d020769c18d7021b60c04e84ccf407088`; Round 3 security and
backlog reconciliation at `30521555df0b2695b9e69a90d7028cfc09d79c4c`; package version `0.8.0`

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [What Was Audited](#what-was-audited)
- [Scope](#scope)
  - [In scope](#in-scope)
  - [Out of scope](#out-of-scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Findings](#r1-findings)
  - [Backlog triage](#backlog-triage)
  - [Handoff usage and retirement assessment](#handoff-usage-and-retirement-assessment)
  - [Feature-set spot-check](#feature-set-spot-check)
  - [Test-suite health](#test-suite-health)
  - [Detailed findings](#detailed-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
- [Round 2 — Current-Tree Re-verification](#round-2--current-tree-re-verification)
  - [R2 Summary](#r2-summary)
  - [R2.1 Finding Status Verification](#r21-finding-status-verification)
  - [R2.2 AD-1 Verification](#r22-ad-1-verification)
  - [R2.3 Verification Matrix](#r23-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
- [Round 3 — Security Hardening + Backlog Reconciliation](#round-3--security-hardening--backlog-reconciliation)
  - [R3 Summary](#r3-summary)
  - [R3.1 Backlog Reconciliation](#r31-backlog-reconciliation)
  - [R3.2 Security Findings](#r32-security-findings)
  - [R3 Verification Matrix](#r3-verification-matrix)
  - [R3 Recommendations](#r3-recommendations)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
  - [AD-1: Handoff retirement strategy](#ad-1)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
  - [Must do before release](#must-do-before-release)
  - [Nice to have before release](#nice-to-have-before-release)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 3 Reviewer Prompt](#round-3-reviewer-prompt)

## What Was Audited

This audit surveyed the unreleased `0.8.0` tree, 550 commits after tag `v0.7.0`, for release blockers, quick wins, stale backlog, structural feature health, handoff retirement, and test health. Every QUICK WIN and RELEASE-BLOCKING classification was checked against current source, tests, or git history rather than accepted from an issue title.

The Round 1 snapshot differed materially from older issue descriptions: update-stamp ordering,
editable-install preservation, quiet install, stopped-session handling, line endings, and icon lint
were already fixed. At that snapshot, handoff was automatically started and drained during
ordinary Claude-session launch, not merely invoked manually. Round 2 confirms those handoff paths
have since been retired; their current status is recorded under F-02 and AD-1.

## Scope

### In scope

- All 80 issue records embedded in the survey request.
- Public docs/config, CLI dispatch, top-level modules, handoff/NATS/launcher paths, tests, skips, and relevant git history.
- Structural feature evidence and release recommendations.

### Out of scope

- Interactive tmux, terminal, remote, VPN, browser-profile, and Windows hands-on UAT.
- Live NATS state, user queue contents, local schedulers, and external callers not committed here.
- The separately dispatched P0 stale-session topology audit.
- Task-store commands and implementation fixes.
- Independent retrieval of the vendor messaging document. Its behavior is a scope-supplied premise, separated from repository-confirmed facts.

## Methodology

**Approach:** Round 1 cross-referenced every supplied record with code/history, traced handoff call sites, mapped major modules to tests and recent commits, attempted the exact requested test commands, and reproduced safe checks under the write-restricted worker. Round 2 independently re-ran every finding's current-state check against source, docs, tests, tracked issue records, and git history. Round 3 re-derived the remaining MUST list, checked each shipped claim at its source/test location, resolved AD-1 through the decision framework, reproduced the seven CodeQL source patterns, and traced fresh security sources to sinks across process, filesystem, credential, and publication boundaries. **CONFIRMED** means reproduced from artifacts; **PLAUSIBLE** marks environment-dependent judgment. The canonical audit scaffold/template was read first. No implementation fix was applied.

## Status Summary

**Latest round:** Round 3

| Severity/disposition | Count | Resolved or non-finding | Partial | Open |
|----------------------|-------|-------------------------|---------|------|
| Prior P0 | 1 | 0 | 1 | 0 |
| Prior P1 | 9 | 5 | 4 | 0 |
| Prior P2 | 1 | 1 | 0 | 0 |
| Round 3 CRITICAL | 4 | 0 | 0 | 4 |
| Round 3 MAJOR | 4 | 0 | 0 | 4 |
| Round 3 CodeQL non-findings | 7 | 7 | 0 | 0 |
| **Total** | **26** | **13** | **5** | **8** |

**Ship-readiness verdict:** **Not ready.** The old implementation backlog is substantially shipped:
F-04, F-06, and F-07 pass this round, while F-02, F-08, and F-11 had already passed Round 2.
F-01, F-03, F-05, F-09, and F-10 remain partial because their literal audit/test or public-hygiene
exit conditions were not independently satisfied at the current HEAD. AD-1 is now resolved as
immediate removal. Independently of that progress, Round 3 found four CRITICAL and four MAJOR
security blockers (F-19 through F-26). **The v0.8.0 PyPI publish is a NO-GO.**

<!-- /doc:region name="scope" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** gpt-5.6-sol

**Round 1 date:** 2026-08-28

**Round 1 scope:** Clean-slate release survey at the pinned commit; findings/recommendations only.

### R1 Summary

The 80 records classify as **12 RELEASE-BLOCKING, 4 QUICK WIN, 36 DEFER, and 28 STALE/QUESTIONABLE**. Some blockers intentionally overlap one root defect and should not create duplicate work. Eleven independent findings were recorded: one P0, nine P1, one P2.

Test collection found **2,608 tests**. The exact full command never reached pytest because uv could not create its cache; direct full pytest could not create the autouse temporary directory. Thus current full pass/fail/skip totals are **unverified**, not zero. A focused current-tree reproduction did run and produced **1 failed** test.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | FAIL | Active architecture/usage docs advertise a removed command and nonexistent `src/ai_cli/gemini.py`/`research.py` (`docs/designs/architecture.md:37,57,64,87-89`; `docs/tools/ai-cli-usage.md:306-378`). |
| IC-2 | FAIL | `docs/plans/handoff-reliability-testing.md:48` names a home queue, while implementation resolves below the configured main project (`src/ai_cli/config.py:441-445`). |
| IC-3 | PASS | Docs acknowledge automatic pickup (`README.md:251-258`; `docs/tools/ai-cli-usage.md:465-496`), agreeing with launch hooks (`src/ai_cli/session_script.py:525-529,651-656`). |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| JA-1 | FAIL | Public rules forbid personal identifiers, but metadata contains an identity/email and its hygiene test exempts them (`pyproject.toml:8-10`; `tests/test_public_repo_hygiene.py:110-125`). Values are intentionally not repeated. |
| JA-2 | FAIL | Public `.[dev]` omits xdist/timeout (`pyproject.toml:71-89`) while default pytest options require xdist (`:97-110`). |
| JA-3 | PARTIAL | Windows is supported per `README.md:67-80`, but unresolved Windows issues and pending E2E have no current hands-on pass artifact. |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| DV-1 | FAIL | NATS payload data controls filesystem paths without validation (`src/ai_cli/main.py:1453-1464,1505-1517`). |
| DV-2 | WARN | Scope-supplied native messaging addresses live sessions; handoff persists files/messages (`src/ai_cli/handoff.py:28-82`; `messaging.py:205-220,263-292`). Retirement drops semantics. |
| DV-3 | FAIL | Copier forces defaults and equates exit-zero/no conflict markers with success (`src/ai_cli/copier_update.py:151-203,353-375`), which cannot prove semantic parity. |

### Backlog triage

The evidence cell is the verification note. STALE/QUESTIONABLE means revalidate/deduplicate, not automatically close.

| Issue | Classification | Verification note / rationale |
|-------|----------------|-------------------------------|
| AI-CLI-9s2k | RELEASE-BLOCKING | Known P0 in flight; launch/reaper integration is current at `session_script.py:516-529`. Require its separate audit; do not duplicate. |
| AI-CLI-pyh | DEFER | Process/design audit, not package behavior; this audit covers the immediate gate. |
| AI-CLI-zks | DEFER | Large new lifecycle feature, not stabilization. |
| AI-CLI-9pb | STALE/QUESTIONABLE | Current iTerm code emits set operations, not terminal queries (`iterm2.py:391-451`); capture a reproducer before accepting the hypothesis. |
| AI-CLI-2ul | RELEASE-BLOCKING | Audit records the physical directory (`session_audit.py:277-303`) but adoption re-slugifies cwd (`:348-378`; `session_adopt.py:723-763`). |
| AI-CLI-1ku | DEFER | Environment migration depends on the general adoption fix. |
| AI-CLI-70on | DEFER | Environment-specific live UAT, not a source quick fix. |
| AI-CLI-2139 | STALE/QUESTIONABLE | Fixed by `6502c72`; current code handles stopped processes (`main.py:341-447`) with dedicated tests. |
| AI-CLI-pcv | STALE/QUESTIONABLE | Current Windows-safe reuse checks emptiness and fails closed (`session.py:989-1006,1100-1114`); reproduce current code first. |
| AI-CLI-y1s | RELEASE-BLOCKING | **PLAUSIBLE behavior, CONFIRMED support gap.** Bare path is `main.py:516-574`; Windows is advertised (`README.md:67-80`). Fix or narrow support. |
| AI-CLI-gdkr | DEFER | External repository housekeeping. |
| AI-CLI-2qu | RELEASE-BLOCKING | Confirmed traversal (`main.py:1453-1464,1505-1517`) is reachable from automatic hooks (`session_script.py:525-529,651-656`). |
| AI-CLI-70q | STALE/QUESTIONABLE | Supersede after AD-1 and ingress deactivation; do not build queue v2 during retirement. |
| AI-CLI-6bg | STALE/QUESTIONABLE | Fixed: stamp is written only after success (`main.py:835-880`). |
| AI-CLI-9sy | DEFER | New cross-repository destructive-change policy. |
| AI-CLI-995 | STALE/QUESTIONABLE | Loop has a bounded delay/regression guard (`session_script.py:765-785`; `test_runaway_loop_guards.py:1-84`); fixes `6cbaaef`/`7cca62a`. |
| AI-CLI-8kd | STALE/QUESTIONABLE | Current launch resolves direnv and handles blocked state (`session_script.py:568-607,765-771`); fix `02fce5c`. |
| AI-CLI-ieh | DEFER | Coverage percentage is lower priority than executable green tests. |
| AI-CLI-3b3 | STALE/QUESTIONABLE | Supersede after AD-1; do not harden a retired architecture. |
| AI-CLI-077 | DEFER | New registry UX. |
| AI-CLI-66g | STALE/QUESTIONABLE | Notifications/optional Windows backend exist (`notifications.py:1-589`; `pyproject.toml:71-78`); restate remaining gap. |
| AI-CLI-81mx | RELEASE-BLOCKING | Focused current run confirms `test_process_probe.py:373-389` fails: `end_process()` returns false. |
| AI-CLI-nieb | RELEASE-BLOCKING | Test mocks old `direnv exec` (`test_session.py:1176-1203`); production uses `direnv export json` (`direnv_setup.py:150-171`). |
| AI-CLI-7p76 | DEFER | Environment decommission coordination. |
| AI-CLI-gqgy | STALE/QUESTIONABLE | Duplicate umbrella for 81mx/nieb. |
| AI-CLI-t4aq | STALE/QUESTIONABLE | Duplicate platform expression of 81mx. |
| AI-CLI-1nxv | STALE/QUESTIONABLE | Already aligned at ruff `0.16.4` (`pyproject.toml:76,85`; `.pre-commit-config.yaml:53-55`). |
| AI-CLI-bsxa | DEFER | Browser profile hardening needs design/runtime work. |
| AI-CLI-32um | STALE/QUESTIONABLE | Named behaviors already landed (`6502c72`, `a7d0a7d`); review unique diffs, do not merge by title. |
| AI-CLI-jz2 | STALE/QUESTIONABLE | Environment-specific editor/Git state lacks current reproducer. |
| AI-CLI-obd2 | DEFER | Broad documentation sweep. |
| AI-CLI-1wi | DEFER | OS-agnostic umbrella; concrete Windows gate handled separately. |
| AI-CLI-11c3 | STALE/QUESTIONABLE | Fixed by `0edbc94`; editable installs preserved (`main.py:1783-1813,2037-2047`). |
| AI-CLI-mbci | STALE/QUESTIONABLE | Fixed by `3676b23`; current file has zero CRLF and `.gitattributes:1-13` pins LF. |
| AI-CLI-ww8o | STALE/QUESTIONABLE | Fixed by `a7d0a7d`; fingerprint early-return at `main.py:792-815`. |
| AI-CLI-8s1 | RELEASE-BLOCKING | Same confirmed root as 2ul (`session_adopt.py:712-724`). |
| AI-CLI-srqy | DEFER | Local tmux repair is environment work; real-tmux skips document the gap (`test_session_launch_integration.py:55-85`). |
| AI-CLI-yf8b | STALE/QUESTIONABLE | One-machine config migration; verify state before acting. |
| AI-CLI-5g58 | STALE/QUESTIONABLE | External sessions cannot receive launcher namespace by construction; define supported integration first. |
| AI-CLI-5m2o | DEFER | Repository bookkeeping; task-store commands intentionally not run. |
| AI-CLI-m7uh | RELEASE-BLOCKING | Broader than title: `.[dev]` omits timeout and xdist while addopts requires xdist (`pyproject.toml:71-110`). |
| AI-CLI-9r4c | DEFER | Legitimate larger recovery feature after adoption fix. |
| AI-CLI-bg7 | STALE/QUESTIONABLE | No implementation in `src`/`tests`; route to owning tool after current reproduction. |
| AI-CLI-lk8 | RELEASE-BLOCKING | Success checks exit status/conflict markers only (`copier_update.py:151-203`); cleanly dropped hunk is undetectable. |
| AI-CLI-40d | STALE/QUESTIONABLE | Same fixed editable root as 11c3 (`main.py:2037-2047`). |
| AI-CLI-5ga | DEFER | New destructive lifecycle policy. |
| AI-CLI-qbq | DEFER | External worktree state; future check useful, not quick release fix. |
| AI-CLI-lb9 | QUICK WIN | Unguarded cross-repo Git remains in `trust.py:73-81,158-177` and `workspace.py:17-24`; helper/pattern already exist. |
| AI-CLI-73x | DEFER | Cross-repository ownership/design decision. |
| AI-CLI-sgv | DEFER | New vendor-daemon lifecycle feature. |
| AI-CLI-mqm | DEFER | Branch housekeeping needs human diff review. |
| AI-CLI-13q | DEFER | Broad audit beyond concrete defects found here. |
| AI-CLI-zq7 | DEFER | Hands-on recording polish, not code quick win. |
| AI-CLI-dkx | DEFER | Live network UAT outside this environment. |
| AI-CLI-0ay | STALE/QUESTIONABLE | Supersede after AD-1; do not expand retired NATS coverage. |
| AI-CLI-bna | DEFER | New package work. |
| AI-CLI-pm2 | DEFER | Feature-phase work without confirmed release-critical path. |
| AI-CLI-5p8 | STALE/QUESTIONABLE | Product preference, not correctness defect. |
| AI-CLI-jhq | STALE/QUESTIONABLE | Design doc exists at `docs/designs/cc-statusline.md`; reconcile/close. |
| AI-CLI-gs0 | DEFER | New panel lifecycle feature. |
| AI-CLI-d2z | RELEASE-BLOCKING | Both copier paths force `--defaults` (`copier_update.py:151-157,359-368`); stored answers are not protected. |
| AI-CLI-l5t | DEFER | Large parity-gated refactor. |
| AI-CLI-jml | RELEASE-BLOCKING | If Windows remains supported (`README.md:67-80`), E2E is required; otherwise narrow claim. |
| AI-CLI-udl | DEFER | Broad reliability program; split concrete repros. |
| AI-CLI-84h | QUICK WIN | Relative source accepted (`copier_update.py:30-40`) but isolated cwd changes (`:151-157,206-249`); resolve before isolation, add one test. |
| AI-CLI-wn3 | DEFER | Coverage expansion without specific blocker. |
| AI-CLI-xg5b | DEFER | Companion to separately audited P0. |
| AI-CLI-d32u | DEFER | Hardening belongs with in-flight topology. |
| AI-CLI-x9lo | RELEASE-BLOCKING | Hard-rule violation at `pyproject.toml:8-10` and exemptions at `test_public_repo_hygiene.py:110-125`. |
| AI-CLI-660i | STALE/QUESTIONABLE | Duplicate/concurrency expression of nieb. |
| AI-CLI-bjs7 | QUICK WIN | `Path.exists()` at `process_probe.py:252-254` authorizes unlink via `main.py:377-378,487-490`; use errno-aware tri-state. |
| AI-CLI-x2ua | DEFER | Human-reviewed stash/branch housekeeping. |
| AI-CLI-3tjq | DEFER | Dependency policy/branch cleanup. |
| AI-CLI-uln5 | DEFER | Broad exception-style maintenance. |
| AI-CLI-50l | STALE/QUESTIONABLE | External-repository-specific; create generic failing fixture first. |
| AI-CLI-mda | STALE/QUESTIONABLE | Product option may be superseded by current diagnostics (`main.py:849-876`). |
| AI-CLI-6tq | QUICK WIN | Two deterministic doc reference/status corrections; verify targets and validate docs. |
| AI-CLI-49g | DEFER | New telemetry with product/privacy implications. |
| AI-CLI-84l | STALE/QUESTIONABLE | Current pinned ruff reports “All checks passed” for the script. |
| AI-CLI-8wg | DEFER | External stash disposition. |

### Handoff usage and retirement assessment

| Surface | Evidence | Assessment |
|---------|----------|------------|
| Local queue | `src/ai_cli/handoff.py:28-82,90-223` | Pending/claimed/completed files; post/check/claim/complete. |
| CLI | `src/ai_cli/main.py:3363-3407,4060-4086` | Public handoff and signal-watch groups. |
| Inbound NATS | `main.py:1430-1531,1553-1613,1639-1747` | Watcher/drain replicate content locally and trigger pickup. |
| Launcher | `session_script.py:525-529,651-656,689-700,775-780` | Every eligible Claude tmux launch starts watcher, drains before first launch, injects prompt, and checks mid-loop pickup. **Not manual-only.** |
| Supervisor | `process_manager.py:77-100`; `session_script.py:204-216` | Managed watcher starts/stops with sessions. |
| Semantics | `messaging.py:205-220,263-292` | JetStream can fall back to non-durable core NATS; durable subscriber ACKs after callback but defines no explicit lease/reconciler/dead-letter policy. |
| Tests/plans | Four handoff/messaging test files; `docs/plans/handoff-reliability-testing.md:97-183` | Substantial tests exist, but planned reliability layers/live tests remain incomplete. |
| User docs | `README.md:32,251-258,377,411,426`; `docs/tools/ai-cli-usage.md:443-496`; `docs/guides/nats-setup.md:30-57,121-143,226-245` | Still recommended and configured. |

Repository grep found no committed external queue automation beyond this package's launcher, supervisor, CLI, tests, and docs. That is not proof of no users: user-local schedulers, live queues, and downstream callers were unavailable. Inventory those before disabling ingress.

Native same-machine live messaging can replace “send to a running session.” Per the scope-supplied behavior it does not replace offline/cross-machine durable queuing, pending/claimed/completed state, receiver-absence retry, or a visible backlog. Current handoff only partially implements stronger reliability, but still persists more state. Release notes must say these semantics are intentionally retired, not “replaced.”

Recommended sequence:

1. Inventory callers and export/backup pending/claimed entries; document dropped semantics.
2. Before release, disable both NATS inbound paths or validate schema/type/size, strict/generated basename, resolved containment, symlinks, and atomic creation.
3. Remove automatic hooks at `session_script.py:525-529,651-656,775-780`, cleanup, and supervisor watcher; stop new remote posts.
4. Keep one deprecation cycle of visibly deprecated wrappers. Allow safe local read/claim/complete/export; make `post` fail with migration guidance unless securely local-only.
5. After wrappers exist, move dormant implementation/history under `archive/`; remove recommendations from README/usage/NATS setup and test that no launch hook remains.
6. Remove wrappers in the next breaking release.

Close AI-CLI-70q, AI-CLI-3b3, and AI-CLI-0ay as superseded only after retirement/deactivation lands. AI-CLI-2qu is release-blocking today and becomes moot only if every vulnerable write path is unreachable. If any archived command/subscriber remains callable, fix it first.

### Feature-set spot-check

“Recent” means a file-specific August 2026 commit. This is structural evidence, not functional certification.

| Feature | Maintenance / test evidence | Risk or status |
|---------|-----------------------------|----------------|
| Sessions/tmux/worktrees/remote/bare | `session.py` touched 08-27; `session_script.py` 08-28; dedicated session/launch/bare suites | Active; in-flight P0 and Windows risks need UAT. |
| Sync | `sync.py` touched 08-24; extensive `test_sync.py` | Active; broad reliability/UAT backlog remains. |
| Handoff | module touched 08-07; launcher integration 08-28; four dedicated suites | Active but incomplete; staged retirement recommended. |
| Shared NATS | `messaging.py` touched 08-19; messaging/sync/quota tests | Active beyond handoff; do not retire whole module. |
| Process hygiene/reaper | modules touched 08-19/08-28; dedicated suites | Active; current probe failure and separate P0. |
| Audit/adopt/migrate | modules touched 08-14/08-23/08-16; dedicated suites | Active, but confirmed source-slug inconsistency. |
| Quota/usage/spend/statusline | quota touched 08-19; usage 08-08; dedicated suites | Active. Statusline design already exists. |
| Tunnel/transport/browser proxy | transport 08-24; tunnel 08-19; dedicated tests | Active; VPN/profile items need live UAT. |
| Notifications | module 08-07; notification/quota tests; Windows extra at `pyproject.toml:71-78` | Implemented; remaining Windows gap underspecified. |
| iTerm/layout/icons | integration 08-19; dedicated suites | Active/platform-specific; icon script passes ruff. |
| Copier propagation | module 08-14; 600-line test file | Implemented but semantic safety is release-blocking. |
| Telemetry/memory | telemetry 08-07; dedicated tests | Generic feature exists; behavior-scoring proposal is separate unfinished work. |
| Picker/setup/trust/workspace | wired top-level modules with dedicated tests | Active; cross-repo Git environment guard incomplete. |

No interactive feature was exercised. Human RC smoke tests should cover local tmux, bare mode, remote reconnect, sync conflict, quota/statusline, and supported Windows behavior.

### Test-suite health

- Exact `uv run pytest --collect-only -q 2>&1 | tail -30`: **did not reach pytest**; uv could not create its cache under the single-file write policy.
- Exact `uv run pytest -q 2>&1 | tail -60`: same result.
- Read-only direct collection with the existing environment, no cache/xdist, and `tempfile.tempdir="."`: **2,608 tests collected in 1.62s**.
- Direct full suite could not create the autouse directory at `tests/conftest.py:385-390`.
- Focused current run without repository conftest: **1 failed in 10.21s** at `tests/test_process_probe.py:373-389`.

Therefore current full counts are **2,608 collected; pass/fail/skip = unverified**. An older issue note or partial run is not a current total. Release CI must rerun the exact commands in a writable environment.

No `xfail` was found. Skip sites have documented premises: uv (`test_cli.py:3036-3073`), jq/POSIX (`test_quota.py:2761-2765`), shell (`test_runaway_loop_guards.py:58-59`), case aliases (`test_worktree_container_collision.py:296-297`), Windows teardown (`test_session.py:431-438`), procfs (`test_bare_worktree.py:181-234`), Windows self-update (`test_windows_self_update.py:151-177`), real tmux (`test_session_launch_integration.py:55-85`; `test_session_launch_shell_resolution.py:94-114`), Git checkout (`test_update_pyproject_bytes.py:142-143`), and shell/signals (`test_stale_session_reaper.py:229-237`). None imported a removed module. Real-tmux skips are genuine environment gaps, not dead tests.

Line-count comparison found no obviously abandoned large module: `main`, `sync`, `quota`, `session`, and handoff all have dedicated large suites. That establishes infrastructure, not branch coverage or functional quality.

### Detailed findings

#### F-01: In-flight stale-session topology remains a release gate — `P0`

**Current status (2026-08-29): STILL OPEN — CONFIRMED.** The separate implementation audit remains
`findings-pending-fix`, says reap mode is not safe to enable, and records two unfixed P0s
(`docs/audits/stale-session-reaper-implementation-audit.md:5,56-58,104,181,219`). The requested live
`bd show AI-CLI-tdm6.1` could not open the embedded database in this single-file sandbox; the tracked
JSONL does not contain that child, so the supplied live-state note that it remains open is not
silently upgraded to repository-confirmed evidence.

**Location:** `src/ai_cli/session_script.py:516-529`; `docs/audits/stale-session-reaper-implementation-audit.md`

**Evidence:** launch executes automatic cleanup then starts watchers; the supplied backlog identifies a separately audited P0. **Verification:** `git log -1 --oneline --` those paths. **Impact:** ordinary sessions execute process/destructive authority. **Recommendation:** require the separate audit to pass on the exact release commit.

#### F-02: Inbound handoff payloads can escape the queue — `P1`

**Current status (2026-08-29): RESOLVED — CONFIRMED.** Commit `86c41d0` removed both inbound handlers,
the drain/watch internal actions, automatic launch hooks, and watcher lifecycle. Current source has
no vulnerable handler symbol or handoff integration; `src/ai_cli/main.py:3051-3063` retains only an
exit-1 retirement stub, and `archive/handoff.py` is outside the shipped package.

**Location:** `src/ai_cli/main.py:1453-1464,1505-1517`

**Evidence:** both handlers use `pending_dir / filename` and `write_text(content)` from payload data without containment/schema/size/symlink checks. **Verification:** `rg -n 'local_file = pending_dir / filename|local_file.write_text\(content' src/ai_cli/main.py`. **Impact:** a publisher can write outside the queue; launch activates ingress automatically. **Recommendation:** disable ingress under AD-1, or fully validate and atomically create with adversarial tests.

#### F-03: Test gate is red and complete result unverified — `P1`

**Current status (2026-08-29): REFRAMED, STILL OPEN — CONFIRMED.** Commits `8894668` and `cdbcbf1`
repair the two originally cited test defects (`tests/test_process_probe.py:372-388` and
`tests/test_session.py:1178-1207`). GitHub-hosted CI is now explicitly non-authoritative because
billing prevents code execution (`docs/procedures/github-actions-retirement.md:10-30`). However, the
replacement local gate is not green: Ruff check/format pass, while Pyright 1.1.411 reports 81 errors
with the existing environment (including unresolved relative imports in `archive/handoff.py:13,63`),
and pytest cannot initialize because this audit sandbox exposes no writable temporary directory.
The original red-test causes are fixed; a complete authoritative green result is still absent.

**Location:** `tests/test_process_probe.py:373-389`; `tests/test_session.py:1176-1203`; `tests/conftest.py:385-390`

**Evidence:** focused run returns `1 failed`; direnv test mocks an obsolete command versus `direnv_setup.py:150-171`; full totals unavailable. **Verification:** exact full commands plus named focused tests in a writable checkout. **Impact:** no healthy release-suite evidence. **Recommendation:** fix/deduplicate both failures and require zero failures on supported platforms with counts attached.

#### F-04: Published dev extra cannot run configured pytest — `P1`

**Current status (2026-08-29): STILL OPEN — CONFIRMED.** The public `dev` extra still omits xdist and
timeout at `pyproject.toml:71-78`, while the separate dependency group contains them at `:80-95` and
configured pytest still requires `-n auto` at `:97-110`.

**Location:** `pyproject.toml:71-89,97-110`

**Evidence:** `.[dev]` lacks xdist/timeout while addopts is `-n auto`. **Verification:** sanitized `tomllib` command in matrix. **Impact:** contributor workflow breaks before tests run. **Recommendation:** one canonical dependency set and clean CI installing only `.[dev]`.

#### F-05: Audit and adoption disagree on transcript source — `P1`

**Current status (2026-08-29): STILL OPEN — CONFIRMED.** The auditor records the physical containing
directory (`session_audit.py:289-299`) but `adopt_ready()` still passes the transcript-recorded cwd as
`source_root` (`:348-377`); the adopter re-slugifies that cwd at
`session_adopt.py:712-724`. No physical-project-directory handoff or legacy-slug regression closes
the mismatch.

**Location:** `session_audit.py:277-303,348-378`; `session_adopt.py:712-763`

**Evidence:** audit knows physical `project_dir`; batch passes cwd; adopter slugifies it and may report no transcript. **Verification:** `rg -n 'project_dir=project_dir|source_root=Path\(record.cwd\)|source_dir = cc_project_dir'` in both modules. **Impact:** an “adoptable” session is rejected. **Recommendation:** pass physical path/transcript UUID explicitly and test root/worktree/legacy slugs.

#### F-06: Windows support lacks release attestation — `P1`

**Current status (2026-08-29): STILL OPEN — CONFIRMED support gap; PLAUSIBLE behavior risk.** README
still promises MSYS2/Git Bash support (`README.md:65-78`), while the real-tmux integration suite
still skips Windows because that behavior is unverified (`tests/test_session_launch_integration.py:57-68`).

**Location:** `README.md:67-80`; `main.py:516-574`; `test_session_launch_integration.py:55-85`

**Evidence:** support is claimed, bare launch is relevant, issues/E2E remain, real tmux is skipped. Behavior is **PLAUSIBLE**; missing attestation **CONFIRMED**. **Verification:** `rg -n 'Windows|bare.*mode|real_tmux'` in cited files. **Recommendation:** pass keyboard/interrupt/stale-dir E2E or mark Windows experimental with limitations.

#### F-07: Active docs advertise removed modules/commands — `P1`

**Current status (2026-08-29): PARTIAL, STILL OPEN — CONFIRMED.** Commit `f3e181d` correctly turns the
removed Gemini wrapper/modules into historical removal notes (`docs/tools/ai-cli-usage.md:300-306`)
and those modules remain absent. But the same active architecture document still says
`process_manager.py` owns `signal-watch` and `session_script.py` performs a handoff drain
(`docs/designs/architecture.md:55,57`), contradicting the current source and the retired-command note
at `:120-121`. The broad finding is therefore not fully resolved.

**Location:** `docs/designs/architecture.md:35-39,43-74,87-89`; `docs/tools/ai-cli-usage.md:306-378`

**Evidence:** two listed modules do not exist and a removed wrapper remains documented. **Verification:** module existence check in matrix. **Impact:** public users receive invalid commands/architecture. **Recommendation:** regenerate from Click registrations, archive obsolete sections, scrub private examples, and smoke-test documented `--help`.

#### F-08: README fences break rendered instructions — `P1`

**Current status (2026-08-29): RESOLVED — CONFIRMED.** Commit `7bfa7a5` repaired the closers, added
`tests/test_readme_fences.py`, and the current regression parser returns `[]` for `README.md`.

**Location:** `README.md:119-180,198-222`

**Evidence:** language-tagged fences appear while another fence is open; parser found 25 and ended unclosed. **Verification:** fence parser in commands/matrix. **Impact:** installation/usage prose renders as code. **Recommendation:** use bare closing fences, render-check, and replace placeholder badge paths at `README.md:5-8` with canonical public links.

#### F-09: Public identity rules remain violated — `P1`

**Current status (2026-08-29): STILL OPEN — CONFIRMED.** Commit `f41eb4d` scrubbed other occurrences
but did not touch the cited metadata or exemption. `pyproject.toml:8-10` still has populated personal
author fields, while `tests/test_public_repo_hygiene.py:9-21,29,110-125` intentionally excludes docs
and treats repository/author identity as allowed, contradicting the repository's stated rule.

**Location:** `pyproject.toml:8-10`; `tests/test_public_repo_hygiene.py:46-63,110-125`

**Evidence:** personal metadata exists and tests exempt it; values intentionally omitted. **Verification:** sanitized metadata-presence check. **Impact:** publication makes prohibited identifiers durable. **Recommendation:** generic/omitted author metadata, remove exemptions, scan root metadata/active docs.

#### F-10: Copier can report success after semantic loss — `P1`

**Current status (2026-08-29): STILL OPEN — CONFIRMED.** Both update paths still force `--defaults`
(`copier_update.py:155-161,386-395`), and the isolated path still returns `ok` after only status,
marker, commit, and optional push checks (`:166-209`). No stored-answer or intended-hunk parity gate
was added.

**Location:** `src/ai_cli/copier_update.py:141-203,353-375`

**Evidence:** both paths force `--defaults`; success proves only exit/no markers/commit, not answers or intended hunks. **Verification:** `rg -n '\[copier_bin, "update"|--defaults|_conflict_files|return "ok"'`. **Impact:** incomplete changes can be committed/pushed as success. **Recommendation:** preserve answers, resolve source before isolation, and fail closed on drifted-anchor fixtures.

#### F-11: Live messaging is not durable-queue parity — `P2`

**Current status (2026-08-29): RESOLVED — CONFIRMED as intentional retirement, not parity.** The
runtime queue is retired and the changelog explicitly says this intentionally removes offline,
cross-machine, and durable lifecycle semantics (`CHANGELOG.md:69-73`). This closes the disclosure
risk identified here; it does not claim native messaging provides queue parity. AD-1 remains
formally pending as a separate governance record.

**Location:** `handoff.py:28-82,90-223`; `messaging.py:205-220,263-292`; `handoff-reliability-testing.md:31-42,97-183`

**Evidence:** handoff persists lifecycle files/messages; scope-supplied native messaging targets live sessions; stronger queue layers are unfinished. **Verification:** `rg -n 'pending|claimed|completed|subscribe_durable|dead.letter|lease|reconcile'` in cited files. **Impact:** retirement silently loses offline/durable semantics unless declared. **Recommendation:** AD-1(a); do not build queue v2 solely for parity no longer wanted.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| F-01 | STILL OPEN | Separate implementation audit remains `findings-pending-fix` with two P0s. |
| F-02 | RESOLVED (`86c41d0`) | Inbound handlers/hooks removed; CLI fails closed through retirement stub. |
| F-03 | REFRAMED — STILL OPEN | Original two pytest defects fixed; hosted CI is non-authoritative, but local Pyright reports 81 errors and full pytest is unverified here. |
| F-04 | STILL OPEN | Public dev extra still omits xdist/timeout. |
| F-05 | STILL OPEN | Audit still passes recorded cwd instead of the known physical project directory. |
| F-06 | STILL OPEN | Windows is still advertised while real-tmux Windows coverage remains skipped as unverified. |
| F-07 | PARTIAL — STILL OPEN (`f3e181d`) | Removed Gemini docs fixed; active architecture still advertises retired signal-watch/handoff-drain behavior. |
| F-08 | RESOLVED (`7bfa7a5`) | README fence regression parser returns no errors. |
| F-09 | STILL OPEN | Metadata and explicit hygiene exemptions remain. |
| F-10 | STILL OPEN | Both copier paths still use `--defaults`; no semantic parity gate exists. |
| F-11 | RESOLVED (`86c41d0`) | Changelog explicitly declares intentional loss of offline/cross-machine/durable semantics. |

No implementation fixes were applied by either audit round; the resolved rows cite independently
verified changes already present in the current tree.

### R1 Verification Matrix

| Finding | Command | Actual | Pass? |
|---------|---------|--------|-------|
| F-02 | traversal `rg` above | write sites at 1458/1464 and 1510/1516 | ✅ |
| F-03 | focused process-probe test | `1 failed in 10.21s`; `ended is False` | ✅ |
| F-04 | parse dev/addopts | `xdist=False, timeout=False, addopts=-n auto` | ✅ |
| F-05 | source-selection `rg` | physical dir recorded; cwd passed/re-slugified | ✅ |
| F-07 | module existence | both false | ✅ |
| F-08 | fence-state parser | 25 tagged fences while open; final unclosed | ✅ |
| F-09 | sanitized metadata check | one entry; name/email fields true | ✅ |
| stale CRLF claim | byte count | `CRLF=0 LF=176` | ✅ |
| stale icon-lint claim | pinned ruff | `All checks passed!` | ✅ |
| automatic handoff | launch-hook `rg` | lines 527 and 655 | ✅ |

**Verified: 10/10 selected claims reproduce at `c9841e3eb6bfc5f0043c144655a8e77cc3bcb6c7`.** This does not turn the blocked full suite into a pass.

## Round 2 — Current-Tree Re-verification

**Round 2 auditor:** gpt-5.6-sol, independent current-tree verification

**Round 2 date:** 2026-08-29

**Round 2 scope:** Verify every F-01 through F-11 finding and AD-1 against current source, docs,
tests, tracked issue records, and git history at `6cf82b5d020769c18d7021b60c04e84ccf407088`.
No implementation or decision resolution was authorized.

### R2 Summary

Three findings are resolved: F-02, F-08, and F-11. F-03 is reframed but remains open: its two
original pytest defects are repaired and hosted CI is not a code signal, but the documented local
gate is not green. F-07 is partial because its original Gemini material is corrected while the
active architecture still advertises retired handoff behavior. F-01, F-04, F-05, F-06, F-09, and
F-10 remain open. AD-1 remains pending; shipped behavior matches option (b) in practice.

The repository-local canonical `docs/audits/TEMPLATE.md` referenced by `docs/audits/README.md` is
absent. The canonical harness copy was read before this round. The requested `bd show` commands
could not open the embedded database because the write-target sandbox forbids its lock file; the
tracked JSONL confirms AI-CLI-fae is in progress and AI-CLI-pt9n is closed, but does not contain
AI-CLI-tdm6.1. Those limitations are not treated as proof of issue absence or closure.

### R2.1 Finding Status Verification

| ID | Current verdict | Evidence and verification note |
|----|-----------------|--------------------------------|
| F-01 | **STILL OPEN** | Separate implementation audit is `findings-pending-fix` and records two unfixed P0s. **CONFIRMED repository artifact; live child-issue state unavailable in sandbox.** |
| F-02 | **RESOLVED** | Vulnerable handlers, drain/watch actions, hooks, and managed watcher are absent; `cmd_handoff_retired` exits 1. **CONFIRMED.** |
| F-03 | **REFRAMED — STILL OPEN** | Original failing tests were corrected; hosted CI is billing-blocked, but Pyright 1.1.411 reports 81 errors and pytest cannot initialize here. Ruff lint/format pass. **CONFIRMED commands.** |
| F-04 | **STILL OPEN** | Public `.[dev]`: `xdist=False`, `timeout=False`; addopts remains `-n auto`. **CONFIRMED.** |
| F-05 | **STILL OPEN** | Physical `project_dir` is recorded, but `adopt_ready` passes `Path(record.cwd)` and adopter re-slugifies it. **CONFIRMED.** |
| F-06 | **STILL OPEN** | Windows support remains public; real-tmux Windows integration remains explicitly skipped as unverified. **CONFIRMED support gap; behavior PLAUSIBLE.** |
| F-07 | **PARTIAL — STILL OPEN** | Removed Gemini command/module prose is historical and correct; architecture still claims signal-watch lifecycle and handoff drain. **CONFIRMED.** |
| F-08 | **RESOLVED** | Current README fence parser returns `[]`; regression test exists. **CONFIRMED.** |
| F-09 | **STILL OPEN** | Populated personal author fields and explicit repository/author exemptions remain; scan excludes docs. **CONFIRMED without repeating values.** |
| F-10 | **STILL OPEN** | Both copier paths still force `--defaults`; success still lacks answer/hunk parity. **CONFIRMED.** |
| F-11 | **RESOLVED** | Handoff is retired and changelog explicitly declares the intended loss of offline/cross-machine/durable semantics. **CONFIRMED; no parity claim made.** |

### R2.2 AD-1 Verification

| ID | Verdict | Evidence |
|----|---------|----------|
| AD-1 | **PENDING; implementation matches (b) in practice** | `86c41d0` implements full archival with no compatibility window: inbound paths/hooks/lifecycle are removed, old code is under `archive/`, and the only public command is a fail-closed stub (`main.py:3051-3063`). This is not option (a), whose safe read/export compatibility cycle is absent. Per the audit authority, `chosen-option=PENDING` is unchanged. |

**Post-R2 addendum (2026-08-29, same day, after this round's snapshot commit):** Sergei explicitly
chose full removal over stubbing ("we should fully remove them, not just stub them"). PR #87
(squash `83d2578`) removed `cmd_handoff_retired` and its Click registration entirely -- `ai handoff`
is no longer a recognized command at all, not even a fail-closed stub -- and deleted
`archive/handoff.py`. AD-1 should be recorded as **Resolved by human: (b) immediate removal** the
next time this doc's decision record is updated; this round intentionally left `chosen-option`
untouched per its own authority boundary, so that update is left for a follow-up pass rather than
made here.

### R2.3 Verification Matrix

| Finding | Command/check | Expected current state | Actual | Status verified? |
|---------|---------------|------------------------|--------|------------------|
| F-01 | Re-grep implementation-audit status/P0 rows | Open safety gate | `findings-pending-fix`; 2 P0 / 0 fixed | ✅ |
| F-02 | Handler/hook/watcher symbol grep | Only retirement stub | Only `cmd_handoff_retired` matched | ✅ |
| F-03 | Ruff, Pyright, pytest attempts + fix-diff read | Mixed gate stated exactly | Ruff passes; Pyright 81 errors; pytest no usable temp dir; cited test fixes present | ✅ |
| F-04 | Parse `pyproject.toml` public dev/addopts | Missing xdist/timeout; `-n auto` | `xdist False timeout False addopts -n auto` | ✅ |
| F-05 | Source-selection grep | Recorded cwd still re-slugified | Physical dir recorded; `Path(record.cwd)` passed; `cc_project_dir(source_root)` used | ✅ |
| F-06 | Windows claim/skip grep | Claim plus unverified skip | README support line and Windows skip both matched | ✅ |
| F-07 | Module existence + active-doc grep | Gemini fixed; handoff claims stale | Modules absent; historical note present; two stale architecture rows matched | ✅ |
| F-08 | Invoke README fence regression parser | Empty error list | `[]` | ✅ |
| F-09 | Sanitized metadata/scan check | Identity fields and exemptions remain | `authors 1 name_field True email_field True scans_docs False explicit_exemption True` | ✅ |
| F-10 | Copier invocation/success grep | `--defaults` twice; parity absent | Lines 156/390 plus marker checks and `return "ok"` | ✅ |
| F-11 | Changelog/retirement grep | Intentional semantic loss declared | Changelog line 72 and retirement stub matched | ✅ |
| AD-1 | `git show 86c41d0` + current source | Immediate archive/removal behavior | Old module archived; runtime/hooks removed; fail-closed stub retained | ✅ |

**Verified: 12/12 statuses reproduce at `6cf82b5d020769c18d7021b60c04e84ccf407088`.**
This verifies the status classification; it does not convert the blocked pytest run or failing
Pyright gate into a pass.

### R2 Recommendations

**MUST be fixed before release:** F-01, F-03, F-04, F-05, F-06, F-07, F-09, F-10, and formal
reconciliation of AD-1 with the already-shipped option-(b) behavior.

**SHOULD be fixed before the next release gate:** none beyond the MUST list.

**Can be folded into a follow-up:** the existing Nice-to-have items below; none changes this
ship-readiness verdict.

## Round 3 — Security Hardening + Backlog Reconciliation

**Round 3 auditor:** Codex (`gpt-5.6-sol`, high effort), independent application-security audit

**Round 3 date:** 2026-09-01

**Round 3 scope:** Reconcile only the Round 2 MUST-fix backlog and AD-1, then conduct a fresh
public-release security pass over credential handling, command construction, filesystem trust
boundaries, insecure defaults, and release supply-chain controls at
`30521555df0b2695b9e69a90d7028cfc09d79c4c`. Source, tests, configuration, and prior audit rounds
were read-only; only this audit document was changed.

### R3 Summary

The stale backlog and the current security posture point in different directions. The code and
documentation fixes for F-04, F-06, and F-07 are present and correct; F-02, F-08, and F-11 were
already resolved in Round 2. F-01, F-03, F-05, F-09, and F-10 remain **PARTIAL**, however: the
separate reaper audit still fails its literal release-commit exit condition, the current full test
gate could not be reproduced, two behavior claims have present source/tests but no independently
executed E2E result, and the public-hygiene guard still encodes prohibited identifiers while
excluding whole documentation categories. A closed issue is not accepted as proof for any of
those items.

AD-1's implementation claim is **CONFIRMED**. The old inbound handlers, drain action, launch
markers/hooks, command registration, runtime module, and archived module are absent. The remaining
`signal-watch` strings in `process_hygiene.py:6,327-346,426-427,577-578` only identify legacy
processes for cleanup; they do not register or start a watcher. The handoff-removal test at
`tests/test_cli.py:125-134` expects Click's `No such command` failure. This is immediate removal,
option (b), not a compatibility archive.

The prompt's tracker snapshot was stale in one material respect: `.beads/issues.jsonl:106` still
records the retired handoff traversal issue as `status:"open"`, even though the vulnerable runtime
paths are absent. That tracking mismatch does not restore reachability, but it should be reconciled.

The seven supplied CodeQL locations all still match the named query patterns at the current HEAD,
but independent live GitHub state verification was unavailable: seven `gh api` requests returned
`error connecting to api.github.com`. Each alert therefore has a source-side disposition below,
while its live open/dismissed state is explicitly **UNVERIFIED**. The code review finds all seven
to be non-security uses or test-only matches. The fresh sweep, by contrast, found four CRITICAL and
four MAJOR issues outside that supplied list.

For completeness only, the prompt reports 46 Dependabot alerts all fixed and zero secret-scanning
alerts. Per instruction, those two surfaces were not re-audited, and their live GitHub state was not
independently asserted.

### R3.1 Backlog Reconciliation

This table re-derives the scope from the eight rows under the document's own **Must do before
release** section (`release-readiness-audit.md:622-633` before this round): F-01; the combined
F-03/F-04 gate; F-05; F-06; F-07; F-09; F-10; and AD-1.

| Item | Verdict | Current evidence and independent verification |
|------|---------|-----------------------------------------------|
| F-01 stale-session safety topology | **PARTIAL** | The tracked resolution cites `5b1aaf0`/`9bc126f9`. The shipped code now sets `remain-on-exit`, captures a dead-pane fingerprint, performs `if-shell -F` compare-and-kill, and checks a live supervisor before persisting a heartbeat (`main.py:1515-1528,3036`; `stale_session_reaper.py:141-183,367-383`). But the required separate audit still says `status: findings-pending-fix`, reports `2 P0 / 0 fixed`, and targets old commit `6668a46` (`stale-session-reaper-implementation-audit.md:5,20,104,181-219`). The literal exit condition, “Separate implementation audit passes the exact release commit,” is not met. |
| F-03 authoritative local gate | **PARTIAL** | The tracked resolution cites `8f9cacd` and 2,521 passing tests. At current HEAD, `ruff check --no-cache src tests scripts` returned `All checks passed!`; `ruff format --no-cache --check src tests scripts` returned `115 files already formatted`. Current `pytest` and Pyright could not be rerun: `python3 -m pytest` returned `No module named pytest`, and `pyright` returned `command not found`. The older issue's count is not evidence for later HEAD `3052155`. |
| F-04 public dev dependencies | **PASS** | The tracked resolution cites `f101371f`. `pyproject.toml:71-90` contains both `pytest-xdist>=3` and `pytest-timeout>=2.4.0` in the public dev extra/dependency group while `addopts` remains `-n auto`. `tomllib` produced `pytest-xdist= pytest-xdist>=3`, `pytest-timeout= pytest-timeout>=2.4.0`, `addopts= -n auto`. |
| F-05 audit/adopt source resolution | **PARTIAL** | The tracked resolution cites `6810e78`. The implementation passes the physical directory with `source_project_dir=record.project_dir` (`session_audit.py:358-376`), and the regression asserts that delegation (`test_session_audit.py:443-460`). The named root/worktree/physical-directory/legacy-slug E2E was not executable without pytest, so the source fix is confirmed but the stated E2E exit condition is not. |
| F-06 Windows contract | **PASS** | The tracked resolution cites `38e75f7`. The public claim is now explicitly experimental and says the real tmux lifecycle is unverified and skipped after a CI hang (`README.md:65-71`; `docs/tools/ai-cli-usage.md:54`). Narrowing the claim was one of the row's two accepted exit paths. |
| F-07 active documentation | **PASS** | The tracked resolution claims no stale active references. The active architecture's module/command maps at `docs/designs/architecture.md:43-79,83-112` no longer advertise handoff drain or signal-watch. `rg -n 'signal-watch|handoff-drain' docs/designs/architecture.md` returned no matches (exit 1), and runtime-symbol grep likewise returned no handler/drain matches. Historical/superseded documents were not misclassified as active behavior. |
| F-09 public-repository hygiene | **PARTIAL** | The tracked resolution cites `f101371f`. Author metadata is generic (`pyproject.toml:8-10`) and the guard now scans active docs (`test_public_repo_hygiene.py:1-16,56-80`). But it deliberately reconstructs the prohibited identifiers from fragments (`:18-45`) and excludes all `archive`, `audits`, `conversations`, `plans`, and `research` docs (`:16,71-72`). Sanitized verification printed `scans_docs=True`, `excludes_historical_docs=True`, `assembles_forbidden_tokens=True`; that is not full compliance with the repository's no-identifiers-in-any-doc-or-test rule. |
| F-10 Copier semantic parity | **PARTIAL** | The tracked resolution cites `25cddd6`. Both paths now call `_verify_update_parity`, preserve stored answers, and return the distinct fail-closed `parityfail` result (`copier_update.py:224-280,283-336,381-421,654-677`). The drifted-hunk and non-default-answer regressions are present (`test_copier_update.py:450-520`) but were not executable without pytest, so their required non-false-pass behavior was not independently run at this HEAD. |
| AD-1 handoff retirement | **PASS** | Commits `86c41d0` and `83d2578` removed ingress/hooks/lifecycle, then deleted the stub and archive. Current grep finds none of `_on_handoff_signal_watch`, `_write_pending_if_claimed_drain`, `handoff-drain`, `cmd_handoff`, or `handoff_pending`; neither `src/ai_cli/handoff.py` nor `archive/handoff.py` exists. Legacy cleanup recognition in `process_hygiene.py` is non-ingress compatibility hygiene. |
| Status Summary, navigation, and global outstanding list | **FAIL — fixed inline** | The Round 2 counts/verdict, ToC, and global MUST list were stale. They were updated to the Round 3 cross-round state in this document; **resolution: doc-only, this round**. |
| AD-1 pending label/record | **FAIL — fixed inline** | The heading and decision record contradicted the already-shipped option (b). The existing block is replaced below with the required resolved skeleton; **resolution: doc-only, this round**. |

### R3.2 Security Findings

Findings are ordered by severity. F-12 through F-18 retain the invocation order of the seven
supplied CodeQL alerts; they appear after the release blockers because independent triage found no
security boundary at those sites.

#### F-19: Remote sync content can traverse symlinks into local files — `CRITICAL`

**Classification:** **CONFIRMED** source-to-sink; a malicious or compromised sync Git remote is a
reachable external-input source.

**File:** `src/ai_cli/sync.py:2353-2373,995-1016,1025-1034,1097-1102,1120-1122`

**Exact evidence:**

> `for src in staging_project_dir.rglob("*"):`
>
> `if not src.is_file():`
>
> `shutil.copy2(src, dst)`

The staging tree is fetched and merged from `origin/main` immediately before this walk. Python's
`Path.is_file()` follows a symlink; the reproduced platform check returned
`is_symlink= True is_file= True` for a known symlink. Reads, `copy2`, `_write_jsonl_translated`, and
the conflict-path `src.write_text(...)` can consequently dereference a Git-controlled symlink.

**Why it matters:** A remote can place an allowed-name symlink (for example a JSONL or memory file)
that reads a victim-local file into synchronized state or causes a conflict-resolution write through
the link. That violates confidentiality/integrity at the local user's privileges and can feed the
dereferenced content into a later push.

**Verification command:**

```bash
nl -ba src/ai_cli/sync.py | sed -n '995,1036p;1117,1125p'
python3 -c "import pathlib; p=pathlib.Path('/etc/localtime'); print('is_symlink=',p.is_symlink(),'is_file=',p.is_file())"
```

**Recommended fix:** Before any staging read or write, reject a leaf or ancestor whose `lstat()` is
a symlink, require `src.resolve(strict=True).is_relative_to(staging_dir.resolve(strict=True))`, and
open the verified file with no-follow semantics (`openat`/`O_NOFOLLOW` on POSIX; the equivalent
reparse-point check on Windows) so validation and use cannot race. Apply the same helper to project,
task, history, memory, overwrite, and commit paths. Add relative- and absolute-symlink regressions for
JSONL, `MEMORY.md`, tasks, and conflict writes; each must fail closed without reading, writing, or
staging the target.

#### F-20: Predictable shared temporary file permits arbitrary clobber during setup — `CRITICAL`

**Classification:** **CONFIRMED** local symlink-clobber primitive on multi-user Unix hosts.

**File:** `setup.sh:168-198,219`

**Exact evidence:**

> `)" > /tmp/vision_expanded.md 2>/dev/null`
>
> `expanded = open('/tmp/vision_expanded.md').read().strip()`
>
> `rm -f /tmp/vision_expanded.md`

**Why it matters:** A local attacker can pre-create the predictable path as a symlink to any file
the victim can write. Shell redirection follows the link and truncates that target before the script
checks whether generated output is valid, producing reachable data loss under the victim account.

**Verification command:**

```bash
nl -ba setup.sh | sed -n '168,198p;216,221p'
```

**Recommended fix:** Allocate the file with `mktemp "${TMPDIR:-/tmp}/ai-cli-vision.XXXXXX"`, fail if
creation fails, immediately set mode `0600`, install `trap 'rm -f -- "$vision_tmp"' EXIT HUP INT TERM`,
and pass the quoted variable to both redirection and Python. Never reopen a fixed shared-directory
pathname.

#### F-21: Generated session shell interpolates untrusted paths and repository metadata — `CRITICAL`

**Classification:** **CONFIRMED** unsafe shell construction; exploit execution was not attempted.

**File:** `src/ai_cli/config.py:436-447`; `src/ai_cli/session_script.py:73-79,126-131,340-347,474-478`;
`src/ai_cli/main.py:2944-2955`

**Exact evidence:**

> `prefix = metadata.get("task_prefix")`
>
> `return prefix.strip(), str(metadata.get("project_type", "tool"))`
>
> `cd_cmd = f"cd {worktree_dir}" if worktree_dir else ":"`
>
> `project_prefix="{project_prefix}"`

**Why it matters:** A checkout path containing shell metacharacters, or repository-controlled
`[tool.ai-cli] task_prefix` containing a quote/substitution, is embedded into a script that the CLI
then runs. Launching a session from such a checkout can execute attacker-chosen shell code as the
user.

**Verification command:**

```bash
nl -ba src/ai_cli/config.py | sed -n '436,450p'
nl -ba src/ai_cli/session_script.py | sed -n '73,80p;126,132p;340,348p;474,480p'
```

**Recommended fix:** At the render boundary, apply `shlex.quote()` to every scalar that becomes
shell syntax (path, session/name, prefix, project, version/commit, UUID, commands), or preferably
stop embedding data and pass it as positional arguments/environment to a constant script. Enforce
one conservative prefix grammar at every source (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`). Add tests with
spaces, quotes, semicolons, newlines, `$()`, and backticks in both the checkout path and metadata;
execute the rendered script against stub binaries and assert no marker command runs.

#### F-22: Notification text is compiled as AppleScript source — `CRITICAL`

**Classification:** Interpolation is **CONFIRMED**; command execution is **PLAUSIBLE** because the
restricted runner could render the injected program but its `osascript` Standard Additions calls
failed before a live notification test. Even the baseline notification probe returned
`syntax error: A identifier can’t go after this identifier. (-2740)` in this environment.

**File:** `src/ai_cli/notifications.py:229-243`; `src/ai_cli/sync.py:1058-1105,1819-1840,2457-2459`

**Exact evidence:**

> `["osascript", "-e", f'display notification "{body}" with title "{title}"']`
>
> `f'display notification "{summary}" with title "ai sync: conflict detected" '`

The sync summary includes Git-controlled project/file names. Rendering a quote-bearing value
produced the following source, demonstrating that data becomes program text even though no shell is
passed to `subprocess.run`:

> `display notification "x" & (do shell script "printf APPLESCRIPT_INJECTION") & "" with title "t"`

**Why it matters:** On macOS, a malicious synchronized filename or any caller-controlled title/body
can break out of the string literal and add AppleScript commands, including `do shell script`, under
the user's account.

**Verification command:**

```bash
nl -ba src/ai_cli/notifications.py | sed -n '229,244p'
nl -ba src/ai_cli/sync.py | sed -n '1819,1840p'
python3 -c "body='x\" & (do shell script \"printf APPLESCRIPT_INJECTION\") & \"'; title='t'; print(f'display notification \"{body}\" with title \"{title}\"')"
```

**Recommended fix:** Use one constant AppleScript handler (`on run argv`) and pass title/body as
`osascript` arguments, reading them with `item 1/2 of argv`; never interpolate them into `-e` source.
Route sync conflict notifications through that same helper. Add quote, backslash, newline, Unicode,
and AppleScript-expression tests that assert the program source remains byte-for-byte constant and
all data appears only in argv.

#### F-23: Secret-bearing configuration is created with ambient world-readable permissions — `MAJOR`

**Classification:** **CONFIRMED** on POSIX defaults; Windows ACL behavior was not exercised.

**File:** `src/ai_cli/config.py:289-305`; `src/ai_cli/notifications.py:104-110,156-175`

**Exact evidence:**

> `# api_key = "ua-api-..."`
>
> `config_dir.mkdir(parents=True, exist_ok=True)`
>
> `config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")`
>
> `ntfy_token = os.environ.get("NTFY_TOKEN", "") or self._cfg.get("ntfy", {}).get("token", "")`

The current shell's reproduced umask is `022`; ordinary `mkdir`/`write_text` therefore create the
directory/file as approximately `0755`/`0644` unless a parent or prior file is stricter.

**Why it matters:** The documented config can contain API keys, webhook URLs, and bearer tokens.
On a shared host, ambient modes can expose them to other local users, and existing broad modes are
never detected or repaired.

**Verification command:**

```bash
umask
nl -ba src/ai_cli/config.py | sed -n '289,311p'
nl -ba src/ai_cli/notifications.py | sed -n '104,111p;156,175p'
```

**Recommended fix:** Create the config directory as `0700` and the file atomically as `0600`; on
every load, reject symlinks/non-regular files, verify ownership, and either repair group/other bits
to `0600` with a warning or refuse to load secrets. Preserve modes across every rewrite. On Windows,
create an owner-only ACL. Add tests under umask `022` for first creation, existing broad files,
symlinks, replacement races, and rewrites.

#### F-24: Credential-bearing clients accept plaintext and redirectable endpoint URLs — `MAJOR`

**Classification:** **CONFIRMED** for request construction; no credential was transmitted during
verification.

**File:** `src/ai_cli/cc_usage.py:263-275,303-315`; `src/ai_cli/notifications.py:104-110,186-226`

**Exact evidence:**

> `url = api_url.rstrip("/") + "/api/v1/usage/cc/ingest"`
>
> `"Authorization": f"Bearer {api_key}",`
>
> `req = urllib.request.Request(ntfy_url, data=body.encode(), headers=headers, method="POST")`

There is no parsed scheme/hostname validation before either request. A safe no-network probe built
the same request shape and returned `scheme=http authorization_header=True`.

**Why it matters:** A typo, stale configuration, or attacker-modified config can send API keys,
ntfy bearer tokens, or credential-bearing webhook URLs over cleartext HTTP; default redirect
handling also lacks an explicit same-origin credential policy.

**Verification command:**

```bash
python3 -c "from urllib.request import Request; from urllib.parse import urlsplit; u='http://example.invalid/api/v1/usage/cc/ingest'; r=Request(u,headers={'Authorization':'Bearer placeholder'},method='POST'); print('scheme='+urlsplit(r.full_url).scheme, 'authorization_header='+str(r.has_header('Authorization')))"
rg -n 'urlsplit|urlparse|Authorization|urlopen' src/ai_cli/cc_usage.py src/ai_cli/notifications.py
```

**Recommended fix:** Parse before request construction and require `https`, a non-empty hostname,
and no URL userinfo for every credential-bearing endpoint. Disable automatic redirects or implement
a handler that revalidates every hop and strips credentials unless scheme and origin are unchanged.
If tokenless self-hosted ntfy over HTTP must remain, permit it only with an explicit insecure config
opt-in and categorically reject sending a token on that path. Add HTTP, userinfo, Unicode hostname,
scheme-relative, redirect, and same-origin HTTPS tests.

#### F-25: Remediation text recommends executing mutable network scripts directly — `MAJOR`

**Classification:** **CONFIRMED** public supply-chain guidance.

**File:** `src/ai_cli/direnv_setup.py:72-82`; `README.md:43-51`

**Exact evidence:**

> `"curl -sfL https://direnv.net/install.sh | bash    # any Unix, no root",`
>
> `curl -LsSf https://astral.sh/uv/install.sh | sh`

**Why it matters:** Users following the CLI's own remediation or README execute whatever bytes the
remote endpoint serves at that moment without a version pin, digest/signature check, or inspection
step. TLS protects transport, not a compromised origin, mutable installer, or DNS/account takeover.

**Verification command:**

```bash
nl -ba src/ai_cli/direnv_setup.py | sed -n '72,86p'
nl -ba README.md | sed -n '43,57p'
```

**Recommended fix:** Prefer OS package-manager commands. Where a direct installer is unavoidable,
name one immutable release artifact, download it to a file, verify a repository-pinned SHA-256 or
publisher signature, then execute it as a separate explicit step. Remove every `curl | sh/bash`
variant from generated remediation and public installation instructions.

#### F-26: PyPI publication grants OIDC to mutable action tags — `MAJOR`

**Classification:** **CONFIRMED** release supply-chain weakness.

**File:** `.github/workflows/publish.yml:12-24,26-44`; `.github/workflows/ci.yml:14-15,35-43,59-63`

**Exact evidence:**

> `permissions:`
>
> `id-token: write`
>
> `- uses: astral-sh/setup-uv@v10.0.1`
>
> `- uses: pypa/gh-action-pypi-publish@release/v1`

**Why it matters:** The publish job runs third-party code selected by movable tags while holding a
PyPI trusted-publishing identity. If an upstream tag or action repository is compromised, the job
can build or publish attacker-controlled artifacts without any change to this repository.

**Verification command:**

```bash
nl -ba .github/workflows/publish.yml | sed -n '1,48p'
rg -n 'uses: .*@(v[0-9]|release/)' .github/workflows/*.yml
```

**Recommended fix:** Pin every action in publish and CI workflows to an audited full 40-character
commit SHA, retaining the human-readable release tag in a comment; enable Dependabot updates for
the `github-actions` ecosystem. Keep `id-token: write` only on the single publish step/job, build
once, verify the wheel/sdist contents, and publish that exact immutable artifact.

#### F-12: CodeQL #17 weak-sensitive-data-hashing is non-security color partitioning — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** source match; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/iterm2.py:217-233`

**Exact evidence:**

> `_fallback_idx = int(_hashlib.md5(ai_name.encode()).hexdigest(), 16) % len(palette)`

**Why it matters:** The digest selects a deterministic UI color only after all color slots are
occupied. No password, credential, signature, integrity decision, or attacker-controlled security
boundary depends on collision resistance; treating it as a vulnerability dilutes alert triage.

**Verification command:**

```bash
nl -ba src/ai_cli/iterm2.py | sed -n '217,233p'
```

**Recommended fix:** Dismiss CodeQL #17 as `false positive / non-cryptographic deterministic
partitioning`, citing these lines. If a zero-alert policy forbids dismissal, replace MD5 with
`sha256(ai_name.encode()).digest()` and derive the integer from the digest; document that the hash
remains non-security UI behavior.

#### F-13: CodeQL #16 URL-substring alert is a test assertion — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** test-only match; live alert state **UNVERIFIED**.

**File:** `tests/test_direnv_setup.py:394-409`

**Exact evidence:**

> `assert "git-scm.com" in text`

**Why it matters:** This assertion checks that Windows remediation prose names an installation
site. It neither accepts a URL nor sanitizes/authorizes a hostname, so the URL-security query has no
production data flow here.

**Verification command:**

```bash
nl -ba tests/test_direnv_setup.py | sed -n '394,409p'
```

**Recommended fix:** Dismiss CodeQL #16 as `used in tests / no sanitization boundary`; do not weaken
the regression merely to silence the analyzer.

#### F-14: CodeQL #15 reports intentional workspace-path stdout, not credential logging — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** source match; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/main.py:4326-4341`

**Exact evidence:**

> `print(f"  + {key}")`

**Why it matters:** `key` is a workspace key returned by an explicitly invoked trust-backfill
command and is printed as the command's direct result. It is not a secret or unattended log sink;
the user requested the path enumeration.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '4326,4341p'
```

**Recommended fix:** Dismiss CodeQL #15 as `false positive / intentional CLI result containing no
credential`. If path-minimizing output is desired as a privacy feature, print the count by default
and add paired `-v/--verbose` output for full paths, but that is not a release-blocking security fix.

#### F-15: CodeQL #14 mistakes one payload-classification operand for URL validation — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** first distinct operand; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/notifications.py:186-199`

**Exact evidence:**

> `is_discord = "discord.com" in webhook_url or "discordapp.com" in webhook_url`

For #14, the independently identified instance is the first operand, `"discord.com" in
webhook_url`.

**Why it matters:** The boolean only chooses Discord's `{"content": ...}` versus Slack's
`{"text": ...}` payload. It does not authorize the destination; arbitrary configured webhook
hosts are supported. A deceptive hostname can select the wrong JSON shape, a correctness issue,
but cannot bypass a host allowlist because none exists.

**Verification command:**

```bash
nl -ba src/ai_cli/notifications.py | sed -n '184,199p'
```

**Recommended fix:** Parse once with `urlsplit`; classify Discord only when the lowercase hostname
is exactly `discord.com` or ends in `.discord.com` (and likewise for the second domain below). Add
tests for userinfo, suffix-confusion, uppercase, ports, and subdomains, then close #14 as fixed.

#### F-16: CodeQL #13 is the second payload-classification operand on the same line — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** second distinct operand; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/notifications.py:186-199`

**Exact evidence:**

> `is_discord = "discord.com" in webhook_url or "discordapp.com" in webhook_url`

For #13, the independently identified instance is the second operand, `"discordapp.com" in
webhook_url`; it is not a duplicate of #14's first substring expression.

**Why it matters:** As with #14, the value controls payload schema only, not permission to contact a
host. The current expression can misclassify a suffix-confusion URL, but no security allowlist is
bypassed.

**Verification command:**

```bash
nl -ba src/ai_cli/notifications.py | sed -n '184,199p'
```

**Recommended fix:** In the same `urlsplit` change as F-15, accept this branch only when hostname is
exactly `discordapp.com` or ends in `.discordapp.com`; add the same boundary tests and close #13.

#### F-17: CodeQL #8 reports an explicit non-secret GCP project identifier — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** source match; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/spend.py:299-309`

**Exact evidence:**

> `print(f"    GCP project: {gcp_project}")`

**Why it matters:** A project ID is an identifier, not an authentication credential, and this is
direct output from an explicitly invoked local spend report. It can be privacy-sensitive in shared
terminal transcripts, but the sink is not a background log.

**Verification command:**

```bash
nl -ba src/ai_cli/spend.py | sed -n '299,309p'
```

**Recommended fix:** Dismiss CodeQL #8 as `false positive / non-secret explicit CLI output`. If the
product wants identifier minimization, show it only under a paired `-v/--verbose` option; do not
classify that optional UX change as credential remediation.

#### F-18: CodeQL #7 mistakes an OAuth usage count for OAuth credential material — `NOT A SECURITY FINDING`

**Classification:** **CONFIRMED** source match; live alert state **UNVERIFIED**.

**File:** `src/ai_cli/spend.py:283-292`

**Exact evidence:**

> `dr_monthly = f"{m_oauth_dr} OAuth"`
>
> `print(f"  Deep Research:  {dr_monthly}")`

**Why it matters:** `m_oauth_dr` is an integer count of runs, not a token, authorization header,
client secret, or session identifier. The alert is driven by the label “OAuth,” not sensitive data
flow.

**Verification command:**

```bash
nl -ba src/ai_cli/spend.py | sed -n '283,292p'
```

**Recommended fix:** Dismiss CodeQL #7 as `false positive / aggregate count, no credential data`.

### R3 Verification Matrix

The following ten checks were rerun during finalization; outputs are reproduced, not inferred from
issue close reasons.

| Finding/claim | Re-run command | Actual output | Verified? |
|---------------|----------------|---------------|-----------|
| F-19 symlink predicate | `python3 -c '<Path(/etc/localtime) checks>'` | `is_symlink= True is_file= True` | ✅ |
| F-20 fixed temp pathname | `nl -ba setup.sh \| sed -n '168,198p;216,221p'` | Redirection/read/removal all name `/tmp/vision_expanded.md` | ✅ |
| F-21 generated-shell interpolation | `nl -ba config.py/session_script.py` at cited ranges | Unvalidated metadata return; raw `cd {worktree_dir}` and quoted-but-unescaped prefix | ✅ |
| F-22 AppleScript source construction | Python render probe from F-22 | `display notification "x" & (do shell script "printf APPLESCRIPT_INJECTION") & "" with title "t"` | ✅ |
| F-23 ambient file modes | `umask` plus cited config write | `022`; write uses no explicit mode | ✅ |
| F-24 plaintext credential request | Safe `urllib.request.Request` construction, no I/O | `scheme=http authorization_header=True` | ✅ |
| F-25 pipe-to-shell guidance | `nl -ba direnv_setup.py README.md` | Both mutable installer pipelines reproduced | ✅ |
| F-26 mutable release actions | `rg -n 'uses: .*@(v[0-9]\|release/)' .github/workflows/*.yml` | Mutable tags in publish and CI; publish job has `id-token: write` | ✅ |
| F-12 CodeQL weak hash context | `nl -ba iterm2.py \| sed -n '217,233p'` | MD5 result used only modulo palette length | ✅ |
| F-15/F-16 distinct same-line instances | `nl -ba notifications.py \| sed -n '184,199p'` | First `discord.com` and second `discordapp.com` operands both reproduced | ✅ |

**Verified: 10/10 matrix rows reproduce at
`30521555df0b2695b9e69a90d7028cfc09d79c4c`.** This matrix does not claim a green full test suite
or live GitHub alert status.

### R3 Recommendations

**MUST be fixed before the v0.8.0 PyPI publish:**

1. Fix F-19 through F-22 before any release candidate: reject sync symlinks safely, remove the
   predictable temp path, eliminate shell-template interpolation, and pass notification text as
   AppleScript argv rather than source.
2. Fix F-23 through F-26: enforce owner-only secret storage, HTTPS/same-origin credential transport,
   remove pipe-to-shell guidance, and SHA-pin all publication actions.
3. Complete F-01's exact-HEAD independent reaper audit and run the F-03 public-dev gate (Ruff,
   Pyright, and full pytest) with actual counts in a writable test environment.
4. Execute the named F-05 and F-10 regressions/E2E at the release HEAD and close the remaining F-09
   public-hygiene gaps rather than treating issue closure as proof.

**SHOULD be completed before publish:** Resolve or dismiss all seven GitHub CodeQL alerts with the
specific source-side rationales above; implement the hostname-boundary correctness improvement for
CodeQL #13 and #14; reconcile the still-open retired-handoff tracker record; and attach the final build hash and
artifact-inspection evidence to the release record.

**Can defer without changing the security verdict:** Optional path/project-ID redaction for explicit
CLI output (F-14/F-17), changing the non-security palette hash (F-12), and any code change for the
test-only F-13 alert. Their alert dispositions should still be recorded so the public dashboard is
not misleading.

**v0.8.0 ship-readiness:** **NO-GO.** Backlog progress does not offset the four confirmed/reachable
CRITICAL paths and four MAJOR hardening gaps in the current release tree.

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Handoff retirement strategy — `✅ Resolved by Codex gpt-5.6-sol/high — (b)`

**Context:** Current code contains no handoff ingress, launch hook, watcher lifecycle, command, live
module, or archive; commits `86c41d0` and `83d2578` therefore implement immediate removal. Legacy
`signal-watch` recognition remains only to identify old processes for hygiene cleanup.

#### (a) Staged deactivation plus one-release compatibility archive

**Pros:**

- Removes automatic ingress before release.
- Gives users safe export/migration time.
- Makes dropped semantics explicit while retaining history.

**Cons:**

- Requires wrappers/deprecation tests.
- Any callable path must be secured during the window.
- Delays complete removal.

#### (b) Immediate removal

**Pros:**

- Smallest surface and fastest vulnerability elimination.
- No maintenance ambiguity.

**Cons:**

- May strand queue entries/external scripts.
- No deprecation window.
- Repository grep cannot prove no external users.

#### Recommendation

> **Recommended (AI):** Confirm **(b) immediate removal** because it is the shipped state and leaves no reachable vulnerable compatibility path. Mitigate stranded local queue entries by documenting a manual read/export route from preserved local files and Git history; mitigate the missing deprecation window with an explicit v0.8.0 breaking-change notice and the hard `No such command` failure; mitigate unprovable external callers by naming the removal and rollback/migration route in release notes rather than claiming repository grep proves absence.
> **Decision:** `(b)`
<!-- decision-record: chosen-option=(b); ai-family=codex; ai-model=gpt-5.6-sol; ai-effort=high; ai-profile=audit -->

## Outstanding Issues to Fix

### Must do before release

| Order | Action | Exit condition |
|-------|--------|----------------|
| 1 | Complete F-01's independent release gate | Separate stale-session implementation audit passes the exact release commit. |
| 2 | Complete F-03's authoritative local gate | Ruff, Pyright, and full pytest pass from the public dev install with actual counts attached. |
| 3 | Execute F-05 and F-10 at release HEAD | Root/worktree/physical-directory/legacy-slug E2E and drifted-hunk/non-default-answer fixtures pass without false success. |
| 4 | Finish F-09 public-repository hygiene | The guard contains no prohibited identifiers and covers every public documentation category without exemptions that contradict repository policy. |
| 5 | Fix F-19 through F-22 | Symlink-safe sync, safe temporary creation, constant-data shell generation, and argv-only AppleScript handling pass adversarial regressions. |
| 6 | Fix F-23 through F-26 | Owner-only secret storage, HTTPS/same-origin credential transport, verified installers, and immutable action SHAs are enforced and tested. |
| — | AD-1 | **Resolved in Round 3; not outstanding:** option (b), immediate removal; doc-only, this round. |

### Nice to have before release

1. Apply `_git_env()` consistently (AI-CLI-lb9).
2. Resolve relative copier sources before isolation (AI-CLI-84h).
3. Make procfs destructive pruning errno-aware (AI-CLI-bjs7).
4. Correct deterministic stale doc references (AI-CLI-6tq).
5. Human RC smoke: local tmux, bare, reconnect, sync conflict, quota/statusline.

## Already-Correct Items

- ✅ Version `0.8.0` aligns (`pyproject.toml:3`; `src/ai_cli/__init__.py:3`).
- ✅ Update stamp is post-success (`main.py:835-880`).
- ✅ Editable installs are preserved (`main.py:1783-1813,2037-2047`).
- ✅ Quiet install uses fingerprints (`main.py:792-815`).
- ✅ LF policy and current ruff `0.16.5` pins agree.
- ✅ Icon script passes pinned ruff.
- ✅ Stopped processes require identity verification before reclamation (`main.py:341-447`).
- ✅ Inspected skips have documented premises; no xfail/removed-module test import found.
- ✅ Round 1 verified substantive handoff implementation/tests/docs before retirement; retirement
  is not based on claiming the feature never existed.
- ✅ Shared messaging has non-handoff consumers and must remain supported.
- ✅ F-02 — vulnerable handoff ingress and automatic launch/watcher paths are absent; the retired
  command fails closed (`src/ai_cli/main.py:3051-3063`).
- ✅ F-08 — README fence regression parser returns no errors (`tests/test_readme_fences.py`).
- ✅ F-11 — release history explicitly declares the intentional durable/offline semantic loss
  (`CHANGELOG.md:69-73`).

## Anti-Patterns to Watch For

- Trusting issue titles over current code/history.
- Treating repository failure-to-find as absence of external callers.
- Calling a CLI-only feature opt-in despite launch/supervisor call sites.
- Equating synchronous messaging with durable queue parity.
- Archiving vulnerable reachable code and calling the bug moot.
- Reporting collection/old notes as current full-suite totals.
- Treating exit-zero/no markers as content parity.
- Advertising platform support without hands-on attestation.
- Repeating prohibited identity values while reporting hygiene.

## Sign-Off Checklist

- [ ] All P0 fixes independently verified.
- [ ] All P1 fixed or public feature/support claim removed with rationale.
- [x] P2/P3 resolved or recommendations recorded (F-11 resolved by explicit retirement disclosure).
- [x] AD-1 decision recorded and reconciled with the already-shipped option-(b) behavior.
- [x] Verification Matrix 10/10 reproduced.
- [x] Round 2 re-verification completed; 12/12 current statuses reproduced.
- [x] Round 3 security/backlog verification completed; 10/10 matrix checks reproduced.
- [ ] Final re-grep and unrestricted full test run complete.
- [x] No inline implementation fixes.
- [x] Already-Correct and Anti-Patterns populated.
- [ ] Maintainer approves sign-off.

<!-- /doc:region name="round_1_findings" -->

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-08-28 | Round 1 complete | 80 classified; 11 findings; 10/10 matrix; no implementation edits. |
| 2026-08-29 | Round 2 current-tree re-verification complete | 3 resolved, 1 reframed-open, 1 partial-open, 6 still open; AD-1 pending with shipped behavior matching option (b); 12/12 status matrix. |
| 2026-09-01 | Round 3 security hardening + backlog reconciliation complete | Current HEAD `3052155`; old MUST backlog: 3 PASS, 5 PARTIAL, AD-1 resolved as (b); 4 CRITICAL + 4 MAJOR fresh blockers; 7 CodeQL source matches independently triaged as non-findings, live alert state unavailable; 10/10 verification matrix; doc-only updates to this audit. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Primary/requirements:** target scaffold; canonical harness `STUB.md`/`TEMPLATE.md`; `AGENTS.md`; `CLAUDE.md`; `README.md`; `pyproject.toml`; `.pre-commit-config.yaml`; `.gitattributes`; `CHANGELOG.md`.

**Handoff:** `handoff.py`, `messaging.py`, `process_manager.py`, `session_script.py`, relevant `main.py`/`config.py`; four handoff/messaging tests; handoff design, reliability, signaling, research, queue README, NATS guide.

**Session/process:** `session.py`, `session_audit.py`, `session_adopt.py`, `cc_migrate.py`, `process_probe.py`, `process_hygiene.py`, `stale_session_reaper.py`, `direnv_setup.py`; their dedicated tests plus stopped/bare/runaway tests.

**Features/docs:** top-level module inventory; detailed `sync`, `quota`, `cc_usage`, `transport`, `tunnel`, `notifications`, `layout`, `iterm2`, `icon_generator`, `copier_update`, `telemetry`, `trust`, `workspace`, `git_repair`; architecture/statusline/usage/docket docs; dedicated feature tests. Targeted issue mirror records were read; no task-store command ran.

**Round 2 current-tree verification:** full current audit doc; canonical harness audit STUB/TEMPLATE;
`docs/audits/README.md`; `docs/audits/stale-session-reaper-implementation-audit.md`; tracked
AI-CLI-fae/AI-CLI-pt9n records and interactions; PR/commit diffs for `86c41d0`, `66c6456`,
`7bfa7a5`, `f3e181d`, `f41eb4d`, `8894668`, and `cdbcbf1`; current `main.py`,
`session_script.py`, `process_manager.py`, `session_audit.py`, `session_adopt.py`,
`copier_update.py`, `pyproject.toml`, `README.md`, `CHANGELOG.md`, architecture/usage/NATS/CI-retirement
docs, and the cited regression tests.

**Round 3 security/reconciliation:** full audit doc plus canonical audit and decision procedures;
current tracked issue JSONL and shipped commit diffs; reaper implementation/audit/tests; handoff
runtime, legacy cleanup, and removal tests; `config.py`, `cc_usage.py`, `notifications.py`, `sync.py`,
`session_script.py`, `main.py`, `spend.py`, `iterm2.py`, `direnv_setup.py`, `setup.sh`; public hygiene,
adoption, Copier, and dependency tests; README/architecture/usage docs; PyPI publish and CI workflows.

## Appendix: Commands Run

```bash
git status --short --branch
git rev-parse HEAD
git describe --tags --always
git log --oneline --all -- <feature-file>
rg --files src/ai_cli tests docs
rg -n 'handoff' README.md docs src tests
rg -n '@pytest.mark\.(skip|skipif)|pytest.skip|xfail|importorskip' tests src
wc -l src/ai_cli/*.py tests/test_*.py
uv run pytest --collect-only -q 2>&1 | tail -30
uv run pytest -q 2>&1 | tail -60
PYTHONDONTWRITEBYTECODE=1 <existing-venv>/bin/python -c '<direct collection, no cache/xdist>'
PYTHONDONTWRITEBYTECODE=1 <existing-venv>/bin/python -c '<focused process-probe test, no conftest>'
python3 -c '<sanitized tomllib/module/CRLF/fence checks>'
<existing-venv>/bin/ruff check --no-cache scripts/generate_iterm2_icons.py
# Round 2
bd show AI-CLI-fae
bd show AI-CLI-pt9n
bd show AI-CLI-tdm6.1
git show --stat --oneline <relevant-commit>
rg -n '<finding-specific symbols>' <cited current files>
python3 -c '<sanitized TOML metadata/dev-extra checks>'
<existing-venv>/bin/ruff check --no-cache src tests
<existing-venv>/bin/ruff format --no-cache --check src tests
<existing-venv>/bin/pyright --venvpath <repository-root>
PYTHONDONTWRITEBYTECODE=1 <existing-venv>/bin/pytest -p no:cacheprovider -q <focused tests>
# Round 3
git show --stat --oneline 83d2578 86c41d0 5b1aaf0 9bc126f9 f101371f 6810e78 25cddd6
rg -n '<handoff ingress/hook/watcher symbols>' src tests docs
ruff check --no-cache src tests scripts
ruff format --no-cache --check src tests scripts
ruff check --no-cache --select S src tests scripts
python3 -m pytest --version
pyright --version
python3 -c '<sanitized dev-extra, hygiene, URL-request, and symlink checks>'
nl -ba <security finding files> | sed -n '<cited ranges>'
gh api 'repos/<owner>/<repo>/code-scanning/alerts/<number>'
markdownlint docs/audits/release-readiness-audit.md
aido validate-doc docs/audits/release-readiness-audit.md
```

The Round 1 exact uv commands failed before pytest while creating the uv cache. Round 2's focused
pytest attempt also failed before collection because no writable temporary directory exists under
the single-file write policy. The three `bd show` commands could not open the embedded database
because it must create/open a lock file; tracked JSONL was read as the repository fallback. Ruff
completed successfully. Pyright was rerun with the existing environment explicitly selected and
reported 81 errors. In Round 3, Ruff lint and format passed, while pytest and Pyright were absent
from the constrained environment. The ad hoc Ruff security selector returned `Found 4751 errors`
(mostly generic subprocess/test-assert checks); relevant source-to-sink results were manually traced
rather than counted as findings. All seven live CodeQL API attempts failed at network connection;
current source locations were still independently reproduced. Markdownlint and canonical document
validation passed after the Round 3 write.

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** gpt-5.6-sol

**Date:** 2026-08-28

```text
Independently audit release readiness against current code/history. Classify all embedded issues;
assess handoff retirement and semantic parity; structurally spot-check features; run requested test
commands and report only actual results. Write only this audit, use canonical structure, cite lines,
distinguish CONFIRMED from PLAUSIBLE, run a verification matrix, and expose no private identifiers.
```

### Round 3 Reviewer Prompt

**Model:** Codex (`cx audit --write-target`, effort: xhigh)

**Date:** 2026-09-01

```text
Two-part round: (A) reconcile the doc's stale "Not ready" Status Summary against what Beads shows
already shipped since Round 2 (F-01..F-11, handoff retirement) -- re-verify each claim against
current code/tests rather than trusting the close-reason, then resolve AD-1 (handoff retirement
strategy) via /decision-framework per the shipped reality. (B) fresh security-hardening audit
dimension for the public PyPI/GitHub release: verify and produce F-N findings for 7 known-open
GitHub CodeQL alerts (weak hashing in iterm2.py:221; incomplete URL substring sanitization in
test_direnv_setup.py:401 and notifications.py:188 x2; clear-text logging of sensitive data in
main.py:4339, spend.py:307, spend.py:292), plus a genuine fresh sweep for credential/secret
logging, command injection, path traversal, insecure defaults, and supply-chain issues beyond
Dependabot's already-clean 46/46-fixed alerts. Full prompt archived at
/tmp/aicli-fae-r3-security-audit-prompt.txt (session e949692e) and in the cx launch command
recorded in this round's Audit Log entry.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->
