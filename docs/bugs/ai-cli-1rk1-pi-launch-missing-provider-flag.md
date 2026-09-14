---
title: "ai p -R launches pi with no --provider, so it silently fails against an unconfigured default"
category: bug
tags: [bug, pi, session-launch, provider, config]
status: fixed
source: "AI-CLI-1rk1"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# ai p -R launches pi with no --provider, so it silently fails against an unconfigured default

**Status:** fixed

**Severity:** P1 — every `ai p`/`ai p -R` launch failed, with no error message, until this fix

**Created:** 2026-09-14

## Summary

`ai-cli-utils` launched the `pi` CLI (`pi --name "$ai_name"` / `pi --continue --name "$ai_name"`)
without ever passing `--provider`. `pi`'s own built-in default provider is `google`. On the
Framework host, `pi auth check --provider google` reports `not_ready` — no Google credentials are
configured there. Every `pi` launch spent several seconds doing nothing (mostly idle wall-clock
time, consistent with a stalled provider/auth probe) and then exited with zero output.

Separately (see `docs/bugs/ai-cli-1rk1-restart-loop-no-circuit-breaker.md` if/when filed — tracked
as `AI-CLI-1rk1`'s second half), the session-launch supervisor's only restart-loop circuit breaker
fires on an exit under 3 seconds; since each failed `pi` attempt took roughly 5.5 seconds, the
breaker never tripped, and the supervisor kept relaunching `pi` forever. From the user's terminal
this looked like a hang: the pane repeatedly reprinted `direnv`'s full environment-export banner
(everything it exports, including `DOPPLER_*` variable *names*, never values) with no `pi` output
ever appearing, until the user manually pressed Ctrl+C twice to escape.

The actual owner of the credentials confirmed `pi` is already authenticated for OpenAI's Codex/
ChatGPT backend — but `pi`'s provider identifier for that backend is `openai-codex`, not `google`,
`openai`, or `codex`. Confirmed directly: `pi auth check --provider openai-codex` → `ready`.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

Live-reproduced on `framework-26-sw` (2026-09-14):

```console
ai p 2 -R
```

produced a tmux pane that repeatedly printed (captured via `tmux capture-pane`):

```
direnv: loading ~/projects/ai-harness/.worktrees/aih-2/.envrc
direnv: ai-harness environment loaded
direnv: export +ADZUNA_APP_ID ... +DOPPLER_CONFIG +DOPPLER_ENVIRONMENT +DOPPLER_PROJECT +DOPPLER_TOKEN ... +WIF_SERVICE_ACCOUNT
```

three times in a row, with zero `pi` output between cycles, until the user's own Ctrl+C landed.

Directly on the host:

```console
$ pi auth check --provider google
not_ready
$ pi auth check --provider anthropic
not_ready
$ pi auth check --provider openai
not_ready
$ pi auth check --provider codex
{"status":"not_ready","provider":"codex","reason":"provider_not_found"}
$ pi auth check --provider openai-codex
ready
$ time timeout 6 pi --name test </dev/null >/tmp/out.txt 2>&1; cat /tmp/out.txt
real  0m5.496s   (empty output)
```

`pi --list-models` confirms the only ready provider's real identifier: every listed model row is
prefixed `openai-codex`, never `codex` or `openai`.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root-cause" kind="replaceable" -->

## Root cause

Three call sites in `ai-cli-utils` launch `pi` without ever specifying `--provider`:

1. `src/ai_cli/main.py`, `_bare_engine_command()` (bare/no-tmux launch).
2. `src/ai_cli/main.py`, the `--once` direct-`tmux new-session` exec path.
3. `src/ai_cli/session_script.py`, the generated supervisor/child bash template's
   `run_agent pi ...` lines (the normal tmux-supervised launch path, used by `ai p -R`).

With no `--provider` flag, `pi` falls back to its own hardcoded default (`google`), which has no
configured credentials on this host (or, in general, on any host that hasn't separately set up
Google credentials for `pi`). No config key in `ai-cli-utils` (`config.toml`) or in `pi`'s own
config (`~/.pi/settings.json`, absent) ever selected the actually-ready provider.

<!-- /doc:region name="root-cause" -->

<!-- doc:region name="fix" kind="replaceable" -->

## Fix

Added a `[pi] provider` config key (`config.py`, default `"openai-codex"`) and threaded a
`pi_provider` parameter through all three launch call sites, so every `pi` invocation now passes
an explicit `--provider <value>`:

- `_bare_engine_command()` — `pi_provider: str = "openai-codex"` parameter, appended
  `--provider <value>` before `--name`.
- The `--once` direct-exec path — reads the same resolved `pi_provider` local.
- `get_engine_script()` (`session_script.py`) — new `pi_provider` parameter, round-tripped through
  the persisted `session-meta-*.json` (`_engine_script_from_meta()`) so template regeneration
  (hot-reload / `ai internal refresh-template`) stays faithful, and embedded into both
  `run_agent pi ...` lines in the generated bash template.

Users with a working provider other than `openai-codex` can override it via
`config.toml`'s new `[pi] provider = "..."` key.

<!-- /doc:region name="fix" -->

<!-- doc:region name="verification" kind="replaceable" -->

## Verification

- New/updated tests (`tests/test_main.py`): `_bare_engine_command` pi cases (default provider,
  configured-provider override), `get_engine_script` pi cases (default + override), all RED on
  unfixed code for the right reason (missing `--provider` in the asserted argv/script text),
  GREEN after the fix.
- `tests/test_main.py`, `tests/test_session_launch_shell_resolution.py`, `tests/test_cli.py`,
  `tests/test_config.py`: 344 passed.
- Full suite: 2863 passed, 16 skipped, 16 warnings. 25 failures in
  `tests/test_stale_session_reaper.py` (real-tmux supervisor timing tests) and 1 in
  `tests/test_native_deps.py` confirmed pre-existing — reproduce identically with this fix's
  changes stashed out. Filed `AI-CLI-test-given-binary-broken-missing-8k0z` for the native_deps
  one; the stale-session-reaper flakiness matches already-tracked `AI-CLI-t3fs`/`AI-CLI-vaq9`.
- `ruff check` / `ruff format --check`: clean.

<!-- /doc:region name="verification" -->

## Follow-up investigation and final fix

The provider fix exposed three further, independent conditions required for a
stable remote Pi session.

1. **Worktree direnv trust:** direnv approvals are path-specific. The
   worktree initializer (`_allow_trusted_worktree_envrc`, shipped in PR #52,
   2026-08-23) propagates approval to a new worktree only when its `.envrc` is
   byte-identical to an *already-approved root* `.envrc` (`envrc_loads(repo_root)`
   must return true). That PR was already deployed on Framework at the time of
   this reproduction — the code-level propagation mechanism was never the gap.
   The actual cause: Framework's own root `ai-cli-utils` checkout had never had
   `direnv allow` run against it (confirmed live: `direnv exec . true` on the
   root reported the same "is blocked" error as the worktree), so the
   propagate-from-trusted-root mechanism had nothing to propagate from — every
   fresh worktree stayed unapproved no matter how many times the launcher ran.
   Remediated by running `direnv allow` once on Framework's root checkout,
   after which the existing propagation mechanism worked as designed for a
   freshly created worktree. This leaves a real, narrow gap: nothing in the
   launch flow ever bootstraps the *root's own* initial trust, so any host
   whose root `.envrc` is unapproved (first-time setup, or after the root
   `.envrc`'s content changes and invalidates the prior approval) is stuck the
   same way until a human manually re-approves the root — worth a follow-up
   hardening item, not fixed here.
2. **Terminal stdin:** a non-interactive Bash or zsh redirects a background
   job's stdin to `/dev/null`. Pi interprets that EOF as a clean exit. `run_agent`
   now reopens `/dev/tty` when available before backgrounding an agent, retaining
   the existing PID and wait-based signal forwarding used by Claude, Gemini,
   Pi, and Codex.
3. **Slow restart loop:** the existing `elapsed < 3` fast-crash guard remains
   unchanged. A new per-session counter records agent exits across freshly
   spawned child shells; the third consecutive exit prints `AI CLI keeps
   failing to start` and stops the session. It intentionally counts a clean
   short-lived exit too, because an interactive TUI should remain running and
   Pi's terminal-EOF failure exits with status zero.

Regression coverage in `tests/test_session_launch_shell_resolution.py` runs the
real generated wrapper in real tmux panes under Bash and zsh, proving a terminal
agent remains interactive. It also drives three slow subprocess exits through
fresh generated child bodies and proves the independent circuit breaker stops
the loop. `tests/test_worktree_envrc_approval.py` continues to cover the
byte-identical approved-root trust boundary.

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons learned

- A silently-unready default provider plus an unbounded restart loop compounds into a UX that
  looks exactly like a hang. The provider fix here addresses why `pi` fails; a separate,
  independent fix (tracked under the same `AI-CLI-1rk1` issue) hardens the restart loop itself so
  *any* persistently-failing agent — regardless of cause — gets a clear diagnostic instead of an
  infinite silent loop.
- `pi auth check --provider <name>` is the fast, authoritative way to check readiness — it caught
  both the wrong-provider-name hypothesis (`codex` → `provider_not_found`) and the eventual
  correct one (`openai-codex` → `ready`) in well under a second each, far faster than trying to
  infer provider state from a live launch attempt.

<!-- /doc:region name="lessons" -->
