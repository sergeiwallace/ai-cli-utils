---
title: "Session launch hard-depends on zsh and direnv — `ai c` prints only `[exited]` on a host without them"
category: bugs
tags: [session, tmux, zsh, direnv, portability, linux, launch]
status: fix-deployed
severity: P0
template_version: "bug-1.0.0"
related_docs:
  - docs/bugs/session-launch-non-mac-host.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session launch hard-depends on zsh and direnv

**Status:** fix-deployed

**Severity:** P0 — `ai c` could not start a session at all on an affected host.

**Created:** 2026-08-15

## Symptoms

On a freshly provisioned Fedora Linux 44 workstation (bash login shell, tmux 3.6a, no `zsh`,
no `direnv`), every session launch failed with a single unhelpful line:

```console
$ ai c 1
[exited]
```

No error, no exit status worth reading, nothing naming a missing dependency. The same command
worked unchanged on every macOS and Debian host in the fleet.

A second, distinct failure mode was reachable on a host that has zsh but not direnv: the
session started, the agent never did, and the script stopped with

```text
Error: agent command did not complete successfully under direnv for <root>. If direnv denied
or could not evaluate .envrc, run 'direnv allow <root>' and correct the reported error.
AI CLI exited too quickly (0 s) — stopping. Run 'ai c' to retry.
```

which sends the operator to a trust prompt for a binary that is not installed.

## Environment

| Field | Value |
|---|---|
| Host | Fedora Linux 44 workstation — the fleet's first non-Debian Linux box and first Linux personal workstation |
| Shell | bash (login) |
| tmux | 3.6a |
| `zsh` | **absent** |
| `direnv` | **absent** |
| ai-cli-utils | `0.7.0.post20260815145304`, source at `6dd0476` |

## Reproduction Steps

Defect A — the pane interpreter. tmux reports success and then quietly discards the session:

```console
$ tmux new-session -d -s zshprobe -- zsh /bin/true ; echo "rc=$?" ; tmux ls
rc=0
zshprobe: 1 windows (created ...)
$ tmux ls
no server running on /tmp/tmux-1000/default
```

The non-zero-exit signal an operator would look for never appears: `new-session` returns 0
because tmux successfully *created* the pane; the interpreter's `exec` fails afterwards, inside
the pane, and the teardown races the very first `tmux ls`.

Defect B — the direnv wrapper. Run the real `run_agent` from the real generated template under
a real bash with no direnv on PATH:

```console
$ bash -c 'direnv_root="$PWD"; agent_direnv_blocked=false; source ./run_agent.sh;
           run_agent /usr/bin/touch marker; echo "run_agent_rc=$?"'
./run_agent.sh: line 2: direnv: command not found
Error: agent command did not complete successfully under direnv for <root>. If direnv denied
or could not evaluate .envrc, run 'direnv allow <root>' ...
run_agent_rc=127
marker exists: no
```

Both are deterministic. Baseline: the full suite was green at `6dd0476` before any edit, so
neither failure is an unrelated pre-existing break.

## Root Cause Analysis

Two independent unguarded hard dependencies on the launch path. They are separate causes with
the same trigger (a host missing an assumed tool), so they are recorded as a two-branch fault
tree rather than forced into one chain.

**A. The tmux pane interpreter was hardcoded to `zsh`.**

```text
`ai c` on a host without zsh
  → tmux is handed `-- zsh <script>` (main.py), argv naming a program that does not exist
  → `new-session` still exits 0: the pane was created, the exec fails inside it
  → the pane dies, tmux tears the session down, the server exits
  → the immediately-following `tmux attach-session` finds nothing and prints `[exited]`
```

Five sites, none guarded: the detached `new-session` and its no-`--` Mac retry, the three
`--once` argv builders, plus the two `exec zsh "$_script_stable_path"` hot-reload lines in the
generated template. The last pair matters independently: a fix that changed only the launcher
would have started the session and then killed it on its first self-update.

The generated template carries no zsh-specific syntax — only `[[ ]]`, `(( ))` and POSIX
builtins — so bash runs it unmodified. Verified two ways rather than by reading: `bash -n`
parses the generated 25KB script clean, and the template's own self-update branch already
`exec bash`-ed a refreshed template, so bash execution was in fact already a supported path.

**B. `direnv exec` was unguarded.**

```text
`ai c` on a host without direnv
  → `run_agent` (generated template) runs `direnv exec "$direnv_root" "$@"`
  → `direnv exec` fails closed: exit 127, the agent binary is never executed
  → the script's own `elapsed < 3` guard fires and stops the session
  → the message blames an .envrc *approval*, naming a trust prompt for an absent binary
```

The same hardcode sat in the three `--once` argv builders in `main.py`, which never reach the
generated script at all.

This is a defect, not a missing feature, because the sibling implementation in the same
codebase already states and enforces the correct invariant. `main._exec_with_direnv()` — the
**bare** launch path — carries the docstring *"direnv is an enhancement, never a precondition
for starting a session"* and handles the no-`.envrc`, usable, blocked, and not-installed cases
explicitly, with `_direnv_installed()` already present as a helper. The tmux path simply never
adopted it. (Differential comparison against a working sibling — the technique router's entry
for exactly this shape.)

### Hypothesis ledger

| Hypothesis | Predicted observation | Check performed | Result |
|---|---|---|---|
| H1: tmux itself fails on this host/version | `tmux new-session` returns non-zero, or a plain session cannot be created | Ran `new-session` with a valid interpreter on an isolated socket | **Rejected** — sessions created and survived; only the zsh-interpreter case died |
| H2: the pane interpreter is missing | `new-session -- zsh …` exits 0 yet leaves no server | Isolated probe above | **Confirmed** |
| H3: the session script needs zsh-only syntax, so bash cannot substitute | `bash -n` reports a syntax error on the generated script | `bash -n` on the real 25KB generated script | **Rejected** — `BASH_SYNTAX_OK`; later confirmed by running the whole template under bash in real tmux |
| H4: direnv failure is an approval/trust problem, as the message claims | `direnv allow` would resolve it | `command -v direnv` on the host | **Rejected** — the binary is absent; the message is misleading, not diagnostic |

### Scope-of-fix decision (procedure Step 2.5)

Scope signals: three-or-more unrelated subsystems — **no** (two files in one cohesive
component); new shared abstraction — **no** (one small resolver plus a factored-out helper
reusing the existing policy); public contract change — **no** (CLI surface unchanged; the
documented requirement list gains an entry it should always have had); repository/architectural
boundary — **no**; broader pattern flagged elsewhere — **partly** (the remote SSH/mosh paths
hardcode `zsh -l -c` too, recorded below as out of scope). One partial signal, so the off-ramp
threshold is not met. Decision Framework: the change is fully reversible and its blast radius
is confined to the launch argv, which the criteria resolve in order without reaching the
weighted comparison. Narrow causal fix retained.

## Prior Fix Attempts

None. This is the first investigation of this defect; no earlier patch was attempted, and no
fix attempt was rejected during this one.

## Fix

`src/ai_cli/session_script.py`

- `SESSION_SHELL_PREFERENCE = ("zsh", "bash")` and `resolve_session_shell()` — resolves the
  interpreter to an absolute path by preference, returning `None` when neither exists. zsh
  remains **preferred** wherever installed, so hosts that have it are behaviourally unchanged;
  this resolves the hardcode rather than swapping it.
- The resolved path is baked into the template, so both hot-reload `exec`s and the self-update
  `exec` use it. The self-update branch's `exec bash` was the same class of hardcode inverted
  (it would break a host with zsh but no bash) and now uses the resolved shell too.
- `run_agent` guards `direnv exec` behind `command -v direnv`, running the agent directly
  otherwise, and no longer emits the approval message when direnv was never used.

`src/ai_cli/main.py`

- `_session_shell_or_exit()` wraps the resolver with an actionable error naming the missing
  dependency, rather than handing tmux a pane command it cannot exec.
- `_direnv_prefix()` factors the "enhancement, never a precondition" decision out of
  `_exec_with_direnv()` so the three `--once` sites reuse the identical policy instead of
  hardcoding `direnv exec`. No parallel implementation was introduced.
- All five interpreter sites and all three `--once` direnv sites now call these.

`README.md` — the Requirements list never mentioned either tool, which is part of why the
hidden dependency went unnoticed. It now states the zsh-or-bash contract and that direnv is
optional.

**Deliberately not changed (out of scope):** the remote SSH/mosh paths
(`main.py`'s `zsh -l -c` / `mosh … -- zsh -l -c` / `os.execvp("zsh", …)`) still hardcode zsh.
They target the *remote* host, not this one, and were excluded from this task's boundaries.
They will fail the same way against a zsh-less remote.

## Verification

Widening rings, all re-run against the final diff:

1. **Frozen regression suite** — `tests/test_session_launch_shell_resolution.py`, 4 tests
   written and confirmed RED **before** any production edit, later extended to 6 with the
   `--once` pair (also confirmed RED against stashed-out `src/` before being kept). Each
   behaviour is pinned under both conditions, tool-absent and tool-present, via a hermetic
   symlink-only PATH, so nothing passes merely because this host lacks the tools. No assertion
   greps for the string `zsh` or `direnv`; a naive hardcode swap fails the tool-present cases.

   RED, on the unfixed code:

   ```text
   AssertionError: session script never executed — the tmux pane died instead of starting,
   which is exactly the `[exited]` symptom
   AssertionError: run_agent did not succeed without direnv: stdout='RUN_AGENT_RC=127\n'
   stderr="… direnv: command not found …"
   AssertionError: --once handed tmux an interpreter it cannot exec: 'zsh' — the pane would
   die immediately and show only `[exited]`
   ```

2. **Revert round trip** — `git stash push -- src/` returned the suite to `2 failed, 2 passed`
   (and `4 failed, 2 passed` once the `--once` pair was added); `git stash pop` returned it to
   green. The tests are coupled to the fix, not incidentally passing.

3. **Focused, after the fix** — `uv run pytest tests/test_session_launch_shell_resolution.py`
   → `6 passed`.

4. **Repository hard gate** — `uv run pytest -q` (which applies the repo's real
   `addopts = "-n auto"`) → `2334 passed, 1 skipped, 3 warnings`. Zero pre-existing failures to
   discount.

5. **Real end-to-end** — the real generated 25KB template, launched by the resolved interpreter
   in a real tmux session on this zsh-less, direnv-less host with a stub engine on PATH: the
   session survived, and the engine was invoked as
   `CLAUDE_RAN args=--dangerously-skip-permissions --name e2e-1`. This is the first time the
   full template has been executed under bash, so it was exercised rather than reasoned about.

6. **Lint** — `ruff check src/ tests/` → `All checks passed!`; `ruff format --check` clean.

Two existing tests had pinned the buggy contract by string (`assert "exec zsh" in script`, and
an unconditional `direnv exec` in the `--once` argv). They were updated to assert the real
contract — the hot-reload interpreter must be a path that actually exists and is executable on
the host — and to create the real `.envrc` precondition the direnv wrap now legitimately
requires, rather than being relaxed.

## Lessons Learned

- **The whole fleet was one OS family wide, so a host assumption read as a fact.** zsh is
  present by default on macOS and was installed on every Debian box; the first host that lacked
  it was the first host to reveal the dependency. Requirements lists are the cheap guard here,
  and this one omitted both tools.
- **A test that greps the generated script for a tool name cannot catch this.** The existing
  coverage asserted `"exec zsh" in script` — it passed on exactly the code that could not
  launch. The replacement asserts the interpreter *exists*, which is the property that actually
  matters, and the new suite drives real tmux and real bash under a hermetic PATH.
- **`tmux new-session` exiting 0 is not evidence the session started.** The launcher checked
  the return code and reported success; the pane died afterwards. Any future check on this path
  should confirm the session is still there, not that the create call returned.
- **The correct invariant already existed in the same file and was not reused.**
  `_exec_with_direnv` documented "direnv is an enhancement, never a precondition" while the
  tmux path a few hundred lines away failed closed on it. Factoring the decision into one
  helper is what stops the two from drifting again.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-08-15 — interpreter resolved, direnv made optional

Delegated as `implement` / effort `high` (root cause pre-established, but the fix spans two
modules, five interpreter sites and four direnv sites, and had to preserve zsh preference).

- `e32346a` `test(session-launch): freeze RED regression for zsh/direnv launch deps` — frozen
  RED regression suite, committed before any production edit.
- `7af7723` `fix(session): resolve the session interpreter and make direnv optional` —
  `src/ai_cli/session_script.py`, `src/ai_cli/main.py`, `README.md`; existing contract-pinning
  assertions in `tests/test_cli.py` and `tests/test_main.py` updated; `--once` coverage added
  to `tests/test_session_launch_shell_resolution.py`.

<!-- /doc:region name="fix_log" -->
