---
title: "AI-CLI-56: Duplicate prompt boxes / statusline in scrollback buffer"
category: bugs
tags: [bug, statusline, quota, scrollback]
status: fix-deployed
severity: P0
task: AI-CLI-56
fixed_by: "AI-CLI-55 regression + cache stampede — fixed in this document's tracking session"
---

# AI-CLI-56: Duplicate Prompt Boxes / Statusline in Scrollback Buffer

**Status:** fix-deployed

**Severity:** P0

**Created:** 2026-04-24

**Task:** AI-CLI-56

## Symptoms

Scrolling back through a Claude Code session history shows the CC prompt box and statusline rendered multiple times at irregular intervals, breaking up the conversation flow. The duplicates appear as complete redraws of the input area rather than single-line artifacts.

## Environment

- macOS + iTerm2 (primary reporter)
- `ai quota statusline-part` called on every CC render cycle during streaming
- Reproduction frequency: consistent under active streaming; intermittent on quiet sessions

## Reproduction Steps

1. Open a CC session with the statusline hook active
2. Submit a prompt that generates a long streaming response
3. Scroll back through the conversation history
4. Observe: the prompt box / statusline appears multiple times, not just once at the end

## Root Cause Analysis

Two independent bugs compound to produce the symptom:

### Bug A — Python: missing DB column causes silent exception

`quota_statusline_part()` opens a raw `sqlite3.connect()` without calling `_init_db()`. When
AI-CLI-55 added `weekly_sonnet_pct` to the `CREATE TABLE IF NOT EXISTS` statement, existing
installations with an older DB schema were not migrated (ALTER TABLE only runs inside
`_init_db()`, which is bypassed by the direct connect). The resulting `SELECT` query:

```sql
SELECT usage_percent, snapshotted_at, weekly_sonnet_pct FROM quota_snapshots
```

raises `sqlite3.OperationalError: table quota_snapshots has no column named weekly_sonnet_pct`
on any DB created before AI-CLI-55. The outer `except Exception: pass` silently swallowed it,
leaving `quota_statusline_part()` returning `0` with **no stdout output**.

### Bug B — Bash: empty cached value bypasses the 30-second cache

The statusline bash script cached the quota output in a two-line file (timestamp + value) and
guarded re-runs with `if [[ -z "$quota_part" ]]; then`. Because an empty string is always
"zero-length", this check could not distinguish "valid cached empty result" from "no cache or
stale cache". Every render cycle found `quota_part=""` and re-ran `ai quota statusline-part`.

Combined effect: every statusLine call (which fires on every CC render during streaming, often
many times per second) executed `ai quota statusline-part`, which takes ~1.4s. Concurrent
blocking 1.4-second subprocesses overlapped, each writing a full terminal render to the
scrollback buffer.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-04-24 | Added 30s quota cache, 60s telemetry rate-limit, 5s branch cache to the bash statusline script. Added `\033[K` (erase-to-EOL) and newline stripping. | Shipped in commits `8125175` / `4ee7bf4`. Appeared to fix the issue but regression returned after AI-CLI-55 ship because the Python-side bug (Bug A) triggered the bash-side bug (Bug B) that the original fix failed to anticipate. |
| 2 | 2026-04-24 | Fixed DB migration path (Bug A) and replaced empty-string cache guard with explicit validity flag (Bug B). | Shipped in `e2bde8f`. Fixed those two bugs but a third root cause (Bug C, cache stampede) remained. Issue recurred after each 30s cache expiry window — visible as a burst of simultaneous redraws. |

## Root Cause Analysis — Round 2 (2026-04-27)

### Bug C — Cache stampede during 30s refresh window

After the Round 1 fix, `ai quota statusline-part` (~700ms: Python startup + SQLite + optional NATS
check) was called synchronously on cache miss. CC calls `statusLine` on every render cycle during
streaming — many times per second. When the 30s cache expires:

1. All concurrent render invocations find `_quota_cache_valid=0` simultaneously
2. Each launches a synchronous `ai quota statusline-part` call (~700ms)
3. All 5–20 concurrent calls complete near-simultaneously, each writing a full status line to the terminal
4. Result: burst of duplicate prompt boxes in scrollback, one per concurrent render cycle

The Round 1 cache fix solved the steady-state case (a single invocation waits 30s before recalling)
but not the transient thundering-herd case (multiple concurrent invocations all missing the expired
cache at the same moment).

## Fix

### Round 1 — Python (`quota_db.py` + `quota.py`)

Added `_migrate_snapshot_columns()` at the end of `_init_db()`. It reads `PRAGMA table_info`
and issues `ALTER TABLE quota_snapshots ADD COLUMN weekly_sonnet_pct REAL` (and `extra_pct REAL`)
when the column is absent — safe to call repeatedly (no-op if columns exist).

In `quota_statusline_part()`, changed `sqlite3.connect()` to also call `_init_db(conn)` on the
connection before executing any query. This ensures the migration runs on every statusline
evaluation, even for the direct-connect path that bypasses `_get_conn()`.

### Round 1 — Bash (`statusline-command.sh`)

Replaced the `if [[ -z "$quota_part" ]]; then` pattern with an explicit `_quota_cache_valid`
flag that tracks whether the timestamp check passed, independent of the cached value.

### Round 2 — Stale-while-revalidate cache pattern (`statusline-command.sh`)

Replaced the simple 30s cache with a three-zone stale-while-revalidate pattern:

| Zone | Age | Behavior |
|------|-----|----------|
| Fresh | < 30s | Serve directly — no call |
| Stale | 30s–300s | Serve stale value this render cycle; fire background refresh once |
| Very old | > 300s | Synchronous fetch (only on first call or after long idle gap) |

A lock file (`${TMPDIR:-/tmp}/.ai-sl-quota-lock-${UID:-0}`) prevents concurrent background
refreshes from stampeding. The lock records its own timestamp; locks older than 120s are treated
as stale (e.g. from a crashed invocation) and replaced.

```bash
_qcache="${TMPDIR:-/tmp}/.ai-sl-quota-${UID:-0}"
_qlock="${TMPDIR:-/tmp}/.ai-sl-quota-lock-${UID:-0}"
quota_part=""
_quota_cache_valid=0
_qnow=$(date +%s)
if [[ -f "$_qcache" ]]; then
  { IFS= read -r _qts && IFS= read -r quota_part; } < "$_qcache" 2>/dev/null
  if [[ "$_qts" =~ ^[0-9]+$ ]]; then
    _qage=$(( _qnow - _qts ))
    if (( _qage < 30 )); then
      _quota_cache_valid=1  # fresh — serve directly
    elif (( _qage < 300 )); then
      _quota_cache_valid=1  # stale — serve this cycle, refresh in background
      _need_refresh=1
      if [[ -f "$_qlock" ]]; then
        IFS= read -r _qlts < "$_qlock" 2>/dev/null
        [[ "$_qlts" =~ ^[0-9]+$ ]] && (( _qnow - _qlts <= 120 )) && _need_refresh=0
        (( _need_refresh )) && rm -f "$_qlock" 2>/dev/null
      fi
      if (( _need_refresh )); then
        printf '%d' "$_qnow" > "$_qlock" 2>/dev/null
        ( _qfresh=$(ai quota statusline-part 2>/dev/null)
          _qfresh="${_qfresh//$'\n'/ }"
          printf '%d\n%s\n' "$(date +%s)" "$_qfresh" > "$_qcache" 2>/dev/null
          rm -f "$_qlock" 2>/dev/null
        ) >/dev/null 2>&1 &
      fi
    fi
  fi
fi
if (( ! _quota_cache_valid )); then
  quota_part=$(ai quota statusline-part 2>/dev/null)
  quota_part="${quota_part//$'\n'/ }"
  printf '%d\n%s\n' "$(date +%s)" "$quota_part" > "$_qcache" 2>/dev/null
fi
```

The key invariant: **at most one slow `ai quota statusline-part` call is in flight at any time**,
and every concurrent render cycle returns immediately with the cached (possibly stale) value.

## Verification

**Round 1:**
- `pytest tests/test_quota_db.py::TestMigrateSnapshotColumns` — 3 tests confirm migration
- `pytest tests/test_quota.py::TestQuotaStatuslinePartLegacyDb` — 2 tests reproduce the legacy DB scenario and confirm non-empty output after fix

**Round 2:**
- `tests/hooks/test-statusline-quota-cache.sh` — 21/21 tests covering:
  - Fresh cache (no-call path)
  - Stale cache (serve stale + background refresh fires)
  - Very old cache (synchronous fetch)
  - No cache / first call (synchronous fetch)
  - Cache file format (timestamp + value)
  - Lock file prevents stampede (fresh lock → no second refresh)
  - Stale lock replaced (>120s lock → refresh fires)
  - Background refresh updates cache file and timestamp
  - Concurrent stale invocations (5 concurrent → only 1 background refresh)
  - Concurrent cold-start invocations (5 concurrent → at most 5 sync calls)
  - Lock file removed after background refresh completes

## Lessons Learned

1. **`_init_db()` must be called on every connection to the quota DB** — any direct
   `sqlite3.connect()` that skips it is a latent migration bug waiting for the next schema change.
2. **Bash empty-string caches are not cache misses** — use a boolean validity flag rather than
   checking `[[ -z "$cached_value" ]]`.
3. **New DB columns require an `ALTER TABLE` migration path**, not just updating `CREATE TABLE IF
   NOT EXISTS` — the `IF NOT EXISTS` clause only applies to table creation, not to columns within
   an existing table.
4. **A 30s TTL cache does not prevent thundering herd** — when the cache expires, all concurrent
   callers miss simultaneously. Stale-while-revalidate eliminates the thundering herd: callers
   always get a fast response (even if slightly stale), and exactly one background refresh runs.
5. **Lock files need timestamps** — a lock file without a timestamp can't be detected as stale after
   a crash. Always write the creation timestamp into the lock file.
