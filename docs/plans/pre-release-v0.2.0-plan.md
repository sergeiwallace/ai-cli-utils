# Pre-Release v0.2.0 — Implementation Plan

**Status:** IN PROGRESS
**Created:** 2026-04-04
**Updated:** 2026-04-05
**Target version:** `0.2.0` (current: `0.1.1`)

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
- [Phase Breakdown](#phase-breakdown)
  - [Phase 1 — Test Quality and Coverage](#phase-1--test-quality-and-coverage)
  - [Phase 2 — Process Hygiene](#phase-2--process-hygiene)
  - [Phase 3 — Privacy Audit](#phase-3--privacy-audit)
  - [Phase 3.5 — CLAUDE.md / GEMINI.md Alignment](#phase-35--claudemd--geminimd-alignment)
  - [Phase 3.6 — iTerm2 Shift+Enter Key Binding Automation](#phase-36--iterm2-shiftenter-key-binding-automation)
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
**Status:** Done

Comprehensive test suite quality audit and coverage recovery following the test_main.py split. Target: ~100% coverage, no lazily-added pragmas, vacuous assertions fixed, all conftest helpers in place.

**Sub-tasks (T-00 through T-07):**

| Task | Description | Status |
|------|-------------|--------|
| T-00 | Expand conftest.py with shared helpers (run_cli, make_subprocess_result, make_iterm2_config) | Done |
| T-01 | Fix vacuous assertions in quota and iterm2 tests | Done |
| T-02 | Fix medium issues (patch targets, dead code, weak publish assertions, real subprocess) | Done |
| T-03 | Remove 3 stale sync.py `# pragma: no cover`, add real mocked tests | Done |
| T-04 | Cover missing lines in main.py | Done — 99% total, 32 lines remaining (see gate) |
| T-05 | Cover gaps in quota/layout/gemini/icon_generator/messaging | Done — all at 100% |
| T-06 | Low-severity cleanup | Done |
| T-07 | Update docs (usage reference, README, code comments) | Done |

**Results:** 1052 tests pass (2 skipped), 99% total line coverage (32 of 3889 statements uncovered).
CI badge: green. Codecov upload: queued on commit `ed3334d`.

**Gate — pragma approval required:** 32 uncovered lines remain. All are infrastructure-dependent
callbacks that cannot be tested without a live NATS server or real mosh binary. Presented below
for explicit approval.

#### Pragma Candidates

**Group A — `main.py`: async NATS closures inside `cli()` (30 lines)**

These are closures (`_on_handoff`, `_write_pending_if_claimed`, `_drain`) defined inside `cli()`
that capture outer-scope variables and are only called when a live NATS JetStream message arrives.

| Lines | Description |
|-------|-------------|
| 1969–1970 | `except OSError: pass` in `_on_handoff` — file write error handler |
| 1995–1996 | `except ValueError: continue` in startup scan — malformed filename |
| 2006–2007 | `except OSError:` in startup scan — file read error |
| 2024–2025 | `except Exception: pass` wrapping `subscribe_durable` call |
| 2051 | `_write_pending_if_claimed` — `not for_machine` early return |
| 2053 | `_write_pending_if_claimed` — `hd_handoff_dir is None` early return |
| 2064–2069 | Cross-machine file write path inside `_write_pending_if_claimed` |
| 2072 | `claimed is None` early return in `_write_pending_if_claimed` |
| 2113–2114 | `except Exception: pass` in local-scan try block |
| 2127–2128 | `_drain()` — `not hd_client.js` early return |
| 2140–2141 | `except Exception: data = {}` in `_drain()` message loop |
| 2144 | `if _write_pending_if_claimed(data): return` in `_drain()` |
| 2147–2148 | `except Exception` for JetStream subscribe failure |
| 2154–2155 | `except Exception` for `asyncio.run(_drain())` |

**Group B — `main.py`: VPN mosh reconnect fallback (2 lines)**

| Lines | Description |
|-------|-------------|
| 2766–2767 | "SSH failed + VPN dropped → reconnect via mosh" branch — requires mosh + network state |

**Group C — `messaging.py` line 90: NATS error callback (1 line)**

| Lines | Description |
|-------|-------------|
| 90 | `async def _noop_error_cb(e): pass` — only invoked by NATS library on connection error |

**Options:**

| Option | Summary | Rec |
|--------|---------|-----|
| A | `# pragma: no cover` on all 32 lines | **Recommended** |
| B | Extract closures to module-level, test with injected deps | Architecture change — deferred |
| C | Add NATS server to CI matrix | CI complexity + still can't unit-test closures in isolation |

**Recommendation: Option A.** All 32 are genuine infrastructure boundaries. Option B is a valid
refactor but adds scope beyond this release. Option C solves Group A but not B or C.

---

### Phase 2 — Process Hygiene

**Task:** `[AI-CLI-28]`
**Plan:** `docs/plans/process-hygiene-plan.md`
**Status:** Done

Add `ai ps` command showing all ai-cli-managed processes (mosh-server orphans, signal-watch, autossh tunnels) with age and status. Add `ai ps clean` to kill orphaned processes. Scoring system for stale session detection. Termius orphan auto-kill at score threshold.

**Gate:** `ai ps` and `ai ps clean` working locally and on Hetzner. Tests pass. CI green.

---

### Phase 3 — Privacy Audit

**Task:** `[AI-CLI-30]`
**Status:** Done

Scrub all proprietary and personal references from code, tests, docs, and comments. Must conform to Public Open-Source Package Standards in `CLAUDE.md`.

**Scope — what to find and fix:**

| Location | Issue | Fix |
|----------|-------|-----|
| `src/ai_cli/gemini.py:155` | `"<private-project-name>"` hardcoded as Doppler project name | Make configurable via config key |
| `src/ai_cli/messaging.py:18` | `"aido"` hardcoded as NATS topic | Remove or make fully configurable |
| `src/ai_cli/messaging.py:50–55` | `"<private-project-name>"`, `"178.104.70.139"` as hardcoded defaults | Remove defaults or use `None` |
| `src/ai_cli/main.py:443,564,566` | `sw-1`, `aido-2` in comments | Replace with generic examples (`myproject-1`, `session-2`) |
| `src/ai_cli/main.py:720` | `# <private-project-name> = "purple"` commented-out personal config | Remove |
| `tests/test_project.py` | `<private-project-name>.toml`, `"<private-project-name>"` project name, personal home-dir paths | Rename to `registry.toml`, `"myproject"`, `/home/user/` |
| `tests/test_sync.py:50–51` | `_MAC_PREFIX`/`_SERVER_PREFIX` hardcode personal home-dir prefixes | Generalize to `-Users-user-projects-`, `-home-user-projects-` |
| `tests/test_cli.py:378` | `{"sw": "<private-project-name>"}` alias | Generic: `{"mp": "myproject"}` |
| `tests/test_cli.py:1059` | `<private-project-name>.toml` registry path | `registry.toml` |
| `tests/test_cli.py:1608` | `"178.104.70.139"`, `"<private-project-name>"` in tunnel config | `"192.0.2.1"`, `"user"` |
| `tests/test_cli.py:1821,1830,1837` | `ai-ide-mobile`, `"<private-project-name>"` project names | Generic names |
| `tests/test_messaging.py:276,284,334,355,374` | `"178.104.70.139"`, `"<private-project-name>"` | `"192.0.2.1"`, `"user"` |
| `tests/test_messaging_jetstream.py:260` | `"aido"` NATS topic in test | Generic |
| `tests/test_session.py:419,425` | `"<private-project-name>"` in `.gemini/tmp/` path | `"user"` |
| `docs/bugs/` | Session names like `c-aido-2`, `c-art-2` in bug reports | Leave as-is (historical bug docs, not public API) |

**Note on `setup.py` and `test_setup.py`:** `ai-core` appears as the feature name throughout (e.g. `_is_managed_platform()`, "managed platform detected"). **Decision (2026-04-04):** rename to generic — `_is_managed_platform()` / "managed platform detected". All references in `setup.py`, `test_setup.py`, and any docs updated accordingly.

**Audit command to verify clean:**
```bash
git grep -rn "ai-core\|aido\|<private-project-name>\|sergeiwallace\|178\.104" -- src/ tests/
```text

Expected residual after cleanup: only `CLAUDE.md`, `GEMINI.md`, `README.md` (where these names appear in their correct context as rule definitions), and `setup.py`/`test_setup.py` pending the ai-core rename decision.

**Gate:** `git grep` returns zero hits in `src/` and `tests/`. CI green.

---

### Phase 3.5 — CLAUDE.md / GEMINI.md Alignment

**Task:** `[AI-CLI-31]`
**Status:** Done

Write a lint script that extracts shared sections from both `CLAUDE.md` and `GEMINI.md` and fails if they differ. Wire to CI. Small lift — shared sections are already defined, GEMINI.md was just synced with CLAUDE.md additions in this release.

**Gate:** Lint script passes in CI on clean files; fails demonstrably on an intentional drift.

---

### Phase 3.6 — iTerm2 Shift+Enter Key Binding Automation

**Task:** `[AI-CLI-37]`
**Status:** Not started

Currently the Shift+Enter → newline binding for CC sessions is a manual step: import `assets/iterm2-key-bindings/shift_enter_cc_new_line_iterm2_key_binding.itermkeymap` via iTerm2 Preferences. This is fragile — new users miss it.

**Recommended approach:** Inject `"Key Mappings"` directly into the per-session DynamicProfile JSON in `generate_dynamic_profile()` (`icon_generator.py`). The binding (`0xd-0x20000-0x24` → CSI `[13;2u`) applies automatically when `ai c` starts and is cleaned up on exit. No import step needed.

**Scope:**
1. Add `"Key Mappings"` to `generate_dynamic_profile()` in `icon_generator.py`
2. Delete `assets/iterm2-key-bindings/` from git
3. Update `docs/tools/iterm2-setup.md` — remove manual step, note it's automatic
4. Update tests for `generate_dynamic_profile()`

**Gate:** `ai c` session launches with Shift+Enter working without any manual iTerm2 configuration.

---

### Phase 4 — Git History Backup and Squash

**Status:** Not started

The full commit history contains commit messages referencing private platform names and personal identifiers. Squashing to a single clean initial commit eliminates all leakage.

Phase 4 is split into two sub-phases: **4a (backup — Claude executes)** and **4b (squash — human gate required before Claude executes)**.

#### Phase 4a — Backup (Claude executes)

1. **Local bare clone backup (Mac):**
   ```bash
   git clone --mirror /Users/user/projects/ai-cli-utils \
     /Users/user/projects-archive/ai-cli-utils-history.git
   ```text

2. **Create private GitHub backup repo and push:**
   ```bash
   gh repo create ai-cli-utils-history --private \
     --description "Full git history backup of ai-cli-utils (pre-squash). See README."
   git -C /Users/user/projects-archive/ai-cli-utils-history.git \
     remote set-url origin git@github.com:sergeiwallace/ai-cli-utils-history.git
   git -C /Users/user/projects-archive/ai-cli-utils-history.git push --mirror
   ```text

3. **Add README to backup repo** noting its purpose (pre-squash history archive, not the active repo).

**Gate (human):** User confirms both backups exist — local bare clone at
`~/projects-archive/ai-cli-utils-history.git` and private GitHub repo `sergeiwallace/ai-cli-utils-history`.
Must give **explicit approval** before Phase 4b proceeds.

#### Phase 4b — Squash (requires explicit human approval from Phase 4a gate)

Once backup is confirmed and approved:

1. **Squash all history to a single commit:**
   ```bash
   cd /Users/user/projects/ai-cli-utils
   git checkout --orphan clean-history
   git add -A
   git commit -m "Initial release"
   git branch -D main
   git branch -m main
   ```text

2. **Force-push clean history:**
   ```bash
   git push origin main --force
   ```text

**Gate:** Force-push to main. Irreversible without the backup. Do not execute without explicit human approval.

---

### Phase 5 — Final Review

**Status:** Not started

After Phase 4b (squash), do a full final review before version bump. Split into automated checks (Claude runs) and human review items (needs human eyes).

#### Step 1 — Privacy & Public Safety (Claude)

- `git grep -rn "ai-core\|aido\|<private-project-name>\|sergeiwallace\|178\.104"` across entire repo (not just src/tests — also docs, configs, comments, scripts)
- Grep for private email patterns and internal hostnames
- Verify `git log --oneline` shows single "Initial release" commit — no history leakage

#### Step 2 — CI / Code Quality Gate (Claude)

- Confirm CI is green on squashed history: `gh run list --limit 3`
- If CI has not re-run since squash, push a no-op commit to trigger it
- Confirm Codecov still at 99% after re-run

#### Step 3 — Package Metadata Audit (`pyproject.toml`) (Claude)

- `version` — confirm ready to bump `0.1.1` → `0.2.0`
- `description`, `keywords`, `classifiers` — accurate and complete for PyPI listing
- `authors` email (`dev@sergeiwallace.com`) — public-facing, confirm intentional
- `requires-python`, `dependencies` — nothing private, no overly tight pins
- `[project.scripts]` entry points — correct

#### Step 4 — CHANGELOG (Claude)

- Confirm `[Unreleased]` section is complete — all features since v0.1.1 listed
- Cross-check against roadmap done items for any missing entries
- Ready to rename to `[0.2.0] - YYYY-MM-DD` at version bump time

#### Step 5 — README & Docs Audit (human)

- Install instructions — correct for v0.2.0?
- Feature list — reflects what's actually in v0.2.0?
- Any screenshots or examples referencing private paths/names?
- `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` — nothing outdated
- `docs/tools/ai-cli-usage.md` — current with all commands (`ai ps`, `ai gemini`, `ai layout`, etc.)?
- `docs/tools/iterm2-setup.md` — accurate for external users?
- `setup.sh` — any hardcoded personal paths?

#### Step 6 — Local Install Smoke Test (human)

```bash
uv build
pip install dist/ai_cli_utils-0.2.0-*.whl --force-reinstall
ai --version         # expect: 0.2.0
ai --help            # commands render correctly
ai gemini --help
ai layout --help     # shows usage (not treated as layout name)
ai ps                # shows process health list
```text

#### Step 7 — TestPyPI Upload

- Build: `uv build`
- Upload to TestPyPI: `uv publish --publish-url https://test.pypi.org/legacy/`
- Install from TestPyPI in a clean venv: `pip install --index-url https://test.pypi.org/simple/ ai-cli-utils==0.2.0`
- Smoke test: `ai --version`, `ai --help`
- Confirm before proceeding to live PyPI publish

#### Step 8 — Present Findings (Claude)

- Summarize all findings from Steps 1–4
- Flag any open questions or decisions
- Wait for human approval before Phase 6

**Gate:** Human reviews Steps 5–6 and approves before version bump.

---

### Phase 6 — Version Bump and PyPI Publish

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

```text
Phase 1 (Tests)
    ↓ CI green + Codecov at target
Phase 2 (Process Hygiene)
    ↓ CI green
Phase 3 (Privacy Audit)
    ↓ git grep clean + CI green
Phase 3.5 (CLAUDE/GEMINI Alignment)
    ↓ lint script passing in CI
Phase 3.6 (iTerm2 key binding automation)
    ↓ CI green
Phase 4a (Backup)   ← Claude executes
    ↓ human confirms backup exists on local + private GitHub → explicit approval
Phase 4b (Squash)   ← Claude executes after explicit human approval
    ↓ CI passes on squashed history
Phase 5 (Final Review)   ← human approves before bump
    ↓ human approves
Phase 6 (Version Bump + Publish)
    ↓ PyPI confirmed
Done
```text

| Gate | After | Who |
|------|-------|-----|
| CI green + coverage at target | Phase 1 complete | Automated |
| Any new `# pragma: no cover` | Phase 1 | Human approval required |
| CI green | Phase 2 complete | Automated |
| `git grep` returns zero hits in src/+tests/ | Phase 3 complete | CI + human verify |
| Alignment lint script passing in CI | Phase 3.5 complete | Automated |
| CI green | Phase 3.6 complete | Automated |
| Backup confirmed before squash | Phase 4a complete | Human — **explicit approval required** |
| Force-push squash | Phase 4b | Human approval gate |
| Final review approved | Phase 5 complete | Human |
| PyPI installable | Phase 6 | Human |

---

## Out of Scope (Post-Release)

These tasks are intentionally deferred until after the release:

- **`[AI-CLI-16]`** — Handoff queue reliability + testing (same-machine and cross-machine scenarios, all 5 pickup layers)
- **`[AI-CLI-29]`** — Windows out-of-box support (OS-aware paths, Windows-compatible subprocess calls, CI matrix expansion)

---

## Open Questions

1. ~~**`ai-core` rename in setup.py**~~ — **Resolved 2026-04-04:** rename to `_is_managed_platform()` / "managed platform detected".

> **Feedback Round 1:**
> - <enter feedback here>

## Approval Log

| Date | Round | Decision |
|------|-------|----------|
| 2026-04-04 | 1 | Phases 1–3 approved for autonomous execution. Human gate before Phase 4. |
| 2026-04-05 | 2 | Phase 1 implementation complete (99% coverage, 1052 tests). Pragma approval for 32 lines pending. |
| 2026-04-05 | 3 | Pragma gate resolved: no pragma added. Inline `# Not covered:` comments added at each site; full documentation added to `docs/test/unit-tests.md §Intentionally Uncovered Lines`. Phase 1 complete. |
| 2026-04-05 | 4 | Phases 2 (process hygiene), 3 (privacy audit), 3.5 (CLAUDE/GEMINI alignment lint) complete. Proceeding to Phase 4a (backup). |
| 2026-04-06 | 5 | Phase 4a backups confirmed (local bare clone at `~/projects-archive/ai-cli-utils-history.git` + private GitHub `sergeiwallace/ai-cli-utils-history`). Phase 4b squash approved and executed — single commit `6201c1f`. Phase 5 plan expanded and approved. |
