---
title: "Design and plan doc audit, 2026-11-14"
category: audit
tags: [audit, coherence, designs, plans, citation-validation]
status: findings-pending-fix
date: 2026-11-14
template_version: "audit-1.0.0"
---

<!-- doc:region name="scope" kind="replaceable" -->

# Design and plan doc audit, 2026-11-14

**Status:** findings-pending-fix

**Created:** 2026-11-14

**Auditor:** claude-opus-5-5 (one round; see the Audit Log)

**Target artifact:** the 55 docs directly in `docs/designs/` and `docs/plans/` at `origin/main`
commit `f807424a`, plus `docs/research/citation-validation/`

## Executive Summary

All 55 docs in scope were classified against the code at `f807424a`. Status counts:

| Status | Designs | Plans | Total |
|---|---|---|---|
| implemented | 12 | 21 | 33 |
| partial | 3 | 7 | 10 |
| unimplemented | 3 | 3 | 6 |
| superseded | 1 | 5 | 6 |
| abandoned | 0 | 0 | 0 |
| **Total** | **19** | **36** | **55** |

Every partial or unimplemented doc now has a Beads issue that tracks the remaining work. Before
this audit, three had none: the v1.0.0 release plan, the Linux half of the session-name identifier
notes, and the stale-session reaper design's own completion step.

No research doc was corrected by a citation validation report, so this audit edited no design or
plan doc. The only report in the repo counted 0 citations across four research docs that hold
19 to 68 links each. That run validated nothing (F-03), so the docs downstream of those research
docs rest on citations nobody has checked.

Ten issues filed:

| Issue | Priority | Title |
|---|---|---|
| AI-CLI-edud | P2 | architecture.md module map omits 56 of 94 modules and calls a 9,743-line main.py thin |
| AI-CLI-t0g2 | P2 | launcher-output-standard: run the live checks and close its implementation audit |
| AI-CLI-brmm | P2 | stale-session-reaper design: fill the shipped Design Overview and re-verify its implementation audit |
| AI-CLI-kwpb | P2 | Close or re-scope 12 Beads issues whose design work shipped, was retired or rests on a falsified premise |
| AI-CLI-8hmb | P2 | Citation validation report parsed 0 citations from research docs holding 19 to 68 links; re-run it |
| AI-CLI-ujmd | P3 | main.py has regrown to 9,743 lines against the refactor plan's 1,000-line target |
| AI-CLI-buvq | P3 | programmatic API plan still declares the removed ai_cli.gemini module stable |
| AI-CLI-tz2e | P3 | Design and plan doc status lines and content lag the shipped code in 20 docs |
| AI-CLI-yowq | P3 | Measure client session stores on a Linux host for the session-name identifier notes |
| AI-CLI-87je | P3 | Track the v1.0.0 release: refresh its readiness criteria and link each to an issue |

Docs edited: none (see the citation validation finding, F-03).

## Table of Contents

- [Executive Summary](#executive-summary)
- [What Was Audited](#what-was-audited)
- [Scope](#scope)
  - [Founding ask and alignment](#founding-ask-and-alignment)
  - [In scope](#in-scope)
  - [Out of scope](#out-of-scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Per-Doc Table: designs](#r1-per-doc-table-designs)
  - [R1 Per-Doc Table: plans](#r1-per-doc-table-plans)
  - [R1 Citation Validation Pass](#r1-citation-validation-pass)
  - [R1 Findings](#r1-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)

## What Was Audited

Every design and plan doc directly under `docs/designs/` and `docs/plans/` at `origin/main`
`f807424a` (the worktree was in sync with its upstream when the audit ran: `git rev-list --count
HEAD..origin/main` printed 0). The trigger is AI-CLI-pyh, a P0 design/plan doc audit.

## Scope

### Founding ask and alignment

The ask, verbatim:

```text
review all docs in `docs/designs/` and `docs/plans/`: for unimplemented docs without roadmap tasks, create the task; for completed docs, verify full implementation + robust tests + updated docs. Also update any design/plan docs informed by research docs corrected in this project's citation validation reports — ensure downstream design decisions remain accurate.
```

"Roadmap tasks" is read as Beads issues, since Beads is the repo's only task record and the old
roadmap is archived at `docs/roadmap/archive/`. The ask names a procedure file that is not in this
repository, so the ask itself is the whole spec.

Each clause and where it is answered:

| Clause | Where answered |
|---|---|
| review all docs in designs/ and plans/ | the two per-doc tables (55 rows) |
| unimplemented docs without tasks: create the task | AI-CLI-87je, AI-CLI-yowq, AI-CLI-brmm |
| completed docs: verify full implementation | the Evidence column of each implemented row |
| robust tests | the Tests column; gaps filed as AI-CLI-t0g2, AI-CLI-brmm |
| updated docs | the Doc gap column; filed as AI-CLI-tz2e, AI-CLI-edud, AI-CLI-buvq |
| update docs informed by corrected research | [R1 Citation Validation Pass](#r1-citation-validation-pass): no correction exists; F-03 |

No clause was narrowed.

### In scope

- The 55 `.md` docs directly in `docs/designs/` (19) and `docs/plans/` (36), excluding
  `archive/`, both `README.md` files and `docs/designs/ARCH-TEMPLATE.md`. The brief counted 56;
  the measured count is 55. The extra file is probably
  `docs/designs/single-source-cli-docs.md.invariants.yaml`, a data file that sits beside that design rather than a doc.
- Every file in `docs/research/citation-validation/` and the research docs it names.

### Out of scope

- Code fixes. Gaps are filed, not fixed.
- Running pytest. Test coverage was judged by reading test files and `git log`.
- Docs under `archive/`, and docs under `docs/research/` that no validation report names.
- Private-name hygiene inside these docs. It is already tracked by AI-CLI-mch8 and AI-CLI-wpdd
  and is not repeated here.

## Methodology

**Approach:** Coherence audit (template kind A): roadmap (Beads) against code against docs.

1. Enumerated the 55 docs by name and read each doc's status line, task ids, phases and checklist.
2. Resolved every `AI-CLI-*` id a doc cites against a full dump of the Beads store (990 issues,
   all statuses). Legacy numeric ids such as `AI-CLI-64` were resolved through each issue's
   `id:AI-CLI-N` label.
3. For each doc, checked the code it describes: the module, the symbol, the CLI command and its
   options in `src/ai_cli/main.py`, and the merge commit from `git log`.
4. Named the test files that drive each implemented behaviour at its entry point (CLI command,
   launch template, daemon loop).
5. Searched Beads (`bd search`, plus the open, in-progress, blocked and deferred dumps) before
   filing any issue.
6. Read the only citation validation report and counted the links in each research doc it
   names.

<!-- /doc:region name="scope" -->

<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Latest round:** Round 1

**Outstanding by severity (across all rounds):** 0 P0, 0 P1, 5 P2 (F-01, F-02,
F-03, F-04, F-05) and 7 P3 (F-06 to F-12). F-11 and F-12 needed no new issue.

**Ship-readiness verdict:** Not applicable; this audits docs, not a release. The docs are not
accurate as a set until AI-CLI-tz2e, AI-CLI-edud and AI-CLI-kwpb land.
<!-- /doc:region name="loop_receipt" -->

## Round 1 — Main Audit

**Round 1 auditor:** claude-opus-5-5

**Round 1 date:** 2026-11-14

**Round 1 scope:** the whole scope above.

### R1 Summary

The code is further along than the docs say. Of the 33 implemented docs, 20 still carry a draft,
approved or in-progress status line, or content the code has moved past (F-06). Twelve open Beads
issues track work that shipped, was retired with the handoff system in v0.8.0, or rests on a
premise that was tested and falsified (F-04). The living architecture doc names only 38 of the 94
modules (F-01).

Two implemented designs, the launcher output standard and the stale-session reaper, shipped with
their own implementation audit tables mostly unchecked and their live checks never run (F-02,
F-05).

### R1 Per-Doc Table: designs

Tests are named only for implemented or partial docs. "Doc gap" lists what the doc gets wrong
against the code; the issue that carries the fix is in brackets.

| Doc | Status | Evidence | Tracking issue | Tests at the entry point | Doc gap |
|---|---|---|---|---|---|
| `architecture.md` | implemented | living doc; module map checked against `src/ai_cli/*.py` | AI-CLI-edud | n/a | 56 of 94 modules unnamed; calls `main.py` thin at 9,743 lines [AI-CLI-edud] |
| `cc-statusline.md` | implemented | `quota.py:1617` `quota_statusline_part`; `ai quota statusline-part` (`main.py:7359`); `data/statusline-command.sh` deployed by `main.py:1171` | none needed | `tests/test_quota_statusline_segments.py`, `tests/hooks/test-statusline-quota-cache.sh`, `tests/test_quota.py` | settings snippet nests `statusLine` under `hooks` [AI-CLI-tz2e]; AI-CLI-jhq (write this doc) still open [AI-CLI-kwpb] |
| `cc-sync-phase2.md` | implemented | `ai sync push/pull/conflicts/resolve/watch` (`main.py:9400-9540`); git staging repo in `sync.py:2-21` | none needed | `tests/test_sync.py` (325 tests), `tests/test_sync_watch.py` | status `approved`; broken link to `../plans/cc-memory-history-sync-plan.md` [AI-CLI-tz2e] |
| `claude-usage-telemetry.md` | partial | Phases 1-2 shipped (`telemetry.py`, `quota.py` scrape); Phase 3 alerting gaps open | AI-CLI-pm2; AI-CLI-35n (deferred, native API) | `tests/test_quota.py`, `tests/test_telemetry.py` | doc itself says its architecture sections are stale; `citation_review: pending` [AI-CLI-8hmb] |
| `cross-session-handoff.md` | superseded | handoff retired in v0.8.0 (`CHANGELOG.md:1506`); no handoff command in `main.py` | none (retired) | n/a | four open phase issues for the retired feature [AI-CLI-kwpb] |
| `event-driven-platform.md` | implemented | `messaging.py` subjects `sync.>`, `session.>`, `memory.>`, `quota.>`; all four phases checked complete | none needed | `tests/test_messaging.py`, `tests/test_messaging_jetstream.py`, `tests/test_nats_callback_handlers.py`, `tests/test_sync_watch.py` | status `approved` [AI-CLI-tz2e] |
| `iterm2-cross-machine-vscode-routing.md` | partial | Phase 1 shipped (`vscode.py`, `ai internal open-vscode-remote`, commit `d0fc6d4c`, #169); Phase 2 needs a physical click | AI-CLI-d6c9 (blocked on a human UAT) | `tests/test_vscode.py` | status `draft` [AI-CLI-tz2e] |
| `iterm2-layout-system.md` | partial | `ai layout` group shipped (`layout.py:393`, `main.py:7593`); the `ai c N` layout hook is not wired | AI-CLI-kb8z | `tests/test_layout.py` | claims `ai c N` applies a layout [AI-CLI-kb8z]; cites the archived roadmap [AI-CLI-tz2e] |
| `iterm2-title-color-system.md` | implemented | `session_script.py:1300-1340` (`_it2`, `_iterm2_fleet_setup`, `_iterm2_status`); lease file `iterm2.py:354` | none needed | `tests/test_iterm2.py` (88 tests), `tests/test_icon_generator.py` | none found (the shell-pane precmd hook it names lives in the user's own shell rc, outside this repo) |
| `launcher-output-standard.md` | implemented | `output.py` (`emit` :758, `render_progress` :657, `_write_records` :701, `flush_sinks` :294); `structlog`, `rich` in `pyproject.toml:59-60`; epic AI-CLI-1o9b closed | AI-CLI-t0g2 | `tests/test_line_discipline.py`, `tests/test_output_rendering.py`, `tests/test_output_config.py`, `tests/test_output_datetime.py`, `tests/test_output_operation.py`, `tests/test_output_sinks.py`, `tests/test_reload_progress.py`, `tests/test_launch_reporter.py` | status `draft`; implementation audit rows 1, 2, 3, 5, 8 unchecked; live checks not run [AI-CLI-t0g2] |
| `notification-system.md` | implemented | `notifications.py` (`Notifier`, `NotificationManager`); `notification_log` (`quota_db.py:126`); `ai notifications list/log` (`main.py:7393-7418`) | none needed | `tests/test_notifications.py` (50 tests) | status `approved`; CLI block omits `-S`/`-F` short forms [AI-CLI-tz2e] |
| `probe-sessions.md` | implemented | `-t/--probe` (`main.py:3548`); commit `6c1cdb6b` (#540) | none needed | `tests/test_probe_launch.py`, `tests/test_probe_transcripts.py` | none found |
| `session-name-registry-dolt.md` | unimplemented | no Dolt backend in `name_registry.py` on main; Phases 1-3 in flight on a branch | AI-CLI-6xud, AI-CLI-6xud.7 (in progress), AI-CLI-6xud.10, AI-CLI-6xud.11 | n/a | none found |
| `session-resume-by-name.md` | implemented | `_resolve_resume_target` (`main.py:4145`); commit `537efd23` (#538) | none needed | `tests/test_resume_by_name.py` | none found |
| `single-source-cli-docs.md` | implemented | `cli_help.py`; `scripts/generate_docs.py`, `scripts/wiki_build.py`, `scripts/lint_doc_alignment.py`; `.github/workflows/wiki.yml`; commit `77e7c11d` (#517) | AI-CLI-lels (blocked, live wiki publication) | `tests/test_cli_docs.py`, `tests/test_cli_help.py`, `tests/test_wiki_build.py`, `tests/test_lint_doc_alignment.py` | frontmatter `draft` while its body says implemented [AI-CLI-tz2e] |
| `stale-session-reaper.md` | implemented | `stale_session_reaper.py`; `ai session-reaper` group (`main.py:7311`) | AI-CLI-brmm | `tests/test_stale_session_reaper.py`, `tests/test_process_manager.py` | Design Overview is a stub; 12 of 16 audit rows unchecked; status `draft` [AI-CLI-brmm] |
| `user-behavior-telemetry.md` | unimplemented | no nudge schema, fatigue score or `analytics.db` in `src/` | AI-CLI-49g | n/a | describes web and agent-API surfaces this CLI does not have; broken research link [AI-CLI-kwpb, AI-CLI-tz2e] |
| `vpn-transport-switching.md` | implemented | `vpn_watch.py`; `transport.py:477-521` VPN cache; `ai watch-vpn` (`main.py:9342`) | AI-CLI-dkx (live UAT) | `tests/test_vpn_watch.py`, `tests/test_transport_vpn_cache.py`, `tests/test_transport.py` | status `draft` [AI-CLI-tz2e] |
| `vscode-terminal-tab-naming.md` | unimplemented | no VS Code terminal detection in `src/` (`TERM_PROGRAM` is read only for iTerm2) | AI-CLI-5abz (blocked on the doc's Open Question 1) | n/a | none found |

### R1 Per-Doc Table: plans

| Doc | Status | Evidence | Tracking issue | Tests at the entry point | Doc gap |
|---|---|---|---|---|---|
| `ai-cli-118-daemon-task-tool-self-healing-plan.md` | superseded | the plan records its root cause as tested and falsified; the launch template writes a pre-launch settings override instead (`session_script.py:1510`) | AI-CLI-sgv (needs re-scope) | n/a | cites the archived roadmap [AI-CLI-tz2e]; AI-CLI-sgv open on a falsified premise [AI-CLI-kwpb] |
| `ai-cli-46-programmatic-api-plan.md` | implemented | quota half: `read_latest_snapshot`, `QuotaSnapshot`; gemini half removed in v0.7.0 (`3ac237ff`) | AI-CLI-buvq | `tests/test_public_api.py` (quota only) | still declares `ai_cli.gemini` stable [AI-CLI-buvq] |
| `ai-cli-6xyr-session-migrate-carries-every-artifact-plan.md` | implemented | 22 of 22 boxes checked; AI-CLI-6xyr closed; `session_migrate_*.py` | none needed | `tests/test_session_migrate.py`, `tests/test_session_migrate_agents.py`, `tests/test_session_migrate_container_files.py`, `tests/test_session_migrate_references.py` | none found |
| `ai-cli-k2r8-sessions-table-views-and-help-plan.md` | implemented | `session_list_view.py`; commit `a6fcba56` (#541); AI-CLI-k2r8 closed | none needed | `tests/test_sessions_list_table.py`, `tests/test_cli_help.py` | none found |
| `ai-cli-nyz8-restart-idle-sessions-plan.md` | partial | `-X/--restart-session` (`main.py:6210`), #372, #375, #378 shipped; rollout open | AI-CLI-nyz8.5, AI-CLI-xewt | `tests/test_session_restart.py`, `tests/test_restart_auto.py`, `tests/test_restart_gate_streaming.py`, `tests/test_restart_supervisor.py` | AI-CLI-nyz8.1 to .4 still open for shipped work [AI-CLI-kwpb] |
| `ai-cli-ofp7-migrate-session-completeness-plan.md` | implemented | AI-CLI-ofp7 and all but two children closed; the two open ones are later bugs | AI-CLI-ofp7.15, AI-CLI-ofp7.16 | `tests/test_session_migrate_finish_sweep.py`, `tests/test_session_migrate_finished_rule.py`, `tests/test_session_migrate_status_verb.py`, `tests/test_session_migrate_on_launch.py` | 0 of 41 boxes checked while the epic is closed [AI-CLI-tz2e scope] |
| `bare-path-pty-submission.md` | partial | POSIX launcher-owned pty shipped (`pty_host.py`, `_pty_handoff` `main.py:361`); Windows ConPTY not built | AI-CLI-ysht | `tests/test_bare_pty_host.py` | none found |
| `cdp-plan.md` | implemented | `ai cdp start/stop/status` (`main.py:9302-9333`) | none needed (bugs AI-CLI-7sme, AI-CLI-nx8d open) | `tests/test_cdp.py` (77 tests) | status `draft`; cites the archived roadmap [AI-CLI-tz2e] |
| `circus-signal-watch-plan.md` | superseded | retired with the handoff system in v0.8.0 | none (retired) | n/a | `process_hygiene.py` still scores signal-watch processes [AI-CLI-tz2e comment] |
| `claude-quota-sync-hw-scheduling.md` | implemented | `quota_sync_from_remote` (`quota.py:1264`), `_publish_quota_snapshot` (`quota.py:1368`) | none needed | `tests/test_quota.py`, `tests/test_quota_env_source.py` | says "live UAT in progress" [AI-CLI-tz2e] |
| `cross-session-signaling-plan.md` | superseded | retired with the handoff system in v0.8.0 | none (retired) | n/a | none |
| `demo-video-plan.md` | partial | `demo/record-demo.sh`, `demo/demo-runner.sh`; `README.md:12` embeds `demo/demo-20260420-053045-1cdf560.mp4` | AI-CLI-zq7 (re-record) | n/a (recording tooling) | none found |
| `going-public-plan.md` | implemented | repository is public; AI-CLI-l2o and AI-CLI-4o3 closed; `renovate.json5`, `.github/ISSUE_TEMPLATE/` present | AI-CLI-3tjq (Renovate policy) | `tests/test_public_repo_hygiene.py` | unchecked ACs drifted: CI runs one Python, not 3.11-3.13; `.github/dependabot.yml` still present |
| `handoff-reliability-testing.md` | superseded | retired with the handoff system in v0.8.0 | none (retired) | n/a | none |
| `iterm2-fleet-config-plan.md` | partial | Dynamic Profiles (`icon_generator.py`, `layout.py`), `allow-passthrough` (`iterm2.py:672`), runtime icons shipped through the iTerm2 overhaul | AI-CLI-ky0 (deferred) | `tests/test_iterm2.py`, `tests/test_iterm2_profile_isolation.py` | none found |
| `iterm2-neighbor-color-plan.md` | unimplemented | no neighbour-aware colour selection in `iterm2.py` | AI-CLI-x3z (deferred) | n/a | none found |
| `iterm2-ntfy-session-status-plan.md` | unimplemented | no ntfy call in `session_script.py` or `iterm2.py`; ntfy exists only for quota alerts | AI-CLI-3mh (deferred) | n/a | none found |
| `iterm2-smart-titles-plan.md` | partial | tmux-session-name titles shipped (`session_script.py` `_iterm2_status`); generated window titles not built | AI-CLI-nfp (deferred) | `tests/test_iterm2.py`, `tests/test_session_title.py` | none found |
| `iterm2-title-color-redesign-plan.md` | implemented | AI-CLI-e4p closed; see `iterm2-title-color-system.md` | none needed | `tests/test_iterm2.py`, `tests/test_icon_generator.py` | none found |
| `launch-output-contract-plan.md` | implemented | 46 of 46 boxes checked; `launch_reporter.py` | none needed | `tests/test_tagged_launch_output.py`, `tests/test_launch_reporter.py` | none found |
| `main-py-refactor-plan.md` | implemented (regressed) | extraction shipped in 2026-04; `main.py` is 9,743 lines against a ~1,000 target | AI-CLI-ujmd | `tests/test_cli_dispatch.py`, `tests/test_main.py` | outcome no longer holds [AI-CLI-ujmd] |
| `open-source-professionalization-plan.md` | implemented | `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, README badges, `.github/workflows/ci.yml` | none needed | `tests/test_contributing_structure.py`, `tests/test_readme_fences.py` | status `draft`; `CODE_OF_CONDUCT.md` absent though the plan lists it [AI-CLI-tz2e] |
| `pre-release-v0.2.0-plan.md` | superseded | package is at 0.8.0 (`pyproject.toml:3`) | AI-CLI-ieh (coverage) | n/a | says IN PROGRESS [AI-CLI-tz2e] |
| `proactive-project-registry-plan.md` | implemented | `ai register -p/-x/-t` (`main.py:7147`); no-registry opt-out (`config.py:1862`); `[tool.ai-cli]` lookup (`config.py:1607`) | none needed | `tests/test_prefix_registry_tiers.py`, `tests/test_cli_dispatch.py` | status `draft` [AI-CLI-tz2e]; AI-CLI-077 open [AI-CLI-kwpb] |
| `process-hygiene-plan.md` | implemented | `ai ps` and `ai ps clean` (`process_hygiene.py:737-783`); score-triggered auto-kill withdrawn, as the doc records | none needed | `tests/test_process_hygiene.py` (82 tests), `tests/test_process_guard.py` | none found |
| `quota-scrape-format-detection-plan.md` | implemented | `quota_meta` (`quota_db.py:141`); mismatch counter (`quota.py:961`) | none needed | `tests/test_quota_format_detection.py` | none found |
| `quota-watch-nats-listener-plan.md` | implemented | `quota.scrape.request.{machine}` subscription (`quota.py:902`) | none needed | `tests/test_quota.py` | none found |
| `release-v1.0.0-plan.md` | unimplemented | package at 0.8.0; no v1.0.0 tag | AI-CLI-87je (new) | n/a | C2 names the retired handoff queue; C4 regressed [AI-CLI-87je, AI-CLI-tz2e] |
| `session-name-identifiers-phase-3-notes.md` | partial | name list matches the notes (622 names: 140 of 3 letters, 482 of 4); cited by `session_clients.py`; Linux measurement missing | AI-CLI-yowq (new) | n/a (measurement notes) | none found |
| `skill-audit-copier-automation-plan.md` | implemented | `copier_update.py`, `copier_recovery.py`; AI-CLI-m1e closed | none needed | `tests/test_copier_update.py` (103 tests), `tests/test_copier_update_recovery.py` | none found |
| `sync-repo-pull-plan.md` | implemented | `sync_repos` (`sync.py:1433`) | none needed | `tests/test_sync.py` (42 references to `sync_repos`) | status `approved` [AI-CLI-tz2e] |
| `tagged-output-enforcement-plan.md` | implemented | 13 of 13 boxes checked; AI-CLI-9brz closed | none needed | `tests/test_output_enforcement.py` | none found |
| `test-quality-coverage-plan.md` | partial | coverage uploaded from CI (`ci.yml:101`) but no coverage floor enforced; 1 of 29 boxes checked | AI-CLI-ieh | n/a | cites the archived roadmap [AI-CLI-tz2e] |
| `tunnel-plan.md` | implemented | `ai tunnel start/stop/status` (`main.py:9269-9293`), `tunnel.py` | none needed (bug AI-CLI-zb89 open) | `tests/test_cli.py` (`test_cmd_tunnel_start_*`), `tests/test_cdp.py` tunnel cases | none found |
| `windows-support-plan.md` | implemented (regressed) | shipped in v0.6.0 (AI-CLI-upt closed); `ai c` now fails on Windows | AI-CLI-4bhw, AI-CLI-1wi | `tests/test_windows_self_update.py`, `tests/test_xdg_isolation_on_windows.py`, the `test-windows` CI job | says COMPLETE with no regression note [AI-CLI-tz2e] |
| `workspace-sync-plan.md` | implemented | `ai ws pull` (`main.py:9546`), `workspace.py` | AI-CLI-6tq (mislinked task id) | `tests/test_workspace.py` (31 tests) | says IN PROGRESS [AI-CLI-tz2e] |

### R1 Citation Validation Pass

`docs/research/citation-validation/` holds one report,
`validation-report_manual__2026-04-29T03-01-16_00-00.md`. It lists six docs, 0 citations,
0 PASS, 0 WARN, 0 FAIL and 0 dead links, and marks nothing corrected. So there is no corrected
research doc, and no design or plan doc needed an edit under the ask's second clause.

The report itself is not evidence of validation. Its six docs include `README.md` and a
`TEMPLATE.md` that no longer exists. The four research docs it names hold inline links:

| Research doc | Links | Downstream design or plan docs |
|---|---|---|
| `github-repo-automation.md` | 68 | `going-public-plan.md` |
| `iterm2-fleet-management-config.md` | 45 | `iterm2-fleet-config-plan.md`, `iterm2-title-color-redesign-plan.md` |
| `iterm2-terminal-customization-research.md` | 19 | `iterm2-title-color-system.md`, `iterm2-title-color-redesign-plan.md` |
| `open-source-package-best-practices.md` | 49 | `open-source-professionalization-plan.md` |

A citation count of zero over 181 links means the validator parsed none of them. F-03 covers it.

### R1 Findings

#### F-01: architecture.md no longer describes the package — `P2`

**Location:** `docs/designs/architecture.md` § Module Map

**Evidence:** 56 of the 94 files in `src/ai_cli/*.py` appear nowhere in the doc (the
`session_migrate*.py` family, `session_restart.py`, `session_run_state.py`, `name_registry.py`,
`pty_host.py`, `vscode.py`, `worktree_move.py` and others). The `main.py` row says "Thin after
AI-CLI-39 refactor"; `wc -l src/ai_cli/main.py` prints 9743.

**Recommendation:** AI-CLI-edud. CONFIRMED.

#### F-02: launcher output standard shipped without its live checks — `P2`

**Location:** `docs/designs/launcher-output-standard.md` § Implementation Audit

**Evidence:** AI-CLI-1o9b and all its children are closed. Rows 1, 2, 3, 5 and 8 of the
implementation audit are unchecked, rows 8 and 9 say the live check was not run, and "Audit
completed" is blank.

**Recommendation:** AI-CLI-t0g2. CONFIRMED.

#### F-03: the citation validation run validated nothing — `P2`

**Location:** `docs/research/citation-validation/validation-report_manual__2026-04-29T03-01-16_00-00.md`

**Evidence:** see [R1 Citation Validation Pass](#r1-citation-validation-pass). Six design and plan
docs build on research whose citations have never been checked, and
`docs/designs/claude-usage-telemetry.md:7` still reads `citation_review: pending`.

**Recommendation:** AI-CLI-8hmb: fix the validator so a zero over linked docs fails, re-run it
over all of `docs/research/`, and act on any correction. CONFIRMED.

#### F-04: twelve open Beads issues track finished, retired or falsified work — `P2`

**Location:** Beads store; listed in AI-CLI-kwpb

**Evidence:** AI-CLI-jhq asks for `cc-statusline.md`, which exists. AI-CLI-077 asks for
`ai register` and the registry opt-out, both in the code. AI-CLI-nyz8.2, .3 and .4 shipped
in PRs #372, #375 and #378. AI-CLI-oa8, AI-CLI-d2p, AI-CLI-k1u and AI-CLI-2qu are phases of the handoff
system v0.8.0 retired. AI-CLI-sgv rests on a cause its own plan records as falsified. AI-CLI-49g
implements a design written for surfaces this CLI does not have. AI-CLI-nyz8.1 is probably done
by `docs/research/claude-code-session-identity-and-reachability.md` (PLAUSIBLE; its ACs need a
check).

**Recommendation:** AI-CLI-kwpb. The auditor did not close them: closing other owners' issues was
outside the brief. CONFIRMED except AI-CLI-nyz8.1.

#### F-05: the stale-session reaper design was never filled in after it shipped — `P2`

**Location:** `docs/designs/stale-session-reaper.md:62` and § Implementation Audit;
`docs/audits/stale-session-reaper-implementation-audit.md:736-760`

**Evidence:** the Design Overview reads "stub — to be filled during/after implementation". 12 of
16 audit rows are unchecked. The implementation audit lists I-03 to I-05 as open P1 items, but the
issues that took them (AI-CLI-qt1e, AI-CLI-028b, AI-CLI-517p) are closed, and I-06 had no issue.

**Recommendation:** AI-CLI-brmm. CONFIRMED.

#### F-06: status lines and content lag the code in 20 docs — `P3`

**Location:** every row with an [AI-CLI-tz2e] tag in the two per-doc tables

**Evidence:** 13 shipped docs still say draft, approved or in progress; `windows-support-plan.md`
says COMPLETE while AI-CLI-4bhw records that `ai c` cannot launch on Windows; two relative links
are broken; four docs point at the archived roadmap; `release-v1.0.0-plan.md` C2 requires the
retired handoff queue; `cc-statusline.md` shows the wrong settings shape.

**Recommendation:** AI-CLI-tz2e. CONFIRMED, except the `cc-statusline.md` settings shape, which is
PLAUSIBLE: it rests on Claude Code's documented top-level `statusLine` key, which this audit did
not re-fetch.

#### F-07: main.py regrew past its refactor target — `P3`

**Location:** `docs/plans/main-py-refactor-plan.md:44`, `:223`

**Evidence:** target about 1,000 lines; measured 9,743. No open issue tracks it.

**Recommendation:** AI-CLI-ujmd. CONFIRMED.

#### F-08: a removed module is still declared stable public API — `P3`

**Location:** `docs/plans/ai-cli-46-programmatic-api-plan.md` § Public API Surface

**Evidence:** `from ai_cli.gemini import run_gemini, GeminiResult, AttemptLog` is declared stable;
`src/ai_cli/gemini.py` was deleted in `3ac237ff`.

**Recommendation:** AI-CLI-buvq. CONFIRMED.

#### F-09: the Linux client-store measurement was never taken — `P3`

**Location:** `docs/plans/session-name-identifiers-phase-3-notes.md:32`

**Evidence:** "The Linux build host still needs the same measurement for every client installed
there." `bd search` found no issue.

**Recommendation:** AI-CLI-yowq. CONFIRMED.

#### F-10: the v1.0.0 release has no tracking issue — `P3`

**Location:** `docs/plans/release-v1.0.0-plan.md` § v1.0.0 Readiness Criteria

**Evidence:** `bd search "v1.0"` and `bd search "1.0.0"` return nothing open.

**Recommendation:** AI-CLI-87je. CONFIRMED.

#### F-11: test coverage has no floor — `P3`

**Location:** `.github/workflows/ci.yml:101`; `docs/plans/test-quality-coverage-plan.md`

**Evidence:** CI runs `pytest --cov` and uploads the report, but nothing fails below a threshold
(`fail_under` and `--cov-fail-under` appear nowhere in `pyproject.toml` or `ci.yml`). The
release plan's C1 asks for 95%.

**Recommendation:** no new issue; folded into AI-CLI-87je C1 and the existing AI-CLI-ieh.
CONFIRMED.

#### F-12: going-public ACs drifted after later decisions — `P3`

**Location:** `docs/plans/going-public-plan.md:73-108`

**Evidence:** the unchecked ACs ask for a 3.11-3.13 test matrix and a removed
`dependabot.yml`; CI now runs one pinned Python on purpose (`ci.yml:9-12`) and
`.github/dependabot.yml` still sits beside `renovate.json5`.

**Recommendation:** no new issue; the Renovate question is AI-CLI-3tjq. CONFIRMED.

### R1 Resolution Pass

| Finding | Resolution |
|---|---|
| F-01 | TRACKED: AI-CLI-edud |
| F-02 | TRACKED: AI-CLI-t0g2 |
| F-03 | TRACKED: AI-CLI-8hmb |
| F-04 | TRACKED: AI-CLI-kwpb |
| F-05 | TRACKED: AI-CLI-brmm |
| F-06 | TRACKED: AI-CLI-tz2e (plus a comment for `CODE_OF_CONDUCT.md` and the signal-watch scorer) |
| F-07 | TRACKED: AI-CLI-ujmd |
| F-08 | TRACKED: AI-CLI-buvq |
| F-09 | TRACKED: AI-CLI-yowq |
| F-10 | TRACKED: AI-CLI-87je |
| F-11 | TRACKED: AI-CLI-87je, AI-CLI-ieh |
| F-12 | TRACKED: AI-CLI-3tjq |

No fix was applied inline. The brief allowed doc edits only for research corrections, and there
were none.

### R1 Verification Matrix

| Finding | Reproduction on `f807424a` | Result |
|---|---|---|
| F-01 | for each `src/ai_cli/*.py`, `grep -q <module stem> docs/designs/architecture.md`; `wc -l src/ai_cli/main.py` | 56 missing; 9743 |
| F-02 | read `launcher-output-standard.md:1586-1596` | 5 rows unchecked |
| F-03 | `grep -c 'https\?://'` on the four research docs | 68, 45, 19, 49 against a reported 0 |
| F-04 | `git log --oneline --grep=nyz8`; `grep -n '"register"' src/ai_cli/main.py`; Beads dump | #372, #375, #378 merged; `cmd_register` at :7147 |
| F-05 | read `stale-session-reaper.md:62`, `:227-246` | stub; 12 of 16 unchecked |
| F-07 | `wc -l src/ai_cli/main.py` | 9743 |
| F-08 | `git log --diff-filter=D -- src/ai_cli/gemini.py` | `3ac237ff` |
| F-09 | `grep -n "Linux build host" docs/plans/session-name-identifiers-phase-3-notes.md` | line 32 |
| F-11 | `grep -n -i "fail_under\|cov-fail" pyproject.toml .github/workflows/ci.yml` | no match |

**Verified: 9 of 12 findings reproduced on `f807424a`;** F-06, F-10 and F-12 are lists of
the same checks already shown in the per-doc tables.

## Decisions Requiring Team Input

None. Every finding is a doc correction, a missing tracking issue or Beads bookkeeping. F-07 asks
for a decision (split `main.py` again or retire the target), but it is contained and two-way, so
it is left to whoever takes AI-CLI-ujmd under the decision framework.

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding |
|---|---|---|---|
| I-01 | P2 | AI-CLI-edud | F-01 |
| I-02 | P2 | AI-CLI-t0g2 | F-02 |
| I-03 | P2 | AI-CLI-8hmb | F-03 |
| I-04 | P2 | AI-CLI-kwpb | F-04 |
| I-05 | P2 | AI-CLI-brmm | F-05 |
| I-06 | P3 | AI-CLI-tz2e | F-06 |
| I-07 | P3 | AI-CLI-ujmd | F-07 |
| I-08 | P3 | AI-CLI-buvq | F-08 |
| I-09 | P3 | AI-CLI-yowq | F-09 |
| I-10 | P3 | AI-CLI-87je | F-10, F-11 |

## Already-Correct Items

- `probe-sessions.md`, `session-resume-by-name.md`, `ai-cli-k2r8-...-plan.md`,
  `launch-output-contract-plan.md` and `tagged-output-enforcement-plan.md` match the code and
  have tests at the CLI entry point.
- Every superseded doc in the handoff family carries the v0.8.0 supersession banner.
- `process-hygiene-plan.md` records its withdrawn auto-kill design plainly, and the code matches
  the revised scope (`ai ps clean` is the only terminating path).
- The session-name list counts in `session-name-identifiers-phase-3-notes.md` (622, 140, 482)
  match `src/ai_cli/data/session-names/names.txt` exactly.
- Every deferred iTerm2 plan has a deferred Beads issue.

## Anti-Patterns to Watch For

- **A zero over linked docs.** A validation report of 0 citations over docs holding 181 links
  looks like a clean pass. Count the haystack before reading a zero as a result.
- **A closed epic over an unchecked audit table.** Two designs shipped with their own
  implementation audits mostly blank. Closing the epic did not require the table.
- **Legacy ids that look missing.** `AI-CLI-64` and similar do not resolve as Beads ids; they
  resolve through the `id:AI-CLI-N` label. Reading them as absent would have invented a dozen
  untracked docs.

## Sign-Off Checklist

- [x] Every doc in scope has a row with a status and evidence
- [x] Every partial or unimplemented doc has a tracking issue
- [x] Every implemented doc names its entry-point tests or states why none apply
- [x] Every citation validation report read; downstream docs listed
- [ ] Round 2 verification by a different model family
- [ ] Filed issues resolved

## Audit Log

| Date | Round | Auditor | What changed |
|---|---|---|---|
| 2026-11-14 | 1 | claude-opus-5-5 | Main audit of 55 docs at `f807424a`; 12 findings; 10 issues filed; no doc edited |

## Appendix: Files Read

**Primary subject:** the 55 docs listed in the two per-doc tables.

**Research / evidence base:** `docs/research/citation-validation/validation-report_manual__2026-04-29T03-01-16_00-00.md`;
the four research docs it names; `docs/research/claude-code-session-identity-and-reachability.md`.

**Existing code:** `src/ai_cli/main.py`, `quota.py`, `quota_db.py`, `telemetry.py`, `sync.py`,
`messaging.py`, `notifications.py`, `output.py`, `vscode.py`, `iterm2.py`, `layout.py`,
`icon_generator.py`, `session_script.py`, `stale_session_reaper.py`, `process_hygiene.py`,
`config.py`, `__init__.py`, `vpn_watch.py`, `transport.py`; `pyproject.toml`;
`.github/workflows/ci.yml`; `CONTRIBUTING.md` § Changelog Fragments.

**Audits:** `docs/audits/stale-session-reaper-implementation-audit.md`.

## Appendix: Commands Run

```bash
git rev-list --count HEAD..origin/main          # 0: audited base is origin/main f807424a
bd list --status <open|in_progress|blocked|deferred|closed> -n 0 --json   # 990 issues
bd search "<keywords>" --json -n 0              # before each filing
grep -c -E '\[x\]|\[ \]' docs/{designs,plans}/*.md
grep -n '@.*command(\|@.*group(' src/ai_cli/main.py
wc -l src/ai_cli/main.py
git log --oneline --grep=nyz8
git log --oneline --diff-filter=D -- src/ai_cli/gemini.py
grep -c 'https\?://' docs/research/<doc>.md
~/.claude/bin/bd-new --repo <repo> --no-assignee --title ... --priority ... --type task --body-file ...
```
