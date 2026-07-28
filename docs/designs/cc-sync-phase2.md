---
title: "CC Memory & History Sync Phase 2 — Bidirectional `ai sync` CLI"
category: infrastructure
tags: [cc, sync, ai-cli, git]
status: approved
source: sergei
---

> **Migrated from `sergei` (SW-837, 2026-07-26).** This design doc followed its implementation
> into this repo: the code it describes lives here, not in `sergei`, since the SW-907 repo-ownership
> migration. `sergei/docs/designs/cc-sync-phase2.md` is now a `status: moved` stub pointing here.
> Content is unchanged by the move — any drift between this doc and the current code predates it
> and is not a migration artifact.

# CC Memory & History Sync Phase 2 — Bidirectional `ai sync` CLI

**Status:** APPROVED

**Created:** 2026-03-23

**Task:** SW-642 (Phase 2)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

<!-- AIDO-128: the ToC sits ABOVE the Executive Summary (it is self-referential otherwise).
  D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc, with GitHub-style
  anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate in-window
  (incl. VS Code Remote-SSH). `aido toc check` validates this once AIDO-127 lands. If
  all-`###` proves too noisy, fall back to D5 (a) "meaningful `###`" — a deterministic
  OR-rule: include a `###` when it (1) has child `####`, (2) its section body ≥ ~8-10
  lines, (3) its parent `##` is allowlisted (Design Decisions / Open Questions /
  appendices), or (4) matches a pattern (`### D-N`); `<!-- toc:skip -->` /
  `<!-- toc:include -->` on a heading override the heuristic. -->

- [CC Memory \& History Sync Phase 2 — Bidirectional `ai sync` CLI](#cc-memory--history-sync-phase-2--bidirectional-ai-sync-cli)
  - [Table of Contents](#table-of-contents)
  - [Problem Statement](#problem-statement)
  - [Design Decisions](#design-decisions)
  - [Core System: `ai sync` CLI](#core-system-ai-sync-cli)
    - [Command Interface](#command-interface)
    - [Staging Repo Architecture](#staging-repo-architecture)
    - [Path Normalization](#path-normalization)
    - [Memory File Merge (Three-Way Git)](#memory-file-merge-three-way-git)
    - [JSONL Merge (Keep-Both)](#jsonl-merge-keep-both)
    - [CC Active Detection](#cc-active-detection)
    - [Conflict Notification](#conflict-notification)
    - [Hook Integration](#hook-integration)
  - [Data Model](#data-model)
    - [Staging Repo Structure](#staging-repo-structure)
    - [Commit Message Format](#commit-message-format)
    - [Conflict File Naming](#conflict-file-naming)
  - [Integration](#integration)
    - [Replacing Phase 1](#replacing-phase-1)
    - [ai-cli Subcommand Registration](#ai-cli-subcommand-registration)
    - [Hook Config Updates](#hook-config-updates)
  - [Implementation Phases](#implementation-phases)
    - [Phase 2a: Core `ai sync push/pull` + Staging Repo](#phase-2a-core-ai-sync-pushpull--staging-repo)
    - [Phase 2b: Hook Integration + Retire Phase 1](#phase-2b-hook-integration--retire-phase-1)
  - [Risks and Mitigations](#risks-and-mitigations)
  - [Open Questions](#open-questions)
  - [Approval Log](#approval-log)

---

## Problem Statement

Claude Code stores all session memory and conversation history under `~/.claude/projects/`, keyed by the absolute path of the project directory. Local Mac and the Hetzner dev server have different absolute paths (`/Users/sergeiwallace/projects/` vs `/home/sergei/projects/`), so Claude Code on each machine accumulates a completely isolated store. Memories learned on one machine are invisible on the other; conversation history diverges.

Phase 1 ([cc-memory-history-sync-plan.md](../plans/cc-memory-history-sync-plan.md)) solves the immediate problem with a one-way rsync shell script triggered by a CC stop hook. This is sufficient for the common case (work on Mac, push to server) but has fundamental limitations:

1. **No bidirectionality.** Work done on the server (remote CC sessions via `ai c --remote`) never flows back to the Mac. The server accumulates its own memories and conversations that are invisible locally.
2. **No conflict detection.** rsync with `--update` uses last-modified-time as the tiebreaker. If both machines edited the same memory file, the older version is silently discarded.
3. **No audit trail.** There is no record of what was synced, when, or whether any data was overwritten. Debugging a lost memory requires manually comparing mtimes.
4. **No data-type-aware merge.** rsync treats all files identically. Memory markdown files and JSONL conversation logs have fundamentally different merge semantics — markdown is text-mergeable, JSONL is not.

Phase 2 replaces the rsync script with a git-backed `ai sync push/pull` CLI subcommand in `ai-cli` that provides bidirectional sync, three-way merge for markdown, safe keep-both handling for JSONL, and full audit trail via git commits. This design doc specifies that system.

**Related documents:**
- [Architecture & Design Philosophy](architecture.md) — platform-level design principles
- [CC Memory & History Sync — Implementation Plan](../plans/cc-memory-history-sync-plan.md) — approved decisions and Phase 1 spec

---

## Design Decisions

All decisions below were made during Phase 2 planning in the implementation plan. This section documents the rationale in a consolidated form.

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Sync mechanism | (a) Custom rsync wrapper, (b) Unison, (c) Syncthing, (d) Git-backed staging repo, (e) Accept divergence | **(d) Git-backed staging repo** | Three-way merge for markdown. JSONL divergence detected before write. Full audit trail via commits. Rollback is `git revert`. Zero new infrastructure (git + SSH already in use). rsync has no conflict detection; Syncthing's continuous mode is unsafe with auto-dream; Unison requires extra config for path encoding. | Approved |
| 2 | JSONL conflict strategy | (a) Keep both files, (b) Timestamp-based line merge | **(a) Keep both** | JSONL conversations are structured sessions where messages reference prior messages (tool calls, continuations). Interleaving two conversations by timestamp would produce a broken file. Keep-both is zero-data-loss and zero-corruption-risk. With sequential machine usage, real JSONL conflicts will be rare. | Approved |
| 3 | CC active detection (Mac to server) | (a) SSH pgrep, (b) tmux session check, (c) Lock file | **(a) SSH pgrep** | `ssh root@server "pgrep -f claude"` is reliable and direct. tmux session check is indirect (session exists != CC active). Lock file on server requires CC hooks on server to manage, adding complexity. | Approved |
| 4 | CC active detection (server to Mac) | (a) Lock file on Mac, (b) Port tunnel / reverse SSH, (c) Skip check | **(c) Skip check** | Mac is behind NAT — server cannot SSH back. Pull-only architecture makes this moot: server never writes directly to Mac's `~/.claude/projects/`. Mac always initiates its own pull. No detection code needed for Phase 2. Implementation detail: lightweight local `pgrep` warning in `ai sync pull` for manual pulls (no SSH needed, does not block). | Approved |
| 5 | Sync trigger | (a) Hook-driven (stop/start hooks), (b) Cron, (c) Manual only | **(a) Hook-driven** | Aligns with event-driven platform philosophy. Stop hook calls `ai sync push` (session just ended, safe to sync). Start hook calls `ai sync pull` (session starting, want latest data). Cron is wasteful for sequential-machine usage. Manual-only defeats the purpose of automation. | Approved |
| 6 | Path encoding in staging | (a) Keep machine-specific paths as-is, (b) Normalize to bare project name | **(b) Normalize to bare project name** | The staging repo is the canonical shared representation. Using bare names (`sergei/`, `aurion/`) means the staging structure is machine-independent. Each machine's push/pull logic handles its own path prefix. This avoids encoding machine identity into the repo structure and makes adding a third machine trivial. | Approved |
| 7 | Project scope | (a) sergei only, (b) All projects with CC memory dirs | **(b) All projects** | User confirmed all projects should be synced. The sync command discovers projects dynamically by globbing `~/.claude/projects/`. No hardcoded project list. | Approved |

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. Git-backed staging repo: <approval or feedback>
> 2. JSONL keep-both: <approval or feedback>
> 3. Mac-to-server detection via SSH pgrep: <approval or feedback>
> 4. Server-to-Mac detection via lock file (tentative): <approval or feedback>
> 5. Hook-driven sync trigger: <approval or feedback>
> 6. Path normalization to bare project name: <approval or feedback>
> 7. All-projects scope: <approval or feedback>
> - <enter feedback here>

---

## Core System: `ai sync` CLI

### Command Interface

Two subcommands under `ai sync`:

```bash
ai sync push [--memories-only] [--dry-run] [--verbose]
ai sync pull [--memories-only] [--dry-run] [--verbose]
```text

**`ai sync push`** — Stages local CC data into the staging repo, commits, pushes to the remote bare repo, and applies changes on the remote machine.

**`ai sync pull`** — Fetches from the remote bare repo into the local staging repo and applies changes to local `~/.claude/projects/`.

**Flags:**
- `--memories-only` — Sync only `memory/*.md` and `MEMORY.md`. Skip JSONL conversation files and tool-results. Used by hooks when fast exit is preferred.
- `--dry-run` — Show what would be synced without writing anything. Prints the file list and any detected conflicts.
- `--verbose` — Print each file as it is staged, committed, and applied.

**Exit codes:**
- `0` — Sync completed successfully (including successful conflict resolution).
- `1` — Fatal error (staging repo corrupt, SSH unreachable, git push rejected).
- `2` — Conflicts detected and preserved (not a failure — files were written as `conflict-<ts>` variants). Prints conflict summary to stderr.

### Staging Repo Architecture

The staging repo is a regular git repository that serves as the intermediary between two machines. It never contains CC's actual working files — it holds a normalized copy used purely for sync.

**Local Mac:** `~/.claude-sync-staging/` — A regular git repo. Local push operations commit here, then `git push` to the server's bare repo.

**Server:** `~/.claude-sync-staging.git` — A bare git repo. Receives pushes from the Mac. Server pull operations clone/fetch from this bare repo into a working checkout at `~/.claude-sync-staging/`.

**Initial setup (one-time):**

```bash
# On server: create bare repo
git init --bare ~/.claude-sync-staging.git

# On Mac: create working repo, add remote
git init ~/.claude-sync-staging
cd ~/.claude-sync-staging
git remote add origin ssh://root@178.104.70.139/root/.claude-sync-staging.git
```text

The `ai sync` command checks for the staging repo on first run and initializes it if absent, so the user never needs to run the setup manually.

**Why a bare repo on the server?** The server also needs a working checkout to apply files from. But the bare repo is the push target (avoids pushing to a checked-out branch, which git refuses by default). The server-side receiver script handles the working-checkout step after receiving the push.

### Path Normalization

CC encodes project paths into directory names under `~/.claude/projects/`. The encoding differs per machine:

| Machine | CC path prefix | Example |
|---------|---------------|---------|
| Mac | `-Users-sergeiwallace-projects-` | `-Users-sergeiwallace-projects-sergei/` |
| Server | `-home-sergei-projects-` | `-home-sergei-projects-sergei/` |
| Worktree (Mac) | `-Users-sergeiwallace-projects-sergei--worktrees-sw-1/` | Glob: `*--worktrees-*` |
| Worktree (Server) | `-home-sergei-projects-sergei--worktrees-sw-1/` | Same glob pattern |

**Normalization rules (push direction):**

1. Strip the machine-specific prefix: `-Users-sergeiwallace-projects-` or `-home-sergei-projects-`
2. The remainder is the bare project name, possibly with a worktree suffix: `sergei/`, `sergei--worktrees-sw-1/`, `aurion/`
3. Commit under that bare name in the staging repo

**Denormalization rules (pull direction):**

1. Read the bare name from the staging repo
2. Prepend the local machine's prefix: `-Users-sergeiwallace-projects-` (Mac) or `-home-sergei-projects-` (server)
3. Copy files into `~/.claude/projects/<denormalized-name>/`

**Implementation detail:** The path prefix is determined at runtime by checking which machine the command is running on. The simplest heuristic: if `os.path.expanduser("~")` starts with `/Users/`, use the Mac prefix; otherwise use the server prefix. This is stored in `ai-cli` config as `[sync] local_prefix` for explicit override if needed.

**Worktree handling:** Worktree directories contain `--worktrees-` in their name. The glob `~/.claude/projects/*--worktrees-*` captures them. They are normalized the same way — the `--worktrees-sw-N` suffix is preserved in the bare name since it distinguishes the worktree's CC state from the main project's CC state.

### Memory File Merge (Three-Way Git)

Memory files (`memory/*.md` and `MEMORY.md`) are plain markdown, small, and infrequently written. They are the highest-value sync target — these are the files that teach CC about preferences, project state, and learned context.

**Merge strategy:** Standard three-way git merge via the staging repo.

**How it works:**

1. **Push side** stages local memory files into the staging repo and commits.
2. **git push** sends the commit to the remote bare repo.
3. **Pull side** fetches the new commit and runs `git merge` in its local staging checkout.
4. If git merge succeeds (clean merge): apply the merged files to `~/.claude/projects/`.
5. If git merge produces conflicts: leave conflict markers in the staging repo file, copy the conflicted file to `~/.claude/projects/` with a `.conflict` extension, and exit with code 2.

**Why three-way merge is right for memory files:** Memory files are edited by CC on one machine at a time (sequential usage pattern). The common case is a fast-forward merge (only one side changed). In the rare case both sides edited the same file between syncs, git's three-way merge handles non-overlapping edits cleanly. Overlapping edits produce conflict markers that a human or CC session can resolve.

**Conflict resolution for memory files:** If `git merge` leaves conflict markers:
- The conflicted file is written to `~/.claude/projects/<project>/memory/<file>.md.conflict`
- The original (pre-merge) file is left untouched
- A summary is printed to stderr: `CONFLICT: sergei/memory/project_current_work.md — resolve manually or in next CC session`
- The user or CC can review the `.conflict` file and merge manually

### JSONL Merge (Keep-Both)

JSONL conversation files (`*.jsonl`) are append-only structured logs. Each line is a JSON object representing a conversation turn. Lines reference prior lines (tool calls reference parent messages by ID). Interleaving lines from two files would break conversation structure.

**Merge strategy:** Keep both files. Zero data loss, zero corruption risk.

**How it works:**

1. **Push side** stages JSONL files into the staging repo and commits.
2. **Pull side** detects that both the staging repo and the local `~/.claude/projects/` have a JSONL file with the same name but different content.
3. **Divergence check:** Compare file hashes. If identical, no action needed. If the local file is a prefix of the staged file (or vice versa), fast-forward by taking the longer file.
4. **True divergence:** Both files grew independently. The pull side:
   - Keeps the local file as-is (it is the "current" file for this machine's CC)
   - Writes the remote version as `conflict-<ISO-timestamp>.jsonl` in the same directory
   - Prints: `JSONL CONFLICT: sergei/conversations.jsonl — remote version saved as conflict-2026-03-23T14-30-00.jsonl`

**Why not attempt a merge:** JSONL lines have semantic dependencies. A conversation looks like:

```jsonl
{"type":"human","text":"Fix the bug in config.py"}
{"type":"assistant","text":"I'll look at config.py...","tool_use":[{"id":"tu_1",...}]}
{"type":"tool_result","tool_use_id":"tu_1","content":"..."}
```text

Interleaving two conversations by timestamp would produce a file where tool results reference tool uses from a different conversation. CC would fail to load this.

**Cleanup:** Conflict JSONL files accumulate over time. They are harmless (CC ignores files it does not own) but could be cleaned up periodically. A future `ai sync clean` command could list and remove old conflict files. This is not in scope for Phase 2.

### CC Active Detection

Before applying synced files to `~/.claude/projects/`, the sync command checks whether CC is actively running on the target machine. Writing to files that CC has open is unsafe — CC may have in-memory state that would be inconsistent with the on-disk change.

**Mac to Server (checking if server CC is active):**

```python
def is_cc_active_on_server(server_host: str) -> bool:
    """Check if any Claude Code process is running on the server."""
    result = subprocess.run(
        ["ssh", server_host, "pgrep", "-f", "claude"],
        capture_output=True, timeout=10
    )
    return result.returncode == 0
```text

If CC is active on the server, `ai sync push` prints a warning and aborts:

```yaml
WARNING: Claude Code is active on server. Sync aborted.
Exit the server CC session first, or use --force to sync anyway.
```text

The `--force` flag overrides the safety check. This is acceptable because the memory files are small and CC re-reads them on access — the risk is a brief inconsistency, not corruption.

**Server to Mac (checking if Mac CC is active):**

This direction is harder because the Mac is behind NAT — the server cannot SSH back to the Mac. See [Open Questions](#open-questions) for the full discussion. For the initial Phase 2 implementation, server-to-Mac sync (`ai sync pull` run from the Mac) does not need this check because the Mac initiates the pull — if you are running `ai sync pull`, you are on the Mac and can see whether CC is running.

The check is only needed for a hypothetical future scenario where the server pushes to the Mac autonomously (e.g., a server-side stop hook that triggers a Mac pull). That scenario is deferred.

**Manual pull warning:** When `ai sync pull` is run manually on the Mac and a local CC process is detected (via local `pgrep -f claude`), the command prints a warning but does not block:

```yaml
WARNING: Claude Code is active locally. Sync will modify files CC may have loaded.
Proceeding anyway — run `ai sync pull` after your session to be safe, or use --force.
```text

### Conflict Notification

When `ai sync` exits with code 2 (conflicts preserved), it fires two notifications so conflicts are never silent — even when the sync runs via a hook in the background.

**1. macOS system notification:**

```python
def notify_conflicts(conflicts: list[str]) -> None:
    """Fire a macOS notification banner listing the conflicted files."""
    summary = ", ".join(conflicts[:3])
    if len(conflicts) > 3:
        summary += f" (+{len(conflicts) - 3} more)"
    subprocess.run([
        "osascript", "-e",
        f'display notification "{summary}" with title "ai sync: conflict detected" subtitle "Review .conflict files or check ~/.claude-sync-conflicts.log"'
    ], capture_output=True)
```text

The notification is visible immediately as a banner, even if the sync ran silently via a start/stop hook.

**2. Persistent conflict log (`~/.claude-sync-conflicts.log`):**

Each conflict appends a structured entry:

```text
2026-03-23T14:30:00 CONFLICT memory sergei/memory/project_current_work.md — .conflict file written
2026-03-23T14:30:00 CONFLICT jsonl  sergei/conversations.jsonl — remote saved as conflict-2026-03-23T14-30-00.jsonl
```text

Format: `<ISO-timestamp> CONFLICT <type> <bare-path> — <action taken>`

You can ask CC in any session: "Were there any sync conflicts?" — CC checks `~/.claude-sync-conflicts.log` and walks you through what happened and what needs review.

**3. CC session startup conflict report (Phase 2b):**

At session start, CC checks `~/.claude-sync-conflicts.log` for unresolved conflicts and scans `~/.claude/projects/*/memory/` for `.conflict` files. If any are found, CC raises them proactively in chat — you do not need to ask:

1. CC reports: "I found a sync conflict from [time]: [file]. Let me resolve it."
2. CC reads both the original and `.conflict` version, auto-merges using its judgment.
3. CC presents a summary: "I resolved the conflict by [description]. Here's what changed: [diff summary]. Does this look right?"
4. **User approves** → CC writes the resolved file, deletes the `.conflict` file, logs the resolution to `~/.claude-sync-conflicts.log`.
5. **User requests changes** → CC revises and re-presents before writing.

This ensures no conflict is ever silently discarded and you have final approval before any merged content is written to memory.

**Conflict resolution responsibilities:**

| Conflict type | Resolved automatically? | Flow |
|---|---|---|
| Clean git merge (non-overlapping edits) | **Yes** — silent | Nothing — CC reads the merged result directly |
| Git conflict markers in `.conflict` file | **CC auto-resolves with approval gate** | CC detects at startup, proposes resolution, user approves before file is written and `.conflict` is deleted |
| JSONL keep-both | **N/A** — no merge attempted | Remote version in `conflict-<ts>.jsonl` is available for reference; CC summarizes its contents on request |

### Hook Integration

Phase 2 hooks replace Phase 1's `sync-cc-to-server.sh` call with `ai sync` commands.

**Stop hook (CC exit):**

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "ai sync push 2>/dev/null || true"
      }
    ]
  }
}
```text

On every CC session exit, `ai sync push` stages all local CC data, commits to the staging repo, and pushes to the server. The `|| true` ensures the hook exits 0 even if the server is unreachable (non-blocking — sync failure should not break the CC exit flow).

**Start hook (CC launch):**

```json
{
  "hooks": {
    "Start": [
      {
        "type": "command",
        "command": "timeout 5 ai sync pull --memories-only 2>/dev/null || true"
      }
    ]
  }
}
```text

On every CC session start, `ai sync pull --memories-only` fetches the latest memories from the server. The `timeout 5` wrapper hard-caps the hook at 5 seconds — if the server is slow or unreachable, CC starts normally after a 5-second delay at most. The `|| true` ensures the hook always exits 0.

**Why `--memories-only` for start hook:** Full JSONL pull could take 5-15 seconds for large conversation histories. Memory files are typically <100KB total across all projects. The start hook should complete in <2 seconds to avoid delaying session startup. Full JSONL sync happens on the push side (stop hook) when latency is less noticeable.

---

> **Feedback Round 1:** Does this approach feel right? What's missing?
> - <enter feedback here>

---

## Data Model

### Staging Repo Structure

The staging repo mirrors the `~/.claude/projects/` structure but with normalized (machine-independent) directory names.

```text
~/.claude-sync-staging/
├── .git/
├── sergei/
│   ├── memory/
│   │   ├── MEMORY.md
│   │   ├── project_current_work.md
│   │   ├── user_profile.md
│   │   └── ...
│   ├── conversations.jsonl
│   └── tool-results/
│       └── <uuid>/
│           └── ...
├── sergei--worktrees-sw-1/
│   ├── memory/
│   │   └── MEMORY.md
│   └── conversations.jsonl
├── sergei--worktrees-sw-2/
│   ├── ...
├── aurion/
│   ├── memory/
│   │   └── ...
│   └── conversations.jsonl
├── aido/
│   └── ...
└── ...
```text

**Key properties:**
- Each top-level directory is a bare project name (or project + worktree suffix)
- No machine-specific path prefixes anywhere in the repo
- Worktree directories preserve the `--worktrees-sw-N` suffix as part of the directory name
- The staging repo is append-only in normal operation (files are added/updated, never deleted by the sync process)

### Commit Message Format

Every sync operation produces a commit in the staging repo. Commit messages follow a structured format for auditing:

```text
sync push from mac 2026-03-23T14:30:00

projects: sergei, aurion
files: 12 changed
memories: 8 files
jsonl: 4 files
```text

Format specification:
- **Line 1:** `sync {push|pull} from {mac|server} <ISO-8601-timestamp>`
- **Line 3+:** Summary metadata — project list, file counts by type

This format enables `git log --oneline` to show a clear sync history:

```text
a1b2c3d sync push from mac 2026-03-23T14:30:00
e4f5g6h sync push from mac 2026-03-23T10:15:00
i7j8k9l sync push from server 2026-03-22T22:00:00
```text

### Conflict File Naming

**Memory files (.md):** Conflicts are written with a `.conflict` extension alongside the original:

```text
~/.claude/projects/-Users-sergeiwallace-projects-sergei/memory/project_current_work.md           # original, untouched
~/.claude/projects/-Users-sergeiwallace-projects-sergei/memory/project_current_work.md.conflict  # conflicted version with markers
```text

**JSONL files:** Conflicts are written with a `conflict-<timestamp>` prefix:

```text
~/.claude/projects/-Users-sergeiwallace-projects-sergei/conversations.jsonl                      # local version, untouched
~/.claude/projects/-Users-sergeiwallace-projects-sergei/conflict-2026-03-23T14-30-00.jsonl       # remote version
```text

The timestamp uses a filesystem-safe ISO format (hyphens instead of colons). This ensures conflict files sort chronologically and are visually identifiable.

---

## Integration

### Replacing Phase 1

Phase 2 fully replaces Phase 1's `scripts/sync-cc-to-server.sh`. The migration path:

1. Phase 2a ships `ai sync push/pull` as working commands
2. Phase 2b updates the CC stop hook to call `ai sync push` instead of `sync-cc-to-server.sh`
3. Phase 2b adds a CC start hook calling `ai sync pull --memories-only`
4. `sync-cc-to-server.sh` is deleted (or archived with a deprecation note)

There is no compatibility period — Phase 1 and Phase 2 do not run concurrently. The switch is atomic: update the hook config, delete the old script.

**Data migration:** The staging repo starts empty. The first `ai sync push` after Phase 2 is installed seeds the repo with the current state of `~/.claude/projects/` on the Mac. This is equivalent to Phase 1's initial full sync but goes through the git staging path instead of raw rsync.

### ai-cli Subcommand Registration

The `ai sync` subcommand follows the existing pattern in `ai-cli/src/ai_cli/main.py`, where subcommands are dispatched via early `sys.argv` checks before the main argparse parser runs (same pattern as `ai handoff` and `ai upgrade`).

```python
# In cli() function, before main argparse:
if len(sys.argv) > 1 and sys.argv[1] == "sync":
    if len(sys.argv) == 2:
        print("Usage: ai sync [push|pull]"); sys.exit(1)
    action = sys.argv[2]
    flags = sys.argv[3:]  # --memories-only, --dry-run, --verbose, --force
    if action == "push":
        sync_push(flags)
    elif action == "pull":
        sync_pull(flags)
    else:
        print(f"Unknown sync action: {action}"); sys.exit(1)
    sys.exit(0)
```text

The sync logic itself lives in a new module: `ai_cli/sync.py`. This keeps the main entry point clean and allows the sync module to be tested independently.

**Package structure after Phase 2:**

```yaml
ai-cli: src/ai_cli/
├── __init__.py
├── main.py          # CLI entry point, subcommand dispatch
├── sync.py          # NEW: ai sync push/pull implementation
├── messaging.py     # NATS messaging
└── notifications.py # System notifications
```text

### Hook Config Updates

Phase 2 modifies `~/.claude/settings.json` on both machines.

**Mac (after Phase 2b):**

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "ai sync push 2>/dev/null || true"
      }
    ],
    "Start": [
      {
        "type": "command",
        "command": "timeout 5 ai sync pull --memories-only 2>/dev/null || true"
      }
    ]
  }
}
```text

**Server (after Phase 2b):**

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "ai sync push 2>/dev/null || true"
      }
    ],
    "Start": [
      {
        "type": "command",
        "command": "ai sync pull --memories-only 2>/dev/null || true"
      }
    ]
  }
}
```text

Both machines get identical hook configs. The `ai sync` command determines the correct path prefix and remote target based on which machine it is running on.

---

## Implementation Phases

<!-- Per-phase task ACs follow the canonical AC quality rules. `docs/procedures/task-authoring-standards.md`
  is AUTHORITATIVE (open it for the full/latest standard; this inline reminder is sync-checked
  against its canonical block by `aido validate-doc` and must not be edited independently): -->

<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes this from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
   gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
   structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
   review against the ACs), never self-declared "done". -->

Phase 2 is split into two sub-phases to allow testing the core sync logic before wiring it into hooks.

### Phase 2a: Core `ai sync push/pull` + Staging Repo

**Scope:** Implement the `ai sync` subcommand as a manually-invoked tool. No hooks yet.

**Deliverables:**
- `ai_cli/sync.py` — staging repo init, path normalization, git commit/push/pull, memory merge, JSONL keep-both, CC active detection
- Subcommand registration in `main.py`
- Staging repo auto-initialization on first run
- `--memories-only`, `--dry-run`, `--verbose`, `--force` flags
- Tests for path normalization, merge logic, conflict detection

**Acceptance criteria:**
- [ ] `ai sync push` on Mac stages all `~/.claude/projects/` memory + JSONL into staging repo, commits, and pushes to server bare repo
- [ ] `ai sync pull` on Mac fetches from server bare repo and applies to local `~/.claude/projects/`
- [ ] Memory files merge via three-way git merge; conflicts produce `.conflict` files
- [ ] JSONL files use keep-both; divergent files produce `conflict-<ts>.jsonl`
- [ ] `--memories-only` skips JSONL and tool-results
- [ ] `--dry-run` prints file list and conflict predictions without writing
- [ ] CC active detection warns and aborts if CC is running on target machine
- [ ] `--force` overrides the active detection check
- [ ] Staging repo is auto-created on first run (both Mac and server)
- [ ] Path normalization correctly handles all project dirs and worktree dirs
- [ ] Idempotent: running push twice with no changes produces an empty commit (or no commit)
- [ ] Exit code 0 on success, 1 on fatal error, 2 on conflicts-preserved

### Phase 2b: Hook Integration + Retire Phase 1

**Scope:** Wire `ai sync` into CC hooks on both machines. Remove the Phase 1 shell script.

**Deliverables:**
- Updated `~/.claude/settings.json` on Mac (stop hook → `ai sync push`, start hook → `timeout 5 ai sync pull --memories-only`)
- Updated `~/.claude/settings.json` on server (same hooks)
- Delete `scripts/sync-cc-to-server.sh` (or archive to `scripts/archive/`)
- Hook config reference checked into `sergei` repo for documentation
- Updated `CLAUDE.md` session startup checklist — add sync conflict check step
- `ai sync conflicts` subcommand that prints unresolved `.conflict` files and recent log entries (for CC to call at startup)

**Acceptance criteria:**
- [ ] Stop hook fires `ai sync push` on CC exit (Mac and server)
- [ ] Start hook fires `timeout 5 ai sync pull --memories-only` on CC start (Mac and server)
- [ ] Hook completes in <5 seconds for memories-only, <15 seconds for full sync
- [ ] Hook fails silently (exits 0) if remote is unreachable
- [ ] Phase 1 script removed; no references remain in hook configs
- [ ] Full round-trip verified: edit memory on Mac → exit CC → start CC on server → memory appears
- [ ] At session start, CC proactively reports any unresolved `.conflict` files found in `~/.claude/projects/`
- [ ] CC auto-resolves memory conflicts and presents summary for user approval before writing the resolved file
- [ ] User approval is required before any `.conflict` file is deleted and resolved content is written
- [ ] JSONL conflict files are summarized by CC on request

**Dependencies:** Phase 2a complete and manually tested for at least a few sessions.

---

> **Feedback Round 1:** Does the phasing feel right -- too big, too small? Should anything move earlier or later?
> - <enter feedback here>

---

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Staging repo corruption** (bad merge, interrupted push) | Sync breaks until repo is repaired. No CC data loss — staging repo is a copy, not the source of truth. | Low | `ai sync` validates repo health before each operation. If corrupt, prints repair instructions (`git reset --hard origin/main`) or re-initializes from scratch. The staging repo is disposable — nuking and re-creating it loses only sync history, not CC data. |
| **Auto-dream race condition** (SW-644) | Anthropic's auto-dream rewrites `MEMORY.md` asynchronously. A sync push/pull mid-dream could stage a partially-written file. | Low (auto-dream not yet on our account) | Deferred to SW-644. Current mitigation: sync is discrete and transactional (commit-then-apply, not continuous). When auto-dream ships, review whether a lock/signal is needed. |
| **Concurrent writes on both machines** | Both machines edit the same memory file between syncs. Git merge produces conflict markers instead of clean file. | Low (sequential usage is the norm) | Three-way merge handles non-overlapping edits. Overlapping edits produce `.conflict` files with clear resolution path. Worst case: user spends 30 seconds resolving a conflict. |
| **Server-to-Mac NAT detection gap** | Cannot detect if Mac CC is active when pushing from server. Risk of writing to files CC has open on Mac. | Medium | Deferred — see Open Questions. Current mitigation: server `ai sync push` only writes to the staging bare repo, not to Mac's `~/.claude/projects/`. The Mac pulls at its own initiative (start hook). This eliminates the push-to-active-CC risk entirely for server-to-Mac direction. |
| **Stale lock file (CC crash)** | If CC crashes without firing the stop hook, `~/.claude/.sync-lock` persists. Server would refuse to sync thinking CC is active. | Medium | Write PID into the lock file. Before honoring the lock, check if the PID is still running. If not, delete the stale lock and proceed. Also apply a mtime-based timeout (e.g., lock older than 2 hours is considered stale). |
| **Large JSONL files slow down sync** | Conversation histories can grow to tens of MB over months. Git staging + SSH push takes time. | Medium | `--memories-only` flag for hooks where speed matters. Git uses delta compression, so incremental pushes of append-only files are efficient. If JSONL files become problematically large, consider `git lfs` or periodic archival (out of scope for Phase 2). |
| **SSH key / connectivity issues** | Server unreachable, SSH key not loaded, network timeout. | Medium | All sync operations fail gracefully with clear error messages. Hook invocations use `|| true` to prevent CC exit failures. `--dry-run` can diagnose connectivity before real sync. |

---

## Open Questions

1. **Server-to-Mac CC detection.** The Mac is behind NAT, so the server cannot SSH back to check if CC is active on the Mac. The lock file approach (`~/.claude/.sync-lock`) was discussed but has stale-lock and crash-recovery concerns. However, the current architecture may not need this check at all: the server never pushes directly to the Mac's `~/.claude/projects/`. The Mac pulls at its own initiative (via start hook or manual `ai sync pull`). If the Mac is pulling, the user is on the Mac and can see whether CC is running. **Question: Is server-to-Mac active detection actually needed in Phase 2, or is it fully mitigated by the pull-only architecture?**

2. **Conflict frequency validation.** The keep-both JSONL strategy and three-way memory merge are designed for the rare-conflict case (sequential machine usage). Before finalizing, Phase 1 should run for 2-4 weeks to establish empirical conflict frequency. **Question: After Phase 1 has run for a few weeks, should we review actual conflict data before committing to Phase 2 implementation? Or is the approved architecture robust enough to proceed regardless?**

3. **Start hook safety.** The start hook calls `ai sync pull --memories-only` at CC session launch. If the pull modifies memory files that CC is about to load, there is a potential race between the hook completing and CC reading the files. CC hooks run before the session is fully initialized, so this should be safe — but it has not been empirically verified. **Question: Should `ai sync pull` be a start hook (automatic, every session) or manually triggered (user runs `ai sync pull` before starting CC)? The risk of automatic pulls is disruption if the pull takes too long or encounters an error; the risk of manual-only is that users forget and work with stale memories.**

---

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. Server-to-Mac active detection — needed or mitigated by pull-only?
>
>     1. help me think through this. is it needed? I'm not sure. I would know if there is a local cc session, sure, but would my knowing matter for this automated 2-way sync? really just want to make sure we mitigate / handle potential syncing issues for 2-way sync? propose to me options, pros/cons, and a recommendation and explain your reasoning for choosing that option(s).
> 2. Conflict frequency validation — wait for data or proceed?
>
>     1. let's go ahead and do it. hopefully conflicts will be low / unlikely as i primarily just work on dev server going forward once this is all done.
> 3. Start hook — automatic or manual?
>
>     1. i will definitely forget and i don't want a manual process at start. let's make it automatic. is there anyway to mitigate potential issues w/ race conditions or pull taking too long or errors etc? we can also see how it goes and address any issues as they arise, but we should try to be proactive right now to catch any issues we can foresee ahead of time.
>
> **AI Response Round 1:**
>
> **Q1 — Server-to-Mac active detection:**
>
> Short answer: **not needed in Phase 2 — the pull-only architecture fully mitigates the risk.** The server never writes directly to Mac's `~/.claude/projects/`. It only writes to the bare staging repo (`~/.claude-sync-staging.git`). The Mac always controls when it reads from staging (via start hook or manual `ai sync pull`). There is no Phase 2 scenario where the server autonomously modifies Mac files while CC is running.
>
> The start hook fires before CC reads files into memory, so even the "pull right as CC starts" case is safe — files land on disk, then CC initializes and reads them. Correct order by construction.
>
> | # | Option | Pros | Cons | Rec |
> |---|--------|------|------|-----|
> | A | No detection — fully mitigated by pull-only | No code, no stale-lock risk, covers Phase 2 scope completely | None for Phase 2 | ✓ |
> | B | Lightweight local check in `ai sync pull` — warn if local CC is running | Safety net for manual pull while CC is active; local `pgrep`, no SSH needed | Trivial overhead | ✓ add-on |
> | C | Lock file on Mac (future-proofing) | Ready if server ever pushes to Mac autonomously | Stale lock on crash, extra hook complexity, premature for Phase 2 | ✗ |
>
> **Recommendation: Option A + B.** No detection needed for the automated flow. Add a lightweight local `pgrep` check in `ai sync pull` that prints a warning (but does not block) when CC is running and a manual pull is triggered. No lock file. This also resolves Decision 4 — recommend revising its status from "Tentative" to "Skip check — mitigated by pull-only (Phase 2)". Will update the table on your confirmation.
>
> **Q2 — Conflict frequency validation:**
>
> Noted — proceeding regardless. The architecture handles both rare and frequent conflicts gracefully. Primary server usage going forward further reduces conflict likelihood. Closing this open question.
>
> **Q3 — Start hook:**
>
> Automatic is right. Four proactive mitigations implemented or confirmed:
>
> 1. **Timeout wrapper** — `timeout 5 ai sync pull --memories-only 2>/dev/null || true`. Hard-caps hook at 5 seconds. If server is slow or unreachable, CC starts normally after 5s at most. Updated the hook spec and both hook config blocks above.
> 2. **Race condition** — Not actually a risk. Hooks run before CC reads files into memory. Files land on disk, then CC initializes and reads them. Correct order by construction.
> 3. **Pull errors / conflicts** — `|| true` ensures CC always starts regardless. Conflicts produce `.conflict` files; CC reads the clean merged state.
> 4. **`--memories-only`** — Already in the spec. Limits scope to ~100KB of small files; fast even on slow connections.
>
> Remaining known risk: if a pull conflict produces a `.conflict` file at session start, CC reads the merged state (good) but the conflict notification is silent at startup. User won't notice until they actively look. Acceptable — rare edge case, not a startup blocker.

---

> **Feedback Round 2:** Follow-up on the open questions:
> 1. Decision 4 revision — confirm updating status to "Skip check — mitigated by pull-only (Phase 2)"?
>
>     1. option A + B approved.
> 2. Start hook `timeout 5` — any other mitigations to add?
>
>     1. that sounds good. I'll trust your judgement on that. approved. regarding a conflict file, will you merge / resolve those conflicts yourself? should we have some warning / notification so i know there was a sync conflict so i can double check on what happened (by talking to cc session) to verify everything was handled correctly.
> 3. Any remaining open questions, or are all three resolved and ready to close?
>
> **AI Response Round 2:**
>
> **Item 1 — Decision 4:** Updated. Row 4 in the table now reads "Skip check — Approved." Option A (no detection for automated flow) + Option B (local pgrep warning for manual pulls) both reflected.
>
> **Item 2 — Conflict resolution and notification:**
>
> **Who resolves conflicts:**
>
> | Conflict type | Resolved automatically? | What to do |
> |---|---|---|
> | Clean git merge (non-overlapping edits) | **Yes** — applied silently | Nothing |
> | Git conflict markers in `.conflict` file | **No** — CC-assisted review | Open `.conflict` file in a CC session; CC helps merge and deletes the file when done |
> | JSONL keep-both | **N/A** — no merge attempted | `conflict-<ts>.jsonl` available for reference; no action required |
>
> **Conflict notification:** Added a new "Conflict Notification" subsection to the doc (above). When `ai sync` exits with code 2, it now:
> 1. Fires a **macOS system notification** — visible as a banner even when sync runs via a background hook
> 2. Appends a structured entry to **`~/.claude-sync-conflicts.log`** — persistent record, queryable from any CC session
>
> In any CC session after a conflict: "Were there any sync conflicts?" — CC checks the log and walks you through what happened.
>
> **Item 3 — Open questions:** All three resolved and closed. Q1 (server-to-Mac detection): closed, mitigated by pull-only. Q2 (conflict frequency): closed, proceeding regardless. Q3 (start hook): closed, automatic with timeout 5.

---

> **Feedback Round 3:** Final review pass:
> - Conflict notification design (macOS banner + log file) — does this feel right?
>
>     1. yes, i think so. as long as the cc session notes it in chat and gives me summary on the sync conflict and if/how it was resolved so i can have visibility and respond as necessary, then we're all good. just don't want it to be silent. macOS banner is fine but also want cc session to discuss / mention it to me, as well.
> - Conflict resolution table — does the "CC-assisted review" flow for `.conflict` files match your expectations?
>
>     1. yes, i think so. I'd like them to automatically handle the conflict resolution, if possible, and then mention / summarize what happened for me to review and approve before we can move on, just as a safety check so cc session doesn't silently resolve a conflict in an undesirable manner.
> - Any sections of the doc that still need attention before approval?
>
> **AI Response Round 3:**
>
> **Conflict notification — CC session mention:** Added. The Conflict Notification section now specifies a third mechanism: at session startup, CC proactively checks `~/.claude-sync-conflicts.log` and scans for `.conflict` files, then raises them in chat without waiting to be asked. Phase 2b deliverables updated to include a `ai sync conflicts` subcommand and a `CLAUDE.md` startup checklist update to wire this in.
>
> **Conflict resolution — auto-resolve with approval gate:** Updated. The flow is now:
> 1. CC detects `.conflict` file at startup and reports it in chat
> 2. CC auto-merges using its judgment and presents a diff summary
> 3. **User approves** before CC writes the resolved file and deletes the `.conflict`
> 4. If user requests changes, CC revises and re-presents
>
> No conflict is silently discarded. You have final say before anything is written. Updated both the Conflict Notification section and Phase 2b ACs to reflect this.
>
> **Remaining sections:** The Design Decisions, Core System, and Implementation Phases feedback prompts still show `<enter feedback here>` — you haven't reviewed those sections yet. If you're satisfied with the overall approach and want to give global approval rather than section-by-section, just say the word and I'll close out all open feedback prompts and update the Approval Log.

---

> **Feedback Round 4:**
> - Does the auto-resolve + approval gate flow match what you had in mind?
>   - yes, its exactly what i want. approved.
> - Ready to give overall approval, or are there other sections to review?
>   - yes, it all looks good now. i approve the entire design doc. on the detailed section details, I trust your judgement on that. don't want / need to get into all that. just want to make sure high level design decisions are what i want. doc is approved. we can proceed.
>
> **AI Response Round 4:**
>
> Acknowledged — full approval received. Doc marked APPROVED. Implementation proceeds directly from this design doc, Phase 2a first. No further review rounds needed.

---

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-03-23 | Design drafted | Phase 2 design doc covering `ai sync push/pull`, staging repo, merge strategies, hook integration |
| 2026-03-23 | Round 1 feedback | Open questions addressed: Decision 4 tentative, conflict frequency proceed regardless, start hook automatic with `timeout 5` |
| 2026-03-23 | Round 2 feedback | Decision 4 approved (skip check, pull-only); start hook `timeout 5` approved; conflict notification question raised |
| 2026-03-23 | Round 3 feedback | Conflict notification approved (macOS banner + log); auto-resolve with approval gate added to spec; CC session startup conflict report added |
| 2026-03-23 | Round 4 feedback | Auto-resolve + approval gate confirmed. Full doc approval given. |
| 2026-03-23 | **APPROVED** | Full design approved. Implementation proceeds from this doc directly (no separate impl plan). Phase 2a first. |
