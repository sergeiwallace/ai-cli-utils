# ai-cli Open-Source Professionalization — Implementation Plan

**Status:** DRAFT

**Created:** 2026-03-29

**Task:** SW-672
**Research:** [`docs/research/open-source-package-best-practices.md`](../research/open-source-package-best-practices.md)

## Table of Contents

- [Overview](#overview)
- [Options](#options)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Professionalize the `ai-cli-utils` PyPI package and `ai-cli` GitHub repo to match the quality standards of well-maintained open-source Python CLI projects. Currently the repo has minimal README, no CI, no badges, no CHANGELOG, and no community files. The goal is to reach litecli/pgcli-level presentation quality so the repo is ready for eventual public release.

## Options

### Option A: Full professionalization now

Do everything in the research doc's "Immediate" and "Short-term" categories in one batch.

**Pros:**
- Repo looks professional immediately
- CI catches issues going forward
- Automated PyPI publishing reduces manual deploy steps

**Cons:**
- Larger scope — more time upfront
- Terminal GIF requires separate tooling (asciinema/vhs)

### Option B: Foundation now, polish later

Split into two phases: foundation (CI, badges, CHANGELOG, community files) now, and polish (README rewrite, GIF, docs site) as a follow-up.

**Pros:**
- Faster to ship the critical infrastructure
- README rewrite is better done after CI is in place (badges need CI URLs)
- GIF can be recorded after features stabilize

**Cons:**
- Two rounds of work on the same repo

### Option C: Minimum viable — CI + badges only

Just add CI workflows and badges. Skip community files, CHANGELOG, etc.

**Pros:**
- Fastest to ship
- Highest ROI items (CI prevents regressions, badges show professionalism)

**Cons:**
- Still looks incomplete (no CHANGELOG, no SECURITY.md)
- Misses the PyPI Trusted Publisher automation

### Recommendation

**Option B: Foundation now, polish later.** This gives us CI, automated publishing, badges, CHANGELOG, and community files — the things that matter for a professional repo — without blocking on the GIF or README rewrite. The README rewrite is better done after CI is set up (need CI URLs for badges) and after the current feature burst stabilizes.

**Now (this batch):**
- CI workflow (lint + test)
- Publish workflow (PyPI on tag via Trusted Publishers)
- Shields.io badges in README
- CHANGELOG.md
- SECURITY.md
- .github/dependabot.yml
- GitHub repo topics + description
- pyproject.toml metadata (keywords, URLs, classifiers)
- py.typed marker

**Later (follow-up task):**
- Full README restructure (13-section PyOpenSci format)
- Terminal GIF/asciinema recording
- CONTRIBUTING.md + CODE_OF_CONDUCT.md
- Issue/PR templates
- MkDocs docs site (when README exceeds ~500 lines)

## Task Breakdown

### T-01: CI workflow

**Size:** M
**Batch:** 1

Add `.github/workflows/ci.yml` — runs on push to main and PRs.

**Deliverables:**
- `ruff check` + `ruff format --check`
- `pytest` with Python 3.12 (expand matrix later if needed)
- Type checking with `pyright` or `mypy` (if type hints are present)

**Acceptance criteria:**
- [ ] CI runs on push to main
- [ ] CI runs on PR
- [ ] Lint, format, and test steps all pass

### T-02: Publish workflow

**Size:** M
**Batch:** 1

Add `.github/workflows/publish.yml` — publishes to PyPI on git tag push using Trusted Publishers (OIDC, no API tokens).

**Deliverables:**
- Workflow triggers on `v*` tag push
- Uses `pypa/gh-action-pypi-publish` with Trusted Publishers
- Builds with `uv build`

**Acceptance criteria:**
- [ ] Tag push triggers publish
- [ ] Package appears on PyPI after tag
- [ ] No manual API token management needed

**Dependencies:** Requires Trusted Publisher configured on PyPI (manual step)

### T-03: README badges

**Size:** S
**Batch:** 1

Add shields.io badges to the top of README.md.

**Deliverables:**
- PyPI version badge
- Python version badge
- License badge
- CI status badge (links to Actions)

**Acceptance criteria:**
- [ ] Badges render correctly on GitHub
- [ ] Badges link to correct URLs (PyPI, Actions)

### T-04: CHANGELOG.md

**Size:** S
**Batch:** 1

Create `CHANGELOG.md` following Keep a Changelog format. Backfill from git history.

**Acceptance criteria:**
- [ ] Follows Keep a Changelog format
- [ ] Has [Unreleased] section at top
- [ ] Backfills key releases from git history

### T-05: Community files

**Size:** S
**Batch:** 1

Add `SECURITY.md` and `.github/dependabot.yml`.

**Deliverables:**
- SECURITY.md with vulnerability reporting instructions
- Dependabot config for pip dependencies

**Acceptance criteria:**
- [ ] SECURITY.md exists with contact info
- [ ] Dependabot creates PRs for dependency updates

### T-06: GitHub repo metadata

**Size:** S
**Batch:** 1

Set via `gh` CLI or GitHub web UI.

**Deliverables:**
- Repository description
- Topics: `cli`, `python`, `tmux`, `mosh`, `claude-code`, `ai-tools`, `developer-tools`
- Homepage URL (if applicable)

**Acceptance criteria:**
- [ ] Description set
- [ ] At least 5 relevant topics added

### T-07: pyproject.toml metadata

**Size:** S
**Batch:** 1

Add missing metadata fields.

**Deliverables:**
- `keywords`
- `[project.urls]` (Homepage, Repository, Changelog, Bug Tracker)
- Classifiers (Development Status, Environment, License, Programming Language, Topic)
- `py.typed` marker file in src/ai_cli/

**Acceptance criteria:**
- [ ] PyPI project page shows rich metadata
- [ ] Type checker recognizes py.typed

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01 through T-07 | Foundation — CI, publishing, badges, metadata | Human approval of plan |

All tasks are independent and can be done in a single session.

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope and approach |
| UAT | After implementation | Verify CI runs, badges render, PyPI publish works |

## Open Questions

1. **Repo name mismatch** — GitHub repo is `ai-cli`, PyPI package is `ai-cli-utils`. Rename repo to match, or keep them different?
> -

2. **Installation method** — Should README recommend `pipx install ai-cli-utils` (isolated) or `uv tool install ai-cli-utils` (what we use)?
> -

3. **Trusted Publisher setup** — Requires manual configuration on pypi.org. Should the implementing session do this, or is it a separate manual step?
> -

---

> **Feedback Round 1:**
> - Scope / task breakdown:
>
>    -
> - Options / recommendation:
>
>    -
> - Open questions:
>
>    -

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
