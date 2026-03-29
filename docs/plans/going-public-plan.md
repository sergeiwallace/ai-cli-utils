---
title: "Going Public — Repository Automation & Hardening Plan"
category: plans
tags: [open-source, github, automation, ci-cd]
status: APPROVED
source: "R-2 research"
---

# Going Public — Repository Automation & Hardening Plan

**Status:** APPROVED
**Created:** 2026-03-29
**Research:** [`docs/research/github-repo-automation.md`](../research/github-repo-automation.md)
**Task:** AI-CLI-6

## Table of Contents

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
- [Execution Order](#execution-order)
- [Deferred Items](#deferred-items)
- [Approval Log](#approval-log)

## Overview

Implement all "do now" and "do before going public" recommendations from R-2 research. Goal: repo is ready to flip public with full automation, quality gates, and professional presentation.

**Scope:** CI enhancements, GitHub Release automation, branch protection, pre-commit, Renovate (replacing Dependabot), issue/PR templates, CodeQL, Codecov, secret scan, initial GH Release.

**Out of scope:** Release Drafter (deferred to roadmap), stale bot, All Contributors, auto-labeler (all deferred until community grows), pyright (separate task — needs type annotation audit first).

## Task Breakdown

### T-1: CI matrix + coverage reporting

**Changes:**
- `ci.yml`: add Python 3.11/3.13 to test matrix via `strategy.matrix`
- `ci.yml`: add `--cov=ai_cli --cov-report=xml` to pytest step
- `ci.yml`: add `codecov/codecov-action@v5` upload step (3.12 only)
- `ci.yml`: upgrade `astral-sh/setup-uv` from v4 to v5
- `pyproject.toml`: no changes needed (pytest-cov already in dev deps)

**AC:**
- [ ] Tests run on 3.11, 3.12, 3.13
- [ ] Coverage XML generated and uploaded to Codecov
- [ ] setup-uv v5 with built-in caching

### T-2: GitHub Release automation in publish workflow

**Changes:**
- `publish.yml`: add `release` job after `publish` that extracts changelog and creates GH Release via `softprops/action-gh-release@v2`

**AC:**
- [ ] Tag push creates both PyPI publish AND GitHub Release
- [ ] Release body contains changelog section for that version

### T-3: Pre-commit config

**Changes:**
- Create `.pre-commit-config.yaml` with ruff + basic hygiene hooks
- Update CONTRIBUTING.md to mention pre-commit setup

**AC:**
- [ ] `pre-commit run --all-files` passes
- [ ] Ruff hooks match CI config

### T-4: Replace Dependabot with Renovate config

**Changes:**
- Create `renovate.json5` with automerge for minor/patch, GH Actions grouping, lock file maintenance
- Delete `.github/dependabot.yml`
- Close open Dependabot PRs

**Note:** Renovate GitHub App must be installed manually on the repo. Config file is all we ship here.

**AC:**
- [ ] `renovate.json5` committed
- [ ] `dependabot.yml` removed
- [ ] Open Dependabot PRs closed

### T-5: Issue and PR templates

**Changes:**
- Create `.github/ISSUE_TEMPLATE/bug-report.yml` (YAML form)
- Create `.github/ISSUE_TEMPLATE/feature-request.yml` (YAML form)
- Create `.github/ISSUE_TEMPLATE/config.yml` (blank issues + security link)
- Create `.github/pull_request_template.md`

**AC:**
- [ ] All 4 template files exist
- [ ] YAML validates

### T-6: Branch protection + merge settings

**Changes (via `gh` CLI):**
- Enable required status checks (lint, test) on main
- Enable linear history
- Disable force pushes and branch deletion
- Set squash as default merge method
- Enable auto-delete head branches

**AC:**
- [ ] Branch ruleset applied to main
- [ ] Merge settings configured

### T-7: Secret scan + going-public prep

**Changes:**
- Run gitleaks or manual grep on full git history
- Verify no real secrets committed
- Create initial GitHub Release for v0.1.1 with changelog
- Verify .gitignore completeness (coverage.xml, .coverage added)

**AC:**
- [ ] No secrets in git history (confirmed)
- [ ] v0.1.1 GitHub Release exists
- [ ] .gitignore covers all build/test artifacts

## Execution Order

All tasks are independent except T-7 (must be last — it creates the GH Release after CI is finalized).

1. T-1 (CI matrix + coverage)
2. T-2 (release automation)
3. T-3 (pre-commit)
4. T-4 (Renovate)
5. T-5 (issue/PR templates)
6. T-6 (branch protection) — after CI changes land so status checks exist
7. T-7 (secret scan + GH Release) — final step

## Deferred Items

| Item | Roadmap task | Trigger |
|------|-------------|---------|
| Release Drafter | AI-CLI-7 | When project gets regular external PRs |
| Stale bot | — | When open issues > 50 |
| All Contributors | — | When contributors > 5 |
| Auto-labeler | — | When PRs > 20/month |
| pyright CI | AI-CLI-8 | After type annotation audit |
| Codecov badge in README | — | When coverage > 80% |

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-03-29 | Plan approved | User approved autonomous implementation |
