---
title: "[BUG-012] Sub-agent transcripts have no interactive viewer — the reported stale-UUID mismatch does not exist"
category: bugs
tags: [claude-code, subagents, transcripts, upstream, not-a-defect, session-adopt]
status: diagnosed-upstream
severity: P3
related_docs:
  - tools/cc-session-audit.md
  - bugs/cc-task-namespace-persistence.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-012] Sub-agent transcripts have no interactive viewer — the reported stale-UUID mismatch does not exist

**Status:** diagnosed-upstream — **not an `ai-cli-utils` defect, and not a
misfiling defect either.** Sub-agent transcripts are written to the correct
location, under the session's *current* UUID, and this package's adoption tooling
already relocates them. The only real gap is that Claude Code ships no
interactive surface for reading them.

**Severity:** P3 — nothing is lost or mislocated. The cost is ergonomic: while a
delegated agent runs, its reasoning cannot be inspected from the parent session,
so a bad approach is caught at the end rather than in the first minutes.

**Created:** 2026-08-07

## Reported symptom

A session running several delegated sub-agents could not view any sub-agent
transcript. Investigation reported two findings and four candidate causes,
the leading candidate being that transcripts are filed under the session's
*first-ever* UUID while the live process identifies as a newer one — either as a
Claude Code bug or as fallout from this package's `ai session-adopt`.

## What is actually true

**The reported UUID mismatch is not real.** It rests on treating a *run id* as a
session UUID. Claude Code mints a per-process id used for the ephemeral run
scratch layer, and keys three separate things by it:

    ~/.claude/session-env/<run-id>/
    ~/.claude/teams/session-<run-id-prefix>/
    <tmp>/claude-<uid>/<project-slug>/<run-id>/tasks/

None of these is the transcript key. The transcript, its sidecar directory, and
therefore `subagents/`, are keyed by the **session UUID**, which is stable across
resume. The two ids appearing in one `/tmp` symlink path is correct by
construction: the *path* is the run scratch layer, the *target* is durable
session storage.

Claude Code reports the session UUID itself, so this needs no inference:

    $ claude agents --json
    [ { "pid": <pid>, "cwd": "<repo>", "kind": "interactive",
        "sessionId": "<session-uuid>", "name": "<session-name>", "status": "busy" } ]

The `sessionId` returned for the live process is exactly the UUID the sub-agent
transcripts are filed under. The supposedly stale UUID has a transcript actively
being appended to, and its `subagents/` directory grows while agents run. The
supposedly current UUID has no transcript anywhere on disk and appears in the
session's own history only inside `/tmp` task-output paths.

**Corollary:** in this layout every `subagents/` directory sits beside a
same-UUID `<uuid>.jsonl`. A `subagents/` directory whose sibling transcript is
missing would be the real signature of misfiling; that configuration does not
occur.

## Causes ruled out

- **Adoption tooling did not cause it.** `cc_migrate.migrate_session` moves the
  transcript's sidecar directory — the parent of `subagents/` — alongside the
  transcript, so `subagents/` travels with it by construction rather than needing
  to be enumerated. Independently, the reporting session had never been adopted:
  only one project directory existed for the repo, with no worktree-slugged
  counterpart.
- **Not orphaning by a scratch-dir clear.** The scratch root is not a tmpfs here;
  it is ordinary storage on the container root, holding user files older than the
  last restart. More decisively, orphaning is irrelevant: the durable path is
  derivable from `claude agents --json` plus the slug rule without consulting the
  scratch layer at all.
- **Not permissions or missing data.** Files exist, are non-empty, and are owned
  by the invoking user.

## The real gap

Claude Code exposes sub-agent text through exactly one documented flag:

    $ claude --help
      --forward-subagent-text   Forward subagent text and thinking blocks as
                                assistant/user messages with parent_tool_use_id set
                                (only works with --print and --output-format=stream-json)

It is restricted to non-interactive `--print` runs, so it does not apply to an
interactive session delegating to sub-agents. There is no interactive panel, no
`claude transcript` subcommand, and no viewer for `subagents/*.jsonl`. The
expectation of an interactive viewer is simply wrong for this version.

## How to read a sub-agent transcript

Derive the directory from first-party output rather than hardcoding a UUID:

1. `claude agents --json` -> take `sessionId` for the entry whose `cwd` is the repo.
2. Slugify the repo's absolute path by replacing every non-alphanumeric character
   with `-` (the rule implemented by `cc_migrate.cc_project_dir`).
3. Read `~/.claude/projects/<slug>/<sessionId>/subagents/agent-<agentId>.jsonl`.

Each transcript has an `agent-<agentId>.meta.json` sibling carrying `agentType`,
`description`, `model`, and `customAgentType` — use it to identify which agent a
transcript belongs to without parsing the JSONL. Both are readable while the
agent is still running, which is what makes mid-flight inspection possible.

For a background `Task` in the current run, the run scratch layer also offers
`<tmp>/claude-<uid>/<project-slug>/<run-id>/tasks/<agentId>.output`, a symlink to
the same durable file. It is a convenience only: it disappears with the run and
must not be treated as the location of record. Note that background *Bash* tasks
write real files in that same directory while *agent* tasks are symlinks, so a
consumer that assumes one form breaks on the other.

## Disposition of existing transcripts

None. Every transcript is already in its correct, durable location under the
current session UUID, discoverable by the derivation above. Nothing needs moving,
and nothing should be moved — relocating them would create exactly the
misfiling this investigation set out to find.

<!-- /doc:region -->
