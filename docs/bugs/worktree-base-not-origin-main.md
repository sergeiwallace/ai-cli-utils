---
title: "[BUG-004] Session worktrees branch from HEAD, not origin/main — not PR-clean, and the sync rebases the wrong commits"
category: bugs
tags: [session, worktree, git, branch, upstream, credentials]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/session-launch-non-mac-host.md
  - docs/bugs/stranded-autostash.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-004] Session worktrees branch from HEAD, not origin/main — not PR-clean, and the sync rebases the wrong commits

**Status:** fix-deployed

**Severity:** P1 — silent. Every worktree created in a repo whose main tree is parked on a
long-running branch is unfit to promote to a pull request, and its first successful sync
rebases commits it never should have carried.

**Created:** 2026-07-28

**Task:** unfiled — the `bd` binary is not installed on this machine, so no issue could be
created. The intended issue is recorded here; file it when tooling is restored.

## Symptoms

The reported symptom was a warning on every launch, and it was the *least* important part:

```text
Warning: git pull --rebase failed in worktree myproject-3 (exit 1) — starting the session
on the branch as-is (it may be behind main). Index restored to HEAD. Last git error:
fatal: Authentication failed for 'https://example.com/org/myproject.git/'
```

The session then started correctly — right worktree, right `--name`. Nothing else was
visible. The real defect had no symptom at all: the worktree branch was based on the wrong
commit, which only surfaces at push or pull-request time.

Measured in the reporting repo (a clone whose main working tree sits on a long-running
workspace branch, not `main`):

```text
main tree HEAD branch                          : user/dev-workspace
origin/main                                    : 6ab5e68
wt-myproject-1                                 : 915785e
wt-myproject-1 upstream                        : origin/main
commits on wt-myproject-1 NOT on origin/main   : 9
commits on wt-myproject-1 NOT on the workspace branch : 0
```

The branch's base and its upstream disagree: base = the workspace branch, upstream =
`origin/main`.

## Environment

- Linux (browser-hosted cloud IDE terminal), `AI_HOST` set to a non-`mac` value
- `ai-cli-utils` at `5a54a9e`
- `[session] use_tmux = false`, `[worktree] enabled = true`
- Reporting repo: an `https://` remote whose main working tree is checked out on
  `user/dev-workspace`, with `origin/main` 1 commit ahead of that branch's fork point
- git 2.43.0 / 2.55.0 (behaviour confirmed identical on both)
- The remote was unauthenticated at the time of the report. It authenticates now; the
  host's credential configuration changed mid-investigation (see Root Cause Analysis)

## Reproduction Steps

1. Clone a repo that has both `main` and a second long-lived branch on the remote.
2. In the clone, check out the second branch, so the main working tree's HEAD is not `main`
   and carries commits absent from `origin/main`.
3. Run `ai c 1`.
4. Inspect the new worktree branch:

```console
$ git rev-list --count origin/main..wt-myproject-1
9                       # expected 0 — the branch claims to track origin/main
$ git rev-parse --abbrev-ref --symbolic-full-name wt-myproject-1@{u}
origin/main
```

Reproduced from first principles in an isolated fixture, not only in the reporting repo:

```console
$ git rev-parse --abbrev-ref HEAD        # in the main tree
user/dev-workspace
$ git worktree add .worktrees/x -b wt-x  # what the launcher ran
$ git rev-parse wt-x | head -c 7 ; git rev-parse user/dev-workspace | head -c 7
<identical>
$ git rev-list --count origin/main..wt-x
2
```

## Root Cause Analysis

Causal chain:

```text
`git worktree add <dir> -b wt-<name>` is called with NO start-point
  → git creates the branch at the main working tree's current HEAD
  → base is the checked-out branch, while --set-upstream-to=origin/main sets upstream to main
  → the branch carries commits that are not on main, and `git pull --rebase` will replay them onto main
  → symptom 1: promoting the branch opens a PR full of unrelated commits (no error, ever)
  → symptom 2: the sync's rebase touches commits it should never have owned
```

`create_worktree()` did two things that only agree when the main tree happens to be on
`main`:

```python
res = subprocess.run(["git", "worktree", "add", str(wt_dir), "-b", branch], ...)   # base = HEAD
...
subprocess.run(["git", "branch", "--set-upstream-to=origin/main", branch], ...)    # upstream = main
```

`git worktree add -b <branch> <dir>` documents `<commit-ish>` as optional and defaults it to
HEAD. The intended contract is that a session worktree branches off fresh `origin/main` and
tracks it, so that a per-session branch can be promoted to a remote branch for review with
nothing in it but that session's work. Only the tracking half was implemented; the base was
left to coincidence.

**Why this stayed invisible.** Three independent maskers:

1. **The common case hides it.** In a repo whose main tree is on `main`, base and upstream
   coincide and nothing is wrong. The bug needs a repo parked on another branch.
2. **The authentication failure hid the consequence.** With an unreachable remote the pull
   fails at fetch, *before* any rebase, so the only symptom is the sync warning. **Anyone who
   repairs the credentials without repairing the base gets the rebase instead of the
   warning** — a surprising rewrite of nine commits in place of a message. Fix order matters.
3. **The tests asserted the tracking half only.** Every existing `create_worktree` test
   either mocks `subprocess.run` wholesale (so no real branch is ever created) or asserts the
   upstream. `AI-CLI-128`'s guard tests, added specifically to harden this function, check
   that `--set-upstream-to=origin/main` succeeds — which it does. A test that checks only
   the upstream passes on the buggy code and proves nothing about the base.

**Secondary finding — the authentication failure was environment, and it resolved during
the investigation (no code defect).** The host's git config configures a credential helper
per host pattern (`credential.https://<host>.helper = !<path>/gh auth git-credential`). At
the time of the report that path did not resolve; the config was rewritten mid-investigation
to point at the CLI's real location on the persistent filesystem, and the remote now
authenticates:

```console
$ git ls-remote --heads origin main   # exit captured directly, not through a pipeline
6ab5e68…  refs/heads/main
exit=0
```

Two process lessons came out of getting this wrong twice, and both are worth more than the
finding itself:

- **A shallow search is not an absence proof.** `find / -maxdepth 4 -name gh` returned
  nothing, and that was read as "the CLI is not installed". The binary was eight levels
  deep. A depth-limited search answers "not within N levels", never "not present" — the
  two outputs are identical, which is exactly the probe-adequacy failure to avoid.
- **Environment findings expire.** The credential config's mtime moved *after* it was first
  measured in this same session, so a correctly-measured fact became false while the work
  was in flight. Re-measure anything load-bearing immediately before relying on it.

The authentication state is host configuration, not an `ai-cli-utils` fault, and nothing in
the host's git configuration was changed by this fix — see Fix.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-07-28 (`fa2e21b`) | Replaced the sync warning's hard-coded "(autostash pop conflict?)" guess with git's own error line | Correct and kept — it is what made the authentication failure visible at all. Did not look at the worktree's base. |
| 2 | 2026-07-28 (`5a54a9e`) | `_pull_refspec()` — pull `origin <branch>` explicitly when a branch has no upstream | Correct and kept. Unrelated to this defect: these branches *do* have an upstream; it is the base that is wrong. |
| 3 | 2026-07-28 (`a653fbd`, AI-CLI-128) | Made `create_worktree` hard-fail when `--set-upstream-to=origin/main` cannot be set | Correct and kept, but it hardened the tracking half only, leaving base and upstream free to disagree. |
| 4 | — | Considered: silence or rate-limit the sync warning | Rejected. The warning was reporting a genuine condition; suppressing it would have hidden both the credential fault and this defect. Attempt 1 exists precisely because a *guessed* cause displaced git's real message. |

## Fix

`src/ai_cli/session.py`:

- New `_resolve_worktree_base(repo_root)` resolves `refs/remotes/origin/main` and returns it
  as the start-point. It **raises `RuntimeError` when that ref cannot be resolved** — no
  fallback to HEAD, since the silent fallback is the defect. This mirrors AI-CLI-128's
  upstream hard-fail: a worktree branch that is not anchored to `main` fails later and more
  confusingly, at push or PR time. A repo with no `origin/main` would be rejected by the
  upstream guard moments later anyway, so the new failure is earlier, not additional.
- `create_worktree()` passes that base to `git worktree add <dir> -b <branch> <base>`, and
  resolves it *before* creating any directory, so an unresolvable base leaves nothing behind.
- The existing fallback path (`git worktree add <dir> <branch>`, taken when the branch
  already exists) deliberately keeps passing **no** start-point: an existing `wt-<name>`
  carries a previous session's commits, and forcing it to `origin/main` would discard them.
  Covered by its own test.
- The base is read from the remote-tracking ref **without fetching**. The launch already
  runs `git pull --rebase` inside the new worktree, which is what makes it current; making
  base resolution depend on the network would turn an unreachable remote into a refused
  launch rather than a slightly-behind worktree.

`--set-upstream-to=origin/main` and its AI-CLI-128 hard-fail are unchanged.

**Deliberately not changed:**

- **The sync warning.** It reports a real condition and now carries git's own error text.
  Quietening it would have hidden this defect (see masker 2) and would regress `fa2e21b`.
- **Skipping the pull for a known-unauthenticated remote.** Rejected: reachability is not a
  stable property, the check costs the same network round-trip it would save, and a launch
  that silently stops syncing is a worse failure than a visible warning. The launch already
  proceeds on failure, so the cost is one failed fetch, not a broken session.
- **The host's git configuration.** Credential setup is the user's global config, and it now
  works; a tool must not rewrite a user's global git config to work around its own defect.
- **Anything that pushes.** A session worktree stays local until someone deliberately
  publishes it, so no automatic push was added and no push configuration was touched.
  `push.default` is unset on this host, which is why a bare `git push` from a worktree
  branch tracking `origin/main` refuses rather than guessing — see the appendix.
- **Existing worktrees created with the wrong base.** Several are live and may hold
  uncommitted work. Re-basing or deleting them is a migration decision for the user, not
  something a bug fix should do silently.

## Verification

- [x] New regression test asserts the base against **real git repositories**, in a fixture
      whose checked-out branch is not `main` and which has commits absent from `origin/main` —
      the only shape that discriminates the defect
- [x] Confirmed RED before the fix, for the right reason:
      `AssertionError: wt-session-1 must add no commits to origin/main, but is 1 ahead`
- [x] Confirmed the test is coupled to the fix: removing the start-point again turns it RED
      and restoring it turns it GREEN
- [x] Negative constraints asserted, not just the positive one — the workspace branch's
      commit and its file are absent from the new worktree, and the branch the main tree is
      on is neither moved nor checked out elsewhere
- [x] A test proves an existing `wt-<name>` keeps its own commits when its worktree
      directory is recreated (guards against "force every add to origin/main")
- [x] A test proves an unresolvable `origin/main` raises and leaves no worktree directory
      behind (guards against reintroducing a HEAD fallback)
- [x] Upstream tracking still `origin/main` (AI-CLI-128 unregressed)
- [x] `ruff check` + `ruff format --check` clean
- [x] Full suite: **1965 passed, 7 skipped, 0 failed** (baseline before the change: 1961
      passed, 7 skipped — the 4 new tests account for the difference)
- [x] Nine pre-existing `create_worktree` tests that mock `subprocess.run` wholesale were
      updated to stub the base lookup; they assert unrelated behaviour and the real
      start-point behaviour is covered against real git instead
- [x] Resolver run read-only against the reporting repo: returns `refs/remotes/origin/main`
      resolving to the same commit as `origin/main`, while that repo's HEAD is a different
      commit on the workspace branch

**Environment checks** (user action, outside this repo — no writes):

```console
# Which credential helper applies, and from which file
git config --get-regexp --show-origin '^credential\.'

# Does the remote authenticate? Read-only, and captures git's own exit status
git -C <repo> ls-remote --heads origin main ; echo "exit=$?"

# push.default in every scope (an unset value here is why a bare push refuses)
for s in --system --global --local; do git -C <repo> config $s --get-all push.default; done
```

## Lessons Learned

- **When two settings must agree, test that they agree — not that each was set.** The
  upstream was asserted, the base was not, so a function whose whole purpose is "branch off
  main and track main" satisfied half its contract with full test coverage. Any invariant
  spanning two calls needs an assertion that spans both.
- **An optional argument with a convenient default is a silent policy decision.** `git
  worktree add -b <branch>` defaults the start-point to HEAD, which is *usually* right and
  therefore never questioned. Where the default only coincides with the intent, state the
  intent explicitly.
- **A loud unrelated failure can mask a silent related one, and fixing the loud one first
  makes things worse.** The authentication failure was the only visible symptom, and it was
  also what stopped the wrong-base rebase from running. Repairing credentials alone would
  have replaced a warning with a nine-commit rebase.
- **Do not silence a signal you have not finished reading.** The obvious response to a
  warning on every launch is to quieten it; that warning was the only thread leading to this
  defect. `fa2e21b` had already fixed the inverse mistake — a guessed cause displacing git's
  real one.
- **Fixture realism decides whether a test can fail.** Every wholesale-`subprocess.run` mock
  in this area passes identically on the buggy and fixed code, because no branch is ever
  created. A defect about which commit a branch starts at can only be caught by a test that
  creates a real branch in a real repository shaped like the reported one.
- **An environment measurement has a shelf life, and it can be shorter than the task.** The
  credential configuration examined at the start of this investigation had changed by the end
  of it — same session, same machine. A fact that was correctly measured can still be false by
  the time it is acted on, so re-measure immediately before anything load-bearing rather than
  carrying the earlier reading forward.
- **State what the probe would print if the conclusion were wrong, before reporting it.** Two
  probes here failed that test: a depth-limited `find` whose empty output is identical for
  "absent" and "deeper than the limit", and a `git credential fill` invocation that exited 0
  for a broken and a working helper alike. Both produced a confident wrong answer, and both
  were caught only by asking what the falsifying output would have looked like.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-07-28 — worktree base pinned to origin/main

`src/ai_cli/session.py` (`_resolve_worktree_base()` added; `create_worktree()` passes the
start-point). Tests: `tests/test_worktree_base.py` (new, 4 tests against real repositories),
`tests/test_session.py` (nine pre-existing mocked tests given a base stub, plus an assertion
that the existing-branch fallback passes no start-point).

Diagnosis and fix performed in-session rather than delegated: the discriminating evidence was
a live measurement of one repository's branch topology, and the fix is a single call site.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

### Evidence — the start-point default, isolated from the reporting repo

Fixture: clone whose main tree is on `user/dev-workspace` (2 commits off the fork point),
with `origin/main` 1 commit further on.

```text
main-tree HEAD branch     : user/dev-workspace
wt-x tip subject          : dev2
origin/main tip subject   : m2
wt-x == user/dev-workspace: YES
wt-x == origin/main       : NO
wt-x ahead of origin/main : 2
wt-x behind origin/main   : 1
```

Then, with a reachable remote, the sync does exactly what the warning had been hiding:

```text
$ git pull --rebase --autostash
Successfully rebased and updated refs/heads/wt-x.
$ git log --format='%s'
dev2
dev1
m2
m1
```

Two commits belonging to the workspace branch were replayed onto `main`.

### Evidence — the start-point probe discriminates

The base resolver's `git rev-parse --verify --quiet refs/remotes/origin/main` was checked
against all three cases it must tell apart:

```text
case A: no remote at all              → exit=1  stdout empty
case B: cloned, origin/main exists    → exit=0  stdout = sha
case C: remote exists, default branch is `trunk`, no origin/main
                                      → exit=1  stdout empty   (origin/HEAD -> origin/trunk)
case D: worktree add -b B <dir> refs/remotes/origin/main
                                      → exit=0  ahead of origin/main = 0
```

Case C is the open question recorded below, not a defect in the probe.

### Evidence — pushing a worktree branch fails safe, and stays manual

With `push.default` unset (checked in all three scopes: system, global, local — all unset),
a bare `git push` from a `wt-<name>` branch whose upstream is `origin/main` refuses rather
than guessing. Measured against a local bare remote:

```text
push.default effective     : <unset>
bare `git push` exit        : 128
  fatal: The upstream branch of your current branch does not match …
  (git then suggests both `HEAD:main` and `HEAD`)
remote main moved           : no (failed safe)
remote branch wt-x created  : no
```

This matters for two reasons. It confirms the wrong base could not have leaked to the remote
by accident, and it confirms that pinning the base changes nothing about publishing: a
worktree stays local until someone runs an explicit refspec. Note git's *first* suggestion in
that message pushes the branch to `main` directly — the more destructive of the two — so the
refusal is protective, not merely inconvenient.

### Evidence — two probe failures worth recording

Both are probe-adequacy failures made during this investigation, kept because the wrong
conclusion each produced was confident and plausible:

```console
# 1. Depth-limited search read as an absence proof. The binary was 8 levels deep;
#    "not found within 4 levels" and "not installed" print identically.
$ find / -maxdepth 4 -name gh -type f -perm -u+x    # no output — proves nothing

# 2. `git credential fill` probe that fed its own answer in on stdin, so a broken
#    helper and a working helper both exited 0. Discarded rather than reported.
$ printf 'protocol=https\nhost=example.com\n\n' | git -c credential.helper='!/nonexistent' credential fill
exit=0    # ...and exit=0 for a WORKING helper too. Cannot discriminate.
```

### Resolved — repos whose default branch is not `main`

This section recorded `main` being hardcoded, and the resulting inability of a repository on
`master` or `trunk` to create a session worktree at all, as an open question. **It is now
resolved.**

Both the base and the upstream resolve to the repository's *integration branch*: the
`[worktree_upstream]` config entry for the repository if it has one, otherwise the branch the
repository's main checkout is on. A repository on `main` is unaffected. Neither the base nor
the upstream is hardcoded any longer, so no repository name is special-cased in source.

Resolution deliberately does **not** read `origin/HEAD`, which was the option floated here.
`origin/HEAD` records the remote's *default* branch, which answers a different question than
"what does this repository integrate through" — it is a cached local ref that can be stale or
absent, and in the reported case it pointed at `main` while the work integrated elsewhere, so
it would have reproduced the bug.

Where resolution fails, the worktree is created with **no upstream** and a warning rather
than falling back to `origin/main`; a branch that exists nowhere, or a missing `origin`
remote, fails loudly. Details and rationale: `tests/test_worktree_upstream.py`.

<!-- /doc:region name="appendix_evidence" -->
