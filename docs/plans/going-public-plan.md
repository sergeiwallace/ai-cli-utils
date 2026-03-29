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
- [Completion Status](#completion-status)
- [AI-CLI-9: Flip Repo Public](#ai-cli-9-flip-repo-public--step-by-step)
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

## Completion Status

| Task | Status | Notes |
|------|--------|-------|
| T-1 | Done | CI matrix 3.11/3.12/3.13 + Codecov upload (needs token) |
| T-2 | Done | softprops/action-gh-release in publish workflow |
| T-3 | Done | .pre-commit-config.yaml + pre-commit in dev deps |
| T-4 | Done | renovate.json5 committed, dependabot.yml removed, PRs closed |
| T-5 | Done | Bug report, feature request, config, PR template |
| T-6 | Partial | Auto-delete branches + auto-merge enabled. Rulesets blocked (needs public repo) |
| T-7 | Partial | Secret scan clean, GH Release v0.1.1 created, .gitignore updated. Remaining: Codecov token, CodeQL |

## AI-CLI-9: Flip Repo Public — Step-by-Step

Remaining steps to complete before and after making the repo public. Do them in order.

### Before flipping to public

#### Step 1: Install Renovate GitHub App

1. Go to https://github.com/apps/renovate
2. Click "Install" and select the `sergeiwallace/ai-cli-utils` repo
3. Renovate will open an onboarding PR — the `renovate.json5` already in the repo should be detected automatically
4. Merge or close the onboarding PR (config is already committed)
5. Verify: Renovate opens a "Dependency Dashboard" issue in the repo

#### Step 2: Set up Codecov

1. Go to https://codecov.io and sign in with GitHub
2. Add the `sergeiwallace/ai-cli-utils` repo
3. Copy the `CODECOV_TOKEN` from Codecov's repo settings
4. In GitHub: repo Settings > Secrets and variables > Actions > New repository secret
5. Name: `CODECOV_TOKEN`, Value: paste the token
6. Verify: push a commit or re-run CI — the "Upload coverage" step should succeed (currently warns but doesn't fail)

#### Step 3: Generate social preview image

1. Go to https://socialify.git.ci/sergeiwallace/ai-cli-utils
2. Configure: enable description, language, stars, forks, issues
3. Download the 1280x640px image
4. In GitHub: repo Settings > Social preview > Upload image

#### Step 4: Final pre-public review (CC session can help)

Ask a CC session to verify these before flipping:

- [ ] `grep -rn "178.104\|hetzner-ai-dev\|sergeipwallace" src/ tests/` — no personal infra references in source
- [ ] `uv tool install ai-cli-utils && ai --help` — clean install from PyPI works
- [ ] README renders correctly on GitHub (all badges, all sections, no broken links)
- [ ] CHANGELOG version links point to correct compare URLs
- [ ] LICENSE year and name are correct
- [ ] pyproject.toml URLs all resolve (Homepage, Repository, Changelog, Issues)
- [ ] GitHub repo description and topics match README

### Flipping to public

#### Step 5: Make repo public

1. GitHub repo Settings > Danger Zone > Change repository visibility
2. Select "Make public" and confirm
3. **This is irreversible for the git history** — all commits become public. Secret scan was already done (clean).

### After flipping to public

#### Step 6: Enable branch protection rulesets

Run this command (failed earlier because repo was private):

```bash
gh api repos/sergeiwallace/ai-cli-utils/rulesets -X POST --input - <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "required_linear_history"
    },
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_status_checks_policy": false,
        "required_status_checks": [
          {"context": "lint"},
          {"context": "test (3.12)"}
        ]
      }
    }
  ]
}
JSON
```

Or via GitHub UI: Settings > Rules > Rulesets > New ruleset:
- Target: `main` branch
- Rules: require status checks (`lint`, `test (3.12)`), require linear history, block deletions, block force pushes

#### Step 7: Configure merge settings

Most already done. Verify in Settings > General > Pull Requests:
- [ ] Allow merge commits: **disabled**
- [x] Allow squash merging: **enabled** (default)
- [x] Allow rebase merging: **enabled**
- [x] Auto-delete head branches: **enabled** (already set)

#### Step 8: Enable code security features

In Settings > Code security and analysis:
- [x] Dependabot alerts: **enable**
- [x] Dependabot security updates: **enable**
- [x] Code scanning (CodeQL): **enable default setup** (no workflow file needed for Python)
- [x] Secret scanning: **enable**
- [x] Push protection: **enable** (blocks pushes containing secrets)

#### Step 9: Post-public verification

1. **Verify badges render** — check README on GitHub, all 5 shields.io badges should resolve
2. **Test external clone + install:**
   ```bash
   cd /tmp
   git clone https://github.com/sergeiwallace/ai-cli-utils.git
   cd ai-cli-utils
   uv tool install .
   ai --help
   ```
3. **Check Security tab** — verify no secret scanning alerts
4. **Verify Codecov** — check that coverage data appears at codecov.io
5. **Add Codecov badge to README** — only if coverage > 80%:
   ```markdown
   [![codecov](https://codecov.io/gh/sergeiwallace/ai-cli-utils/graph/badge.svg)](https://codecov.io/gh/sergeiwallace/ai-cli-utils)
   ```

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
