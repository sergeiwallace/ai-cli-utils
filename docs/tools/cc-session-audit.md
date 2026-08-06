# `ai session-audit` — survey titled Claude Code sessions, and drive their adoption

`ai session-adopt` fixes **one** session whose name and location you already know.
`ai session-audit` answers the questions you have to ask before that: which titled
sessions exist, where do they really live, and which ones will `ai c <n>` fail to
resume?

It is read-only by default. With `-a/--adopt` it drives adoption for everything
safe to adopt, delegating to the same code path as `ai session-adopt` — so every
refusal that command makes, this one makes too.

## Usage

```sh
ai session-audit                      # survey everything, write nothing
ai session-audit -r /path/to/myproject   # only sessions owned by one repo
ai session-audit -t myproject-2        # only one title
ai session-audit -a -n                # show what adoption would do, write nothing
ai session-audit -a                    # adopt, after a confirmation prompt
ai session-audit -a -y                 # adopt without the prompt
```

## Options

| Short | Long | Meaning |
| --- | --- | --- |
| `-r` | `--repo` | Report only sessions owned by this repo root (default: every repo found) |
| `-t` | `--title` | Report only the session with this exact title |
| `-a` | `--adopt` | Adopt every session that is safe to adopt |
| `-n` | `--dry-run` | With `-a`: show what would be adopted without writing anything |
| `-y` | `--yes` | Skip the confirmation prompt (never covers a title collision) |

## What it reports

Per session: the title, the transcript path, its line count and size, the working
directory it recorded, the repo that owns it, whether it already sits in its
`.worktrees/<title>` slot, whether a process is currently running it, and whether
`ai c <n>` actually resolves it from that slot.

Two conditions get called out separately:

* **Title collisions** — one title claimed by more than one transcript, listed
  with every claimant. Reported in the survey itself, before any adoption is
  attempted.
* **Skips** — each session that is not safe to adopt, with the specific reason
  (live, colliding, already adopted, no recorded cwd, no owning repo, or a title
  that is not `<prefix>-<index>` and so cannot be addressed by `ai c <n>`).

## How discovery works, and why it matters

The survey scans **outward from `~/.claude/projects/`**, not inward from a list of
repositories.

That direction is the point. Walking known repos means enumerating every place a
session might sit — the repo root, `<repo>/.worktrees/<name>`,
`<repo>/.claude/worktrees/<id>` (Claude Code's own agent worktrees), and whatever
convention appears next — and any place nobody thought to enumerate is silently
missing from the report. That is exactly the defect this command was written to
catch: a session documented as living at a repo root actually lived in an agent
worktree, and adopting it by title from the root failed with "no transcript titled
X" until its real directory was supplied by hand.

Every session, wherever it ran, has a project directory under
`~/.claude/projects/`, and each transcript records the working directory it ran in.
So the transcripts are the census and the repo is *derived* from the recorded cwd.
Nothing has to be supplied by the caller.

Attribution uses path shape first: anything under `<repo>/.worktrees/` or
`<repo>/.claude/worktrees/` belongs to `<repo>` by construction, whether or not
that directory still exists — a cleaned-up worktree must still be attributed,
because its transcript outlives it. Only when neither convention appears does it
walk up looking for a repository marker.

### What counts as "already adopted"

Adoptedness is decided by **where the transcript file sits** — whether its project
directory is the one the worktree slot slugifies to — and by whether `ai c` actually
resolves it. It is deliberately *not* decided from the `cwd` recorded in the
transcript's records.

The distinction is not academic. An adoption moves the transcript into the slot's
project directory, which is what makes `ai c <n>` resolve it, and rewrites the cwd
fields it needs to — but a long transcript legitimately keeps thousands of
historical cwds pointing at wherever the session originally ran, including
sub-agent paths under `.claude/worktrees/agent-*`. One real session carried 5062
records with the old repo-root cwd against 140 rewritten ones, and was resuming
perfectly. Judging from the cwd majority reports such a session as still needing
adoption, which is the wrong answer: re-adopting a working session is at best a
no-op and at worst disturbs one that currently resumes.

`slug-mismatch` still reports the cwd-versus-directory comparison, because "this
transcript still carries pre-adopt cwds" is worth seeing. It is informational only
and never drives the adoptable/skip decision.

### The slug is one-way

A project directory name is the working directory with every non-alphanumeric
character replaced by `-`, so `/home/user/my_proj` and `/home/user/my-proj`
slugify identically and the mapping cannot be inverted. The recorded cwd is
therefore the only reliable source of a session's real path. It is *checked*
against its containing directory by re-slugifying it; a mismatch is reported as
`slug-mismatch`, which means the transcript was moved without its cwd fields being
rewritten.

## Refusals are reported, never routed around

Two refusals belong to the adoption module and stay there:

* **A duplicate title is an unconditional gate.** `ai c` resolves a title by
  scanning newest-first for the first matching `customTitle`, so two transcripts
  claiming one title make resume nondeterministic — it would silently pick one of
  two conversations. A human has to choose which one keeps the name.
  **`-y/--yes` does not cover this**, by design.
* **A live session is refused.** Adopting one moves its transcript out from under
  a running process that still has it open.

`session-audit` classifies both *before* calling the adopter, so a bulk run reports
them as expected skips and keeps going. One refusal never aborts the batch. The
adopter's own gates remain the authority and still fire regardless.

A duplicate title is skipped wherever it occurs, including across repos: titles
double as worktree directory names and as the argument to `ai c`, so a duplicate
anywhere is a human decision.

## Exit codes

`0` when nothing was skipped and nothing refused; `1` when any session was skipped
or any adoption was refused, so a script can tell "all clean" from "needs a human".
A survey with no `-a` exits `0`.

## See also

* [`cc-session-adoption.md`](cc-session-adoption.md) — what an adoption actually
  moves, and the full inventory of session state
* [`cc-session-migration.md`](cc-session-migration.md) — moving a single
  transcript between project roots
