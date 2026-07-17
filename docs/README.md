# Documentation Index

> Auto-generated from YAML frontmatter. Run `aido docs index` to refresh.

**51 docs** across 11 categories.

## Design

Architectural 'how' — system design, tradeoff analysis.

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [CC Statusline — Design Document](designs/cc-statusline.md) | active | claude-sonnet-4-6 | statusline, quota, iterm2, claude-code |
| [Citation Validation for ai gemini Research Output](designs/archive/citation-validation.md) | archived | claude-sonnet-4-6 2026-04-21 | citation-validation, gemini, research, lychee, semantic-scholar, AI-CLI-50 |
| [Claude Usage Telemetry — Token Tracking and Quota Pacing](designs/claude-usage-telemetry.md) | approved | R-5 deep-think 2026-04-01, opus synthesis 2026-04-01 | claude, quota, telemetry, token-tracking, pacing, AI-CLI-22, AI-CLI-23, AI-CLI-55, AI-CLI-56 |
| [Quota Notification System](designs/notification-system.md) | approved | session-2026-04-20 | quota, notifications, circus, ntfy, discord, process-management |
| [[Project Name] — Architecture & Design Philosophy](designs/ARCH-TEMPLATE.md) | active | <!-- claude-opus / human / etc. --> | architecture, platform, design-philosophy |
| [ai-cli-utils — Architecture](designs/architecture.md) | active | claude-sonnet-4-6 2026-04-18 | architecture, cli, gemini, quota, sync, nats, circus |
| [iTerm2 Tab Title and Color System — Design](designs/iterm2-title-color-system.md) | implemented | ai-cli-utils | iterm2, tab-title, tab-color, session-title, fleet, gemini, remote, mosh |
| [iTerm2 YAML Layout Templating System — Design](designs/iterm2-layout-system.md) | implemented | ai-cli-utils | iterm2, layout, yaml, dynamic-profiles, panes, tmux, templates |

## Plan

Tactical implementation — tasks, batches, gates.

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [AI-CLI-16 — Handoff Reliability Testing](plans/handoff-reliability-testing.md) | in_progress | session-2026-04-06 | handoff, reliability, testing, ai-cli-16 |
| [AI-CLI-46: Stabilize Programmatic API for aido Integration](plans/ai-cli-46-programmatic-api-plan.md) | complete | ai-cli-utils | api, programmatic, aido, versioning |
| [AI-CLI-68: Quota Scrape Format-Change Detection — Plan](plans/quota-scrape-format-detection-plan.md) | active | claude-sonnet-4-6 | quota, scrape, telemetry, statusline, db |
| [CDP Browser Debug Server — Implementation Plan](plans/cdp-plan.md) | draft | ai-cli-utils | cdp, chrome, devtools, browser, debugging |
| [Circus-Managed signal-watch](plans/circus-signal-watch-plan.md) | implemented | session-2026-04-01 | signal-watch, circus, handoff, process-management |
| [Gemini Deep Research — OAuth Fix & GCP Client Setup](plans/archive/gemini-deep-research-oauth-plan.md) | archived | ai-cli-utils | gemini, oauth, deep-research, gcp, ai-cli-45 |
| [Implementation Plan — Gemini Checkpoint-to-Chat Conversion](plans/archive/gemini-checkpoint-chat-conversion-plan.md) | archived | session-2026-04-08 | ai-cli, gemini, session-resume, checkpoint, chat-files |
| [Implementation Plan — Geo-Aware SSH Reverse Proxy for `ai gemini`](plans/archive/geo-aware-proxy-tunnel-plan.md) | archived | claude-sonnet-4-6 | ai-cli, ai-gemini, geo-restriction, ssh, socks-proxy, deep-research |
| [Implementation Plan — ai gemini Research Depth Tiers (--depth flag)](plans/archive/ai-gemini-research-depth-tiers.md) | archived | session-2026-04-04 | ai-cli, ai-gemini, research-pipeline, depth-tiers, track-a |
| [Resilient SSH Tunnels via autossh](plans/tunnel-plan.md) | implemented | session-2026-04-01 | tunnel, autossh, ssh, remote |
| [Skill Audit, Copier Automation, and Session Config Drift Prevention](plans/skill-audit-copier-automation-plan.md) | complete | ai-cli-utils | session-config, skills, copier, project-template, automation, auto-restart |
| [Terminal Demo Video — Implementation Plan](plans/demo-video-plan.md) | in_progress | ai-cli-utils | demo, gif, screencapture, ffmpeg, readme |
| [Windows Out-of-Box Support — Implementation Plan](plans/windows-support-plan.md) | complete | ai-cli-utils | windows, portability, cross-platform, AI-CLI-29 |
| [ai ws — Workspace-wide git pull/rebase for all repos and worktrees](plans/workspace-sync-plan.md) | in_progress | internal | git, worktrees, workspace, sync |
| [iTerm2 + ntfy Session Status Integration — Implementation Plan](plans/iterm2-ntfy-session-status-plan.md) | active | internal | iterm2, ntfy, notifications, session-status, ai-cli, nats |
| [iTerm2 Fleet Management Configuration — Implementation Plan](plans/iterm2-fleet-config-plan.md) | active | internal | iterm2, terminal, fleet-management, ai-cli, configuration |
| [iTerm2 Neighbor-Aware Pane Color — Plan](plans/iterm2-neighbor-color-plan.md) | draft | ai-cli-utils | iterm2, tab-color, pane-color, layout, color-collision, spatial-awareness |
| [iTerm2 Smart Tab/Window Titles](plans/iterm2-smart-titles-plan.md) | approved | ai-cli-utils | iterm2, fleet, tab-title, window-title, session-title |
| [iTerm2 Title & Color System Redesign — Plan](plans/iterm2-title-color-redesign-plan.md) | implemented | ai-cli-utils | iterm2, tab-title, tab-color, session-title, fleet, gemini, remote, mosh, research |
| [quota_watch NATS Listener — Implementation Plan](plans/quota-watch-nats-listener-plan.md) | implemented | claude-sonnet-4-6 | quota, nats, quota-watch, ai-cli-57 |
| [sync repo pull — Auto-pull affected project repos after ai sync pull](plans/sync-repo-pull-plan.md) | approved | sw-2 | sync, git, worktrees, repos, safety |

## Research

Synthesized findings — comparisons, best practices, deep dives.

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [Claude Code subscription quota surfaces (2026) — statusline rate_limits vs /usage scraping](research/claude-quota-statusline-rate-limits-2026.md) | complete | opus-manual-2026-07-13 | research, claude-code, quota, statusline, rate_limits, AI-CLI-98, AI-CLI-94, AIH-120 |
| [GitHub Repository Automation & Ecosystem Tooling for Python CLI Projects](research/github-repo-automation.md) | complete | opus-researcher-2026-03-29 | github, automation, ci-cd, bots, open-source, python, cli |
| [Open-Source Python CLI Package Best Practices](research/open-source-package-best-practices.md) | complete | opus-researcher-2026-03-29 | open-source, python, cli, github, pypi, best-practices |
| [Terminal Tab/Pane Title, Color, and Icon Customization for AI Fleet Management — Research](research/iterm2-terminal-customization-research.md) | complete | gemini-deep-think-2026-04-02 | iterm2, terminal, tab-title, tab-color, session-title, fleet, gemini, remote, mosh, tmux, wezterm, kitty, ghostty |
| [iTerm2 Power User Configuration for AI Agent Fleet Management](research/iterm2-fleet-management-config.md) | complete | claude-opus-2026-03-29 | research, iterm2, terminal, fleet-management, ssh, tmux, developer-tools |

## Test

Testing templates and tracking.

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [UAT — iTerm2 Tab Title and Color System Redesign](test/uat-iterm2-title-color-redesign.md) | pending | ai-cli-utils | iterm2, uat, tab-title, tab-color, fleet, gemini |

## Bugs

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [AI-CLI-56: Duplicate prompt boxes / statusline in scrollback buffer](bugs/statusline-scrollback-duplicates.md) | fix-deployed | ai-cli-utils | bug, statusline, quota, scrollback |
| [[AI-CLI-70] Git worktree index corruption — hundreds of D/untracked changes after rebase](bugs/worktree-index-corruption.md) | fix-deployed | ai-cli-utils | bug, git, worktree, recurring |
| [[BUG-001] iTerm2 tab title and color system — multiple bugs](bugs/iterm2-title-color-system.md) | uat-in-progress | ai-cli-utils | iterm2, tab-title, tab-color, session-title, gemini, remote, mosh |
| [[BUG-002] Automatic tmux injection triggers CC rewind conversation TUI](bugs/prompt-injection-rewind-menu.md) | investigating | ai-cli-utils | bug, injection, tmux, signal-watch, watcher, cc-session |

## Designs

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [VPN-Aware Transport Switching](designs/vpn-transport-switching.md) | draft | internal | mosh, ssh, vpn, transport, session-management |

## Guide

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [Optional: NATS Setup for Fleet Messaging](guides/nats-setup.md) | current | internal | nats, messaging, fleet, optional, setup |

## Plans

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [Gemini API Cost and Usage Tracking Overhaul](plans/archive/gemini-usage-tracking-plan.md) | archived | internal | ai-gemini, usage-tracking, billing, deep-research, quota, ai-cli-41 |
| [Going Public — Repository Automation & Hardening Plan](plans/going-public-plan.md) | APPROVED | R-2 research | open-source, github, automation, ci-cd |
| [Proactive Project Registry Registration — Implementation Plan](plans/proactive-project-registry-plan.md) | DRAFT | claude-sonnet-4-6 | registry, config, setup, copier, multi-user |
| [main.py Refactor — Module Extraction + CLI Dispatch Redesign](plans/main-py-refactor-plan.md) | implemented | internal | refactor, architecture, cli, modules |

## Procedures

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [AC Writing Practices](procedures/ac-writing-practices.md) | active | AIDO-69 | ac, acceptance-criteria, plan-docs, quality, feature-parity |
| [Claude Token Efficiency Guide](procedures/claude-token-efficiency.md) | active | internal | claude, tokens, efficiency, models, quota |
| [Reasoning Checkpoints](procedures/reasoning-checkpoints.md) | active | internal | reasoning, checkpoints, quality, agents, claude-code |

## Research-Prompts

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [Research Prompt Registry](research/prompts/research-prompt-registry.md) | active | project-template | research, prompts |

## Tools

| Doc | Status | Source | Tags |
|-----|--------|--------|------|
| [ai CLI Design — Subcommands Reference](tools/ai-cli-usage.md) | current | internal | ai-cli, cli, tmux, session-management, reference |
| [iTerm2 Setup & Shortcuts](tools/iterm2-setup.md) | active | internal | iterm2, terminal, shortcuts, configuration, fleet-management |
