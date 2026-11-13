---
title: "ai sessions prints a grouped table with pair and fleet views, and ai -h shows the whole command tree"
category: plan
tags: [plan, sessions, help, docs, output]
status: active
source: "None"
template_version: "plan-1.0.0"
task: AI-CLI-k2r8
stub: false
---

<!-- doc:region name="overview" kind="replaceable" -->

# ai sessions table, views and the full ai -h: Implementation Plan

## Founding Ask Coverage

`canonical_root_json`: N/A (no ask-fidelity root was minted for this task). `founding_ask_ref_sha256`: N/A.
The maintainer's asks, from four prompts on 2026-11-12. This repo is public, so session names, host
names and the pasted terminal output are left out; each removal is marked `[...]`.

```text
how do i show sessions for `ai sessions` command for ai-cli-utils?

it's not clear how this works from `ai -h`. `ai sessions` ust show all running sessions on the machine host and ideally even the machine host pair if available (possibly even all cc sessions on all machine hosts, if that info is git-tracked and retrievable).

any command groups and specific commands for command groups need to be outlined clearly how to use in both the canonical user cli usage documentation in the repo and the root readme.md and also in the `ai -h` help message. i need to know exactly what all i can do with it from the `ai -h` command. its not all what i can do. also, we need to show how to clearly launch a ai session and also resume / continue / attach to an existing cc session (it will default attach to an ai session if its already running, right? we don't need to explicitly pass in or use `attach` command, right?). the help message needs to more clearly outline how all this works, and it also should include a github url link to the canonical cli usage documentation btw for complete reference in the `ai -h` help message.

also, `ai sessions list` needs to be formatted in a clear table or something with aligned columns and column headers or something. see if there is a good open source popular highly rated community repo / package / library etc for displaying tables or other types of data in the terminal. see if any of our existing cli terminal output formatting tools do it. we should try to leverage open source library / framework / package for this as much as possible so we don't have to create it from scratch on our own. [...]

also, for that handoff that [...] sent, for the table, i want it to gorup them by machine profile as well in the table or output.

i need it to be grouped by machine profile and also sorted by cc sessions that are sorted by status (with active ones at the top). also, i want an option / arg to be able to sort / group the table in cusotm configurable ways. also we should have config file defaults which are what i said to do above. that can be in the ai-cli-utils git-tracked config in [...] or wherever it is and also in the default template config for ai-cli-utils repo that is git-tracked in that repo since it should be the default
```

| Ask entry | Where covered |
|-----------|---------------|
| "`ai sessions` [must] show all running sessions on the machine host" | T-02: bare `ai sessions` runs the list, running sessions only by default |
| "ideally even the machine host pair if available" | T-02: the default scope is `pair`, which adds the paired host when one is configured and published |
| "possibly even all cc sessions on all machine hosts, if that info is git-tracked" | T-02: `-S fleet` reads every host's published snapshot |
| "outlined clearly ... in the canonical user cli usage documentation ..., the root readme.md and ... `ai -h`" | T-03: one generated command tree in all three, checked by a test |
| "show how to clearly launch ... resume / continue / attach" and the two questions about `attach` | T-03: the "Start, resume, attach" section; answer below |
| "github url link to the canonical cli usage documentation" | T-03: the last line of `ai -h` |
| "a clear table ... aligned columns and column headers" | T-01: `rich.table` through `output.table()` |
| "see if there is a good open source ... library ... see if any of our existing ... tools do it" | D-1 |
| "grouped by machine profile ... sorted by status (with active ones at the top)" | T-01: default `group_by = "host"`, `sort_by = ["status", "name"]` |
| "an option / arg to be able to sort / group the table in custom configurable ways" | T-01: `-g/--group-by`, `-s/--sort-by` |
| "config file defaults which are what i said ... and also in the default template config" | T-01: `[sessions_list]` in the shipped config template and `docs/reference/sessions-list-defaults.toml`; the managed copy in the maintainer's private tooling is added outside this repo |

The answer to the two `attach` questions, confirmed in `src/ai_cli/main.py` (the launch path that runs
`tmux attach-session -d` when the session's tmux session already exists): yes, `ai c NAME` attaches to
NAME when its tmux session is running, and `ai attach` is not needed for a session `ai` started. `ai c
NAME -r` resumes a session that is not running, and refuses, creating nothing, when no session holds
NAME.

**Status:** ACTIVE

**Created:** 2026-11-12

**Tasks:** AI-CLI-k2r8, AI-CLI-rhnd, AI-CLI-cha0, built in that order on one branch.

<!-- doc:end -->

## Decisions

Every decision below is resolved; none needs the maintainer before it ships.

**D-1. The table library is `rich.table`.** Rich is already a runtime dependency (`rich>=15.0`) and
`output.py` already renders through a Rich `Console`; `rich.table.Table` aligns columns, measures wide
characters and handles a narrow terminal. Considered: `tabulate` (popular, plain text only, a new
dependency), `prettytable` and `texttable` (same, fewer features), Click's own `HelpFormatter.write_dl`
(two columns only), and padding with `str.ljust` (hand-rolled, which the ask rules out). Rich costs
nothing new and matches the existing output path.

**D-2. One shared helper, `output.table(headers, groups)`.** It prints one table with a header row
and a rule between groups, through a new `Untagged.TABLE` reason: a tag on each row would break the
column alignment the table exists for. Without a terminal (a pipe), the width is unbounded so a row
never wraps and `grep` keeps working; with one, the terminal width applies. No colour is written.

**D-3. Columns.** `HOST`, `SESSION`, `STATUS`, `CAUSE`, `CHANGED`, `RELAUNCH`. The grouping column is
printed first and only on the first row of its group, so a group reads as a block.

**D-4. Grouping and sorting.** `-g/--group-by` takes `host`, `status`, `relaunch` or `none`.
`-s/--sort-by` takes a comma-separated list of `status`, `name`, `changed`, `host`, `relaunch`.
Status order is `running`, `relaunched`, `unknown`, `exited`, so active sessions come first; `changed`
sorts newest first; the others sort A to Z. Host groups put this host first, then the others A to Z;
status groups follow the status order. `-g` and `-s` are free on `ai sessions list` (only `-j` is taken).

**D-5. Config defaults, read at call time.** A `[sessions_list]` table in `config.toml`:
`group_by = "host"`, `sort_by = ["status", "name"]`, `show_all = false`, `scope = "pair"`,
`pair_host = ""`. Built-in defaults equal these values, so an absent table behaves the same. CLI flags
override config. An invalid value is refused naming the key and the file. Documented in
`docs/reference/sessions-list-defaults.toml` and in the template `config.toml` that `ai` writes on
first use; a test holds all three equal.

**D-6. Running sessions only by default.** `-a/--all` adds every other row. "Active" means `running`
or `relaunched`; an `unknown` row is not proved running, so it is hidden by default.

**D-7. Bare `ai sessions` runs `ai sessions list` with the config defaults.** The group takes no list
options of its own, so `ai sessions -h` still prints the group's help; options go on `ai sessions list`.

**D-8. Scope: `-S/--scope host|pair|fleet`.** This host's rows always come from the live registry.
Other hosts' rows come from the snapshots `ai sessions publish` already writes: each host pushes
`sessions/<host>.toml` to `<ref_prefix><host>` on the publication remote. The list fetches
`<ref_prefix>*` into the publication repo's `refs/session-state/*` (bounded at 15 s); if the fetch
fails it reads the refs already fetched and says so. `pair` is this host plus `pair_host`, or, when
that is empty, the default `[remote]` machine's alias. `fleet` is every published host. Each other
host's snapshot time is printed under the table.

Limits, stated rather than hidden: a host that does not publish (no `session_relaunch.publish.repo`)
is absent from `pair` and `fleet`; a snapshot is only as new as that host's last publish; with no
publication repo on this host, `pair` and `fleet` show this host alone and say why. `pair` is the
default scope because the ask names the pair "ideally ... if available": with no pair or no
publication it is the same as `host`.

**D-9. `--json` is unchanged.** `ai sessions list -j` prints this host's registry, every row, in
registry order, with the same five keys, whatever `[sessions_list]` says. `-j` with `-S pair` or
`-S fleet` is a usage error rather than a new JSON shape.

**D-10. `ai -h` prints the whole command tree, one line per command.** Today the root list prints
each top-level command's complete description (some run to forty lines) and no subcommands. The root
help now prints every command and subcommand, indented under its group, each with its one-line
summary (`command_summary`: the first sentence, wrapped, never cut with `...`). Each group's own `-h`
keeps the complete descriptions. Before the options comes a "Start, resume, attach" section; the last
line is the URL of the CLI guide. This replaces the root-only rule in `tests/test_cli_help.py` that the
root list carries every complete description; the group rule stays.

**D-11. One source for the tree and the section.** The "Start, resume, attach" rows live once in
`cli_help.py`. `scripts/generate_docs.py` renders them, and the command index it already renders for
`README.md`, into marked regions of both `README.md` and `docs/tools/ai-cli-usage.md`; its `-c` check
and the pre-commit hook cover both files. A test compares the command paths `ai -h` prints with the
paths in each doc.

**D-12. The URL comes from the package metadata.** `ai -h` reads the `Repository` project URL of the
installed distribution and appends `/blob/main/docs/tools/ai-cli-usage.md`, so no account name is
written into source. When the metadata is missing, the line names the doc's repo path instead.

## Task Breakdown

**T-01 (AI-CLI-k2r8). Table, grouping, sorting, config.** `output.table()` and `Untagged.TABLE`;
`ai sessions list` prints the table; `-g/--group-by`, `-s/--sort-by`; `[sessions_list]` defaults in
`session_list_view.py`, the template and the reference file.

**T-02 (AI-CLI-rhnd). Views.** Bare `ai sessions`; `-a/--all`; `-S/--scope`; snapshot reading in
`session_state_publish.read_snapshots()`.

**T-03 (AI-CLI-cha0). Help and docs.** The root tree, the "Start, resume, attach" section and the URL;
the generated regions in `README.md` and the CLI guide; a one-line summary for every command (the
`migrate-transcript` summary is cut at "e.g." today and is reworded).

## Tests

All through `cli()` with the registry in a temporary state home, config patched at `load_config`, and
a real bare Git remote for snapshots. Each is red on 81b73854 and green on the branch.

- The table has a header row and aligned columns; rows group by host with this host first.
- Within a group, running sessions come before unknown and exited ones.
- `-g` and `-s` override `[sessions_list]`; `[sessions_list]` overrides the built-in defaults; a bad
  value is refused.
- Bare `ai sessions` prints the same table as `ai sessions list`; exited rows appear only with `-a`.
- `-S pair` adds the paired host's published rows; `-S fleet` adds every published host; with no
  publication repo the list says so and shows this host.
- `-j` output is byte-for-byte the same as before for a fixed registry.
- Every visible command has a non-empty summary; `ai -h` lists every command path, the section and
  the URL; the README and the guide carry the same paths and section.

## Acceptance

`ruff check`, `ruff format --check`, `scripts/generate_docs.py -c` and the full suite pass.
