#!/usr/bin/env bash
# tests/hooks/test-commit-msg.sh
#
# Tests for .githooks/commit-msg
#
# Runs the hook in an isolated temp git repo so no real repo state is affected.
# Usage: bash tests/hooks/test-commit-msg.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."
HOOK="$REPO_ROOT/.githooks/commit-msg"
PASS=0
FAIL=0

# Unset git environment variables inherited from hook context (e.g. when this
# script is invoked from inside a pre-push hook). Without this, git commands
# in the test's isolated temp repos would operate on the parent repo's index.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR

# ── Helpers ───────────────────────────────────────────────────────────────────

setup_repo() {
    local repo
    repo=$(mktemp -d)
    git -C "$repo" init -q
    git -C "$repo" config user.email "test@example.com"
    git -C "$repo" config user.name "Test"
    # Seed an initial commit so HEAD exists
    touch "$repo/init"
    git -C "$repo" add init
    git -C "$repo" commit -q -m "chore: init"
    echo "$repo"
}

teardown_repo() {
    rm -rf "$1"
}

run_hook() {
    local repo="$1"
    local msg="$2"
    local msg_file
    # Use system mktemp (TMPDIR is unset above so this uses the real temp dir)
    msg_file=$(command mktemp)
    echo "$msg" > "$msg_file"
    # Run hook with cwd = repo; env vars already unset at script top
    (cd "$repo" && bash "$HOOK" "$msg_file")
    local exit_code=$?
    rm -f "$msg_file"
    return $exit_code
}

assert_pass() {
    local name="$1"
    if run_hook "$2" "$3" 2>/dev/null; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name (expected exit 0, got non-zero)"
        FAIL=$((FAIL + 1))
    fi
}

assert_fail() {
    local name="$1"
    if ! run_hook "$2" "$3" 2>/dev/null; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name (expected non-zero, got exit 0)"
        FAIL=$((FAIL + 1))
    fi
}

stage_file() {
    local repo="$1"
    local filename="$2"
    local content="${3:-dummy content}"
    mkdir -p "$(dirname "$repo/$filename")"
    echo "$content" > "$repo/$filename"
    git -C "$repo" add "$filename"
}

# Run hook with SKIP_CHANGELOG=1 bypass using a temp file for the message
run_hook_bypass() {
    local repo="$1"
    local msg="$2"
    local msg_file
    msg_file=$(command mktemp)
    echo "$msg" > "$msg_file"
    (cd "$repo" && SKIP_CHANGELOG=1 bash "$HOOK" "$msg_file")
    local exit_code=$?
    rm -f "$msg_file"
    return $exit_code
}

# ── Tests ─────────────────────────────────────────────────────────────────────

echo "Running commit-msg hook tests..."
echo ""

# 1. feat: without CHANGELOG.md staged → should fail
echo "Group: feat: commits"
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
assert_fail "feat: commit missing CHANGELOG.md is blocked" "$REPO" "feat: add new feature"
teardown_repo "$REPO"

# 2. feat: with CHANGELOG.md staged → should pass
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
stage_file "$REPO" "CHANGELOG.md" "## [Unreleased]\n\n### Added\n- new feature"
assert_pass "feat: commit with CHANGELOG.md staged passes" "$REPO" "feat: add new feature"
teardown_repo "$REPO"

# 3. feat(scope): with CHANGELOG.md staged → should pass
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
stage_file "$REPO" "CHANGELOG.md" "## [Unreleased]"
assert_pass "feat(scope): commit with CHANGELOG.md staged passes" "$REPO" "feat(gemini): add new feature"
teardown_repo "$REPO"

# 4. feat(scope): without CHANGELOG.md → should fail
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
assert_fail "feat(scope): commit missing CHANGELOG.md is blocked" "$REPO" "feat(gemini): add new feature"
teardown_repo "$REPO"

# 5. fix: without CHANGELOG.md → should pass (only feat: is enforced)
echo ""
echo "Group: non-feat commits (should not be blocked)"
REPO=$(setup_repo)
stage_file "$REPO" "src/fix.py"
assert_pass "fix: commit without CHANGELOG.md passes" "$REPO" "fix: correct null check"
teardown_repo "$REPO"

# 6. chore: without CHANGELOG.md → should pass
REPO=$(setup_repo)
stage_file "$REPO" "src/tool.py"
assert_pass "chore: commit without CHANGELOG.md passes" "$REPO" "chore: update dependencies"
teardown_repo "$REPO"

# 7. docs: without CHANGELOG.md → should pass
REPO=$(setup_repo)
stage_file "$REPO" "README.md"
assert_pass "docs: commit without CHANGELOG.md passes" "$REPO" "docs: update README"
teardown_repo "$REPO"

# 8. SKIP_CHANGELOG=1 bypasses even feat: without CHANGELOG.md
echo ""
echo "Group: bypass"
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
if run_hook_bypass "$REPO" "feat: bypassed" 2>/dev/null; then
    echo "  PASS: SKIP_CHANGELOG=1 bypasses enforcement"
    PASS=$((PASS + 1))
else
    echo "  FAIL: SKIP_CHANGELOG=1 should bypass enforcement"
    FAIL=$((FAIL + 1))
fi
teardown_repo "$REPO"

# 9. Multiline message — feat: on first line without CHANGELOG.md → fail
echo ""
echo "Group: multiline messages"
REPO=$(setup_repo)
stage_file "$REPO" "src/feature.py"
MSG="feat: add new feature

This is the longer body of the commit message."
assert_fail "multiline feat: commit without CHANGELOG.md is blocked" "$REPO" "$MSG"
teardown_repo "$REPO"

# 10. Message with feat: NOT on first line → should pass (body text, not conventional commit prefix)
REPO=$(setup_repo)
stage_file "$REPO" "src/refactor.py"
MSG="refactor: extract helper

Internally uses feat: pattern from the design doc but this is a refactor."
assert_pass "feat: in commit body but not prefix passes" "$REPO" "$MSG"
teardown_repo "$REPO"

# 11. Body line starting with feat(scope): → should pass (only first line checked)
REPO=$(setup_repo)
stage_file "$REPO" "src/chore.py"
MSG="chore: add commit-msg hook

Blocks feat: commits (including
feat(scope):) unless CHANGELOG.md is staged."
assert_pass "feat(scope): at start of body line does not trigger hook" "$REPO" "$MSG"
teardown_repo "$REPO"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
