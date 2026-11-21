# Human-name session identifiers: implementation notes

These notes record the measurements the human-name session identifier work takes before
it writes code that depends on them: each AI client's session store, and the size of the
generated name list. The design that asks for them is kept in the environment
repository; this file holds the numbers so the code that relies on them can cite a
tracked source.

## Client session stores

Measured 2026-10-23 on a macOS arm64 host. Field names only: no transcript content was
read. A client with no sessions on the host was measured from its installed
implementation, the code its own resume path runs.

| Client | Version | Store root | Record holding the working directory | Working-directory field | Id field | Title field |
|---|---|---|---|---|---|---|
| Claude Code | 2.1.309 | `~/.claude/projects/<slug>/`, one `*.jsonl` per session | every `user`, `assistant`, `system` and `attachment` record | `cwd`; the slug is derived from it by `cc_migrate.cc_project_dir` | `sessionId` | a `custom-title` record's `customTitle` (an `agent-name` record's `agentName` beside it) |
| Codex | 0.163.0 | `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl` | the first record, `type = "session_meta"` | `payload.cwd` | `payload.id` | none |
| Pi | 1.4.0 | `~/.pi/agent/sessions/--<cwd with each /, \ and : as ->--/<timestamp>_<uuid>.jsonl` | the first record, `type = "session"` | `cwd` | `id` | a `session_info` record's `name`, the latest one wins |
| Gemini CLI | 0.63.0 | `~/.gemini/tmp/<project id>/chats/*.json` or `*.jsonl` | none in the record: `~/.gemini/projects.json` maps each absolute project path to its project id under `projects` | the `projects.json` key | `sessionId` | none (`summary` is generated, not a title) |

Two points differ from what the design's store table recorded on the other host:

- Pi stores a title. `/name` appends a `session_info` record whose `name` is the
  session's display name, so a Pi migration has a title to rewrite, not only a path.
- Gemini's project id is a slug of the project directory's basename (`myproject`,
  then `myproject-1` on a clash), assigned in `~/.gemini/projects.json` under a lock.
  The older layout, a SHA-256 of the project path, is migrated to it on first use. A
  session's working directory is therefore found through `projects.json`, never
  through the chat record.

The Linux build host still needs the same measurement for every client installed there.

### Codex slot identity contract

Interactive numbered slots store a version 1 identity record beside the launch scripts:
`$XDG_STATE_HOME/ai-cli-utils/sessions/<tmux-slot>.codex.json` (the usual
`~/.local/state` fallback applies). Native rollouts remain under
`$CODEX_HOME/sessions` or `~/.codex/sessions`. Only their first metadata record is read.
Claude Code, Gemini and Pi keep their existing adapters and launch arguments.

Before a Codex process starts, the launcher writes `phase: pending`. A native
`SessionStart` hook confirms `session_id`, `cwd` and nullable `transcript_path` for
that launch token. The record retains only identity metadata: schema version, slot,
directory, native thread ID, rollout path, client version, execution mode, route,
result and launch token. It contains no prompt, message or transcript bodies.
These records are neither process liveness nor commit authority.

On restart or supervisor recreation, a confirmed ID must match exactly one readable
rollout's `payload.id` and `payload.cwd`. The launcher passes that ID to
`codex resume`; it never selects by newest file, working directory or `--last`.
Missing, duplicate, malformed, wrong-directory and ephemeral history refuses resume.
An unconfirmed callback, incompatible schema or unsupported version stays pending or
unknown. There is no automatic adoption of old directory-only slots. A live pane
can still be attached with `ai cx NAME` or `ai attach <tmux-slot>` without claiming
that it has resumable persisted history. Confirmed history alone never proves liveness.

The supported native event contract is pinned to Codex 0.169.0:
[event schema](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/hooks/src/schema.rs)
and [configuration discovery](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/hooks/src/engine/discovery.rs).
The launcher owns exactly one session-flags `hooks.SessionStart` override, with one
group and one handler at position 0/0. File-layer hooks stay independent. The command
is PATH-resolved, as are the launcher's other `ai internal` callbacks:

```text
ai internal codex-session-confirm
hooks.SessionStart=[{hooks=[{type="command",command="ai internal codex-session-confirm"}]}]
```

These bytes are identical across launches, slots and hosts. There is no matcher,
timeout, async, statusMessage or additionalContextLimit override. The native defaults
are used. The four-line `prepare` reply contains target, declaration, absolute slot
record path and launch token, in that order; record paths containing CR/LF are refused
before writing. `_run_codex` passes `AI_CLI_CODEX_SLOT_RECORD` and
`AI_CLI_CODEX_LAUNCH_TOKEN` only to the client invocation and its descendants. The
callback reads these variables and SessionStart JSON from stdin. The former
`python -m ai_cli.codex_session confirm <record> <token>` form is removed.

Missing/empty variables, a missing record, a stale token or a nonbinding event fail
with a nonzero status and a tagged error, without changing the identity record or
printing tokens, payloads or exception text. This intentionally replaces the prior
behavior that wrote pending/unknown before rejecting an event; a launch already has
pending state before its client starts. Duplicate valid callbacks remain idempotent.
A disabled or unapproved callback leaves that launch pending. No trust bypass is added.
A new client version needs verified event and hook fixtures before enablement.

#### Version 1 hook trust boundary

`ai internal codex-hook-contract` prints the document in
[codex_hook_contract_v1.json](../../tests/fixtures/codex_hook_contract_v1.json).
Launch adapters own the declaration here; the policy installer owns approvals in its
separate repository. Both sides must replay this fixture. Its fields are
`schema_version`, `supported_client_versions`, `event`, `command`, `declaration`,
`position`, `keys` (linux/macos) and `expected_hash`. The hash is pinned to 0.169.0:

```text
sha256:f48156510f48462d9df8bbeb7f27e892142a567bc35edb0b0505f2716dd6723d
```

The installer must obtain the exact key and current hash from the pinned client's
`hooks/list`, compare them with this fixture, and refuse any mismatch before writing
trust. Linux/macOS use `/<session-flags>/config.toml:session_start:0:0`. Windows key
spelling differs (`C:\<session-flags>\config.toml:session_start:0:0`) and must be read
from the native client; Windows execution is unverified. Repeated overrides replace
the SessionStart array, so a second session-flags override would violate this boundary.
The generated-launch tests assert one override and one handler for each launch path.

Source citations below were re-opened at commit
`d32063f96c2dff1dc96af839b3952abb6acfeda4` (Codex 0.169.0):

- Hash identity: [hooks/src/engine/discovery.rs:972–997](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/hooks/src/engine/discovery.rs#L972-L997).
  The independent Python implementation normalizes the handler with timeout 600 and
  async false, omits absent optionals, recursively sorts JSON keys and hashes compact
  UTF-8 bytes with SHA-256, following
  [config/src/fingerprint.rs:54–65](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/config/src/fingerprint.rs#L54-L65).
  Environment values and executable contents are outside this identity.
- Key: [discovery.rs:469–485](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/hooks/src/engine/discovery.rs#L469-L485)
  constructs the synthetic path; [hooks/src/lib.rs:124–135](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/hooks/src/lib.rs#L124-L135)
  appends event and declaration position.
- Trust is read from user/session layers and merged field by field in
  [hooks/src/config_rules.rs:8–64](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/hooks/src/config_rules.rs#L8-L64).
  The native trust action writes `trusted_hash` under `hooks.state` using Upsert in
  [tui/src/hooks_rpc.rs:69–94](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/hooks_rpc.rs#L69-L94).
  The installer must preserve unrelated entries and existing enablement preferences.
- Directory consent is separate: [tui/src/lib.rs:2041–2076](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/lib.rs#L2041-L2076)
  checks it before hook review at [2313–2319](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/lib.rs#L2313-L2319).
  [tui/src/config_update.rs:368–377](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/tui/src/config_update.rs#L368-L377)
  defines when consent can be skipped. A fresh untrusted Git project still prompts a
  human. Satisfying directory consent is a precondition this launcher does not remove.
- Environment transport: [hooks/src/engine/command_runner.rs:484–495](https://github.com/openai/codex/blob/d32063f96c2dff1dc96af839b3952abb6acfeda4/codex-rs/hooks/src/engine/command_runner.rs#L484-L495)
  replays the session environment and filters `is_non_inheritable_env_var`. That filter
  and any earlier session-environment policy remain a runtime transport risk. The
  synthetic bash/zsh tests prove launcher inheritance, not real-client replay.

The owner's real-client canary must verify the two variables reach the hook, the
PATH-resolved command runs, the exact hook approval removes its review gate, and the
slot becomes confirmed. This change cannot run the real client or edit trust state.
The paired host, macOS and Windows also remain unverified.

#### Hook acceptance criteria

- When any supported slot launches, the launcher shall pass the same declaration
  exactly once at group 0/handler 0, with no launch identity in its command text.
- When confirmation lacks bound environment or event identity, it shall refuse
  without an identity-record write or disclosure of private input.
- When the contract is requested, it shall equal the reciprocal version 1 fixture,
  including the independently calculated pinned hash.
- When a non-Codex client launches, its existing behavior shall be preserved.

The reciprocal boundary fixture is
[codex_session_identity_v1.json](../../tests/fixtures/codex_session_identity_v1.json).
It records input/output identity, client version, execution mode, route and an explicit
unknown live-canary result. Local tests replay the actual callback command, collision
dispatch with and without a queued prompt, and a synthetic client in an isolated tmux
pane through the local attach/list path. They do not establish actual client behavior
on either host. The policy-side consumer must replay the same versioned fixture.

Remaining exit checks: repeat a real same-directory collision through local and
paired-host attach, then repeat on macOS. Record installed version, execution mode,
route and result without message bodies. The storage mode of tmux-launched Codex on
each host, and exact-thread `queue --resume` readback, remain open questions. The
launcher refuses missing legacy rollouts; support for another native store is unverified.

## The generated name list

Built 2026-10-23 by `scripts/refresh_session_names.py` from the two pinned artifacts,
whose downloaded digests both matched their pins, at the template's default limits of
3 to 4 letters.

| Length | Names |
|---|---|
| 3 | 140 |
| 4 | 482 |
| Total | 622 |

A greedy pass that never keeps two names one edit apart (substitution, insertion,
deletion or adjacent swap) holds at least 267 of them at once. That is a lower bound on
how many sessions the default limits can name at the same time with the typo filter on.

Before `non-names.txt` was curated, the same run kept 633 names. The 11 dropped are
abbreviations and truncations (`chas`, `eliz`, `jame`, `marg`, `robt`), name particles
(`del`, `von`) and tokens read first as something other than a first name (`asia`,
`isis`, `mao`, `marx`).

`non-names.txt` does not list the tokens the public-repo hygiene test forbids, because
the file is inside that test's scan. The test scans `names.txt` too, so a forbidden token
reaching the generated list fails CI. No such token appears at 3 to 4 letters.
