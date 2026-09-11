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

**Loop state:** Round 2 verification projection complete — **NOT promotion-ready**. Seven of nine
Round 1 findings are verified fixed; JA-1 and DV-1 remain PARTIAL, and one new MAJOR finding is
open. This document contains an untrusted broker projection; only the adjacent trusted receipt may
assert stabilization or promotion.

| Loop | Round | Target digest | New CRITICAL | New MAJOR | MAJOR justified | New MINOR | Open blocking | Scope | Terminal |
|---|---:|---|---:|---:|---:|---:|---:|---|---|
| audit | 1 | `sha256:a8842576e99d546d67386b5c329ff5c6eed4494de4012d1e7b26944faa85c82c` | 0 | 9 | 0 | 0 | 9 | discovery | none — projection only |
| audit | 2 | `blob:c454026f066f10c7e3b3537c2acd9ab8bab44861` | 0 | 1 | 0 | 0 | 3 | verification | none — projection only |

### Run Ledger

| Field | Value |
|---|---|
| ledger-version | 2 |
| Stage cursor | `R2 verification complete; resolution required` |
| Driver lane | `verification` |
| Capability plan | `cx-audit-medium + test execution requested` |
| Promotion | `blocked: JA-1, DV-1, and N-1 open; trusted receipt pending` |
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
| I-05 | MAJOR | Bind dead-pane state and captured generation into one atomic relaunch fence; add a concurrent replacement regression | JA-1, DV-1, N-1 | Team | Round 3 resolution |

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

## Sign-Off Checklist

- [x] Canonical STUB/TEMPLATE read before authoring; repository-local absence recorded
- [x] Target commit and current relevant-path equivalence verified
- [x] Entire requested signal call-site inventory re-derived from source
- [x] Verification Matrix run on 9 findings with actual output
- [x] Already-Correct Items populated with code evidence
- [x] No inline source/doc/config fixes applied
- [x] All CRITICAL findings fixed or explicitly accepted — none found
- [ ] All MAJOR findings fixed or explicitly accepted — JA-1, DV-1, and N-1 open
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

<!-- /doc:region name="appendix_reviewer_prompt" -->
