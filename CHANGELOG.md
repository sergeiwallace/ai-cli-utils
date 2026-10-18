# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Validated `[restart_session]` settings for safe session restarts, with a default
  context threshold of 20 percent and configurable idle, uptime and cooldown gates.
- A guarded restart engine validates process identity, background work and Remote
  Control activation, owns a per-session OS lock, and records durable restart pins
  and verification results. Unknown evidence refuses an action.
- `ai -X/--restart-session [TARGET]` and `-Y/--restart-check [TARGET]`, also on
  `ai c`, resolve titles, conversation IDs or managed sessions. Requested restarts
  resume the pinned conversation through the existing supervisor without replacing
  its pane, with configured `/exit` injection as a fallback.
- `ai -X` now runs the `/save-state` pre-step when a session's context is over the gate. The
  configured injection adapter submits `/save-state` once; the command waits for a new
  compaction record and a fresh idle interval, then rechecks every gate before restarting.
  Adapter failures, a held or queued submission, a lost lock or a changed process refuse
  without retrying or signalling. `-Y` never runs it.
- Automatic restart evaluation: with `[restart_session] auto = true` (off by default), each
  supervisor heartbeat starts a detached evaluator once the session's `auto_interval_seconds`
  have elapsed. It restarts only for a verified newer native install, the update-installed
  notice, or a positively observed reachability failure, through the same gates, lock and
  cooldown as `-X`. Unknown or unobserved results never trigger a restart.
- The restart gates now certify long-lived sessions: the whole transcript history is scanned in
  bounded memory (no 8 MiB limit; unreadable, malformed or unmatched evidence still refuses), and an
  installer-generated `restart-session.d/mcp-servers.toml` lets servers started through a wrapper or a
  title-rewriting launcher count as MCP roots when their exact argv, executable and file stamps match.
  Stale stamps refuse and ask for regeneration; a missing file keeps the strict behaviour.
- Local tmux launches now open a detached `wake-watch` window when the effective
  sidecar mode is `session` or `both`. The watcher retries every exit after two seconds.
- Every launch now records durable session run state (`running` or `exited`, with a cause and
  the host's boot id) in the session registry, independent of terminal tracking and hook
  evidence. A launch that cannot persist its state is refused. The launch recipe is stored
  with the record, and the supervisor settles the final exit before the shell returns.
- `ai sessions mark` sets a session's run state by hand, and the new `[session_relaunch]`
  settings (`on_boot` off by default, permitted causes `host_reboot` and
  `terminal_quit_or_crash`) are validated on every config load. The optional
  `[session_relaunch.publish]` settings publish a debounced registry snapshot to a Git ref with
  plumbing commands, never touching a checkout. Boot relaunch itself ships separately.
- `ai sessions relaunch --boot` relaunches, detached in tmux and without a terminal application, the
  sessions that were running when the host shut down. It follows the `[session_relaunch]` switch
  (off by default), applies the ordered vetoes (a session exited by hand or with an unknown cause never
  relaunches), refuses a second copy, reserves each attempt per boot so a retry cannot spawn twice, and
  `--dry-run` prints every decision with the winning veto.
- Local `ai c`, `ai g`, `ai p`, and `ai cx` launches now ask a discovered ai-harness
  checkout to idempotently ensure its machine wake sidecar is running. The bounded check
  never blocks the session after a failure and is skipped for remote launches and
  unsupported platforms. The new `[sidecar]` configuration defaults to disabled.

- `ai iterm2 restore --startup` now opens the default arrangement on a cold start, when no
  selected session survived the iTerm2 restart, before it fills the arrangement's idle panes.
  When any session survived, iTerm2's own window restoration already brought the windows back,
  so the run prints why the arrangement was not opened instead of opening a duplicate set.

- A bare session hosted on the launcher's pty now records when the operator last typed, in
  `<session>.input-at` beside its submission FIFO (one line, epoch seconds), written atomically
  at mode 0600 from launch until exit. A submitter that must not write over a draft can read it
  instead of the terminal's access time, which Linux moves only in 8-second steps. Text arriving
  through the FIFO does not move it.

- `ai c NEW` (and every engine launch) now runs the pending `session-migrate --finish` of any
  migration to NEW this host's `session-migrations.jsonl` records, before the session starts,
  so a migration made while NEW was live completes on its next launch. A finish that completes
  appends a `"finished": true` row for the pair, from `ai c` or a manual `--finish`, so it runs
  once; one that refuses or fails is reported on one line with the retry command and never
  blocks the launch.

- `ai session-migrate` (and `--finish`) now re-keys the chief-of-staff registration `ai cos`
  wrote when it names OLD (`state/chief-session.json` and `registry.json`'s chief fields,
  leaving it named and exiting 1 while its tmux target is live), and renames OLD's
  `launch-logs/` files for NEW so NEW's retention prunes them. Resume `evidence/` files
  (`<key>-<attempt id>-<reason>.txt`) are now re-keyed with the rest of the resume tree. The
  per-class table in `docs/tools/cc-session-adoption.md` lists every class found left under
  the old name, its owner and its disposition (AI-CLI-2oes).

- `ai session-migrate` now cleans up the old name in every other repo once the session has
  moved: a `.worktrees/OLD` slot that is clean and landed is retired the way
  `ai session-retire` retires one (tips anchored, checkout removed), an empty `OLD-agents`
  container is removed, and anything kept is named with its reason and makes the command
  exit 1. Run with `-r` on a repo whose OLD slot is such a leftover, it does only that
  cleanup, so it no longer refuses while NEW is live in its own slot elsewhere.
- `ai session-migrate OLD NEW --finish` carries or removes every leftover still named OLD for a
  slot that already moved to NEW, hooks included, so a migration run before a step existed
  completes without hand steps.
- `ai session-migrate` now also renames memory files named for the session (`active-work-OLD.md`,
  `.resume-OLD-N.md`) and the links to them in `MEMORY.md`, removes an emptied
  `.worktrees/OLD-agents/` container, and removes the old name's slot lock and watcher lock
  once no process holds them.
- `ai c` and `ai g` accept `-d` as the short form of `--dry-run`.
- Every `ai c`, `ai g`, `ai p` and `ai cx` launch prints a `host` line naming the machine the
  launcher runs on and the machine the session will run on, so a `-R` launch says where it
  is going: `launcher on host-a; session on box (box.example.com, remote)`.
- Lines the session script prints now reach the session's launch log as the same
  record the terminal shows, and its warnings and errors, such as a failed direnv load,
  also reach telemetry. A reload row is stored only once it has escalated to a warning
  or an error. When the renderer cannot run, the plain fallback line is still appended
  to the log. `[output.sinks.file]` gains `min_level` (default `detail`, every level),
  like `[output.sinks.telemetry]`.
- The per-launch log and telemetry now come from the same record as the terminal line,
  so all three carry the same time, tag, operation, level and text. The log file gets
  the line's elements as plain text and always starts with the time, even when the
  element is off for the terminal. It also gets `-v` detail lines and lines `-q`
  suppresses. A line's level in the log is the record's own: an info line that mentions
  "failed" is no longer logged as an error. Warnings and errors (`min_level`) are stored
  in the local telemetry database as `telemetry.log.<tag>` events, in one batch just
  before the launch hands off or the command exits, and never published to the message
  bus. The new `[output.sinks.terminal]`, `[output.sinks.file]` and
  `[output.sinks.telemetry]` tables turn each sink on or off, and
  `[telemetry] enabled = false` also stops the telemetry sink. A launch-log directory
  that cannot be written, or a telemetry store that is locked or unwritable, no longer
  stops or delays the launch.
- Every line an `ai` command prints now starts with the time it was produced, as
  `2026-07-01 14:30:00 EDT`: 24-hour, in US Eastern time by default, dim on a terminal.
  The `[output.elements.datetime]` table sets its `format`, `timezone` (`UTC`, `local`
  or an IANA zone name), delimiters and `style`, or turns it off with `enabled = false`.
  An invalid format or zone is refused when the table is read, naming the file and the
  key, and lines render with the defaults. The session script's plain fallback line
  carries the same time.
- When a session reloads its script, the pane now shows one row that updates in place,
  `[session] reload: session script updated, reloading (attempt 3, 4s)`, instead of
  printing the same `session script updated — reloading...` line once per reload. The
  row names the reason, the attempt and the time since the first consecutive reload.
  It turns into a warning from the third consecutive reload, and at the sixth an error
  line naming the count and how to stop the session (Ctrl+C twice within 3 seconds) is
  also printed and stays in scrollback, so a reload loop gets louder rather than looking
  healthy. Both thresholds are `warn_after` and `error_after` in the new
  `[output.progress.reload]` config table. When stderr is not a terminal, each reload
  prints one plain counted line. Sessions already running keep their old supervisor
  until they are started again, and until then their reloads print nothing.
- A bare `ai c` launch (`-b/--bare`, `[session] use_tmux = false`, or no usable tmux) now
  runs the agent on a pseudo-terminal the launcher owns instead of replacing itself with
  the agent. Text written to the session's FIFO, at
  `$XDG_STATE_HOME/ai-cli-utils/pty/<session>.in` and passed to the agent as
  `AI_CLI_SUBMIT_FIFO`, is read by the agent as typed input, so the auto-compact prompt
  can be submitted without tmux. Keystrokes, Ctrl-C and window resizes reach the agent
  unchanged. SIGTERM and SIGHUP are forwarded to it, and the terminal is restored on
  every exit. There is no scrollback, detach or reattach on this path; tmux remains the
  default transport and is unchanged. Windows keeps its current bare launch.
- Every line an `ai` command prints now names the operation it belongs to, as
  `[<tag>] <operation>: <text>`, in bold cyan: `[transport] reconnect: Connection lost ...`,
  `[dolt_server] ensure: healthy ...`. Launch phases are operations too, so
  `[launch] Worktree: ...` becomes `[launch] worktree: ...`, the start line becomes
  `[launch] start: Starting Claude Code session (local, tmux)`, and the last line is
  `[launch] handoff: ready, handing off to ...` in bold green. A failed or interrupted
  phase reports at error or warning level under its own operation. The allowed
  operations, whether a line must carry one, the default for a line that has none (a
  logging record, `[log] general: ...`), the colour and the pattern all live in the
  `[output.elements.operation]` config table; a user table may add operations but not
  remove the shipped ones. `ai internal emit` takes the operation after the level:
  `ai internal emit <tag> <level> <operation> <terminal|plain> <text>`.

### Changed

- Every line an `ai` command prints is now rendered by one renderer, and every element
  of it is styled. `[transport]` and `[dolt_server]` lines used to print plain; the tag
  is now bold blue on every line (it was dim), `Warning:` and `Error:` keep their colours,
  and text passed in with its own escape sequences is shown without them. The session
  script's messages go through the same renderer via a new `ai internal emit`, and fall
  back to the same text uncoloured when it cannot run. A nested `ai` command such as the
  launch-time `ai update` hands its lines to the parent as records, so each keeps its own
  tag instead of being tagged twice. structlog and Rich are new runtime dependencies; the
  `--version`, statusline and `ai internal` reply paths do not import them.

### Fixed

- `ai copier-update` no longer fails an update because a project's pre-commit formatter rewrote
  files. The hook aborts the commit but leaves its fixes in the tree, so the update now stages them
  and commits once more through the same hooks. A hook that rejects without rewriting, or keeps
  failing after the retry, still fails the update (anchor and recovery issue as before).
- `ai copier-update` no longer fails with "Destination repository is dirty" for a project whose
  stored template source is spelled differently from the one it resolves to (an SSH URL read over
  HTTPS, or a bare or relative name read from the sibling checkout). The source rewrite is now
  hidden from `git status` for the length of the Copier run and the stored spelling is restored.
- `ai copier-update` no longer reports a nested file as missing when the template's
  `_skip_if_exists` skips it by pattern. An entry such as `README.md` covers a README at any depth,
  as Copier reads it, so a project's own `docs/audits/README.md` is left alone and the parity check
  now agrees. A nested file the project does not have is still reported.
- `ai copier-update` now finds a template stored as a bare name such as `project-template`. The
  name was only looked up inside the project, so a project that records the sibling checkout this
  way failed with "could not inspect template diff". A copy inside the project still wins; otherwise
  the sibling in the projects directory is used.
- `ai copier-update` now updates projects whose stored template source is a GitHub SSH URL on a
  host with no SSH access. The reachability of the SSH source is probed once per run and, when it
  fails, the same repository is read over HTTPS for that run only; the stored answer is untouched.
  Before, every such project failed on framework-fedora with \"could not inspect template diff\".
- `ai copier-update` no longer reports a conflict for a changed file that merely mentions the
  conflict marker inside a line (a comment quoting `<<<<<<<`). Only a line that starts with the
  marker counts, the shape Git and Copier write.
- `ai copier-update` parity no longer reports a file as missing when Copier legitimately leaves it
  out: a path the template excludes for the project's answers (the python-only `pytest_memory_guard.py`
  for a TypeScript project, now rendered with the answers and the question defaults), or a file the old
  template shipped that the project has removed. A file Copier skipped that the project still has keeps
  failing. Adds the `jinja2` dependency for the exclusion rendering.
- `ai copier-update` no longer reports a file Copier added inside a new directory as missing from
  the update. `git status --porcelain` collapsed a wholly new directory to one `?? dir/` entry, so
  the template parity check failed every project the first time a template added a directory
  (all eight fleet projects on `.claude/skills/lint-md/SKILL.md`). Status is now read with
  `--untracked-files=all` on both the isolated and the `--no-isolate` path. Changed paths are
  compared as POSIX paths, so a file in a subdirectory also matches on Windows.

- Isolated `ai copier-update` conflicts and semantic failures now save tracked,
  conflicted and non-ignored untracked files in a verified
  `refs/backup/<UTC date>/copier-update/<repo>-<template sha>` commit before removing
  the temporary checkout and branch. Repeated failures comment on the repository's
  existing recovery issue through Beads; new issues use `~/.claude/bin/bd-new`.
  Failed backup verification keeps the checkout untouched and reports an error.
  Rejected pushes retain their committed update in a clean checkout, backing up
  any additional dirty content first. Start-of-run leftover protection is unchanged.

- `ai iterm2 restore` no longer opens tabs or windows, and no longer types into a pane that
  is not an idle shell at the moment of the write. A session goes back only into its own
  recorded pane when that pane is an idle shell; otherwise it is skipped with the reason. At
  `--startup`, when any session survived the iTerm2 restart, records without a client are
  not relaunched, because iTerm2 kept every session that was running. A record whose
  relaunch would attach to a session that is already open, or that another record already
  relaunches, is skipped, so a stale record can no longer re-dial a live remote session and
  detach it from its pane. Before this, an iTerm2 restart opened a new tab for every record
  whose launcher had long since exited, three of those detached live remote sessions to the
  shell, and one "new tab" write typed its command into a running Claude Code prompt.

- Remote mosh sessions share a VPN-state cache for the configured `vpn_poll_interval`,
  so concurrent tabs run one detection command per interval. Detection and cache locking
  run in worker threads to keep the transport event loop responsive.

- `ai session-migrate OLD NEW` now re-keys Beads assignees. Every non-closed issue assigned to
  exactly OLD, in every store under the projects dir, is reassigned to NEW through `bd --actor
  OLD update --if-assignee OLD` and given a comment naming the migration, so the migrated
  session can close and reassign its own issues without `--force`. `--dry-run` lists each
  issue per store; a store bd cannot read is named and fails the run; each changed store is
  named as needing its publish step. `--finish` re-keys issues a past migration left on OLD
  when this host's `session-migrations.jsonl` has a row naming exactly that pair, and lists
  them unchanged when it does not. `--beads-only` does just that re-key and runs while NEW is
  live.

- `ai session-audit` judges a session by the most recent cwd its transcript records that exists
  on this host, not by the first one. A session that moved hosts or was migrated now reads
  `in-slot, resolvable`, and a session whose repo does not exist on this host is skipped, never
  offered for adoption into a missing path.
- A session no longer prints `no such file or directory: ...session-reload-reason-...` each
  time its agent exits without a reload pending.
- A reload after the agent has started and run counts from attempt 1 again. Every
  update's reload used to add to one count for the life of the session, so the third and
  sixth showed the reload-loop warning and error though nothing was looping.
- A Ctrl+C that reaches a session while it is still starting its next agent (a reload
  after an `ai` update, or a restart) now ends the session and removes its tmux session.
  It used to exit without that teardown, and because the window keeps exited panes, the
  pane stayed up dead and ignored every key.
- `ai update` replaces a running session's script instead of rewriting it in place. The
  session's supervisor is executing that file, and the shell reads a running script as it
  goes, so an in-place rewrite made it continue partway through the new text. Seen as a
  reload that ran the wrong lines and looked for `session-reload-reason-` with no session
  name.

- A telemetry store that another process holds locked no longer delays a launch's
  handoff past its 100 ms budget (102 to 111 ms on a macOS runner). Two costs added up:
  the batch looked up the machine id before trying for the lock, and its 30 ms busy
  timeout ran as a series of short sleeps that a loaded macOS runner overshoots. The lock
  is now tried once with no wait, a store that is held drops that telemetry batch at once
  (the launch log keeps every record), and only a batch that holds the lock looks the
  machine id up (AI-CLI-n0d7).
- `ai session-retire` now retires a slot whose branch was squash-merged. It used to
  refuse because the branch's commits were not ancestors of `origin/main`, which a
  squash merge never makes them. It now checks content: every line the branch added
  since its merge-base must be on the target, have been there in a commit since then,
  or have replaced a line the target also rewrote. A branch with any other added line
  still refuses, and the refusal names the file and the line. The tip is anchored as
  before. The refusal no longer names `refs/remotes/origin/main` twice when the
  branch tracks it.
- The test suite no longer reaches the tmux server you run it under. A test that made a
  bare `tmux` call, such as the launch report's server-version query, used tmux's default
  socket, which is your live server. Every test now gets its own `TMUX_TMPDIR` under
  `/tmp` with `TMUX` unset, and any server a test starts there is killed when it ends. A
  killed run's leftovers are killed by the next run. The suite-end cleanup that listed
  and killed test-named sessions on your default server is gone.
- Under zsh on macOS, a session whose agent ends cleanly is no longer restarted. The
  session shell's wrapper used to stop itself until it was handed the terminal, and zsh
  then reported that stop and resume (19) in place of the agent's real exit, so a clean
  final exit read as "restart". The wrapper now waits for the handoff without stopping.
- On tmux 3.8 the stale-session reaper, and a relaunch over a session whose pane has
  died, no longer fail for every session when one pane is dead. tmux 3.8 stopped printing
  a dead pane's pid, and the reaper read that empty pid as a malformed listing, so it
  decided nothing and a relaunch reattached to the frozen dead pane. A dead pane is now
  identified by the time tmux recorded its death. A pane that has neither a pid nor a
  death time is never killed, and the other sessions are still decided.
- A session started with no terminal attached (a detached `ai c`) no longer stalls for
  several seconds on a busy machine before starting the agent. Its wait for a terminal to
  attach is now capped at about a second of real time; it used to count polls, and on a
  loaded host each poll took long enough to stretch that wait to 7 seconds.

- `ai c N -R` now titles the iTerm2 pane after the session the remote host actually
  opens. For a numeric slot the launcher skipped the remote allocator and composed the
  name itself as `<engine>-r-<repo>-<N>`, without the remote host's alias, so a migrated
  session showed its old name in the pane, and its transport state and cleanup were
  keyed to a session that did not exist. Inside a session, a pane rename that iTerm2's
  scripting API cannot make (always the case on the remote end of `-R`) now falls back
  to the OSC 1 title sequence, so the session's own name reaches the pane over ssh.

- A remote session that stops relaunching its agent, for example after `/login` forces
  an exit and the next launch fails before you have logged in again, now tells you how
  to get the agent back. The shell it leaves in the tmux pane lists `exit 78` to
  relaunch the agent in place, resuming the conversation, and plain `exit` to close the
  session. It used to name only the way to close it, so the only route back to the agent
  was to tear the session down and start again from your local machine.

- Sending SIGTERM or SIGHUP to `ai c -R` (closing its terminal window, `kill`) now
  forwards the signal to ssh or mosh, waits for it to restore the terminal, then runs
  the session's cleanup and records a `terminated` outcome. The launcher used to die
  on the spot, leaving the terminal in raw mode with mouse reporting on.

- A restarted agent's terminal is now reset with the same sequence the remote transport
  sends, autowrap and colour reset included. The session script kept its own copy, which
  had drifted and sent a different modifyOtherKeys form.

- `ai c -R` reattach output no longer runs lines together or joins the last line to the
  shell prompt. While ssh held the terminal in raw mode, a relayed line ended in a bare
  line feed and so did not return to column 0. Text the remote side left after a carriage
  return stayed on screen next to the next local line. Every line now ends in CR LF on a
  terminal, and control characters in relayed text are removed. When an ssh attempt
  returns, the launcher restores the terminal's output settings and starts a fresh row.
  Lines are also coloured again after the per-launch log starts: its stderr mirror had
  made the terminal look like a pipe. Every exec now flushes output first.

- `ai c firstmate-N -R` no longer loops on `no machine key`. The remote side runs in a
  login shell that is not interactive, so a machine key exported in an rc file below its
  "not interactive: return" guard never reached it. With no key, the remote `ai` now uses
  the one chief home the installer seeded on that machine, and refuses, naming each one,
  when there are several. A remote launch that refuses now exits 78, and the ssh reconnect
  ladder stops on that code with one message instead of retrying the same refusal ten
  times. A chief registered under a name the host no longer gives it (its host alias
  changed) is refused with the `ai session-migrate OLD NEW -r DIR` command to run first,
  where it used to start a new conversation under the new name and leave the old worktree
  behind.

- `ai register -p DIR` now refuses a directory that is not itself a repository root but
  sits inside one, naming the containing repository. It used to register that ancestor
  and rewrite the `issue-prefix` in its `.beads/config.yaml`, a repository the caller
  never named. Registering a linked worktree still registers its main checkout. The test
  suite also refuses to start when its temp root (`--basetemp`, or the system temp dir)
  is inside a Git checkout, which is how a test run rewrote a real repository's prefix.

- `ai c -R` on a machine with no `[remote] host` now says which machine it ran on, that `-R`
  launches on the configured remote, and both remedies: drop `-R` to launch here, or run the
  command from the client machine. The bare "host not set" error left an operator who had
  simply typed the command on the remote itself with no next step.

- `ai session-migrate` now moves a worktree that holds initialised submodules, which
  `git worktree move` refuses. It moves the directory, runs `git worktree repair`, and
  repoints each submodule's `core.worktree`, so `git status` works in the moved
  worktree and its submodules. A locked worktree is still never moved.

- `ai c` now starts the independently supervised stale-session reaper when it is
  missing and retries after Circus or the worker dies. Startup is asynchronous,
  singleton across concurrent launches, observe-only by default, and a no-op on
  native Windows where tmux is unavailable. A startup failure warns with the
  manual recovery command but never blocks the session launch.

### Added

- An `[output]` table in `config.toml` controls every element of every output line:
  which elements appear, their order, the separator, each element's prefix, suffix and
  colour, per-tag, per-operation and per-level colours, and the regex patterns values
  must match. It is read again whenever the file changes, so an edit applies to the next
  line of a running session. An invalid table is ignored as a whole. Lines then render
  with the shipped defaults, and each problem is reported once as a `[config]` error
  naming the file and the key. With no `[output]` table, output is unchanged. Turning on
  the datetime element or requiring an operation is refused as not supported yet. The README's
  "Output configuration" section lists every key.

- The commit-msg hook now runs: `.githooks/install.sh` installs the commit-msg stage, and a
  `feat:` or `feat(scope):` commit that does not stage a `CHANGELOG.md` change is refused. The
  script existed before but no hook stage ran it, so about half of recent features reached
  `main` without an entry. `SKIP_CHANGELOG=1` bypasses it for one commit.

- `ai session-adopt -t/--transcript FILE` adopts a transcript whose source directory no longer
  exists, the case where `-s/--source` cannot be guessed. It refuses alongside `-s` or `-a`, and
  refuses a file that is not the one its title resolves to.

- `ai session-retire NAME` retires a dead session's home slot `.worktrees/NAME`. It
  refuses, before writing anything, while the session runs (a Claude Code process, its
  tmux session or a fresh heartbeat), while the checkout holds uncommitted or untracked
  work, or while its checked-out commit is in neither its upstream nor `origin/main`.
  Otherwise it anchors the branch tips under `refs/preserved/session-retire/`, runs
  `git worktree remove`, drops the canonical-registry entry and appends a row to
  `session-retirements.jsonl`. The branches and the Claude Code project directory stay.

- `ai c -R` over ssh now says what a long-lived launcher is running and how it is doing.
  Each drop notice gives the connection's uptime. The outcome record in
  `transport-outcomes/` names the launcher's pid, launch time and loaded commit, and it is
  rewritten at every drop, so a running launcher's remaining budget is on disk. When an
  update has been installed since launch, a drop warns once that the launcher's code is
  older and that reattaching with the same command loads the fix.

- `ai session-migrate OLD NEW` now also carries the session's delegated work in every
  repository under `[project] projects_dir`. Agent worktrees under
  `.worktrees/OLD-agents/` move to `.worktrees/NEW-agents/`, and `wt/OLD/*` branches,
  checked out or parked, are renamed with each tip anchored under
  `refs/preserved/session-migrate/` first. Session-home markers naming the old slot
  follow. A worktree a live Claude Code process is working in is left alone with its
  branch, an existing destination is skipped, and a git refusal warns and exits 1.

- Each iTerm2 session-registry record now carries why its session ended (`exit`:
  `manual_exit`, `terminal_quit_or_crash`, `host_reboot` or `unknown`) and the boot it
  was launched in; `ai iterm2 sessions --record-exit NAME --cause CAUSE [--evidence JSON]`
  records one. Before relaunching, `ai iterm2 restore` classifies each record whose
  session is proved gone, relaunches it only when
  `[iterm2.persistence.restore.relaunch.scenarios]` allows its cause (a manual exit is
  not relaunched by default), lists the rest as skipped with their cause, and launches
  each relaunched session with `AIH_LAUNCH_REASON=<cause>`.
  `[iterm2.persistence.restore.relaunch] on_terminal_reopen = false` makes
  `restore --startup` relaunch nothing. A registry written by an earlier version reads
  as having no exit causes and is upgraded on its next change.

- `ai iterm2 sessions --adopt [--dry-run]` records the sessions that were already
  running before the iTerm2 session registry existed, so `ai iterm2 restore` can bring
  them back: each local tmux session named `c-<prefix>-<n>` (re-attached with
  `ai c -p <project> <slot>`) and each running `ai c -R` launcher (re-run with its own
  command line). A session that already has a record is left alone, a command line that
  cannot be read or parsed is skipped with its pid and the reason, and tracking switched
  off is a refusal naming the key. Run it once after installing. See
  `docs/iterm2-persistence.md`.

- `ai iterm2 restore [--startup] [--dry-run] [--only local|remote] [--arrangement NAME]`
  re-attaches the sessions in the iTerm2 session registry after an iTerm2 restart
  (macOS). It opens the saved arrangement on demand (through iTerm2's `it2` when it
  answers, else it prints the menu path), then types each session's `ai` command into
  its recorded pane when that pane is an idle shell, or into a new tab, staggered, and
  prints one `restored`, `skipped` or `dead` line per session. A session that is
  already attached is skipped rather than pulled out of the pane showing it. Off
  unless `[iterm2.persistence.restore] enabled = true`; `--startup` is the form an
  iTerm2 startup hook runs. See `docs/iterm2-persistence.md`.

- `ai c` now requires a way to submit its auto-compact prompt. Either transport
  satisfies it: tmux where tmux hosts the session, or a harness-owned pty on the
  bare paths (`-b/--bare`, `[session] use_tmux = false`, an unusable tmux). The
  transport is fixed by the terminal that owns the engine process and cannot be
  added later, so a session started without one did not fail at launch — it failed
  hours later with a full context and no managed way to compact.

  Three outcomes. A transport resolves and the launch proceeds. A transport was
  achievable here and is not available (`[session] use_pty = false` with no tmux,
  or a pty that will not allocate) and the launch is **refused**, naming what each
  side failed on, above every write it would perform, so nothing is left to clean
  up. Or no transport is implementable on the platform at all — `os.openpty` is
  POSIX-only, so a Windows host without tmux can satisfy neither side — and the
  launch **proceeds** with one notice naming the upgrade path. Windows is not
  refused: a condition no in-process action can satisfy is not a guard, and what is
  lost there is harness-*driven* compaction, not compaction itself, since the
  engine's own auto-compact needs no terminal.

  The pty carries the submission only: your terminal stays the terminal, and the
  pty path provides no detach/reattach and no scrollback.

- `-F/--firstmate` launches a machine's single chief-of-staff session on **any**
  engine (`ai c`, `ai g`, `ai p`, `ai cx`), with `ai cos` kept as the Claude
  shorthand. It resolves the chief home from `-k/--machine-key` (default
  `$AI_MACHINE_ID`) or `-H/--fm-home` — both refused without `-F` rather than
  silently ignored — refuses a missing or unseeded home, `-R/--remote`, and a
  second chief whose tmux session is still running, writes
  `state/chief-session.json` and the schema-2 `registry.json` (generation and
  route revision incremented, VP entries preserved), and hands off to the
  ordinary launch with `FM_HOME` plus `AI_SESSION_ROLE=chief-of-staff` forwarded
  into the tmux pane. `--dry-run` registers nothing.

  The session is named `{project-prefix}-{host-alias}-firstmate-[suffix-]{index}` (the
  positional NAME is the optional suffix) off the ordinary index allocator, and
  it gets a worktree at `.worktrees/{project-prefix}-{host-alias}-firstmate-[suffix-]{index}`:
  with every main tree read-only under the worktree model, a chief running in a
  repo root was the anomaly. `-W/--no-worktree` still turns isolation off.

- **A commit that deletes a file still live on the default branch is now rejected.**
  Files have gone missing from worktrees repeatedly and silently — `git status` reports
  a tracked path as deleted, every exit code reads fine, and the loss only surfaces
  later as someone else's work missing from `main`. Three investigations failed to
  identify what removes the file in the first place, and the cause is not identifiable
  even in principle: `rm` + `git add -A` and `git rm` produce identical index states,
  so an accidental deletion and an intentional one are structurally indistinguishable.
  The outcome is guardable, so that is what is now guarded — a missing file is harmless
  and recoverable from `HEAD`, only the commit is not. A new pre-commit-stage hook
  (`live-file-deletion-guard`, `scripts/check_live_file_deletions.py`) compares HEAD
  against the index and refuses any staged deletion of a path that still exists on the
  last-fetched upstream default branch. Only what *that* commit removes can block it,
  so a deletion an earlier commit on the branch made on purpose does not fail every
  later one. Intentional deletions are acknowledged per path with
  `ALLOW_DELETIONS=path/a,path/b git commit` — per path rather than all-or-nothing,
  because an intentional deletion riding along with an accidental one is precisely the
  case worth catching; `ALLOW_DELETIONS=1` and `SKIP=live-file-deletion-guard` are the
  blanket forms. An unresolvable comparison ref fails CLOSED when deletions are staged
  (and passes silently when none are): an unavailable probe treated as "no problem" is
  how a guard comes to enforce nothing while every exit code still reads fine. It never
  touches the network, so the comparison is a lower bound on what is live upstream — a
  stale ref can miss a deletion but cannot invent one, and a false positive is the
  failure mode that gets a guard switched off. The pre-commit framework itself is not
  patched or worked around; restoring unstaged changes after hooks run is its correct
  designed behaviour. Standard library and `git` only, with no import from this
  package, so the checker is copyable into any repo. (`AI-CLI-9sy`)

### Changed

- **CI now refuses to install from a stale lockfile.** All four jobs synced with
  `uv sync --dev`, which RE-RESOLVES silently whenever `pyproject.toml` and `uv.lock`
  disagree — so a pull request could pass every required check having installed versions
  that were not the ones it shipped, and merge a stale lock, with no log line saying so.
  Every sync step now passes `--locked`, which fails with ``The lockfile at `uv.lock`
  needs to be updated`` and names `uv lock` as the remedy. `--frozen` was rejected as the
  alternative on measurement: against one deliberately staled lock, `--frozen` exited 0
  and installed the stale versions while `--locked` exited 1, so `--frozen` makes the
  disagreement quiet rather than loud. Local `uv sync --dev` is unchanged and still
  re-resolves, so an edit-and-run loop needs no relock at every step; CI is the boundary
  where it stops. `tests/test_ci_lock_assertion.py` fails if any workflow sync step stops
  asserting the lock, since the flag is one word that a later unrelated step rewrite would
  drop silently. (`AI-CLI-qqnx`)
- **BREAKING: Python 3.14 is now the only supported version.** `requires-python` was
  `>=3.11` and is now `>=3.14,<3.15`, so installing under 3.11, 3.12 or 3.13 is refused
  rather than silently unsupported, and installing under a future 3.15 will be refused
  until that version is adopted deliberately. The trove classifiers advertise only 3.14.
  The reason is that the version was declared in six independent places that disagreed —
  the local venv on 3.14 while CI ran 3.11/3.12/3.13 — and under that arrangement a defect
  could be *unobservable* locally rather than merely unobserved: `pathlib` allows a
  `WindowsPath` on POSIX on 3.14 and refuses it at or below 3.13, so a test passed every
  local run and went red on four CI jobs. Every CI job now pins 3.14, matching the
  interpreter `.python-version` gives the local venv, so a local run predicts CI.
  `scripts/check_python_version_sync.py` runs as a pre-commit hook and as CI's first lint
  step and fails if `.python-version`, `requires-python`, the pyright target, the
  classifiers, any workflow's pinned version or the running interpreter disagree. The cost
  is real and is not hidden: CI no longer runs more than one interpreter, so
  version-dependent behaviour has to be pinned down by a test that forces it explicitly
  instead of by the matrix. (`AI-CLI-6rwp`)
- Documentation, tests and one source comment no longer name a real machine or
  network. A VPN-range IP address was used as a test fixture and appeared in an
  archived plan document's example config, and several provenance notes
  ("Measured on `<machine>`: ...") named private hosts and the organization whose
  network they sit on. The measurements are unchanged; the names are now generic
  ("an EC2 Linux host", "a corporate-managed machine") and the address is an
  RFC 5737 documentation address. The public-repository hygiene suite gained a
  standing guard for both classes, so the next one fails a test rather than
  reaching a published commit: literal IPv4 addresses are checked against an
  enumerated list of synthetic values plus the RFC 5737 ranges, and the private
  host-name prefixes are matched with a left-only word boundary so a suffixed
  hostname cannot slip past.

### Removed

- The optional `internal-testkit` extra and dependency group, which pinned a package
  to a private git repository. Nothing about it was reachable for a public
  contributor, and its presence in the resolution graph made every dependency bump
  fail: an optional, never-installed extra is still resolved, so a change to
  `pyproject.toml` with no matching relock forced `uv` to re-resolve and fetch a
  revision from a repository the runner cannot read. All four required checks then
  failed before a single test ran, with a git clone error naming neither the cause
  nor the remedy, and the automated dependency updater's own lock-file update failed
  the same way — which is why its pull requests arrived without the relock that would
  have avoided the fetch at all. A new guard
  (`tests/test_dependency_source_hygiene.py`) fails if any dependency or locked
  package is reintroduced from a VCS or direct URL rather than a package index.
  The now-orphaned `testkit_enabled` pytest option went with it, which also silences
  the `Unknown config option` warning every run emitted. (`AI-CLI-f8la`, `AI-CLI-qrkr`)

### Fixed

- **`ai c <name> -R` kept running a stale client and said it was current.** A remote
  launch updated the remote host's ai-cli-utils but never the local checkout running
  the launch, and the install check only compared that checkout's files with what was
  last installed from them, so an unpulled editable checkout printed
  `Install: editable checkout ...; current` while missing the fix it needed. A remote
  launch now fetches `origin/main` into an editable client checkout and fast-forwards
  it when it is clean and on `main`, then restarts on the new code. Otherwise it leaves
  the checkout alone, says how many commits behind it is, and prints the command that
  fixes it; a failed or timed-out fetch reads `could not check origin/main` and the
  launch continues.

- **An accidental Ctrl+Z froze an `ai c` session with no way back** (AI-CLI-y6el).
  Claude Code stops its whole process group on Ctrl+Z and says to run `fg`, but an
  `ai c` pane has no job-control shell to run it, so nothing typed into the pane
  could ever resume it, and the only way out was a kill that lost every background
  agent and shell. Each session now runs a small watchdog that continues a stopped
  foreground group within about a second, and `ai c <n>`, `ai c -r`, `ai attach` and
  the `ai ls` picker resume a stopped pane before attaching. A session process stopped
  in its own terminal is now resumed instead of ended by the AI-CLI-2139
  reclamation, which still ends a stopped process with no terminal attached. That
  decision is made on a fresh reading of the process, so a session the watchdog has
  just resumed is never ended on a stale one.

- `ai c` no longer prunes every stale worktree registration in the repository on
  launch. It removes only this slot's own stale entry (one git marks `prunable`,
  whose directory is gone, at the slot's path or holding its `wt-<name>` branch),
  so another slot's registration, with its index and HEAD, survives. An
  unreadable worktree listing removes nothing.

- `ai copier-update` no longer destroys the temp worktree a conflicted or
  rejected run left for manual resolution. The pre-clean at the start of each
  repo's update now removes a leftover only when it is provably disposable (a
  clean checkout and branch at the base commit). One holding uncommitted or
  staged changes, commits not on the base, a `MERGE_HEAD` or other in-progress
  operation, or any state it cannot read is kept and reported as `LEFTOVER KEPT`
  with its path and reason (exit 3). Its teardown no longer runs a repo-wide
  `git worktree prune`.

- **An auto-restarted agent could be handed a pane that turned every key into
  escape text.** Claude Code arms SGR mouse tracking and modifyOtherKeys, and tmux
  keeps both as pane state after the process exits. When an agent died without
  disarming them, its replacement received mouse motion as `[<35;x;yM` text and
  Ctrl+C as `[27;5;99~`, so the new agent could not be interrupted and the
  double Ctrl+C exit never fired. The session now disarms those modes, restores a
  cooked line discipline and drops the input queued while nothing read the pane,
  before and after every agent run. (`AI-CLI-9la0`)
- **A long-lived session stopped auto-restarting on its third agent exit ever.**
  The "3 consecutive agent exits" breaker never reset, so exits days apart added
  up. A run that lasted at least 60 seconds now starts a new count.
- **`ai cx` could only ever be session 1.** Three places matched the engine
  segment of a session name with `[cgp]`, which matches the `c` of `cx-…` and
  then fails on the `x`, so the prefix stopped parsing for every Codex launch:
  `find_next_index` returned 1 however many Codex sessions were live, its
  bare-mode fallback looked for worktrees under the wrong name, and a
  case-insensitive `ai cx <N>` resolve found nothing. One shared pattern now
  spells `cx` before the single-letter class.

- The `ruff-version-sync` pre-commit hook refused every commit in the repo: the
  renovate bump to ruff 0.16.10 moved `pyproject.toml`'s pins and left
  `.pre-commit-config.yaml` on v0.16.9. The gate's own tests only ever ran
  against synthetic trees, so the suite stayed green while the real pins
  disagreed; it now also asserts the invariant on this repository.

- `ai ls` no longer tries to install a system package. When `fzf` was missing it
  shelled out to `apt install -y fzf`, which needs root: on an ordinary account
  that printed a package-manager lock-file permission error and then fell through
  to the plain list anyway, and on a root account it would have installed software
  that nobody asked a list command to install. The picker is optional, so an absent
  `fzf` now goes straight to the plain list and prints one line on stderr naming
  `fzf` as the optional dependency. A test asserts no package manager is invoked.

- A session launch could die on a cosmetic iTerm2 tab rename. The rename runs an
  AppleScript that walks every window, tab and session comparing `tty`, under a
  five-second deadline; when iTerm2 was momentarily busy the Apple Event did not
  answer in time and `subprocess.TimeoutExpired` propagated out of the launcher.
  `check=False` already made a non-zero exit survivable, so a timeout — the one
  failure mode that is not an exit code — was the only way this cosmetic step
  could be fatal, and it was fatal *late*: the traceback arrived after the
  worktree had been created, synchronized and reported ready, discarding all of
  it from the user's point of view. The timeout (and an unrunnable `osascript`)
  is now caught where the non-zero exit already was, reported as
  `[launch] Warning: iTerm2 pane rename ...` naming the cause, and the launch
  continues to hand off — the same outcome a failed worktree sync already
  produces. The deadline was deliberately not raised: a longer one only narrows
  the window and is paid in full by every launch that does hang.
- A remote session could hang on a blank pane, ignoring repeated Ctrl+C, and
  print `ai-cli: could not promote child process group to terminal
  foreground` forever. The generated supervisor's child wrapper stops itself
  with `SIGSTOP` while it waits to be promoted to the terminal foreground
  group; when that promotion never succeeded, the cleanup path sent only
  `SIGTERM` to the stopped child. A stopped process only records `SIGTERM` as
  pending — it never acts on it until continued — so the supervisor's own
  wait blocked forever on a child that could never die. The cleanup path now
  also sends `SIGCONT` to the child's process group, so it wakes, processes
  the queued terminate signal, and the supervisor exits normally instead of
  hanging. (`AI-CLI-jpnd`)
- The fix above could itself be silently defeated. The supervisor promoted
  `$!` — the pid it backgrounded — as a process group id, but `$!` is the pid
  that calls `setpgrp()` only if every wrapper in between execs through, and
  where `python3` resolves through a `uv run` shim, `uv` spawns the
  interpreter as a child of its own and `setpgrp()` runs a level further down.
  The promoted group had no members, so the earlier fix's `SIGCONT` went to an
  empty group and the child never resumed. The child now reports its real
  pgid through the readiness file; the supervisor promotes the reported group
  instead of the pid it backgrounded. (`AI-CLI-dw1g`)
- A remote launch (`ai c`/`g`/`p`/`cx -R`) runs the generated supervisor
  entirely from the remote host's own installed `ai-cli-utils`, not the local
  one, and nothing kept that installation current — measured 40 and 34
  commits stale on the two configured remote machines, including missing
  both fixes above. Every remote launch now runs `ai update --quiet` on it
  first; a failed or timed-out update degrades to a warning rather than
  blocking the launch, and `--dry-run` skips it. (`AI-CLI-qmnd`)
- `ai c/g/p/cx <name> --remote --dry-run` performed a real launch: a real SSH
  shell probe, a real `ai update` on the remote host, real session allocation,
  and a real SSH/mosh handoff — confirmed live, where it created a real tmux
  session and used a real, pre-existing worktree on the target host. The
  documented dry-run contract ("nothing was created, started, or reaped") was
  implemented only for the local launch path; the `if remote:` branch exited
  via `sys.exit(0)` (mosh) or `os.execvp` (SSH) long before ever reaching that
  shared check. `--dry-run` now exits before any remote network call and
  prints the resolved plan (target host, transport, session identity, the
  remote command that would run) instead. (`AI-CLI-shpu`)

## [0.8.0] - 2026-09-22

### Added

- zsh is now provisioned as a native runtime dependency, alongside tmux and
  direnv. It is the interpreter a session prefers (`SESSION_SHELL_PREFERENCE` is
  `("zsh", "bash")`) and the macOS default, so on a host without it the shell that
  actually runs your sessions was also the one nothing verified -- 16 real-shell
  signal regressions skipped while bash, the fallback, stayed fully covered.
  Two rules shape it, both learned from the tmux loader bug:
  - **It installs where the host cannot take it away**: a prefix under your own
    data directory (`$XDG_DATA_HOME/ai-cli-utils/native`, `%LOCALAPPDATA%` on
    Windows), never the package manager's default. A conda base prefix can sit on
    a filesystem the platform rebuilds -- measured on a managed notebook host
    where `conda info` reports `/opt/conda` and `df` puts it on an ephemeral
    overlay while only `$HOME` persists -- so installing there yields a tool that
    silently vanishes on every restart.
  - **A launch adopts, it never installs.** Adoption is a directory stat and a
    `PATH` edit, so an already-provisioned zsh is picked up with no subprocess at
    all. Installation belongs to `ai setup`, `ai update` and `ai doctor`, because a
    package-manager solve is minutes and no session may wait on one.
  `ai doctor` reports zsh alongside bash and tmux, and `-n/--dry-run` reports
  without provisioning. (`AI-CLI-s2q2`)
- Explicit commands may now escalate to a system package manager with a password
  prompt; launches still never can. `attempt_installs` takes `allow_root`, which
  defaults to False and is opted into only by `ai setup` and `ai doctor` -- and even
  then only when `sudo` exists and **stdin is a terminal**, because a prompt nobody
  is watching does not fail, it blocks forever. The exact command is printed before
  the password is asked for, the password is collected by a separate visible
  `sudo -v` so output capture cannot swallow the prompt, and the installer then
  runs under `sudo -n` so it can never silently re-prompt. Verification is never
  disabled and there is no `--insecure` path. (`AI-CLI-s2q2`)

### Fixed

- On Linux, an `ai c` launch or relaunch no longer prints `[session] session script
  updated — reloading...` over and over without ever starting the agent. The session
  script read its own mtime with `stat -f "%m" FILE || stat -c "%Y" FILE`. On GNU
  coreutils the first form prints a filesystem report, free-block counters included,
  and exits 1, so the captured value changed between any two reads and every
  replacement child decided the script had been updated. The GNU form is now tried
  first, each answer must be an integer, and an unreadable mtime never counts as a
  change. A real-tmux test now runs the whole generated session under GNU `stat`
  semantics and checks that it reaches the agent, and that one genuine rewrite
  reloads exactly once.

- State, cache, config and data directories can no longer resolve relative to the
  current working directory, which is how a captured process listing came to be
  written into a repository checkout instead of your home directory. The cause is
  a distinction that only shows up for a variable that is *set but empty*:
  `os.environ.get("XDG_STATE_HOME", default)` returns `""` rather than the
  default, and `Path("") / "ai-cli-utils"` is the **relative** path
  `ai-cli-utils`, so everything built on it resolved against the cwd. Measured
  side by side with `XDG_STATE_HOME=""`, the old code produced
  `ai-cli-utils/remote-ps-cache.json` <!-- gitignored-path-ok: named as the WRONG location the bug wrote to, not cited as evidence; nothing is meant to resolve there --> and the new code produces an absolute path
  under `$HOME`. One site made this more than misplaced clutter: the CC
  update-staging reaper *deletes* under its resolved path, so a relative base
  aimed a delete at `<cwd>/claude/staging`. All seven base-directory reads now go
  through a single `resolve_base_dir()` that ignores a value which is empty or
  non-absolute (as the XDG Base Directory spec requires for relative values) and
  rejects a relative fallback outright. An AST sweep of the source tree fails the
  suite if any module reads one of those variables directly again, since the
  behavioural tests cannot see a call site that does not exist yet.
  (`AI-CLI-583m`)
- Test fixtures no longer leak resources when a host-capability probe decides to
  skip. `real_tmux_socket` created a temp directory and started a probe tmux
  server, then decided whether to skip -- with the `try`/`finally` that cleans
  both up beginning *below* the skip, so on the skip path neither was released.
  That path is by definition the one taken on hosts where tmux misbehaves. An AST
  sweep found four "acquire, then validate" sites; three are fixed by probing
  first (a `PATH` lookup or a filesystem capability check builds nothing) or by
  opening the `try` above the probe, and the fourth is legitimate -- its `write`
  is the probe's own input -- so it is allowlisted by name with the reason
  attached. A meta-guard in `tests/test_skip_hygiene.py` now enforces both correct
  shapes, with positive and negative controls plus a stale-allowlist check so an
  exemption cannot outlive the code it covers.
  (`AI-CLI-bug-tests-skip-capability-probe-bfqy`)

- A launch no longer dies at `tmux new-session` when tmux is on PATH but cannot
  load a shared library. `ai c` used to print `tmux version unavailable (binary
  does not run)` and then `launching inside tmux` in consecutive lines,
  synchronize a worktree, and only then fail with the dynamic loader's error --
  because the preflight asked whether tmux was on PATH while the report asked
  whether it ran, and nothing acted on the report. Now the launch **self-heals**:
  it reads the loader's own error, looks for the named library in directories
  around tmux's install prefix (including one nesting level down, where extracted
  self-contained bundles keep theirs), points the loader at what it finds, and
  re-runs tmux to prove it worked. Nothing is installed, downloaded, or written to
  the machine, and a repair that cannot be demonstrated is rolled back. Because
  it is re-derived on every invocation, a host whose system libraries are wiped by
  a restart or a rebuilt container fixes itself on the next launch. If no such
  library can be found, the launch degrades to bare mode -- naming the soname, the
  directories searched, and the `AI_CLI_LIBRARY_PATH` override -- *before* a
  worktree is created rather than after. (`AI-CLI-i2ih`, `AI-CLI-d89q`)
- `ai c --dry-run` (and `ai g`/`ai p`/`ai cx`) now genuinely does nothing. The
  flag was documented but never declared, and because the session commands set
  `ignore_unknown_options` so engine flags can be forwarded, it was silently
  passed to the engine and the launch ran for real -- creating a worktree, a
  branch, registering workspace trust, reinstalling the CLI, and starting a
  session. It now prints the resolved plan (engine, tmux-vs-bare after config and
  preflight, session name, worktree path, branch, sandbox) and exits above every
  write. (`AI-CLI-wepi`)

### Changed

- A missing tmux no longer blocks a launch. The launcher now attempts one
  unattended install (rootless managers first: micromamba/conda/brew, then the
  system ones when running as root) and falls back to bare mode with a notice
  naming what was lost. Previously every non-Windows host without tmux printed
  remediation and exited 1, making an enhancement a launch precondition.
  Resolution order is unchanged where it was already right: `[session] use_tmux`
  wins outright, and with no setting tmux is the default. (`AI-CLI-yrpa`)
- `ai doctor` reports tmux by running `tmux -V` rather than only finding it on
  PATH, and says so explicitly when tmux resolves but cannot execute. A tmux that
  died on a missing shared library previously read as `OK tmux` while no session
  could start. It now also performs the loader repair and, when that succeeds,
  prints the exact `export` line that makes it permanent for shells outside `ai`.
- Installing or reinstalling verifies tmux by **executing** it, alongside the
  existing direnv bootstrap, and never fails the install over it -- there is no
  native tmux on Windows to provision, and a missing enhancement must not make the
  package uninstallable. (`AI-CLI-i2ih`)
- Every tmux decision now hangs on execution rather than resolution:
  `install_tmux` verifies with `tmux_runs` instead of `tmux_present`, so a package
  manager that lands a tmux which cannot load its libraries is reported as the
  failure it is. A tmux that is present but broken is deliberately *not* sent to a
  package manager: the broken binary keeps its place on PATH, so the install could
  not be verified to have fixed the tmux that will actually run, and the launch
  would pay the manager's timeout on every attempt to change nothing.
  (`AI-CLI-d89q`)

- Corrected stale-session documentation: launch-time housekeeping does not
  terminate tmux sessions. Start the independent reaper with
  `ai session-reaper start`; it observes by default and requires explicit
  `mode = "reap"` opt-in before it can reclaim an eligible session.

### Added

- The stale-session reaper now records generation-fenced heartbeats and uses
  independently verified liveness and staleness evidence before it can reclaim
  a session. Its default mode is observation; missing or invalid evidence
  preserves the session. (`AI-CLI-tdm6`; 8a25e04, 268970b)
- `ai p` and `ai cx` launch pi and Codex CLI sessions with the same worktree
  isolation, resume behavior, and terminal integration as existing session
  engines. (`AI-CLI-o3gi`, `AI-CLI-z7ms`; 35c5eef, a77c453)
- Remote sessions can select a named machine with
  `ai c -R -m/--remote-machine <alias>`; legacy single-remote configuration
  remains supported. `ai ssh [ALIAS]` opens an interactive shell using the same
  remote-machine configuration. (`AI-CLI-d2cu`, `AI-CLI-fzm0`,
  `AI-CLI-o7xq`; bb0400c, 4f133da)
- `ai session-audit` surveys titled Claude Code sessions and can safely drive
  adoption for sessions that `ai c` cannot resume. (`AI-CLI-168`; 0576159)
- `ai sync` now includes Claude Code task records and rewrites transcript paths
  for the receiving machine, so synced sessions can be resumed there.
  (`AI-CLI-n7h7`; b1ecdd0)
- Session recovery uses a portable process probe on macOS and Windows as well
  as Linux. (`AI-CLI-session-reclamation-linux-only-zusn`; 46dda09)
- Session launch can bootstrap `direnv` portably, with a clear bypass when it
  is intentionally unavailable. (`AI-CLI-ai-cli-utils-abvz`; b3515c5)

- `ai session-adopt` adopts a Claude Code session that was started **without**
  `ai c` — a plain `claude` in a repo root — so `ai c <n>` resumes it from the
  right worktree, losing nothing. `ai cc-migrate` moves the transcript and
  nothing else, which is not enough: some session state is keyed by the project
  slug (the cwd, slugified) or by a task namespace derived from the session UUID,
  and only those break. The new command ensures the worktree exists (reusing an
  existing one as-is, never clobbering it), moves the transcript via the existing
  `cc-migrate` machinery, merges the CC task namespace into the pinned one,
  copies auto-memory, and then verifies with the launcher's own lookup that
  `ai c` genuinely resolves the result — exiting non-zero if it does not. Task
  ids are namespace-scoped, so the same `1.json` exists in both namespaces
  holding unrelated work; colliding files are renumbered rather than overwritten,
  with each file's `id` and inter-task references remapped to match. Auto-memory
  is copied and never moved (the memory directory is shared by every session that
  ran from that root, so moving it would strand them) and never overwritten (a
  worktree's own memory is about the work that happened there). Three refusals
  precede any write: a live session (checked by reading `/proc/<pid>`, since a
  session record outlives the process that wrote it — matched by name or
  transcript UUID, or by a session holding the destination worktree, but not
  every session merely sharing the source root), a **duplicate title**, and
  insufficient free space (a copy-then-verify adoption transiently doubles a
  transcript, and an ENOSPC mid-write truncates it). Two transcripts claiming one
  title make resume nondeterministic, so that case is an unconditional human
  gate: the command stops, prints both candidates with the detail needed to tell
  them apart, and proposes the lowest index claimed by neither a worktree nor a
  title — it never picks by size or mtime, `-y/--yes` does not cover it, and
  applying the remedy requires the human's title or index on a second
  invocation. Bulk mode (`-a/--all`) pauses on a collision and continues with the
  remaining sessions. Procedure, including the by-hand equivalent of every step:
  `docs/tools/cc-session-adoption.md`. (`AI-CLI-168`)

### Changed

- `ai handoff` and its cross-machine queue, NATS ingress, and automatic launch
  hooks are removed entirely; the command no longer exists. NATS continues to
  support its remaining messaging features. This intentionally removes the
  queue's offline, cross-machine, and durable lifecycle semantics.
  (`AI-CLI-archive-retire-exjn`; 86c41d0)
- **BREAKING (config):** the `cc-usage push` legacy usage configuration section
  is renamed to `[usage_api]`. The old name was a private platform name that did
  not belong in a public package, and it did not describe what the section
  configures: a REST endpoint that CC token-usage events are POSTed to. The keys
  inside it are unchanged (`api_url`, `api_key`), and there is no fallback to the
  old name — a populated legacy section is now simply ignored. **If your own
  `config.toml` has that legacy section filled in, rename its header to
  `[usage_api]`**; nothing else in your config needs to change. If you miss it,
  `ai cc-usage push` fails with `usage_api.api_url and usage_api.api_key must be
  set in config.toml under [usage_api]` rather than pushing. A commented-out
  legacy block (the untouched template default) needs no action; the new
  template ships `[usage_api]`. (`AI-CLI-189`)

### Fixed

- The stale-session supervisor no longer kills a predecessor `mosh-server`
  process without proving it belongs to the current session. (`AI-CLI-sdgi`;
  4a534ff)
- `ai cc-usage push`, quota operations, and copier updates now return distinct
  exit codes for configuration errors (78), transient failures (75), and,
  for copier updates, a partially mutated state requiring human resolution (3).
  (`AI-CLI-15vn`; fe9da7b)
- Remote named sessions use the identity allocated on the remote machine;
  unnamed remote sessions no longer collide. Remote launch probes its available
  shell, limits failed mosh retries before falling back to SSH, and reports a
  suspicious fast mosh exit. (`AI-CLI-209`, `AI-CLI-rjxm`, `AI-CLI-gg9s`,
  `AI-CLI-jbyo`; 999a0dd, 582017d, 8fe0796, 893fd10, ecb3dfd)
- `ai sync` serializes staging-repository mutations, rejects real conflict
  markers before committing, preserves marker examples in ordinary files, and
  can use Codex before its existing fallback for memory-conflict merges.
  (`AI-CLI-5o8j`, `AI-CLI-0wmi`, `AI-CLI-ezcr`; 5859304, ab3f264, f69f9da)
- `ai c` resumes the exact matching transcript UUID, and session cleanup no
  longer kills unrelated live sessions while still reaping verified dead ones,
  including remote sessions. (`AI-CLI-7d3r`, `AI-CLI-p2qj`, `AI-CLI-7cyn`,
  `AI-CLI-pzjy`; 061e720, a223fe5, 549abe9, 1532f49)
- `ai ws pull` aborts and reports a failed worktree rebase instead of leaving
  the worktree mid-rebase and reporting success. (`AI-CLI-fbz5`; 7563f6c)
- Windows session launch and the `direnv` trust probe work without Unix-only
  executables or administrator privileges. (`AI-CLI-worktree-envrc-auto-approval-not-vtxx`,
  `AI-CLI-windows-test-suite-failures-nvqt`; 89e471d, 4b0f076)
- `ai c` reuses an empty session-worktree slot and creates it at the repository
  root rather than the caller's current directory. (`AI-CLI-ai-c-1-msxj`;
  0165e25, c1e0c8b)

- **`ai` could vanish from your shell entirely, reporting `bash: ai: command not
  found`,** for as long as an installer run took to finish. Self-update installed
  the package *plainly* into its own environment, which silently converts an
  editable install into a copied snapshot. The fleet installer treats a missing
  editable marker as damage and repairs it by reinstalling the tool from scratch —
  and that repair deletes and rebuilds the whole environment, taking the `ai`
  entry point with it. So the two owners took turns undoing each other forever,
  and every installer-side repair was an outage window. The failure looked random
  because it depended on which of the two had run more recently, and it could not
  be defended against from inside the program: the command is gone before a single
  line of it runs. Self-update now preserves editability, so the marker survives,
  the installer sees nothing to repair, and the destructive path is never taken.
  The same change fixes a quieter symptom of the same cause — between a
  self-update and the next installer run, `ai` was a stale copy that no longer
  tracked its source, so edits appeared to have no effect.

- **`ai c` could crash with `ModuleNotFoundError: No module named
  'ai_cli.session_adopt'`** when two launches ran at once. It was never flaky: two
  conditions had to coincide. `ai c` auto-updates by running `ai update --force`,
  and `uv tool install` rewrites the very installation the running process imports
  from. Only one launch wins that lock; the loser used to treat "someone else is
  updating" as "nothing to do", so it kept running while its own module files were
  replaced underneath it. It then crashed only if it *also* had a prior transcript
  to resume, because the import that failed sits in the resume path. So sibling
  launches in the same burst survived untouched, which is what made it look random.
  The loser now waits for the other update to finish and restarts itself, and the
  liveness check degrades instead of raising if a module goes missing mid-run.
- `ai c <n>` no longer reinstalls the package, or prints the installer
  transcript, on nearly every launch (`AI-CLI-ww8o`). The launch-time staleness
  check compared the repository's `HEAD` against a stamp, so any commit — a
  docs edit, a task-tracker sync — armed the next launch on every machine for a
  full `git pull` plus `uv tool install`, whose whole `Resolved / Prepared /
  Installed` block streamed into the terminal immediately before the session
  painted. `HEAD` is the wrong signal in both directions: it also fails to move
  for an uncommitted edit under `src/`, which is the most common way the
  installed build goes stale while a change is being tested. The trigger is now
  a content fingerprint of the files that actually ship (`pyproject.toml` and
  `src/`), recorded at install time, so an unrelated commit costs nothing and an
  uncommitted source edit is now picked up where it previously was not. The
  reinstall itself keeps the unique `.post<timestamp>` version that stops uv
  serving a cached build — the guarantee the unconditional reinstall existed for
  — and reports one line naming the version and why it rebuilt, with `git`'s and
  `uv`'s output captured. Failures are never quiet: the captured transcript is
  printed in full on stderr and the stamp is cleared so the next launch retries.
  `AI_CLI_UPDATE_VERBOSE=1` restores the full transcript at launch, `ai update`
  gains `-q/--quiet` and `-v/--verbose`, and `ai update --force` still forces a
  cache-bypassing reinstall on demand regardless of the fingerprint.
- `ai update` no longer rewrites `pyproject.toml`'s line endings, and
  `.gitattributes` now pins every text file to LF in the index and in every
  checkout (`AI-CLI-mbci`). The version bump round-tripped the file through
  `Path.read_text()`/`Path.write_text()`, and `read_text()` applies
  universal-newline translation: a CRLF file came back LF, so the restore
  reverted the version but silently converted the whole file. `pyproject.toml`
  had itself been committed as CRLF from a Windows host — the only such file in
  the repository — so every checkout showed a permanent 172-line phantom diff
  that re-appeared after every update run, and only the `git checkout --
  pyproject.toml` opening the next update kept it from accumulating. The bump now
  reads and writes bytes, so the restore is byte-identical even when the install
  between the two writes raises, and the committed file is normalized to LF.
  Details: `docs/bugs/pyproject-crlf-round-trip.md`.
- Exiting a session could leave its process **stopped** rather than gone, and the
  launcher then refused to resume the name (`AI-CLI-2139`). A process in state `T`
  is fully present in `/proc`, holds its pid and its open files, and never resumes
  on its own, so the registry check's two questions — does the pid exist, does its
  start time match the record — were both answered "yes" for a session nobody was
  using. `ai c <n>` printed "still running", dropped `--continue`, and started a
  new differently-named session; nothing ever reaped the stopped process, so the
  name stayed reserved. The terminate that looked successful was the same trap: a
  stopped process does not run, so it queues the `SIGTERM` and `kill` returns 0
  without anything happening. The liveness check now reads the `/proc` state field
  and classifies `T`/`t`/`Z`/`X`/`x` as *abandoned* rather than live, and the
  launcher ends an abandoned process it can positively identify — `SIGTERM`,
  `SIGCONT`, bounded wait, `SIGKILL`, aimed at the process **group** so a
  wrapper's children are not orphaned — confirms the outcome from the absence of
  `/proc/<pid>` rather than from a signal's return code, prunes the stale record,
  and says what it found and what it did before resuming. Reclamation is scoped to
  the session being resumed, so a launch never becomes a fleet-wide reaper; a
  genuinely running session still blocks its name and is never signalled; and a
  record that cannot prove which process its pid is (no matching start time) is
  reported and left alone rather than killed. Procedure, including the
  verification step to run after any exit:
  `docs/procedures/exiting-a-cc-session.md`.
- **Data loss:** creating a session worktree could delete nested git worktrees,
  including commits that existed nowhere else (`AI-CLI-200`). `.worktrees/<name>`
  carries two incompatible meanings: `ai c <name>` wants that path to *be* the
  session's own checkout, while per-task agent worktrees are nested *inside* it as
  `<name>/<task>/<leaf>`. A session launched from a repository root therefore finds
  that container sitting exactly where its own checkout must go. Two defects met
  there and masked each other. First, `create_worktree` decided whether the
  directory was a registered worktree with a **substring** test against
  `git worktree list --porcelain`, and `…/.worktrees/name` is a substring of the
  line `worktree …/.worktrees/name/leaf` — so a container with no checkout of its
  own reported as registered. Second, the branch taken when that test fails is an
  unconditional `shutil.rmtree`, which reached with a container in place deletes
  every nested worktree and any unpushed commit in it. The safe outcome was luck,
  not correctness: the substring happened to match, so the deletion was never
  reached, and fixing only the substring test would have armed it. Registration is
  now matched line-exactly, and the deletion refuses to run on any directory
  holding a git checkout — registered or not, since an unregistered clone is
  invisible to `git worktree list` yet still holds commits. The refusal names the
  colliding paths and the `git worktree move` that relocates them; nothing is moved
  automatically, because relocating a human's unpushed work is a human decision. A
  stale directory git knows nothing about is still replaced, so a leftover from an
  interrupted run cannot block a session forever.

- `ai session-adopt` reported success into a destination that held none of the
  repository's content (`AI-CLI-200`). `_ensure_worktree` accepted any existing
  directory (`is_dir()`), which cannot distinguish a git checkout from an unrelated
  directory of the same name — so the collision above also made adoption rewrite
  every recorded working directory in a transcript to a path with no repository in
  it. Adoption now verifies that its destination is a worktree *of that
  repository* and otherwise refuses with the collision and the remedy named. The
  refusal fires in `-n/--dry-run` too: previously the only signal in a preview was
  the *absence* of a "worktree to create" line, which is how this stayed invisible.

- Four real-tmux launch integration tests failed on any machine with a `tmux` that
  is on `PATH` but cannot execute — an extracted bundle whose shared libraries are
  not on the loader path, for instance (`AI-CLI-201`). The guard asked
  `shutil.which("tmux") is None`, which only stats the file and checks the
  executable bit; the failure happens later, in the dynamic loader, so every test
  ran and failed on an empty session list, reading as a defect in the launch path
  rather than as a broken tool. The guard now runs `tmux -V` and skips with the
  loader's own message when it does not exit 0. No assertion was weakened: with a
  working `tmux` all five tests run and pass exactly as before, and the skip is
  scoped to the fixture that needs a live server rather than to the module, so the
  fully-mocked test in the same file keeps running either way.

- Every `ai update` and the auto-update at session launch printed a hardlink-fallback
  warning to stderr when uv's cache and tool-install directories resided on different
  filesystems (e.g., cache on EFS, tools on local NVMe). The install always succeeded,
  but the warning fired on every update, training users to ignore update output. The
  code now detects the `st_dev` mismatch via `os.stat()` and passes `--link-mode=copy`
  only when cache and target are on different filesystems, suppressing the warning
  without degrading the hardlink fast path on single-filesystem machines.
  (`AI-CLI-148`, BUG-008 in `docs/bugs/uv-hardlink-fallback-warning.md`)

- Seven tests covering the LLM memory-merge path (`_llm_merge_memory_conflict` and
  the `apply_pull_files` auto-merge branch) had never executed anywhere: they were
  guarded by `pytest.importorskip("google.genai")`, but `google-genai` was not a
  declared dependency in any group, so CI and every dev machine skipped them
  unconditionally. `google-genai` is now a dev-group dependency (test-only — the
  runtime import stays lazy and optional) and the skip guards are removed, so a
  missing module fails the suite instead of silently skipping coverage.
  (`AI-CLI-146`, BUG-007 in `docs/bugs/genai-tests-skipped-everywhere.md`)

- `ai quota scrape` leaked Claude Code's update downloads to disk at roughly 8 GB/day
  (`AI-CLI-131`). The scrape launches `claude` in a throwaway tmux session and kills
  that session unconditionally about 15 seconds later; Claude Code had by then begun
  downloading any newer version into `~/.cache/claude/staging/`, so the kill truncated
  the download mid-write and orphaned the partial binary. Nothing reaped it — Claude
  Code only removes a staging entry it successfully installs. Under the `*/10` cron
  that is ~144 orphans a day: the Hetzner box held 402 entries totalling 23 GB, 400 of
  them created exactly on cron minutes, and reached 0 bytes free. The scrape now runs
  Claude Code with `DISABLE_AUTOUPDATER=1`, so an ephemeral read-only scrape never
  starts a download it cannot finish, and sweeps staging entries left idle for over an
  hour after each run. The sweep only removes well-formed entry directories past that
  age, so a download in flight for a real session is never touched.

- Session watcher busy-spun instead of polling once per second (`AI-CLI-129`). The
  loop paced itself with `read -t 1 -r < /dev/null`, which never blocks: `/dev/null`
  returns EOF immediately, so `read` returns before its timeout applies. Measured at
  ~140 ticks/second against a specified 1, for the lifetime of every session — each
  tick able to fire `tmux capture-pane`, `ai internal publish-heartbeat` and a
  `sha256sum` pipeline. Every duration the loop derives from its tick counter
  (`counter >= 10` as a 10-second startup grace period, `counter % 30` as a 30-second
  heartbeat, `counter % 10` as a 10-second config-hash check) was wrong by the same
  factor. Replaced with `sleep 1`.

- `ai update`'s live-session template refresh rewrote every session's stable launch
  script unconditionally, and had no bound on how often it could run (`AI-CLI-129`).
  An unchanged script is now left alone, so its mtime — the hot-reload signal every
  running wrapper polls — is no longer bumped for nothing, and a plain `ai c`
  re-attach no longer makes the running wrapper `exec` a pointless reload. The
  refresh now also refuses to run past 20 attempts in 60 seconds (tracked in-process
  and in the state dir) and reports the runaway caller once per window on stderr,
  rather than spawning a `tmux list-sessions` subprocess plus a write and a chmod per
  live session for as long as the caller keeps asking.

## [0.7.0] - 2026-07-13

### Added

- Windows compatibility — cross-platform fixes so the package installs and runs on Windows:
  - Machine profile auto-detection: `detect_machine_profile()` reads the `AI_HOST` env var
    (falls back to `socket.gethostname()`) and maps `sys.platform` to a human-readable
    `os_type` (`"windows"`, `"macos"`, `"linux"`). `ensure_machine_profile_registered()`
    writes `host_id` and `os_type` to `config.toml` on first `load_config()` call — no
    manual config edit needed on first run.
  - Cross-platform root check: replaced `os.getuid() == 0` with `_is_root()` helper
    (`ctypes.windll.shell32.IsUserAnAdmin()` on Windows, `os.getuid()` on POSIX).
  - Replaced POSIX `find`/`rm` pycache cleanup with `Path.rglob("__pycache__")` +
    `shutil.rmtree`.
  - `is_cc_active_locally()` uses `psutil.process_iter()` on Windows instead of `pgrep`.
  - Remote SSH transport (`os.execvp("zsh", ...)`) exits with a clear error message on
    Windows rather than crashing.
  - `DEFAULT_CONFIG` written with `encoding="utf-8"` (fixes `UnicodeEncodeError` on
    Windows CP1252 locales when the config contains non-ASCII characters).

### Fixed

- iTerm2 pane-header drift on session re-attach (`AI-CLI-59`, final). The pane is
  now renamed by resolving the tmux session's live **client tty** at set-name time
  (`tmux list-clients` → tty → iTerm2 session with that `tty`), instead of a stored
  `ITERM_SESSION_ID` GUID. tty is 1:1 with a physical pane, so cross-session
  clobbering (two tmux sessions sharing one pane GUID) is now impossible. Removed
  the racy dual-GUID tracking: deleted `_evict_iterm2_guid` and
  `_get_current_iterm_session_id`, and the ITERM_SESSION_ID env-propagation from
  the launch/re-attach path. Panes display the engine-prefixed session id
  (`c-sw-3` for Claude, `g-…` for Gemini/agy) so sessions stay visually
  distinguishable by engine.

### Removed

- `ai gemini` command and the underlying `gemini.py` / `research.py` modules deleted.
  Migration: use a Gemini CLI tool directly for research queries.
- Contract tests for `GeminiResult`, `AttemptLog`, and `run_gemini()` in `test_public_api.py`
  removed (symbols no longer exist).

### Changed

- `ai spend gemini` help text updated to clarify it reads historical local JSONL logs only.

## [0.6.1] - 2026-04-25

### Added

- `ai sync pull` now automatically pulls affected project repos and their worktrees after a successful sync (`AI-CLI-63`). Clean trees are pulled with `git pull --rebase`; dirty main trees use stash+pull+pop with a warning; dirty worktrees are skipped with a log message that includes CC session state (idle vs actively executing). Not triggered in `--memories-only` or `--dry-run` mode. 13 new tests.

### Deprecated

- `ai gemini` (quick depth) is now a thin alias to `companion research -d quick`. Invocations
  print a `DeprecationWarning` to stderr and delegate to `companion`. The `--depth standard`
  path is unaffected. `ai gemini` will be removed in a future release (`COMP-47`).

## [0.6.0] - 2026-04-25

### Added

- Windows out-of-box support (`AI-CLI-29`):
  - Platform-aware config/state/cache paths: `%APPDATA%\ai-cli-utils` (config), `%LOCALAPPDATA%\ai-cli-utils` (state/cache) on Windows; XDG paths unchanged on Linux/macOS.
  - Replaced `fcntl.flock` with `portalocker` (cross-platform file locking); `portalocker>=2.0` is now a hard dependency.
  - Added `psutil>=5.9` as a hard dependency; extracted `_pid_alive()` helper in `config.py` using `psutil.pid_exists()`. All `os.kill(pid, 0)` calls eliminated.
  - `SIGTERM` calls replaced with `psutil.Process(pid).terminate()` (cross-platform).
  - Lock file paths use `tempfile.gettempdir()` instead of `/tmp` literals.
  - Null device references use `os.devnull` instead of `"/dev/null"`.
  - UTF-8 stdout guard in `quota.py` prevents `UnicodeEncodeError` for emoji output on Windows (cp1252 default encoding).
  - `ai c`/`ai g` on Windows prints a clear error and MSYS2 install instructions if `tmux` is not found.
  - Optional `[notify-win]` extra (`pip install "ai-cli-utils[notify-win]"`) enables Windows toast notifications via `plyer`. Silently degrades if not installed.
  - CI matrix expanded to include `windows-latest` for Python 3.11, 3.12, 3.13.

## [0.5.5] - 2026-04-25

### Fixed

- `ai quota statusline-part` no longer produces empty output on installations with a legacy quota DB
  that pre-dates AI-CLI-55 (`weekly_sonnet_pct` column was not migrated via `ALTER TABLE`, causing a
  silent `OperationalError` inside the `try`/`except` block) (`AI-CLI-56`).
- `_init_db()` now calls `_migrate_snapshot_columns()` to add `weekly_sonnet_pct` and `extra_pct`
  to existing `quota_snapshots` tables that lack them (`AI-CLI-56`).
- `quota_statusline_part()` now calls `_init_db(conn)` on its direct SQLite connection so schema
  migrations always run before any query (`AI-CLI-56`).
- Statusline bash script quota cache now uses a `_quota_cache_valid` flag instead of checking
  `[[ -z "$quota_part" ]]`, preventing legitimate empty results from bypassing the 30-second cache
  and triggering repeated blocking calls that produced duplicate prompt boxes in scrollback (`AI-CLI-56`).

## [0.5.3] - 2026-04-25

### Added

- `ai quota statusline-part` now shows Sonnet weekly quota alongside all-models quota (`AI-CLI-55`). Format: `📊 42% W ✅ →8% | 87% S`. Labels: `W` = weekly all-models, `S` = weekly Sonnet. Sonnet % color-coded with same thresholds as all-models % (<50% green, <75% yellow, ≥75% red). When Sonnet data is absent, shows dimmed `-% S` and fires an immediate background scrape to populate it.

## [0.5.2] - 2026-04-21

### Fixed

- `__init__.__version__` was `0.3.0` while `pyproject.toml` was at `0.5.1` — now synced (`AI-CLI-46`).

### Added

- Programmatic API contract tests (`tests/test_public_api.py` expanded from 6 to 12 tests): version sync guard, `run_gemini()` parameter signature enforcement, and field-completeness checks for `GeminiResult`, `AttemptLog`, and `QuotaSnapshot`. These catch breaking API changes before downstream callers (e.g. companion) break (`AI-CLI-46`).

## [0.5.1] - 2026-04-21

### Fixed

- Discord webhook delivery failing with 400 "empty message" for webhooks hosted on `discordapp.com` (legacy Discord CDN domain). The `is_discord` check only matched `discord.com`, causing the payload to be sent in Slack format (`text` field instead of `content`), which Discord rejects. Both domains now detected correctly.

## [0.5.0] - 2026-04-20

### Added

- Unified notification system (`AI-CLI-25`): `Notifier` class with parallel multi-channel delivery (Discord webhook, ntfy push, OS native). All configured channels fire concurrently; OS notification fires as fallback when all primary channels fail.
- `NotificationResult` dataclass with per-channel success/failure tracking (channel, status_code, error).
- `notification_log` SQLite table in `quota.db` — persistent delivery history with per-row JSON lists of attempted/succeeded/failed channels.
- `ai notifications log` command — query notification history with `--last N`, `--since DATETIME`, `--from DATE`, `--to DATE`, `--source TEXT`, `--failed` filters. Relative time expressions supported (`2h`, `30m`, `1d`, `yesterday`).
- `ai notifications list` command — audit configured channels showing name, enabled status, and whether credentials are present (no values exposed).
- `ai quota watch` is now a command group with `start`, `stop`, `status`, and `run` subcommands. `run` is the raw Circus entry point; `start`/`stop`/`status` are the user-facing dispatchers.
- Quota-watch auto-start at session launch (idempotent `ai quota watch start` in session bash template).
- `_notify_threshold()` helper routes quota threshold alerts through `Notifier` with priority and tag mapping (`urgent`/`rotating_light` at ≥90%, `high`/`warning` at ≥75%).

### Changed

- `quota_watch()` daemon no longer has a PID file guard — Circus owns restart/lifecycle. NATS connection failure is non-fatal; quota watch runs without NATS if unavailable.
- Notification send functions (`_send_notification`, `_send_webhook_notification`, `_send_ntfy_notification`) removed from `quota.py` and replaced by channel driver functions (`_send_discord`, `_send_ntfy`, `_send_os_notification`) in `notifications.py`.

## [0.4.5] - 2026-04-20

### Added

- ntfy notification channel for quota threshold alerts. Both Discord webhook and ntfy fire independently on every threshold crossing. Reads `NTFY_BASE_URL`, `NTFY_TOPIC`, and `NTFY_TOKEN` env vars (or `[quota]` config keys). Proper `Title`, `Priority`, and `Tags` headers ensure full message visibility in macOS/iOS notification banners.
- macOS Notification Center fallback via `osascript` when no push channels are configured (replaces `notify-send` on macOS).

## [0.4.4] - 2026-04-20

### Fixed

- `ai c` / `ai c -R`: iTerm2 session name now correctly uses the project prefix (e.g. `c-r-ai-cli-1`) when `ai c -R` is run from a pane whose cwd has drifted away from the project root. `get_project_prefix()` now falls back to parsing `AI_TMUX_SESSION` (set by the session script) before resorting to the `[:3]` truncation. Also fixed a trailing-hyphen bug in the `[:3]` fallback that produced double-dash names like `c-r-ai--1` for project names whose 3rd character is a hyphen (e.g. `ai-cli-utils`).

## [0.4.3] - 2026-04-20

### Fixed

- `ai sync push` no longer creates new git blobs for unchanged files — SHA-256 check before `shutil.copy2` skips files with identical content, eliminating the primary driver of staging repo pack bloat.
- `git gc --auto` runs after each sync commit to repack loose objects when needed.
- Memory directory non-markdown files (e.g. `.consolidate-lock`) are now excluded from sync via `should_sync_file`, preventing spurious conflict detections and `.conflict.conflict` cascades.

## [0.4.2] - 2026-04-20

### Fixed

- iTerm2 Session Name not updating for local CC sessions (`ai c`). Added `_set_iterm2_name_applescript` which targets the iTerm2 session directly by GUID via the AppleScript API, bypassing DCS passthrough failures caused by nested tmux, process-tracking title overrides, and double-wrap requirements.

## [0.4.1] - 2026-04-19

### Added

- Session auto-refresh on update: when the `ai c` session loop detects a version change after `ai update`, the bash template is reloaded via `ai internal refresh-template` and exec-replaced in the same shell. No user action required; mosh-safe. (AI-CLI-39)
- `ai sync` subcommands: `push`, `pull`, `conflicts`, `watch` — each exposed as a Click subcommand with `--help` at the subcommand level. (AI-CLI-47)

### Fixed

- `_auto_update_if_stale` now uses an `O_CREAT|O_EXCL` lockfile instead of a stamp-file read/write race. Prevents double-update on concurrent session launches.
- `transport.py` deferred import `from .main import _ensure_circusd` was left over after the module split; updated to `from .process_manager import _ensure_circusd`. Caused a test regression where the function was imported before the patch was applied.
- Pyright type error: `entry.value` (`bytes | None`) in `quota.py` now guarded with `if entry.value is not None` before `json.loads`.
- Architecture doc updated: monolithic `main.py` dispatch → Click command group dispatch; `ai tunnel open|close` → `start|stop`; handoff publisher attribution corrected to `handoff.py`.
- Integration tests for `_do_session_launch` now use libtmux directly for `has-session` / `new-session` instead of subprocess rerouting, fixing failures in non-interactive CI environments.

### Refactored

- `main.py` split into 8 modules (`config`, `handoff`, `iterm2`, `layout`, `process_manager`, `session`, `session_script`, `transport`); `main.py` reduced from ~4 000 to ~1 900 lines. All patch targets in tests updated to owning modules. (AI-CLI-39, AI-CLI-47)
- NATS callback closures extracted to module-level functions (`_on_handoff_signal_watch`, `_write_pending_if_claimed_drain`, `_on_quota_snapshot_handler`) so they can be unit-tested without a live NATS server. (AI-CLI-17)

## [0.4.0] - 2026-04-17

### Added

- `ai cc-usage push [-d/--dry-run]`: scan `~/.claude/projects/` JSONL files and push per-call token usage events (input, cache-creation, cache-read, output tokens) to a configured REST API backend. Cursor-tracked — only events since the last successful push are sent, in batches of 500. Config: `[core-cli] api_url` and `api_key` in `config.toml`. (AI-CLI-23)
- `ai cc-usage status`: print the number of CC sessions tracked by the push cursor and the timestamp of the last successful push.

### Fixed

- `ai --version` / `ai -V`: no longer raises `PackageNotFoundError` when run in an environment where the package is not installed (e.g. editable dev installs). Falls back to printing `unknown`.
- Session resume (bash template): bare `except: pass` in the Python one-liner that finds the most recent CC session file was catching `SystemExit`, causing `sys.exit(0)` to be swallowed after the first match. All subsequent files sharing the same `customTitle` (e.g. sync-generated `conflict-*.jsonl` copies) also had their paths written to stdout, producing a concatenated multi-path blob in `$matched_file`. `touch` then failed with "File name too long" before CC could launch. Fixed by using `except Exception: pass` so `SystemExit` propagates correctly.
- Session resume (bash template): `ls "$cc_project_dir"/*.jsonl &>/dev/null` used a shell glob that zsh NOMATCH fires on before the command runs, bypassing `&>/dev/null` and printing an error to the terminal. Replaced with a `find`-based check that avoids shell glob expansion entirely.
- `ai statusline` quota indicator — disappeared on remote sessions when the weekly quota reset occurred and the anchor file was not yet refreshed by a scrape. The inline week-start computation now advances forward from a stale anchor (matching `quota_db._get_current_week_start()`) instead of always subtracting one week, which queried the wrong week and returned no rows. (bug)

## [0.3.0] - 2026-04-11

### Added

- `ai spend gemini`: print Gemini usage summary combining local JSONL logs (OAuth/free-tier run counts, per-model stats) with GCP BigQuery billing export (actual billed amounts for paid runs). Graceful degradation when BigQuery is not configured or `google-cloud-bigquery` not installed. Config under `[gemini_billing]` in `config.toml`. (AI-CLI-41 T-03)
- `ai gemini -P`/`--confirm-paid`: explicit per-run confirmation flag for Deep Research paid API usage. Required when `paid_fallback_enabled = true` in config; exits with actionable error if absent. (AI-CLI-41 T-02)
- `ai gemini` — `paid_fallback_enabled` config key under `[gemini]` in `config.toml`: when `false` (default), the `ai_studio_paid` tier is excluded from all fallback chains, preventing accidental paid API spend. Set `true` only after confirming billing credit status. (AI-CLI-41 T-02)
- `ai gemini` — Deep Research daily run counter persisted to `~/.local/state/ai-cli/dr-daily.json`. Paid run count printed to stderr after each successful run; warning printed when approaching soft daily limit (`DEEP_RESEARCH_DAILY_WARNING = 18` out of `DEEP_RESEARCH_DAILY_LIMIT = 20`). (AI-CLI-41 T-02)

### Changed

- `ai gemini` — auth tier names are now Google-aligned: `oauth` (was `gemini-cli (OAuth)`), `ai_studio_free` (was `API free-tier`), `ai_studio_paid` (was `API paid tier-1`). Used in JSONL logs and fallback-chain messages. (AI-CLI-41 T-01)
- `ai gemini` — token counts (`input_tokens`, `output_tokens`, `total_tokens`) in JSONL logs are now `null` (not `0`) when usage metadata is absent from the API response. Null is distinguishable from a model that genuinely returned zero tokens. (AI-CLI-41 T-01)
- `ai gemini` — `GeminiResult` gains `is_deep_research: bool` field (logged to JSONL); token fields changed from `int = 0` to `int | None = None`. (AI-CLI-41 T-01)

### Fixed

- `ai sync`: removed handoff queue sync and config file sync from push/pull pipeline. Scope is now explicitly CC session data only: `~/.claude/projects/` JSONL and memory files, `~/.claude/history.jsonl`. Git-tracked files (config, hooks, statusline script) are no longer touched by `ai sync`.
- `ai update`: now deploys `src/ai_cli/data/statusline-command.sh` to `~/.claude/statusline-command.sh` as a plain file, replacing any existing symlink. The statusline script is now owned by this package, not by any other project repo.
- `ai signal-watch start`: removed invalid `autostart` key from Circus `add` options — was silently failing watcher registration so signal-watch was never actually started via Circus when `ai c` launched. `start=True` top-level parameter already handles immediate start. (AI-CLI-16)
- `ai sync pull`: removed `replicate_history_to_worktrees` from pull pipeline — it was injecting phantom `history.jsonl` entries (worktree-path copies of main-project entries) on every pull. These phantoms caused the conversation picker to show the wrong project's sessions inside worktree sessions, and made `--continue` fail to find the correct conversation. Added `purge_phantom_history_entries()` which runs on each pull to clean up any previously created phantoms. Existing phantom entries are purged on the next `ai sync pull`.
- `ai ps cron`: VPN detection in `cmd_ps` — switches to `vpn_host` when Mullvad is active, preventing a 30s hang at session start when the Tailscale IP is unreachable. Added `ConnectTimeout=5` to the remote SSH call. (AI-CLI-37)
- `ai internal handoff-drain` / NATS tunnel: `NATSClient._open_ssh_tunnel()` now checks only `AI_HOST` (was silently skipping when the var was absent from non-login shells). Session start no longer hangs on NATS retry loop on Mac when NATS tunnel fails to open. (AI-CLI-37)
- `ai tunnel start`: `_cmd_tunnel_start` now uses `vpn_host` when VPN is active so autossh reaches the server through the VPN-routed address rather than the Tailscale IP. (AI-CLI-37)
- Remote sessions / tmux invocations: replaced all `bash -l -c` / `bash -c` with `zsh -l -c` / `zsh -c` so that `~/.zshenv` (the canonical env var file) is sourced on remote connects and all tmux session spawns. (AI-CLI-37)

### Changed

- `AI_HOST` is the canonical env var for host machine identification (`mac`, `hetzner`, etc.). Set `AI_HOST` in `~/.zshenv` (sourced by all zsh sessions including non-interactive). (AI-CLI-37)

## [0.2.0] - 2026-04-06

### Added

- `ai gemini -m deep-research`: Gemini Deep Research via the Interactions API (`deep-research-pro-preview-12-2025`). Submits a background job, polls every 30s until complete, cancels via DELETE on Ctrl-C. Auth: `GOOGLE_API_KEY_FREE_TIER` → `GOOGLE_API_KEY_TIER_1` (REST-only; no OAuth path). Output follows the same `-o`/auto-file/stdout conventions as other models. (AI-CLI-36)
- `ai --version` / `ai -V`: print the installed package version and exit.
- `ai gemini -s`/`--start-tier TIER`: Skip earlier auth tiers explicitly (1=OAuth CLI, 2=free API key, 3=paid API key). Useful when OAuth returns truncated responses without erroring. (AI-CLI-36)
- `ai gemini -d standard`/`--depth standard`: Planner-Executor research pipeline — query generation → concurrent Gemini-grounded search → synthesis. Per-step JSON checkpointing at `~/.local/state/ai-cli/research-runs/<run-id>/`. Resume with `--resume <run-id>`. (AI-CLI-36)

### Fixed

- `ai gemini -m deep-research`: Interactions API submit response returns `"id"` (flat string), not `"name"` (resource path). Code was using `.get("name", "").split("/")[-1]`, producing an empty string and silently failing before polling started. Fixed to `interaction.get("id") or interaction.get("name", "").split("/")[-1]`. (AI-CLI-36)
- Prompt injection watcher: hardened against stale `❯` prompt visible during `claude --continue` startup. Added 10-cycle grace period, double `capture-pane` verification, removed `C-u` keystroke, and signal file is now deleted after injection (not before). (AI-CLI-35)
- Process hygiene (`ai ps`): stale PID file cleanup, orphan detection, and session health checks.
- `ai layout` command: YAML-driven iTerm2 window/tab/pane layout system. Subcommands: `list`, `validate <name>`, `profiles <name>`, `<name>` (apply). Layout files at `~/.config/iterm2/layouts/*.yaml`. Nested pane split model (vertical/horizontal). Dynamic Profile generation + runtime tinted icon per tab. See `docs/designs/iterm2-layout-system.md`.
- `icon_generator` module: runtime PNG icon generation with Pillow. Auto-contrast tint derived from tab background color via HSL color theory (180° hue rotation + lightness adaptation). Explicit `icon_color` override supported. Source logos at `src/ai_cli/data/icons/`. Falls back to Claude brand orange (`#da7756`) when no tab color is set.
- Per-session Dynamic Profile generation: each session gets a `ai-cli:{ai_name}` Dynamic Profile JSON (inherits from base profile, sets tab color + icon) written to `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-generated/` at launch and cleaned up on exit.
- Lease-file-based collision-free iTerm2 tab color assignment. Replaces modulo-based system; any palette size supported. Lease files at `~/.local/state/ai-cli/iterm2/color-leases.json`.
- `[iterm2.base_profiles]` config section: configure which iTerm2 base profile each session type inherits from.
- `[iterm2.project_colors]` config section: pin project/session names to preferred palette color slots.
- `[iterm2.icon_color_overrides]` config section: explicit icon tint override per palette color slot.
- `ai color <name|#hex>` command: ad hoc tab color reassignment for the current session.
- `ai internal cleanup-session-files <ai_name>`: remove session icon PNG and Dynamic Profile JSON; called by EXIT trap.
- OSC 1 title fix: session name now set via `\033]1;` instead of `\033]0;` so mosh on remote sessions does not prepend `[mosh]` to the tab title.
- `ai setup` command: detects managed platform vs standalone environment and configures `CLAUDE.md` accordingly; marks file `assume-unchanged` in git after swap so external users don't see local modifications
- `CLAUDE-full.md`: standalone self-contained Claude Code session config for users without the managed platform; `CLAUDE.md` remains the lean variant for managed platform users
- pyright basic mode type checking in CI lint job

### Security

- iTerm2 session-tracking files moved from `/tmp/` to `~/.local/state/ai-cli/iterm2/` (XDG Base Directory Specification), eliminating symlink attack surface on shared servers
- Session runtime files (`cc-exit-*`, `cc-resume-prompt-*`, `ai-watcher-lock-*`) moved from `/tmp/` to `~/.local/state/ai-cli/`
- `session_id_uuid` validated against UUID regex before bash f-string interpolation in `get_engine_script()`; malformed input is cleared
- `--project` argument rejects values containing `/` or `\` path separators

### Added

- CI test matrix (Python 3.11/3.12/3.13) with Codecov coverage reporting
- GitHub Release automation on tag push
- Pre-commit config (ruff + hygiene hooks)
- Renovate for dependency management (replaces Dependabot)
- Issue templates (bug report, feature request) and PR template
- Full README rewrite (PyOpenSci 13-section structure)
- CONTRIBUTING.md

## [0.1.1] - 2026-03-29

### Changed

- Generalized all code to remove hardcoded personal references; package now works
  for any user with config-driven settings
- `_get_main_project_name()` returns `None` instead of a default when not configured
- Sync requires explicit `[sync] remote_host` or `[remote] host` configuration
- Settings path translation is now config-driven instead of hardcoded

### Added

- CI workflow (ruff lint + pytest) on push to main and PRs
- PyPI publish workflow with Trusted Publishers (OIDC)
- Shields.io badges in README (PyPI, Python version, license, CI)
- CHANGELOG.md
- SECURITY.md with vulnerability reporting instructions

### Fixed

- 13 ruff lint issues (unused imports, undefined names, unused variables)
- 7 failing tests (remote transport, sync worktree isolation, PID lock mocking)

## [0.1.0] - 2026-03-29

### Added

- Initial release extracted from monorepo
- Unified `ai` CLI entry point for Claude Code and Gemini CLI sessions
- tmux-based session management with auto-resume loop
- Git worktree isolation per session
- Remote session support via mosh/SSH
- Cross-machine sync (`ai sync push/pull`) with bidirectional git staging
- JSONL conversation sync with cwd path translation
- Handoff queue for cross-session task delegation
- NATS-based fleet messaging (heartbeats, events, sync notifications)
- Desktop and push notifications
- Memory watch daemon with auto-dream support
- Quota tracking for API usage
- Stale session cleanup
- Session reconnection (`ai reconnect`)

[Unreleased]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.7.0...v0.8.0
[0.4.1]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sergeiwallace/ai-cli-utils/releases/tag/v0.1.0
