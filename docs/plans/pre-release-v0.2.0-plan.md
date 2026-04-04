# Pre-Release v0.2.0 — Implementation Plan

**Status:** DRAFT
**Created:** 2026-04-04
**Target version:** `0.2.0` (current: `0.1.1`)

## Table of Contents

- [Overview](#overview)
- [Phase Breakdown](#phase-breakdown)
  - [Phase 1 — Test Quality and Coverage](#phase-1--test-quality-and-coverage)
  - [Phase 2 — Process Hygiene](#phase-2--process-hygiene)
  - [Phase 3 — Privacy Audit](#phase-3--privacy-audit)
  - [Phase 4 — Git History Backup and Squash](#phase-4--git-history-backup-and-squash)
  - [Phase 5 — Version Bump and PyPI Publish](#phase-5--version-bump-and-pypi-publish)
- [Sequence and Gates](#sequence-and-gates)
- [Out of Scope (Post-Release)](#out-of-scope-post-release)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Full pre-release sequence for the next version bump and PyPI publish. Covers test quality recovery, process hygiene, privacy scrub, git history cleanup, and publish. Each phase has a hard gate before moving to the next. Phases 1–3 can partially overlap but Phase 4 (squash) must come last before Phase 5 (publish).

> **Feedback Round 1:**
> - <enter feedback here>

## Phase Breakdown

### Phase 1 — Test Quality and Coverage

**Task:** `[AI-CLI-17]`
**Plan:** `docs/plans/test-quality-coverage-plan.md`
**Status:** In progress

Comprehensive test suite quality audit and coverage recovery following the test_main.py split. Target: ~100% coverage, no lazily-added pragmas, vacuous assertions fixed, all conftest helpers in place.

**Sub-tasks (T-00 through T-07):**

| Task | Description | Status |
|------|-------------|--------|
| T-00 | Expand conftest.py with shared helpers (run_cli, make_subprocess_result, make_iterm2_config) | Done |
| T-01 | Fix vacuous assertions in quota and iterm2 tests | In progress |
| T-02 | Fix medium issues (patch targets, dead code, weak publish assertions, real subprocess) | In progress |
| T-03 | Remove 3 stale sync.py `# pragma: no cover`, add real mocked tests | In progress |
| T-04 | Cover 155 missing lines in main.py | Pending |
| T-05 | Cover gaps in quota/layout/gemini/icon_generator/messaging | Pending |
| T-06 | Low-severity cleanup | Pending |
| T-07 | Update docs (usage reference, README, code comments) | Pending |

**Gate:** CI badge green on `main`, Codecov at target. Any remaining uncovered lines presented with options/pros/cons/recommendation for explicit approval before adding any `# pragma: no cover`.

---

### Phase 2 — Process Hygiene

**Task:** `[AI-CLI-28]`
**Plan:** `docs/plans/process-hygiene-plan.md`
**Status:** Plan approved, not started

Add `ai ps` command showing all ai-cli-managed processes (mosh-server orphans, signal-watch, autossh tunnels) with age and status. Add `ai ps clean` to kill orphaned processes. Scoring system for stale session detection. Termius orphan auto-kill at score threshold.

**Gate:** `ai ps` and `ai ps clean` working locally and on Hetzner. Tests pass. CI green.

---

### Phase 3 — Privacy Audit

**Task:** New — `[AI-CLI-30]` (to be added to roadmap)
**Status:** Not started

Scrub all proprietary and personal references from code, tests, docs, and comments. Must conform to Public Open-Source Package Standards in `CLAUDE.md`.

**Scope — what to find and fix:**

| Location | Issue | Fix |
|----------|-------|-----|
| `src/ai_cli/gemini.py:155` | `"sergei"` hardcoded as Doppler project name | Make configurable via config key |
| `src/ai_cli/messaging.py:18` | `"aido"` hardcoded as NATS topic | Remove or make fully configurable |
| `src/ai_cli/messaging.py:50–55` | `"sergei"`, `"178.104.70.139"` as hardcoded defaults | Remove defaults or use `None` |
| `src/ai_cli/main.py:443,564,566` | `sw-1`, `aido-2` in comments | Replace with generic examples (`myproject-1`, `session-2`) |
| `src/ai_cli/main.py:720` | `# sergei = "purple"` commented-out personal config | Remove |
| `tests/test_project.py` | `sergei.toml`, `"sergei"` project name, `/home/sergei/` paths | Rename to `registry.toml`, `"myproject"`, `/home/user/` |
| `tests/test_sync.py:50–51` | `_MAC_PREFIX = "-Users-sergeiwallace-projects-"`, `_SERVER_PREFIX = "-home-sergei-projects-"` | Generalize to `-Users-user-projects-`, `-home-user-projects-` |
| `tests/test_cli.py:378` | `{"sw": "sergei"}` alias | Generic: `{"mp": "myproject"}` |
| `tests/test_cli.py:1059` | `sergei.toml` registry path | `registry.toml` |
| `tests/test_cli.py:1608` | `"178.104.70.139"`, `"sergei"` in tunnel config | `"192.0.2.1"`, `"user"` |
| `tests/test_cli.py:1821,1830,1837` | `humanware-mobile`, `"sergei"` project names | Generic names |
| `tests/test_messaging.py:276,284,334,355,374` | `"178.104.70.139"`, `"sergei"` | `"192.0.2.1"`, `"user"` |
| `tests/test_messaging_jetstream.py:260` | `"aido"` NATS topic in test | Generic |
| `tests/test_session.py:419,425` | `"sergei"` in `.gemini/tmp/` path | `"user"` |
| `docs/bugs/` | Session names like `c-aido-2`, `c-art-2` in bug reports | Leave as-is (historical bug docs, not public API) |

**Note on `setup.py` and `test_setup.py`:** `humanware` appears as the feature name throughout (e.g. `_is_humanware_platform()`, "humanware platform detected"). **Decision (2026-04-04):** rename to generic — `_is_managed_platform()` / "managed platform detected". All references in `setup.py`, `test_setup.py`, and any docs updated accordingly.

**Audit command to verify clean:**
```bash
git grep -rn "humanware\|aido\|\bsergei\b\|sergeiwallace\|178\.104" -- src/ tests/
```

Expected residual after cleanup: only `CLAUDE.md`, `GEMINI.md`, `README.md` (where these names appear in their correct context as rule definitions), and `setup.py`/`test_setup.py` pending the humanware rename decision.

**Gate:** `git grep` returns zero hits in `src/` and `tests/`. CI green.

---

### Phase 4 — Git History Backup and Squash

**Status:** Not started

The full commit history contains commit messages referencing private platform names and personal identifiers. Squashing to a single clean initial commit eliminates all leakage.

**Steps (in order — human executes Phase 4):**

1. **Local bare clone backup:**
   ```bash
   git clone --mirror /Users/sergeiwallace/projects/ai-cli-utils \
     /Users/sergeiwallace/projects/ai-cli-utils-history.git
   ```

2. **Push to private GitHub repo:**
   - Create private repo `ai-cli-utils-history` on GitHub
   - `git -C /Users/sergeiwallace/projects/ai-cli-utils-history.git remote set-url origin <private-repo-url>`
   - `git -C /Users/sergeiwallace/projects/ai-cli-utils-history.git push --mirror`

3. **Squash all history to a single commit:**
   ```bash
   cd /Users/sergeiwallace/projects/ai-cli-utils
   git checkout --orphan clean-history
   git add -A
   git commit -m "Initial release"
   git branch -D main
   git branch -m main
   ```

4. **Force-push clean history:**
   ```bash
   git push origin main --force
   ```

**Gate:** History backup confirmed on both local bare clone and private GitHub repo before squash. Human executes and confirms. No automated execution of this phase.

---

### Phase 5 — Version Bump and PyPI Publish

**Status:** Not started

**Steps:**

1. Bump version in `pyproject.toml`
2. Update `CHANGELOG.md` with release notes
3. Commit: `chore: release vX.Y.Z`
4. Tag: `git tag vX.Y.Z`
5. Push tag to trigger GH Release workflow (`.github/workflows/release.yml`)
6. Confirm PyPI publish succeeds via `gh run list`
7. Verify on PyPI: `pip install ai-cli-utils==X.Y.Z`

**Gate:** PyPI package installable and `ai --version` returns correct version. Human confirms.

---

## Sequence and Gates

```
Phase 1 (Tests)
    ↓ CI green + Codecov at target
Phase 2 (Process Hygiene)
    ↓ CI green
Phase 3 (Privacy Audit)
    ↓ git grep clean + CI green
Phase 4 (Backup + Squash)   ← human executes
    ↓ backup confirmed on both local + private GitHub
Phase 5 (Version Bump + Publish)
    ↓ PyPI confirmed
Done
```

| Gate | After | Who |
|------|-------|-----|
| CI green + coverage at target | Phase 1 complete | Automated |
| Any new `# pragma: no cover` | Phase 1 | Human approval required |
| CI green | Phase 2 complete | Automated |
| `git grep` returns zero hits in src/+tests/ | Phase 3 complete | CI + human verify |
| humanware rename decision | Phase 3 start | Human (see Open Questions) |
| Backup confirmed before squash | Phase 4 | Human |
| PyPI installable | Phase 5 | Human |

---

## Out of Scope (Post-Release)

These tasks are intentionally deferred until after the release:

- **`[AI-CLI-16]`** — Handoff observability checkpoint (NATS handoff reliability, layer attribution, fix gaps)
- **`[AI-CLI-29]`** — Windows out-of-box support (OS-aware paths, Windows-compatible subprocess calls, CI matrix expansion)

---

## Open Questions

~~1. **`humanware` rename in setup.py**~~ — **Resolved 2026-04-04:** rename to `_is_managed_platform()` / "managed platform detected".

> **Feedback Round 1:**
> - <enter feedback here>

## Approval Log

| Date | Round | Decision |
|------|-------|----------|
