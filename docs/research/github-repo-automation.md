---
title: "GitHub Repository Automation & Ecosystem Tooling for Python CLI Projects"
category: research
tags: [github, automation, ci-cd, bots, open-source, python, cli]
status: complete
source: "opus-researcher-2026-03-29"
---

# GitHub Repository Automation & Ecosystem Tooling for Python CLI Projects

> Researched: 2026-03-29 | Confidence: high

## Table of Contents

- [Summary](#summary)
- [1. GitHub Apps and Bots](#1-github-apps-and-bots)
- [2. GitHub Release Automation](#2-github-release-automation)
- [3. Branch Protection and Merge Settings](#3-branch-protection-and-merge-settings)
- [4. CI Enhancements](#4-ci-enhancements)
- [5. Dependabot vs Renovate](#5-dependabot-vs-renovate)
- [6. Pre-commit and Contributor Tooling](#6-pre-commit-and-contributor-tooling)
- [7. Going Public Checklist](#7-going-public-checklist)
- [8. Issue and PR Templates](#8-issue-and-pr-templates)
- [Consolidated Recommendations](#consolidated-recommendations)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Appendix: Research Prompt](#appendix-research-prompt)

## Summary

This document evaluates GitHub ecosystem tooling, automation, and repository configuration for `ai-cli-utils`, a solo-maintained Python 3.11+ CLI tool. It covers GitHub Apps/bots, release automation, branch protection, CI enhancements, dependency management (Dependabot vs Renovate), pre-commit hooks, going-public preparation, and issue/PR templates. Each recommendation is classified by urgency and ROI for a solo maintainer. The project already has CI (lint + test on single Python version), PyPI publish on tag, Dependabot, and community files (README, CONTRIBUTING.md, SECURITY.md).

---

## 1. GitHub Apps and Bots

### 1.1 Renovate (dependency updates)

**What it does:** Automated dependency update PRs with native grouping, automerge, and scheduling. Runs as a GitHub App or self-hosted.

**Maintenance burden:** Low after initial config. The `renovate.json` file requires upfront thought but then runs hands-off. Dashboard issue in your repo tracks all pending updates. [VERIFIABLE FACT: https://docs.renovatebot.com/configuration-options/]

**Verdict: Do now** (replaces Dependabot -- see Section 5 for full comparison)

### 1.2 CodeQL / GitHub Code Scanning

**What it does:** Static analysis for security vulnerabilities. GitHub's built-in SAST tool. Free for public repositories. Runs as a GitHub Action. For Python, it requires no build step -- analysis is automatic. [VERIFIABLE FACT: https://github.com/github/codeql-action]

**Maintenance burden:** Near-zero. Enable default setup via repository Settings > Code security and analysis, or add a workflow file. Occasional false positives require dismissal.

**Verdict: Do before going public.** Free, zero-config for Python, and having the "Code scanning" badge in your Security tab signals professionalism. The default setup (no workflow file needed) takes under 2 minutes.

### 1.3 Codecov

**What it does:** Coverage reporting service. Receives coverage XML from CI, tracks coverage over time, posts PR comments showing coverage delta, provides badge. [VERIFIABLE FACT: https://about.codecov.io/blog/python-code-coverage-using-github-actions-and-codecov/]

**Maintenance burden:** Low. Requires a `CODECOV_TOKEN` repository secret (free for public repos) and a `codecov/codecov-action@v5` step in CI.

**Verdict: Do now.** The project already has `pytest-cov` in dev dependencies. Add the upload step to CI and the badge to README once coverage is at a number you are comfortable displaying publicly. Codecov's PR comments showing coverage delta on each PR are the real value -- the badge is secondary.

### 1.4 Release Drafter

**What it does:** Automatically drafts GitHub Release notes as PRs are merged. Categorizes changes by PR labels (features, bug fixes, etc.). Produces a draft release that you publish when ready. [VERIFIABLE FACT: https://github.com/marketplace/actions/release-drafter]

**Maintenance burden:** Low-medium. Requires a `.github/release-drafter.yml` config and consistent PR labeling. For a solo maintainer who commits directly to main (no PRs for most work), the value is limited since Release Drafter is PR-driven.

**Verdict: Skip.** For a solo project where most changes go directly to main without PRs, Release Drafter provides little value. Use `softprops/action-gh-release` with a manually maintained CHANGELOG.md instead (see Section 2).

### 1.5 Stale Bot (actions/stale)

**What it does:** Automatically labels and closes issues/PRs with no activity after a configured period. The original Probot Stale app is deprecated; `actions/stale` is the official replacement. [VERIFIABLE FACT: https://github.com/actions/stale]

**Maintenance burden:** Low. A cron-triggered workflow with configurable thresholds.

**Verdict: Do when community grows.** A solo-maintained project with few issues does not need automated stale management. If you get to 50+ open issues and cannot triage manually, add it then. Premature stale bots on small projects annoy contributors.

### 1.6 All Contributors

**What it does:** Automates contributor acknowledgment in README via a bot command (`@all-contributors please add @user for code`). Tracks code, docs, design, and other contribution types. [VERIFIABLE FACT: https://allcontributors.org/]

**Maintenance burden:** Minimal once installed.

**Verdict: Do when community grows.** For a solo-maintained project, this is performative. Add it when you have 5+ contributors and want to recognize non-code contributions.

### 1.7 actions/labeler (Auto-label PRs)

**What it does:** Automatically labels PRs based on file paths changed (e.g., `docs/**` gets `documentation` label, `src/**` gets `code` label). [VERIFIABLE FACT: https://github.com/actions/labeler]

**Maintenance burden:** Low. Requires `.github/labeler.yml` mapping paths to labels.

**Verdict: Do when community grows.** Useful when you have enough PRs that manual labeling is tedious. For a solo project with occasional external PRs, manual labeling is fine.

### 1.8 Auto-merge Bot

**What it does:** Automatically merges PRs that pass CI and have a specific label (e.g., `automerge`). Common implementations: `pascalgn/automerge-action`, GitHub's built-in auto-merge feature, or Renovate's native automerge. [VERIFIABLE FACT: https://github.com/pascalgn/automerge-action]

**Maintenance burden:** Low, but requires branch protection with required status checks to be safe.

**Verdict: Do now -- but use Renovate's built-in automerge** rather than a separate bot. Renovate can automerge its own dependency update PRs after CI passes, which is the primary use case. GitHub's native auto-merge (repository settings) handles the rest. No need for a third-party action.

### Summary Table

| Tool | What | Effort | Verdict |
|------|------|--------|---------|
| Renovate | Dependency updates | Low | **Do now** |
| CodeQL | Security scanning | Near-zero | **Do before going public** |
| Codecov | Coverage tracking | Low | **Do now** |
| Release Drafter | Auto-draft release notes | Low-medium | **Skip** |
| actions/stale | Close inactive issues | Low | **Do when community grows** |
| All Contributors | Contributor credits | Minimal | **Do when community grows** |
| actions/labeler | Auto-label PRs | Low | **Do when community grows** |
| Auto-merge bot | Merge passing PRs | Low | **Do now** (via Renovate + GitHub native) |

---

## 2. GitHub Release Automation

### 2.1 Options Compared

| Approach | Pros | Cons | Best for |
|----------|------|------|----------|
| **`softprops/action-gh-release@v2`** | Simple, tag-triggered, supports `body_path` for changelog extraction, uploads assets | No automatic changelog generation | Solo projects with manual changelogs |
| **Release Drafter** | Auto-categorizes by PR labels, draft-then-publish flow | PR-driven (useless if you commit to main directly), requires labeling discipline | Teams with PR-based workflows |
| **`gh release create` in CI** | No third-party action dependency, full control | More scripting required | Projects wanting minimal dependencies |
| **Manual `gh release create`** | Maximum control, no CI config needed | Easy to forget, no automation | Very infrequent releases |

[VERIFIABLE FACT: softprops/action-gh-release docs at https://github.com/softprops/action-gh-release]

### 2.2 Recommended Approach: softprops/action-gh-release with CHANGELOG Extraction

For `ai-cli-utils`, the best approach is `softprops/action-gh-release` triggered by the same tag push that publishes to PyPI. Extract the current version's changelog section and use it as the release body.

```yaml
# .github/workflows/publish.yml — add a release job after the publish job
  release:
    runs-on: ubuntu-latest
    needs: [publish]
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Extract changelog for current version
        id: changelog
        run: |
          # Extract the section between the current version header and the next version header
          VERSION="${GITHUB_REF_NAME#v}"
          awk "/^## \[${VERSION}\]/{found=1; next} /^## \[/{if(found) exit} found{print}" \
            CHANGELOG.md > release_notes.md
      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          body_path: release_notes.md
          generate_release_notes: false
```

**Why this over alternatives:**

- **Fits the existing workflow.** The project already uses tag-push to trigger PyPI publish. Adding a release job to the same workflow is natural.
- **CHANGELOG.md is the single source of truth.** No drift between what PyPI shows and what GitHub Releases shows.
- **No PR labeling discipline required.** Solo maintainers committing directly to main do not benefit from PR-label-based changelog generation. [SYNTHESIZED INFERENCE: based on the project's commit-to-main workflow described in CLAUDE.md]

### 2.3 Alternative: gh CLI in CI

If you want zero third-party action dependencies:

```yaml
      - name: Create GitHub Release
        run: gh release create "${{ github.ref_name }}" --title "${{ github.ref_name }}" --notes-file release_notes.md
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Both approaches work. `softprops/action-gh-release` has 7K+ stars and is well-maintained. The `gh` CLI approach has no dependency but requires the `GH_TOKEN` env var. [VERIFIABLE FACT: gh CLI docs at https://cli.github.com/manual/gh_release_create]

---

## 3. Branch Protection and Merge Settings

### 3.1 Recommended Rules for main

For a solo-maintained project that wants quality gates without blocking its own workflow: [SYNTHESIZED INFERENCE: based on GitHub docs and solo-maintainer community discussion at https://github.com/orgs/community/discussions/23727]

| Rule | Setting | Rationale |
|------|---------|-----------|
| **Require status checks to pass** | Yes | Prevents merging broken code. Set `lint` and `test` jobs as required. |
| **Require branches to be up to date** | No | Adds friction for a solo maintainer. CI re-runs on every rebase are wasteful when you are the only committer. |
| **Require pull request reviews** | No | You cannot review your own PRs. Setting this to 0 reviews while still requiring PRs adds overhead without benefit for solo work. |
| **Require linear history** | Yes | Enforces rebase merges. Keeps `git log` clean and bisectable. |
| **Allow force pushes** | No | Protect against accidental history rewrite. |
| **Allow deletions** | No | Protect main from accidental deletion. |
| **Include administrators** | Yes (when going public) | Ensures you follow the same rules as contributors. Can bypass for emergencies. |

### 3.2 Merge Settings (Repository Settings)

| Setting | Recommendation |
|---------|---------------|
| **Allow merge commits** | No (disable) |
| **Allow squash merging** | Yes (default for PRs) |
| **Allow rebase merging** | Yes |
| **Default merge method** | Squash |
| **Auto-delete head branches** | Yes |
| **Suggest updating branches** | Yes |

**Rationale:** Squash merging keeps main clean when external contributors submit multi-commit PRs. Rebase merging is useful for your own feature branches where you want to preserve individual commits. Auto-delete head branches prevents branch clutter. [INDUSTRY HEURISTIC]

### 3.3 Rulesets vs. Branch Protection Rules

GitHub introduced Repository Rulesets as a more flexible replacement for branch protection rules. Rulesets support the same features plus org-level rules, tag protection, and bypass lists. For a solo project, either works. If setting up from scratch, use Rulesets -- they are the forward-looking option. [VERIFIABLE FACT: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches]

---

## 4. CI Enhancements

### 4.1 Python Version Matrix

The project claims Python 3.11+ support in `pyproject.toml` classifiers but only tests on 3.12. This is a gap.

**Recommended matrix:**

```yaml
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv sync --dev
      - run: uv run pytest --tb=short -q
```

**Why 3.11/3.12/3.13:** The project declares `requires-python = ">=3.11"` and includes classifiers for all three. Test what you claim to support. [INDUSTRY HEURISTIC]

**Why not 3.10 or earlier:** The `>=3.11` constraint is already set. No reason to test unsupported versions.

### 4.2 Coverage Reporting

Add coverage collection and upload to the test matrix:

```yaml
      - run: uv run pytest --cov=ai_cli --cov-report=xml --tb=short -q
      - name: Upload coverage
        if: matrix.python-version == '3.12'
        uses: codecov/codecov-action@v5
        with:
          file: coverage.xml
          token: ${{ secrets.CODECOV_TOKEN }}
```

Upload from a single matrix entry (3.12) to avoid duplicate reports. The `CODECOV_TOKEN` is required even for public repos as of 2024. [VERIFIABLE FACT: https://github.com/codecov/codecov-action — token requirement documented in v5 README]

**Coverage badge threshold:** Do not add a coverage badge to the README until coverage is above 80%. A low coverage badge signals "we started measuring but do not care enough to improve." [INDUSTRY HEURISTIC]

### 4.3 Caching with uv

The `astral-sh/setup-uv` action provides built-in caching. Ensure you are using v5+ (the current project uses v4):

```yaml
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
          # Caching is enabled by default in v5+
```

The action caches the uv package cache directory (`~/.cache/uv`), keyed on OS + `uv.lock` hash. This avoids re-downloading wheels on every CI run. [VERIFIABLE FACT: https://github.com/astral-sh/setup-uv — caching docs in README]

**Alternative:** `hynek/setup-cached-uv` adds calendar-week expiry and automatic `uv cache prune --ci` before saving. Worth considering if cache sizes become an issue. [VERIFIABLE FACT: https://github.com/hynek/setup-cached-uv]

### 4.4 Artifact Retention

GitHub Actions defaults to 90-day artifact retention. For a CLI project that publishes to PyPI, CI artifacts (build outputs, coverage reports) have limited long-term value.

**Recommendation:** Leave the default. If storage becomes an issue, set `retention-days: 7` on `upload-artifact` steps. [INDUSTRY HEURISTIC]

### 4.5 Type Checking in CI

**Should you add pyright or mypy?**

| Factor | Assessment |
|--------|-----------|
| **Current state** | Project declares `Typing :: Typed` classifier. Unknown how complete type annotations are. |
| **pyright vs mypy** | Pyright is faster (significant for CI) and stricter. mypy has more plugins but that is irrelevant for a CLI tool without Django/SQLAlchemy. [VERIFIABLE FACT: https://github.com/microsoft/pyright/blob/main/docs/mypy-comparison.md] |
| **CI time impact** | Pyright adds 5-15 seconds to CI. Negligible. |
| **Maintenance burden** | Medium. Type errors require fixing or explicit ignores. Initial setup may surface many issues if annotations are incomplete. |

**Verdict: Do now, with pyright in basic mode.** Add `pyright` to dev dependencies and a CI step. Start with `basic` type checking mode to avoid being overwhelmed by strict-mode findings on an existing codebase. Tighten later.

```yaml
  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync --dev
      - run: uv run pyright src/
```

Add to `pyproject.toml`:

```toml
[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
```

[SYNTHESIZED INFERENCE: pyright in basic mode is the best risk/reward for an existing Python 3.11+ CLI project. It catches real bugs (wrong argument types, missing attributes) without the false-positive noise of strict mode.]

---

## 5. Dependabot vs Renovate

### 5.1 Detailed Comparison for ai-cli-utils

| Feature | Dependabot | Renovate |
|---------|-----------|----------|
| **Hosting** | Built into GitHub (no install) | GitHub App (free) or self-hosted |
| **Python/uv support** | pip ecosystem via `pyproject.toml`. Does not update `uv.lock` natively. | Native `pep621` manager. Detects and updates both `pyproject.toml` and `uv.lock`. [VERIFIABLE FACT: https://docs.renovatebot.com/modules/manager/pep621/] |
| **Grouping** | Manual groups via `groups:` key in config. You define every group. | Built-in presets (`group:monorepos`, `group:recommended`). Auto-groups common packages. [VERIFIABLE FACT: https://docs.renovatebot.com/bot-comparison/] |
| **Automerge** | Not built-in. Requires a separate GitHub Actions workflow using `gh pr merge --auto`. | Built-in. `"automerge": true` in package rules. Supports branch automerge (no PR created). [VERIFIABLE FACT: https://docs.renovatebot.com/key-concepts/automerge/] |
| **PR noise** | One PR per dependency by default. Grouping helps but requires manual config. | Groups by default. Noise reduction of 80-90% reported in complex projects. [INDUSTRY HEURISTIC: widely reported in community comparisons] |
| **Dashboard** | None. Check PRs individually. | Dependency Dashboard issue tracks all pending updates in one place. |
| **GitHub Actions updates** | Yes, native support. | Yes, native support. |
| **Security-only mode** | Yes, via `open-pull-requests-limit: 0` plus security updates enabled in repo settings. | Yes, via `"enabled": false` globally with `"vulnerabilityAlerts"` enabled. |
| **Config complexity** | Simple YAML, limited options. | Rich JSON config, steeper learning curve, but more powerful. |
| **Lock file maintenance** | Updates `uv.lock` only if the ecosystem detects it (inconsistent for uv). | Explicit `lockFileMaintenance` option that periodically refreshes the lock file. [VERIFIABLE FACT: https://docs.renovatebot.com/configuration-options/] |

### 5.2 Recommended: Switch to Renovate

**Why:** The uv lock file support and built-in automerge are the deciding factors. Dependabot's lack of native `uv.lock` support means dependency updates may leave the lock file out of sync, requiring manual `uv lock` after merging Dependabot PRs. Renovate handles this natively.

### 5.3 Recommended Renovate Configuration

```json5
// renovate.json5
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "config:recommended",
    "group:monorepos",
    "group:recommended",
    ":automergeMinor",
    ":automergePatch"
  ],
  "labels": ["dependencies"],
  "packageRules": [
    {
      "description": "Automerge minor and patch updates for stable dependencies",
      "matchUpdateTypes": ["minor", "patch"],
      "matchCurrentVersion": "!/^0/",
      "automerge": true
    },
    {
      "description": "Do not automerge major updates or pre-1.0 dependencies",
      "matchUpdateTypes": ["major"],
      "automerge": false
    },
    {
      "description": "Group all GitHub Actions updates",
      "matchManagers": ["github-actions"],
      "groupName": "GitHub Actions",
      "automerge": true
    }
  ],
  "lockFileMaintenance": {
    "enabled": true,
    "schedule": ["before 6am on monday"]
  }
}
```

**Key decisions in this config:**

- **Pre-1.0 dependencies excluded from automerge** (`!/^0/`): SemVer allows breaking changes in minor versions for 0.x packages. The project depends on `nats-py>=2.9.0`, `circus>=0.18.0`, `libtmux>=0.30.0`, and `watchdog>=4.0.0` -- all post-1.0 except potentially circus. Review circus's versioning. [SYNTHESIZED INFERENCE]
- **GitHub Actions grouped and automerged:** Actions updates are low-risk and high-noise. Grouping them into a single weekly PR eliminates clutter.
- **Lock file maintenance weekly:** Ensures `uv.lock` stays fresh even when no dependency has a new release.

### 5.4 Migration Steps

1. Install the Renovate GitHub App on the repository (https://github.com/apps/renovate)
2. Renovate will open an onboarding PR with a default config
3. Replace the default config with the `renovate.json5` above
4. Remove `.github/dependabot.yml`
5. Close any open Dependabot PRs

### 5.5 Keeping Dependabot (Alternative)

If switching to Renovate feels like unnecessary complexity for a solo project, enhance the existing Dependabot config:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    groups:
      python-deps:
        patterns:
          - "*"
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    groups:
      actions:
        patterns:
          - "*"
```

Then add an automerge workflow:

```yaml
# .github/workflows/dependabot-automerge.yml
name: Dependabot auto-merge
on: pull_request

permissions:
  contents: write
  pull-requests: write

jobs:
  automerge:
    runs-on: ubuntu-latest
    if: github.actor == 'dependabot[bot]'
    steps:
      - uses: dependabot/fetch-metadata@v2
        id: metadata
      - if: steps.metadata.outputs.update-type != 'version-update:semver-major'
        run: gh pr merge --auto --squash "$PR_URL"
        env:
          PR_URL: ${{ github.event.pull_request.html_url }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

[VERIFIABLE FACT: Dependabot automerge pattern from https://docs.github.com/en/code-security/dependabot/working-with-dependabot/automating-dependabot-with-github-actions]

---

## 6. Pre-commit and Contributor Tooling

### 6.1 Should You Use .pre-commit-config.yaml?

**Current state:** The project uses ruff for linting/formatting in CI. No `.pre-commit-config.yaml` exists.

**Arguments for:**

- Catches lint/format issues before they reach CI, saving round-trip time.
- Ruff's pre-commit hook runs in ~0.2 seconds -- fast enough to be invisible. [VERIFIABLE FACT: https://github.com/astral-sh/ruff-pre-commit]
- For external contributors, pre-commit ensures they do not submit PRs that fail the lint CI step.

**Arguments against:**

- For a solo maintainer who runs `ruff check` and `ruff format` locally (or has editor integration), it adds a layer that catches nothing new.
- Contributors must install pre-commit (`pip install pre-commit && pre-commit install`) -- a friction point.
- CI is the authoritative gate regardless. Pre-commit is defense-in-depth, not a replacement.

**Verdict: Do before going public.** The friction is minimal (ruff hooks are sub-second), and it prevents contributor PRs from failing on trivial formatting issues. Keep the config minimal.

### 6.2 Recommended .pre-commit-config.yaml

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.4  # pin to current version
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
```

**Why these hooks and no others:**

- **ruff + ruff-format:** Matches the CI lint/format checks exactly. The `--fix` flag auto-corrects fixable issues on commit.
- **trailing-whitespace, end-of-file-fixer:** Universal hygiene. Prevents diff noise from whitespace-only changes.
- **check-yaml:** Catches syntax errors in GitHub Actions workflows, Renovate config, etc.
- **check-added-large-files:** Prevents accidentally committing large binaries. 500KB threshold is generous for a CLI project.

**What to skip:**

- **check-json, check-toml:** Only useful if you regularly hand-edit these files. TOML errors are caught by `uv sync` and `ruff` anyway.
- **mypy/pyright hooks:** Too slow for pre-commit. Run in CI instead.
- **detect-secrets:** Overkill for a solo project. GitHub's secret scanning handles this for public repos.

[SYNTHESIZED INFERENCE: the minimal config above covers the highest-value hooks without adding unnecessary build time or contributor friction.]

### 6.3 CI Integration

Add a pre-commit CI step or use the [pre-commit.ci](https://pre-commit.ci/) service (free for public repos) to auto-fix PRs from contributors who do not have pre-commit installed locally.

**Verdict: Do when community grows.** For a solo project, local pre-commit + CI ruff checks are sufficient. pre-commit.ci is valuable when you regularly receive PRs with formatting issues.

---

## 7. Going Public Checklist

### 7.1 Before Flipping the Switch

| # | Item | Priority | Status for ai-cli-utils |
|---|------|----------|------------------------|
| 1 | **Secret scanning: full history audit** | Critical | Run `git log --all -p` through a secret scanner (truffleHog or gitleaks) on the complete history. GitHub's built-in secret scanning activates only after the repo is public and only for known patterns. A pre-publication scan catches custom secrets, internal URLs, and API keys that GitHub's scanner would miss. [VERIFIABLE FACT: https://docs.github.com/en/code-security/secret-scanning/about-secret-scanning] |
| 2 | **Credential rotation** | Critical | Rotate any credentials that were ever committed, even if subsequently removed. Git history retains all prior commits. Assume every secret in history is compromised the moment the repo goes public. |
| 3 | **License audit** | Critical | Verify MIT license file is present and correct. Check that all dependencies are compatible (MIT, BSD, Apache 2.0 are fine; GPL dependencies in a MIT project are a problem). `nats-py` is Apache 2.0, `circus` is Apache 2.0, `libtmux` is MIT, `watchdog` is Apache 2.0 -- all compatible. [VERIFIABLE FACT: checked on PyPI as of 2026-03-29] |
| 4 | **README review** | High | Ensure installation instructions work for a fresh user. Test `uv tool install ai-cli-utils` and `pipx install ai-cli-utils` from PyPI. Remove any internal references (private URLs, internal project names). |
| 5 | **Repository description and topics** | High | Set via Settings or `gh repo edit`. Add: `python`, `cli`, `ai`, `developer-tools`, `tmux`, `claude`, `gemini`. |
| 6 | **Social preview image** | Medium | 1280x640px image. Use [socialify.git.ci](https://socialify.git.ci/) for a quick auto-generated image, or create a custom one. Shows up in social media cards when the repo URL is shared. |
| 7 | **Initial GitHub Release** | High | Create a v0.1.1 release (matching current PyPI version) so the repo has a release visible in the sidebar. Repos without releases look abandoned. |
| 8 | **Enable GitHub features** | High | Issues (should be on), Discussions (optional -- enable if you want a Q&A channel), Wiki (leave off -- docs belong in the repo). |
| 9 | **Enable code security features** | High | Settings > Code security: enable Dependabot alerts, Dependabot security updates, Code scanning (CodeQL default setup), Secret scanning + push protection. All free for public repos. |
| 10 | **Remove .venv from repo** | High | Currently shows as untracked (per git status). Ensure `.venv/` is in `.gitignore`. Verify it is not committed. |
| 11 | **Verify .gitignore completeness** | High | Ensure: `.venv/`, `dist/`, `*.egg-info/`, `__pycache__/`, `.pytest_cache/`, `coverage.xml`, `.coverage`, `*.pyc`. |

### 7.2 After Going Public (First 48 Hours)

| # | Item | Notes |
|---|------|-------|
| 1 | **Verify secret scanning results** | Check Security tab for any alerts. |
| 2 | **Test external clone + install** | From a clean machine: `git clone`, `uv tool install .`, verify `ai --help` works. |
| 3 | **Check badge rendering** | All shields.io badges should resolve correctly for a public repo. |
| 4 | **Post announcement** (optional) | If you want initial visibility: a brief post to relevant communities. |

---

## 8. Issue and PR Templates

### 8.1 Bug Report Template (YAML Issue Form)

```yaml
# .github/ISSUE_TEMPLATE/bug-report.yml
name: Bug Report
description: Report something that is not working correctly
labels: ["bug"]
body:
  - type: textarea
    id: description
    attributes:
      label: What happened?
      description: A clear description of the bug.
      placeholder: When I run `ai c 1`, I see...
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: What did you expect?
      placeholder: I expected the session to...
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      description: Minimal steps to trigger the bug.
      placeholder: |
        1. Run `ai c 1`
        2. ...
    validations:
      required: true
  - type: input
    id: version
    attributes:
      label: ai-cli-utils version
      description: Output of `ai --version` or `pip show ai-cli-utils`
      placeholder: "0.1.1"
    validations:
      required: true
  - type: input
    id: python-version
    attributes:
      label: Python version
      placeholder: "3.12.3"
    validations:
      required: true
  - type: dropdown
    id: os
    attributes:
      label: Operating system
      options:
        - Linux
        - macOS
        - Other
    validations:
      required: true
```

### 8.2 Feature Request Template

```yaml
# .github/ISSUE_TEMPLATE/feature-request.yml
name: Feature Request
description: Suggest a new feature or improvement
labels: ["enhancement"]
body:
  - type: textarea
    id: problem
    attributes:
      label: What problem does this solve?
      description: Describe the use case or pain point.
    validations:
      required: true
  - type: textarea
    id: solution
    attributes:
      label: What would you like?
      description: Describe the feature or behavior you want.
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: Any workarounds or alternative approaches you have tried.
    validations:
      required: false
```

### 8.3 Issue Template Config

```yaml
# .github/ISSUE_TEMPLATE/config.yml
blank_issues_enabled: true
contact_links:
  - name: Security Vulnerability
    url: https://github.com/sergeiwallace/ai-cli-utils/blob/main/SECURITY.md
    about: Please report security issues privately via SECURITY.md
```

### 8.4 Pull Request Template

```markdown
<!-- .github/pull_request_template.md -->
## What does this PR do?

<!-- Brief description. Link to issue if applicable: Fixes #123 -->

## How to test

<!-- Steps to verify the change works -->

## Checklist

- [ ] Tests pass (`pytest`)
- [ ] Linting passes (`ruff check src/ tests/ && ruff format --check src/ tests/`)
- [ ] CHANGELOG.md updated (if user-facing change)
```

[SYNTHESIZED INFERENCE: These templates are deliberately minimal. Long templates with many required fields discourage contributions. The YAML issue forms provide structure (dropdowns, required fields) without the wall-of-text problem that markdown templates have. The PR template is a lightweight checklist -- contributors can delete irrelevant sections.]

**Verdict: Do before going public.** Takes 15 minutes to set up and immediately improves issue quality.

---

## Consolidated Recommendations

### Do Now

| Item | Section | Effort |
|------|---------|--------|
| Add Python version matrix (3.11/3.12/3.13) to CI | 4.1 | 10 min |
| Add coverage reporting with Codecov | 4.3 | 20 min |
| Upgrade `astral-sh/setup-uv` to v5+ | 4.3 | 5 min |
| Add pyright type checking to CI | 4.5 | 30 min |
| Switch to Renovate (or enhance Dependabot config) | 5.2 | 30 min |

### Do Before Going Public

| Item | Section | Effort |
|------|---------|--------|
| Enable CodeQL default setup | 1.2 | 2 min |
| Add GitHub Release automation to publish workflow | 2.2 | 20 min |
| Configure branch protection / rulesets on main | 3.1 | 10 min |
| Set merge method preferences | 3.2 | 5 min |
| Add `.pre-commit-config.yaml` | 6.2 | 10 min |
| Run secret scanning on full git history | 7.1 | 30 min |
| Credential rotation for any committed secrets | 7.1 | Variable |
| License compatibility audit | 7.1 | 10 min (done above) |
| Set repository description, topics, social preview | 7.1 | 10 min |
| Create initial GitHub Release (v0.1.1) | 7.1 | 5 min |
| Add issue and PR templates | 8 | 15 min |

### Do When Community Grows

| Item | Section | Effort |
|------|---------|--------|
| actions/stale for issue management | 1.5 | 15 min |
| All Contributors bot | 1.6 | 10 min |
| actions/labeler for PR auto-labeling | 1.7 | 10 min |
| pre-commit.ci service | 6.3 | 10 min |

### Skip

| Item | Section | Reason |
|------|---------|--------|
| Release Drafter | 1.4 | Solo project commits directly to main; PR-driven changelog is not useful |

---

## Open Questions

- **Renovate vs Dependabot timing:** Should the switch to Renovate happen before or after going public? Renovate's onboarding PR can be noisy. Consider switching while private to settle the config.
- **Type checking strictness:** What is the current state of type annotations in `ai-cli-utils`? If sparse, pyright basic mode may still surface many errors. May need a phased rollout with per-file ignores.
- **Coverage threshold:** What is the current test coverage percentage? This determines whether to add the Codecov badge immediately or wait.
- **Git history cleanliness:** Has any sensitive data (API keys, internal URLs, credentials) ever been committed to the repository? If yes, a full history rewrite (`git filter-repo`) is needed before going public.

---

## Sources

- [Renovate Bot Documentation](https://docs.renovatebot.com/)
- [Renovate Bot Comparison (vs Dependabot)](https://docs.renovatebot.com/bot-comparison/)
- [Renovate PEP 621 Manager](https://docs.renovatebot.com/modules/manager/pep621/)
- [Renovate Automerge Docs](https://docs.renovatebot.com/key-concepts/automerge/)
- [GitHub CodeQL Action](https://github.com/github/codeql-action)
- [Codecov GitHub Action](https://github.com/codecov/codecov-action)
- [Codecov Python + GitHub Actions Guide](https://about.codecov.io/blog/python-code-coverage-using-github-actions-and-codecov/)
- [softprops/action-gh-release](https://github.com/softprops/action-gh-release)
- [Release Drafter](https://github.com/marketplace/actions/release-drafter)
- [actions/stale](https://github.com/actions/stale)
- [All Contributors](https://allcontributors.org/)
- [actions/labeler](https://github.com/actions/labeler)
- [pascalgn/automerge-action](https://github.com/pascalgn/automerge-action)
- [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv)
- [hynek/setup-cached-uv](https://github.com/hynek/setup-cached-uv)
- [uv GitHub Actions Integration Guide](https://docs.astral.sh/uv/guides/integration/github/)
- [Ruff Pre-commit Hooks](https://github.com/astral-sh/ruff-pre-commit)
- [GitHub Branch Protection Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub Solo Maintainer Branch Protection Discussion](https://github.com/orgs/community/discussions/23727)
- [GitHub Secret Scanning Docs](https://docs.github.com/en/code-security/secret-scanning/about-secret-scanning)
- [GitHub Issue Forms Syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
- [Pyright vs Mypy Comparison (Microsoft)](https://github.com/microsoft/pyright/blob/main/docs/mypy-comparison.md)
- [gh release create CLI Docs](https://cli.github.com/manual/gh_release_create)
- [Dependabot Automating with GitHub Actions](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/automating-dependabot-with-github-actions)

---

## Appendix: Research Prompt

**Registry ID:** R-2
**Model:** opus researcher (claude-opus-4-6)
**Date:** 2026-03-29

**Prompt:**

You are a senior open-source maintainer who has shipped and maintained Python CLI packages with 5K-50K stars on GitHub. You know the difference between "looks professional" and "runs itself."

Research the GitHub ecosystem tooling, automation, and repository configuration best practices for a solo-maintained Python CLI project that already has the basics (CI, PyPI publish, Dependabot, badges, README, CONTRIBUTING.md, SECURITY.md).

The project is `ai-cli-utils` -- a Python 3.11+ CLI tool installed via `uv tool install` / `pipx install`. It uses ruff for linting, pytest for testing, hatchling for builds, and uv for dependency management. Repo is currently private, will go public.

Cover these areas:

1. **GitHub Apps & Bots** -- Which GitHub Apps/bots are worth installing for a solo-maintained project? Evaluate: Renovate (vs Dependabot), Release Drafter, Stale bot, All Contributors, CodeQL/security scanning, Codecov, auto-merge bots, label bots. For each: what it does, maintenance burden, whether it's worth it for a solo maintainer.

2. **GitHub Release Automation** -- Best practice for creating GitHub Releases automatically from tags. Compare: release-drafter, gh-action-tag, softprops/action-gh-release, manual `gh release create`. Include changelog extraction patterns.

3. **Branch Protection & Merge Settings** -- What rules to set on main for a solo-maintained repo that still wants quality gates. Required status checks, linear history, auto-delete branches, merge method preferences.

4. **CI Enhancements** -- Python version matrix testing (3.11/3.12/3.13), coverage reporting and badge thresholds, caching strategies for uv, artifact retention policies. Should we add pyright/mypy type checking to CI?

5. **Dependabot vs Renovate** -- Detailed comparison for this specific project. Grouping strategies, automerge for patch/minor, security-only mode, config examples for both.

6. **Pre-commit & Contributor Tooling** -- Should a solo project use .pre-commit-config.yaml? What hooks? Does it add friction for a project that already has ruff + CI?

7. **Going Public Checklist** -- What to do before flipping a private repo to public. Secret scanning, credential audit, license audit, README review, topic/description verification, initial GitHub Release, social preview image.

8. **Issue & PR Templates** -- Minimal effective templates for bug reports, feature requests, and PRs. Show actual YAML/markdown. Keep them short -- long templates discourage contributions.

For each recommendation, classify as:
- **Do now** -- clear ROI, low effort
- **Do before going public** -- necessary for public perception
- **Do when community grows** -- premature for a solo maintainer
- **Skip** -- not worth it for this project type
