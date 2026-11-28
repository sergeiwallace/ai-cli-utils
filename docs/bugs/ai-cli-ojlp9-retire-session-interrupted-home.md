---
title: "retire-session could not remove a session home whose worktree creation was interrupted"
category: bugs
tags: [session, retire-session, worktree, git, locked-worktree, process-residency]
status: fix-deployed
severity: P2
related_docs:
  - docs/tools/cc-session-adoption.md
---

# retire-session could not remove a session home whose worktree creation was interrupted

When a launch is killed while `git worktree add` is still checking out a new session home, git
leaves the checkout behind half-made: its admin dir holds a `locked` file whose reason is
`initializing`, there is no index, and only some of HEAD's files are on disk, the last one often
cut off part way. The session never starts, so nothing in the checkout is anyone's work.

`ai retire-session <name>` is the sanctioned way to remove a session home, but it ran a plain
`git worktree remove`. Git refuses to remove a locked worktree unless `--force` is given twice, so
retire-session could not remove these homes, and nothing else was allowed to.

## Symptoms

- Two session homes on one host were left this way by launches killed about 40 seconds into
  worktree creation. Each had no index, a `locked` file reading `initializing` and an
  `index.lock`. Each had exactly one file on disk, a strict prefix of its HEAD blob (0 of 2580
  bytes and 16384 of 43214 bytes), and no commit of its own.
- The host's worktree reaper classes a session home as canonical and defers it to
  `ai retire-session`, which then failed on the lock. The homes could not be removed by any
  sanctioned route.

## Causal mechanism

`retire_session` treated every checkout the same way: anchor the tip, then
`git worktree remove`. A locked worktree needs `git worktree remove --force --force`, and
passing that for any locked checkout would remove a checkout that a live process had locked on
purpose. So the fix is not the flag alone. It is a proof that the checkout is dead, and the
double force only when the proof holds.

## The fix

PR #726 adds the dead-checkout proof to
`src/ai_cli/session_retire.py`. A locked checkout is removed with `--force --force` only when every
check holds, and is refused otherwise, before anything is written, with a message naming each
check that failed:

1. The lock reason is exactly `initializing`.
2. The admin dir has been unchanged for `DEAD_CHECKOUT_QUIET_SECONDS` (60).
3. No live process has its working directory, an open file, a mapped file or its executable
   inside the checkout or its admin dir.
4. HEAD is contained in the target.
5. Every file on disk is HEAD's bytes or a strict prefix of them, and no file is outside HEAD's
   tree.

PR #727 follows up on check 5. A
post-checkout hook in the host repository had already linked `.venv` to a shared environment
store, so the first live dry run refused the home on `.venv is not in HEAD's tree`.

Behaviour tests in `tests/test_session_retire.py` drive `retire-session` against real interrupted
checkouts. They cover removal when the proof holds and refusal, naming the check, for each check
that fails.

## Decisions

### D-1: An unreadable process passes the residency check only on positive evidence — `✅ Resolved by engineer (Claude): (b) narrowed fail-closed`

**Context.** Check 3 walks every process on the host. On macOS a large share of system processes
deny this user their details, so a rule that fails the check on any unreadable process could never
pass there, and no interrupted home could ever be retired.

**Options.** (a) Fail closed on any unreadable process. (b) Treat an unreadable process as out of
reach only when one detail that can be read proves it: it started before the checkout existed, it
runs as another user, or its executable is in one of the system directories in
`OS_EXECUTABLE_DIRS`. Any other unreadable process still fails the check. (c) Skip unreadable
processes.

**Resolution.** (b). It keeps the check fail-closed for everything that could be the session's own
launch, which runs as this user after the checkout exists, and lets the check pass on a stock
macOS host. (c) would pass a process that could be the launch itself.

> **Decision:** ✅ Resolved — (b) narrowed fail-closed
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=engineer -->

> **Post-build review:** requirement-change: the brief asked that any unreadable process fail the residency check, which can never pass on macOS, so an unreadable process passes only on one readable detail that proves it is not the launch.

### D-2: A symlink that resolves outside the checkout is accepted and unlinked — `✅ Resolved by engineer (Claude): (a) accept and unlink`

**Context.** A provisioning hook can create a symlink in a fresh checkout (a `.venv` pointing at
a shared store). It is not in HEAD's tree, so check 5 refused it.

**Options.** (a) Accept a symlink whose target resolves outside the checkout. Removal unlinks it
and never touches its target. A symlink that resolves inside the checkout is still refused, since
it could stand in for content. (b) Keep refusing every path outside HEAD's tree.

**Resolution.** (a). The link holds no content of the checkout, and `git worktree remove` deletes
the link, not what it points to. A test pins that the target survives.

> **Decision:** ✅ Resolved — (a) accept and unlink
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=engineer -->

### D-3: The proof is implemented in this package, not shared with an external reaper — `✅ Resolved by engineer (Claude): (a) own implementation`

**Context.** A host-side worktree reaper carries an equivalent proof for non-session checkouts.

**Options.** (a) Implement the proof here with the same checks. (b) Depend on the external
reaper's implementation.

**Resolution.** (a). This package is public and must not depend on a private tool. The checks
match the reaper's, so a checkout either tool calls dead is dead to both.

> **Decision:** ✅ Resolved — (a) own implementation
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=engineer -->

### D-4: The quiet window is a module constant — `✅ Resolved by engineer (Claude): (a) constant, 60 s`

**Context.** Check 2 needs a quiet window long enough that a checkout still being written is never
taken for dead.

**Options.** (a) A module constant, `DEAD_CHECKOUT_QUIET_SECONDS = 60`, matching the external
reaper's window. (b) A configuration setting.

**Resolution.** (a). The window is a safety floor, not a preference: a shorter one weakens the
proof, and a longer one only delays a retirement that is asked for by hand. No caller needs to
change it.

> **Decision:** ✅ Resolved — (a) constant, 60 s
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=engineer -->

## Verification

- PR 726 and PR 727 each passed all 21 CI checks on Linux, macOS and Windows.
- On the affected host, `ai retire-session --dry-run` and then `-y` retired both homes. Each tip
  was anchored under `refs/preserved/retire-session/`, each branch was kept, and
  `git worktree list` no longer lists either checkout.

## Prevention lesson

A removal that needs force must carry its own proof that force is safe, and the proof must say
which check failed when it refuses. A plain removal that fails on a lock leaves the caller with
nothing but a wrong tool.
