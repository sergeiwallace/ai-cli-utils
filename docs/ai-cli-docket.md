---
title: "Docket"
category: docket
tags: [docket, review-queue, autonomous-batch]
status: current
source: "codex-implement, delegated by aih-2"
template_version: "docket-1.0.0"
---

# Docket

**Repository:** ai-cli-utils

**Last refreshed:** 2026-08-20

**Ordering:** Within each queue, order ready work by priority (P0 → P3), then by
unblock impact. `priority_override` always wins over the derived score.

**Row fields:** `bucket`, `priority`/`value`, `task_id`, `title`,
`decision_needed`/`why_ungated`, `doc_path`, `unblock_links`,
`time_criticality`, `risk_opportunity`, `confidence`, `effort`/`size`
(1/2/3/5/8/13/20), `unblock_count`, `agent_ready`, `priority_override`, and
derived `score`.

<!-- doc:region name="review_queue" kind="replaceable" -->

## Review Queue — 69 open bd issues

`Ordering: P0 → P3, then descending WSJF-lite composite score within each band. Score = confidence × (value+tc+ro) × (1+log(1+unblock)) ÷ effort; `*` = unscored, running on defaults.`

| Priority | Task | Title | Decision needed | Absolute doc path | Unblocks | Score inputs |
|----------|------|-------|-----------------|-------------------|----------|--------------|
| P0 | `AI-CLI-53` | **Design/plan doc audit** | — (no open decision; ready to work) | — | — | `0.98` |
| P0 | `AI-CLI-71` | **Auto context-window management for CC sessions — detection, compaction, handoff** | — (no open decision; ready to work) | — | — | `0.97` |
| P0 | `AI-CLI-176` | **Publish latest ai-cli-utils changes to PyPI + update docs + scrub public repo** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-112` | **`ai c`/`ai g` launcher doesn't activate direnv before starting the agent — fleet-wide stale/missing-secrets exposure** | — (no open decision; ready to work) | — | — | `2.55` |
| P1 | `AI-CLI-62` | **Proactive project registry registration** | — (no open decision; ready to work) | — | — | `1.76` |
| P1 | `AI-CLI-153` | **Handoff v2 Phase 1: queue integrity + observability (leases, reconciler, dead-letter, lifecycle events, ID serialization, machine targeting)** | — (no open decision; ready to work) | — | unblocks 2 | `1.75*` |
| P1 | `AI-CLI-17` | **Resolve last 2% coverage gap in main.py** | — (no open decision; ready to work) | — | — | `1.5` |
| P1 | `AI-CLI-16` | **Handoff queue reliability — testing and hardening** | — (no open decision; ready to work) | — | — | `1.46` |
| P1 | `AI-CLI-154` | **Handoff v2 Phase 2: hook-based delivery (Stop/UserPromptSubmit/SessionStart); retire restart-based delivery and pre-claim** | — (no open decision; ready to work) | — | unblocks 1 | `1.41*` |
| P1 | `AI-CLI-live-verify-rc-nxkm` | **Live-verify RC zero-touch auto-reconnect on a Mac sw-* session after the AI-CLI-an5r auto-update-race fix** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-exiting-cc-session-vkck` | **Exiting a CC session can leave it STOPPED not dead, so the launcher reads the name as in use and starts a new session instead of resuming** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-ai-c-1-msxj` | **ai c 1 — stale orphan worktree directory blocks launch on Windows** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-ai-c-bare-xzzf` | **ai c bare-mode Windows — TUI corruption and keyboard handling broken (random chars, Shift+Enter, Ctrl+C)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-209` | **Remote named-session launch preview identity does not match the server-allocated tmux session name** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-196` | **Sweep every repo's git worktrees: inventory, action uncommitted changes, sync with remote, merge to target, leave mains in sync** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-157` | **[bug] NATS handoff payload writes unvalidated filename/content into the queue dir (path traversal + untrusted payload)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-135` | **Auto-update stamp is written before the update runs, latching a failed host behind forever** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-133` | **Guard the harm, not the cause: block commits that delete files still live on origin/main** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-129` | **Runaway loop re-ran _refresh_live_session_scripts ~9M times in 2 minutes, emitting 1.1 GB of output** | — (no open decision; ready to work) | — | — | `0.83*` |
| P1 | `AI-CLI-61` | **Windows notifications / backend system for project repos** | — (no open decision; ready to work) | — | — | `0.44` |
| P2 | `AI-CLI-124` | **ai-cli-utils: git_repair._git_env() guard applied to only 3 of 7 files doing cross-repo git targeting** | — (no open decision; ready to work) | — | — | `4.08` |
| P2 | `AI-CLI-92` | **`ai copier-update` `--defaults` silently drops non-default stored copier answers** | — (no open decision; ready to work) | — | — | `3.75` |
| P2 | `AI-CLI-122` | **Two stale branches carry real unmerged work — review each for merge, finish, or explicit close** | — (no open decision; ready to work) | — | — | `3.2` |
| P2 | `AI-CLI-107` | **`ai copier-update` must tolerate a relative `_src_path` in isolated-worktree mode** | — (no open decision; ready to work) | — | — | `2.93` |
| P2 | `AI-CLI-48` | **NATS integration tests for handoff callbacks** | — (no open decision; ready to work) | — | — | `2.25` |
| P2 | `AI-CLI-118` | **CC background-daemon restart silently drops `Task*` tool availability for long-lived sessions — detect + nudge restart** | — (no open decision; ready to work) | — | — | `2.1` |
| P2 | `AI-CLI-102` | **Test-drive ai-cli end-to-end on the acn-windows machine** | — (no open decision; ready to work) | — | — | `2.0` |
| P2 | `AI-CLI-38` | **UAT: VPN-aware transport switching** | — (no open decision; ready to work) | — | — | `1.75` |
| P2 | `AI-CLI-65` | **Create CC statusline design doc — document statusline-command.sh** | — (no open decision; ready to work) | — | — | `1.5` |
| P2 | `AI-CLI-80` | **Remove session $ cost tracker from CC statusline** | — (no open decision; ready to work) | — | — | `1.35` |
| P2 | `AI-CLI-103` | **Expand `ai-cli` `config.toml` coverage** | — (no open decision; ready to work) | — | — | `0.92` |
| P2 | `AI-CLI-51` | **Create `ai-citation-validator` shared PyPI package** | — (no open decision; ready to work) | — | — | `0.91` |
| P2 | `AI-CLI-58` | **Complete Phase 3 alerting gaps** | — (no open decision; ready to work) | — | — | `0.84` |
| P2 | `AI-CLI-land-two-stranded-ai-cli-utils-rdxw` | **Land the two stranded ai-cli-utils branches: aicli-exit-fix and aicli-quiet-install, both ~88 behind main** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-apply-canonical-decision-record-faub` | **Apply canonical decision-record format across ai-cli-utils docs (fleet-wide sweep slice)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-ai-cli-utils-abvz` | **ai-cli-utils: make CLI fully OS-agnostic (Windows/macOS/Linux)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-ai-update-silently-drep` | **ai update silently converts an editable install into a copy, per source edit** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-pyproject-toml-committed-gdrw` | **pyproject.toml is committed as CRLF, so ai update dirties it on every POSIX box** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-make-ai-c-ytbw` | **Make ai c launch quiet: only reinstall when the installed code is actually stale** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-ai-session-adopt-cngk` | **ai session-adopt — fails when session ran in worktree (searches repo-root CC slug instead of worktree slug)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-202` | **Repair the broken local tmux install (loader error: libutempter.so.0 unresolved) so the 4 real-tmux launch tests run instead of skipping** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-199` | **Personal-machine check: rename the config section header if AI-CLI-189's old section is populated there** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-194` | **Sessions started outside ai c never get the stable task namespace, so they land in an ephemeral session-XXXX dir** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-185` | **ai-cli-utils' .beads/issues.jsonl has 5+ records absent from the local Dolt store -- bd export refuses to overwrite** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-187` | **pytest-timeout is declared in [dependency-groups], not [project.optional-dependencies], so `uv pip install -e ".[dev]"` silently omits it and --timeout becomes a no-op flag** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-186` | **A session carrying a stale worktree binding relocates its transcript on worktree exit, and there is no standalone repair for one already in that state.** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-183` | **bd-new -C <repo> resolves display-id prefix from shell cwd, not the -C target (3rd occurrence)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-156` | **Handoff v2 Phase 4: Channels bridge pilot (gated on preview-flag acceptance)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-155` | **Handoff v2 Phase 3: live NATS integration tests + failure-injection matrix** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-169` | **ai copier-update silently drops a patch hunk (false success) when target-repo content has drifted from template anchor lines** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-140` | **ai update --force silently converts an editable tool install into a copy** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-130` | **Auto-clean stale worktrees and branches that carry no unique work** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-128` | **Three canonical worktrees don't track origin/main — a sync check that reports 'in sync' while 46 commits behind** | — (no open decision; ready to work) | — | — | `0.83*` |
| P2 | `AI-CLI-32` | **CLI ergonomics and first-run UX audit** | — (no open decision; ready to work) | — | — | `0.77` |
| P2 | `AI-CLI-82` | **Auto-show task panel on CC session start and auto-restart** | — (no open decision; ready to work) | — | — | `0.77` |
| P2 | `AI-CLI-52` | **Re-record demo video for README and portfolio** | — (no open decision; ready to work) | — | — | `0.75` |
| P2 | `AI-CLI-105` | **`ai sync` reliability — root-cause the conflicts + observability + robust prevention (part of the CORE-46 deploy/sync program)** | — (no open decision; ready to work) | — | — | `0.62` |
| P2 | `AI-CLI-111` | **Review + reconcile `ai` CLI vs ai-core's `core` CLI — scope, ownership, interaction, optional-dependency boundary** | — (no open decision; ready to work) | — | — | `0.6` |
| P2 | `AI-CLI-93` | **Migrate the `ai` CLI to Typer — consolidate the existing click+argparse split (DESIGN-FIRST, feature-parity-gated)** | — (no open decision; ready to work) | — | — | `0.51` |
| P3 | `AI-CLI-prune-authorises-delete-gpjh` | **Prune authorises a delete from Path.exists(), which cannot distinguish a gone pid from an unreadable /proc** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-191` | **Read and action the four stash-migrate/* branches (plus the unmerged gemini-usage-tracking feature branch) before deleting any** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-190` | **Decide Renovate policy for ai-cli-utils and clear the six long-lived renovate/* remote branches durably** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-188` | **Decide S110 (try-except-pass) per site rather than adopting the family: 56 residual in src, 46 of them with no available justification** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-170` | **ai copier-update fails entirely on ai-dojo + ai-ide-macos (internal checkout/pathspec error, not a normal conflict)** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-149` | **Decide + possibly build Option F — fail-closed discriminating pre-launch clear for the Auto-update-failed banner** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-162` | **Fix mislinked task reference: workspace-sync-plan.md points at AI-CLI-64 (wrong issue), and vpn-transport-switching.md has stale status: draft despite being fully implemented** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-138` | **Fix ruff check errors (F841 x3, F811 x1) in scripts/generate_iterm2_icons.py** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-136` | **Decide disposition of 4 stashes on the remote build host's ai-cli-utils checkout** | — (no open decision; ready to work) | — | — | `0.83*` |
| P3 | `AI-CLI-142` | **Implement Tier 1 of user behavior telemetry design (ADHD nudge lifecycle events, fatigue score, analytics.db)** | — (no open decision; ready to work) | /Users/sergeiwallace/projects/ai-cli-utils/docs/designs/user-behavior-telemetry.md | — | `0.3` |

<!-- /doc:region name="review_queue" -->

<!-- doc:region name="autonomous_batch" kind="replaceable" -->

## Autonomous Batch

Ready, agent-safe work; ordered priority→impact. Only `agent_ready: true` items
belong here.

**0 items** — zero open issues in this store carry `autonomous_eligible: true` metadata, and `/next` Step 3 forbids inventing the flag.

<!-- /doc:region name="autonomous_batch" -->

<!-- doc:region name="provenance_log" kind="append_only" -->

## Provenance Log

| Date | Change | Source / notes |
|------|--------|----------------|
| YYYY-MM-DD | Created | Generated from `docs/docket/STUB.md` |
| 2026-08-20 | First real refresh (delegated to Codex implement) | 69 open (3 P0, 17 P1, 39 P2, 10 P3), 66 ready. Autonomous Batch empty — no autonomous_eligible issues. 7 review-bearing docs have no backing bd issue: docs/designs/vpn-transport-switching.md; docs/plans/cdp-plan.md; docs/plans/ai-cli-118-daemon-task-tool-self-healing-plan.md; docs/plans/iterm2-title-color-redesign-plan.md; docs/plans/proactive-project-registry-plan.md; docs/plans/iterm2-neighbor-color-plan.md; docs/audits/ai-cli-118-daemon-task-tool-self-healing-plan-audit.md. Not filed as new issues this pass — flagging for a human call. |

<!-- /doc:region name="provenance_log" -->
