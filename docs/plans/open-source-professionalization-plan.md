# ai-cli Open-Source Professionalization — Implementation Plan

**Status:** DRAFT

**Created:** 2026-03-29

**Task:** AI-CLI-5 (SW-672)
**Research:** [`docs/research/open-source-package-best-practices.md`](../research/open-source-package-best-practices.md)

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
  with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
  they navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this
  once AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
  `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
  (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
  Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
  `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

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
- **Generalize: remove all <private-project-name>/ai-core-specific references** (prerequisite — must be done first)
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

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes each task from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
  gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
  structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
  review against the ACs), never self-declared "done". -->

### T-00: Generalize — remove <private-project-name>/ai-core-specific references

**Size:** L
**Batch:** 1 (DO THIS FIRST — other tasks depend on clean generic code)

Remove all hardcoded references to `<private-project-name>`, `ai-core`, and personal paths so the package works for any user out of the box.

**Changes required:**

1. **`PRIVATE_PROJECT_TOML` → `PROJECT_REGISTRY`**: Rename the concept. The path is derived from config: `~/.config/ai-cli/projects.toml` (user-level) or `{main_project_dir}/{project_name}.toml` (project-level). Both should be checked, project-level first.

2. **`_get_main_project_name()` default**: Change from `"<private-project-name>"` to `None`. If no `main_project` is configured and no project TOML is found, features that depend on it (project aliases, cross-project sync) gracefully degrade — they just don't work until configured.

3. **`DEFAULT_SERVER_HOST`**: Remove the hardcoded personal `user@host` default. If `[sync] remote_host` is not set in config.toml, sync commands should print a helpful error message pointing to config setup.

4. **`ai-core` section reference**: The code reads `config.get("ai-core", {}).get("task_prefix", "SW")` for the main project. Generalize: read `config.get("project", {}).get("task_prefix")` — or better, just look up the project name in the `[[projects]]` list like all other projects.

5. **Comments/docstrings**: Replace personal home-dir path examples with generic `/home/user/...` and `/Users/username/...`.

6. **Help text**: Replace `<private-project-name>`, `aurion` examples with generic `myproject`, `webapp`.

7. **Default config template (`DEFAULT_CONFIG`)**: Already clean, verify no <private-project-name> references remain.

8. **`sync.py`**: Remove hardcoded `DEFAULT_SERVER_HOST`. Replace path examples in docstrings. The `_MAC_HOME` and `_SERVER_HOME` constants for path translation need to be derived from config or auto-detected, not hardcoded.

**Acceptance criteria:**
- [ ] `grep -rn "<private-project-name>\|ai-core" src/ai_cli/` returns 0 results (excluding test fixtures if any)
- [ ] Package works with empty config (graceful degradation for unconfigured features)
- [ ] Package works with a minimal config (`[remote] host = ...` + `[project] main_project = ...`)
- [ ] All existing functionality preserved for configured users

**Dependencies:** None — do this first.

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
| 1 | T-00 first, then T-01 through T-07 | Generalize + Foundation — remove personal refs, then CI, publishing, badges, metadata | Human approval of plan |

T-00 must be done first. T-01 through T-07 are independent of each other.

### T-08: Post-implementation review + verification

**Size:** M
**Batch:** 1 (final step)

Autonomous review of the completed work:

1. **Code review:** `grep -rn "<private-project-name>\|ai-core\|sergeiwallace" src/ai_cli/` — must return 0 results
2. **Config review:** verify `~/.config/ai-cli/config.toml` on both Mac and server has `main_project = "myproject"` set (since that's what makes the generic code work for our installation)
3. **Functional test:** verify `ai c 1 -R` still works (project aliases, session naming, auto-resume)
4. **CI verification:** push a test commit and verify GitHub Actions runs
5. **Badge verification:** check README renders correctly on GitHub with all badges
6. **PyPI metadata:** verify `pip show ai-cli-utils` shows correct URLs and metadata
7. **Clean install test:** `uv tool install ai-cli-utils` from PyPI (not editable) and verify `ai --help` works

**Acceptance criteria:**
- [ ] Zero hardcoded personal references in source
- [ ] Existing installations still work with config
- [ ] CI passes on GitHub
- [ ] Badges render on GitHub README
- [ ] Fresh install from PyPI works

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope and approach |
| Post-implementation review | After T-08 | User verifies installations work, does manual testing |

## Open Questions

1. **Repo name mismatch** — GitHub repo is `ai-cli`, PyPI package is `ai-cli-utils`. Rename repo to match, or keep them different?

> **Recommendation: Keep them different.** Many popular projects have repo/PyPI name mismatches (Pillow, beautifulsoup4). Renaming creates churn across the ecosystem (directory paths, platform.toml, memory files, deploy scripts, editable installs on 3 machines) for minimal user benefit. Only you see the repo name (contributor) and PyPI name (installer). Rename later when going public if alignment matters then.
>
> **Risks if we did rename (for reference):**
> - Local directory `~/projects/ai-cli/` needs renaming on Mac + server
> - `platform.toml` path entry needs updating
> - Memory files and CLAUDE.md deploy commands reference old path
> - All editable installs (`uv tool install -e ~/projects/ai-cli`) break until path updated
> - GitHub auto-redirects old URLs (permanent redirect, low risk)
> - PyPI package name stays `ai-cli-utils` regardless (no PyPI impact)

1. **Installation method** — Should README recommend `pipx install ai-cli-utils` or `uv tool install ai-cli-utils`?

> **Recommendation: `uv tool install` as primary, `pipx` as alternative.** uv is the modern standard, faster than pipx, and what we already use. Document both for users without uv.

1. **Trusted Publisher setup** — Requires manual configuration on pypi.org. Should the implementing session do this, or is it a separate manual step?

> **Recommendation: Include as a manual step in the batch.** ~5 minutes on pypi.org — fill in repo name, workflow filename, environment name. The implementing session can document the steps but the user needs to do the PyPI web UI part.

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
