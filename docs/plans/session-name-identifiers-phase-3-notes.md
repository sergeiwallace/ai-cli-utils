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
