---
title: "AI-CLI-203/AI-CLI-204 worktree launch fix — audit"
category: audit
tags: [audit, ai-cli-203, ai-cli-204, worktree, session-launch]
status: findings-pending-fix
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

**Status:** findings-pending-fix

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

**Latest round:** Round 1

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 0 | 0 | 0 |
| P1 | 2 | 0 | 0 |
| P2 | 5 | 0 | 0 |
| P3 | 1 | 0 | 0 |
| **Total** | **8** | **0** | **0** |

**Ship-readiness verdict:** Not ready for an unqualified sign-off. The destructive recycle path is
removed and the ordinary case-collision path is correct, but concurrent creation can report
`created=True` after both of this process's `git worktree add` attempts failed, while identity
probe/add failures can silently launch from the repository root. Both P1 findings need a follow-up
before this behavior should be called robust under concurrent or partially inaccessible state.

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
| I-01 | P1 | Make worktree creation concurrency-safe and report the true winner | DV-1, AD-1 | Team | Follow-up commit |
| I-02 | P1 | Fail closed on indeterminate identity and failed Git add | DV-2 | Team | Follow-up commit |
| I-03 | P2 | Align adoption with identity-aware reuse | IC-1 | Team | Follow-up commit |
| I-04 | P2 | Add exact fleet-chain and distinct refusal-state regressions | JA-1, JA-2 | Team | Follow-up commit |
| I-05 | P2 | Reconcile issue state and genericize public reproduction | F-1, F-2 | Team | Issue metadata update |
| I-06 | P3 | Refresh stale adjacent prose | IC-2 | Team | Follow-up commit |

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

## Sign-Off Checklist

- [x] All P0 findings have linked fixes (none)
- [ ] All P1 findings fixed or explicitly deferred with rationale
- [ ] All P2/P3 findings logged to a follow-up tracker
- [ ] AD-1 approved or explicitly closed with rationale
- [x] Verification Matrix run on at least 5 findings; 8/8 reproductions recorded
- [ ] At least one verification round completed because Round 1 found issues
- [ ] Re-grep verification completed after fixes
- [x] No inline target fixes were applied
- [x] Already-Correct Items populated with specific evidence
- [x] Anti-Patterns section reflects audit methodology lessons
- [ ] User reviewed and approved sign-off

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-08-10 | — | Doc created from canonical ai-harness STUB/TEMPLATE ahead of Round 1 launch. |
| 2026-08-10 | Round 1 | Independent audit complete: 8 findings (2 P1, 5 P2, 1 P3), 8/8 reproduced; no source edits; AD-1 pending. |

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
