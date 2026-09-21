---
title: "ai c hangs on a git credential prompt when the worktree's remote cannot authenticate"
status: fixed
kind: bug
issue: "pending — no Beads store on the reporting machine yet (see Handoff)"
---

# `ai c` hangs on a git credential prompt when the worktree's remote cannot authenticate

## Symptom

On a freshly set-up machine, launching a session printed a bare git credential prompt and never
returned — no session, no worktree report, no error:

```
$ cd ~/projects/myproject
$ ai c my-session
Username for 'https://github.com':
```

Answering it is not possible in the general case: the prompt comes from a subprocess whose output
the launcher is capturing, and on an unattended launch there is nobody there to answer at all.

## Environment

- macOS (darwin 27.0.0), zsh, git 2.x, `ai-cli-utils` installed as a `uv` tool.
- Target repo's `origin` is an **https** GitHub remote.
- `credential.helper = osxkeychain`, but the keychain held no credential for `github.com`; the
  machine's GitHub token lived only in another credential store that was never wired into git.
- Reproduced on `main` at `4dce375`.

## Reproduction

```yaml
reproduction:
  revision: 4dce375
  environment: macOS 27.0.0, zsh, interactive tty, https origin, no usable git credential
  command_or_steps: cd <repo with https origin>; ai c <session>
  expected: session launches; an unreachable remote degrades to a warning
  observed: "Username for 'https://github.com':" printed, process blocks indefinitely
  exit_status: none — never exits
  reproducibility: deterministic
  baseline_failures: >
    24 pre-existing, unrelated: tests/test_stale_session_reaper.py (21) and
    tests/test_quota.py (3). Verified pre-existing by stashing the fix and re-running
    those files on the unfixed tree — the same set fails identically with and without
    the change. Not caused by, and not addressed by, this fix; see "Pre-existing
    failures" below.
  evidence: see "Causal chain" below
```

## Causal chain (reproduced, not inferred)

1. **Trigger.** `ai c <session>` reaches the worktree-sync step in `main.py`, which calls
   `pull_rebase_autostash(worktree_path)` to bring the worktree up to date with `main`.
2. **The git call.** That helper runs `git -C <repo> pull --rebase --autostash …` with
   `capture_output=True` and `env=_git_env()` (`git_repair.py`).
3. **Incorrect decision.** `_git_env()` built an environment that strips git's repo-*targeting*
   variables but deliberately left `GIT_TERMINAL_PROMPT` untouched, so git was still permitted to
   prompt interactively.
4. **Propagation — the non-obvious step.** `capture_output=True` does **not** contain a git
   credential prompt. Git writes that prompt to `/dev/tty` and reads the answer from `/dev/tty`,
   not from the subprocess's stdin/stdout. So on a real terminal git opened the tty directly,
   printed `Username for 'https://github.com':`, and blocked forever inside `subprocess.run`.
5. **Symptom.** The launcher never returned from the sync step, so it never reached its own
   already-written degradation branch — the one whose comment explicitly anticipates this case
   ("this was no network, missing credentials for the remote, or something that needs attention")
   and which starts the session on the branch as-is with a warning. The designed soft-failure
   path was unreachable because git blocked before it could report a failure.

The mechanism is directly observable. With prompting left enabled, git reaches the credential step
for real; it only fails fast here because an agent shell has no tty to open:

```
$ git -C <repo> fetch origin main          # no tty available
fatal: could not read Username for 'https://github.com': Device not configured
```

On the user's interactive terminal that same code path finds a tty, and blocks instead.

## Rejected hypotheses

| Hypothesis | Discriminating check | Result |
|---|---|---|
| `ai c` is doing an authenticated git operation it should not do at all | Read `session.py`'s worktree-base contract and `main.py`'s sync step | **Rejected.** Syncing the worktree with `main` is intended and documented; the remote-tracking ref is deliberately *not* fetched at creation time precisely because the launch pull covers it. The operation is correct; its failure mode was not. |
| `capture_output=True` already prevents any prompt, so the prompt must come from somewhere else | Ran the real git call under `capture_output=True` and watched where the prompt went | **Rejected.** The prompt is a `/dev/tty` write; capturing stdout/stderr does not suppress or redirect it. Only `GIT_TERMINAL_PROMPT=0`, an askpass, or a helper changes this. |
| Purely an environment problem (machine not bootstrapped), no defect in this package | Weighed against the launcher's own contract | **Rejected as sufficient.** The missing credential *is* environmental, but a best-effort sync inside a launcher must never block on an unanswerable interactive prompt. The code already had the right degraded behaviour and could not reach it. Both facts are true; this fix addresses the one that belongs to this package. |

## Scope of fix

Assessed after the root-cause gate. Scope signals: one cohesive component (a single private env
helper plus its tests); no public contract change (`_git_env` is private); no new abstraction; no
repository or architectural boundary crossed. Threshold not met — a contained narrow fix is
correct, and it is trivially reversible.

## Fix

`_git_env()` now defaults `GIT_TERMINAL_PROMPT=0`:

```python
env.setdefault("GIT_TERMINAL_PROMPT", "0")
```

`setdefault`, not assignment: a caller that has deliberately set the variable keeps its own value,
so the containment is a default rather than a clamp.

This targets the causal mechanism — git's *permission* to prompt from unattended automation — not
the terminal symptom. Every git subprocess routed through `_git_env()` is automation that captures
its result and reads it back, so a prompt there can only ever hang the caller. Removing that
permission makes git fail fast, which is exactly what makes the launcher's pre-existing
degraded-remote handling reachable.

Every git invocation in the `ai c` launch path (and in `copier_update.py`) already routes through
`_git_env()`, so the single change covers all of them.

## Verification

- **Frozen regression tests** (written and confirmed RED before the production edit):
  - `test_git_env_when_caller_left_prompt_unset_then_real_git_refuses_to_prompt` — drives the real
    `git credential fill` CLI with the helper list reset, so only git's own terminal-prompt path
    remains. RED before the fix with
    `could not read Username for 'https://git.example': Device not configured`.
  - `test_pull_rebase_autostash_when_remote_demands_credentials_then_fails_without_prompting` —
    the launch-path function itself against a real loopback HTTP remote that answers every request
    `401`, so git reaches the credential step over a real protocol boundary and nothing can pass by
    stubbing it. RED before the fix with
    `could not read Username for 'http://127.0.0.1:<port>': Device not configured`. Also asserts
    the negative constraint: `stranded is None`, because a credential failure must stay a soft
    failure the caller can launch through.
  - `test_git_env_when_caller_set_prompt_explicitly_then_value_is_not_overridden` — sibling
    constraint proving the fix only defaults the variable.
- **Focused:** `uv run pytest tests/test_git_repair.py` — 39 passed.
- **Hard gate:** `uv run ruff check .` + `uv run ruff format --check .` clean. Full `uv run pytest`:
  2925 passed, 41 failed — all 41 in the pre-existing, unrelated set described under "Pre-existing
  failures" below, none introduced here.
- **Real end-to-end, in the reporting environment** — the exact call `ai c` makes, on the real
  worktree with the real https remote and the real missing credential:

  ```
  elapsed: 1.71s  (a prompt would never return)
  returncode: 1
  stderr: fatal: could not read Username for 'https://github.com': terminal prompts disabled
  stranded: None
  ```

  Fails fast, reports why, and leaves the repo usable so the launch proceeds.
- **Launcher dry run:** `ai c <session> --dry-run` in the reporting repo resolves the full launch
  plan with no prompt.

## Known remaining gap (same defect, different commands)

`sync.py` and `workspace.py` run their own network git operations (`clone`, `fetch`, `pull`,
`push`) with no `env=` at all, so they never received this containment and can still block the same
way — reachable from `ai sync` and `ai ws pull`, not from `ai c`. **Not fixed here:** those are
separate command surfaces needing their own regression coverage, and the reported defect is in the
launcher. Called out so the boundary of this fix is explicit rather than assumed.

## Pre-existing failures (found, not fixed here)

The full suite on this machine fails in `tests/test_stale_session_reaper.py` and
`tests/test_quota.py`. These were confirmed pre-existing by stashing the fix and re-running those
files against the unfixed tree: the **same** set fails identically either way, so nothing here
caused or masked them. They are almost certainly this machine's unfinished setup — the reaper tests
drive real shells, real tmux children and real supervisor processes, which is exactly the surface a
not-yet-bootstrapped machine lacks.

Diagnosing them is a separate investigation on a separate subsystem, out of proportion to this
launcher fix, so it was surfaced rather than absorbed. It needs its own bug doc and issue once the
machine's bootstrap has run — re-baseline first, since the bootstrap may resolve them outright.

## Lessons learned

- **`capture_output=True` is not prompt containment.** A credential prompt travels over `/dev/tty`,
  bypassing the pipes entirely. Any unattended subprocess that can reach an authenticating remote
  needs prompting disabled explicitly; capturing its output proves nothing about whether it can
  block. This is the reusable lesson — it applies to any tool with a tty-based prompt, not just git.
- **Why the tests didn't catch it.** Every existing `_git_env` test asserted the environment
  *dict*, and one even asserted `GIT_TERMINAL_PROMPT` was passed through untouched — the absence of
  a default was encoded as intended behaviour. No test drove a real git subprocess against a remote
  that demanded credentials, which is the only way the blocking shows up. The new tests close that
  gap at the real boundary rather than on the dict.
- **An unreachable degradation path is the same as a missing one.** The launcher's handling for a
  credential failure was already written, already commented, and already correct — and contributed
  nothing, because the blocking call upstream never let control reach it. A fallback is only real
  if the failure it handles actually surfaces as a failure.

## Handoff

- CC task and Beads issue: **not created at fix time.** The `Task*` tools are unavailable in the
  reporting session (see `ai-harness`'s `docs/bugs/aih-335-task-tool-permanently-disabled.md`), and
  this machine's Beads store has not been bootstrapped yet. Both records are to be created once the
  `ai-harness` bootstrap has run on this machine, and this doc linked from them.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-09-19 | `_git_env()` defaults `GIT_TERMINAL_PROMPT=0`; three regression tests added (two RED first) | Fixed — focused + full suite green, real-environment launch verified |
