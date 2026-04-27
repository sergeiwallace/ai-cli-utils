#!/usr/bin/env bash
# tests/hooks/test-statusline-quota-cache.sh
#
# Tests for the stale-while-revalidate quota cache in statusline-command.sh.
#
# Exercises the three cache zones (fresh / stale / very old), the lock-file
# stampede prevention, and the concurrent-invocation safety guarantee.
#
# Usage: bash tests/hooks/test-statusline-quota-cache.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."
SCRIPT="$REPO_ROOT/src/ai_cli/data/statusline-command.sh"
PASS=0
FAIL=0

# Real UID so cache file path matches what the statusline script uses.
REAL_UID=$(id -u)

# Minimal JSON input the statusline script can parse without errors.
FAKE_INPUT='{"model":"claude-sonnet","context_window":{"used_percentage":42,"total_input_tokens":1000,"total_output_tokens":200,"current_usage":{"cache_creation_input_tokens":0,"cache_read_input_tokens":0}},"workspace":{"project_dir":"/tmp/testproject"}}'

# ── Helpers ───────────────────────────────────────────────────────────────────

_assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS: $name"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: $name (expected: '$expected', got: '$actual')"
        FAIL=$(( FAIL + 1 ))
    fi
}

_assert_contains() {
    local name="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  PASS: $name"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: $name (expected '$needle' in: '$haystack')"
        FAIL=$(( FAIL + 1 ))
    fi
}

_assert_not_contains() {
    local name="$1" haystack="$2" needle="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        echo "  PASS: $name"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: $name (expected '$needle' NOT in: '$haystack')"
        FAIL=$(( FAIL + 1 ))
    fi
}

_assert_le() {
    local name="$1" expected="$2" actual="$3"
    if (( actual <= expected )); then
        echo "  PASS: $name ($actual <= $expected)"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: $name (expected <= $expected, got: $actual)"
        FAIL=$(( FAIL + 1 ))
    fi
}

# Write a test cache file with an explicit age (seconds in the past).
write_cache() {
    local tmpdir="$1" age_secs="$2" value="${3:-📊 42% W ✅ →5%}"
    local ts=$(( $(date +%s) - age_secs ))
    printf '%d\n%s' "$ts" "$value" > "$tmpdir/.ai-sl-quota-${REAL_UID}"
}

# Write a test lock file with an explicit age.
write_lock() {
    local tmpdir="$1" age_secs="${2:-0}"
    local ts=$(( $(date +%s) - age_secs ))
    printf '%d' "$ts" > "$tmpdir/.ai-sl-quota-lock-${REAL_UID}"
}

call_count() {
    local log="$1"
    [[ -f "$log" ]] && wc -l < "$log" | tr -d ' ' || echo "0"
}

# Run the statusline script with a fake 'ai' command in a clean temp environment.
run_script() {
    local tmpdir="$1"
    # Unset SHELLOPTS/BASHOPTS: the test script's set -euo pipefail propagates these
    # to child bash processes, which causes early exits inside the statusline script
    # (e.g., arithmetic (( )) returning 1 when false, or unset TMUX with set -u).
    TMPDIR="$tmpdir" PATH="$tmpdir/bin:$PATH" CALL_LOG="$tmpdir/ai_calls.log" \
        env -u SHELLOPTS -u BASHOPTS bash -c "echo '$FAKE_INPUT' | bash '$SCRIPT'" 2>/dev/null
}

# Install a fake 'ai' command in the given tmpdir/bin directory.
# The fake sleeps $sleep_ms (milliseconds) when called as 'quota statusline-part'
# and returns the given quota value.
install_fake_ai() {
    local tmpdir="$1" return_value="${2:-📊 FAKE}" sleep_ms="${3:-0}"
    mkdir -p "$tmpdir/bin"
    local sleep_secs
    sleep_secs=$(awk "BEGIN {printf \"%.2f\", $sleep_ms / 1000.0}")
    cat > "$tmpdir/bin/ai" <<FAKE
#!/usr/bin/env bash
if [[ "\$1 \$2" == "quota statusline-part" ]]; then
    echo "CALL" >> "\${CALL_LOG:-/dev/null}"
    [[ "$sleep_ms" -gt 0 ]] && sleep $sleep_secs
    echo "$return_value"
fi
FAKE
    chmod +x "$tmpdir/bin/ai"
}

# ── Tests ─────────────────────────────────────────────────────────────────────

echo "Running statusline quota cache tests..."
echo ""

# 1. Fresh cache (< 30s): no Python call, cached value shown
echo "Group: fresh cache"
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 10 "📊 CACHED"
install_fake_ai "$TMPDIR" "📊 NEW"
output=$(run_script "$TMPDIR")
_assert_contains "fresh cache: cached value shown" "$output" "CACHED"
_assert_not_contains "fresh cache: new value not shown" "$output" "NEW"
_assert_eq "fresh cache: no Python call" "0" "$(call_count "$TMPDIR/ai_calls.log")"
rm -rf "$TMPDIR"

# 2. Stale cache (60s): serve stale value, fire background refresh
echo ""
echo "Group: stale cache (serve-stale path)"
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 60 "📊 STALE"
install_fake_ai "$TMPDIR" "📊 FRESH" 100
output=$(run_script "$TMPDIR")
_assert_contains "stale cache: stale value returned immediately" "$output" "STALE"
_assert_not_contains "stale cache: fresh value not in sync path" "$output" "FRESH"
sleep 0.3  # let background refresh finish
_assert_eq "stale cache: exactly 1 background refresh call" "1" "$(call_count "$TMPDIR/ai_calls.log")"
wait 2>/dev/null || true
rm -rf "$TMPDIR"

# 3. Very old cache (> 300s): synchronous fetch, fresh value shown
echo ""
echo "Group: very old cache (sync fetch)"
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 400 "📊 VERY_OLD"
install_fake_ai "$TMPDIR" "📊 SYNC_FRESH"
output=$(run_script "$TMPDIR")
_assert_contains "very old cache: fresh value shown" "$output" "SYNC_FRESH"
_assert_not_contains "very old cache: stale value not shown" "$output" "VERY_OLD"
_assert_eq "very old cache: exactly 1 synchronous call" "1" "$(call_count "$TMPDIR/ai_calls.log")"
rm -rf "$TMPDIR"

# 4. No cache (first call): synchronous fetch
echo ""
echo "Group: no cache (first call)"
TMPDIR=$(mktemp -d)
install_fake_ai "$TMPDIR" "📊 FIRST"
output=$(run_script "$TMPDIR")
_assert_contains "no cache: fresh value shown" "$output" "FIRST"
_assert_eq "no cache: exactly 1 synchronous call" "1" "$(call_count "$TMPDIR/ai_calls.log")"
rm -rf "$TMPDIR"

# 5. After sync fetch: cache file written with correct format
echo ""
echo "Group: cache file format"
TMPDIR=$(mktemp -d)
install_fake_ai "$TMPDIR" "📊 WRITTEN"
run_script "$TMPDIR" >/dev/null
cache_file="$TMPDIR/.ai-sl-quota-${REAL_UID}"
if [[ -f "$cache_file" ]]; then
    { IFS= read -r ts && IFS= read -r val; } < "$cache_file" || true
    if [[ "$ts" =~ ^[0-9]+$ ]]; then
        echo "  PASS: cache file has valid Unix timestamp on line 1"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: cache file has invalid timestamp: '$ts'"
        FAIL=$(( FAIL + 1 ))
    fi
    _assert_contains "cache file has quota value on line 2" "$val" "WRITTEN"
    # Timestamp should be close to now
    now=$(date +%s)
    age=$(( now - ts ))
    if (( age < 5 )); then
        echo "  PASS: cache timestamp is recent ($age s ago)"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: cache timestamp is not recent ($age s ago)"
        FAIL=$(( FAIL + 1 ))
    fi
else
    echo "  FAIL: cache file not created after sync fetch"
    FAIL=$(( FAIL + 1 ))
fi
rm -rf "$TMPDIR"

# 6. Lock file present (fresh, < 120s): no additional background refresh
echo ""
echo "Group: lock file / stampede prevention"
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 60 "📊 STALE"
write_lock "$TMPDIR" 30  # lock is 30s old — fresh
install_fake_ai "$TMPDIR" "📊 NEW"
run_script "$TMPDIR" >/dev/null 2>&1
sleep 0.1
_assert_eq "fresh lock present: no background refresh" "0" "$(call_count "$TMPDIR/ai_calls.log")"
rm -rf "$TMPDIR"

# 7. Lock file present but stale (> 120s): replaced, background refresh fires
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 90 "📊 STALE"
write_lock "$TMPDIR" 130  # lock is 130s old — stale
install_fake_ai "$TMPDIR" "📊 REFRESHED" 50
run_script "$TMPDIR" >/dev/null 2>&1
sleep 0.3
_assert_eq "stale lock: background refresh fires" "1" "$(call_count "$TMPDIR/ai_calls.log")"
wait 2>/dev/null || true
rm -rf "$TMPDIR"

# 8. After background refresh: cache is updated with fresh value
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 60 "📊 STALE"
install_fake_ai "$TMPDIR" "📊 BACKGROUND_RESULT" 50
run_script "$TMPDIR" >/dev/null 2>&1
# Wait for background to complete (script startup + fake-ai 50ms + write overhead)
sleep 1.5
wait 2>/dev/null || true
cache_file="$TMPDIR/.ai-sl-quota-${REAL_UID}"
if [[ -f "$cache_file" ]]; then
    { IFS= read -r ts && IFS= read -r val; } < "$cache_file" || true
    _assert_contains "background refresh updates cache file" "$val" "BACKGROUND_RESULT"
    now=$(date +%s)
    age=$(( now - ts ))
    if (( age < 5 )); then
        echo "  PASS: cache timestamp refreshed to recent ($age s ago)"
        PASS=$(( PASS + 1 ))
    else
        echo "  FAIL: cache timestamp not updated ($age s ago)"
        FAIL=$(( FAIL + 1 ))
    fi
fi
rm -rf "$TMPDIR"

# 9. Concurrent invocations with stale cache: only 1 background refresh fires
echo ""
echo "Group: concurrent invocation safety"
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 60 "📊 STALE"
install_fake_ai "$TMPDIR" "📊 CONCURRENT" 50
# Fire 5 concurrent script invocations
for _ in $(seq 1 5); do
    run_script "$TMPDIR" >/dev/null &
done
wait
sleep 0.3
count=$(call_count "$TMPDIR/ai_calls.log")
# Lock file prevents stampede: only 1 background refresh should fire.
_assert_eq "5 concurrent stale invocations: only 1 background refresh" "1" "$count"
wait 2>/dev/null || true
rm -rf "$TMPDIR"

# 10. Concurrent invocations with no cache: all see cache miss simultaneously
# At most all 5 fire synchronously (unavoidable cold start race), but the second
# invocation to complete writes the cache, reducing the window for subsequent ones.
TMPDIR=$(mktemp -d)
install_fake_ai "$TMPDIR" "📊 COLD" 50
for _ in $(seq 1 5); do
    run_script "$TMPDIR" >/dev/null &
done
wait
count=$(call_count "$TMPDIR/ai_calls.log")
_assert_le "5 concurrent cold-start invocations: at most 5 sync calls" 5 "$count"
rm -rf "$TMPDIR"

# 11. Lock file removed after background refresh completes
TMPDIR=$(mktemp -d)
write_cache "$TMPDIR" 60 "📊 STALE"
install_fake_ai "$TMPDIR" "📊 DONE" 50
run_script "$TMPDIR" >/dev/null 2>&1
sleep 1.5
wait 2>/dev/null || true
lock_file="$TMPDIR/.ai-sl-quota-lock-${REAL_UID}"
if [[ ! -f "$lock_file" ]]; then
    echo "  PASS: lock file removed after background refresh completes"
    PASS=$(( PASS + 1 ))
else
    echo "  FAIL: lock file not removed after background refresh"
    FAIL=$(( FAIL + 1 ))
fi
rm -rf "$TMPDIR"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
