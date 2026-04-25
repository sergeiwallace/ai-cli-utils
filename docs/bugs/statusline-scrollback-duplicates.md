---
title: "AI-CLI-56: Duplicate prompt boxes / statusline in scrollback buffer"
category: bugs
tags: [bug, statusline, quota, scrollback]
status: resolved
severity: P0
task: AI-CLI-56
fixed_by: "AI-CLI-55 regression — fixed in this document's tracking session"
---

# AI-CLI-56: Duplicate Prompt Boxes / Statusline in Scrollback Buffer

**Status:** resolved

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

## Fix

### Python (`quota_db.py` + `quota.py`)

Added `_migrate_snapshot_columns()` at the end of `_init_db()`. It reads `PRAGMA table_info`
and issues `ALTER TABLE quota_snapshots ADD COLUMN weekly_sonnet_pct REAL` (and `extra_pct REAL`)
when the column is absent — safe to call repeatedly (no-op if columns exist).

In `quota_statusline_part()`, changed `sqlite3.connect()` to also call `_init_db(conn)` on the
connection before executing any query. This ensures the migration runs on every statusline
evaluation, even for the direct-connect path that bypasses `_get_conn()`.

### Bash (`statusline-command.sh`)

Replaced the `if [[ -z "$quota_part" ]]; then` pattern with an explicit `_quota_cache_valid`
flag that tracks whether the timestamp check passed, independent of the cached value:

```bash
_quota_cache_valid=0
if [[ -f "$_qcache" ]]; then
  { IFS= read -r _qts && IFS= read -r quota_part; } < "$_qcache" 2>/dev/null
  [[ "$_qts" =~ ^[0-9]+$ ]] && (( $(date +%s) - _qts < 30 )) && _quota_cache_valid=1 || quota_part=""
fi
if (( ! _quota_cache_valid )); then
  quota_part=$(ai quota statusline-part 2>/dev/null)
  quota_part="${quota_part//$'\n'/ }"
  printf '%d\n%s' "$(date +%s)" "$quota_part" > "$_qcache" 2>/dev/null
fi
```

Even if `quota_statusline_part()` returns empty output (e.g. a future schema mismatch or
transient error), the cache is honored for 30 seconds and the slow call does not repeat.

## Verification

- `pytest tests/test_quota_db.py::TestMigrateSnapshotColumns` — 3 tests confirm migration
- `pytest tests/test_quota.py::TestQuotaStatuslinePartLegacyDb` — 2 tests reproduce the legacy DB scenario and confirm non-empty output after fix
- Full suite: 1694 passing, 2 skipped (CI green)

## Lessons Learned

1. **`_init_db()` must be called on every connection to the quota DB** — any direct
   `sqlite3.connect()` that skips it is a latent migration bug waiting for the next schema change.
2. **Bash empty-string caches are not cache misses** — use a boolean validity flag rather than
   checking `[[ -z "$cached_value" ]]`.
3. **New DB columns require an `ALTER TABLE` migration path**, not just updating `CREATE TABLE IF
   NOT EXISTS` — the `IF NOT EXISTS` clause only applies to table creation, not to columns within
   an existing table.
