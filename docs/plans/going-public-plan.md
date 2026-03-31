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
        "strict_required_status_checks_policy": false,
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
| Codecov badge in README | — | Done (coverage reached 91%) |
| SSH integration tests | AI-CLI-12 | See below |

## AI-CLI-12: Coverage push to ~97-98%

Three parallel tracks. Do in order — re-evaluate SSH need after NATS CI results.

### Track A: `os.execvp` coverage (main.py lines 1162-1270)

**Approach:** combination of mocking + `# pragma: no cover`.

- **Mock `os.execvp`** for paths where argument construction is non-trivial and worth asserting:
  - Remote mode (`-R`) — verifies SSH/mosh args, session name, project path
  - Sandbox flag logic — verifies `--dangerously-skip-permissions` conditionally included
  - `--once` mode — verifies tmux new-session args
  - Existing session re-attach — verifies `attach-session -d` called
- **`# pragma: no cover`** on simple/obvious exec lines where asserting the args would be tautological (e.g. bare `os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])`)

Add tests to `tests/test_main.py`. Expected gain: main.py 93% → ~96%, TOTAL ~91% → ~93%.

### Track B: NATS CI service container (sync.py bulk gap)

Add a NATS service container to `ci.yml` so unit tests can use a real NATS server. This removes the need to mock NATS in sync/messaging tests, covering the publish/subscribe paths that are currently skipped.

**Change to `ci.yml` test job:**
```yaml
services:
  nats:
    image: nats:latest
    ports:
      - 4222:4222
```

No code changes needed — tests already have `@pytest.mark.skipif` guards or mock NATS; with a real server available they'll hit the live paths.

**Expected gain:** sync.py 87% → ~92-93%, TOTAL ~93% → ~95%. Re-evaluate SSH integration need after seeing actual numbers.

### Track C: SSH Integration Tests (Hetzner)

**Decision:** Deferred until after Track B. If NATS CI covers most of sync.py, SSH tests may only add 1-2% and may not be worth the operational complexity. Re-evaluate after Track B is implemented.

If proceeding, **Decision:** Option D — separate CI job with `continue-on-error: true`. Failures get flagged as P0/P1 in the session config and investigated. The CLAUDE.md guardrails mean failures won't be silently ignored.

### Setup steps

#### Step 1: Generate a dedicated CI keypair

On the Hetzner server:
```bash
ssh-keygen -t ed25519 -C "ai-cli-utils CI" -f ~/.ssh/ci_integration -N ""
```

#### Step 2: Restrict the key on the server

In `~/.ssh/authorized_keys` on Hetzner, add the public key with a `command=` restriction:
```
command="cd /home/sergei/projects/ai-cli-utils && bash",restrict ssh-ed25519 AAAA... ai-cli-utils CI
```

This means even if the key leaks, it can only run a constrained shell in the project directory.

#### Step 3: Add secrets to GitHub

In GitHub repo Settings → Secrets and variables → Actions:
- `INTEGRATION_SSH_KEY` — contents of `~/.ssh/ci_integration` (private key)
- `INTEGRATION_SSH_HOST` — `178.104.70.139`
- `INTEGRATION_SSH_USER` — `sergei`

#### Step 4: Add integration workflow

Create `.github/workflows/integration.yml`:

```yaml
name: Integration Tests

on:
  push:
    branches: [main]
  schedule:
    - cron: '0 6 * * *'  # daily at 6am UTC

permissions:
  contents: read

jobs:
  integration:
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v6
      - name: Check SSH reachability
        id: ssh_check
        run: |
          if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
            -i <(echo "${{ secrets.INTEGRATION_SSH_KEY }}") \
            ${{ secrets.INTEGRATION_SSH_USER }}@${{ secrets.INTEGRATION_SSH_HOST }} \
            exit 0 2>/dev/null; then
            echo "reachable=false" >> $GITHUB_OUTPUT
          else
            echo "reachable=true" >> $GITHUB_OUTPUT
          fi
      - uses: astral-sh/setup-uv@v7
        if: steps.ssh_check.outputs.reachable == 'true'
        with:
          python-version: "3.12"
      - name: Run integration tests
        if: steps.ssh_check.outputs.reachable == 'true'
        env:
          INTEGRATION_SSH_KEY: ${{ secrets.INTEGRATION_SSH_KEY }}
          INTEGRATION_SSH_HOST: ${{ secrets.INTEGRATION_SSH_HOST }}
          INTEGRATION_SSH_USER: ${{ secrets.INTEGRATION_SSH_USER }}
        run: uv run pytest -m integration --tb=short -q
      - name: Skip (server unreachable)
        if: steps.ssh_check.outputs.reachable == 'false'
        run: echo "Hetzner server unreachable — skipping integration tests"
```

#### Step 5: Write the integration tests

Add `@pytest.mark.integration` tests to `tests/test_sync_integration.py` covering:
- `sync_push` over real SSH
- `sync_pull` over real SSH
- Conflict detection

Mark with `pytest.ini` or `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = ["integration: requires Hetzner SSH access"]
```

#### Step 6: Session config update

Add to `CLAUDE.md` (ai-cli-utils project):
> If the `integration` CI job is failing, create a P1 task `[AI-CLI-integration-fix]` and investigate before other work.

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-03-29 | Plan approved | User approved autonomous implementation |
