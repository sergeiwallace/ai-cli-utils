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

**Target artifact:** working tree at `c9841e3eb6bfc5f0043c144655a8e77cc3bcb6c7`; package version `0.8.0`

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

## What Was Audited

This audit surveyed the unreleased `0.8.0` tree, 550 commits after tag `v0.7.0`, for release blockers, quick wins, stale backlog, structural feature health, handoff retirement, and test health. Every QUICK WIN and RELEASE-BLOCKING classification was checked against current source, tests, or git history rather than accepted from an issue title.

The snapshot differs materially from older issue descriptions: update-stamp ordering, editable-install preservation, quiet install, stopped-session handling, line endings, and icon lint are already fixed. Conversely, handoff is automatically started and drained during ordinary Claude-session launch, not merely invoked manually.

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

**Approach:** Round 1 cross-referenced every supplied record with code/history, traced handoff call sites, mapped major modules to tests and recent commits, attempted the exact requested test commands, and reproduced safe checks under the write-restricted worker. **CONFIRMED** means reproduced from artifacts; **PLAUSIBLE** marks environment-dependent judgment. The canonical audit scaffold/template was read first. No implementation fix was applied.

## Status Summary

**Latest round:** Round 1

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 1 | 0 | 0 |
| P1 | 9 | 0 | 0 |
| P2 | 1 | 0 | 1 |
| P3 | 0 | 0 | 0 |
| **Total** | **11** | **0** | **1** |

**Ship-readiness verdict:** **Not ready.** Close the in-flight P0, remove or fix reachable handoff traversal, make tests executable and green, repair adoption, resolve Windows support claims, make copier fail closed, and correct public docs/hygiene before publication.

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

**Location:** `src/ai_cli/session_script.py:516-529`; `docs/audits/stale-session-reaper-implementation-audit.md`

**Evidence:** launch executes automatic cleanup then starts watchers; the supplied backlog identifies a separately audited P0. **Verification:** `git log -1 --oneline --` those paths. **Impact:** ordinary sessions execute process/destructive authority. **Recommendation:** require the separate audit to pass on the exact release commit.

#### F-02: Inbound handoff payloads can escape the queue — `P1`

**Location:** `src/ai_cli/main.py:1453-1464,1505-1517`

**Evidence:** both handlers use `pending_dir / filename` and `write_text(content)` from payload data without containment/schema/size/symlink checks. **Verification:** `rg -n 'local_file = pending_dir / filename|local_file.write_text\(content' src/ai_cli/main.py`. **Impact:** a publisher can write outside the queue; launch activates ingress automatically. **Recommendation:** disable ingress under AD-1, or fully validate and atomically create with adversarial tests.

#### F-03: Test gate is red and complete result unverified — `P1`

**Location:** `tests/test_process_probe.py:373-389`; `tests/test_session.py:1176-1203`; `tests/conftest.py:385-390`

**Evidence:** focused run returns `1 failed`; direnv test mocks an obsolete command versus `direnv_setup.py:150-171`; full totals unavailable. **Verification:** exact full commands plus named focused tests in a writable checkout. **Impact:** no healthy release-suite evidence. **Recommendation:** fix/deduplicate both failures and require zero failures on supported platforms with counts attached.

#### F-04: Published dev extra cannot run configured pytest — `P1`

**Location:** `pyproject.toml:71-89,97-110`

**Evidence:** `.[dev]` lacks xdist/timeout while addopts is `-n auto`. **Verification:** sanitized `tomllib` command in matrix. **Impact:** contributor workflow breaks before tests run. **Recommendation:** one canonical dependency set and clean CI installing only `.[dev]`.

#### F-05: Audit and adoption disagree on transcript source — `P1`

**Location:** `session_audit.py:277-303,348-378`; `session_adopt.py:712-763`

**Evidence:** audit knows physical `project_dir`; batch passes cwd; adopter slugifies it and may report no transcript. **Verification:** `rg -n 'project_dir=project_dir|source_root=Path\(record.cwd\)|source_dir = cc_project_dir'` in both modules. **Impact:** an “adoptable” session is rejected. **Recommendation:** pass physical path/transcript UUID explicitly and test root/worktree/legacy slugs.

#### F-06: Windows support lacks release attestation — `P1`

**Location:** `README.md:67-80`; `main.py:516-574`; `test_session_launch_integration.py:55-85`

**Evidence:** support is claimed, bare launch is relevant, issues/E2E remain, real tmux is skipped. Behavior is **PLAUSIBLE**; missing attestation **CONFIRMED**. **Verification:** `rg -n 'Windows|bare.*mode|real_tmux'` in cited files. **Recommendation:** pass keyboard/interrupt/stale-dir E2E or mark Windows experimental with limitations.

#### F-07: Active docs advertise removed modules/commands — `P1`

**Location:** `docs/designs/architecture.md:35-39,43-74,87-89`; `docs/tools/ai-cli-usage.md:306-378`

**Evidence:** two listed modules do not exist and a removed wrapper remains documented. **Verification:** module existence check in matrix. **Impact:** public users receive invalid commands/architecture. **Recommendation:** regenerate from Click registrations, archive obsolete sections, scrub private examples, and smoke-test documented `--help`.

#### F-08: README fences break rendered instructions — `P1`

**Location:** `README.md:119-180,198-222`

**Evidence:** language-tagged fences appear while another fence is open; parser found 25 and ended unclosed. **Verification:** fence parser in commands/matrix. **Impact:** installation/usage prose renders as code. **Recommendation:** use bare closing fences, render-check, and replace placeholder badge paths at `README.md:5-8` with canonical public links.

#### F-09: Public identity rules remain violated — `P1`

**Location:** `pyproject.toml:8-10`; `tests/test_public_repo_hygiene.py:46-63,110-125`

**Evidence:** personal metadata exists and tests exempt it; values intentionally omitted. **Verification:** sanitized metadata-presence check. **Impact:** publication makes prohibited identifiers durable. **Recommendation:** generic/omitted author metadata, remove exemptions, scan root metadata/active docs.

#### F-10: Copier can report success after semantic loss — `P1`

**Location:** `src/ai_cli/copier_update.py:141-203,353-375`

**Evidence:** both paths force `--defaults`; success proves only exit/no markers/commit, not answers or intended hunks. **Verification:** `rg -n '\[copier_bin, "update"|--defaults|_conflict_files|return "ok"'`. **Impact:** incomplete changes can be committed/pushed as success. **Recommendation:** preserve answers, resolve source before isolation, and fail closed on drifted-anchor fixtures.

#### F-11: Live messaging is not durable-queue parity — `P2`

**Location:** `handoff.py:28-82,90-223`; `messaging.py:205-220,263-292`; `handoff-reliability-testing.md:31-42,97-183`

**Evidence:** handoff persists lifecycle files/messages; scope-supplied native messaging targets live sessions; stronger queue layers are unfinished. **Verification:** `rg -n 'pending|claimed|completed|subscribe_durable|dead.letter|lease|reconcile'` in cited files. **Impact:** retirement silently loses offline/durable semantics unless declared. **Recommendation:** AD-1(a); do not build queue v2 solely for parity no longer wanted.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| F-01 | OPEN | Separate verification required. |
| F-02 | TEAM INPUT NEEDED | Disable ingress under AD-1 or secure all reachable paths. |
| F-03 | OPEN | Fix tests and obtain unrestricted totals. |
| F-04 | OPEN | Align public dev extra. |
| F-05 | OPEN | Correct source-directory contract. |
| F-06 | TEAM INPUT NEEDED | Pass Windows UAT or narrow claim. |
| F-07 | OPEN | Reconcile docs/CLI/modules. |
| F-08 | OPEN | Repair/render-check README. |
| F-09 | OPEN | Remove identifiers/exemptions. |
| F-10 | OPEN | Make copier fail closed. |
| F-11 | TEAM INPUT NEEDED | Decide intentional loss in AD-1. |

No implementation fixes were applied.

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

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Handoff retirement strategy — `[PENDING]`

**Context:** Handoff is active in normal launch, contains reachable traversal, and offers partial durable/offline semantics absent from scope-supplied native live messaging. Moving code while keeping commands “working” leaves security/launch ambiguity.

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

#### (c) Retain and complete queue v2

**Pros:**

- Preserves offline/durable/cross-machine semantics.
- Could add leases/reconciliation/dead letters.

**Cons:**

- Conflicts with stated product direction.
- Greatly expands release scope and operational ownership.
- Still requires immediate security remediation.

#### Recommendation

> **Recommended (AI):** Choose **(a)**. Disable automatic/NATS ingress before release, secure any compatibility path, preserve read-only local migration for one cycle, then archive implementation/history. Close AI-CLI-70q, AI-CLI-3b3, and AI-CLI-0ay only after deactivation. Close AI-CLI-2qu without a fix only if every vulnerable write path is demonstrably unreachable; otherwise fix it first.
> **Decision:** `PENDING`
<!-- decision-record: chosen-option=PENDING; ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

## Outstanding Issues to Fix

### Must do before release

| Order | Action | Exit condition |
|-------|--------|----------------|
| 1 | Finish P0 topology | Separate audit passes exact release commit. |
| 2 | Decide AD-1; remove/secure ingress | No unvalidated network write; no retired launch hook. |
| 3 | Fix tests/dev dependencies | Exact full run in clean writable environment; actual counts recorded, zero failures. |
| 4 | Fix audit/adopt resolution | Root/worktree/legacy-slug E2E pass. |
| 5 | Resolve Windows contract | Supported E2E passes or claims narrowed. |
| 6 | Make copier fail closed/remove support | Drifted-hunk/non-default-answer fixtures cannot false-pass. |
| 7 | Repair docs/README/hygiene | Render/doc/hygiene checks pass; documented commands exist. |

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
- ✅ LF policy and ruff `0.16.4` pins agree.
- ✅ Icon script passes pinned ruff.
- ✅ Stopped processes require identity verification before reclamation (`main.py:341-447`).
- ✅ Inspected skips have documented premises; no xfail/removed-module test import found.
- ✅ Handoff has real tests/docs; retirement is not based on claiming no implementation.
- ✅ Shared messaging has non-handoff consumers and must remain supported.

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
- [x] P2/P3 recommendations recorded.
- [ ] AD-1 approved/implemented.
- [x] Verification Matrix 10/10 reproduced.
- [ ] Verification round completed after fixes.
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

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Primary/requirements:** target scaffold; canonical harness `STUB.md`/`TEMPLATE.md`; `AGENTS.md`; `CLAUDE.md`; `README.md`; `pyproject.toml`; `.pre-commit-config.yaml`; `.gitattributes`; `CHANGELOG.md`.

**Handoff:** `handoff.py`, `messaging.py`, `process_manager.py`, `session_script.py`, relevant `main.py`/`config.py`; four handoff/messaging tests; handoff design, reliability, signaling, research, queue README, NATS guide.

**Session/process:** `session.py`, `session_audit.py`, `session_adopt.py`, `cc_migrate.py`, `process_probe.py`, `process_hygiene.py`, `stale_session_reaper.py`, `direnv_setup.py`; their dedicated tests plus stopped/bare/runaway tests.

**Features/docs:** top-level module inventory; detailed `sync`, `quota`, `cc_usage`, `transport`, `tunnel`, `notifications`, `layout`, `iterm2`, `icon_generator`, `copier_update`, `telemetry`, `trust`, `workspace`, `git_repair`; architecture/statusline/usage/docket docs; dedicated feature tests. Targeted issue mirror records were read; no task-store command ran.

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
```

The exact uv commands failed before pytest while creating the uv cache. Direct full pytest could not create the autouse temp directory because this audit file was the worker's sole writable path.

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

<!-- /doc:region name="appendix_reviewer_prompt" -->
