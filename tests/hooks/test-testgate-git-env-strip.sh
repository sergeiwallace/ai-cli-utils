#!/usr/bin/env bash
# tests/hooks/test-testgate-git-env-strip.sh
#
# Regression test for AI-CLI-70 Round 3 (docs/bugs/worktree-index-corruption.md): git itself
# injects GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE (and related GIT_* vars) into every hook process
# it invokes. pre-commit strips these before its OWN internal git plumbing but not before
# running a hook's configured entry command -- so without stripping them ourselves at the top
# of test-gate.sh, every subprocess it spawns (pytest, and any test shelling a real `git` call
# inside an ephemeral tmp fixture repo) inherits the real worktree's git-targeting env raw, and
# can silently retarget the real repo instead of its own fixture.
#
# This test proves the leak is closed: it simulates the leaked env exactly as pre-commit would
# raw-inherit and pass it through from git's own hook invocation, shims `git` on PATH to capture
# what env vars the FIRST git subprocess test-gate.sh spawns actually sees, and asserts none of
# the leaked vars survive to it.
#
# Usage: bash tests/hooks/test-testgate-git-env-strip.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."
HOOK="$REPO_ROOT/.githooks/test-gate.sh"
REAL_GIT="$(command -v git)"
PASS=0
FAIL=0

# Unset git environment variables inherited from THIS outer context -- same hygiene the
# existing corrupt-index-guard test applies, and exactly the class of leak under test.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
    GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_CONFIG GIT_CONFIG_GLOBAL 2>/dev/null || true

setup_repo() {
    local repo
    repo=$(mktemp -d)
    "$REAL_GIT" -C "$repo" init -q
    "$REAL_GIT" -C "$repo" config user.email "test@example.com"
    "$REAL_GIT" -C "$repo" config user.name "Test"
    touch "$repo/init"
    "$REAL_GIT" -C "$repo" add init
    "$REAL_GIT" -C "$repo" commit -q -m "chore: init"
    echo "$repo"
}

teardown_paths() {
    rm -rf "$@" 2>/dev/null || true
}

# Sets up a fakebin/ dir with a `git` shim that records the GIT_* env it sees on its FIRST
# invocation only, then execs the real git so the script under test still functions normally.
setup_git_shim() {
    local fakebin capture
    fakebin=$(mktemp -d)
    capture="$fakebin/captured-env"
    # Match only the git-targeting vars the fix strips (mirrors test-gate.sh's own list) --
    # NOT a blanket ^GIT_ prefix, which would also catch unrelated, harmless vars like
    # GIT_EDITOR/GIT_PAGER that may legitimately be set in the ambient environment.
    cat > "$fakebin/git" <<SHIM
#!/usr/bin/env bash
if [ ! -e "$capture" ]; then
    env | grep -E '^GIT_(DIR|WORK_TREE|INDEX_FILE|OBJECT_DIRECTORY|COMMON_DIR|ALTERNATE_OBJECT_DIRECTORIES|PREFIX|CONFIG|CONFIG_GLOBAL)=' > "$capture" || true
fi
exec "$REAL_GIT" "\$@"
SHIM
    chmod +x "$fakebin/git"
    echo "$fakebin"
}

echo "Running test-gate.sh git-env-strip regression tests (AI-CLI-70 Round 3)..."
echo ""

# 1. Leaked GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_OBJECT_DIRECTORY (simulating what git
#    injects into every hook process) must not survive to the first git subprocess test-gate.sh
#    spawns -- otherwise a test that shells out to git in an ephemeral fixture repo (this
#    repo's own suite does exactly that) could silently retarget the real worktree.
echo "Group: leaked hook-invocation env vars are stripped"
REPO=$(setup_repo)
BOGUS=$(setup_repo)
FAKEBIN=$(setup_git_shim)
CAPTURE="$FAKEBIN/captured-env"

(
    cd "$REPO"
    PATH="$FAKEBIN:$PATH" \
    GIT_DIR="$BOGUS/.git" \
    GIT_WORK_TREE="$BOGUS" \
    GIT_INDEX_FILE="$BOGUS/.git/index" \
    GIT_OBJECT_DIRECTORY="$BOGUS/.git/objects" \
    SKIP_TESTS=1 \
    bash "$HOOK" >/dev/null 2>&1
) || true

if [ -f "$CAPTURE" ] && [ -z "$(cat "$CAPTURE")" ]; then
    echo "  PASS: no GIT_* vars visible to the first git subprocess test-gate.sh spawns"
    PASS=$((PASS + 1))
elif [ -f "$CAPTURE" ]; then
    echo "  FAIL: leaked GIT_* vars survived into test-gate.sh's own git subprocess:"
    sed 's/^/    /' "$CAPTURE"
    FAIL=$((FAIL + 1))
else
    echo "  FAIL: capture file missing -- the git shim never ran (test-gate.sh didn't spawn git?)"
    FAIL=$((FAIL + 1))
fi

teardown_paths "$REPO" "$BOGUS" "$FAKEBIN"

# 2. Same scenario, but confirm test-gate.sh's own REPO_ROOT-dependent behavior stays correct
#    under the leaked env -- it should resolve and operate against the fixture repo it's
#    actually running in, not silently fail or hang trying to reach the (nonexistent) bogus one.
echo ""
echo "Group: script still completes normally despite the leaked env"
REPO=$(setup_repo)
BOGUS_PATH="/nonexistent/$(mktemp -u)"

exit_code=0
(
    cd "$REPO"
    GIT_DIR="$BOGUS_PATH/.git" \
    GIT_WORK_TREE="$BOGUS_PATH" \
    SKIP_TESTS=1 \
    bash "$HOOK" >/dev/null 2>&1
) || exit_code=$?

if [ "$exit_code" -eq 0 ]; then
    echo "  PASS: test-gate.sh exits 0 (SKIP_TESTS path) even with GIT_DIR pointing nowhere"
    PASS=$((PASS + 1))
else
    echo "  FAIL: test-gate.sh exited $exit_code -- leaked GIT_DIR pointing at a nonexistent path broke it"
    FAIL=$((FAIL + 1))
fi

teardown_paths "$REPO"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
