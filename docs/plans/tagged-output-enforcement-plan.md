---
title: "Every output line carries a log type tag, enforced by design"
category: plan
tags: [output, logging, launch, cli-ux, enforcement]
status: implemented
task: AI-CLI-9brz
source: session-2026-10-11
---

# Every output line carries a log type tag, enforced by design

## Founding Ask

Verbatim:

> also, i see there is not `ai` session launcher log output statement log type tag e.g. `[<log-type-tag-name>]`
> for the following log output line: `dolt_server: healthy (socket accepted)`
>
> review all the log output lines that are possible for ai-cli-utils and make sure they all systematically
> have to include a log type tag included in the log output line for all lines and systaematically enforce
> that and make sure all log output lines go through some systematically library / class / function etc
> interface that includes a log type tag and any other formatting for rich termainl logging and output as
> appropriate. i do not want to see any log output lines without a log type tag going forared and it
> shouldn't be possible systamtically and we need to enforce that at the code design level.

| Ask fragment | Where it is answered |
|---|---|
| the `dolt_server: healthy ...` line has no tag | T-03: child output is relayed through the interface as `[dolt_server] ...` |
| review all the log output lines that are possible | [Inventory](#inventory) |
| all lines go through one library / class / function interface | T-01: `ai_cli.output` |
| that interface includes the tag and rich terminal formatting | T-01: the tag is a required `Tag` enum member; styling is owned there |
| it shouldn't be possible, enforced at the code design level | T-04: AST enforcement test plus a runtime type check |

## Table of Contents

- [Founding Ask](#founding-ask)
- [Inventory](#inventory)
- [Design](#design)
- [Tasks](#tasks)
- [Decisions](#decisions)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Inventory

Measured at base `c221d40` with an AST scan of `src/ai_cli/` (the scan becomes the T-04 test):

| Output path | Sites | Tagged at base |
|---|---|---|
| `print(...)` | 485 | a handful hand-copy `[memory-watch]` style prefixes; the rest carry none |
| `click.echo(...)` | 18 (16 `main.py`, 2 `launch_reporter.py`) | 2 (the reporter's `[launch]`) |
| `sys.stdout.write` / `sys.stderr.write` | 6 / 1 | 0 (4 are iTerm2 escape sequences) |
| `echo` callback calls (`iterm2_restore.py`, fed `click.echo` by `main.py`) | 13 | 0 |
| logging handlers writing to a terminal | 0 configured; `stale_session_reaper` warnings fall through to Python's `lastResort` stderr handler | 0 |
| rich `Console` | 0 | n/a |
| child processes inheriting the terminal (of 193 `subprocess` calls) | 7: dolt supervisor, `sudo -v`, circusd, 3 ssh/mosh transports, macOS `open -na` | 0 |
| shell lines in the generated session script (`session_script.py`) | about 12 `echo`/`printf` messages (`ai-cli: ...`, `Warning: ...`) | 0 |

So of 523 Python output call sites, only the two reporter writes carried a tag by construction.

Found during implementation, and converted with the rest:

| Output path | Sites |
|---|---|
| `print` passed as a value (`out = stdout_fn or print`, `process_hygiene.py`) | 2 |
| prompts: `input(prompt)` 3, `click.confirm` 3 | 6 |
| `subprocess.run(..., capture_output=<flag>)`, inheriting the terminal whenever the flag is false (`ai update`, uv installs, `git checkout`, the layout script) | 5 |
| a child `ai update` whose already-tagged output was replayed under a second tag (`[update] [update] ...`) | 1 |
| the generated script's stderr relay, which passed child lines (direnv, git) through untagged | 1 |
| `launch_logging._log_level` reading `Error:`/`Warning:` at column 0, so tagged lines lost their level in the launch log | 1 |

## Design

One module, `src/ai_cli/output.py`, is the only code that writes to the terminal.

- `Tag` is a `StrEnum` of every log type tag. Each value must match `^[a-z0-9_-]+$`, checked when the
  module is imported. `emit(tag, message)` raises `TypeError` unless `tag` is a `Tag` member, so a bare
  string is refused at runtime as well as by the type checker. A new tag is a one-line enum addition,
  which is the review point.
- `emit(tag, message, *, err=False, nl=True, label=None, ...)` renders each line of `message` as
  `[tag] <label>: <text>`. Multi-line messages get the tag on every line; blank lines are dropped,
  because a line without a tag is exactly what this forbids.
- `warning(tag, text)` / `error(tag, text)` are `emit` on stderr with the `Warning` / `Error` label.
- Styling: `[tag]` dim, `Warning` bold yellow, `Error` bold red, the launch phase label bold cyan,
  `Ready` bold green. Colour reaches a TTY only, and `NO_COLOR` / `TERM=dumb` turn it off. These are the
  existing reporter rules (AI-CLI-1o9b D-1), moved here so every command gets them.
- `raw(reason, text)` writes untagged text, and `reason` must be a member of `Untagged`, a closed enum
  whose members are the named allowlist: machine-readable JSON, `ai internal` protocol replies, the Claude
  Code statusline segment, the `--version` string, Click help text, and terminal control sequences. Each
  untagged write is therefore greppable and justified at its call site.
- `run_relayed(tag, argv, ...)` runs a child with its stdout and stderr captured and emits each line
  tagged. A leading `<tag>: ` the child already prints is dropped, so `dolt_server: healthy` becomes
  `[dolt_server] healthy`, not `[dolt_server] dolt_server: healthy`.
- A logging handler on the `ai_cli` logger renders records as `[log] ...` so nothing reaches `lastResort`.
- `LaunchReporter` keeps its phase grammar and becomes a client of `output`: its `[launch]` prefix is
  `Tag.LAUNCH`, and it no longer calls `click.echo` itself.

Enforcement (T-04) is an AST test over `src/ai_cli/` that fails on any of: a `print` call, any reference
to `click.echo` / `click.secho`, any `sys.stdout` / `sys.stderr` reference other than `isatty`, `fileno`,
`flush` or `encoding`, `os.write` to fd 1 or 2, a `logging.StreamHandler`, and a `subprocess` call that
lets the child inherit stdout or stderr. Allowlist, each entry named in the test with its reason:

| Entry | Why |
|---|---|
| `output.py` | the interface itself |
| `launch_logging.py` | wraps `sys.stderr` to mirror already-rendered lines into the launch log file; originates none |
| `ssh`/`mosh` transports (`transport.py` `run_ssh_with_reconnect`, `_run_transport_loop`) | interactive terminal handoff: the remote session owns the terminal |
| `sudo -v` (`native_deps.py` `_authenticate_root`) | the password prompt must reach the terminal; capturing it hangs the install |

circusd, which daemonizes and logs to its own `logoutput` file, now gets `DEVNULL` rather than an
allowlist entry: a pipe would be held open by the daemon, and an inherited terminal is the bypass.
The macOS `open -na` and the iTerm2 layout script run through `run_relayed`.

The generated session shell script gets the same rule at its own level: a test renders it and fails on
any `echo` / `printf` user message that does not start with `[session] ` or another tag.

## Tasks

### T-01: `ai_cli.output`

- [x] `Tag`, `Untagged`, `emit`, `warning`, `error`, `raw`, `run_relayed`, logging handler.
- [x] `emit` with a non-`Tag` raises `TypeError`; `raw` with a non-`Untagged` reason raises `TypeError`.
- [x] TTY colour, and no `\x1b` on a non-TTY or under `NO_COLOR`.
- [x] `LaunchReporter` renders through it; its existing tests stay green.

### T-02: Route every Python output site through it

- [x] Every site in the inventory converted; hand-copied prefixes (`[memory-watch]`, `ai-cli:`,
      `ai-cli-utils:`, leading `Error:`/`Warning:`) become the tag or the label.
- [x] `--json`, `ai internal` replies, statusline, `--version`, help and escape sequences go through `raw`.
- [x] Click usage errors render tagged (`[cli] Error: ...`).

### T-03: Child-process relay

- [x] The dolt supervisor, circusd and macOS `open -na` run through `run_relayed` (or have no terminal
      output at all), so `dolt_server: healthy (socket accepted)` prints as `[dolt_server] healthy (socket accepted)`.

### T-04: Enforcement

- [x] AST test with the allowlist above; adding a raw `print` turns it red (mutation control).
- [x] Session-script test: scans `session_script.py` for every `echo`/`printf` message that reaches the
      terminal and requires a registered tag; the script's stderr relay tags untagged child lines `[session]`.

### T-05: Proof through the real entry point

- [x] `ai c 7 --dry-run`, an `ai c -R 2` launch to its ssh handoff (and a dropped link), and a Click usage
      error, all through `ai_cli.main.cli`: every emitted stdout and stderr line matches `^\[[a-z0-9_-]+\] `,
      and the supervisor's stubbed `dolt_server: healthy (socket accepted)` arrives as `[dolt_server] healthy ...`.
- [x] `ai internal allocate-session-name` stdout stays a bare JSON document; `ai iterm2 sessions --json` stays parseable.

### T-06: Docs

- [x] README "Launch output" section and `docs/designs/architecture.md` name the interface and the rule.

## Decisions

| # | Decision | Options | Chosen | Status |
|---|---|---|---|---|
| D-1 | Where the interface lives | (a) grow `LaunchReporter` into a general writer, (b) new `output` module the reporter uses | (b) | ✅ Resolved by engineer (Claude) |
| D-2 | Human-readable tables and reports (`ai notifications`, `ai quota history`, session-adopt reports) | (a) tag every line, (b) allowlist them as data | (a) | ✅ Resolved by engineer (Claude) |
| D-3 | Click's own `--help` text | (a) tag every help line, (b) `Untagged.HELP` | (b) | ✅ Resolved by engineer (Claude) |
| D-4 | Blank separator lines | (a) emit `[tag]` alone, (b) drop them | (b) | ✅ Resolved by engineer (Claude) |

D-1: the reporter is a phase grammar (`Phase: outcome`, heartbeats, elapsed) that only fits a launch;
forcing `ai quota history` rows through phases would be a worse fit than a small module both share. It
still satisfies "extend the existing reporter if it fits": the reporter keeps its API and gains the
shared sink, so there is exactly one writer.

D-2: the ask says every line. A table is only untagged where a program parses it, and none of these is
parsed (each has a `--json` form where one is needed), so they are tagged. Con: wider lines; accepted.

D-3: help is the CLI's reference text rendered by Click, not a log line, and tagging it means replacing
Click's help formatter. Kept untagged and named in `Untagged` so the exception is explicit. If the
operator wants help tagged too, it is a contained follow-up.

D-4: a lone `[tag]` fails the `^\[tag\] ` contract and carries no information; sections are separated by
their own heading lines instead.

## Open Questions

1. Should Click's help text be tagged as well (D-3)?

## Approval Log

| Date | Decision | Actor | Notes |
|---|---|---|---|
| 2026-10-11 | D-1..D-4 | engineer (Claude, Opus 5.5) | Resolved under the Authority test: no new dependency, reversible, no interface change outside this package. The launching session reviews before merge. |
