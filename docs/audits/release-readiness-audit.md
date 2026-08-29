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
Round 2 re-verification at `6cf82b5d020769c18d7021b60c04e84ccf407088`; package version `0.8.0`

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

**Approach:** Round 1 cross-referenced every supplied record with code/history, traced handoff call sites, mapped major modules to tests and recent commits, attempted the exact requested test commands, and reproduced safe checks under the write-restricted worker. Round 2 independently re-ran every finding's current-state check against source, docs, tests, tracked issue records, and git history. **CONFIRMED** means reproduced from artifacts; **PLAUSIBLE** marks environment-dependent judgment. The canonical audit scaffold/template was read first. No implementation fix was applied.

## Status Summary

**Latest round:** Round 2

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 1 | 0 | 0 |
| P1 | 9 | 2 | 0 |
| P2 | 1 | 1 | 0 |
| P3 | 0 | 0 | 0 |
| **Total** | **11** | **3** | **0** |

**Ship-readiness verdict:** **Not ready.** Three findings are resolved (F-02, F-08, F-11), but
one P0 and seven P1 findings remain open or reframed-open. The stale-session safety gate has not
passed, the authoritative local verification gate is not green, and adoption, Windows attestation,
public docs/hygiene, dev dependencies, and copier semantic safety remain unresolved. AD-1 is also
still formally pending even though the current runtime behavior matches immediate removal.

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

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Handoff retirement strategy — `[PENDING]`

**Context:** Round 1 found handoff active in normal launch, with reachable traversal and partial
durable/offline semantics absent from scope-supplied native live messaging. Current-tree fact:
`86c41d0` has already removed ingress/hooks/watcher lifecycle, archived the old module, and left an
exit-1 command stub. That shipped behavior matches **(b) immediate removal** in practice, not (a):
there is no safe read/claim/complete/export compatibility cycle. This implementation fact does not
constitute the required human decision, so the decision record remains pending.

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
> **Decision:** `(b) Immediate removal` -- resolved by Sergei 2026-08-29, diverging from the AI recommendation: "we should fully remove them, not just stub them." Implemented in PR #87 (squash `83d2578`): `cmd_handoff_retired` and its Click registration removed entirely (`ai handoff` is no longer a recognized command, not even a fail-closed stub), `archive/handoff.py` deleted. AI-CLI-70q, AI-CLI-3b3, AI-CLI-0ay, and AI-CLI-2qu (referenced in the AI recommendation above) were not independently re-checked as part of this resolution -- confirm before closing.
<!-- decision-record: chosen-option=b; ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

## Outstanding Issues to Fix

### Must do before release

| Order | Action | Exit condition |
|-------|--------|----------------|
| 1 | Finish F-01 P0 topology (`AI-CLI-tdm6.1`, external `AI-CLI-stale-session-reaper-wkmj`) | Separate implementation audit passes the exact release commit. |
| 2 | Close F-03 local gate and F-04 dependency split | Ruff, Pyright, and full pytest pass from the public dev install with actual counts attached. |
| 3 | Fix F-05 audit/adopt resolution | Root/worktree/physical-directory/legacy-slug E2E pass. |
| 4 | Resolve F-06 Windows contract | Supported MSYS2/Git Bash E2E passes or public claims are narrowed. |
| 5 | Finish F-07 active-doc reconciliation | Architecture has no signal-watch/handoff-drain claims; documented commands match Click registrations. |
| 6 | Fix F-09 public-repo hygiene | Metadata and guard scope/exemptions comply with the repository rule. |
| 7 | Fix F-10 copier semantic safety or remove support | Drifted-hunk/non-default-answer fixtures cannot false-pass. |
| 8 | Obtain the AD-1 human decision | Pending record is reconciled with the already-shipped option-(b) behavior. |

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
- [ ] AD-1 human decision recorded and reconciled with the already-shipped behavior.
- [x] Verification Matrix 10/10 reproduced.
- [x] Round 2 re-verification completed; 12/12 current statuses reproduced.
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
```

The Round 1 exact uv commands failed before pytest while creating the uv cache. Round 2's focused
pytest attempt also failed before collection because no writable temporary directory exists under
the single-file write policy. The three `bd show` commands could not open the embedded database
because it must create/open a lock file; tracked JSONL was read as the repository fallback. Ruff
completed successfully. Pyright was rerun with the existing environment explicitly selected and
reported 81 errors.

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
