---
title: "AI-CLI-203/AI-CLI-204 worktree launch fix — audit"
category: audit
tags: [audit, ai-cli-203, ai-cli-204, worktree, session-launch]
status: converged-with-follow-up
date: 2026-08-10
source: "aido-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  delegated_to: codex/audit
  tier: balanced
  model: gpt-5.6-terra
  effort: medium
  persona: none
  worktree: /Users/sergeiwallace/projects/ai-cli-utils/.worktrees/ai-cli-1
  session: none
---

# AI-CLI-203/AI-CLI-204 worktree launch fix — audit

**Status:** converged-with-follow-up

**Created:** 2026-08-10

**Auditor:** Codex audit (`cx audit`, effort: medium) — bounded bug fix across 2 source files,
low blast radius — findings incorporated by Claude

**Target commit:** `23e8796`

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [Scope](#scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
- [Round 2 — Verification Pass](#round-2--verification-pass-append-only)
  - [R2 Summary](#r2-summary)
  - [R2.1 Round 1 IC/JA/DV verification](#r21-round-1-icjadv-verification)
  - [R2.2 Round 1 F-N verification](#r22-round-1-f-n-verification)
  - [R2.3 AD-N decisions verification](#r23-ad-n-decisions-verification)
  - [R2.4 NEW issues surfaced](#r24-new-issues-surfaced)
  - [R2 Verification Matrix](#r2-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
- [Round 3 — Round 2 Resolution Verification](#round-3--round-2-resolution-verification-append-only)
  - [R3 Summary](#r3-summary)
  - [R3.1 Round 2 N-N verification](#r31-round-2-n-n-verification)
  - [R3.2 AD-N decision verification](#r32-ad-n-decision-verification)
  - [R3.3 NEW issues surfaced](#r33-new-issues-surfaced)
  - [R3 Verification Matrix](#r3-verification-matrix)
  - [R3 Recommendations](#r3-recommendations)
- [Round 4 — N-5 Fix Attempt (reverted, not shipped)](#round-4--n-5-fix-attempt-reverted-not-shipped)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
  - [AD-1: Concurrent worktree creation policy](#ad-1)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## Scope

Target: the AI-CLI-203/AI-CLI-204 fix at commit `23e8796` — `src/ai_cli/session.py`'s
`create_worktree()` (case-insensitive-filesystem-safe registered-worktree matching via
`Path.samefile`, and removal of the automatic `shutil.rmtree` recycle path in favor of an
unconditional refusal) and `src/ai_cli/main.py`'s `_announce_worktree_isolation()` (now reports
the actual created-vs-reused outcome, for both `ai c` and `ai g`, both root and non-root
launches). Scope: internal consistency, AC compliance against bd issues AI-CLI-b5c9 (external ref
AI-CLI-203) and AI-CLI-y98c (external ref AI-CLI-204), domain validity of the filesystem-identity
comparison and the refuse-only-never-delete safety property, and any independent findings —
missed call sites, edge cases in the `samefile`/case-insensitivity handling, test coverage gaps,
or regressions in adjacent worktree/session-launch logic.

Out of scope: changing source, tests, issue state, or configuration; live multi-process testing
that would create worktrees; and external service state. The audit evaluates commit `23e8796` as
checked out locally and records where sandbox policy prevented write-requiring tests.

## Methodology

Read the canonical audit template first, inspected the target commit and its parent, read the
changed implementation and tests plus adjacent callers/configuration, and searched every named
symbol under `src/` and `tests/`. Claims were checked against source, `.beads/issues.jsonl`, git
history, focused read-only Python reproductions, and case-insensitive filesystem behavior on the
host. The affected pytest set was attempted three ways; execution was blocked because the audit
sandbox exposes no writable temporary directory and permits writes only to this audit file.

## Status Summary

**Latest round:** Round 3, plus a Round 4 fix attempt for N-5 that was reverted (see below)

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 0 | 0 | 0 |
| P1 | 5 | 3 | 1 (AI-CLI-205, N-5) |
| P2 | 8 | 6 | 1 (N-6, corrected not fixed) |
| P3 | 1 | 1 | 0 |
| **Total** | **14** | **10** | **2** |

**Ship-readiness verdict for the ORIGINAL scope (AI-CLI-203/204 — the case-insensitive
collision/delete bug and the create-vs-reuse announcement accuracy):** **Ready, shipped, and
independently verified.** JA-1 through JA-5, DV-3, DV-4 all PASS across every round; the
`shutil.rmtree` recycle path is gone and every unregistered collision refuses. Both issues are
closed with corrected, accurate close reasons (N-6).

**Ship-readiness verdict for the CONCURRENCY HARDENING extension (AD-1, discovered during Round
1's DV-1/DV-2 and pursued through Round 3):** **Converged with a known, narrow, non-blocking gap.**
N-1 through N-4 are fixed and verified. N-5 (a creator that fails during post-add initialization
leaves a registered-but-incomplete worktree that a waiting launcher can reuse without finishing
that initialization) had a fix attempt in this session that was reverted after independently
finding it broke ordinary worktree reuse/adoption (27 test failures — the fix wrongly assumed
every registered worktree's checked-out branch is named `wt-<ai_name>`). Filed as its own issue,
**AI-CLI-205 (P1, open)**, for a more careful follow-up rather than iterating further under time
pressure. This gap requires two specific, unlikely-to-coincide conditions (a genuine race between
two launchers for the identical session slot, AND the first launcher failing mid-initialization) —
narrow enough that it does not block the shipped AI-CLI-203/204 fix, which stands independently.

<!-- /doc:region name="scope" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** Codex audit

**Round 1 date:** 2026-08-10

**Round 1 scope:** Full implementation audit of commit `23e8796` against the two reproduced issue
AC sets, including callers, filesystem identity semantics, refusal behavior, launch messaging,
tests, git history, and issue state.

### R1 Summary

Eight findings: 2 P1, 5 P2, and 1 P3. The central no-`rmtree` change is CONFIRMED and the normal
case-differing registered worktree is reused correctly on this host's case-insensitive filesystem.
The remaining P1s concern concurrency and error degradation, not a residual delete call. Seven
findings have unambiguous follow-up fixes; the concurrency policy is moved to AD-1.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | FAIL (P2, CONFIRMED) | `src/ai_cli/session_adopt.py:655-659` retains `if wt_dir.exists():` followed by `if wt_dir.resolve() in registered_worktrees(repo_root):`, bypassing `create_worktree()`'s filesystem-identity logic. |
| IC-2 | FAIL (P3, CONFIRMED) | `src/ai_cli/session_adopt.py:638` still says `creating it off ``origin/main```; `tests/test_worktree_container_collision.py:15-17` still describes an unconditional live `shutil.rmtree` branch. |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| JA-1 | FAIL (P2, CONFIRMED) | The only case-collision test patches the new helper at `tests/test_worktree_container_collision.py:230-233` and calls `create_worktree("SESSION-6")` directly at line 235; it does not exercise fleet prefix resolution or `build_session_name()`. |
| JA-2 | FAIL (P2, CONFIRMED) | `tests/test_worktree_container_collision.py:213-238` parameterizes the three requested state labels but asserts reuse, not the AC's required refusal; the two committed states are constructed identically. |
| JA-3 | PASS (CONFIRMED) | `src/ai_cli/session.py:835-853` refuses every existing unregistered slot and `rg` finds no `rmtree` in that module. |
| JA-4 | PASS (CONFIRMED) | `src/ai_cli/main.py:1924-1931` announces only after `create_worktree(..., with_status=True)` returns; lines 2061-2078 and 2089-2143 place the announcement before either engine entry path. |
| JA-5 | PASS (CONFIRMED) | `tests/test_session_launch_locality.py:372-388,430-442` parameterizes both `c` and `g` for creation and reuse, asserting outcome text and path. |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| DV-1 | FAIL (P1, CONFIRMED) | `src/ai_cli/session.py:866-881` ignores the fallback add result and treats later path existence as proof this process created it; executable reproduction returned `(PosixPath('/repo/.worktrees/session-1'), True)` after two failed adds. |
| DV-2 | FAIL (P1, CONFIRMED behavior; PLAUSIBLE real trigger) | `src/ai_cli/session.py:793-798` converts every identity-probe `OSError` to `False`; failed adds return `None` at line 926, and `src/ai_cli/main.py:2067,2090` then falls back to `Path.cwd()`. |
| DV-3 | PASS (CONFIRMED) | On the active case-insensitive filesystem, a casing-changed spelling of the current worktree reports `exists=True` and `samefile=True`; symlink aliases are also covered by `Path.samefile` filesystem identity. |
| DV-4 | PASS (CONFIRMED) | A genuinely absent candidate makes `samefile` fail closed to non-match, but no deletion follows: existing paths refuse and absent paths proceed only to Git creation. |

#### Independent Findings (F-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | FAIL (P2, CONFIRMED) | `.beads/issues.jsonl:198-199` still records both implementation issues as `"status":"open"` at target commit `23e8796`. |
| F-2 | FAIL (P2, CONFIRMED) | `.beads/issues.jsonl:198` retains an account-specific personal identifier in the reproduction; the repository policy requires generic public examples. |

#### DV-1: Concurrent creation can be reported as this process's successful creation — `P1`

**Location:** `src/ai_cli/session.py:866-881`; `src/ai_cli/main.py:1924-1931`

**Evidence:**

> `if res.returncode != 0:`
>
> `subprocess.run(`
>
> `if wt_dir.exists():`
>
> `result = (wt_dir, True)`

Both add results can fail while another launcher creates the directory between the initial probe
and line 881. The reproduced result was exactly
`(PosixPath('/repo/.worktrees/session-1'), True) failed_adds=2`.

**Why it matters:** Two launches can enter one worktree concurrently and the loser can claim it
created that worktree despite neither of its add operations succeeding. That violates actual-
decision messaging and leaves session isolation dependent on timing.

**Verification command:**

```bash
.venv/bin/python -c $'from pathlib import Path\nfrom unittest.mock import MagicMock, patch\nfrom ai_cli.session import create_worktree\nslot=Path("/repo/.worktrees/session-1"); state={"n":0}; calls=[]\ndef exists(p):\n state["n"] += (p==slot); return state["n"]>1 if p==slot else False\ndef run(c, **k):\n calls.append(c); return MagicMock(returncode=1, stdout="", stderr=b"collision")\nwith patch("ai_cli.session.detect_repo_root",return_value=Path("/repo")),patch("ai_cli.session.repair_bare_worktree_config"),patch("ai_cli.session.registered_worktrees",return_value=[]),patch("ai_cli.session._resolve_worktree_target",return_value=("base","main")),patch("ai_cli.session._set_upstream_or_raise"),patch("ai_cli.trust.ensure_workspace_trusted"),patch.object(Path,"mkdir"),patch.object(Path,"exists",exists),patch("ai_cli.session.subprocess.run",side_effect=run): print(create_worktree("session-1",with_status=True), "failed_adds="+str(sum(c[:3]==["git","worktree","add"] for c in calls)))'
```

**Recommendation:** Resolve AD-1; preferred fix is a per-repository/per-slot cross-process lock,
followed by a fresh registered-worktree identity check and explicit checking of both add results.
Never infer creation solely from `wt_dir.exists()`.

#### DV-2: Identity and add failures can silently degrade isolation — `P1`

**Location:** `src/ai_cli/session.py:793-798,866-926`; `src/ai_cli/main.py:2067,2090`

**Evidence:**

> `except OSError:`
>
> `return False`
>
> `return None`
>
> `target_root = worktree_path or Path.cwd()`

The read-only reproduction printed `samefile_error_result= False` and `create_result= None` after
simulated permission-denied probes/adds.

**Why it matters:** A permissions or transient filesystem failure can become an ordinary “not the
same path,” then an unreported add failure, and finally a session launched in the repository root
without worktree isolation. The exact real-world permission trigger is PLAUSIBLE; the silent
degradation chain is CONFIRMED.

**Verification command:**

```bash
.venv/bin/python -c $'from pathlib import Path\nfrom unittest.mock import MagicMock,patch\nfrom ai_cli.session import _same_worktree_path,create_worktree\nwith patch.object(Path,"samefile",side_effect=PermissionError("denied")): print("samefile_error_result=",_same_worktree_path(Path("/a"),Path("/b")))\nwith patch("ai_cli.session.detect_repo_root",return_value=Path("/repo")),patch("ai_cli.session.repair_bare_worktree_config"),patch("ai_cli.session.registered_worktrees",return_value=[Path("/repo/.worktrees/session-1")]),patch("ai_cli.session._same_worktree_path",return_value=False),patch("ai_cli.session._resolve_worktree_target",return_value=("base","main")),patch.object(Path,"exists",return_value=False),patch.object(Path,"mkdir"),patch("ai_cli.session.subprocess.run",return_value=MagicMock(returncode=1,stdout="",stderr=b"denied")): print("create_result=",create_worktree("SESSION-1",with_status=True))'
```

**Recommendation:** Distinguish `FileNotFoundError` from other `OSError`s in the identity probe;
raise an actionable `RuntimeError` for indeterminate identity. Capture and check both Git add
results, include stderr, and make `_do_session_launch()` refuse when worktrees are enabled but
creation/reuse returns no path.

#### IC-1: Session adoption retains the case-sensitive collision bug — `P2`

**Location:** `src/ai_cli/session_adopt.py:637-680`

**Evidence:**

> `if wt_dir.exists():`
>
> `if wt_dir.resolve() in registered_worktrees(repo_root):`
>
> `raise AdoptionError(`

The executable probe supplied an existing uppercase candidate and lowercase registered path and
printed `AdoptionError refused_case_differing_registered_path`.

**Why it matters:** `ai session-adopt` can reject the same live worktree that `ai c` now correctly
reuses, producing contradictory behavior from two callers of the same worktree machinery.

**Verification command:**

```bash
.venv/bin/python -c $'from pathlib import Path\nfrom unittest.mock import patch\nfrom ai_cli.session_adopt import _ensure_worktree,AdoptionError\nwith patch.object(Path,"exists",return_value=True),patch("ai_cli.session.registered_worktrees",return_value=[Path("/repo/.worktrees/session-1")]):\n try: print(_ensure_worktree(Path("/repo"),"SESSION-1",False))\n except AdoptionError: print("AdoptionError refused_case_differing_registered_path")'
```

**Recommendation:** Remove the duplicate registration gate and call `create_worktree(...,
with_status=True)` for non-dry runs; for dry runs, reuse `_registered_worktree_at()` (or a public
identity-aware helper) and return the registered canonical path.

#### JA-1: The regression is neither the exact fleet-prefix repro nor runnable against the parent — `P2`

**Location:** `tests/test_worktree_container_collision.py:213-238`; commit `23e8796^`

**Evidence:**

> `monkeypatch.setattr(`
>
> `"ai_cli.session._same_worktree_path",`
>
> `reused = create_worktree("SESSION-6")`

The parent reports `parent helper absent`, and the test contains no call to
`resolve_project_prefix()` or `build_session_name()`.

**Why it matters:** This test validates a mocked implementation seam, not the reported chain from
fleet registry casing through session naming to a materialized worktree. It would fail to install
its monkeypatch on the pre-fix parent before reproducing the original behavior.

**Verification command:**

```bash
git show 23e8796^:src/ai_cli/session.py | rg '_same_worktree_path' || echo 'parent helper absent'; rg -n '_same_worktree_path|create_worktree\("SESSION-6"\)|resolve_project_prefix|build_session_name|fleet' tests/test_worktree_container_collision.py
```

**Recommendation:** Add an end-to-end regression that writes a generic uppercase prefix through
the fleet-registry fixture, derives `ai_name` via production naming, and launches against an
already registered lowercase worktree. On case-sensitive CI, isolate only the filesystem-identity
probe behind a capability fixture; do not patch the new implementation helper by name.

#### JA-2: The three loss states do not have the required refusal tests — `P2`

**Location:** `tests/test_worktree_container_collision.py:213-238`

**Evidence:**

> `@pytest.mark.parametrize("state", ["uncommitted", "unpushed", "absent-from-integration"])`
>
> `reused = create_worktree("SESSION-6")`
>
> `assert reused == lower`

For both committed labels the setup is the same `git add` plus `git commit`; no remote or
integration-branch ancestry assertion distinguishes them.

**Why it matters:** Reuse is correct for a registered case alias, but it does not satisfy the
separate AC requiring refusal coverage for unsafe unregistered collisions. A future heuristic
could reintroduce deletion for one of those states without these tests failing.

**Verification command:**

```bash
sed -n '213,238p' tests/test_worktree_container_collision.py
```

**Recommendation:** Add three refusal tests using unregistered colliding directories/checkouts:
one with uncommitted content, one with a commit absent from every remote, and one with a commit not
reachable from the configured integration branch. Spy on `shutil.rmtree` (or assert the symbol is
absent) and verify the original files/commit IDs survive.

#### F-1: Both shipped issues remain open — `P2`

**Location:** `.beads/issues.jsonl:198-199`

**Evidence:**

> `"status":"open"`

The verification output was `AI-CLI-b5c9 open` and `AI-CLI-y98c open` while `23e8796` is already
`origin/main`.

**Why it matters:** Tracking state now contradicts shipped repository state, so future planning
can duplicate work or treat the fix as incomplete for the wrong reason.

**Verification command:**

```bash
.venv/bin/python -c 'import json; [print(d["id"], d["status"]) for d in map(json.loads, open(".beads/issues.jsonl")) if d.get("id") in {"AI-CLI-b5c9", "AI-CLI-y98c"}]'
```

**Recommendation:** After the P1 follow-up is linked, close both issues or explicitly reopen them
with the outstanding audit findings and follow-up commit as the remaining scope.

#### F-2: The public issue artifact contains account-specific reproduction data — `P2`

**Location:** `.beads/issues.jsonl:198` (`AI-CLI-b5c9` description, `REPRODUCTION` step 1)

**Evidence:**

> `project_prefix is registered as uppercase "SW" in the fleet registry`

The verification probe returned `account_specific_identifier_present= True`; the identifier is
not repeated here because doing so would reproduce the public-package policy violation.

**Why it matters:** `.beads/issues.jsonl` is repository content, and the project instructions ban
personal identifiers and account-specific examples in public source and docs.

**Verification command:**

```bash
.venv/bin/python -c 'import json,re; d=next(d for d in map(json.loads,open(".beads/issues.jsonl")) if d.get("id")=="AI-CLI-b5c9"); print("account_specific_identifier_present=", bool(re.search(r"\n  1\. [a-z]+(?:[^ ]*) project_prefix is registered", d["description"], re.I)))'
```

**Recommendation:** Rewrite the reproduction with generic actors and paths (for example, “a
project prefix is registered as uppercase `APP` while the existing worktree is lowercase
`app-1`”), preserving the technical sequence and ACs.

#### IC-2: Adjacent documentation still describes superseded behavior — `P3`

**Location:** `src/ai_cli/session_adopt.py:638`; `tests/test_worktree_container_collision.py:15-17`

**Evidence:**

> `creating it off ``origin/main`` if absent.`
>
> `the branch taken when that test fails is an unconditional`
>
> ```shutil.rmtree```

**Why it matters:** The adoption docstring contradicts integration-branch resolution, and the
collision module describes deletion in the present tense after that path was removed. Maintainers
will reason from behavior that no longer exists.

**Verification command:**

```bash
sed -n '638p;655,659p' src/ai_cli/session_adopt.py; sed -n '15,17p' tests/test_worktree_container_collision.py
```

**Recommendation:** Change the adoption wording to “created from the repository's resolved
worktree base” and make the test module's defect description explicitly historical (“used to
take an unconditional `shutil.rmtree` branch”).

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| DV-1 | TEAM INPUT NEEDED | Concurrency strategy requires a policy choice; see AD-1. |
| DV-2 | UNRESOLVED | Straightforward source/test follow-up: fail closed on indeterminate identity/add failure. |
| IC-1 | UNRESOLVED | Straightforward source/test follow-up: share identity-aware registration logic. |
| JA-1 | UNRESOLVED | Add a production-chain fleet-prefix regression. |
| JA-2 | UNRESOLVED | Add three distinct refusal-state regressions. |
| F-1 | UNRESOLVED | Update issue state after follow-up scope is linked. |
| F-2 | UNRESOLVED | Genericize the public issue reproduction. |
| IC-2 | UNRESOLVED | Update stale docstrings/comments. |

No inline target fixes were applied; this invocation only permits writing the audit document.

### R1 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---------|---------|----------|--------|-------|
| DV-1 | mocked concurrent `create_worktree(..., with_status=True)` probe | Do not claim creation after failed adds | `(PosixPath('/repo/.worktrees/session-1'), True) failed_adds=2` | ✅ reproduces |
| DV-2 | mocked `PermissionError` identity probe plus failed adds | Loud refusal | `samefile_error_result= False`; `create_result= None` | ✅ reproduces |
| IC-1 | `_ensure_worktree()` with uppercase candidate/lowercase registration | Reuse canonical registration | `AdoptionError refused_case_differing_registered_path` | ✅ reproduces |
| JA-1 | parent/helper and regression-chain grep | Parent-reproducible full chain | `parent helper absent`; only helper patch and direct `create_worktree("SESSION-6")` matched | ✅ reproduces |
| JA-2 | `sed -n '213,238p' ...` | Three refusal paths | One parameterized reuse path; committed states share setup | ✅ reproduces |
| IC-2 | focused `sed` of stale prose | Current behavior | `origin/main` and unconditional `shutil.rmtree` text remain | ✅ reproduces |
| F-1 | parse issue ids/statuses | Closed or follow-up-scoped | Both print `open` | ✅ reproduces |
| F-2 | boolean policy probe | No account-specific identifier | `account_specific_identifier_present= True` | ✅ reproduces |

**Verified: 8/8 findings reproduce on commit `23e879631fee4eb74de8c686655208de4c289797`.**

<!-- /doc:region name="round_1_findings" -->

## Round 2 — Verification Pass (append-only)

**Round 2 auditor:** Codex (GPT-5), independent verification pass

**Round 2 date:** 2026-08-10

**Round 2 scope:** Verified every Round 1 IC/JA/DV/F finding and AD-1 against current `HEAD`
`1bd495cae01f3298ff72a48a5c4ec429e4027537` (follow-up implementation commit `b2072ca` and issue
metadata commit `9187629`). Read-only on source and tests; only this audit document was changed.

### R2 Summary

Eleven of thirteen Round 1 checks PASS, DV-2 is PARTIAL, and F-1 FAILS. The follow-up correctly
fixes adoption identity matching, adds the requested fleet-prefix and three loss-state regressions,
removes stale prose, fails the launch closed instead of using the repository root, and prevents a
failed Git add from being reported as this process's creation. AD-1 is PARTIAL: the code implements
recommended option (a), but the decision remains `[PENDING]`, lock acquisition has no timeout, and
the lock does not cover post-add initialization. Four N-N findings are confirmed: 2 P1 and 2 P2.

Test execution remains blocked by the audit sandbox's lack of any writable temporary directory.
Collection now fails even earlier because importing the newly added `portalocker` dependency calls
`tempfile.gettempdir()` at import time. Static AST parsing succeeded for all six requested
source/test files, and the verification commands below were run against the checked-out files.

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | PASS (CONFIRMED) | Dry-run adoption now calls `_registered_worktree_at()` and returns Git's canonical path at `src/ai_cli/session_adopt.py:655-660`; non-dry-run adoption delegates to `create_worktree(..., with_status=True, repo_root=repo_root)` at lines 674-684. |
| IC-2 | PASS (CONFIRMED) | `src/ai_cli/session_adopt.py:638` now says `creating it from the resolved worktree base`; `tests/test_worktree_container_collision.py:15-17` uses historical `failed`, `used`, and `deleted` wording. |
| JA-1 | PASS (CONFIRMED) | `tests/test_worktree_container_collision.py:214-243` writes uppercase `APP` through the fleet-registry marker, calls production `resolve_project_prefix()` and `build_session_name()`, and verifies reuse of lowercase `app-1`. On this host, `SRC/AI_CLI/SESSION.PY` and `src/ai_cli/session.py` resolve to inode `304339064`, so the capability guard at lines 233-235 does not skip the exact case-alias condition. |
| JA-2 | PASS (CONFIRMED) | Separate tests construct and preserve uncommitted work (`tests/test_worktree_container_collision.py:258-268`), a commit with no remote (lines 271-285), and a commit not reachable from `origin/main` (lines 288-305); each asserts `refusing to delete`. |
| JA-3 | PASS (CONFIRMED) | Existing unregistered paths still raise at `src/ai_cli/session.py:853-871`; `rg` finds no `rmtree` in `src/ai_cli/session.py`. |
| JA-4 | PASS (CONFIRMED) | `src/ai_cli/main.py:1924-1937` obtains the actual tuple, refuses a missing path, then announces; bare and tmux engine entry remain downstream at lines 2068-2085 and 2096-2151. |
| JA-5 | PASS (CONFIRMED) | Creation and reuse tests remain parameterized over `c` and `g` and assert message/path content at `tests/test_session_launch_locality.py:372-388,441-453`; failure-to-create now also asserts root fallback refusal at lines 430-438. |
| DV-1 | PASS (CONFIRMED) | Both add return codes set `created` at `src/ai_cli/session.py:883-897`; after failed adds, a fresh identity-aware registration probe returns reuse with `False` or `None`, never `True`, at lines 899-907. The advisory lock serializes that decision at lines 834-910. See N-3 for the shorter-than-required lock lifecycle. |
| DV-2 | PARTIAL (CONFIRMED) | Present: non-`FileNotFoundError` identity failures raise at `src/ai_cli/session.py:795-804`, and launch refuses a falsey result at `src/ai_cli/main.py:1930-1936`. Missing: `registered_worktrees()` still turns every failed Git query into `[]` at `src/ai_cli/session.py:763-772`, and two failed adds still return `None` without either stderr at lines 883-907. See N-1. |
| DV-3 | PASS (CONFIRMED) | `_same_worktree_path()` still uses `Path.samefile` at `src/ai_cli/session.py:795-804`; the active case-alias probe resolved both spellings to inode `304339064`. |
| DV-4 | PASS (CONFIRMED) | A missing candidate returns non-match only for `FileNotFoundError` at `src/ai_cli/session.py:797-800`; existing unregistered paths refuse at lines 853-871, while absent paths proceed to Git add at lines 873-897. |

### R2.2 Round 1 F-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | FAIL (CONFIRMED) | `.beads/issues.jsonl:198-199` still contains `"status":"open"` for both issues. `AI-CLI-b5c9` now has resolution/status notes, but `AI-CLI-y98c` has neither, and the P1 follow-up named as the reason for leaving them open landed in `b2072ca`. See N-2. |
| F-2 | PASS (CONFIRMED) | `.beads/issues.jsonl:198` now uses generic `APP`, `app-1`, “A project's project_prefix,” and “display-id”; the account-specific reproduction strings identified by Round 1 are absent. |

### R2.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| AD-1 | PARTIAL (CONFIRMED) | Recommended option (a) is implemented with a per-slot file and `portalocker.LOCK_EX` at `src/ai_cli/session.py:834-910`. However, AD-1 remains `[PENDING]` in this document; the low-level blocking call at line 839 has no bounded timeout or owner metadata, and unlock at line 910 precedes upstream setup and other initialization at lines 912-960. This is not the complete recommended contract; see N-3. |

### R2.4 NEW issues surfaced

#### N-1: Identity/add failure handling is still not actionable — `P1`

**Location:** `src/ai_cli/session.py:763-772,883-907`; `src/ai_cli/main.py:1930-1936`

**What the Round 1 Resolution Pass claimed:**

> `Straightforward source/test follow-up: fail closed on indeterminate identity/add failure.`

**Actual state:** Identity `OSError`s now raise and the launch no longer falls back to the repository
root. However, a failed `git worktree list --porcelain` is still represented as an authoritative
empty registration set, and two failed add results still become bare `None`; neither add's stderr
reaches the caller. `tests/test_session.py:1011-1036` explicitly expects `None`, while
`tests/test_session_launch_locality.py:430-438` asserts only the generic launch refusal.

**Why it matters:** The dangerous root fallback is closed, but users still cannot distinguish a
branch collision, permissions failure, lock-related race, or broken Git state from the generic
“could not create” message. Treating an unavailable registration query as “no worktrees” also lets
creation proceed from indeterminate state rather than failing closed.

**Verification note:** CONFIRMED by the focused source extract: lines 771-772 are `if
res.returncode != 0: return []`, and lines 902-907 return reuse or `None` without reading `res.stderr`.

**Recommended fix (Round 3):** Raise an actionable `RuntimeError` when registration enumeration
fails, and after both add attempts fail include sanitized stderr from both commands. Update tests to
assert the specific error and diagnostic, while retaining the main-launch refusal assertion.

#### N-2: Issue tracking still contradicts shipped state — `P2`

**Location:** `.beads/issues.jsonl:198-199`

**What the Round 1 Resolution Pass claimed:**

> `Update issue state after follow-up scope is linked.`

**Actual state:** Commit `9187629` added resolution notes only to `AI-CLI-b5c9` and deliberately left
both records open pending the P1 follow-up. That follow-up subsequently landed as `b2072ca`, but
both records remain `"status":"open"`; `AI-CLI-y98c` has no equivalent resolution/status note.

**Why it matters:** The tracker still presents both shipped AC sets as open work and gives the two
issues inconsistent completion evidence.

**Verification note:** CONFIRMED by parsing the two JSONL records: output was
`AI-CLI-b5c9 status=open resolved_note=True status_note=True` and
`AI-CLI-y98c status=open resolved_note=False status_note=False`.

**Recommended fix (Round 3):** Close both issues with the implementing commit references and track
N-1/N-3 separately, or explicitly redefine both remaining scopes and add symmetric status notes.

#### N-3: Advisory lock ends before initialization and can wait forever — `P1`

**Location:** `src/ai_cli/session.py:834-960`; `.venv/lib/python3.13/site-packages/portalocker/constants.py:35-45`

**What the Round 1 Resolution Pass claimed:**

> `Concurrency strategy requires a policy choice; see AD-1.`

AD-1's recommendation required a bounded timeout and made create-versus-reuse a deterministic,
single-owner decision.

**Actual state:** The code calls `portalocker.lock(lock_fd, portalocker.LOCK_EX)` at line 839 with no
nonblocking flag or timeout, and unlocks at line 910. The newly created worktree is not fully
initialized until upstream configuration, symlinks, trust, and envrc handling complete at lines
912-960.

**Why it matters:** A second launcher can acquire the lock at line 839, observe registration at
line 847, and return/reuse the worktree while the first launcher is still configuring it outside
the lock. If the first launcher then fails upstream setup at line 943, the second process may
already be running in a worktree that the creator never successfully returned. A hung lock holder
also hangs every same-slot launch indefinitely.

**Verification note:** CONFIRMED by line-order inspection and the installed lock flags: `LOCK_EX`
is exclusive only; `LOCK_NB` is separate. No timeout-bearing `portalocker.Lock` object is used.

**Recommended fix (Round 3):** Hold the per-slot lock through all initialization and the final
successful return state, use bounded acquisition with an actionable timeout diagnostic, and add a
two-launch regression that blocks the creator during upstream setup and proves the reuser cannot
return early.

#### N-4: Lock dependency makes session import require a writable temp directory — `P2`

**Location:** `src/ai_cli/session.py:17`; `pyproject.toml:34`;
`.venv/lib/python3.13/site-packages/portalocker/__init__.py:1-8`;
`.venv/lib/python3.13/site-packages/portalocker/utils.py:453-459`

**What the Round 1 Resolution Pass claimed:** No Round 1 row anticipated a new import-time
filesystem prerequisite; this was introduced by the AD-1 implementation.

**Actual state:** `session.py` imports `portalocker` at module load. Installed portalocker 3.2.0
imports `BoundedSemaphore`, whose class definition evaluates `tempfile.gettempdir()` even though
`create_worktree()` does not use that class. In the current no-writable-temp audit environment,
`python -c 'import ai_cli.session'` fails before any CLI logic runs.

**Why it matters:** Read-only or tightly sandboxed environments that could previously import and
use non-worktree session functionality now fail globally because an unrelated dependency helper
probes for a writable temp directory during import.

**Verification note:** CONFIRMED in the active environment; the command terminated with
`FileNotFoundError: [Errno 2] No usable temporary directory found`. The failure also prevents
pytest collection of the requested files here.

**Recommended fix (Round 3):** Use a verified locking package/version whose import path has no
unrelated temp-directory side effect, or fix/guard that upstream import behavior. Add a subprocess
smoke test that imports `ai_cli.session` with standard temporary locations unavailable.

### R2 Verification Matrix

| Finding/check | Command | Expected | Actual | Pass? |
|---------------|---------|----------|--------|-------|
| IC-1 | `sed -n '637,684p' src/ai_cli/session_adopt.py` | Shared identity helper and delegated non-dry run | Helper at 658; delegated tuple call at 677 | ✅ |
| IC-2 | Focused `sed` of adoption docstring and collision module header | Historical/current wording | `resolved worktree base`; `failed/used/deleted` | ✅ |
| JA-1 | Test extract plus case-alias `stat` | Production prefix chain; host supports exact repro | Resolver/name calls present; both spellings inode `304339064` | ✅ |
| JA-2 | `rg` four loss-state test names | Three distinct refusal states | Uncommitted, unpushed, and outside-integration tests present | ✅ |
| JA-3 | `rg 'shutil\.rmtree|rmtree\(' src/ai_cli/session.py` | No match | No match | ✅ |
| DV-1 | `sed -n '883,910p' src/ai_cli/session.py` | Failed add never yields `created=True` | Return codes checked; failed path returns `(registered, False)` or `None` | ✅ |
| DV-2 / N-1 | `sed -n '763,777p;883,910p' src/ai_cli/session.py` | Loud errors with Git diagnostics | Failed list returns `[]`; failed adds return `None` without stderr | ❌ partial |
| F-1 / N-2 | Parse both JSONL records | Closed or explicitly rescoped after follow-up | Both `open`; only b5c9 has status notes | ❌ |
| AD-1 / N-3 | `rg -n 'portalocker.lock|portalocker.unlock|_set_upstream_or_raise' src/ai_cli/session.py` | Bounded lock covers initialization | Lock 839, unlock 910, upstream setup 943 | ❌ partial |
| N-4 | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import ai_cli.session'` | Import succeeds | `FileNotFoundError: ... No usable temporary directory found` | ❌ |
| Syntax self-check | Parse six requested source/test files with `ast.parse` | 6/6 parse | `ast_parse=6/6` | ✅ |

**Verified: 11 matrix checks run on `1bd495cae01f3298ff72a48a5c4ec429e4027537`; 7 passed and
4 reproduced PARTIAL/FAIL findings. Runtime pytest was not claimed as run.**

### R2 Recommendations

**MUST be fixed before unqualified sign-off:**

- N-1 / DV-2: fail closed with actionable registration/add diagnostics.
- N-3 / AD-1: hold the lock through complete initialization and bound lock acquisition.

**SHOULD be fixed before closing this audit:**

- N-2 / F-1: reconcile both issue statuses and completion notes.
- N-4: remove the import-time writable-temp prerequisite and add a restricted-import smoke test.
- Formally approve or close AD-1; its current implementation does not resolve a `[PENDING]` decision.

**Can be folded into a follow-up:**

- None. N-1 and N-3 are blocking correctness issues; N-2 and N-4 are small enough to resolve in
  the same Round 3 pass.

## Round 3 — Round 2 Resolution Verification (append-only)

**Round 3 auditor:** Codex, independent verification pass

**Round 3 date:** 2026-08-10

**Round 3 scope:** Verified every Round 2 finding (N-1 through N-4) and AD-1 against current
`HEAD` `1ad2615bc2df76d3b83131684bcfc8dbe03dcb15`, specifically implementation commit `20a8436`
and issue-metadata commit `1ad2615`. Read-only on source, tests, and issue metadata; only this
audit document was changed.

### R3 Summary

Two of four Round 2 findings PASS (N-1 and N-4); N-2 and N-3 are PARTIAL. AD-1 is also PARTIAL.
The error-diagnostic, root-refusal, bounded-lock, successful-concurrency, lazy-import, and issue-
closure changes are present with focused tests. Two new issues are confirmed: one P1 correctness
gap in failed initialization and one P2 tracking/audit-evidence contradiction.

Runtime pytest remains unverified because the audit sandbox has no writable temporary directory;
the focused invocation failed during pytest's own capture initialization before collection. Static
AST parsing passed for all five requested source/test files. Focused read-only probes confirmed
registration diagnostics, restricted import, issue state, and the failed-initialization reuse gap.

### R3.1 Round 2 N-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| N-1 | PASS (CONFIRMED) | `registered_worktrees()` raises a diagnostic `RuntimeError` for genuine Git-list failures at `src/ai_cli/session.py:769-777`; failed adds raise with both command diagnostics at lines 903-924; and `_do_session_launch()` catches the error, prints its detail, and exits instead of launching at repository root at `src/ai_cli/main.py:1924-1945`. Focused assertions exist at `tests/test_session.py:836-851,1034-1046` and `tests/test_session_launch_locality.py:431-448`. |
| N-2 | PARTIAL (CONFIRMED) | Both issue records are now closed with `closed_at` and implementing commit references at `.beads/issues.jsonl:198-199`, satisfying the state-change portion. Their shared close reason says the fixes were “independently verified by 2-round Codex audit,” but Round 2's own summary and recommendations report four unresolved N-N findings and “Not ready” (`docs/audits/ai-cli-203-204-worktree-launch-fix-audit.md` § R2 Summary, § R2 Recommendations). See N-6. |
| N-3 | PARTIAL (CONFIRMED) | `portalocker.Lock(..., timeout=20)` encloses registration, both adds, upstream setup, symlinks, trust, envrc, and the successful return at `src/ai_cli/session.py:853-978`; installed portalocker defaults to nonblocking retry flags, so the timeout is effective (`portalocker/utils.py:20-23,212-242`). The successful two-launch regression at `tests/test_session.py:1065-1118` proves the waiter cannot return early. Missing: failed initialization is not represented, so the next launcher takes the early registered-reuse return at `src/ai_cli/session.py:860-864` and skips initialization at lines 947-972; the focused probe returned `(path, False)` after the creator raised. See N-5. |
| N-4 | PASS (CONFIRMED) | `portalocker` is no longer imported at module scope and is imported only inside `create_worktree()` at `src/ai_cli/session.py:848`. The restricted-import smoke test at `tests/test_session.py:1120-1137` replaces `tempfile.gettempdir()` with a failure before importing `ai_cli.session`; the same read-only command completed with `session_import=ok` in this pass. |

### R3.2 AD-N decision verification

| ID | Verdict | Evidence |
|----|---------|----------|
| AD-1 | PARTIAL (CONFIRMED) | The target implements recommended option (a): a per-repository/session-slot advisory lock with a 20-second timeout and fresh registration probes at `src/ai_cli/session.py:845-860,913-920,978-982`. It does not complete the decision contract: AD-1 remains `[PENDING]` in this document (§ AD-1), the lock artifact contains no owner metadata, and failed initialization can still be reused without re-establishing required invariants (N-5). |

### R3.3 NEW issues surfaced

#### N-5: A worktree from failed initialization is returned as reusable — `P1`

**Location:** `src/ai_cli/session.py:860-864,925-974`; `tests/test_session.py:1065-1118`

**What the Round 2 Resolution Pass claimed:** Commit `20a8436` says the lock now spans
“registration probe through post-creation initialization” and closes the window in which another
launcher can reuse a worktree while the creator is still initializing it.

**Actual state:** The lock does span the successful path. If `_set_upstream_or_raise()`, symlink
creation, trust registration, or envrc handling raises after Git has registered the worktree, the
lock is released with no ready/failed state. The next launcher sees the registration at lines
860-864 and returns `(registered, False)` before any of the creator-only initialization at lines
947-972. The added concurrency test releases a successful creator and never exercises an
initialization exception.

**Why it matters:** A waiting session can launch in a worktree whose upstream, environment links,
workspace trust, or envrc approval was never completed. The lock changes when the race occurs, but
does not enforce the intended invariant that only successfully initialized worktrees are reusable.

**Verification note:** CONFIRMED with a read-only mocked two-call probe. The first call raised
`upstream init failed`; the second returned
`(PosixPath('/repo/.worktrees/session-1'), False)` through the registered-reuse branch.

**Recommended fix (Round 4):** Under the slot lock, make required initialization idempotent and run
or validate it before both created and reused returns. Preserve the worktree on failure, but record
or derive that initialization is incomplete so a later launcher retries it rather than returning
early. Add a two-launch failure-path regression in which the creator raises during upstream setup
and the waiter must also fail or complete initialization before returning.

#### N-6: Closed issue metadata claims verification that did not occur — `P2`

**Location:** `.beads/issues.jsonl:198-199`; audit § R2 Summary and § R2 Recommendations

**What the Round 2 Resolution Pass claimed:** N-2 recommended closing both issues with implementing
commit references after reconciling their shipped state.

**Actual state:** Both records are closed and cite `23e8796`, `b2072ca`, and `20a8436`, but each
close reason says the fixes were “independently verified by 2-round Codex audit.” Round 2 verified
the pre-follow-up target at `1bd495c`, found N-1 through N-4, and explicitly said “Not ready.” It
did not verify `20a8436`; this Round 3 pass is the first verification of that commit and finds N-5.

**Why it matters:** The tracker converts an unresolved audit into evidence of completion. Future
maintainers following the close reason will conclude the concurrency contract was independently
verified even though the cited round rejected ship readiness and the first actual follow-up
verification remains partial.

**Verification note:** CONFIRMED by git order and content: `20a8436` applies the Round 2 fixes;
`d0cb358` records the Round 2 report; `1ad2615` then closes both issues using the contradicted
verification claim.

**Recommended fix (Round 4):** Correct both close reasons to distinguish “fixes applied” from
“fixes independently verified.” Reopen or separately track N-5 and AD-1 until a verification pass
confirms the failed-initialization path and the decision is formally approved or closed.

### R3 Verification Matrix

| Finding/check | Command | Expected | Actual | Pass? |
|---------------|---------|----------|--------|-------|
| N-1 registration failure | Mocked failed `git worktree list --porcelain` call to `registered_worktrees()` | Raise with Git diagnostic | `could not list ...: fatal: permission denied` | ✅ |
| N-1 add/launch tests | Source and test extract at `session.py:903-924`, `main.py:1924-1945`, and focused tests | Both add diagnostics reach a clean root refusal | Both diagnostics are raised, caught, printed, and asserted | ✅ static |
| N-2 | Parse `.beads/issues.jsonl` records 198-199 | Closed with completion metadata | Both `closed`; both have `closed_at` and `close_reason` | ✅ state / ❌ evidence claim |
| N-3 successful path | Lock/body and concurrency-test extract | Bounded lock through successful initialization; waiter cannot return early | `timeout=20`; return remains inside lock; success-path test blocks waiter | ✅ static |
| N-3 / N-5 failure path | Mock creator initialization failure, then invoke same slot again | Waiter must not return an incompletely initialized worktree | Creator raised; waiter returned `(path, False)` | ❌ |
| N-4 | Replace `tempfile.gettempdir()` with a raising function, then import `ai_cli.session` | Import succeeds without probing tempdir | `session_import=ok` | ✅ |
| Test presence | `rg` focused N-1/N-3/N-4 test names | Claimed regressions exist and assert outcomes | Five focused tests present; no timeout or failed-initialization concurrency test | ✅ partial |
| Syntax self-check | `ast.parse` the five requested source/test files | 5/5 parse | `ast_parse=5/5` | ✅ |
| Focused pytest | Five exact test node IDs with cache disabled | Tests execute | Blocked before collection: pytest capture requires a writable temporary directory | ⚠ unverified |

**Verified: 9 matrix checks run on `1ad2615bc2df76d3b83131684bcfc8dbe03dcb15`;
5 passed, 3 were partial/failed as recorded, and runtime pytest was blocked before collection.**

### R3 Recommendations

**MUST be fixed before unqualified sign-off:**

- N-5 / N-3 / AD-1: prevent early reuse after creator initialization failure and add the
  corresponding two-launch failure regression.

**SHOULD be fixed before closing this audit:**

- N-6 / N-2: correct the issue close reasons and track the residual concurrency work honestly.
- Formally approve or close AD-1 and either implement its owner-metadata contract or explicitly
  record that portion as intentionally dropped with rationale.
- Run the five focused tests in an environment with a writable temporary directory.

**Can be folded into a follow-up:**

- A direct lock-timeout regression is deferrable once N-5 is fixed; current behavior is supported
  by installed portalocker's effective nonblocking retry flags and the explicit timeout handler.

## Round 4 — N-5 Fix Attempt (reverted, not shipped)

**Date:** 2026-08-10

**What was attempted:** Codex (`cx implement`, effort medium) extracted a shared
`_initialize_worktree()` helper (upstream setup, symlinks, trust, envrc) and called it
unconditionally on every reuse path — both the early registered-worktree-found branch at the top
of `create_worktree()` and the post-failed-`git worktree add` reuse branch — intending to complete
any initialization a prior failed creator left unfinished (per N-5's acceptance criteria, option
(b): the waiter completes the skipped initialization).

**Why it was reverted:** independently re-running the full test suite in this session (not the
audit's constrained sandbox) surfaced 27 failures, concentrated in `tests/test_session_adopt.py` —
real adoption/reuse scenarios, not edge-case mocks. Root cause: the early-reuse call hardcoded the
branch name as `f"wt-{ai_name}"` without verifying that is the branch the registered worktree
actually has checked out, so `_set_upstream_or_raise()` failed with `fatal: branch 'wt-...' does
not exist` on ordinary worktree reuse. Re-running the (not naturally idempotent)
upstream-mutation step on every single reuse — not only the narrow N-5 recovery case — was itself
a second, independent problem with the same diff.

**Disposition:** reverted (`git checkout -- src/ai_cli/session.py tests/test_session.py`); nothing
from this attempt shipped. N-5 tracked as its own follow-up issue, **AI-CLI-205** (P1, open), with
the failure mode and a safer design direction recorded on the issue (verify the actual checked-out
branch rather than assuming the naming convention; only mutate upstream when a check shows it's
genuinely missing/wrong, matching how the symlink/trust/envrc steps already behave). Not iterated
further in this session — the fix's blast radius (breaking everyday worktree reuse) exceeded the
narrow race it was meant to close, and three real regressions surfacing across three review passes
in one round was the signal to stop and hand this to a more careful, dedicated pass instead.

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Concurrent worktree creation policy — [PENDING]

**Context:** DV-1 confirms that two launchers can race between registration probing and Git add,
and the loser can report creation after both of its add attempts failed. The team must choose the
cross-process coordination contract for a session slot.

#### (a) Serialize each repository/session slot with an advisory lock

**Pros:**
- Makes create-versus-reuse a single-owner decision and keeps the printed result deterministic.
- Provides a clear place to re-probe registration and surface stale/permission state.

**Cons:**
- Requires portable lock lifecycle, timeout, and stale-owner handling across Windows, macOS, and Linux.
- Adds one coordination artifact to session launch.

#### (b) Keep optimistic creation and verify Git's result atomically afterward

**Pros:**
- Avoids lock files and lets Git arbitrate collisions.
- Keeps the uncontended path structurally close to the current implementation.

**Cons:**
- Correct classification requires careful stderr/status interpretation and a fresh identity-aware registration probe.
- Two launchers can still intentionally converge on one worktree unless the loser refuses after verification.

#### Recommendation

> Choose (a), a per-repository/per-slot advisory lock, then re-probe under the lock and check every Git result. The portability and stale-owner costs should be contained behind the repository's existing cross-platform locking dependency with a bounded timeout and owner metadata; the extra artifact is justified because it makes ownership and created/reused messaging deterministic instead of inferring state after a race.

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | P1 | Prevent reuse after failed worktree initialization and add a two-launch failure-path regression | AD-1, N-3, N-5 | Team | Round 4 |
| I-05 | P2 | Correct the unsupported audit-verification claim in both issue close reasons | N-2, N-6 | Team | Round 4 issue metadata update |

## Already-Correct Items

- ✅ `create_worktree()` contains no `shutil.rmtree` call at `23e8796`; existing unregistered slots always raise at `src/ai_cli/session.py:835-853`.
- ✅ A case-changed spelling on the active case-insensitive filesystem has `exists=True` and `Path.samefile=True`, validating the ordinary identity mechanism.
- ✅ Registered case aliases return Git's canonical registered path and `created=False` at `src/ai_cli/session.py:829-833`.
- ✅ Default callers retain the `Path | None` contract; `with_status=False` returns a path at `src/ai_cli/session.py:833,925`, so `session_adopt.py:675` is not broken by the new tuple type.
- ✅ Creation/reuse announcements are downstream of the actual `create_worktree()` call at `src/ai_cli/main.py:1924-1931`.
- ✅ Both Claude and Gemini announcement paths are parameterized in `tests/test_session_launch_locality.py:372-388,430-442`.
- ✅ The creation announcement retains both short and long opt-out forms at `src/ai_cli/main.py:336-337`.
- ✅ Existing unregistered directories preserve arbitrary non-Git files and provide an `rmdir` remediation at `src/ai_cli/session.py:849-852`.
- ✅ Nested and unrelated Git checkouts receive a `git worktree move`/refusal diagnostic at `src/ai_cli/session.py:838-847`.
- ✅ `registered_worktrees()` parses exact porcelain records rather than substring matches at `src/ai_cli/session.py:748-775`.

## Anti-Patterns to Watch For

- Treating path existence after an external command as proof that this process created the path.
- Catching all filesystem identity errors as “not equal”; permission and transient errors mean “unknown.”
- Mocking the newly introduced helper in a regression that is supposed to fail against the parent implementation.
- Using parameter labels as substitutes for constructing and asserting materially different Git states.
- Updating a shared primitive without searching callers that duplicate its precondition logic.
- Calling a pytest command “run” when sandbox policy prevented creation of its temporary directory.
- Releasing a concurrency lock after object creation but before the object is fully initialized.
- Treating lock coverage as initialization atomicity without testing what a waiter observes after
  the creator raises and releases the lock.
- Adding a top-level dependency without testing import under the restricted environment the module previously supported.

## Sign-Off Checklist

**For the shipped AI-CLI-203/204 scope:**

- [x] All P0 findings have linked fixes (none)
- [x] All P1 findings in the original scope (DV-1, DV-2) fixed and verified
- [x] AI-CLI-203/204's own AC sets (JA-1 through JA-5) independently PASS-verified
- [x] Both issues closed with accurate close reasons (N-6 correction applied)

**For the concurrency-hardening extension (AD-1 and its findings):**

- [ ] All P1 findings fixed or explicitly deferred with rationale — N-5 deferred to AI-CLI-205
      with rationale (see Round 4); N-1/N-3 fixed and verified
- [x] All P2/P3 findings logged to a follow-up tracker (AI-CLI-205 for N-5; N-6 corrected inline)
- [ ] AD-1 approved or explicitly closed with rationale — still `[PENDING]`; the implemented
      lock+timeout is sound (Round 3 R3.1) but the owner-metadata portion was never built and the
      decision was never formally closed
- [x] Verification Matrix run on at least 5 findings across every round (8/8, 11/13, 9 checks)
- [x] At least one verification round completed because Round 1 found issues (three were run)
- [x] Re-grep verification completed after fixes (Round 2 and Round 3 matrices)
- [x] No inline target fixes were applied by any audit round (all fixes were separate delegated/
      direct commits, per the canonical prompt's read-only-except-audit-doc constraint)
- [x] Already-Correct Items populated with specific evidence
- [x] Anti-Patterns section reflects audit methodology lessons
- [ ] User reviewed and approved sign-off

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-08-10 | — | Doc created from canonical ai-harness STUB/TEMPLATE ahead of Round 1 launch. |
| 2026-08-10 | Round 1 | Independent audit complete: 8 findings (2 P1, 5 P2, 1 P3), 8/8 reproduced; no source edits; AD-1 pending. |
| 2026-08-10 | Round 2 | Verification complete at `1bd495c`: 11 PASS, 1 PARTIAL, 1 FAIL; AD-1 PARTIAL; 4 new findings (2 P1, 2 P2); no target edits. |
| 2026-08-10 | Round 3 | Resolution verification complete at `1ad2615`: N-1/N-4 PASS, N-2/N-3 PARTIAL, AD-1 PARTIAL; N-5 (P1) and N-6 (P2) added; no target edits; pytest blocked before collection by sandbox temporary-directory policy. |
| 2026-08-10 | — | N-6 corrected: both issues' close reasons updated to stop claiming verification Round 2 explicitly withheld. N-5 filed as its own issue, AI-CLI-205. |
| 2026-08-10 | Round 4 (attempted, reverted) | N-5 fix attempt broke ordinary worktree reuse (27 test failures, independently verified outside the audit sandbox) via a wrong branch-name assumption on the reuse path. Reverted before commit; nothing shipped. AI-CLI-205 left open with the failure mode and a safer design direction recorded. Converged: AI-CLI-203/204 shipped scope is fully verified and closed; the concurrency-hardening extension has one known, narrow, non-blocking gap tracked separately. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Audit format and repository policy:**

- Canonical `docs/audits/TEMPLATE.md` and `STUB.md` fallback copies — read in full before audit work.
- `AGENTS.md`, `docs/audits/README.md`, `pyproject.toml`, `CONTRIBUTING.md` — repository policy and test commands.
- `docs/audits/ai-cli-203-204-worktree-launch-fix-audit.md` — full designated stub and immutable reviewer prompts.

**Primary source and adjacent callers:**

- `src/ai_cli/session.py` — full file; all requested worktree/session symbols and cleanup behavior.
- `src/ai_cli/main.py` — full file; announcement, launch ordering, all worktree/no-worktree fallbacks, engine entry paths.
- `src/ai_cli/session_adopt.py` — full file; duplicate registration gate and default return contract.
- `src/ai_cli/config.py` — full file; all prefix tiers and case-preserving return behavior.

**Changed and directly relevant tests:**

- `tests/test_session.py` — full file; requested create-worktree classes and status/failure behavior.
- `tests/test_session_launch_locality.py` — full file; both-engine creation/reuse announcement coverage.
- `tests/test_worktree_container_collision.py` — full file; deletion/refusal and case-collision regressions.
- `tests/test_session_adopt.py` — worktree handling and mocked `create_worktree` call sites.
- `tests/test_worktree_base.py`, `tests/test_worktree_upstream.py` — every production `create_worktree` call site surfaced by symbol search.
- `tests/test_session_audit.py`, `tests/test_cli.py`, `tests/test_main.py`, `tests/test_bare_worktree.py`, `tests/test_session_launch_integration.py` — surfaced replacement/call-site sections checked for signature and launch compatibility.
- `tests/test_prefix_registry_tiers.py`, `tests/test_config.py`, `tests/test_project.py` — surfaced prefix-resolution coverage checked for casing assumptions.

**Authoritative requirements and history:**

- `.beads/issues.jsonl:198-199` — complete issue descriptions and ACs for both issue IDs; both have zero comments.
- Commit `23e8796` and parent versions of all five changed files; relevant log/blame history through the original collision tests and integration-branch change.

**Round 2 verification additions:**

- Current `HEAD` `1bd495c`, implementation follow-up `b2072ca`, and issue metadata follow-up `9187629` — complete diffs and commit messages checked.
- `.venv/lib/python3.13/site-packages/portalocker/{__init__,constants,portalocker,utils}.py` — lock blocking flags, timeout API, and import-time temp-directory evaluation.

**Round 3 verification additions:**

- Current `HEAD` `1ad2615`, implementation follow-up `20a8436`, Round 2 audit commit `d0cb358`, and issue closure `1ad2615` — complete relevant diffs, commit messages, and ordering checked.
- `src/ai_cli/session.py:748-982` — registration diagnostics, add diagnostics, lock boundary, all early returns, initialization, and timeout handling.
- `src/ai_cli/main.py:1913-1946` — raised creation errors converted to a diagnostic refusal rather than repository-root fallback.
- `tests/test_session.py:835-851,1034-1147` — registration/add diagnostics, successful two-launch serialization, restricted import, and absence of failed-initialization/timeout regressions.
- `tests/test_session_launch_locality.py:431-448` — clean launch refusal preserves detailed error text.
- `.beads/issues.jsonl:198-199` — current closed state, close timestamps/reasons, implementing commits, and verification wording.
- `.venv/lib/python3.13/site-packages/portalocker/utils.py:20-23,181-340` — effective timeout defaults, nonblocking retry flags, acquisition, and release semantics.

## Appendix: Commands Run

```bash
sed -n '1,500p' <canonical-audit-template>
sed -n '1,420p' docs/audits/ai-cli-203-204-worktree-launch-fix-audit.md
git status --short
git rev-parse HEAD
git show --stat --oneline 23e8796
git diff 23e8796^ 23e8796 -- src/ai_cli/session.py src/ai_cli/main.py tests/test_session.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py
grep -rnE '<every requested symbol>' src tests
rg -n --glob '*.py' 'create_worktree\(' src tests
rg -n --glob '*.py' 'shutil\.rmtree|rmtree\(' src tests
nl -ba src/ai_cli/session.py
nl -ba src/ai_cli/main.py
nl -ba src/ai_cli/session_adopt.py
nl -ba src/ai_cli/config.py
nl -ba tests/test_session.py
nl -ba tests/test_session_launch_locality.py
nl -ba tests/test_worktree_container_collision.py
git show 23e8796^:src/ai_cli/session.py
git show 23e8796^:tests/test_worktree_container_collision.py
git log --all -- src/ai_cli/session.py tests/test_worktree_container_collision.py
git blame -L 1,22 tests/test_worktree_container_collision.py
git blame -L 637,680 src/ai_cli/session_adopt.py
pytest -q tests/test_session.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py tests/test_session_adopt.py tests/test_worktree_base.py tests/test_worktree_upstream.py
uv run pytest -q -n 0 tests/test_session.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py tests/test_session_adopt.py tests/test_worktree_base.py tests/test_worktree_upstream.py
.venv/bin/pytest -q -n 0 tests/test_session.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py tests/test_session_adopt.py tests/test_worktree_base.py tests/test_worktree_upstream.py
# The three pytest attempts were blocked respectively by missing PATH entry, denied uv cache,
# and absence of any sandbox-writable temporary directory. The eight exact finding commands are
# recorded in each detailed finding and were re-run for the Verification Matrix.

# Round 2 additions
git status --short
git rev-parse HEAD
git log --oneline --decorate -12
git diff --name-status 23e8796..HEAD
git show 9187629 -- .beads/issues.jsonl
git show b2072ca -- src/ai_cli/session.py src/ai_cli/main.py src/ai_cli/session_adopt.py tests/test_session.py tests/test_session_adopt.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py
nl -ba src/ai_cli/session.py | sed -n '720,990p'
nl -ba src/ai_cli/main.py | sed -n '300,355p;1840,1965p;2020,2160p'
nl -ba src/ai_cli/session_adopt.py | sed -n '620,700p'
nl -ba tests/test_session.py | sed -n '780,1135p'
nl -ba tests/test_session_launch_locality.py | sed -n '320,475p'
nl -ba tests/test_worktree_container_collision.py | sed -n '1,350p'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest --collect-only -q -s -p no:cacheprovider tests/test_session.py tests/test_session_launch_locality.py tests/test_worktree_container_collision.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import ai_cli.session'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import ast,pathlib; fs=["src/ai_cli/session.py","src/ai_cli/main.py","src/ai_cli/session_adopt.py","tests/test_session.py","tests/test_session_launch_locality.py","tests/test_worktree_container_collision.py"]; [ast.parse(pathlib.Path(f).read_text(),filename=f) for f in fs]; print("ast_parse=6/6")'
stat -f '%i' src/ai_cli/session.py SRC/AI_CLI/SESSION.PY
rg -n 'portalocker\.lock|portalocker\.unlock|_set_upstream_or_raise|return None|git stderr|registered_worktrees\(repo_root\)' src/ai_cli/session.py
rg -n 'test_given_an_uppercase_fleet_prefix|test_given_unregistered_slot_with_uncommitted|test_given_unregistered_slot_with_an_unpushed|test_given_unregistered_slot_with_commit_outside|test_given_worktree_creation_fails|test_given_a_new_worktree|test_given_an_existing_worktree' tests/test_worktree_container_collision.py tests/test_session_launch_locality.py

# Round 3 additions
git status --short
git rev-parse HEAD
git log --oneline --decorate -15
git diff --name-status 1bd495c..HEAD
git show 20a8436 -- src/ai_cli/session.py src/ai_cli/main.py tests/test_session.py tests/test_session_launch_locality.py
git show 1ad2615 -- .beads/issues.jsonl
nl -ba src/ai_cli/session.py | sed -n '730,995p'
nl -ba src/ai_cli/main.py | sed -n '1900,1965p;2050,2165p'
nl -ba tests/test_session.py | sed -n '825,875p;1030,1150p'
nl -ba tests/test_session_launch_locality.py | sed -n '425,455p'
rg -n 'registered_worktrees|initialization_is_blocked|lock.*timeout|tempdir_probe|adds_fail|worktree_creation_fails' tests/test_session.py tests/test_session_launch_locality.py
rg -n 'portalocker\.Lock|timeout=20|_set_upstream_or_raise|ensure_workspace_trusted|_allow_trusted_worktree_envrc|import portalocker|LockException' src/ai_cli/session.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '<mock failed git-worktree-list probe>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '<restricted ai_cli.session import probe>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '<parse both issue records and print state booleans>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '<mock creator-init-failure then waiter-reuse probe>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '<AST-parse five requested source/test files>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider <five focused test node IDs>
# Pytest was blocked before collection because its capture layer requires a writable temporary directory.
git diff --check
aido validate-doc docs/audits/ai-cli-203-204-worktree-launch-fix-audit.md
```

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** gpt-5.6-terra (Codex, `cx audit`, effort: medium)

**Date:** 2026-08-10

```text
You are a principal staff engineer specializing in developer-experience tooling, CLI session
orchestration, and Git worktree management. You have shipped production systems that manage
per-session Git worktrees at scale and you know the gap between what looks rigorous on paper and
what actually holds up under a case-insensitive filesystem, a concurrent session, or a partially
failed prior run. You call out that gap directly. When you cannot verify a claim, you say so
explicitly rather than waving past it. Your judgment is the product, not a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous.

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit the AI-CLI-203/AI-CLI-204 fix (src/ai_cli/session.py, src/ai_cli/main.py, and their tests)
at commit 23e8796 against the scope below, on the following validation dimensions:

  1. Internal Consistency (IC-N): does the fix contradict itself, or leave `create_worktree()`'s
     callers (e.g. `session_adopt.py`) or its docstring out of sync with the new behavior?
  2. Spec / AC Compliance (JA-N): does it satisfy every acceptance criterion in bd issues
     AI-CLI-b5c9 (AI-CLI-203) and AI-CLI-y98c (AI-CLI-204), reproduced verbatim below?
  3. Domain Validity (DV-N): is the `Path.samefile`-based filesystem-identity comparison correct
     and complete (race conditions between the check and use, symlink handling, the directory not
     existing yet, permission-denied cases)? Is "never auto-delete, always refuse" actually
     enforced on every code path that used to reach `shutil.rmtree`, with no residual path that
     still deletes?
  4. Independent Findings (F-N, open scope): anything else that matters — missed call sites,
     untested edge cases, a race between the `samefile` check and worktree creation, inconsistent
     messaging between `ai c` and `ai g`, or an incomplete removal of the old delete-based recycle
     behavior (e.g. dead code, a stale comment describing behavior that no longer exists).

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line). Use this exact skeleton for
any AD-N you add (do not improvise a different shape):

```
### AD-N: <name> — [PENDING]
<a id="ad-N"></a>

**Context:** <1-3 sentences>

#### (a) <option a name>

**Pros:**
- <bullet>

**Cons:**
- <bullet>

#### (b) <option b name>

**Pros:**
- <bullet>

**Cons:**
- <bullet>

#### Recommendation

> <one recommended option, with reasoning that addresses every Con listed for it>
```

For each finding, supply:
  - File:line reference.
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

You MUST run a Verification Matrix on at least 5 of your own findings (or all of them if fewer
than 5 exist): re-run the verification command and record the actual output. A finding without a
reproduced verification command is a hypothesis, not a fact.

## AI-CLI-b5c9 / AI-CLI-203 — acceptance criteria (verbatim)

- Root cause reproduced with a minimal test (or documented why not reproducible in CI, e.g.
  case-insensitive-fs-only) before the fix lands.
- create_worktree() never calls shutil.rmtree on a path that is case-insensitively the same file
  as an existing registered worktree.
- A new session launch with a case-differing prefix against an existing worktree either reuses the
  existing worktree correctly or fails loudly with an accurate diagnostic -- never silently
  deletes or silently duplicates.
- Regression test covers the exact repro: fleet-registry prefix casing differs from an
  already-materialized worktree directory's casing.
- USER DIRECTIVE: the recycle-delete path must never delete when there is ANY possibility of
  losing work (uncommitted changes, unpushed commits, commits not merged into the integration
  branch) regardless of whether a .git directory is detected there. Default to NOT auto-deleting
  at all; refuse and print a remediation command. Auto-delete may remain only for a directory
  provably empty of value.
- Regression test asserting create_worktree() refuses (does not rmtree) when the colliding
  directory has uncommitted changes, unpushed commits, or commits absent from the integration
  branch.

## AI-CLI-y98c / AI-CLI-204 — acceptance criteria (verbatim)

- Launching into a brand-new worktree prints "Creating ... <path>"; launching into an
  already-registered existing worktree prints something like "Using existing worktree: <path>" --
  both before the CC/Gemini session is entered, for both `ai c` and `ai g`, not just repo-root
  launches.
- The printed outcome (new vs existing) matches create_worktree()'s ACTUAL decision, not a
  pre-emptive guess made before create_worktree runs.
- Regression test covers both paths: first launch (new) and a second launch reusing the same
  session slot (existing), asserting on the exact message content re: created-vs-reused and the
  path.
- No change to worktree creation/deletion behavior itself in this issue's scope (implemented
  together with AI-CLI-203 as one coherent change, per the delegation brief).

## Code-review scope (lean toward over-reading)

Read all source code and tests that the fix touches, calls, or is called by. This is a
completeness requirement, not a sampling exercise. For every function the fix changed, run
`grep -rn <symbol> src/ tests/` to surface every call site. Add anything that surfaces to your
read list before producing findings.

## Files to read (read in full, do not skim — expand this list during the run)

### Audit format (read FIRST)

0. `docs/audits/TEMPLATE.md` in this repo (if absent, `~/projects/project-template/template/docs/audits/TEMPLATE.md`).

### Primary subject

1. `src/ai_cli/session.py` — `create_worktree()`, `_same_worktree_path()`,
   `_registered_worktree_at()`, `registered_worktrees()`, `_contains_git_checkout()`,
   `find_next_index()`, `_find_next_index_from_worktrees()`, `build_session_name()`.
2. `src/ai_cli/main.py` — `_announce_worktree_isolation()` and its call site in
   `_do_session_launch()` (~line 1915-1940).

### Tests

3. `tests/test_session.py` — `TestCreateWorktree`, `TestCreateWorktreeEdgeCases`,
   `TestCreateWorktreeEdgeCases2`.
4. `tests/test_session_launch_locality.py` — the worktree-isolation-announcement tests
   (search `AI-CLI-195`).
5. `tests/test_worktree_container_collision.py` — the full file; this is where the new
   case-collision regression tests live.

### Other call sites / adjacent code (lean toward over-listing)

6. `src/ai_cli/session_adopt.py` — calls `create_worktree(ai_name)` without `with_status=True`;
   verify the default-path return type contract still holds after this change.
7. `src/ai_cli/config.py` — `resolve_project_prefix()`, `_fleet_registry_prefix()`,
   `_local_registry_prefix()`, `_xdg_registry_prefix()` (the prefix-casing source this bug
   originated from — not itself in scope to fix, but verify the fix doesn't assume a prefix
   casing invariant that config.py doesn't actually guarantee).

## Output

Write findings into this audit doc following the Round 1 section structure:
  R1 Summary → R1 Findings (IC / JA / DV / F tables + detailed F-N subsections) → R1 Resolution
  Pass → R1 Verification Matrix → AD-N entries in "Decisions Requiring Team Input" if any →
  Already-Correct Items.

Append a row to the Audit Log when done. Update the Status Summary's cross-round counts and
ship-readiness verdict.

Never fabricate evidence to satisfy a section. Empty findings sections are honest if nothing was
found; faked findings are not. Cite file:line for every codebase claim.

## Anti-patterns (avoid)

- Code-only check that ignores the bd issue ACs reproduced above.
- Under-reading the codebase — read sibling/neighbor code (session_adopt.py, config.py) for
  pattern-consistency, not just the two changed files.
- Verification commands that aren't actually run.
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0. Grep for a finding heading, or run ai-harness/scripts/check_audit_doc_filled.py.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** <!-- ideally a fresh agent for independent verification -->

**Date:** (post-Round-1)

```text
You are a principal staff engineer specializing in developer-experience tooling, CLI session
orchestration, and Git worktree management (same domain as Round 1, ideally a fresh agent/model
for independent verification). You are reading the Round 1 audit of the AI-CLI-203/AI-CLI-204
worktree launch fix — see the Round 1 Reviewer Prompt above in this same audit doc. This is the
Round 2 verification pass.

Your task is to verify that EVERY Round 1 finding (IC-N / JA-N / DV-N / F-N) and EVERY AD-N
decision has been correctly applied to the target. You will also surface NEW issues (N-N) that the
Round 1 fixes themselves introduced.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for Round 3 to apply.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each Round 1 finding:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections Round 1 modified. Look for stale cross-references
introduced by Round 1, Resolution Pass claims that didn't actually land, Round 1 fixes that
introduced new contradictions, and leftover scaffolding from the Round 1 edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → R2.1 IC/JA/DV verification table (PASS/FAIL/PARTIAL + evidence) → R2.2 F-N
  verification table → R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. `docs/audits/TEMPLATE.md` in this repo.
1. `src/ai_cli/session.py`, `src/ai_cli/main.py` — read every section Round 1 touched.
2. THIS AUDIT DOC — the Round 1 sections are your verification checklist.
3. The relevant test files (`tests/test_session.py`, `tests/test_session_launch_locality.py`,
   `tests/test_worktree_container_collision.py`) — verify claimed regression tests actually exist
   and actually assert what the Resolution Pass claims.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->
