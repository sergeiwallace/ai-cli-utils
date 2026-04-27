#!/usr/bin/env bash
# tests/hooks/test-corrupt-index-guard.sh
#
# Tests for .githooks/corrupt-index-guard.sh
#
# Simulates index corruption by staging file deletions in an isolated temp repo.
# Uses git rm --cached to produce staged-deletion entries that the hook counts.
# Usage: bash tests/hooks/test-corrupt-index-guard.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."
HOOK="$REPO_ROOT/.githooks/corrupt-index-guard.sh"
PASS=0
FAIL=0

# Unset git environment variables inherited from outer context (worktree, hook, etc.)
# Without this, git commands in isolated temp repos would operate on the outer index.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR

# ── Helpers ───────────────────────────────────────────────────────────────────

setup_repo() {
    local repo
    repo=$(mktemp -d)
    git -C "$repo" init -q
    git -C "$repo" config user.email "test@example.com"
    git -C "$repo" config user.name "Test"
    touch "$repo/init"
    git -C "$repo" add init
    git -C "$repo" commit -q -m "chore: init"
    echo "$repo"
}

teardown_repo() {
    rm -rf "$1"
}

# Stage N file deletions in the repo (creates N files in a commit, then stages
# them as deleted so git diff --cached --diff-filter=D reports N entries).
stage_deletions() {
    local repo="$1"
    local count="$2"
    local prefix="${3:-del_file}"

    # Create and commit the files first so they appear in HEAD
    for i in $(seq 1 "$count"); do
        touch "$repo/${prefix}_${i}"
        git -C "$repo" add "${prefix}_${i}"
    done
    git -C "$repo" commit -q -m "chore: add files for deletion test"

    # Stage the deletions (remove from index only — leaves working tree intact
    # to match the real corruption scenario where files exist on disk)
    for i in $(seq 1 "$count"); do
        git -C "$repo" rm -q --cached "${prefix}_${i}"
    done
}

run_hook() {
    local repo="$1"
    shift
    (cd "$repo" && "$@" bash "$HOOK")
    return $?
}

assert_pass() {
    local name="$1"
    local repo="$2"
    shift 2
    if (cd "$repo" && "$@" bash "$HOOK") 2>/dev/null; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name (expected exit 0, got non-zero)"
        FAIL=$((FAIL + 1))
    fi
}

assert_fail() {
    local name="$1"
    local repo="$2"
    shift 2
    if ! (cd "$repo" && "$@" bash "$HOOK") 2>/dev/null; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name (expected non-zero, got exit 0)"
        FAIL=$((FAIL + 1))
    fi
}

# ── Tests ─────────────────────────────────────────────────────────────────────

echo "Running corrupt-index-guard hook tests..."
echo ""

# 1. Clean index — no staged deletions → should pass
echo "Group: clean index"
REPO=$(setup_repo)
assert_pass "clean index passes" "$REPO"
teardown_repo "$REPO"

# 2. A few staged changes that are not deletions (additions) → should pass
REPO=$(setup_repo)
echo "content" > "$REPO/newfile.txt"
git -C "$REPO" add newfile.txt
assert_pass "staged additions (not deletions) passes" "$REPO"
teardown_repo "$REPO"

# 3. Below threshold: 49 staged deletions (default threshold=50) → should pass
echo ""
echo "Group: threshold boundary (default threshold=50)"
REPO=$(setup_repo)
stage_deletions "$REPO" 49
assert_pass "49 staged deletions passes (below threshold)" "$REPO"
teardown_repo "$REPO"

# 4. At threshold: exactly 50 staged deletions → should pass (threshold is >50, not >=50)
REPO=$(setup_repo)
stage_deletions "$REPO" 50
assert_pass "50 staged deletions passes (at threshold, not above)" "$REPO"
teardown_repo "$REPO"

# 5. Above threshold: 51 staged deletions → should fail
REPO=$(setup_repo)
stage_deletions "$REPO" 51
assert_fail "51 staged deletions blocked (above threshold)" "$REPO"
teardown_repo "$REPO"

# 6. Well above threshold: 100 staged deletions → should fail
REPO=$(setup_repo)
stage_deletions "$REPO" 100
assert_fail "100 staged deletions blocked (well above threshold)" "$REPO"
teardown_repo "$REPO"

# 7. Error message content — should mention recovery commands
echo ""
echo "Group: error message content"
REPO=$(setup_repo)
stage_deletions "$REPO" 60
hook_output=$( (cd "$REPO" && bash "$HOOK") 2>&1 || true)
if echo "$hook_output" | grep -q "git read-tree HEAD"; then
    echo "  PASS: error message contains recovery command (git read-tree HEAD)"
    PASS=$((PASS + 1))
else
    echo "  FAIL: error message missing recovery command 'git read-tree HEAD'"
    FAIL=$((FAIL + 1))
fi
if echo "$hook_output" | grep -q "corrupt"; then
    echo "  PASS: error message contains 'corrupt'"
    PASS=$((PASS + 1))
else
    echo "  FAIL: error message missing 'corrupt'"
    FAIL=$((FAIL + 1))
fi
teardown_repo "$REPO"

# 8. SKIP_INDEX_GUARD=1 bypasses even a corrupt index
echo ""
echo "Group: bypass"
REPO=$(setup_repo)
stage_deletions "$REPO" 100
assert_pass "SKIP_INDEX_GUARD=1 bypasses corrupt index check" "$REPO" env SKIP_INDEX_GUARD=1
teardown_repo "$REPO"

# 9. SKIP_INDEX_GUARD=0 does NOT bypass (explicit zero)
REPO=$(setup_repo)
stage_deletions "$REPO" 100
assert_fail "SKIP_INDEX_GUARD=0 does not bypass" "$REPO" env SKIP_INDEX_GUARD=0
teardown_repo "$REPO"

# 10. Custom threshold via CORRUPT_INDEX_THRESHOLD — lower threshold
echo ""
echo "Group: custom threshold (CORRUPT_INDEX_THRESHOLD)"
REPO=$(setup_repo)
stage_deletions "$REPO" 6
assert_fail "6 deletions blocked when CORRUPT_INDEX_THRESHOLD=5" "$REPO" env CORRUPT_INDEX_THRESHOLD=5
teardown_repo "$REPO"

# 11. Custom threshold — at custom threshold passes
REPO=$(setup_repo)
stage_deletions "$REPO" 5
assert_pass "5 deletions passes when CORRUPT_INDEX_THRESHOLD=5 (at boundary, not above)" "$REPO" env CORRUPT_INDEX_THRESHOLD=5
teardown_repo "$REPO"

# 12. Custom threshold — higher than default, large deletion count passes
REPO=$(setup_repo)
stage_deletions "$REPO" 80
assert_pass "80 deletions passes when CORRUPT_INDEX_THRESHOLD=100 (higher threshold)" "$REPO" env CORRUPT_INDEX_THRESHOLD=100
teardown_repo "$REPO"

# 13. Custom threshold combined with SKIP — bypass overrides custom threshold
REPO=$(setup_repo)
stage_deletions "$REPO" 200
assert_pass "SKIP_INDEX_GUARD=1 overrides custom threshold" "$REPO" env CORRUPT_INDEX_THRESHOLD=10 SKIP_INDEX_GUARD=1
teardown_repo "$REPO"

# 14. Mixed staged changes — deletions + additions, above threshold
echo ""
echo "Group: mixed staged state"
REPO=$(setup_repo)
stage_deletions "$REPO" 55
# Also stage some new files (additions should not count toward threshold)
for i in $(seq 1 20); do
    echo "new" > "$REPO/addition_$i"
    git -C "$REPO" add "addition_$i"
done
assert_fail "55 deletions + 20 additions blocked (only deletions counted)" "$REPO"
teardown_repo "$REPO"

# 15. Mixed staged changes — additions only well above 50, no deletions → passes
REPO=$(setup_repo)
for i in $(seq 1 60); do
    echo "new" > "$REPO/addition_$i"
    git -C "$REPO" add "addition_$i"
done
assert_pass "60 staged additions (no deletions) passes" "$REPO"
teardown_repo "$REPO"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
