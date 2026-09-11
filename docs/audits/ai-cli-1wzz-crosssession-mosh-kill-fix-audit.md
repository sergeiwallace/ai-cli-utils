---
title: AI-CLI-1wzz cross-session mosh/tmux kill fix — audit
category: audit
tags: [audit]
status: findings-pending-fix
date: 2026-09-10
source: "canonical-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---
<!-- Canonical Jinja source: STUB.md.jinja. This direct-copy stub is retained for consumers that have not migrated to rendering it. -->

# AI-CLI-1wzz cross-session mosh/tmux kill fix — audit

**Status:** findings-pending-fix

**Created:** 2026-09-10

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [Scope](#scope)
  - [Founding ask and alignment](#founding-ask-and-alignment)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
  - [Run Ledger](#run-ledger)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Findings](#r1-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
  - [R1 Receipt Payload Projection](#r1-receipt-payload-projection)
- [Round 2 — Verification Pass](#round-2--verification-pass-append-only)
  - [R2 Summary](#r2-summary)
  - [R2.1 Round 1 IC/JA/DV verification](#r21-round-1-icjadv-verification)
  - [R2.2 Round 1 F-N verification](#r22-round-1-f-n-verification)
  - [R2.3 AD-N decisions verification](#r23-ad-n-decisions-verification)
  - [R2.4 NEW issues surfaced](#r24-new-issues-surfaced)
  - [R2.5 Verification Matrix](#r25-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
  - [R2 Receipt Payload Projection](#r2-receipt-payload-projection)
- [Round 3 — Verification Pass](#round-3--verification-pass-append-only)
  - [R3 Summary](#r3-summary)
  - [R3.1 Open MUST-fix backlog verification](#r31-open-must-fix-backlog-verification)
  - [R3.3 AD-N decisions verification](#r33-ad-n-decisions-verification)
  - [R3.4 NEW issues surfaced](#r34-new-issues-surfaced)
  - [R3.5 Verification Matrix](#r35-verification-matrix)
  - [R3 Recommendations](#r3-recommendations)
- [Round 4 — Verification Pass](#round-4--verification-pass-append-only)
  - [R4 Summary](#r4-summary)
  - [R4.1 Open MUST-fix backlog verification](#r41-open-must-fix-backlog-verification)
  - [R4.2 Destructive call-site sweep](#r42-destructive-call-site-sweep)
  - [R4.3 AD-N decisions verification](#r43-ad-n-decisions-verification)
  - [R4.4 NEW issues surfaced](#r44-new-issues-surfaced)
  - [R4.5 Verification Matrix](#r45-verification-matrix)
  - [R4 Recommendations](#r4-recommendations)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
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

### Founding ask and alignment

`founding_ask_ref`: fidelity-tier-1 raw entry supplied with this dispatch.

| Authoritative raw entry | Audit coverage |
|---|---|
| `we need to /delegate /codex /auto-flow it as well to make sure its audited and robust.` | This delegated Codex round performs the independent audit. It does not itself execute the remaining autonomous pipeline stages. |

This audit targets the already-shipped fix for a recurring class in which one managed session's
lifecycle can destructively signal a sibling session's process. The target is merged commit
`744499b4` (PR #128).

**Alignment conflict:** the non-authoritative stage framing says no design/plan document exists
for this work. Ground truth contains `docs/plans/process-hygiene-plan.md`, the approved plan that
introduced this exact auto-kill behavior. It is not fix-specific, but it is load-bearing and now
contradicts the shipped fix (F-6). The direct audit is still useful and in scope, but it cannot be
represented as completion of the founding ask's `/auto-flow` portion. The fidelity requirement
also requires the private workflow command names above to be preserved verbatim, while the public
repository policy ordinarily forbids private workflow references; this audit preserves the exact
ask once and adds no account-specific identifiers.

**Auditor:** Codex `gpt-5.6-sol`, `audit` role (effort: high)

**Target commit:** `744499b4` (ai-cli-utils main)

**Target artifact:** `docs/bugs/cross-session-mosh-termination.md` at commit `744499b4`

**Write scope:** this audit document only. No source, test, configuration, plan, bug-record, or
external-state edits were made.

## Methodology

Read the canonical audit STUB and TEMPLATE before audit work. The repository-local
`docs/audits/STUB.md` and `docs/audits/TEMPLATE.md` named by the dispatch are absent from the
working tree and searched sibling worktree locations, so the canonical copies in the local
harness repository were read in full and used. The bug record, changed implementation, complete
named source files, complete process-hygiene test file, relevant neighboring tests and docs, the
target diff, prior related history, and every source signal call site were then inspected.

All code evidence is pinned to `744499b4`. Current `HEAD` is `c74146ec3ea4`; a path-scoped diff
proved every relevant source/doc/test member is byte-identical to the target commit. Read-only
Python probes exercised the production predicates and signal calls with controlled process and
tmux adapters. The real subprocess cron-survival probe passed. Focused pytest execution was also
attempted, but the sandbox supplies no writable temporary directory, so pytest failed before
collection; historical suite counts in the bug record remain **UNVERIFIED by this audit**.

<!-- /doc:region name="scope" -->

<!-- doc:region name="loop_receipt" kind="replaceable" -->
## Status Summary

**Loop state:** Direct Round 4 verification complete — **NOT promotion-ready**. N-1 and N-2 have
credible source repairs and genuine post-observation mutation tests, but both required tests remain
blocked before collection by the actual worker sandbox. The class invariant is still false: Round 4
found three additional ownership gaps (N-3 through N-5), including a raw name-only supervisor
teardown. This direct round does not advance the driver receipt; only the adjacent trusted receipt
may assert stabilization or promotion.

**Cross-round finding count:** 14 unique MAJOR findings (7 fixed; 7 open: JA-1, DV-1, and
N-1 through N-5), 0 CRITICAL, and 0 MINOR.

| Loop | Round | Target digest | New CRITICAL | New MAJOR | MAJOR justified | New MINOR | Open blocking | Scope | Terminal |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| audit | 1 | `sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c` | 0 | 9 | 0 | 0 | 9 | discovery | none — projection only |
| audit | 2 | `blob:c454026f066f10c7e3b3537c2acd9ab8bab44861` | 0 | 1 | 0 | 0 | 3 | verification | none — projection only |
| audit | 3 | `blob:1e21c3e7144cba2595225461bd314cd2706bb854ef5679c228f8f288ecd524a1` | 0 | 1 | 0 | 0 | 4 | direct verification | blocked — JA-1, DV-1, N-1, N-2 |
| audit | 4 | `blob:1e21c3e7144cba2595225461bd314cd2706bb854ef5679c228f8f288ecd524a1` | 0 | 3 | 0 | 0 | 7 | direct verification + authority sweep | blocked — JA-1, DV-1, N-1 through N-5 |

### Run Ledger

| Field | Value |
|---|---|
| ledger-version | 2 |
| Stage cursor | `direct R4 verification complete; resolution required` |
| Driver lane | `verification` |
| Capability plan | `cx-audit-medium + test execution requested` |
| Promotion | `blocked: JA-1, DV-1, and N-1 through N-5 open; trusted receipt pending` |
<!-- /doc:region name="loop_receipt" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** Codex audit

**Round 1 date:** 2026-09-10

**Round 1 scope:** Full implementation audit of commit `744499b4`, including the causal record,
the shipped diff, all lifecycle kill authority, sibling ownership patterns, regression validity,
related plans/designs, and relevant history.

### R1 Summary

Nine MAJOR findings are CONFIRMED. The narrow repair is real: `ai ps cron` no longer calls
`auto_clean_orphans()`, and a real subprocess classified at score 80 survives the cron path.
That does **not** establish the claimed class-wide invariant.

At least two implicit/session-launch-reachable mechanisms can still target processes or sessions
without conclusive ownership. Most directly, arbitrary session launch invokes a background-spare
sweep whose session-name regex rejects valid hyphenated project prefixes; the resulting false
"no live session" decision terminates the sibling helper. When quota watcher auto-start is opted
in, its scraper also kills a fixed tmux name before creation and again in `finally`, without a
managed marker or generation check. Additional explicit kill paths use only a tmux name or stale
PID. The related approved plan still directs implementers to restore score-based launch/cron
auto-kill. The shipped occurrence is fixed; the recurring class is not structurally eliminated.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | FAIL (MAJOR, CONFIRMED) | The bug record says one-command removal is "sufficient" (`docs/bugs/cross-session-mosh-termination.md:68-72`), while launch-time `cleanup_stale_sessions()` still reaches `process.terminate()` (`src/ai_cli/main.py:2830-2834`; `src/ai_cli/session.py:467-518`). |
| — | PASS (CONFIRMED) | The incident-specific causal chain and diff agree: commit `744499b4` removes only the cron call to `auto_clean_orphans()` and updates the session-script comment. |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| JA-1 | FAIL (MAJOR, CONFIRMED) | The whole-class requirement is not met: F-1 and F-2 preserve launch-reachable, unowned signal authority; F-3 through F-5 preserve additional unowned explicit authority. |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| DV-1 | FAIL (MAJOR, CONFIRMED) | The real-process regression at `tests/test_process_hygiene.py:840-865` genuinely proves only `cmd_ps(["cron"])`; it never invokes launch cleanup, quota polling, sandbox recreation, or another signal owner, so it passes while F-1/F-2 remain live. |
| — | PASS (CONFIRMED) | Removing destructive score authority from an implicit hook is a defensible local repair. The live subprocess probe returned `score=80 verdict=orphaned rc=0 survived=True`. |

#### Independent Findings (F-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | FAIL (MAJOR, CONFIRMED) | A valid `c-my-project-1` name is rejected by `_AI_SESSION_RE`; launch cleanup then terminates its live `claude bg-spare` (`src/ai_cli/session.py:431-432,467-518,521-562`). |
| F-2 | FAIL (MAJOR, CONFIRMED) | Quota scraping kills fixed name `ai-quota-scrape` twice without ownership metadata (`src/ai_cli/quota.py:476-506,646-654`), and is reachable from configured session auto-start (`src/ai_cli/session_script.py:593-595`; `src/ai_cli/process_manager.py:80-119`). |
| F-3 | FAIL (MAJOR, CONFIRMED) | `--sandbox` recreation kills any live tmux session matching the derived name after only `has-session` (`src/ai_cli/main.py:3197-3202`). |
| F-4 | FAIL (MAJOR, CONFIRMED) | `ai ps clean` reopens a PID and terminates it after an interactive delay without revalidating process identity (`src/ai_cli/process_hygiene.py:603-648,789-833`). |
| F-5 | FAIL (MAJOR, CONFIRMED) | Tunnel and CDP stop commands trust PID-only state files and terminate the current PID holder without start-time or command verification (`src/ai_cli/tunnel.py:104-113,316-328`). |
| F-6 | FAIL (MAJOR, CONFIRMED) | The approved process-hygiene plan still mandates score-based auto-kill on launch and cron (`docs/plans/process-hygiene-plan.md:68-86,181-184,250-266,341-347`). |

#### Destructive call-site inventory

| Site | Trigger / target | Ownership verdict |
|---|---|---|
| `src/ai_cli/main.py:3201` | Explicit sandbox relaunch; tmux name | **UNGUARDED — F-3.** Existence plus name is not a managed-session identity. |
| `src/ai_cli/main.py:3216` | Relaunch; all panes report dead | Acceptable for the requested live-process invariant: every pane must be dead; no live sibling process is signalled. |
| `src/ai_cli/main.py:3271` | Configuration failure after successful `new-session` | Conclusively scoped to the session this invocation just created. |
| `src/ai_cli/quota.py:490,650` | Poll/scrape; fixed tmux name | **UNGUARDED — F-2.** No session ID, marker, token, or generation revalidation. |
| `src/ai_cli/session.py:510,514` | Arbitrary managed-session launch; PID from sibling state | **UNGUARDED — F-1.** Identity is checked, but live-session ownership can be falsely rejected by name parsing. |
| `src/ai_cli/process_hygiene.py:627` | Explicit clean; inventory PID | **UNGUARDED — F-4.** No time-of-use process identity check. |
| `src/ai_cli/tunnel.py:111,324` | Explicit stop; PID file | **UNGUARDED — F-5.** PID-only durable identity. |
| `src/ai_cli/process_probe.py:310-318,407-412` | Same-transcript abandoned-process reclaim | Guarded: caller revalidates recorded process start identity immediately before `end_process()` (`src/ai_cli/main.py:464-495`). |
| `src/ai_cli/session_script.py:230,284,332,368,417,550,674` | Supervisor-owned direct child/watcher PIDs | Guarded by direct parent-created PID ownership; `kill -0` lines are probes, not termination. |
| `src/ai_cli/session_script.py:350` | Clean supervisor exit; baked current tmux name | Guarded by topology: the executing supervisor is still inside the uniquely named session it ends. |
| `src/ai_cli/session_script.py:261,327` | Child process group `SIGCONT`; child self-`SIGSTOP` | Non-destructive job-control signals scoped to the newly created child. |
| `src/ai_cli/stale_session_reaper.py:176` | Explicitly started reaper; exact tmux ID | Guarded by token, same-boot heartbeat, two identity snapshots, generation lease, and atomic fingerprint compare (`src/ai_cli/stale_session_reaper.py:148-183,300-488`). |
| `src/ai_cli/messaging.py:313` | Client-owned tunnel `Popen` | Guarded by the direct `Popen` handle created by that client. |
| `src/ai_cli/transport.py:282,286,292,296,359` | Transport-loop child `Popen` | Guarded by direct `Popen` handles created by that transport loop. |
| Entire source tree | `pkill` | No call site found at `744499b4`. |

#### IC-1: The contained/sufficient scope conclusion contradicts retained authority — `MAJOR`

**Location:** `docs/bugs/cross-session-mosh-termination.md:66-80`;
`src/ai_cli/main.py:2830-2834`; `src/ai_cli/session.py:467-562`

**Evidence:**

> `Removing signal authority from one implicit command is contained,`
>
> `reversible, and sufficient; redesign criteria are not met.`
>
> `This runs as part of launching an arbitrary session.`
>
> `_sweep_orphaned_claude_bg_spares(active_sessions, orphan_bg_spare_timeout_seconds, now)`

The record acknowledges a broader known pattern but counts only the incident's one subsystem.
Source inspection finds another launch-time module with process termination authority and a
second configured auto-start path with tmux kill authority.

**Why it matters:** Readers are told the architectural invariant is satisfied and redesign is
unnecessary when the same one-session-launch-signals-a-sibling authority still exists. That can
end remediation at the sixth narrow patch rather than closing the class.

**Verification command:**

```bash
git show 744499b4:docs/bugs/cross-session-mosh-termination.md | nl -ba | sed -n '66,80p'
git show 744499b4:src/ai_cli/session.py | nl -ba | sed -n '521,562p' | rg 'arbitrary session|_sweep_orphaned|active_sessions|_AI_SESSION_RE'
```

**Recommendation:** Change the bug record to distinguish incident containment from class
elimination. Keep `fix-verified` only for the cron occurrence, mark class-level closure blocked,
and require a complete authority inventory plus removal or generation-fenced ownership for every
launch/restart/exit signal path.

#### JA-1: The whole-class no-cross-session-kill invariant is unsatisfied — `MAJOR`

**Location:** founding ask and audit scope; `src/ai_cli/main.py:2830-2834`;
`src/ai_cli/session.py:431-562`; `src/ai_cli/session_script.py:585-595`;
`src/ai_cli/process_manager.py:80-119`; `src/ai_cli/quota.py:476-506,646-654`

**Evidence:**

> `# Session lifecycle code must never signal another session's processes.`
>
> `This runs as part of launching an arbitrary session.`
>
> `# Kill any stale scrape session left by a previous failed run`
>
> `["tmux", "kill-session", "-t", window_name]`

The first retained path can terminate a live helper owned by another valid session (F-1). With
quota auto-start enabled, a launch starts a watcher whose scrape kills a fixed tmux name without
proof that the session is its own (F-2).

**Why it matters:** The requested structural boundary is still false in reachable configurations;
a sibling session's process can still be terminated because another session launches.

**Verification command:**

```bash
git grep -n -E 'kill-session|\.terminate\(|\.kill\(|os\.kill\(|os\.killpg\(' 744499b4 -- 'src/ai_cli/*.py'
git show 744499b4:src/ai_cli/session_script.py | nl -ba | sed -n '585,595p'
```

**Recommendation:** Establish one enforceable boundary: implicit session lifecycle code must have
no cross-session signal capability. Move reclamation to explicit commands or the generation-
fenced reaper, require an immutable owner token plus time-of-use identity for all remaining
destructive paths, and add an architecture test enumerating forbidden call edges.

#### DV-1: The valid cron regression does not prove the lifecycle-wide guard — `MAJOR`

**Location:** `tests/test_process_hygiene.py:825-865`;
`docs/bugs/cross-session-mosh-termination.md:79-95`

**Evidence:**

> `rc = cmd_ps(["cron"], self._make_config(), stdout_fn=lambda _line: None)`
>
> `assert sibling.poll() is None, "cron terminated a live sibling mosh transport"`
>
> `Consequently, session launch, replacement, restart, and exit transitions have`
>
> `no route through heuristic process scoring to another session's process.`

The test starts a real subprocess and correctly asserts its survival, but mocks inventory and
calls `cmd_ps` directly. It does not generate/run the session child or invoke the other launch-
reachable cleaners. The audit's equivalent real-process probe passed while F-1 reproduced.

**Why it matters:** A strong unit-level negative control is being used as system-level evidence.
It can stay green while the class invariant is broken elsewhere, which is the exact state at the
target commit.

**Verification command:**

```bash
.venv/bin/python -B -c '
import subprocess, sys
from unittest.mock import patch
from ai_cli.process_hygiene import ProcessInfo, cmd_ps, score_mosh_server, _verdict_for
sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
score, detail = score_mosh_server(sibling.pid, 25 * 3600, "mosh-server new -s -c 256", set(), {"c-r-myproject-1": True})
classified = ProcessInfo(sibling.pid, "mosh-server", 25 * 3600, score, _verdict_for(score), detail, "local", "mosh-server")
try:
    with patch("ai_cli.process_hygiene.collect_local_processes", return_value=[classified]), patch("ai_cli.process_hygiene.collect_idle_since_launch_sessions", return_value=[]), patch("ai_cli.process_hygiene._clean_stale_transport_files"):
        rc = cmd_ps(["cron"], {}, stdout_fn=lambda _line: None)
    print(f"score={score} verdict={classified.verdict} rc={rc} survived={sibling.poll() is None}")
finally:
    if sibling.poll() is None: sibling.terminate()
    sibling.wait(timeout=5)'
```

**Recommendation:** Keep this regression, but add an authority-level integration test that runs
every implicit launch/restart/exit hook against foreign live processes and tmux sessions, including
hyphenated names and quota auto-start. Add a mutation/allowlist check that fails if a new signal
edge becomes reachable from those hooks.

#### F-1: Hyphenated live sessions are filtered out and their helper is killed at launch — `MAJOR`

**Location:** `src/ai_cli/session.py:431-432,450-518,521-562,648-705`;
`src/ai_cli/main.py:2830-2834`; `tests/test_session.py:513-547`

**Evidence:**

> `_AI_SESSION_RE = re.compile(r"^[cgp](-r)?-[a-zA-Z0-9]+-\d+$")`
>
> `if not _AI_SESSION_RE.match(session_name):`
>
> `continue`
>
> `process.terminate()`

`build_session_name("c", "my-project", "1", ...)` legitimately returns
`c-my-project-1`, but the classifier accepts only one alphanumeric project segment. The production
probe produced `active_after_filter=[]`, `live_match=False`, and `terminate_calls=1`. Existing
tests use only `c-test-1` and `c-r-test-1`, so they miss the legal hyphen case.

**Why it matters:** Starting any other managed session can terminate a live helper owned by a
sibling session whose project prefix contains a hyphen. This is the prohibited recurring bug
shape, in a path untouched by the shipped fix.

**Verification command:**

```bash
.venv/bin/python -B -c '
import json
from unittest.mock import MagicMock, patch
from ai_cli import session
built, ai_name = session.build_session_name("c", "my-project", "1", {}, is_remote=False)
listed = MagicMock(returncode=0, stdout=f"{built}\n"); captured = {}
with patch("ai_cli.session.subprocess.run", return_value=listed), patch("ai_cli.session._sweep_orphaned_claude_bg_spares", side_effect=lambda active, *_: captured.setdefault("active", active)), patch("ai_cli.session._sweep_stale_iterm2_profiles"):
    session.cleanup_stale_sessions({"session": {}})
state_file = MagicMock(); state_file.read_text.return_value = json.dumps({"pid": 4242, "startedAt": 0, "updatedAt": 0, "name": ai_name, "status": "idle"})
sessions_dir = MagicMock(); sessions_dir.exists.return_value = True; sessions_dir.glob.return_value = [state_file]
process = MagicMock(); process.create_time.return_value = 0; process.cmdline.return_value = ["claude", "bg-spare"]
active = captured["active"]
with patch("ai_cli.session._claude_sessions_dir", return_value=sessions_dir), patch("psutil.Process", return_value=process):
    session._sweep_orphaned_claude_bg_spares(active, 60, 1000)
print(f"built={built}"); print(f"active_after_filter={sorted(active)}"); print(f"live_match={session._has_live_tmux_session(ai_name, active)}"); print(f"terminate_calls={process.terminate.call_count}")'
```

**Recommendation:** Remove termination from `cleanup_stale_sessions()` so launch cleanup is
bookkeeping-only, as the reaper design requires. If helper reclamation remains automatic, move it
behind a generation-bound owner marker and revalidate that identity immediately before signalling.
Add local/remote and hyphenated/multi-segment regression cases.

#### F-2: Quota scraping kills an unowned fixed-name tmux session — `MAJOR`

**Location:** `src/ai_cli/quota.py:476-506,646-654,690-701,722-762`;
`src/ai_cli/session_script.py:593-595`; `src/ai_cli/process_manager.py:80-119`;
`src/ai_cli/config.py:247-254`

**Evidence:**

> `window_name = "ai-quota-scrape"`
>
> `# Kill any stale scrape session left by a previous failed run`
>
> `["tmux", "kill-session", "-t", window_name]`
>
> `finally:`

The scraper destroys that name before `new-session` and again in `finally`. It never records or
checks a managed marker, captured session ID, creator token, or process identity. Quota auto-start
is off by default but explicitly reachable by configuration during session launch.

**Why it matters:** A user or sibling tool with a live tmux session named `ai-quota-scrape` can
have it destroyed by periodic quota polling; a same-name replacement can also be destroyed by the
`finally` call. Exact name targeting is not ownership.

**Verification command:**

```bash
.venv/bin/python -B -c '
from unittest.mock import MagicMock, patch
from ai_cli.quota import _scrape_usage_hidden_pane
calls = []
def run(argv, **kwargs):
    calls.append(argv); return MagicMock(returncode=1 if argv[1] == "new-session" else 0, stdout="")
with patch("ai_cli.quota.subprocess.run", side_effect=run), patch("ai_cli.quota.reap_cc_update_staging"):
    result = _scrape_usage_hidden_pane()
kills = [argv for argv in calls if argv[1] == "kill-session"]
ownership = [argv for argv in calls if argv[1] in {"show-options", "display-message"}]
print(f"result={result} kill_targets={[argv[-1] for argv in kills]} ownership_reads={len(ownership)}")'
```

**Recommendation:** Create each scrape under a unique name, capture its opaque tmux session ID,
write a random managed-generation option, and compare ID plus token immediately before cleanup.
Refuse to delete pre-existing tokenless/foreign sessions; never pre-kill a shared fixed name.

#### F-3: Sandbox relaunch treats a tmux name as proof of ownership — `MAJOR`

**Location:** `src/ai_cli/main.py:3197-3202`

**Evidence:**

> `existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True, check=False)`
>
> `if existing.returncode == 0 and sandbox:`
>
> `subprocess.run(["tmux", "kill-session", "-t", session_id], ... )`

The branch does not query `@ai_cli_session_generation` or any creator identity. A manually or
independently created live session can legally collide with the derived name.

**Why it matters:** An explicit sandbox launch can destroy a live tmux session that this tool did
not create. User intent to enable sandboxing is not proof of ownership of the colliding session.

**Verification command:**

```bash
git show 744499b4:src/ai_cli/main.py | nl -ba | sed -n '3197,3203p'
git show 744499b4:src/ai_cli/main.py | sed -n '3197,3203p' | rg 'generation|@ai_cli|display-message|show-option' || echo 'ownership-check: none'
```

**Recommendation:** Before destructive recreation, require the managed marker and capture the
existing generation/session ID. Refuse a live tokenless or foreign collision with a clear message;
kill only after atomic ID/token revalidation. Add a foreign-name-collision regression.

#### F-4: `ai ps clean` can terminate a recycled PID after confirmation — `MAJOR`

**Location:** `src/ai_cli/process_hygiene.py:603-648,769-833`

**Evidence:**

> `answer = input(f"\nKill {len(to_kill)} orphan(s)? [y/N] ").strip().lower()`
>
> `psutil.Process(proc.pid).terminate()`

`ProcessInfo` carries no process-creation identity. Inventory and prompt can take arbitrarily long;
the target can exit and its PID can be reused before `psutil.Process(pid)` is opened. The
executable probe confirmed zero identity reads and one termination.

**Why it matters:** Even the explicit cleanup path can kill an unrelated process that inherited a
recycled PID. Confirmation approves the displayed process, not a future process at the same PID.

**Verification command:**

```bash
.venv/bin/python -B -c '
from unittest.mock import MagicMock, patch
from ai_cli.process_hygiene import ProcessInfo, auto_clean_orphans
record = ProcessInfo(4242, "mosh-server", 90000, 80, "orphaned", "heuristic", "local", "mosh-server")
replacement = MagicMock(); log_path = MagicMock(); log_path.open.return_value.__enter__.return_value = MagicMock()
with patch("ai_cli.process_hygiene.psutil.Process", return_value=replacement) as lookup:
    killed = auto_clean_orphans([record], log_path)
print(f"lookup={lookup.call_args} identity_reads={replacement.create_time.call_count} terminate_calls={replacement.terminate.call_count} killed={len(killed)}")'
```

**Recommendation:** Capture a typed process identity (at minimum PID plus creation time and
expected command) during inventory and revalidate it immediately before `terminate()`. Treat
missing/mismatched identity as preserve-and-report. Keep the confirmation tied to that identity.

#### F-5: Tunnel and CDP stop commands trust stale PID-only state — `MAJOR`

**Location:** `src/ai_cli/tunnel.py:70-112,285-328`

**Evidence:**

> `pid_file.write_text(str(proc.pid))`
>
> `pid = int(pid_file.read_text().strip())`
>
> `psutil.Process(pid).terminate()`

Both durable records contain only a PID, and both stop paths perform no start-time, executable,
port-owner, or command-line check before signalling its current holder.

**Why it matters:** After an unclean exit and PID reuse, `ai tunnel stop` or `ai cdp stop` can
terminate an unrelated live process. A stale control file is a known normal failure mode, so PID
reuse is reachable rather than merely theoretical.

**Verification command:**

```bash
.venv/bin/python -B -c '
from unittest.mock import MagicMock, patch
from ai_cli import tunnel
state = MagicMock(); pid_file = MagicMock(); pid_file.exists.return_value = True; pid_file.read_text.return_value = "4242"; state.__truediv__.return_value = pid_file
proc = MagicMock()
with patch("ai_cli.tunnel.get_xdg_state_home", return_value=state), patch("ai_cli.tunnel.psutil.Process", return_value=proc) as lookup:
    tunnel._cmd_tunnel_stop(1234); tunnel._cmd_cdp_stop(1234)
print(f"pid_lookups={lookup.call_count} identity_reads={proc.create_time.call_count} terminate_calls={proc.terminate.call_count}")'
```

**Recommendation:** Store versioned JSON containing PID, creation time, expected executable/
command, and port. Revalidate every field immediately before signalling; on mismatch, remove or
quarantine only the stale record and preserve the process.

#### F-6: The approved plan still mandates the unsafe behavior — `MAJOR`

**Location:** `docs/plans/process-hygiene-plan.md:29-44,64-88,181-184,250-266,341-347`

**Evidence:**

> `| ≥ 80 | **orphaned** | Auto-kill on session start + daily cron |`
>
> `**Note:** killing a mosh-server is safe regardless`
>
> ```text
> Wire auto-kill into session start (`ai c` launch) and daily cron.
> ```
>
> ```text
> - [ ] `ai ps cron` refreshes remote cache and kills orphans on both machines
> ```

The plan has three approval-log rows and remains the closest implementation contract for this
feature. It neither marks the dangerous behavior superseded nor links the shipped reversal.

**Why it matters:** A maintainer implementing or reconciling this approved plan literally will
restore the exact cross-session termination mechanism removed by `744499b4`. It also makes the
stage claim that no relevant plan exists factually incomplete.

**Verification command:**

```bash
git show 744499b4:docs/plans/process-hygiene-plan.md | nl -ba | sed -n '64,88p;181,184p;250,266p;341,347p'
```

**Recommendation:** Mark the auto-hygiene portions superseded by the bug record, change T-03 and
its ACs to non-destructive cron behavior, remove the "safe regardless" claim, and add an explicit
parity decision: scoring is advisory; only identity-bound explicit cleanup may signal. Link the
class-wide follow-up until every lifecycle authority edge is closed.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| IC-1 | CLAIMED FIXED (commit `067a358b`, PR #129) | Bug record's "contained and sufficient" claim corrected to accurately describe class-wide remediation; status downgraded `fix-verified` → `fix-implemented` pending independent re-audit (`docs/bugs/cross-session-mosh-termination.md`). |
| JA-1 | CLAIMED FIXED (commit `067a358b`, PR #129) | Every implicit/explicit signal site below now requires proven ownership before signalling, closing the class-wide gap this finding identified. |
| DV-1 | CLAIMED FIXED (commit `067a358b`, PR #129) | New regression test `tests/test_stale_session_reaper.py` (added lines) asserts a live sibling process survives across the launch/quota/sandbox/explicit-clean paths, not just the cron path DV-1 flagged as insufficient coverage. |
| F-1 | CLAIMED FIXED (commit `067a358b`, PR #129) | `process.terminate()` removed entirely from implicit launch-time cleanup; `_sweep_orphaned_claude_bg_spares` renamed `_sweep_stale_claude_session_state` (`src/ai_cli/session.py:457`), now bookkeeping-only — `cleanup_stale_sessions` (`src/ai_cli/session.py:485-495`) no longer calls any terminate path. |
| F-2 | CLAIMED FIXED (commit `067a358b`, PR #129) | Quota scraping now allocates a unique per-scrape tmux window name with a `secrets.token_urlsafe(32)` generation marker (`src/ai_cli/quota.py:487`), captures identity via `capture_tmux_session_identity` (`quota.py:510`), and kills only that exact identity via the new `tmux_ownership` module's atomic `tmux if-shell` fence (`quota.py:658`; `src/ai_cli/tmux_ownership.py`). |
| F-3 | CLAIMED FIXED (commit `067a358b`, PR #129) | `--sandbox` relaunch captures identity and atomically revalidates the managed generation via `tmux_ownership.capture_tmux_session_identity`/`kill_owned_tmux_session` before killing, refusing a tokenless/unowned collision (`src/ai_cli/main.py:3201-3229`). |
| F-4 | CLAIMED FIXED (commit `067a358b`, PR #129) | `ai ps clean` now captures `create_time` at inventory (`src/ai_cli/process_hygiene.py:60,63-66,134,458`) and revalidates it immediately before `terminate()`, skipping instead of killing on mismatch (`process_hygiene.py:640-645`). |
| F-5 | CLAIMED FIXED (commit `067a358b`, PR #129) | Tunnel/CDP stop commands now persist and revalidate a full `_ManagedProcessIdentity` (pid, create_time, executable, command, port) via `_matching_process` before signalling; a stale record is removed without touching the live PID holder (`src/ai_cli/tunnel.py:26-56,75-132`). |
| F-6 | CLAIMED FIXED (commit `067a358b`, PR #129) | `docs/plans/process-hygiene-plan.md`'s score-based auto-kill-on-launch/cron sections marked superseded; the "safe regardless" claim corrected. |

No inline target fixes were applied in Round 1. All nine resolutions above are Round-2-unverified
claims from PR #129 (commit `067a358b`) — the Round 2 dispatch below exists to check each one
against the actual code, not to take this table's word for it. Every resolution requires a source, test, bug-record, or plan
change outside this audit document's sole writable target.

### R1 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---------|---------|----------|--------|-------|
| IC-1 | Target bug-scope excerpt plus launch-cleanup grep | No retained implicit signal owner if removal is sufficient | Record says `sufficient`; source says `arbitrary session` and calls `_sweep_orphaned_claude_bg_spares` | ✅ reproduces |
| JA-1 | Full target-commit signal-site grep | No unowned signal edge reachable from lifecycle | Found launch cleanup, quota fixed-name kills, and sandbox name-only kill in addition to guarded sites | ✅ reproduces |
| DV-1 | Real subprocess through `cmd_ps(["cron"])` | Cron survivor alone must not imply system invariant | `score=80 verdict=orphaned rc=0 survived=True`; F-1 independently terminates | ✅ reproduces |
| F-1 | In-memory production launch/name/sweep probe | Preserve helper for valid live hyphenated session | `built=c-my-project-1`; `active_after_filter=[]`; `live_match=False`; `terminate_calls=1` | ✅ reproduces |
| F-2 | Controlled `_scrape_usage_hidden_pane()` subprocess adapter | Do not kill a session without ownership | `kill_targets=['ai-quota-scrape', 'ai-quota-scrape'] ownership_reads=0` | ✅ reproduces |
| F-3 | Target source excerpt plus ownership-query grep | Managed identity required before kill | `has-session` then `kill-session`; `ownership-check: none` | ✅ reproduces |
| F-4 | Controlled `auto_clean_orphans()` process adapter | Revalidate displayed process identity | `lookup=call(4242) identity_reads=0 terminate_calls=1 killed=1` | ✅ reproduces |
| F-5 | Controlled tunnel/CDP PID-file adapters | Revalidate stored process identity | `pid_lookups=2 identity_reads=0 terminate_calls=2` | ✅ reproduces |
| F-6 | Target plan excerpts including Approval Log | Shipped non-destructive behavior | Approved plan still says launch/cron auto-kill and `ai ps cron ... kills orphans` | ✅ reproduces |

**Verified: 9/9 findings reproduced against source byte-identical to commit `744499b4`.** Pytest
was attempted but failed before collection with `FileNotFoundError: No usable temporary directory`;
that infrastructure block is not represented as a passing test.

### R1 Receipt Payload Projection
```json
{
  "iteration": 1,
  "scope": {
    "mode": "discovery",
    "purpose": "Round 1 independent implementation audit of the shipped cross-session termination fix",
    "members_in_scope": [
      "docs/bugs/cross-session-mosh-termination.md"
    ],
    "diff_since_digest": null,
    "capability_plan": [
      "cx-audit-high"
    ]
  },
  "findings": [
    {
      "finding_key": "ai-cli-1wzz:IC-1",
      "id": "IC-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e848623a999757d33fd3d8a9f816a6ff4218e3e87fd23fe0c3cfe6921e1ff7cb",
      "cluster": {
        "spec_version": "bug-1.0.0",
        "invariant_id": "implicit-lifecycle-no-cross-session-signal",
        "subsystem": "bug-record",
        "violation_kind": "scope-conclusion-conflict"
      },
      "reachable_behavior": "The record declares one-command containment sufficient while arbitrary session launch still reaches a sibling-process terminator.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:JA-1",
      "id": "JA-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "3684bf2eb0a77b37005f6a4eb35934b1dc0fbad9deab625da43e37a39c5c71d7",
      "cluster": {
        "spec_version": "bug-1.0.0",
        "invariant_id": "implicit-lifecycle-no-cross-session-signal",
        "subsystem": "session-lifecycle",
        "violation_kind": "acceptance-invariant-unsatisfied"
      },
      "reachable_behavior": "Starting a managed session can still reach cleanup or quota paths that signal processes or tmux sessions not conclusively owned by that launch.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:DV-1",
      "id": "DV-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "22da09a2415b0afa71404abdea42a5aae80d709e0e1e401bd829f60df5632961",
      "cluster": {
        "spec_version": "bug-1.0.0",
        "invariant_id": "implicit-lifecycle-no-cross-session-signal",
        "subsystem": "regression-coverage",
        "violation_kind": "guard-scope-overclaim"
      },
      "reachable_behavior": "The cron-only survivor regression passes while other launch-reachable signal owners remain capable of terminating a sibling process.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-1",
      "id": "F-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "4be0d3bf3751c5baea422e9ae793f4c04de025e162a64b474dcf02bf0f5a4e1a",
      "cluster": {
        "spec_version": null,
        "invariant_id": "implicit-lifecycle-no-cross-session-signal",
        "subsystem": "bg-spare-cleanup",
        "violation_kind": "name-classifier-false-negative"
      },
      "reachable_behavior": "A valid hyphenated live session is filtered from the active set, causing another session launch to terminate its live background helper.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-2",
      "id": "F-2",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e71908a3ae85d879b93a459d732ace20ff1594b2646ae68545c5bc7cd7a6cda1",
      "cluster": {
        "spec_version": null,
        "invariant_id": "owned-session-destruction-only",
        "subsystem": "quota-scrape",
        "violation_kind": "fixed-name-unowned-kill"
      },
      "reachable_behavior": "Quota polling destroys any tmux session using the fixed scrape name before creation and again during cleanup without checking ownership.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-3",
      "id": "F-3",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "ad63c84311244b496f9fa242bc302eb887f45fc60c372db0447d0a73559fcf77",
      "cluster": {
        "spec_version": null,
        "invariant_id": "owned-session-destruction-only",
        "subsystem": "session-launch",
        "violation_kind": "name-only-destruction"
      },
      "reachable_behavior": "A sandbox relaunch kills a live same-name tmux session after only an existence check, including a foreign or manually created collision.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-4",
      "id": "F-4",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "f22309373f31cd8d3c8ed97a7f415b241eaab564c3f4bacf498e8794d5b596d4",
      "cluster": {
        "spec_version": null,
        "invariant_id": "process-identity-before-signal",
        "subsystem": "process-hygiene",
        "violation_kind": "pid-reuse-toctou"
      },
      "reachable_behavior": "During the cleanup confirmation interval the displayed target can exit and an unrelated process can inherit its PID before termination.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-5",
      "id": "F-5",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e4a25502b18ea45ece85c4fb773b8d5e16bdbb3e9eb96dec0cebaa3170135af0",
      "cluster": {
        "spec_version": null,
        "invariant_id": "process-identity-before-signal",
        "subsystem": "utility-stop",
        "violation_kind": "pid-reuse-toctou"
      },
      "reachable_behavior": "A stale tunnel or browser PID file can cause an explicit stop command to terminate an unrelated process that inherited the PID.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    },
    {
      "finding_key": "ai-cli-1wzz:F-6",
      "id": "F-6",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e6ab77355cdb64e46d0f748d17ab8d99c2c1ecc649066141b2309b5190bc3be7",
      "cluster": {
        "spec_version": "plan-legacy",
        "invariant_id": "implicit-lifecycle-no-cross-session-signal",
        "subsystem": "process-hygiene-plan",
        "violation_kind": "stale-destructive-contract"
      },
      "reachable_behavior": "A maintainer following the approved plan literally restores score-based process killing during session launch and cron.",
      "disposition": "open-blocking",
      "member_digest": "sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c"
    }
  ],
  "new_by_severity": {
    "critical": 0,
    "major": 9,
    "minor": 0
  },
  "major_nonblocking_justifications": {}
}
```

<!-- /doc:region name="round_1_findings" -->

## Round 2 — Verification Pass (append-only)

**Round 2 auditor:** Codex audit (GPT-5; exact deployment ID not exposed, effort: medium)

**Round 2 date:** 2026-09-10

**Round 2 scope:** Verify all nine Round 1 MUST-fix findings against commit `067a358b`, including
the shared tmux ownership primitive and the claimed regression coverage. Surface only defects in
the remediation or claims it introduced. No target source, tests, bug record, or plan were edited.

### R2 Summary

Seven of nine Round 1 findings PASS. JA-1 and DV-1 are PARTIAL because one reachable lifecycle
kill remains unsafe and the added tests do not exercise that race. One new MAJOR finding, N-1, is
CONFIRMED: the ordinary dead-pane relaunch path decides that a name is dead before it captures the
session identity. If another launch replaces that name in the gap, the later identity capture
binds to the replacement and the ID+generation-only fence kills that live session. This is the
same cross-session lifecycle failure class the fix is intended to eliminate.

The isolated ownership helper itself does execute comparison and kill in one `tmux if-shell`
command (`src/ai_cli/tmux_ownership.py:64-84`). The defect is that its predicate contains only
session ID and generation (`src/ai_cli/tmux_ownership.py:10-11,67-79`), while the dead-pane
authorization was observed separately and earlier (`src/ai_cli/main.py:3220-3229`). The established
reaper fence includes attachment and every pane's ID, PID, and dead state in the atomic predicate
(`src/ai_cli/stale_session_reaper.py:35-39,141-183`).

The requested pytest run was attempted twice. Both attempts failed before test collection because
this worker has no permitted temporary directory; the second attempt disabled capture and the
cache plugin but import-time `tempfile.gettempdir()` still failed. Therefore historical pass counts
and the new real-tmux tests are not independently verified by execution in this round.

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | **PASS (CONFIRMED)** | The record is `status: fix-implemented` and now says, verbatim, “The initial cron-only change contained the reported incident but was not sufficient to eliminate the broader class” and “Independent re-audit is still required” (`docs/bugs/cross-session-mosh-termination.md:5,68-73`). |
| JA-1 | **PARTIAL (CONFIRMED)** | F-1 through F-6's named repairs landed, but the class invariant is still false in the dead-pane relaunch race: `list-panes` establishes deadness at `src/ai_cli/main.py:3220-3227`, then identity is captured and killed at lines 3228-3229. A same-name live replacement between those operations is the identity that gets killed. See N-1. |
| DV-1 | **PARTIAL (CONFIRMED source gap; test execution BLOCKED)** | Coverage was broadened across several files, not solely `tests/test_stale_session_reaper.py` as claimed. The new real-tmux tests there cover tokenless identity and generation change (`tests/test_stale_session_reaper.py:610-651`), while sandbox composition mocks both ownership functions (`tests/test_cli.py:629-662`) and the dead-pane integration test has no mutation between deadness observation and capture (`tests/test_session_launch_integration.py:346-374`). No test covers N-1's reachable race. |

### R2.2 Round 1 F-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | **PASS (CONFIRMED)** | `_sweep_stale_claude_session_state()` only unlinks stale JSON state (`src/ai_cli/session.py:457-482`); `cleanup_stale_sessions()` calls only that bookkeeping sweep and the iTerm2 profile sweep (`src/ai_cli/session.py:485-496`). There is no `.terminate()` or `.kill()` in this module's production cleanup path. |
| F-2 | **PASS (CONFIRMED)** | Quota scraping uses `secrets.token_urlsafe(32)`, a per-invocation name, and capture with `expected_generation` (`src/ai_cli/quota.py:487-513`); cleanup calls only `kill_owned_tmux_session(identity)` (`src/ai_cli/quota.py:655-658`). That helper compares and kills in one `tmux if-shell` argv (`src/ai_cli/tmux_ownership.py:64-84`). |
| F-3 | **PASS (CONFIRMED)** | The `--sandbox` branch refuses a session without a valid generation identity and exits if the atomic ID+generation fence fails (`src/ai_cli/main.py:3198-3214`). F-3's prior name-only `kill-session` is gone. N-1 concerns the neighboring non-sandbox dead-pane authorization, not this explicit sandbox branch. |
| F-4 | **PASS (CONFIRMED)** | Both local inventory producers capture `create_time` (`src/ai_cli/process_hygiene.py:125-135,448-459`), and explicit cleanup skips missing/mismatched identity before `process.terminate()` (`src/ai_cli/process_hygiene.py:640-651`). Installed psutil's `terminate()` also performs its own pre-signal PID-reuse check. |
| F-5 | **PASS (CONFIRMED)** | The versioned state contains PID, creation time, executable, command, and port (`src/ai_cli/tunnel.py:22-56`); reading validates all fields and the requested port (`src/ai_cli/tunnel.py:75-110`); `_matching_process()` rechecks process identity (`src/ai_cli/tunnel.py:113-125`); both stop paths unlink stale records and signal only a returned match (`src/ai_cli/tunnel.py:215-227,429-444`). |
| F-6 | **PASS (CONFIRMED)** | The approved plan is marked `SUPERSEDED IN PART`, states background launch/cron are non-destructive, corrects the “safe” claim, rewrites T-03 to forbid implicit signals, and records the safety remediation (`docs/plans/process-hygiene-plan.md:3,30-38,91-95,256-275,348-355`). |

### R2.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| — | **N/A (CONFIRMED)** | Round 1 recorded no AD-N decisions (`docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md § Decisions Requiring Team Input`), and the full Round 1 backlog contains only IC-1, JA-1, DV-1, and F-1 through F-6. |

### R2.4 NEW issues surfaced

#### N-1: Dead-pane authorization is outside the generation fence — `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:3220-3229`;
`src/ai_cli/tmux_ownership.py:10-11,64-84`;
`src/ai_cli/stale_session_reaper.py:35-39,141-183`

**What the Round 1 Resolution Pass claimed:**

> “Every implicit/explicit signal site below now requires proven ownership before signalling,
> closing the class-wide gap this finding identified.”

The F-3 resolution also claims atomic generation revalidation for the neighboring sandbox path.

**Actual state:**

```python
# src/ai_cli/main.py:3226-3229
pane_states = [line for line in pane_check.stdout.splitlines() if line]
if pane_check.returncode == 0 and pane_states and all(state == "1" for state in pane_states):
    identity = _tmux_ownership.capture_tmux_session_identity(session_id)
    if identity is not None and _tmux_ownership.kill_owned_tmux_session(identity):
```

The destructive authorization (“all panes dead”) is evaluated before identity capture. If the
observed dead session disappears and a concurrent launch creates a live managed session with the
same name, `capture_tmux_session_identity(session_id)` captures the replacement. The helper then
atomically verifies only `#{session_id}|#{@ai_cli_session_generation}` and kills it
(`src/ai_cli/tmux_ownership.py:10-11,67-79`); it never revalidates `pane_dead`.

This is not merely a theoretical weakness in tmux's `if-shell`: the older reaper implementation
already treats pane topology as part of the fence, comparing session ID, generation, attachment,
window IDs, pane IDs, pane PIDs, and pane-dead flags inside the same server command
(`src/ai_cli/stale_session_reaper.py:35-39,158-183`). Its regression explicitly respawns a pane
between capture and fence and asserts survival (`tests/test_stale_session_reaper.py:654-665`). The
new dead-pane launch path does not use that fingerprint and its positive integration test does not
inject a replacement (`tests/test_session_launch_integration.py:346-374`).

**Why it matters:** Ordinary relaunch is an implicit session lifecycle action. In a reachable
concurrent launch/teardown interleaving, one invocation can terminate the newly live tmux session
created by another invocation—the prohibited cross-session destructive behavior and a MAJOR safety
invariant violation under this audit's binding rubric.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '3220,3230p'
nl -ba src/ai_cli/tmux_ownership.py | sed -n '10,11p;64,84p'
nl -ba src/ai_cli/stale_session_reaper.py | sed -n '35,39p;141,183p'
nl -ba tests/test_stale_session_reaper.py | sed -n '654,665p'
```

**Recommended fix (Round 3):** Capture the dead session's opaque identity and full pane
fingerprint before deciding, then compare that same ID, generation, attachment state, window/pane
IDs, pane PIDs, and dead flags inside the one `tmux if-shell` command that performs the kill. Reuse
or factor the established `SubprocessTmuxAdapter.capture_fingerprint()` / `fence_and_kill()`
contract rather than using the ID+generation-only cleanup helper. Add a deterministic integration
regression that replaces or respawns the named session after the initial dead observation and
before the fence; the replacement must survive and the launch must attach/retry or fail closed.

### R2.5 Verification Matrix

| Finding | Command | Expected | Actual | Pass? |
|---------|---------|----------|--------|-------|
| IC-1 | `rg -n 'status: fix-implemented|initial cron-only change contained|Independent re-audit' docs/bugs/cross-session-mosh-termination.md` | Corrected status/scope, pending re-audit | Lines 5, 68, and 73 match | ✅ |
| JA-1 / N-1 | `rg -n 'pane_states|all\\(state == "1"|capture_tmux_session_identity|kill_owned_tmux_session' src/ai_cli/main.py` | Deadness bound to same atomic fence | Deadness is lines 3226-3227; capture/kill are later at 3228-3229 | ❌ |
| DV-1 | Added-test diff plus focused test grep | Coverage of every claimed path and boundary | Tests are distributed; sandbox mocks both ownership calls; no dead-pane mutation test | ❌ |
| F-1 | `rg -n 'def cleanup_stale_sessions|def _sweep_stale_claude_session_state|\\.terminate\\(|\\.kill\\(' src/ai_cli/session.py` | Bookkeeping functions present; no signal call | Functions at 457/485; no terminate/kill matches | ✅ |
| F-2 | Quota/ownership symbol grep | Unique name, expected token, single-call atomic kill | `quota.py:487-510,658`; `tmux_ownership.py:68-79` | ✅ |
| F-3 | `nl -ba src/ai_cli/main.py \| sed -n '3198,3214p'` | Sandbox refuses unowned/mutated session | Identity `None` and failed fence both exit 1 | ✅ |
| F-4 | Process identity symbol grep | Capture at inventory; mismatch skips signal | Captures at lines 134/458; checks at 640-648 | ✅ |
| F-5 | Tunnel identity/stop symbol grep | Full durable identity rechecked before both signals | Validation at lines 75-125; stops at 215-227/429-444 | ✅ |
| F-6 | Plan supersession grep | Destructive contract withdrawn throughout | Status, safety note, T-03, and Approval Log all corrected | ✅ |

**Verified: 7/9 backlog dispositions pass by reproduced source evidence; 2/9 are PARTIAL. N-1
reproduces by source ordering and comparison with the existing full-fingerprint fence. Test
execution is UNVERIFIED because pytest failed before collection under the worker's temporary-file
policy.**

### R2 Recommendations

**MUST be fixed before closing AI-CLI-1wzz or claiming class-wide verification:**

- N-1: move dead-pane authorization into the atomic tmux fingerprint fence.
- JA-1 and DV-1: carry both PARTIAL dispositions forward until N-1 is fixed and a mutation-style
  regression proves a live replacement survives the observation-to-kill interleaving.

**SHOULD be fixed before the next verification gate:**

- Correct the R1 Resolution Pass's DV-1 claim that all class-level regressions were added in
  `tests/test_stale_session_reaper.py`; the coverage is distributed and partially mocked.
- Re-run the named focused suite in a test environment with a writable temporary directory and
  record actual results; this round cannot independently confirm the historical pass counts.

**Can be folded into a follow-up:**

- None. The only new issue is in the destructive lifecycle boundary and is blocking.

### Post-R2 status note (2026-09-11, not a new round)

N-1 fixed and merged: ai-cli-utils PR #132, commit `518f222`. Reused
`stale_session_reaper.py`'s `SubprocessTmuxAdapter.capture_fingerprint()` /
`fence_and_kill()` pattern in the dead-pane relaunch path (`src/ai_cli/main.py`), closing
the same-name-replacement race. New mutation regression
`test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives`
independently confirmed RED against pre-fix code, GREEN after (verified via `git stash`, not
just the delegated worker's self-report).

A formal, driver-brokered Round 3 confirmation could not be dispatched this session.
`audit_loop.py next confirmation` returned `audit-loop: respecify required; freeze
docs/bugs/cross-session-mosh-termination.md.invariants.yaml before another dispatch` — the
`high` risk tier's `cluster_recurrence_limit`/`blocking_streak_limit` policy tripped after 2
consecutive dispatched rounds both carrying blocking findings in the same invariant cluster.

This is a genuine, code-enforced human gate, not a bug or a style choice: `config/
audit_risk_tiers.yaml` sets `respecify_requires_human_ratification: true` for `high`, and
`scripts/invariant_register.py`'s `validate_freeze()` hard-rejects any freeze whose
`human_ratification` event does not have `actor.family == "human"` — an AI-authored
ratification record cannot satisfy this check by construction. Separately, authoring the
`.invariants.yaml` register itself (full state/transition/dimension/cell formalization,
schema in `invariant_register.py:REQUIRED`) is real design work, and the fleet's own
decision-tier floor reserves design decisions for Opus/Fable/Codex-flagship, not the Sonnet
session that ran this loop.

**Next step, owned by Sergei, not autonomous:** either (a) ratify a respecify — author or
review the invariants register and record a human decision event over it — then dispatch a
real Round 3 confirmation of the `N-1` fix through the driver, or (b) accept this session's
independent manual verification (diff read + RED/GREEN stash test + independent suite re-run)
as sufficient and close `AI-CLI-1wzz` without a formal Round 3. `AI-CLI-1wzz`'s bug record
correctly says `fix-implemented`, not `fix-verified`, reflecting this open item either way.

(A real, separate driver bug found investigating this: the halt-handling path in
`audit_loop.py`'s `next()` crashes with `ModuleNotFoundError: No module named 'yaml'` when
invoked via bare `python3` instead of `uv run` — worth filing, not blocking.)

### R2 Receipt Payload Projection
```json
{
  "iteration": 2,
  "scope": {
    "mode": "verification",
    "purpose": "Round 2 verification of all nine Round 1 cross-session termination findings",
    "members_in_scope": [
      "docs/bugs/cross-session-mosh-termination.md"
    ],
    "diff_since_digest": "blob:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c",
    "capability_plan": [
      "filesystem-read",
      "git-history-read",
      "shell-read",
      "test-execution-requested"
    ]
  },
  "findings": [
    {
      "finding_key": "ai-cli-1wzz:IC-1",
      "id": "IC-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e848623a999757d33fd3d8a9f816a6ff4218e3e87fd23fe0c3cfe6921e1ff7cb",
      "cluster": {"spec_version": "bug-1.0.0", "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "bug-record", "violation_kind": "scope-conclusion-conflict"},
      "reachable_behavior": "The corrected record no longer declares the cron-only containment sufficient and remains pending independent verification.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:JA-1",
      "id": "JA-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "3684bf2eb0a77b37005f6a4eb35934b1dc0fbad9deab625da43e37a39c5c71d7",
      "cluster": {"spec_version": "bug-1.0.0", "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "session-lifecycle", "violation_kind": "acceptance-invariant-unsatisfied"},
      "reachable_behavior": "Ordinary relaunch can still kill a concurrent live same-name replacement because dead-pane authorization is not part of the atomic fence.",
      "disposition": "open-blocking",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:DV-1",
      "id": "DV-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "22da09a2415b0afa71404abdea42a5aae80d709e0e1e401bd829f60df5632961",
      "cluster": {"spec_version": "bug-1.0.0", "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "regression-coverage", "violation_kind": "guard-scope-overclaim"},
      "reachable_behavior": "The broadened suite has no mutation test for replacement after dead-pane observation and mocks the sandbox ownership boundary.",
      "disposition": "open-blocking",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-1",
      "id": "F-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "4be0d3bf3751c5baea422e9ae793f4c04de025e162a64b474dcf02bf0f5a4e1a",
      "cluster": {"spec_version": null, "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "bg-spare-cleanup", "violation_kind": "name-classifier-false-negative"},
      "reachable_behavior": "Implicit launch cleanup no longer signals background helper processes and only removes stale state records.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-2",
      "id": "F-2",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e71908a3ae85d879b93a459d732ace20ff1594b2646ae68545c5bc7cd7a6cda1",
      "cluster": {"spec_version": null, "invariant_id": "owned-session-destruction-only", "subsystem": "quota-scrape", "violation_kind": "fixed-name-unowned-kill"},
      "reachable_behavior": "Quota scraping uses a unique name and cleanup can kill only the captured opaque ID with its expected random generation.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-3",
      "id": "F-3",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "ad63c84311244b496f9fa242bc302eb887f45fc60c372db0447d0a73559fcf77",
      "cluster": {"spec_version": null, "invariant_id": "owned-session-destruction-only", "subsystem": "session-launch", "violation_kind": "name-only-destruction"},
      "reachable_behavior": "Explicit sandbox replacement refuses tokenless sessions and requires an atomic ID and generation match before killing.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-4",
      "id": "F-4",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "f22309373f31cd8d3c8ed97a7f415b241eaab564c3f4bacf498e8794d5b596d4",
      "cluster": {"spec_version": null, "invariant_id": "process-identity-before-signal", "subsystem": "process-hygiene", "violation_kind": "pid-reuse-toctou"},
      "reachable_behavior": "Explicit process cleanup skips a PID whose captured creation time is missing or differs immediately before termination.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-5",
      "id": "F-5",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e4a25502b18ea45ece85c4fb773b8d5e16bdbb3e9eb96dec0cebaa3170135af0",
      "cluster": {"spec_version": null, "invariant_id": "process-identity-before-signal", "subsystem": "utility-stop", "violation_kind": "pid-reuse-toctou"},
      "reachable_behavior": "Tunnel and browser stop paths signal only after every persisted process identity field matches the current process.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:F-6",
      "id": "F-6",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "e6ab77355cdb64e46d0f748d17ab8d99c2c1ecc649066141b2309b5190bc3be7",
      "cluster": {"spec_version": "plan-legacy", "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "process-hygiene-plan", "violation_kind": "stale-destructive-contract"},
      "reachable_behavior": "The approved plan now marks score-triggered launch and cron termination superseded and requires explicit identity-checked cleanup.",
      "disposition": "fixed",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    },
    {
      "finding_key": "ai-cli-1wzz:N-1",
      "id": "N-1",
      "submitted_severity": "major",
      "severity": "major",
      "fingerprint": "b1e509f3fc6c9192b318ca3aba6bc43b6063f3bf70d7dae26da5326da0666a6f",
      "cluster": {"spec_version": null, "invariant_id": "implicit-lifecycle-no-cross-session-signal", "subsystem": "dead-pane-relaunch", "violation_kind": "authorization-outside-generation-fence"},
      "reachable_behavior": "After dead panes are observed, a concurrent same-name live replacement can be captured as the new identity and killed because pane liveness is absent from the atomic predicate.",
      "disposition": "open-blocking",
      "member_digest": "blob:c454026f066f10c7e3b3537c2acd9ab8bab44861"
    }
  ],
  "new_by_severity": {
    "critical": 0,
    "major": 1,
    "minor": 0
  },
  "major_nonblocking_justifications": {}
}
```

## Round 3 — Verification Pass (append-only)

**Round 3 auditor:** Codex audit (GPT-5; exact deployment ID not exposed, effort: medium)

**Round 3 date:** 2026-09-10

**Round 3 scope:** Verify the complete Round 2 open backlog (JA-1, DV-1, and N-1) against
commit `518f222` or later, including source fidelity, mutation-test quality, and actual focused
test execution. Re-check the class invariant only where the named Round 3 files contradict a prior
PASS. No source, test, bug-record, or other repository file was edited.

### R3 Summary

N-1's code repair is **PASS at source level**: the relaunch path captures a dead-pane fingerprint
bound to the old opaque session ID and then compares that exact fingerprint inside tmux's atomic
`if-shell` before killing. The new regression is a genuine mutation test, not a tautology: its
post-`list-panes` hook destroys the observed dead session, creates a live same-name replacement,
and asserts both the replacement ID and its generation marker survive.

The required runtime verification is **BLOCKED**, not passed. The exact `uv run pytest ... -v`
command failed before collection because uv could not initialize its cache. A direct
`.venv/bin/pytest` fallback also failed before collection because the sandbox exposes no writable
temporary directory. A pre-fix RED run was consequently unavailable without violating the
single-write-target constraint. N-1 and DV-1 are PARTIAL rather than fully verified.

JA-1 is **FAIL**. While checking the whole-class claim in the named `main.py`, Round 3 found N-2:
after creating a new tmux session, any option-configuration failure reaches an unfenced
`kill-session -t session_id`. A concurrent process can replace that same name between the failed
configuration command and the cleanup call, causing this launch to kill the replacement. This
predates commit `518f222`, but it directly contradicts the existing class-wide PASS assertion and
is the same reachable destructive lifecycle failure class.

### R3.1 Open MUST-fix backlog verification

| ID | Verdict | Evidence | Verification note |
|----|---------|----------|-------------------|
| JA-1 | **FAIL (CONFIRMED)** | N-1's dead-pane path is fenced, but new-session configuration failure still calls name-only `tmux kill-session -t session_id` (`src/ai_cli/main.py:3284-3295`). The audit previously asserts this “cleans only the tmux session just created” (`docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md:1277-1278`), yet no captured opaque ID or generation is checked before the kill. See N-2. | Source ordering and `git blame` reproduced the authority gap; dynamic pytest execution was unavailable. |
| DV-1 | **PARTIAL (CONFIRMED source; execution BLOCKED)** | The requested mutation test now exists at `tests/test_session_launch_integration.py:375-418`; the fixture runs its one-shot replacement hook after the real `list-panes` observation (`tests/test_session_launch_integration.py:143-158`). It does not cover the newly found configuration-cleanup race, and neither the mutation test nor the ordinary dead-pane test could collect in this sandbox. | Genuine regression source is present; no independent GREEN/RED runtime result was obtained. |
| N-1 | **PARTIAL (source PASS; execution BLOCKED)** | `_do_session_launch()` selects the prior candidate, captures its full dead-pane fingerprint, and calls `fence_and_kill(candidate.session_id, fingerprint)` (`src/ai_cli/main.py:3223-3238`). The adapter fingerprint contains session ID, generation, attachment, window/pane IDs, PIDs, and `pane_dead` (`src/ai_cli/stale_session_reaper.py:35-48,141-183,526-548`). The exact requested test did not reach collection. | Current relevant files are byte-identical to commit `518f222`; source adaptation is faithful, runtime verification is incomplete. |

### R3.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| — | **N/A (CONFIRMED)** | No prior AD-N exists, and N-2 has a single fail-closed remedy rather than a product-policy choice. |

### R3.4 NEW issues surfaced

#### N-2: Configuration-failure cleanup can kill a concurrent same-name replacement — `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:3262-3295`;
`tests/test_session_launch_integration.py:130-141,183-184`;
`docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md:1277-1278`

**Existing class-wide claim contradicted:**

> “`main.py:3271` cleans only the tmux session just created by the current invocation after its
> configuration fails.”

**Actual state:**

```python
# src/ai_cli/main.py:3289-3295
for tmux_option in tmux_options:
    configured = subprocess.run(tmux_option, capture_output=True, check=False)
    if configured.returncode != 0:
        subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True, check=False)
        Path(_script_path).unlink(missing_ok=True)
        print(f"Error: failed to configure tmux session '{session_id}'", file=sys.stderr)
        sys.exit(1)
```

The current invocation does create a session before this loop, but it retains only the mutable
name. If another actor kills that session and creates a replacement with the same name while an
option command is running, that command can fail and the subsequent cleanup resolves the name to
the replacement. Unlike the sandbox and N-1 paths, there is no captured opaque session ID,
generation token, or atomic comparison between the configuration result and destruction.

`git blame` attributes the kill to commit `2f90ebe9`, so N-2 was not introduced by N-1's commit.
It is reported because the Round 3 mandate explicitly requires verification that JA-1's class
invariant now holds everywhere, and the named source file directly contradicts that claim.

**Why it matters:** A routine launch configuration failure can terminate another live tmux session
that acquired the same name during the cleanup race. This is reachable incorrect destructive
behavior and therefore MAJOR under the binding rubric.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '3262,3295p'
git blame -L 3284,3295 -- src/ai_cli/main.py
rg -n 'configured.returncode|failed to configure tmux session|kill-session.*session_id' \
  src/ai_cli/main.py tests/test_session_launch_integration.py tests/test_cli.py
```

**Recommended fix (follow-up round):** Immediately after successful `new-session`, establish the
managed generation marker and capture the new session's opaque identity. On configuration failure,
kill only through an atomic ID+generation fence. If identity capture or the fence fails, preserve
the current name occupant and exit. Add a mutation integration test that replaces the created
session immediately after a forced option failure and asserts the live replacement survives.

### R3.5 Verification Matrix

| Check | Command | Expected | Actual | Pass? |
|-------|---------|----------|--------|-------|
| N-1 production adaptation | `nl -ba src/ai_cli/main.py \| sed -n '3216,3238p'` | Full fingerprint capture and atomic exact-ID fence | Candidate selected by name, fingerprint captured, exact `candidate.session_id` fenced at lines 3223-3238 | ✅ |
| N-1 helper fidelity | `nl -ba src/ai_cli/stale_session_reaper.py \| sed -n '35,48p;141,183p;526,548p'` | ID, generation, attachment, every pane's ID/PID/dead state compared | Format and validators include all fields; `require_dead=True` at capture and fence | ✅ |
| DV-1 mutation quality | `nl -ba tests/test_session_launch_integration.py \| sed -n '143,158p;375,418p'` | Replacement occurs after observation; replacement identity survives | One-shot hook replaces after real `list-panes`; assertions check opaque ID and generation | ✅ |
| Ordinary dead-pane behavior | `nl -ba tests/test_session_launch_integration.py \| sed -n '315,372p'` | Dead managed session is recreated with a live pane | Test creates a real dead managed pane and asserts the resulting named session has `pane_dead == 0` | ✅ source / runtime blocked |
| Requested runtime test | `uv run pytest tests/test_session_launch_integration.py -k test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v` | One selected test passes | Exit 2 before collection: `Failed to initialize cache ... Operation not permitted` | ❌ BLOCKED |
| Direct pytest fallback | `.venv/bin/pytest ... -v -p no:cacheprovider` | Bypass uv and run the selected test | Failed before collection: `FileNotFoundError: No usable temporary directory found` | ❌ BLOCKED |
| JA-1 / N-2 authority edge | `nl -ba src/ai_cli/main.py \| sed -n '3262,3295p'` | Cleanup destroys only a captured owned identity | Line 3292 destroys by mutable name after a separately observed configuration failure | ❌ |
| N-2 coverage | `rg -n 'configured.returncode|failed to configure tmux session|configuration.*fail' tests/test_session_launch_integration.py tests/test_cli.py` | Mutation test for replacement before failure cleanup | No matching test; integration fixture makes all option commands succeed at lines 183-184 | ❌ |

**Verified: 4/8 checks pass by reproduced source evidence; 2/8 runtime checks are blocked before
collection; 2/8 checks confirm N-2 and its missing regression. No test pass count is claimed.**

### R3 Recommendations

**MUST be fixed before closing AI-CLI-1wzz or claiming the class invariant:**

- N-2: replace the name-only configuration-failure kill with captured owned identity plus an
  atomic fence, and add a same-name replacement mutation regression.
- JA-1 and DV-1: remain open until N-2 is fixed and covered.
- N-1: rerun the named focused test in an environment with writable uv cache and temporary paths;
  also run the ordinary dead-pane recreate test. If feasible, repeat the requested pre-fix RED
  mutation check without modifying the audited worktree.

**SHOULD be corrected in the next audit update:**

- Supersede the prior Already-Correct assertion that configuration cleanup necessarily kills only
  the session created by the current invocation; N-2 disproves it.

**Can be folded into a follow-up:**

- None. N-2 is a destructive lifecycle-boundary defect.

**Closure verdict:** `AI-CLI-1wzz` is **not safe to close**. N-1's source fix is credible, but its
required runtime verification is incomplete, and the class invariant remains false at N-2.

## Round 4 — Verification Pass (append-only)

**Round 4 auditor:** Codex `gpt-5.6-sol`, `audit` role (effort: medium)

**Round 4 date:** 2026-09-10

**Round 4 scope:** Verify the complete open backlog (JA-1, DV-1, N-1, and N-2) against
commit `0663ac0` or later, execute both named mutation regressions, and sweep every destructive
call in the requested source files plus directly related lifecycle modules. No source, test,
bug-record, receipt, or other repository file was edited.

### R4 Summary

N-1 and N-2 are **PARTIAL**. Both production changes are present and the corresponding tests are
genuine post-observation replacement mutations, but neither required test executed. The exact
commands both exited 2 before collection because uv could not write its cache. No-cache retries
also exited 2 before collection because the sandbox denied its system temporary directory. The
dispatch statement that this worker had “a normal writable environment” is stale relative to the
actual permission profile and command output.

JA-1 and DV-1 **FAIL**. The expanded sweep found three additional MAJOR ownership gaps. First,
both new-session creators establish their generation marker through the mutable session name only
after `new-session` returns, so a replacement in that interval can be marked, captured, and later
killed as if this invocation created it (N-3). Second, the persistent supervisor still ends a tmux
session using only its baked name; a rename followed by reuse makes its clean exit kill the new
name occupant (N-4). Third, abandoned-session reclamation validates a recorded process start time,
then separately calls an API that signals a bare PID/process group without receiving or
revalidating that identity (N-5). No test covers any of these interleavings.

The current relevant source, tests, and bug record are byte-identical to commit `0663ac0`; current
`HEAD` is `640cf66`, whose only later change in this worktree is the Round 4 prompt scaffold.

### R4.1 Open MUST-fix backlog verification

| ID | Verdict | Evidence | Verification note |
|----|---------|----------|-------------------|
| JA-1 | **FAIL (CONFIRMED)** | Although the N-1 and N-2 terminal fences are present, ownership is still not proven at N-3 (`src/ai_cli/main.py:3263-3295`; `src/ai_cli/quota.py:493-513`), supervisor teardown remains name-only at N-4 (`src/ai_cli/session_script.py:165,350`), and abandoned-process reclamation separates identity validation from bare-PID signalling at N-5 (`src/ai_cli/main.py:483-497`; `src/ai_cli/process_probe.py:284-319,398-412`). | Reproduced by the source-ordering and full-tree destructive-call scans in R4.5. |
| DV-1 | **FAIL (CONFIRMED coverage gap; execution BLOCKED)** | The two requested tests cover replacement after dead-pane observation (`tests/test_session_launch_integration.py:400-443`) and after configuration failure is observed (`tests/test_session_launch_integration.py:446-490`). They do not mutate the creation-to-marker interval, rename/reuse a running supervisor's session, or recycle identity between process validation and signalling; repository search found no such tests. | Both required commands and both no-cache retries exited 2 before collection, so no runtime PASS is claimed. |
| N-1 | **PARTIAL (source PASS; execution BLOCKED)** | The relaunch path captures an all-dead full fingerprint and passes the old opaque ID to `fence_and_kill()` (`src/ai_cli/main.py:3224-3238`); the atomic predicate includes ID, generation, attachment, windows, pane IDs/PIDs, and `pane_dead` (`src/ai_cli/stale_session_reaper.py:35-48,141-183`). The test replaces the session after the real `list-panes` observation and asserts the replacement ID and generation survive (`tests/test_session_launch_integration.py:400-443`). | Exact requested pytest: exit 2, `Failed to initialize cache ... Operation not permitted`; no-cache retry: exit 2 on a denied system temp path. |
| N-2 | **PARTIAL (cleanup fence PASS; ownership bootstrap gap CONFIRMED; execution BLOCKED)** | Commit `0663ac0` added a random token, `set-option`, capture with `expected_generation`, and `kill_owned_tmux_session(identity)` (`src/ai_cli/main.py:3285-3308`). Its test genuinely replaces the session after configuration failure is observed (`tests/test_session_launch_integration.py:108-118,180-190,446-490`). However, `set-option` still targets the mutable name after `new-session` returns, before opaque identity is captured; N-3 shows why that does not prove which session was created. | Exact requested pytest and no-cache retry both exited 2 before collection. |

### R4.2 Destructive call-site sweep

| Site | Destructive target | Fence verdict |
|---|---|---|
| `src/ai_cli/main.py:3201-3216` | Sandbox replacement | **PASS for the previously scoped collision:** captures ID+generation and kills through the single-command fence (`src/ai_cli/tmux_ownership.py:59-88`). |
| `src/ai_cli/main.py:3224-3239` | Dead-pane relaunch | **PASS at source:** captured full dead-pane fingerprint is compared in the same tmux `if-shell` command that kills the exact opaque ID. Runtime remains blocked under N-1. |
| `src/ai_cli/main.py:3263-3308` | New-session configuration cleanup | **FAIL — N-3:** the final kill is fenced, but the purported ownership token is first written through a mutable name after creation returns. |
| `src/ai_cli/main.py:483-497`; `src/ai_cli/process_probe.py:284-319,398-412` | Abandoned registered process/tree | **FAIL — N-5:** recorded start identity is checked outside `end_process(pid)`; the destructive API receives only a PID. |
| `src/ai_cli/session.py` | — | **PASS:** no production `.terminate()`, `.kill()`, `os.kill*`, or `kill-session` call exists. |
| `src/ai_cli/quota.py:493-513,655-658` | Hidden quota tmux session | **FAIL — N-3:** cleanup uses the atomic helper, but creation and name-targeted token assignment precede identity capture. |
| `src/ai_cli/tunnel.py:184-187,391-399` | Newly opened tunnel/browser | **PASS:** each failure cleanup uses the direct unreaped `Popen` child handle created by that invocation. |
| `src/ai_cli/tunnel.py:215-226,429-441` | Registered tunnel/browser | **PASS:** persisted PID, creation time, executable, command, and port are revalidated on a `psutil.Process` object; `terminate()` performs psutil's own PID-reuse guard. |
| `src/ai_cli/process_hygiene.py:63-68,448-459,640-648` | Explicitly confirmed scored process | **PASS:** creation time is captured at inventory, matched on the same `psutil.Process` object, and psutil rechecks PID reuse when signalling. The implicit cron branch is non-destructive (`src/ai_cli/process_hygiene.py:857-865`). |
| `src/ai_cli/tmux_ownership.py:26-88` | Captured tmux ID+generation | **PASS as a terminal fence:** validated opaque ID and generation are compared in the same `tmux if-shell` command as the kill. It cannot establish that its input identity belongs to the creator; N-3 is a caller-side defect. |
| `src/ai_cli/stale_session_reaper.py:108-183,333-383` | Stale managed tmux session | **PASS:** token, two process snapshots, heartbeat/lease checks, and exact full fingerprint all fail closed before the atomic exact-ID kill. |
| `src/ai_cli/session_script.py:165,350` | Supervisor clean-exit tmux session | **FAIL — N-4:** raw `tmux kill-session -t "$tmux_session"` uses only the baked mutable name. This additional full-tree hit contradicts the earlier topology-only clearance. |
| `src/ai_cli/session_script.py:228-230,278-287,327-335,363-419`; `src/ai_cli/transport.py:266-300,355-360`; `src/ai_cli/messaging.py:305-314` | Direct children | **PASS:** targets are unreaped child PIDs or direct `Popen` handles owned by the current supervisor/client. |

The requested four-file `subprocess.run` sweep found no remaining raw `kill-session` argv in
`main.py`, `session.py`, `quota.py`, or `tunnel.py`; the main/quota kills are indirect calls to
`kill_owned_tmux_session`. That syntactic result is not equivalent to class safety because N-3
invalidates how those identities can be acquired, and the expanded source-tree scan found N-4.

### R4.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| — | **N/A (CONFIRMED)** | No prior AD-N exists. N-3 through N-5 each have a fail-closed ownership-preserving remedy and do not require a product-policy choice. |

### R4.4 NEW issues surfaced

#### N-3: Post-creation name targeting can adopt and later kill a replacement — `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:3263-3308`; `src/ai_cli/quota.py:487-513,655-658`;
`tests/test_session_launch_integration.py:142-153,180-190,446-490`

**Evidence:**

```python
# src/ai_cli/main.py:3263-3295
result = subprocess.run(
    ["tmux", "new-session", "-d", "-s", session_id, ...],
    ...,
)
...
marked = subprocess.run(
    ["tmux", "set-option", "-t", session_id, "@ai_cli_session_generation", generation],
    ...,
)
...
identity = _tmux_ownership.capture_tmux_session_identity(session_id, expected_generation=generation)
```

The new session creator returns before the token is assigned, and both token assignment and
identity capture resolve the mutable name. If the created session is removed and a same-name
replacement appears in that interval, this invocation writes its token onto the replacement,
captures the replacement's opaque ID, and can later kill it at line 3308. Quota scraping repeats
the same sequence at `src/ai_cli/quota.py:493-513,655-658`; its random name makes accidental
collision unlikely but does not constitute immutable ownership proof.

The normal-session child concurrently mints a second generation and writes it through the same
name (`src/ai_cli/session_script.py:174,215`). Even without a replacement, that independent writer
can race the creator's expected-generation capture or invalidate the identity before
configuration cleanup, leaving the failed launch alive. The creator and supervisor therefore do
not share one authoritative session generation.

The N-2 test injects replacement only from the failed configuration result's `returncode`
property, after marker assignment and capture (`tests/test_session_launch_integration.py:108-118,
180-190,446-490`). It cannot fail for this earlier interleaving.

**Why it matters:** The repair's generation fence can be made to authenticate a sibling session
because the credential itself is installed through the mutable locator it is supposed to replace.
A later routine configuration failure or quota cleanup can therefore kill that sibling.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '3263,3309p'
nl -ba src/ai_cli/quota.py | sed -n '487,513p;655,658p'
nl -ba src/ai_cli/session_script.py | sed -n '165,227p'
nl -ba tests/test_session_launch_integration.py | sed -n '108,118p;142,190p;446,490p'
rg -n '_after_new_session|after_new_session|creation.*replacement' tests src/ai_cli
```

**Verification note:** CONFIRMED from current source ordering and the mutation seam's placement;
the concurrency interleaving could not be executed because this worker cannot create a test temp
directory.

**Recommended fix:** Request the new session's opaque ID directly from the creating tmux command
(for example, `new-session -P -F '#{session_id}'`), validate it, and target that ID—not the name—for
marker assignment and all subsequent capture/configuration. Apply the same contract to quota
creation. Add a mutation immediately after `new-session` returns that replaces the name; marker
assignment must fail closed and the replacement must survive.

#### N-4: Supervisor clean exit kills a mutable session name — `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/session_script.py:165,215,350,355`; R1 destructive call-site inventory

**Evidence:**

```bash
# src/ai_cli/session_script.py:165,350
tmux_session=<baked session name>
...
tmux kill-session -t "$tmux_session" 2>/dev/null || true
```

The Round 1 inventory cleared this call because the supervisor starts inside the named session.
That topology does not keep a mutable name bound to the same session. If the original session is
renamed while its supervisor remains live and another session takes the old name, the original
supervisor's clean-exit path resolves and kills the new occupant. The script already creates a
generation token at line 174 and sets it at line 215, but the destructive line does not check it
or an opaque ID.

**Why it matters:** A normal agent exit can terminate a sibling tmux session after a reachable
rename/name-reuse sequence—the exact lifecycle safety class this audit is meant to close.

**Verification command:**

```bash
nl -ba src/ai_cli/session_script.py | sed -n '165,227p;337,355p'
rg -n 'rename-session|rename_session' tests src/ai_cli
git blame -L 350,350 -- src/ai_cli/session_script.py
```

**Verification note:** CONFIRMED that the production kill is name-only and no rename/reuse
regression exists. The live tmux interleaving was not executed because the sandbox denies socket
temporary-directory creation.

**Recommended fix:** Give the supervisor one immutable identity established by the creator and
perform clean-exit teardown through an atomic ID+generation fence. Do not mint a second token in
the child through the session name. Add a real-tmux regression that renames the original session,
creates a live replacement under the old name, exits the original supervisor, and proves the
replacement survives.

#### N-5: Abandoned-session reclamation drops process identity before signalling — `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:467-497`; `src/ai_cli/process_probe.py:159-180,284-319,398-412`

**Evidence:**

```python
# src/ai_cli/main.py:488,497
identified = probe.start_time_match(pid, record.get("procStart")) is StartTimeMatch.MATCH
...
if probe.end_process(pid):
```

`end_process` accepts only a PID. On Linux it rediscovers a process group and sends TERM, CONT,
and KILL by numeric PID/group (`src/ai_cli/process_probe.py:303-319`); on other platforms it
reopens the tree from the PID (`src/ai_cli/process_probe.py:398-412`). The recorded start identity
validated at line 488 is not passed into either implementation or revalidated inside it. A target
exit and PID reuse between those calls therefore redirects the destructive action to the new PID
holder and potentially its process group.

**Why it matters:** A routine session launch can signal an unrelated process tree in the exact
PID-reuse race that the surrounding comments claim to prevent. Because group escalation can kill
multiple processes, the reachable impact is MAJOR.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '467,500p'
nl -ba src/ai_cli/process_probe.py | sed -n '159,180p;284,319p;398,412p'
rg -n -C 5 'start_time_match\(|end_process\(' src/ai_cli tests
```

**Verification note:** CONFIRMED API/ordering defect and absence of an identity-carrying
termination contract. The narrow PID-reuse interleaving is not dynamically forced in this round.

**Recommended fix:** Change destructive process APIs to accept the previously captured immutable
`ProcessIdentity`, revalidate it inside the terminating operation immediately before every signal,
and fail closed on mismatch. Use a PID-bound OS handle where available; otherwise keep the
identity check and signal in the smallest platform-supported critical sequence. Add a deterministic
mutation seam that swaps the observed identity before termination and asserts no signal call.

### R4.5 Verification Matrix

| Check | Command | Expected | Actual | Pass? |
|-------|---------|----------|--------|-------|
| Target equivalence | `git diff --quiet 0663ac0..HEAD -- <R4 relevant paths>` | No relevant implementation/test drift | Exit 0; relevant paths byte-identical | ✅ |
| N-1 production fence | `nl -ba src/ai_cli/main.py \| sed -n '3224,3239p'` plus adapter read | Full dead fingerprint and exact-ID atomic kill | Capture and fence include generation, attachment, window/pane IDs, PIDs, and dead state | ✅ source |
| N-1 requested runtime | Exact requested `uv run pytest ...dead_session_replaced... -v` | One selected test passes | Exit 2 before collection: uv cache path `Operation not permitted` | ❌ BLOCKED |
| N-2 production cleanup | `nl -ba src/ai_cli/main.py \| sed -n '3285,3309p'` | Captured identity used by cleanup | Token, capture, and helper call present; ownership bootstrap still name-targeted (N-3) | ⚠️ PARTIAL |
| N-2 requested runtime | Exact requested `uv run pytest ...new_session_replaced... -v` | One selected test passes | Exit 2 before collection: uv cache path `Operation not permitted` | ❌ BLOCKED |
| No-cache runtime retries | `UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider ...` | Bypass cache and execute each test | Both exit 2 before collection on denied system temp paths | ❌ BLOCKED |
| Four-file destructive scan | `rg -n 'kill-session|\\.terminate\\(|\\.kill\\(' main.py session.py quota.py tunnel.py` plus all `subprocess.run` contexts | Every destructive call terminally fenced | No raw four-file `kill-session`; tunnel handles pass; main/quota identity acquisition fails N-3 | ❌ |
| Expanded full-tree scan | `git grep -n -E 'kill-session|\\.terminate\\(|\\.kill\\(|os\\.kill' HEAD -- src` | No additional unfenced lifecycle call | Found raw supervisor name kill at `session_script.py:350` and bare-PID reclamation behind `main.py:497` | ❌ |
| N-3 coverage | `rg -n '_after_new_session|after_new_session|creation.*replacement' tests src/ai_cli` | Mutation before generation assignment | No matches | ❌ |
| N-4/N-5 coverage | Rename and identity-carrying end-process searches | Rename/reuse and pre-signal identity mutations | No rename/reuse test; `end_process` accepts only PID | ❌ |

**Verified: 2/10 matrix checks pass completely at source level, 1/10 is partial, 4/10 reproduce
blocking source/coverage gaps, and 3/10 runtime checks are blocked before collection. No pytest
PASS count is claimed.**

### R4 Recommendations

**MUST be fixed before closing AI-CLI-1wzz or claiming the class invariant:**

- N-3: bind new-session ownership to the opaque ID returned by creation before writing a marker;
  never acquire ownership by marking whichever session currently holds a mutable name.
- N-4: replace supervisor clean-exit's raw name-only `kill-session` with an atomic opaque-ID and
  generation fence, with a rename/name-reuse survival regression.
- N-5: carry captured process identity into the destructive API and revalidate inside the signal
  operation; add a PID-reuse mutation regression.
- JA-1 and DV-1 remain FAIL until N-3 through N-5 are fixed and the class-level regression boundary
  covers future destructive call edges.
- N-1 and N-2 remain PARTIAL until both exact named pytest commands execute successfully in an
  actually writable test environment. Also add N-3's earlier mutation to the N-2 test family.

**SHOULD be corrected in the next audit update:**

- Supersede the prior Already-Correct assertions that a baked tmux name is sufficient topology
  proof, that `procStart` is verified “before signalling,” and that configuration cleanup can only
  target the just-created session; N-3 through N-5 disprove those statements.
- Replace the bug record's “every identified path” and class-wide regression claims
  (`docs/bugs/cross-session-mosh-termination.md:68-73,90-93`) with pending language until a clean
  re-audit executes the tests and finds no unfenced call site.

**Can be folded into a follow-up:**

- None. Every new issue is in the destructive lifecycle ownership boundary.

**Closure verdict:** `AI-CLI-1wzz` is **not safe to close**. The required runtime tests did not
execute, JA-1 and DV-1 fail, and the eighth-mechanism sweep found three additional MAJOR gaps.

## Decisions Requiring Team Input

None. Each finding has a fail-closed resolution that does not require choosing among materially
different product policies. No AD-N entry is warranted.

## Outstanding Issues to Fix

| ID | Severity | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | FIXED | Remove process authority from implicit launch cleanup and generation-fence quota cleanup | IC-1, F-1, F-2 | — | Verified in Round 2 |
| I-02 | FIXED | Require owned tmux identity for destructive sandbox recreation | F-3 | — | Verified in Round 2 |
| I-03 | FIXED | Add process identity to explicit PID-based cleaners/stoppers | F-4, F-5 | — | Verified in Round 2 |
| I-04 | FIXED | Supersede the stale auto-kill plan and correct the bug record's scope claim | IC-1, F-6 | — | Verified in Round 2 |
| I-05 | PARTIAL | Dead-pane fence and mutation source landed; execute the blocked regression in a writable environment | JA-1, DV-1, N-1 | Team | Next verification |
| I-06 | MAJOR | Bind creation, generation assignment, and identity capture to the creator's opaque tmux ID; cover pre-marker replacement | JA-1, DV-1, N-2, N-3 | Team | Next fix round |
| I-07 | MAJOR | Fence supervisor clean-exit teardown by opaque ID+generation and cover rename/name reuse | JA-1, DV-1, N-4 | Team | Next fix round |
| I-08 | MAJOR | Carry process identity into abandoned-session termination and cover PID reuse before signal | JA-1, DV-1, N-5 | Team | Next fix round |

## Already-Correct Items

- ✅ Commit `744499b4` removes the sole `auto_clean_orphans(local, ...)` call from the `cron`
  branch; `src/ai_cli/process_hygiene.py:836-844` performs only transport-file cleanup and cache
  refresh.
- ✅ The real-process regression is meaningful for that narrow branch: it places an actual child
  at the exact score threshold and asserts the child remains alive (`tests/test_process_hygiene.py:840-865`).
- ✅ The reproduced incident scoring mechanism is accurate: no local client, age over 24 hours,
  and no inferred matching session yields score 80 (`src/ai_cli/process_hygiene.py:253-290`).
- ✅ `session_script.py:350` uses the baked supervisor `tmux_session`; child transcript resolution
  does not mutate that supervisor variable.
- ✅ The stale-session reaper is fail-closed: it requires a managed generation marker, same-boot
  stale heartbeat, exact process identities across snapshots, an exclusive generation lease, and
  atomic tmux fingerprint comparison before its exact-ID kill.
- ✅ The abandoned-transcript reclamation path explicitly recognizes PID-reuse risk and verifies
  `procStart` before signalling (`src/ai_cli/main.py:464-495`).
- ✅ `main.py:3271` cleans only the tmux session just created by the current invocation after its
  configuration fails; transport and messaging terminations use direct child `Popen` handles.
- ✅ No source `pkill` call exists at the target commit.
- ✅ Round 2 verified quota cleanup's destructive action is inside one `tmux if-shell` call and
  rechecks the captured opaque ID plus expected generation (`src/ai_cli/tmux_ownership.py:64-84`).
- ✅ Round 2 verified the sandbox-specific replacement path refuses tokenless collisions and a
  changed generation (`src/ai_cli/main.py:3198-3214`).
- ✅ Round 2 verified explicit process hygiene and tunnel/CDP stops revalidate durable process
  identity before signalling (`src/ai_cli/process_hygiene.py:640-651`; `src/ai_cli/tunnel.py:75-125`).

## Anti-Patterns to Watch For

- Treating a process score, session name, PID, or stale state file as ownership evidence.
- Auditing only the call removed by the incident fix instead of every authority edge reachable
  from the lifecycle that triggered it.
- Extrapolating a strong unit-level negative control into a system-wide invariant.
- Using a name regex as the managed-session classifier when the session builder allows a wider
  grammar.
- Pre-killing a fixed tmux name as cleanup instead of allocating a unique, identity-bound resource.
- Calling a pytest command "passed" when sandbox policy prevented collection.
- Leaving an approved plan with acceptance criteria that reintroduce a shipped safety defect.
- Treating a generation marker as ownership proof when the marker itself was assigned through a
  mutable name after creation returned.
- Treating “the process/session started here” as a lifetime identity without carrying its opaque
  identity into the destructive operation.

## Sign-Off Checklist

- [x] Canonical STUB/TEMPLATE read before authoring; repository-local absence recorded
- [x] Target commit and current relevant-path equivalence verified
- [x] Entire requested signal call-site inventory re-derived from source
- [x] Verification Matrix run on 9 findings with actual output
- [x] Already-Correct Items populated with code evidence
- [x] No inline source/doc/config fixes applied
- [x] All CRITICAL findings fixed or explicitly accepted — none found
- [ ] All MAJOR findings fixed or explicitly accepted — JA-1, DV-1, and N-1 through N-5 open
- [x] At least one append-only verification round confirms the fixes — Round 2 completed; 7/9 PASS
- [ ] Trusted receipt asserts stabilization/promotion
- [ ] User reviewed and approved sign-off

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-09-10 | Round 1 | Independent audit complete: 9 MAJOR findings, 9/9 reproduced; narrow cron fix verified, class-wide invariant failed; no source or target-artifact edits; promotion blocked. |
| 2026-09-10 | Fix round | Codex (gpt-5.6-sol, `implement-network`, high effort) implemented remediation for all 9 findings, independently reviewed and re-verified by the orchestrating session, merged as ai-cli-utils PR #129 (commit `067a358b`). Bug record status downgraded `fix-verified` -> `fix-implemented` pending the Round 2 re-audit below; this row is orchestrator-recorded, not a trusted receipt assertion -- promotion/stabilization still requires the driver's own Round 2 verification. |
| 2026-09-10 | Round 2 | Codex (GPT-5, exact deployment ID not exposed; `audit`, medium effort): 7 PASS, 2 PARTIAL, 1 new MAJOR (N-1); dead-pane relaunch authorization is outside the atomic generation fence; pytest execution blocked before collection by temporary-directory policy; no target edits. |
| 2026-09-10 | Round 3 | Codex (GPT-5, exact deployment ID not exposed; `audit`, medium effort): N-1 source fix PASS, N-1 runtime verification BLOCKED, DV-1 PARTIAL, JA-1 FAIL; 1 new MAJOR (N-2) in name-only configuration-failure cleanup; not safe to close; no target edits. |
| 2026-09-10 | Round 4 | Codex (`gpt-5.6-sol`, `audit`, medium effort): N-1/N-2 source repairs present but runtime BLOCKED; JA-1/DV-1 FAIL; 3 new MAJOR findings (N-3 post-create ownership adoption, N-4 name-only supervisor teardown, N-5 identity-dropping process termination); not safe to close; audit-doc-only edit. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Audit format and repository policy:**

- `AGENTS.md` supplied with the invocation — repository policy and test requirements.
- Canonical `docs/audits/STUB.md` and `docs/audits/TEMPLATE.md` in the local harness repository —
  read in full; repository-local copies were absent.
- `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md` — complete designated scaffold
  and immutable reviewer prompts.

**Primary and related documents:**

- `docs/bugs/cross-session-mosh-termination.md` — full target at `744499b4`.
- `docs/bugs/remote-session-double-interrupt-exit.md` — full prior lifecycle/clean-exit bug record.
- `docs/plans/process-hygiene-plan.md` — full approved plan surfaced by symbol search.
- `docs/designs/stale-session-reaper.md` — full safety design, decisions, implementation audit,
  open questions, and Approval Log.

**Production source:**

- `src/ai_cli/process_hygiene.py` — full file.
- `src/ai_cli/session_script.py` — full file.
- `src/ai_cli/session.py` — full file.
- `src/ai_cli/stale_session_reaper.py` — full file.
- `src/ai_cli/process_probe.py` — full file.
- `src/ai_cli/process_manager.py` — full file.
- `src/ai_cli/messaging.py` — full file.
- `src/ai_cli/transport.py` — full file.
- `src/ai_cli/tunnel.py` — full file.
- `src/ai_cli/main.py` — all signal call sites, launch ordering, transcript reclamation, session
  creation/recreation, quota dispatch, and tunnel/CDP dispatch contexts.
- `src/ai_cli/quota.py` — all signal call sites, hidden-pane scraper, snapshot selection, and quota
  watcher polling contexts.
- `src/ai_cli/config.py` — quota auto-start defaults and relevant generated configuration.

**Tests:**

- `tests/test_process_hygiene.py` — full file and complete `TestCmdPs` class.
- `tests/test_session.py` — background-spare cleanup fixtures/tests and neighboring name behavior.
- `tests/test_quota.py`, `tests/test_process_manager.py`, and `tests/test_cli.py` — every relevant
  quota scraper/auto-start/signal call-site section surfaced by symbol search.

**History and generated evidence:**

- Commit `744499b4`, its parent, the complete four-file diff, and current relevant-path diff.
- Related kill-authority history including `a223fe5`, `ed17415`, `1532f49`, `4a534ff`, and
  `72721a7`; commit `1532f49`'s remote background-spare mapping change was inspected in detail.
- Round 1 worktree status and commit `c74146ec3ea4`; unrelated existing changes were not modified.

**Round 2 additions:**

- `src/ai_cli/tmux_ownership.py` — full file; validated input grammar and single-call ID/generation
  comparison-and-kill.
- `src/ai_cli/main.py:3160-3292` — sandbox and ordinary dead-pane recreation ordering.
- `src/ai_cli/stale_session_reaper.py:30-185` — established full pane-fingerprint atomic fence.
- `tests/test_stale_session_reaper.py:587-710` — real-tmux identity/generation/respawn tests.
- `tests/test_session_launch_integration.py:300-374` — dead-pane relaunch integration path.
- `tests/test_session.py:283-566`, `tests/test_quota.py:511-625`, `tests/test_cli.py:629-718,2853-2908`,
  `tests/test_process_hygiene.py:555-647,850-890`, and `tests/test_cdp.py:392-455` — claimed
  regression coverage and mocking boundaries.
- Commit `067a358b`, its parent, complete source/test diff, and path equivalence through current
  `HEAD` `05983a8`.

**Round 3 additions:**

- `docs/bugs/cross-session-mosh-termination.md` — full current target; verification claims and
  pending independent-audit status.
- `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md` — full prior Round 1/Round 2
  history and the frozen Round 3 reviewer prompt.
- `src/ai_cli/main.py:3199-3300` — sandbox replacement, N-1 dead-pane fence, session creation,
  configuration, failure cleanup, and attach control flow.
- `src/ai_cli/stale_session_reaper.py:35-183,340-394,513-548` — candidate grammar, complete
  fingerprint format/validation, exact-ID fence, and established evaluator use.
- `tests/test_session_launch_integration.py:110-200,290-418` — real-tmux subprocess adapter,
  ordinary dead-pane recreation, and post-observation replacement mutation.
- `tests/test_stale_session_reaper.py:587-706` — positive fence, tokenless/generation/respawn
  negatives, hostile-token rejection, and exact argv contract.
- `tests/test_cli.py:1070-1187,2000-2053` — session creation/configuration mocks and absence of a
  configuration-cleanup replacement mutation.
- Commit `518f222`, its parent, complete two-file diff, path equivalence through current `HEAD`
  `5e4e80b`, and blame/history for the pre-existing N-2 kill at commit `2f90ebe9`.

**Round 4 additions:**

- `docs/bugs/cross-session-mosh-termination.md` — full current target at digest
  `1e21c3e7144cba2595225461bd314cd2706bb854ef5679c228f8f288ecd524a1`.
- `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md` — complete Rounds 1-3,
  cross-round tables, appendices, and frozen Round 4 prompt.
- `src/ai_cli/main.py:467-500,3160-3316` — abandoned-process termination, sandbox/dead-pane
  replacement, creation, generation assignment, identity capture, configuration, and cleanup.
- `src/ai_cli/session.py` — every `subprocess.run` context and full destructive-pattern scan; no
  production signal call found.
- `src/ai_cli/quota.py:478-661` — complete hidden-session create/configure/capture/cleanup path.
- `src/ai_cli/tunnel.py:22-226,350-444` — process identity capture/revalidation and every terminate.
- `src/ai_cli/process_hygiene.py:50-68,420-459,614-665,833-865` — process identity capture,
  explicit termination, and non-destructive cron path.
- `src/ai_cli/tmux_ownership.py` — full file and exact atomic terminal fence.
- `src/ai_cli/stale_session_reaper.py:1-210,280-505,510-560` — full fingerprint, evaluation,
  two-snapshot identity checks, lease, and atomic kill path.
- `src/ai_cli/session_script.py:143-440` — supervisor identity setup, child ownership, and raw
  clean-exit tmux teardown surfaced by expanded full-tree scan.
- `src/ai_cli/process_probe.py:150-180,250-430` — process identity and PID-only destructive APIs.
- `src/ai_cli/transport.py:250-375` and `src/ai_cli/messaging.py:285-325` — direct-child handle
  terminations surfaced by the expanded scan.
- `tests/test_session_launch_integration.py` — full file, including both required mutation tests
  and their subprocess seam.
- Relevant test symbol searches across `tests/test_stale_session_reaper.py`,
  `tests/test_process_probe.py`, `tests/test_session.py`, `tests/test_quota.py`, and
  `tests/test_cli.py` for ownership, generation, rename/reuse, and PID-reuse coverage.
- Commits `518f222` and `0663ac0`, their source/test diffs, current `HEAD` `640cf66`, and a
  path-scoped equivalence check from `0663ac0` through `HEAD`.

## Appendix: Commands Run

```bash
wc -l <canonical-STUB> <canonical-TEMPLATE>
sed -n '<chunk>' <canonical-STUB-or-TEMPLATE>
git show 744499b4:<path>
git diff 744499b4^ 744499b4 -- <affected-paths>
git diff --quiet 744499b4..HEAD -- <all-relevant-paths>
git log --oneline --all -- <relevant-paths>
git show <related-commit>
git grep -n -E 'kill-session|kill_session|SIGTERM|os\.kill\(|\.terminate\(\)|\.kill\(\)|\bpkill\b' 744499b4 -- 'src/ai_cli/*.py'
git grep -n -E '(^|[^A-Za-z_])kill([[:space:]-]|$)|kill-session|\.terminate\(|\.kill\(|os\.kill\(|os\.killpg\(' 744499b4 -- 'src/ai_cli/*.py'
rg -n '<symbol>' src tests docs
.venv/bin/python -B -c '<real cron-survival subprocess probe>'
.venv/bin/python -B -c '<hyphenated-session launch cleanup probe>'
.venv/bin/python -B -c '<quota fixed-name kill probe>'
.venv/bin/python -B -c '<process-hygiene PID identity probe>'
.venv/bin/python -B -c '<tunnel/CDP PID identity probe>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q \
  tests/test_process_hygiene.py::TestCmdPs::test_given_live_sibling_mosh_server_when_ps_cron_runs_then_process_survives \
  tests/test_process_hygiene.py::TestCmdPs::test_given_ps_cron_when_orphans_found_then_it_does_not_auto_clean
shasum -a 256 <canonical-STUB> <canonical-TEMPLATE>
git show 744499b4:docs/bugs/cross-session-mosh-termination.md | shasum -a 256
markdownlint docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md
<canonical-doc-validator> validate-doc docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md
.venv/bin/python -B -c '<extract and validate the single receipt payload projection>'
# Round 3
git rev-parse HEAD
git show --stat --oneline 518f222
git diff 518f222..HEAD -- src/ai_cli/main.py src/ai_cli/stale_session_reaper.py \
  tests/test_session_launch_integration.py docs/bugs/cross-session-mosh-termination.md
git show 518f222 -- src/ai_cli/main.py tests/test_session_launch_integration.py
nl -ba src/ai_cli/main.py | sed -n '3199,3300p'
nl -ba src/ai_cli/stale_session_reaper.py | sed -n '35,183p;340,394p;513,548p'
nl -ba tests/test_session_launch_integration.py | sed -n '110,200p;290,418p'
nl -ba tests/test_stale_session_reaper.py | sed -n '587,706p'
rg -n 'kill-session|fence_and_kill|capture_fingerprint' src/ai_cli/main.py \
  src/ai_cli/stale_session_reaper.py tests/test_session_launch_integration.py tests/test_cli.py
git blame -L 3284,3295 -- src/ai_cli/main.py
uv run pytest tests/test_session_launch_integration.py -k \
  test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v
.venv/bin/pytest tests/test_session_launch_integration.py -k \
  test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives \
  -v -p no:cacheprovider
```

The pytest command failed before collection because no permitted temporary directory exists. The
five read-only Python probes completed; their actual outputs are recorded in the R1 Verification
Matrix.

Round 2 additionally ran:

```bash
git diff --stat 067a358b..HEAD -- <Round-2-member-paths>
git diff 067a358b^..067a358b -- <source-and-test-paths>
rg -n -g '*.py' 'kill-session|kill_session|SIGTERM|\.terminate\(|\.kill\(|os\.kill\(|os\.killpg\(|pkill' src/ai_cli tests
nl -ba <Round-2-source-or-test> | sed -n '<evidence-ranges>'
git diff 067a358b^..067a358b -- tests | rg '^\+\s*(async\s+)?def test_'
.venv/bin/python -B -c 'import inspect, psutil; print(inspect.getsource(psutil.Process.terminate)); print(inspect.getsource(psutil.Process._send_signal))'
.venv/bin/pytest -q tests/test_stale_session_reaper.py tests/test_session.py tests/test_quota.py \
  tests/test_cli.py tests/test_process_hygiene.py tests/test_cdp.py tests/test_session_launch_integration.py
.venv/bin/pytest -s -p no:cacheprovider -q <three-focused-quota-ownership-tests>
```

Both Round 2 pytest attempts failed before collection with `FileNotFoundError: No usable temporary
directory`; no test result is reported as PASS from those commands.

Round 4 additionally ran:

```bash
git status --short
git rev-parse --short HEAD
git log --oneline -12
git show --stat --oneline 518f222
git show --stat --oneline 0663ac0
git show --format=fuller --no-ext-diff 0663ac0 -- \
  src/ai_cli/main.py tests/test_session_launch_integration.py
git diff --quiet 0663ac0..HEAD -- <Round-4-relevant-paths>
shasum -a 256 docs/bugs/cross-session-mosh-termination.md <relevant-source-files>
nl -ba docs/bugs/cross-session-mosh-termination.md
nl -ba src/ai_cli/main.py | sed -n '420,520p;3160,3335p'
nl -ba src/ai_cli/quota.py | sed -n '470,670p'
nl -ba src/ai_cli/tmux_ownership.py
nl -ba src/ai_cli/stale_session_reaper.py | sed -n '1,210p;280,505p;510,560p'
nl -ba src/ai_cli/process_hygiene.py | sed -n '45,155p;420,475p;595,665p;820,875p'
nl -ba src/ai_cli/tunnel.py | sed -n '1,250p;350,460p'
nl -ba src/ai_cli/session_script.py | sed -n '143,440p'
nl -ba src/ai_cli/process_probe.py | sed -n '150,180p;250,430p'
nl -ba tests/test_session_launch_integration.py
rg -n -C 5 -g '*.py' \
  'kill-session|kill_session|\.terminate\(|\.kill\(|os\.kill\(|os\.killpg\(' \
  src/ai_cli/main.py src/ai_cli/session.py src/ai_cli/quota.py src/ai_cli/tunnel.py \
  src/ai_cli/process_hygiene.py src/ai_cli/tmux_ownership.py src/ai_cli/stale_session_reaper.py
git grep -n -E \
  'kill-session|kill-server|\.terminate\(|\.kill\(|os\.kill\(|os\.killpg\(|(^|[^[:alnum:]_])pkill([^[:alnum:]_]|$)' \
  HEAD -- src ':!tests'
rg -n -C 5 'start_time_match\(|end_process\(' src/ai_cli tests
rg -n 'rename-session|_after_new_session|after_new_session|creation.*replacement' tests src/ai_cli
git blame -L 340,355 -- src/ai_cli/session_script.py
uv run pytest tests/test_session_launch_integration.py -k \
  test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v
uv run pytest tests/test_session_launch_integration.py -k \
  test_given_new_session_replaced_after_configuration_failure_when_cleanup_runs_then_replacement_survives -v
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider \
  tests/test_session_launch_integration.py -k \
  test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider \
  tests/test_session_launch_integration.py -k \
  test_given_new_session_replaced_after_configuration_failure_when_cleanup_runs_then_replacement_survives -v
```

Both exact Round 4 pytest commands exited 2 before collection because uv could not write its cache.
Both no-cache retries also exited 2 before collection because the sandbox denied creation under
the system temporary directory. No runtime PASS is reported.

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** Codex (gpt-5.6-sol, `audit` role, effort: high)

**Date:** 2026-09-10

```text
You are a principal staff engineer specializing in developer-experience tooling, terminal
multiplexer/session-lifecycle systems (tmux, mosh), and reliability engineering. You have shipped
production systems in this domain and you know the gap between what looks rigorous on paper and
what actually holds up. You call out that gap directly. When you cannot verify a claim, you say so
explicitly rather than waving past it. Your judgment is the product, not a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous.

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit docs/bugs/cross-session-mosh-termination.md at target commit 744499b4 (ai-cli-utils main)
against the following scope on these validation dimensions:

SCOPE: this is an already-shipped, already-merged bug fix (PR #128, commit 744499b4). The fix
removed the `auto_clean_orphans()` call from the `ai ps cron` session-start hook in
src/ai_cli/process_hygiene.py, so that path no longer has authority to SIGTERM any process. Kill
authority remains reachable only through the explicit, user-confirmed `ai ps clean` path. This is
the 6th distinct occurrence of a recurring bug class where one CC session's lifecycle
(launch/restart/exit) destructively kills a sibling session's mosh-server/tmux process; Sergei's
explicit framing is that this class must be structurally eliminated, not narrowly patched again.
Audit specifically:

  1. Internal Consistency (IC-N): does the bug doc's causal analysis, hypothesis ledger, and fix
     description contradict itself or the actual diff?
  2. Spec / AC Compliance (JA-N): does the fix actually satisfy Sergei's explicit ask -- eliminate
     the WHOLE CLASS of cross-session kill bugs, not just today's specific `-R`-reconnect trigger?
     Specifically verify: does the fix structurally eliminate process-kill authority from EVERY
     implicit session-lifecycle path (session start, restart, exit), not just the `ai ps cron`
     mechanism this incident happened to hit?
  3. Domain Validity (DV-N): is "remove the kill call from the auto-triggered path entirely,
     restrict kill authority to an explicit user-confirmed command" a defensible, complete fix for
     this class of bug given tmux/mosh session-lifecycle semantics? Does the regression test
     (tests/test_process_hygiene.py::test_given_live_sibling_mosh_server_when_ps_cron_runs_then_process_survives)
     genuinely prove the guard -- it spawns a real subprocess and asserts survival -- or is there a
     gap that would let it pass while the guard is broken?
  4. Independent Findings (F-N, open scope): grep the ENTIRE ai-cli-utils codebase for every
     kill-session / SIGTERM / process-kill / os.kill / pkill call site (the bug doc and prior
     investigation cited src/ai_cli/main.py:3201,3216,3271; src/ai_cli/quota.py:490,650;
     src/ai_cli/session_script.py:350; src/ai_cli/stale_session_reaper.py:176 -- treat this as a
     stale starting list, re-derive it yourself). For each one found, determine whether it retains
     UNGUARDED heuristic host-wide signal authority over another session's process (i.e. could this
     path, on some input, kill a process it does not conclusively own) -- if so, that is a MAJOR or
     F-N finding, since it means the recurring bug class is NOT actually eliminated, only this one
     occurrence's specific mechanism was patched. Also assess whether the bug doc's "Scope-of-fix
     decision" section's reasoning for a CONTAINED fix (vs. a broader redesign / consolidated
     reaper) holds up against Sergei's explicit ask (quoted in the bug doc) for a fix that makes
     the WHOLE CLASS "no longer possible."

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce. For this
  audit specifically: an unguarded kill-authority call site that can still terminate an unrelated
  live session's process is MAJOR, because it means the recurring bug class survives this fix.

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line). Every AD-N Decision line
must be followed by TEMPLATE.md's fixed-key `decision-record` HTML comment: chosen-option and,
when AI-resolved, family, concrete model ID, effort, and profile/persona.
When a finding or AD-N recommends an externally sourced idea, tool, repo, or pattern, apply
docs/procedures/source-vetting-and-corroboration.md and record the evidence route or exploratory
status; a repository name alone is not proof of a best practice.

For each finding, supply:
  - File:line reference (or doc-section).
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

For receipt-driven rounds, additionally return the closed `audit` findings payload: every finding
has stable identity, submitted/effective severity, fingerprint, cluster, non-empty reachable
behavior, disposition, and member digest. Carry the complete current blocking backlog, exactly one
driver lane, and its capability plan; only the trusted receipt may assert stabilization or promotion.
Append exactly one broker-readable `### R<N> Receipt Payload Projection` heading (replace `N` with
this round number), immediately followed by one fenced `json` object containing that closed payload.
Do not put commentary inside the JSON fence or emit a second projection for the same round.

You MUST run a Verification Matrix on at least 5-10 of your own findings: re-run the verification
command and record the actual output. A finding without a reproduced verification command is a
hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code, schemas, configuration, prompts, and tests that the target artifact
references, modifies, replaces, extends, makes claims about, or proposes new behavior next to.
This is a completeness requirement, not a sampling exercise.

**Bias toward reading too much code rather than too little.** It is much better to read code that
turns out to be irrelevant than to miss code that contains a finding the audit should have
surfaced. If you are uncertain whether a file is relevant: read it.

For every symbol / function / class / module / config key / CLI command the target references, run
`grep -r <symbol> <src-roots>` to surface every call site and every related file. Add anything
that surfaces to your read list before producing findings. Repeat for sibling / neighbor code that
implements the same pattern the target proposes.

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

If a file is missing from the active worktree, search sibling worktrees at `.worktrees/*/` and
read it from there before concluding it is missing.

### Audit format (read FIRST — this is how to WRITE the audit)

0. docs/audits/TEMPLATE.md in this repo — read it before writing anything so your output matches
   the required structure: the multi-round append-only model, the finding-ID taxonomy (IC-N / JA-N
   / DV-N / F-N / N-N / AD-N), the severity terminology, and the Decisions / Outstanding Issues /
   Already-Correct / Sign-Off / Verification-Matrix / Audit-Log / Appendix sections.

### Primary subject

1. docs/bugs/cross-session-mosh-termination.md — the bug record under audit (read in full, first).

### Existing code / schemas / prompts / configs / tests

2. src/ai_cli/process_hygiene.py — the fixed file: `auto_clean_orphans()`, `cmd_ps()`,
   `score_mosh_server()`, `_run_lsof_udp()`.
3. src/ai_cli/session_script.py — the generated engine script's `ai ps cron` call site
   (session-start hook) and its comment.
4. tests/test_process_hygiene.py — the regression test and the full `TestCmdPs` class.
5. src/ai_cli/main.py — grep for kill-session / SIGTERM / os.kill / pkill call sites.
6. src/ai_cli/quota.py — grep for kill-session / SIGTERM / os.kill / pkill call sites.
7. src/ai_cli/stale_session_reaper.py — grep for kill-session / SIGTERM / os.kill / pkill call
   sites, and read its generation-fenced/lease-gated design (the bug doc cites this as the
   established "safe ownership rule").
8. src/ai_cli/session.py — `_sweep_orphaned_claude_bg_spares()` / `_has_live_tmux_session()` (site
   of a prior, related fix in this same recurring bug class — AI-CLI-pzjy).
9. Any other file a grep for `kill-session`, `kill_session`, `SIGTERM`, `os.kill(`, `pkill` in
   `src/ai_cli/*.py` surfaces — expand this list during the run, do not treat the list above as
   exhaustive.

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

- Code-only check that ignores Approval Logs in linked plan docs.
- Frontmatter-only status check that ignores doc-body completion signals.
- Skipping linked docs and trusting the parent-doc description alone.
- Partial read of long docs — Approval Logs and sign-off are at the bottom.
- Inline fixes without commit hashes recorded in Resolution Pass.
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling / neighbor files for pattern-consistency, not just the
  named symbols. A missed finding because you didn't read a relevant file is the audit's most
  serious failure mode.
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** Codex (fresh session, independent verification — different model/persona from Round 1's `gpt-5.6-sol`)

**Date:** 2026-09-10 (post-Round-1; Round 1 left 9 open MUST-fix items, all now claimed fixed by PR #129, commit `067a358b`)

```text
You are a principal staff engineer specializing in developer-experience tooling, terminal
multiplexer/session-lifecycle systems (tmux, mosh), and reliability engineering (same domain as
the prior round, a fresh agent / model for independent verification). You are reading the
audit history of docs/bugs/cross-session-mosh-termination.md. This is a later-round verification
pass.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

## Scope guard — full open MUST-fix backlog

The full current OPEN-MUST-FIX-BACKLOG (every item marked MUST be fixed before merge from Round 1,
with its ID and latest claimed-resolution status per the R1 Resolution Pass table above):

- IC-1 — bug record's "contained and sufficient" claim was false given the class-wide gaps below. Claimed fixed: status downgraded `fix-verified` → `fix-implemented`, scope-of-fix section rewritten (`docs/bugs/cross-session-mosh-termination.md`).
- JA-1 — the approved fix's scope claim ("one-command removal is sufficient") did not hold: 5 other call sites retained unowned signal authority. Claimed fixed: all 5 sites below now require proven ownership before signalling.
- DV-1 — the shipped regression test only proved the cron path safe, not the other reachable paths. Claimed fixed: new regression coverage added in `tests/test_stale_session_reaper.py` (live sibling mosh-server survival across launch/quota/sandbox/explicit-clean paths).
- F-1 — `session.py`'s implicit launch-time cleanup could terminate a live sibling's `claude bg-spare` on a legally-hyphenated session name (`c-my-project-1`) misclassified by `_AI_SESSION_RE`. Claimed fixed: `process.terminate()` removed entirely from `cleanup_stale_sessions()`/`_sweep_stale_claude_session_state()` (formerly `_sweep_orphaned_claude_bg_spares`), `src/ai_cli/session.py:457,485-495` — now bookkeeping-only.
- F-2 — `quota.py`'s usage-scraping pre-killed a FIXED tmux name (`ai-quota-scrape`) with zero ownership check. Claimed fixed: unique per-scrape window name + `secrets.token_urlsafe(32)` generation marker + `tmux_ownership.capture_tmux_session_identity`/`kill_owned_tmux_session` atomic fence, `src/ai_cli/quota.py:487,510,658`.
- F-3 — `main.py`'s `--sandbox` relaunch killed any tmux session matching a derived name after only an existence check. Claimed fixed: identity capture + atomic generation revalidation before kill via `tmux_ownership`, `src/ai_cli/main.py:3201-3229`.
- F-4 — `process_hygiene.py`'s `ai ps clean` explicit kill path had a TOCTOU window (PID captured at inventory, killed later with no revalidation). Claimed fixed: `create_time` captured at inventory and revalidated immediately before `terminate()`, skip-not-kill on mismatch, `src/ai_cli/process_hygiene.py:60,63-66,134,458,640-645`.
- F-5 — `tunnel.py`'s `ai tunnel stop`/`ai cdp stop` trusted PID-only state files with no start-time/command check (PID-reuse hazard). Claimed fixed: full `_ManagedProcessIdentity` (pid, create_time, executable, command, port) persisted and revalidated via `_matching_process` before signalling; stale records removed without touching the live PID holder, `src/ai_cli/tunnel.py:26-56,75-132`.
- F-6 — the APPROVED `docs/plans/process-hygiene-plan.md` still mandated the exact score-based auto-kill behavior the fix removed as unsafe. Claimed fixed: those sections marked superseded, the "safe regardless" claim corrected.

Your task is to verify that EVERY item above, including every applicable finding (IC-N / JA-N /
DV-N / F-N) and AD-N decision, has been correctly applied to the target AT ITS CLAIMED LOCATION —
do not just check that the location exists; check that the code there actually does what the claim
says. If you find an older open MUST-fix item this list omits, add it to your output; do not accept
a narrowed scope. The immediately preceding round's new findings are additive only, never a
replacement for the full backlog.

You will also surface NEW issues (N-N) that prior fixes themselves introduced. Any FAIL or PARTIAL
backlog item remains in the open MUST-fix backlog and must be carried into every subsequent
re-verification and fix scope until it passes.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for Round 3 to apply.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each open MUST-fix backlog item:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections prior fixes modified. Look for stale cross-references
introduced by those fixes, Resolution Pass claims that didn't actually land, contradictions those
fixes introduced, and draft-author scaffolding left over from the edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → full open MUST-fix backlog verification table (PASS/FAIL/PARTIAL + evidence) →
  R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

For a receipt-driven round, append exactly one broker-readable `### R<N> Receipt Payload
Projection` heading (replace `N` with this round number), immediately followed by one fenced `json`
object containing the closed `audit` findings payload. Include `iteration`, `scope`, `findings`,
`new_by_severity`, and `major_nonblocking_justifications`; use the same stable finding fields and
dispositions required by Round 1. Do not put commentary inside the JSON fence or emit a second
projection for the same round.

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. docs/audits/TEMPLATE.md in this repo — so your Round 2 section follows the required
   append-only structure.
1. docs/bugs/cross-session-mosh-termination.md — the artifact being verified. Read every section
   prior fixes touched. Target commit: `067a358b`.
2. THIS AUDIT DOC — its full history is your verification checklist; derive and check the complete
   `OPEN-MUST-FIX-BACKLOG`, not only the preceding round.
3. src/ai_cli/session.py — F-1's claimed fix (launch-time cleanup, `_sweep_stale_claude_session_state`).
4. src/ai_cli/quota.py — F-2's claimed fix (generation-fenced tmux identity for quota scraping).
5. src/ai_cli/main.py — F-3's claimed fix (`--sandbox` relaunch ownership revalidation).
6. src/ai_cli/process_hygiene.py — F-4's claimed fix (create_time capture + revalidation in `ai ps clean`).
7. src/ai_cli/tunnel.py — F-5's claimed fix (`_ManagedProcessIdentity` for tunnel/CDP stop).
8. src/ai_cli/tmux_ownership.py — the NEW shared module F-2/F-3 both depend on (generation-fenced
   identity capture + atomic `tmux if-shell` ownership-fenced kill). Verify the atomicity claim
   directly: does the kill really happen inside one `tmux if-shell` call, or is there still a
   capture-then-kill gap?
9. docs/plans/process-hygiene-plan.md — F-6's claimed fix (superseded auto-kill sections).
10. tests/test_stale_session_reaper.py, tests/test_session.py, tests/test_quota.py, tests/test_cli.py,
    tests/test_process_hygiene.py — the regression coverage DV-1 and each F-N claim rely on. Actually
    run the relevant tests, don't just read them, to confirm PASS is real and not a tautological
    assertion against a mock.
```

### Round 3 Reviewer Prompt (Re-audit — N-1 closure verification)

**Model:** Codex (fresh session, independent verification — different model/persona from both
Round 1's `gpt-5.6-sol` and Round 2's `audit`/medium session)

**Date:** 2026-09-11 (post-Round-2; Round 2 left N-1 open-blocking, and JA-1/DV-1 PARTIAL
pending N-1's closure; N-1 is now claimed fixed by ai-cli-utils PR #132, commit `518f222`)

```text
You are a principal staff engineer specializing in developer-experience tooling, terminal
multiplexer/session-lifecycle systems (tmux, mosh), and reliability engineering (same domain as
the prior rounds, a fresh agent / model for independent verification). You are reading the audit
history of docs/bugs/cross-session-mosh-termination.md. This is a later-round verification pass.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

## Scope guard — full open MUST-fix backlog

The full current OPEN-MUST-FIX-BACKLOG (every item Round 2 left open or PARTIAL, with its ID and
latest claimed-resolution status):

- JA-1 — Round 2 found the class invariant still false in one reachable path (the dead-pane
  relaunch race, N-1). Claimed fixed: N-1's fix (below) closes that path, so JA-1's class
  invariant should now hold everywhere. Verify this claim, not just N-1 in isolation.
- DV-1 — Round 2 found no test covered the dead-pane-replacement race. Claimed fixed: a new
  mutation-style regression test was added (see N-1 below) that specifically covers it.
- N-1 — Round 2 confirmed MAJOR: `src/ai_cli/main.py`'s dead-pane relaunch path decided a session
  was dead via a separate `tmux list-panes` call, then captured identity and killed it via a fence
  that only checked ID+generation, never pane-dead state — so a concurrent live replacement could
  be killed. Claimed fixed in ai-cli-utils PR #132 (commit `518f222`): the two-step
  check-then-capture-and-kill logic was replaced with `stale_session_reaper.py`'s established
  `SubprocessTmuxAdapter.capture_fingerprint()` / `fence_and_kill()` pattern — one atomic tmux read
  captures session ID, generation, attachment, AND every pane's dead state together, then the
  atomic fence compares that exact fingerprint before killing. A new mutation regression test,
  `test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives`
  (`tests/test_session_launch_integration.py`), replaces the named dead session with a live
  managed replacement immediately after the dead-pane observation and asserts the replacement
  survives.

F-1 through F-6 and IC-1 are Round-2-CONFIRMED PASS and are NOT in scope for this round — do not
re-verify them unless you find something that specifically contradicts a PASS verdict while
reading the files this round names.

Your task is to verify that EACH item above has been correctly applied to the target AT ITS
CLAIMED LOCATION — do not just check that the location exists; check that the code there actually
does what the claim says, and that the new regression test is a genuine mutation test (not a
tautological assertion). If you find a new issue introduced by this fix, surface it as a new N-N
finding.

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for a follow-up round to apply.
- READ-ONLY on prior rounds' findings: do not rewrite JA-1's wording or change N-1's severity.
  Verify, report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each open MUST-fix backlog item:
  1. Read the claimed-resolution text above.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).
  5. For N-1 specifically: actually RUN the new regression test
     (`uv run pytest tests/test_session_launch_integration.py -k
     test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v`)
     and report the real pass/fail output, not just a source-code read. If you can, also try
     reverting just the `main.py` fix (e.g. `git stash` the relevant hunk) and re-running the test
     to independently confirm it goes RED without the fix.

## Output

Write into a new Round 3 section of this audit doc, following the same structure as Round 2 (R3
Summary → full open MUST-fix backlog verification table → R3.4 NEW issues (N-N) if any → R3
Recommendations). Append a row to the Audit Log. Update the Status Summary cross-round counts.
Never fabricate; cite file:line for every claim.

If ALL of JA-1, DV-1, and N-1 come back PASS with no new blocking issues, say explicitly in your
R3 Recommendations whether you believe `AI-CLI-1wzz` is now safe to close (all 10 findings across
2 rounds resolved) — this is the specific question this round exists to answer.

## Files to read

1. docs/bugs/cross-session-mosh-termination.md — the artifact being verified. Target commit:
   `518f222` or later.
2. THIS AUDIT DOC — Round 1 and Round 2 sections are your verification checklist for this
   narrower backlog.
3. src/ai_cli/main.py — N-1's claimed fix (the dead-pane relaunch fingerprint fence, around what
   was lines 3195-3232 as of the Round 2 audit; the fix changed this block).
4. src/ai_cli/stale_session_reaper.py — the `SubprocessTmuxAdapter` pattern N-1's fix reuses
   (`_TMUX_FINGERPRINT_FORMAT`, `capture_fingerprint()`, `fence_and_kill()`). Confirm the reuse is
   faithful, not a partial/incorrect adaptation.
5. tests/test_session_launch_integration.py — the new mutation regression test claimed for N-1;
   also re-read the existing `test_given_existing_session_with_dead_pane_when_relaunched_then_recreates_...`
   test nearby to confirm the fix didn't regress the ordinary (non-race) dead-pane-recreate case.
```

### Round 4 Reviewer Prompt (Re-audit — full remaining backlog: JA-1, DV-1, N-1, N-2)

**Model:** Codex (fresh session, independent verification — different model/persona from prior rounds)

**Date:** 2026-09-11 (post-Round-3; Round 3 found N-2 MAJOR and left JA-1/DV-1/N-1 unresolved
pending it; N-2 is now claimed fixed by ai-cli-utils PR #133, commit `0663ac0`)

```text
You are a principal staff engineer specializing in developer-experience tooling, terminal
multiplexer/session-lifecycle systems (tmux, mosh), and reliability engineering (same domain as
the prior rounds, a fresh agent / model for independent verification). You are reading the audit
history of docs/bugs/cross-session-mosh-termination.md. This is a later-round verification pass.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

## Scope guard — full open MUST-fix backlog

The full current OPEN-MUST-FIX-BACKLOG:

- JA-1 — class invariant (no cross-session tmux kill without proven ownership). Blocked on N-1 and
  N-2 both actually closing every reachable path. Verify it holds EVERYWHERE now, not just at the
  two previously-named sites.
- DV-1 — regression coverage for the whole class, not just individual sites.
- N-1 — dead-pane relaunch race, claimed fixed in commit `518f222` (PR #132). Round 3 confirmed the
  source fix but could not execute the test in its sandbox; you have a normal writable environment,
  so actually run `uv run pytest tests/test_session_launch_integration.py -k
  test_given_dead_session_replaced_after_observation_when_relaunched_then_live_replacement_survives -v`
  and report the real result.
- N-2 — new-session configuration-failure cleanup race, claimed fixed in commit `0663ac0` (PR #133):
  immediately after `tmux new-session` succeeds, a random generation token is set and identity is
  captured via `tmux_ownership.capture_tmux_session_identity`; the configuration-failure cleanup path
  now calls `tmux_ownership.kill_owned_tmux_session(identity)` instead of a raw name-only
  `kill-session`. Verify this at `src/ai_cli/main.py` (search for `@ai_cli_session_generation` near
  the `tmux_options` loop), and run
  `uv run pytest tests/test_session_launch_integration.py -k
  test_given_new_session_replaced_after_configuration_failure_when_cleanup_runs_then_replacement_survives -v`.

Your task: verify EACH item above against the actual code AND by actually running the cited tests.
Also actively look for an 8th mechanism: read every remaining `subprocess.run` call in
`src/ai_cli/main.py`, `src/ai_cli/session.py`, `src/ai_cli/quota.py`, and `src/ai_cli/tunnel.py`
whose argv contains `kill-session`, `kill`, or `.terminate(` / `.kill(`, and confirm each one is
gated by a captured-identity/generation fence (not a bare name or bare PID). This is the question
this round exists to answer: is there ANY remaining unfenced destructive call site in this codebase?

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round.
- READ-ONLY on prior rounds' findings: report PASS / FAIL / PARTIAL with quoted evidence, do not
  rewrite prior wording or severity.

## Output

Write into a new Round 4 section of this audit doc, following the same structure as Round 3 (R4
Summary → full open MUST-fix backlog verification table → R4.4 NEW issues (N-N) if any → R4
Recommendations). Append a row to the Audit Log. Update the Status Summary cross-round counts.
Never fabricate; cite file:line for every claim, and cite the actual pytest output for every test
claim.

**This is the round that determines whether AI-CLI-1wzz can close.** If JA-1, DV-1, N-1, and N-2
all come back PASS with real (not blocked) test execution, and your sweep for an 8th mechanism
finds nothing new, say explicitly in your R4 Recommendations that AI-CLI-1wzz is safe to close. If
anything is still open or you find a new issue, say explicitly that it is not.

## Files to read

1. docs/bugs/cross-session-mosh-termination.md — target commit `0663ac0` or later.
2. THIS AUDIT DOC — Rounds 1-3 are your verification checklist.
3. src/ai_cli/main.py — N-1's fix (around the dead-pane relaunch block) and N-2's fix (around the
   new-session configuration block, look for `@ai_cli_session_generation`).
4. src/ai_cli/session.py, src/ai_cli/quota.py, src/ai_cli/tunnel.py, src/ai_cli/process_hygiene.py,
   src/ai_cli/tmux_ownership.py, src/ai_cli/stale_session_reaper.py — sweep every destructive call
   site in these files for the 8th-mechanism check above.
5. tests/test_session_launch_integration.py — run the N-1 and N-2 regression tests directly, don't
   just read them.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->

## Round 5 -- Verification Pass (append-only)

**Round 5 auditor:** Codex `gpt-5.6-sol`, `audit` role (effort: medium)

**Round 5 date:** 2026-09-10

**Round 5 target commit:** `e15a51f`

**Round 5 scope:** Verify the full remaining backlog (JA-1, DV-1, N-3, N-4, N-5) against
PR #134's merged commit, independently inspect the two review-time regressions described in that
commit, execute the cited regressions, and repeat the full-tree `subprocess.run` and destructive
process/tmux call-site sweep. Append-only: no source, test, bug-record, receipt, or prior audit
content was edited.

### R5 Summary

N-3 and N-5 **PASS at source level**. Both `main.py` `tmux new-session` calls have `text=True`,
parse the returned opaque session ID, and use that ID for marker assignment and identity capture.
Quota creation follows the same ID-bound sequence. Abandoned-process reclamation passes the
recorded start identity into `end_process()`, whose procfs and psutil backends revalidate before
TERM, CONT/resume, and KILL. Controlled in-process probes also confirmed later phases become
unreachable when identity changes after TERM.

N-4 **FAILS**. The terminal `tmux if-shell` fence is atomic, but the supervisor's ownership
bootstrap is not ownership proof: it mints a second generation and resolves the mutable baked name
with `tmux display-message -t "$tmux_session"` before marking whichever opaque ID currently owns
that name. A rename plus name reuse before this bootstrap makes the original supervisor adopt the
replacement and later kill it through an otherwise-correct fence (N-6). This is the exact
credential-bootstrap defect N-3 fixed in the parent launcher and that Round 4's N-4 recommendation
explicitly required the fix not to repeat.

JA-1 and DV-1 therefore remain **FAIL**. The class invariant is still false, and no regression
mutates the name before the supervisor bootstrap. The N-4 real-tmux test constructs an already
captured opaque ID/generation and tests only the final fence, while the generated-supervisor test's
fake tmux always returns `$1`; neither drives the unsafe acquisition interval.

The shipped `test_stale_session_reaper.py` fake-tmux correction is present at lines 1187-1189, and
both `main.py` creation attempts have `text=True` at lines 3279 and 3301. The claimed real-tmux
launch success is **UNVERIFIED in this round**: `mktemp -d` failed with `Operation not permitted`,
tmux could not connect to `/private/tmp/tmux-501/default`, and the 13-test focused pytest command
failed before collection with `FileNotFoundError: No usable temporary directory`. Those are actual
worker-policy restrictions, contrary to the invocation's non-authoritative writable-environment
framing; no blocked test is counted as PASS.

### R5.1 Open MUST-fix backlog verification

| ID | Verdict | Evidence | Verification note |
|----|---------|----------|-------------------|
| JA-1 | **FAIL (CONFIRMED)** | N-6 leaves a reachable cross-session kill: supervisor ownership is acquired through the reusable name at `src/ai_cli/session_script.py:174,217-221`, then exercised destructively at lines 358-365. | Source ordering and generated-script probe reproduced the gap; live interleaving was blocked by tmux socket policy. |
| DV-1 | **FAIL (CONFIRMED coverage gap; execution BLOCKED)** | `tests/test_session_launch_integration.py:540-563` tests only an already captured ID/token; `tests/test_stale_session_reaper.py:1183-1190,1413-1434` makes the fake name lookup always return `$1`; `tests/test_session_launch_shell_resolution.py:173-190,211-236` replaces the real generated supervisor with a trivial script. No test renames/reuses the name before lines 217-221. | Repository search confirms the missing seam. The combined focused run failed before collection. |
| N-3 | **PASS (CONFIRMED source; execution BLOCKED)** | Both creation attempts request `-P -F '#{session_id}'`, use `text=True`, validate `result.stdout`, and target `created_session_id` for marker/capture (`src/ai_cli/main.py:3263-3328`). Quota does the same (`src/ai_cli/quota.py:493-516`). | Exact diff and current source agree. Mutation and real-launch tests could not collect. |
| N-4 | **FAIL (CONFIRMED) -- see N-6** | The final fence compares opaque ID plus generation atomically (`src/ai_cli/session_script.py:358-365`), but both inputs were acquired by a second, independent supervisor writer through the mutable name (`src/ai_cli/session_script.py:174,217-221`). | Terminal fence PASS; ownership bootstrap FAIL. The test-fixture correction is present but models only the happy-path lookup. |
| N-5 | **PASS (CONFIRMED source; execution BLOCKED)** | Caller passes `record.get("procStart")` into `end_process()` (`src/ai_cli/main.py:488-497`). Procfs rechecks before each `os.kill*` (`src/ai_cli/process_probe.py:284-321`); psutil rechecks before terminate/resume/kill (`src/ai_cli/process_probe.py:400-417`). | Controlled phase-mutation probes sent TERM only, then suppressed CONT/KILL after identity changed. Pytest could not collect. |

### R5.2 Full-tree destructive call-site sweep

The complete AST sweep found 169 `subprocess.run` calls across 21 `src/ai_cli` files. Only two
direct Python argv sites contain tmux destruction: the stale reaper and `tmux_ownership`; both put
the exact opaque-ID predicate and `kill-session` in one `tmux if-shell` call. The generated shell
contains the third tmux kill site and is the N-6 failure. No `kill-window` or `kill-pane` site was
found.

| Site(s) | Target / authority | Verdict |
|---|---|---|
| `src/ai_cli/stale_session_reaper.py:108-183` | Captured full session/pane fingerprint | **PASS:** exact ID, generation, attachment, windows, pane IDs/PIDs/dead state are compared inside the destructive `if-shell`. |
| `src/ai_cli/tmux_ownership.py:26-88` | Captured opaque ID plus generation | **PASS as terminal fence:** validated values are compared in the same tmux command as the kill. |
| `src/ai_cli/session_script.py:174,217-221,358-365` | Supervisor clean-exit session | **FAIL -- N-6:** terminal fence is atomic, but the supervisor acquires both its independent token and ID through the mutable name. |
| `src/ai_cli/main.py:3263-3344`; `src/ai_cli/quota.py:478-658` | Newly created normal/quota sessions | **PASS for N-3:** creation-returned opaque IDs establish ownership; destructive cleanup is delegated to the atomic helper. |
| `src/ai_cli/process_probe.py:284-321,400-417` | Abandoned registered process/tree | **PASS for N-5:** recorded birth identity is checked before every signal phase. |
| `src/ai_cli/process_hygiene.py:614-669`; `src/ai_cli/tunnel.py:136-227,340-444` | Explicitly selected or durably registered process | **PASS:** create time/full identity is revalidated on the psutil handle immediately before its guarded signal. |
| `src/ai_cli/transport.py:266-300,350-365`; `src/ai_cli/messaging.py:305-314`; `src/ai_cli/tunnel.py:184-189,391-401` | Direct unreaped `Popen` children | **PASS:** signals use the handle returned to the creating owner, not a rediscovered PID. |
| `src/ai_cli/session_script.py:237-340` | Supervisor-created heartbeat/child PIDs and child process group | **PASS:** targets are direct children; `kill -0` is only a probe, and SIGCONT/SIGSTOP are scoped job-control operations. |
| Entire `src/ai_cli` tree | `kill-window`, `kill-pane` | **PASS:** no call site found. |

Dynamic `subprocess.run(cmd)` sites used for user-selected commands, git/package operations, and
read-only process/tmux probes were inspected through their callers; none adds an implicit
cross-session signal owner. This syntactic result does not cure N-6 because that kill is embedded
in the generated shell string.

### R5.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| -- | **N/A (CONFIRMED)** | No prior AD-N exists. N-6 has the same fail-closed ownership rule already selected for N-3/N-4 and requires no product-policy choice. |

### R5.4 NEW issues surfaced

#### N-6: Supervisor reacquires ownership through the mutable name and can adopt a replacement -- `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:3177-3192,3263-3328`;
`src/ai_cli/session_script.py:165,174,217-223,358-365`;
`tests/test_session_launch_integration.py:540-563`;
`tests/test_stale_session_reaper.py:1183-1190,1413-1434`

**What the Round 4 recommendation required:**

> “Give the supervisor one immutable identity established by the creator and perform clean-exit
> teardown through an atomic ID+generation fence. Do not mint a second token in the child through
> the session name.”

**Evidence:**

```bash
# src/ai_cli/session_script.py:165,174,217-221
tmux_session=<baked mutable name>
generation_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || true)
_supervisor_tmux_session_id=$(tmux display-message -p -t "$tmux_session" '#{session_id}' 2>/dev/null || true)
tmux set-option -t "$_supervisor_tmux_session_id" @ai_cli_session_generation "$generation_token"
```

The parent separately mints its own generation only after `new-session` returns
(`src/ai_cli/main.py:3318-3328`); that value is not passed into the already-generated script. The
supervisor therefore repeats the unsafe ownership bootstrap through the mutable name. If its own
session is renamed and another session takes the old name before line 218, the lookup returns the
replacement's ID, line 220 writes the supervisor's token onto that replacement, and the old
supervisor's clean exit later kills the replacement through the valid ID/token fence at line 359.

Even without name reuse, the parent and supervisor can overwrite each other's different tokens
between the parent's `set-option` and expected-generation capture, causing a real launch to fail
closed intermittently. The real-launch shell-resolution test substitutes `get_engine_script()`
with `touch ...; sleep 30`, so it verifies the `text=True`/stdout boundary but cannot expose this
dual-writer race.

**Why it matters:** A reachable rename/name-reuse interleaving still lets one session's normal
exit terminate a live sibling, so the founding safety invariant remains false. The independent
token race can also reject an otherwise successful launch, making the highest-value launch path
not structurally reliable even though `text=True` is present.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '3177,3192p;3263,3328p'
nl -ba src/ai_cli/session_script.py | sed -n '165,223p;358,365p'
nl -ba tests/test_session_launch_integration.py | sed -n '540,563p'
nl -ba tests/test_stale_session_reaper.py | sed -n '1183,1190p;1413,1434p'
rg -n 'display-message -p -t.*tmux_session|before.*supervisor.*ownership|bootstrap' \
  src/ai_cli/session_script.py tests
```

**Verification note:** CONFIRMED against `e15a51f`: blame attributes the name lookup, independent
ownership flag, and final fence to PR #134. A generated-script probe located the mutable-name
lookup before marker assignment and the later fence. The precise live interleaving remains
UNVERIFIED dynamically because this worker cannot create a temp directory or tmux socket; this
restriction is recorded verbatim in R5.5 rather than converted into a PASS.

**Recommended fix:** Establish one shared creator/supervisor generation, not two. Bind the
supervisor to its own current tmux pane/session context (or pass the creation-returned opaque ID
through a fail-closed handoff) without resolving the baked name, and require the shared ID/token
before heartbeat or teardown authority is enabled. Add a deterministic seam before supervisor
ownership acquisition that renames the original and creates a replacement under the old name;
the replacement must remain unmarked and survive. Add an end-to-end launch using the real generated
supervisor so the parent/supervisor token race is exercised.

### R5.5 Verification Matrix

| Check | Command | Expected | Actual | Pass? |
|---|---|---|---|---|
| Target pin | `git rev-parse --short HEAD` | `e15a51f` | `e15a51f` | ✅ |
| N-3 / launch bytes fix | `git show e15a51f:src/ai_cli/main.py ...` | Both creation calls use `text=True` and parse opaque IDs | `text=True` present at current lines 3279 and 3301; ID parsed at 3313-3317 | ✅ source |
| N-3 creation ownership | Full main/quota excerpts plus mutation-test read | Marker/capture target creation-returned ID | `created_session_id` targets at `main.py:3320,3328` and `quota.py:506,513` | ✅ source |
| N-4 terminal fence | Generated script and real-tmux fence-test read | Atomic ID+generation compare-and-kill | `if-shell` contains compare and exact-ID kill in one command | ✅ terminal fence only |
| N-4 / N-6 bootstrap | Generated script ordering probe | Creator-established identity, no mutable-name reacquisition | Probe reported `bootstrap_uses_mutable_name=True`; lookup precedes marker/fence | ❌ |
| N-5 procfs phases | Controlled identity sequence `MATCH, UNPROVEN, UNPROVEN` | Only TERM before identity changes; CONT/KILL suppressed | `signal_count=1 signals=['SIGTERM']` | ✅ |
| N-5 psutil phases | Controlled identity sequence `MATCH, UNPROVEN, UNPROVEN` | Only terminate before identity changes; resume/kill suppressed | `signals=['TERM']` | ✅ |
| Full destructive sweep | AST inventory plus full-tree `rg` | Every destructive edge owned; no hidden kill-window/pane | 169 runs/21 files; N-6 is the sole failed lifecycle owner; zero kill-window/pane | ❌ class gate |
| Temp/tmux capability | `mktemp -d`; real `tmux new-session` | Writable temp and isolated real tmux | `Operation not permitted` for temp and `/private/tmp/tmux-501/default` | ❌ BLOCKED |
| 13 cited regressions | Focused `.venv/bin/pytest -p no:cacheprovider -v ...` | 13 tests collect and pass | Exit 1 before collection: `FileNotFoundError: No usable temporary directory found ...` | ❌ BLOCKED |

**Verified: 6/10 checks pass at source or controlled-process level; 2/10 reproduce the blocking
N-6/class failure; 2/10 runtime checks are blocked by the exact worker permission errors above.
No pytest or real-tmux PASS is claimed.**

### R5 Recommendations

**MUST be fixed before closing AI-CLI-1wzz or claiming class-wide verification:**

- N-6: remove the supervisor's mutable-name ownership bootstrap and independent token writer;
  establish one creator/supervisor identity and add pre-bootstrap rename/reuse plus real generated-
  supervisor launch regressions.
- JA-1 and DV-1 remain FAIL until N-6 is fixed, the missing interleaving is covered, and the cited
  runtime suite executes in a genuinely writable tmux-capable environment.
- Correct the stale hypothesis-ledger rationale at
  `docs/bugs/cross-session-mosh-termination.md:62`: “teardown uses the baked tmux_session” is not
  safety evidence and is contradicted by N-4/N-6.

**SHOULD be re-verified in the next audit round:**

- Re-run all 13 focused regressions, especially the real `text=True` launch test, rather than
  carrying PR #134's historical 3/3 claim as independent evidence.
- Repeat the full-tree destructive scan after the N-6 repair; keep generated shell bodies in the
  inventory because an AST-only Python call scan does not expose them.

**Can be folded into a follow-up:**

- None. N-6 is the original recurring cross-session kill class, not a neighboring cleanup item.

**Closure verdict:** `AI-CLI-1wzz` is **not safe to close**. N-3 and N-5 are credible source fixes,
but N-4 is incomplete, N-6 preserves a reachable cross-session kill, JA-1/DV-1 fail, and this
worker could not independently execute the real-tmux launch or cited regression suite.

### R5 Audit Log

| Date | Round | Notes |
|---|---|---|
| 2026-09-10 | Round 5 | Codex (`gpt-5.6-sol`, `audit`, medium): N-3/N-5 source PASS; N-4/JA-1/DV-1 FAIL; new MAJOR N-6 (supervisor mutable-name ownership bootstrap); runtime BLOCKED before collection by temp/tmux socket policy; not safe to close; audit-doc-only append. |

### R5 Files Read

- Canonical ai-harness `docs/audits/STUB.md` and `TEMPLATE.md` -- full scaffold, later-round
  boilerplate, taxonomy, verification matrix, anti-patterns, and Decision/AD-N skeleton.
- `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md` -- full prior history through
  Round 4 and every prior reviewer prompt.
- Commit `e15a51f` -- full metadata and diff for all nine changed source/test files.
- `src/ai_cli/main.py`, `quota.py`, `session_script.py`, `process_probe.py`,
  `tmux_ownership.py`, `stale_session_reaper.py`, `session.py`, `tunnel.py`, and
  `process_hygiene.py` -- requested lifecycle paths plus all destructive/run call sites.
- `src/ai_cli/transport.py` and `messaging.py` -- additional full-tree termination hits and direct
  child ownership.
- `tests/test_session_launch_integration.py`, `test_quota.py`, `test_process_probe.py`,
  `test_stale_session_reaper.py`, `test_session_launch_shell_resolution.py`, and `test_cli.py` --
  changed tests, fixtures, mutation seams, and real-launch boundary.
- `tests/test_process_hygiene.py`, `test_session.py`, and `test_cdp.py` -- prior-backlog survivor
  and identity regressions named by the audit history.
- `docs/bugs/cross-session-mosh-termination.md` -- full current bug record and stale hypothesis
  rationale.

### R5 Commands Run

```bash
git status --short
git rev-parse --short HEAD
git log -8 --oneline --decorate
git show --format=fuller --find-renames e15a51f -- <all changed paths>
nl -ba <requested source-or-test> | sed -n '<evidence ranges>'
rg -n -g '*.py' 'subprocess\.run|\.terminate\(|\.kill\(|os\.kill\b|killpg\b|kill-session|kill-window|kill-pane' src/ai_cli
.venv/bin/python -B -c '<AST inventory of every subprocess.run and process signal call>'
.venv/bin/python -B -c '<procfs phase-mutation probe>'
.venv/bin/python -B -c '<psutil phase-mutation probe>'
.venv/bin/python -B -c '<generated-supervisor ownership-order probe>'
git blame -L 165,223 e15a51f -- src/ai_cli/session_script.py
git log -p -S '_supervisor_tmux_session_id' -- src/ai_cli/session_script.py
mktemp -d
tmux -V
tmux has-session -t ai-cli-r5-audit-20260910
tmux new-session -d -P -F '#{session_id}' -s ai-cli-r5-audit-20260910 'sleep 30'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -v <13 cited tests>
git diff --check e15a51f^ e15a51f
```

## Round 6 -- Verification Pass (append-only)

**Round 6 auditor:** Codex `gpt-5.6-sol`, `audit` role (effort: medium)

**Round 6 date:** 2026-09-10

**Round 6 target commit:** `34552a2`

**Round 6 scope:** Verify the complete stated backlog (JA-1, DV-1, N-6) against PR #135,
reconcile that list against Round 5's actual MUST recommendations, reproduce the N-6 rename/reuse
race with real tmux, execute the N-1 through N-6 regressions, repeat the complete
`src/ai_cli` destructive-call inventory, and hunt the systemic pattern of resolving a mutable
tmux name after an opaque ID or pane reference is available. Append-only: no source, test,
bug-record, receipt, or prior audit content was edited.

### R6 Summary

N-6 **PASSES at source and local-manual evidence level but remains PARTIAL overall**. Commit
`34552a2` removes `-t "$tmux_session"` from the supervisor bootstrap, and the generated script
contains exactly one untargeted `tmux display-message -p '#{session_id}'` before marker assignment.
The installed tmux 3.7c manual says IDs are unique and unchanged for an object's lifetime
(`/opt/homebrew/share/man/man1/tmux.1:916-925`) and says untargeted `display-message` takes format
information from the active pane (`/opt/homebrew/share/man/man1/tmux.1:7429-7437`). The resulting
ID is validated and used for marker assignment and the later atomic ID+generation teardown fence
(`src/ai_cli/session_script.py:217-223,358-365`). This removes N-6's mutable-name lookup in source.

The required dynamic proof did not execute. Contrary to the non-authoritative environment claim,
`mktemp -d` failed with `Operation not permitted`, and a separate `tmux new-session` failed with
`error connecting to /private/tmp/tmux-501/default (Operation not permitted)`. The exact new test
and a combined 15-test N-1-through-N-6 command both failed before collection because
`portalocker` calls `tempfile.gettempdir()` during `tests/conftest.py` import and Python found no
usable temporary directory. No real-tmux or pytest PASS is claimed. DV-1 is therefore PARTIAL,
and the closure gate remains open even though the new mutation test is structurally genuine.

The supplied three-item backlog is also incomplete. Round 5 expressly marked correction of the
stale bug-record hypothesis as MUST (`docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md:
2499-2508`), but `docs/bugs/cross-session-mosh-termination.md:62` still calls the baked mutable
name a reason to reject cross-session teardown. JA-2 records that alignment failure.

Finally, the requested systemic name-identity hunt found N-7: after the launcher has captured the
creation-returned opaque ID, it reverts to `session_id` (the mutable name) for window/session
configuration, iTerm2 configuration, rename, and attach (`src/ai_cli/main.py:3313-3349`). This
does not create a new unfenced kill--the failure cleanup still kills only the captured ID through
the generation fence--but a rename plus name reuse can configure and attach the replacement while
leaving the created session behind. It is the same identity-resolution anti-pattern the scope
explicitly required this round to hunt.

### R6.1 Open MUST-fix backlog verification

| ID | Verdict | Evidence | Verification note |
|---|---|---|---|
| JA-1 | **PARTIAL (source PASS; runtime UNVERIFIED)** | The complete AST/embedded-shell sweep found the same three tmux kill sites as Round 5: full-fingerprint fence (`src/ai_cli/stale_session_reaper.py:158-183`), ID+generation fence (`src/ai_cli/tmux_ownership.py:59-84`), and supervisor ID+generation fence (`src/ai_cli/session_script.py:358-365`). No raw name-targeted tmux kill, `kill-window`, or `kill-pane` exists. | The source invariant is CONFIRMED at `34552a2`; the required hostile real-tmux interleaving could not run, so class closure is not independently runtime-verified. N-7 is a non-destructive name-identity defect and does not contradict this kill-site result. |
| DV-1 | **PARTIAL (coverage present; execution BLOCKED)** | The new test pauses immediately before supervisor ownership acquisition, renames the original by opaque ID, creates a same-name replacement, releases the bootstrap, verifies only the original receives the marker, then verifies clean exit removes only the original (`tests/test_stale_session_reaper.py:205-296,433-489`). | Exact test: exit 4 before collection with `FileNotFoundError: No usable temporary directory found`; combined regression command failed identically. The test is not counted as passing. |
| N-6 | **PARTIAL (source PASS; runtime BLOCKED)** | PR #135 changes the bootstrap to `_supervisor_tmux_session_id=$(tmux display-message -p '#{session_id}')` and preserves ID-targeted marker/fence operations (`src/ai_cli/session_script.py:217-223,358-365`). Installed tmux documentation binds omitted `display-message -t` to the active pane and documents opaque-ID lifetime stability. | CONFIRMED source/manual semantics; PLAUSIBLE exact-race immunity until the requested live rename/reuse test executes. Both raw tmux and pytest were blocked by the exact permission errors in R6.6. |
| JA-2 | **FAIL (CONFIRMED omitted prior MUST item)** | Round 5 required correction of `docs/bugs/cross-session-mosh-termination.md:62`, but the supplied Round 6 backlog omitted it and the stale sentence remains byte-for-byte. | Reproduced by direct `rg` against the Round 5 Recommendations and current bug record. |

### R6.2 Alignment finding

#### JA-2: Round 6's stated backlog omits Round 5's still-unfixed safety-rationale correction -- `MAJOR` (CONFIRMED)

**Location:** `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md:2499-2508`;
`docs/bugs/cross-session-mosh-termination.md:58-64`

**Evidence:**

> “Correct the stale hypothesis-ledger rationale at
> `docs/bugs/cross-session-mosh-termination.md:62`: ‘teardown uses the baked tmux_session’ is not
> safety evidence and is contradicted by N-4/N-6.”

The current bug record still says:

> “Rejected: teardown uses the baked `tmux_session`; transcript resolution mutates child-only
> `session_id`”

**Why it matters:** The supplied backlog calls itself complete but drops an explicit prior MUST
item. The retained rationale teaches precisely the mutable-name-as-identity rule that caused N-4
and N-6, so marking the incident closed with it intact would preserve a regression-inducing safety
claim in the canonical bug record.

**Verification command:**

```bash
rg -n 'Correct the stale hypothesis-ledger|teardown uses the baked' \
  docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md \
  docs/bugs/cross-session-mosh-termination.md
```

**Verification note:** CONFIRMED at `34552a2`; the command returns the Round 5 MUST requirement at
audit lines 2506-2508 and the still-stale bug-record assertion at line 62.

**Recommended fix:** Correct the hypothesis ledger to state that baked names are mutable and were
not ownership proof; record that PRs #134/#135 replaced the name-targeted teardown/bootstrap with
an opaque-ID+generation fence and pane-context bootstrap. Carry JA-2 in the next complete backlog.

### R6.3 Full-tree destructive and mutable-name identity sweep

The full AST pass again found exactly 169 `subprocess.run` calls across 21 files (40 Python files
parsed). Fifteen AST-visible destructive process/tmux calls were inspected, and the generated shell
adds the third tmux-kill site. PR #135 introduced no new `subprocess.run`, process signal, tmux
kill, `kill-window`, or `kill-pane` site.

| Site(s) | Target / authority | Verdict |
|---|---|---|
| `src/ai_cli/stale_session_reaper.py:108-183` | Captured full session/pane fingerprint | **PASS:** exact ID, generation, attachment, windows, pane IDs/PIDs/dead state are compared in the destructive `if-shell`. |
| `src/ai_cli/tmux_ownership.py:26-84` | Captured opaque ID plus generation | **PASS:** exact ID+generation predicate and kill share one tmux command. |
| `src/ai_cli/session_script.py:217-223,358-365` | Supervisor's live pane/session | **PASS at source/manual level; runtime BLOCKED:** ownership bootstrap is untargeted and teardown uses the resulting opaque ID plus generation. |
| `src/ai_cli/main.py:3263-3349`; `src/ai_cli/quota.py:478-661` | Newly created normal/quota sessions | **PASS for destructive cleanup:** creation-returned IDs establish ownership and all kills use fenced identities. **FAIL for later normal-session targeting (N-7):** main reverts to the mutable name for configuration/attach. Quota continues using the opaque ID. |
| `src/ai_cli/process_probe.py:284-321,400-417` | Abandoned registered process/tree | **PASS against the accepted N-5 contract:** recorded birth identity is rechecked before TERM, CONT/resume, and KILL. |
| `src/ai_cli/process_hygiene.py:614-669`; `src/ai_cli/tunnel.py:136-227,340-444` | Explicitly selected or durably registered process | **PASS:** captured creation/full identities are checked before signalling. |
| `src/ai_cli/transport.py:266-300,350-365`; `src/ai_cli/messaging.py:305-314`; `src/ai_cli/tunnel.py:184-189,391-401` | Direct unreaped `Popen` children | **PASS:** direct handles returned to the creating owner are signalled. |
| Entire `src/ai_cli` tree | `kill-window`, `kill-pane`, raw name-targeted `kill-session` | **PASS:** none found. |

Name-based lookups without a previously available stable identity remain legitimate in discovery,
reattach, and user-selected resolution paths (`src/ai_cli/session.py:271-304,407-427,536-560` and
`src/ai_cli/main.py:2157-2162,3201-3239`). The defect is specifically the post-creation branch,
where `created_session_id` and `identity.session_id` already exist but are discarded in favor of
the name.

### R6.4 AD-N decisions verification

| ID | Verdict | Evidence |
|---|---|---|
| -- | **N/A (CONFIRMED)** | No prior AD-N exists. JA-2 and N-7 each have a single fail-closed consistency fix and require no product-policy choice. |

### R6.5 NEW issues surfaced

#### N-7: Launcher reverts from the creation-returned opaque ID to the mutable name -- `MAJOR` (CONFIRMED)

**Location:** `src/ai_cli/main.py:3313-3349`; `src/ai_cli/iterm2.py:355-388`;
`tests/test_session_launch_integration.py:497-563`

**What the Round 5 systemic requirement asks:**

> Hunt whether any place “resolves identity by mutable name when a stable identifier was available.”

**Evidence:**

```python
# src/ai_cli/main.py:3313-3349
created_session_id = result.stdout.strip() if isinstance(result.stdout, str) else ""
...
identity = _tmux_ownership.capture_tmux_session_identity(created_session_id, ...)
...
["tmux", "set-window-option", "-t", session_id, "remain-on-exit", "on"]
["tmux", "set-option", "-t", session_id, "mouse", "on"]
...
_iterm2._configure_tmux_for_iterm2(session_id)
_iterm2._rename_tmux_window(session_id, ai_name)
os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])
```

The iTerm2 helpers issue three more per-pane/window commands against their argument
(`src/ai_cli/iterm2.py:370-388`). If the created session is renamed and a replacement takes its
old name after identity capture, these calls configure, rename, and attach the replacement. The
failure cleanup is not redirected--it retains `identity` and can kill only the original opaque
ID--so this is a new cross-session mutation/attachment defect, not a fourth unfenced tmux-kill site.

The existing creation mutation stops immediately after proving the replacement was not marked
(`tests/test_session_launch_integration.py:497-537`); the N-4 fence test starts from a separately
constructed ID/token (`tests/test_session_launch_integration.py:540-563`). Neither mutates the name
after identity capture and observes the actual configuration/attach targets.

**Why it matters:** A concurrent rename/name-reuse can make a launch configure and attach a
sibling session while abandoning the session it actually created. This is reachable incorrect
cross-session lifecycle behavior and repeats the identity-resolution anti-pattern that produced
N-3 and N-6, even though the destructive cleanup edge itself is now fenced.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '3313,3349p'
nl -ba src/ai_cli/iterm2.py | sed -n '355,388p'
rg -n 'after.*marker|before.*configuration|before.*attach|rename.*after.*ownership' \
  tests/test_session_launch_integration.py tests/test_cli.py tests/test_session_launch_shell_resolution.py
```

**Verification note:** CONFIRMED by source data flow: the opaque ID remains in scope but every
post-capture per-session operation named above receives `session_id`. The test search returned no
post-capture mutation seam. Dynamic reproduction is blocked by the same tmux/temp restrictions.

**Recommended fix:** After creation succeeds, use `identity.session_id` for every per-session,
per-window, iTerm2, rename, and attach target. Reserve the logical name only for messages and
persistent logical metadata. Add a mutation immediately after identity capture that renames the
created session and creates a same-name replacement; assert only the created opaque ID is
configured/attached and the replacement is untouched.

### R6.6 Verification Matrix

| Check | Command | Expected | Actual | Pass? |
|---|---|---|---|---|
| Target pin | `git rev-parse --short HEAD` | `34552a2` | `34552a2` | ✅ |
| PR #135 diff | `git show --format=fuller --find-renames 34552a2` | Only name target removed; mutation test added | 3 files, 73 insertions/3 deletions; bootstrap drops `-t "$tmux_session"` | ✅ |
| Generated-script shape | `.venv/bin/python -B -c '<get_engine_script probe>'` | One untargeted lookup, no name-targeted lookup/raw name kill | `untargeted_lookup_count 1`, `name_targeted_lookup False`, `opaque_fence True`, `raw_name_kill False` | ✅ source |
| tmux semantics | `sed -n '720,930p;7385,7445p' /opt/homebrew/share/man/man1/tmux.1` | Opaque IDs stable; omitted display target uses active pane | Manual states both at lines 916-925 and 7429-7437 | ✅ manual |
| Exact rename/reuse race | `mktemp -d`; `tmux new-session -d -s <probe> 'sleep 5'` | Both succeed, then hostile sequence is reproduced | `mktemp: ... Operation not permitted`; tmux socket: `Operation not permitted` | ❌ BLOCKED |
| New N-6 regression | `.venv/bin/pytest -s -p no:cacheprovider -v ...renamed_supervisor_during_ownership_bootstrap...` | One test passes | Exit 4 before collection; `FileNotFoundError: No usable temporary directory found` during conftest import | ❌ BLOCKED |
| N-1 through N-6 regression set | Combined explicit 15-node pytest command | All collect and pass | Exit 4 before collection with the same `portalocker`/`tempfile.gettempdir()` error | ❌ BLOCKED |
| Full AST inventory | Python AST walk over `src/ai_cli/**/*.py` | Round 5's 169 calls/21 files; no new destructive call | `python_files=40 subprocess_runs=169 run_files=21`; 15 AST-visible destructive calls | ✅ |
| Full tmux-kill sweep | Full-tree `rg` plus AST contexts | Exactly three fenced tmux-kill sites; no kill-window/pane | Reaper, ownership helper, generated supervisor only; zero kill-window/pane/raw-name kill | ✅ source |
| Prior MUST reconciliation | `rg` across R5 Recommendations and bug record | Every R5 MUST item carried/fixed | R5 correction requirement found; stale bug-record line still present and omitted from supplied backlog | ❌ JA-2 |
| Stable-ID anti-pattern | Main/iTerm2 excerpts plus test mutation search | No return to mutable name after opaque ID capture | Six post-capture operations use `session_id`; no matching mutation seam | ❌ N-7 |

**Verified: 6/11 checks pass at source/manual level; 3/11 runtime checks are blocked before
collection or socket creation; 2/11 reproduce new blocking findings. No pytest or real-tmux PASS
is claimed.**

### R6 Recommendations

**MUST be fixed before closing AI-CLI-1wzz or claiming class-wide verification:**

- JA-2: correct the bug record's stale claim that a baked tmux name is teardown safety evidence,
  and carry every actual prior MUST item in the next backlog.
- N-7: retain the creation-returned opaque ID through configuration, iTerm2 setup, rename, and
  attach; add a post-capture rename/reuse mutation.
- N-6/DV-1: rerun the exact real-tmux bootstrap mutation and the previously cited N-1-through-N-5
  regressions in an environment that can create temporary directories and tmux sockets. Source and
  local tmux-manual evidence are not a substitute for the explicitly required hostile runtime test.
- JA-1 remains PARTIAL until that runtime evidence is obtained. No source-level unfenced kill site
  remains at `34552a2`, but this round cannot independently establish the requested live invariant.

**SHOULD be corrected in the next audit update:**

- Update the cross-round status/log/checklist projections without rewriting prior round bodies;
  this scoped append-only worker did not alter those earlier generated/append-only regions.
- Distinguish the non-destructive N-7 identity defect from the now-source-fenced kill inventory so
  future audits do not incorrectly report a fourth kill site.

**Can be folded into a follow-up:**

- None under this round's explicit systemic mutable-name hunt. N-7 is the repeated root pattern,
  and JA-2 preserves the rejected safety rationale that enabled it.

**Closure verdict:** `AI-CLI-1wzz` is **not safe to close**. PR #135 credibly removes N-6's
name-based supervisor bootstrap in source, but the required real-tmux proof and regression suite
did not execute; Round 5's stale-rationale MUST item remains unfixed/omitted (JA-2); and the
systemic hunt found a further post-capture mutable-name identity defect (N-7).

### R6 Audit Log

| Date | Round | Notes |
|---|---|---|
| 2026-09-10 | Round 6 | Codex (`gpt-5.6-sol`, `audit`, medium): N-6 source/manual PASS but runtime BLOCKED; JA-1/DV-1 PARTIAL; prior MUST omission JA-2 and new MAJOR N-7; AST inventory remains 169 calls/21 files with only three fenced tmux-kill sites; not safe to close; audit-doc-only append. |

### R6 Files Read

- Canonical ai-harness `docs/audits/STUB.md` and `TEMPLATE.md` -- read in full before audit work,
  including taxonomy, later-round boilerplate, verification matrix, anti-patterns, and exact AD-N
  option/Pros/Cons/final-Recommendation skeleton.
- `docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md` -- full history through Round 5;
  prior uncommitted append-only content was preserved.
- Commits `34552a2` and `e15a51f` -- full metadata and diffs; current `HEAD` and relevant path
  equivalence checked.
- `src/ai_cli/session_script.py`, `main.py`, `quota.py`, `process_probe.py`,
  `tmux_ownership.py`, `stale_session_reaper.py`, `session.py`, `tunnel.py`, and
  `process_hygiene.py` -- complete AST parse plus manual inspection of every tmux/process
  identity, destructive call, and changed lifecycle region.
- `src/ai_cli/iterm2.py`, `transport.py`, and `messaging.py` -- N-7 downstream targets and
  additional full-tree destructive call contexts.
- `tests/test_stale_session_reaper.py`, `test_session_launch_integration.py`, `test_quota.py`,
  `test_process_probe.py`, `test_session_launch_shell_resolution.py`, and `test_cli.py` -- complete
  AST parse/test inventory plus manual inspection of every cited N-1-through-N-6 regression,
  fixture seam, and adjacent mutation boundary.
- `docs/bugs/cross-session-mosh-termination.md` -- full current bug record and stale hypothesis
  ledger.
- `/opt/homebrew/share/man/man1/tmux.1` -- target/ID rules and `display-message` semantics for the
  installed tmux 3.7c.

### R6 Commands Run

```bash
sed -n '<chunks>' ~/projects/ai-harness/docs/audits/STUB.md
sed -n '<chunks>' ~/projects/ai-harness/docs/audits/TEMPLATE.md
mktemp -d
tmux new-session -d -s <probe> 'sleep 5'
git rev-parse --short HEAD
git status --short
git show --format=fuller --find-renames 34552a2
git show --format=fuller --find-renames e15a51f
sed -n '<chunks>' docs/audits/ai-cli-1wzz-crosssession-mosh-kill-fix-audit.md
nl -ba <requested source-or-test> | sed -n '<evidence ranges>'
rg -n -g '*.py' '<tmux identity/destructive patterns>' src/ai_cli tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c '<AST subprocess/destructive inventory>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c '<requested-file AST/test inventory>'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c '<generated supervisor shape probe>'
sed -n '720,930p;7385,7445p' /opt/homebrew/share/man/man1/tmux.1
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -s -p no:cacheprovider -v \
  tests/test_stale_session_reaper.py::test_given_renamed_supervisor_during_ownership_bootstrap_when_clean_exit_then_replacement_survives
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -s -p no:cacheprovider -v <15 explicit N-1-through-N-6 nodes>
git diff 34552a2^ 34552a2 --check
git diff --quiet 34552a2..HEAD -- <PR-135 paths>
```
