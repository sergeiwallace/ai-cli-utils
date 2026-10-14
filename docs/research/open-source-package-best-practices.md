---
title: "Open-Source Python CLI Package Best Practices"
category: research
tags: [open-source, python, cli, github, pypi, best-practices]
status: complete
source: "opus-researcher-2026-03-29"
registry_id: R-4
---

# Open-Source Python CLI Package Best Practices

**Status:** complete

**Created:** 2026-03-29

**Task:** SW-672
**Prompt:** R-1 in `docs/research/prompts/research-prompt-registry.md`

## Summary

This document covers best practices for maintaining a professional open-source Python CLI package (`ai-cli-utils`) on GitHub and PyPI. It spans README structure and badges, GitHub project configuration (CI, releases, community files), Python packaging conventions, and exemplary projects to emulate. Recommendations are grounded in official Python packaging documentation, PyOpenSci guidelines, and patterns observed in successful CLI projects with 1K-50K stars.

---

## Part 1: README.md Best Practices

### 1.1 Recommended Section Structure

The following order is based on PyOpenSci guidelines and patterns from high-quality Python CLI projects. [INDUSTRY HEURISTIC]

| # | Section | Purpose |
|---|---------|---------|
| 1 | **Title + tagline** | Package name, one-line description. Keep it scannable. |
| 2 | **Badges** | Social proof and project health signals (see 1.2 below). |
| 3 | **Hero example or GIF** | A single terminal screenshot or GIF showing the tool in action. This is the highest-impact element for first impressions. |
| 4 | **What it does** | 2-4 sentences. What problem it solves, who it's for. Avoid jargon. |
| 5 | **Features** | Bulleted list or table of key capabilities. Keep it to 5-10 items. |
| 6 | **Installation** | `pip install ai-cli-utils` front and center. Include `pipx` if applicable. |
| 7 | **Quick Start** | 3-5 real command examples that a user can copy-paste immediately. |
| 8 | **Usage / Commands** | Command reference table or expandable sections for each subcommand. Link to full docs if they exist. |
| 9 | **Configuration** | Environment variables, config files, or setup requirements. |
| 10 | **Requirements / Prerequisites** | Python version, OS support, external dependencies (tmux, mosh, etc.). |
| 11 | **Contributing** | Link to CONTRIBUTING.md or brief inline instructions. |
| 12 | **License** | One line with license name and link. |
| 13 | **Acknowledgments** (optional) | Credit to libraries, inspirations, or contributors. |

**Source:** [PyOpenSci README Best Practices](https://www.pyopensci.org/python-package-guide/documentation/repository-files/readme-file-best-practices.html)

### 1.2 Recommended Badges

Place badges on a single line or two lines at most, immediately below the title. [VERIFIABLE FACT — shields.io provides all of these]

```markdown
[![PyPI version](https://img.shields.io/pypi/v/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Python versions](https://img.shields.io/pypi/pyversions/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![License](https://img.shields.io/pypi/l/ai-cli-utils)](https://github.com/user/ai-cli-utils/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/user/ai-cli-utils/ci.yml?label=CI)](https://github.com/user/ai-cli-utils/actions)
[![Downloads](https://img.shields.io/pypi/dm/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
```text

**Essential badges (in order of importance):**

| # | Badge | Why |
|---|-------|-----|
| 1 | PyPI version | Shows the package is published and what version is current |
| 2 | Python versions | Tells users instantly if their Python is supported |
| 3 | License | Legal clarity at a glance |
| 4 | CI status | Signals the project is tested and builds pass |
| 5 | Downloads | Social proof (only add once downloads are meaningful) |
| 6 | Code style | Signals you follow community standards (ruff) |

**Skip until relevant:** Coverage badge (add when coverage is high enough to be proud of), docs badge (add when you have a docs site).

### 1.2a Badges that stay truthful, and badges for private or internal repositories

Added 2026-10-14 (refine pass). The list above assumes a public repository and a published
package. Two further rules apply everywhere, and a private or internal repository changes which
mechanisms work at all.

**A badge asserts that a process exists.** Add a badge only when the thing it reports is produced
by an automated process the badge reads from. A "semver" badge on a repository with no release
tags claims a practice the repository does not follow. So does a "Dependabot enabled" badge that
is a static image, or a coverage badge nobody updates. A static badge is fine for a fact that does not
change (the Python version floor, the formatter, the license). [INDUSTRY HEURISTIC]

**Which badges render depends on who can read the repository.**

| Mechanism | How it reads data | Public repo | Private or internal repo |
|---|---|---|---|
| GitHub's own workflow badge, `https://github.com/OWNER/REPO/actions/workflows/FILE/badge.svg` | Served by GitHub; `?branch=` and `?event=` filters | Yes | Renders for a signed-in reader with access; "not accessible externally", so it cannot be embedded elsewhere [VERIFIABLE FACT: https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge] |
| shields.io dynamic badges (`github/actions/workflow/status`, `github/v/release`, `pypi/v`) | shields.io calls the public API | Yes | No: shields.io cannot read a repository the public API does not expose [INFERENCE from the badge's parameters, which carry no credential: https://shields.io/badges/git-hub-actions-workflow-status] |
| shields.io static badges, `https://img.shields.io/badge/<label>-<message>-<color>` | Text is in the URL; `_` or `%20` is a space, `--` is a dash, `logo=` adds a simple-icons logo | Yes | Yes [VERIFIABLE FACT: https://shields.io/badges/static-badge] |
| An image hosted on a private network or behind credentials | GitHub proxies README images through Camo; "if an image is being served from a private network" it "can't be viewed by GitHub" | n/a | No [VERIFIABLE FACT: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-anonymized-urls] |
| An SVG committed to the repository and referenced by relative path | Served by GitHub as repository content | Yes | Yes |

**Coverage and test-count badges without a hosted service.** `genbadge` writes a tests badge from
`junit.xml` and a coverage badge from `coverage.xml`; by default it renders "through shields.io",
and `--local` uses "the included SVG file template" with no network call [VERIFIABLE FACT:
https://smarie.github.io/python-genbadge/]. A CI job can regenerate the SVG and commit it. The
commit message must carry `[skip ci]`, `[ci skip]`, `[no ci]`, `[skip actions]` or `[actions skip]`
so the badge commit does not start another CI run. GitHub honours these on `push` and
`pull_request` events only. A skipped workflow's checks "remain in a Pending state", which blocks a
pull request that requires them [VERIFIABLE FACT:
https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/skipping-workflow-runs].
Push the badge commit straight to the default branch, or to a dedicated branch the README does not
reference by relative path, and never through a protected pull request. GitHub reads the marker
from the head commit's message wherever it appears, so a commit whose message merely quotes the
string in prose is skipped too, and its pull request then waits on required checks that never run.
Keep the literal out of ordinary commit messages; a `workflow_dispatch` run on the branch is the
recovery when it happens. [VERIFIABLE FACT: observed 2026-10-14 on a documentation commit; the
docs page above describes the marker semantics]

**Ecosystem badges that only cover public projects.** The OpenSSF Scorecard badge,
`https://api.scorecard.dev/projects/github.com/{owner}/{repo}/badge`, is served from an API the
project describes as covering "OSS projects"; it is a public-repository badge [VERIFIABLE FACT:
https://github.com/ossf/scorecard/blob/main/README.md]. The same holds for Codecov's free tier,
PyPI version and download badges, and `pypi/l` license badges: each needs a public artifact.

**Order and count.** One line, at most two, in the order a new reader needs: does it build, what
does it run on, how is it licensed, how is it formatted. A README with zero badges is better than a README with a broken one. A badge that renders an
error image tells the reader the maintainer does not look at the README. [INDUSTRY HEURISTIC]

### 1.3 CLI Usage Documentation in the README

[SYNTHESIZED INFERENCE based on patterns from httpie, typer, pgcli, and other successful CLI projects]

**What works:**

- **Command examples with output** — show the command and its actual terminal output side-by-side. Users want to see what happens when they run a command, not just the command itself.
- **Feature tables** — a compact table mapping command to description is more scannable than prose.
- **Animated GIF or screenshot** — a single well-chosen GIF near the top is worth more than paragraphs of description. Record with [asciinema](https://asciinema.org/) or [VHS](https://github.com/charmbracelet/vhs) for terminal recordings, or a simple screenshot for static output.
- **Real-world examples** — show commands that solve actual problems, not contrived demos.

**What to avoid:**

- Exhaustive `--help` output pasted into the README (link to it instead).
- More than ~10 examples in the README — move the rest to a docs site or wiki.

### 1.4 What Makes a README Stand Out

[SYNTHESIZED INFERENCE]

The difference between "good enough" and "makes people want to star/install":

1. **Visual hook in the first 5 seconds** — a GIF or screenshot that shows the tool doing something impressive. pgcli's auto-completion GIF and httpie's colorized output screenshots are the gold standard.
2. **Immediate utility** — "I can copy-paste this command and see value in 30 seconds." The README should make the time-to-first-success as short as possible.
3. **Honest scope** — clearly state what the tool does AND what it does not do. Users respect boundaries.
4. **Personality without clutter** — a brief, opinionated tagline ("Unified AI session manager") beats a committee-written description.
5. **Active maintenance signals** — recent commits, CI passing, recent release date. These are implicit but powerful.

---

## Part 2: GitHub Project Configuration

### 2.1 Repository Metadata

[VERIFIABLE FACT — GitHub repository settings]

| Setting | Recommendation |
|---------|---------------|
| **Description** | "Unified CLI for managing Claude Code and Gemini CLI sessions with tmux, mosh, git worktrees, and cross-machine sync" |
| **Topics/tags** | `python`, `cli`, `tmux`, `ai`, `developer-tools`, `claude`, `gemini`, `mosh`, `git-worktrees`, `session-management` |
| **Website URL** | PyPI page initially (`https://pypi.org/project/ai-cli-utils/`), docs site later |
| **Social preview** | 1280x640px image with logo + tagline. Can be generated with tools like [socialify.git.ci](https://socialify.git.ci/) |

### 2.2 GitHub Actions CI Workflows

A Python CLI project should have these workflows. [INDUSTRY HEURISTIC, based on official Python packaging guide and pypa conventions]

#### ci.yml — Lint + Test (runs on every push/PR)

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install ruff
      - name: Lint
        run: ruff check src/ tests/
      - name: Format check
        run: ruff format --check src/ tests/

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: pyproject.toml
      - name: Install package with dev deps
        run: pip install -e '.[dev]'
      - name: Run tests
        run: pytest --cov=ai_cli --cov-report=xml
      - name: Upload coverage
        if: matrix.python-version == '3.12'
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml
```text

#### publish.yml — PyPI Release (runs on tag push)

Uses PyPI Trusted Publishers (no API tokens needed). [VERIFIABLE FACT — recommended by PyPA]

```yaml
name: Publish to PyPI

on:
  push:
    tags: ["v*"]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install build tools
        run: pip install build
      - name: Build distributions
        run: python -m build
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    runs-on: ubuntu-latest
    needs: [build]
    environment: release
    permissions:
      id-token: write
    steps:
      - name: Download artifacts
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```text

**Setup required:** Configure a Trusted Publisher on PyPI under your project settings (Project > Publishing > Add a new publisher). Specify the GitHub repository, workflow filename (`publish.yml`), and environment name (`release`).

**Source:** [PyPA gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish), [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/using-a-publisher/), [Simon Willison's guide](https://til.simonwillison.net/pypi/pypi-releases-from-github)

### 2.3 Community Files

| File | Priority | Notes |
|------|----------|-------|
| `LICENSE` | **Essential** | Already MIT. Keep it. |
| `CONTRIBUTING.md` | **Nice-to-have** | Add when you want external contributions. For a solo-maintained tool, a brief section in the README suffices initially. |
| `CODE_OF_CONDUCT.md` | **Nice-to-have** | Use the [Contributor Covenant](https://www.contributor-covenant.org/). Becomes essential if you have active contributors. |
| Issue templates | **Nice-to-have** | Bug report + feature request templates. Add when you start getting issues. GitHub's built-in template chooser works well. |
| PR template | **Low priority** | Useful for multi-contributor projects. Not needed for a solo maintainer. |

[SYNTHESIZED INFERENCE] For a solo-maintained project, start with LICENSE + a contributing section in the README. Add the rest as the community grows. Premature community infrastructure signals "enterprise project pretending to have contributors" — which can be worse than having nothing.

### 2.4 Release Strategy

[INDUSTRY HEURISTIC]

**Semantic versioning (SemVer):**
- `0.x.y` while pre-1.0 (current state) — minor = new features, patch = fixes
- `1.0.0` when the CLI interface is stable and you're committing to backward compatibility
- After 1.0: major = breaking CLI changes, minor = new features, patch = fixes

**Release workflow:**

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md` (see below)
3. Commit: `git commit -m "release: v0.2.0"`
4. Tag: `git tag v0.2.0`
5. Push: `git push origin main --tags`
6. GitHub Actions publishes to PyPI automatically
7. Create a GitHub Release from the tag with the changelog entry as the body

**Changelog format — Keep a Changelog:**

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.2.0] - 2026-04-01
### Added
- New `ai sync` command for cross-machine session sync
### Fixed
- tmux session detection on macOS

## [0.1.0] - 2026-03-15
### Added
- Initial release with `ai c`, `ai handoff`, and `ai sync` commands
```text

**Source:** [Keep a Changelog](https://keepachangelog.com/), [SemVer](https://semver.org/)

### 2.5 Security

| Item | Priority | Notes |
|------|----------|-------|
| `SECURITY.md` | **Add now** | Even a brief file saying "email me at X for security issues" is better than nothing. Prevents public disclosure of vulnerabilities. |
| Dependabot | **Add now** | Free, zero-effort. Add `.github/dependabot.yml` to get automated dependency update PRs. |
| Signed commits | **Low priority** | Nice but not expected for a solo project. |

**Dependabot config:**

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
```text

---

## Part 3: Python Package Best Practices

### 3.1 pyproject.toml Best Practices

The current `pyproject.toml` is solid. Recommended additions: [SYNTHESIZED INFERENCE]

```toml
[project]
# Add these classifiers (in addition to existing ones):
classifiers = [
    # ... existing classifiers ...
    "Typing :: Typed",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
]
keywords = ["cli", "tmux", "ai", "claude", "gemini", "session-management"]

[project.urls]
Homepage = "https://github.com/user/ai-cli-utils"
Repository = "https://github.com/user/ai-cli-utils"
Issues = "https://github.com/user/ai-cli-utils/issues"
Changelog = "https://github.com/user/ai-cli-utils/blob/main/CHANGELOG.md"
```text

**Key points:**
- `keywords` improves PyPI search discoverability. [VERIFIABLE FACT — [PyPI search uses keywords](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)]
- `Changelog` URL in `[project.urls]` shows up on the PyPI page as a sidebar link. [VERIFIABLE FACT]
- `"Typing :: Typed"` classifier signals type hint support. [VERIFIABLE FACT]
- Entry point (`[project.scripts]`) is already correctly configured.

### 3.2 Type Hints + py.typed

[VERIFIABLE FACT — [PEP 561](https://peps.python.org/pep-0561/)]

If the package provides type hints (which it should for a modern Python 3.11+ package):

1. Add an empty `src/ai_cli/py.typed` marker file
2. Add `"Typing :: Typed"` to classifiers
3. This allows downstream users and tools (mypy, pyright) to use your type information

This matters less for a CLI tool than a library, but it's a quality signal and costs nothing.

### 3.3 Documentation Beyond README

[SYNTHESIZED INFERENCE]

**When to add a docs site:**
- When the README exceeds ~500 lines
- When you have >10 commands with non-trivial options
- When users start asking questions that the README already answers (signals discoverability problem)

**For ai-cli-utils right now:** A well-structured README is sufficient. The tool has a focused scope and a single audience (developers using Claude Code / Gemini CLI).

**When the time comes:** MkDocs with mkdocs-material is the current standard for Python CLI projects. It's what Typer, Ruff, and uv use. Hosted on GitHub Pages (free) or Read the Docs.

### 3.4 Test Infrastructure Visibility

[INDUSTRY HEURISTIC]

- **Coverage badge** — add to README once coverage is above 80%. Below that, the badge hurts more than it helps.
- **Test matrix** — visible in the CI workflow (Python 3.11, 3.12, 3.13). This shows up in the Actions tab and on the CI badge.
- **pytest output in CI** — use `pytest --tb=short` for readable CI logs.

---

## Part 4: Exemplary Python CLI Projects

These projects demonstrate professional open-source practices that a solo maintainer can realistically emulate. [VERIFIABLE FACT for star counts and features; SYNTHESIZED INFERENCE for "what to emulate" assessments]

### 4.1 pgcli (~12.8K stars)

**Repo:** [github.com/dbcli/pgcli](https://github.com/dbcli/pgcli)

**What they do well:**
- GIF demo at the top of the README showing auto-completion in action — immediately communicates the value proposition
- Clean feature list with screenshots
- Separate docs site for detailed usage, while README stays focused
- Well-organized `dbcli` GitHub org with related tools (mycli, litecli) sharing infrastructure

**Emulate:** The GIF-first README approach. Show `ai c 1` launching a Claude Code session with tmux in a terminal recording.

### 4.2 Typer (~19.1K stars)

**Repo:** [github.com/fastapi/typer](https://github.com/fastapi/typer)

**What they do well:**
- "FastAPI of CLIs" positioning — immediately leverages an existing mental model
- Progressive disclosure: starts with the absolute minimum example, then builds up
- Four focused badges (test, publish, coverage, version) — not badge overload
- Separate docs site with excellent tutorials

**Emulate:** Progressive example structure. Start with the simplest command, then show more complex usage.

### 4.3 HTTPie CLI (~34K stars)

**Repo:** [github.com/httpie/cli](https://github.com/httpie/cli)

**What they do well:**
- Professional branding (logo, consistent color scheme, social preview)
- SECURITY.md, CODE_OF_CONDUCT.md, CONTRIBUTING.md — full community infrastructure
- 47 releases with consistent versioning
- CI with coverage (Codecov integration)

**Emulate:** The professional polish level. Even before you have many users, having CI, a clear license, and consistent releases signals "this person ships."

### 4.4 Glances (~27K stars)

**Repo:** [github.com/nicolargo/glances](https://github.com/nicolargo/glances)

**What they do well:**
- Screenshot-driven README that shows exactly what you get
- Clear system requirements section (important for a tool with OS-level dependencies)
- Docker support documented prominently
- Active maintenance with regular releases over many years

**Emulate:** The clear prerequisites/requirements section. `ai-cli-utils` has real system dependencies (tmux, mosh, SSH) that need to be documented upfront.

### 4.5 howdoi (~10.7K stars)

**Repo:** [github.com/gleitz/howdoi](https://github.com/gleitz/howdoi)

**What they do well:**
- Extremely simple README — the tool is simple, and the README matches
- Installation + usage in the first 20 lines
- No over-engineering of the README for a focused tool

**Emulate:** Scope-appropriate documentation. Don't create enterprise-level docs for a focused developer tool.

### 4.6 litecli (~3.1K stars)

**Repo:** [github.com/dbcli/litecli](https://github.com/dbcli/litecli)

**What they do well:**
- Shared infrastructure with pgcli (same dbcli org, same patterns, same quality)
- Achieves professional presentation at 3K stars — proof that a smaller project can look polished
- Clean pyproject.toml with proper classifiers and entry points

**Emulate:** This is the most realistic comparison point. A focused CLI tool by a small team that looks professional without massive community infrastructure. Proof that the fundamentals (CI, badges, clear README, regular releases) matter more than scale.

### 4.7 Ruff (~40K stars)

**Repo:** [github.com/astral-sh/ruff](https://github.com/astral-sh/ruff)

**What they do well:**
- Performance benchmarks prominently displayed (relevant when speed is a differentiator)
- Badge for code style that other projects can use (self-marketing)
- Clean docs site (docs.astral.sh/ruff)
- Part of a cohesive ecosystem (Astral: ruff, uv, ty)

**Emulate:** The ecosystem branding. If `ai-cli-utils` is part of a broader platform, mention that context briefly. And the Ruff badge pattern: providing a badge that other projects can include (e.g., "Managed with ai-cli") is clever organic marketing, though only applicable once there is adoption.

---

## Recommendations

1. **Immediate (before next release):**
   - Add CI workflow (`ci.yml`) with lint + test matrix
   - Add publish workflow (`publish.yml`) with Trusted Publishers
   - Add shields.io badges to README (version, python versions, license, CI)
   - Add `CHANGELOG.md` following Keep a Changelog format
   - Add `.github/dependabot.yml`
   - Add `SECURITY.md` (even a minimal one)
   - Set GitHub repository topics and description

2. **Short-term (next 1-2 releases):**
   - Record a terminal GIF/asciinema showing key workflows
   - Restructure README to follow the section order in 1.1
   - Add `keywords` and `Changelog` URL to `pyproject.toml`
   - Add `py.typed` marker if type hints are present
   - Configure Trusted Publisher on PyPI

3. **When the community grows:**
   - Add `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`
   - Add issue templates (bug report, feature request)
   - Consider MkDocs docs site when README exceeds ~500 lines

## Open Questions

- Should the GitHub repository be renamed from `ai-cli` to `ai-cli-utils` to match the PyPI package name, or keep them different? Mismatches between repo name and package name can confuse users.
- Is `pipx install ai-cli-utils` the recommended installation method? For CLI tools, `pipx` is generally preferred over `pip` to avoid polluting the global Python environment. Consider documenting both.
- When (if ever) should a 1.0.0 release happen? This signals CLI interface stability and a commitment to backward compatibility.

---

## Appendix: Research Prompt

**Registry ID:** R-1 (in `docs/research/prompts/research-prompt-registry.md`)
**Model:** opus researcher (claude-opus-4-6)
**Date:** 2026-03-29

**Prompt:** Research best practices for maintaining a professional open-source Python CLI package on GitHub + PyPI. The package is `ai-cli-utils` (command: `ai`), a unified CLI for managing Claude Code and Gemini CLI sessions with tmux, mosh, git worktrees, and cross-machine sync. Cover: README structure and badges, GitHub project configuration (CI, releases, community files, security), Python packaging conventions (pyproject.toml, type hints, docs), and exemplary Python CLI projects to emulate (1K-50K stars range).

**Sources consulted:**
- [PyOpenSci README Best Practices](https://www.pyopensci.org/python-package-guide/documentation/repository-files/readme-file-best-practices.html)
- [Python Packaging User Guide — GitHub Actions CI/CD](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)
- [PyPA gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish)
- [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [Simon Willison — PyPI releases from GitHub Actions](https://til.simonwillison.net/pypi/pypi-releases-from-github)
- [Shields.io](https://shields.io/)
- [Shields.io static badge](https://shields.io/badges/static-badge) (refine 2026-10-14)
- [GitHub Docs: Adding a workflow status badge](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge) (refine 2026-10-14)
- [GitHub Docs: About anonymized URLs (Camo)](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-anonymized-urls) (refine 2026-10-14)
- [GitHub Docs: Skipping workflow runs](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/skipping-workflow-runs) (refine 2026-10-14)
- [genbadge](https://smarie.github.io/python-genbadge/) (refine 2026-10-14)
- [OpenSSF Scorecard README](https://github.com/ossf/scorecard/blob/main/README.md) (refine 2026-10-14)
- [Keep a Changelog](https://keepachangelog.com/)
- [PEP 561 — py.typed](https://peps.python.org/pep-0561/)
- GitHub repositories: httpie/cli, fastapi/typer, dbcli/pgcli, dbcli/litecli, nicolargo/glances, gleitz/howdoi, astral-sh/ruff

## Run History

- 2026-10-14 (refine, Claude Fable 5.1): added section 1.2a on badges that stay truthful and on
  which badge mechanisms render for private or internal repositories (GitHub-native workflow badges,
  shields.io static badges, committed SVGs via `genbadge --local` with `[skip ci]`), with six new
  sources. The original sections are unchanged. Generalized from work on a private repository; no
  repository-specific detail is recorded here.
