#!/usr/bin/env bash
# Test gate — runs at pre-push stage via pre-commit framework.
# Auto-detects tech stack from repo contents (Cargo.toml, vitest config, go.mod, Python sources).
# Ported from the consolidated pre-push.jinja (SW-796 T-01).
#
# Environment overrides:
#   SKIP_TESTS=1  — skip this gate entirely
set -uo pipefail

# AI-CLI-70/AIH-70 root cause: git itself injects GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE
# (and related GIT_* vars) into every hook process it invokes. pre-commit strips these
# before its OWN internal git plumbing (see pre_commit/git.py's no_git_env(), tracking
# upstream pre-commit issue #300) but does NOT strip them before running this script —
# so without this, every subprocess we spawn (pytest, and any test that shells out to
# git inside an ephemeral tmp fixture repo) inherits env vars pointing at THIS worktree.
# A test's own git command there, unless it explicitly overrides these, silently
# retargets at the real repo instead of its own fixture — corrupting this worktree's
# index/working tree. Unsetting them here is safe: git falls back to cwd-based repo
# discovery, which resolves to the same worktree since we haven't changed directory.
for _v in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
    GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_CONFIG GIT_CONFIG_GLOBAL; do
    unset "$_v"
done
unset _v

REPO_ROOT="$(git rev-parse --show-toplevel)"
# In a linked worktree, --show-toplevel is the WORKTREE dir, which never carries
# a committed .venv. --git-common-dir always points at the main tree's .git (in
# the main tree itself, its dirname is the repo root too — same value either
# way), so anchor venv/PATH pytest resolution there instead (AIH-409). The
# ambient GIT_* scrub already happens above (AI-CLI-70) before subprocesses run.
MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
ERRORS=0

if [ "${SKIP_TESTS:-0}" = "1" ]; then
    echo "[test-gate] SKIP_TESTS=1 — skipping test gate"
    exit 0
fi

# Docs-only bypass: if every file changed since origin/main is non-code, skip tests.
# Covers .md, .yaml/.yml, .toml, .txt, .json, and anything under docs/ or .claude/.
_CHANGED=$(git diff --name-only "origin/main..HEAD" 2>/dev/null || git diff --name-only "HEAD~1..HEAD" 2>/dev/null || true)
if [ -n "$_CHANGED" ] && ! echo "$_CHANGED" | grep -qvE '(^docs/|^\.claude/|^\.githooks/|\.md$|\.yaml$|\.yml$|\.toml$|\.txt$|\.json$)'; then
    echo "[test-gate] Docs-only push — skipping test gate"
    exit 0
fi
unset _CHANGED

# HEAD-sentinel cache: if tests already passed on this HEAD, skip the re-run.
# Sentinel is written at end of this script after all tests pass; it's also
# written by running this script directly (e.g. `bash .githooks/test-gate.sh`
# after manual pytest). Cache lives in the git dir (not tracked, per-clone).
# NOTE: resolve the git dir via `git rev-parse --git-dir` — a literal ".git" is a
# FILE (a gitdir: pointer), not a directory, inside a git worktree, so a hardcoded
# ".git/…" path silently no-ops there and the cache never engages (the ai-cli
# `.worktrees/sw-N` sessions are all worktrees). --git-dir returns the worktree's
# own private git dir (…/.git/worktrees/<name>), a real writable directory.
GIT_DIR_PATH="$(git rev-parse --git-dir 2>/dev/null || echo ".git")"
SENTINEL="$GIT_DIR_PATH/pytest-last-pass"
if [ -f "$SENTINEL" ]; then
    CURRENT_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "")
    LAST_PASS=$(cat "$SENTINEL" 2>/dev/null || echo "")
    if [ -n "$CURRENT_HEAD" ] && [ "$CURRENT_HEAD" = "$LAST_PASS" ]; then
        echo "[test-gate] Tests already passed on this HEAD ($CURRENT_HEAD) — skipping"
        echo "[test-gate] (delete $SENTINEL or move HEAD to force a re-run)"
        exit 0
    fi
fi

# ── Rust ────────────────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/Cargo.toml" ]; then
    echo "[test-gate] Rust detected → cargo test --workspace"
    if ! cargo test --workspace 2>&1; then
        echo "[test-gate] ERROR: Rust tests failed"
        ERRORS=1
    fi
fi

# ── TypeScript (vitest, root) ───────────────────────────────────────────────
if [ -f "$REPO_ROOT/vitest.config.ts" ] || [ -f "$REPO_ROOT/vite.config.ts" ]; then
    echo "[test-gate] Vitest detected (root) → npx vitest run"
    if ! npx vitest run 2>&1; then
        echo "[test-gate] ERROR: TypeScript tests failed"
        ERRORS=1
    fi
fi

# ── TypeScript (vitest, web/ subdir — for python-typescript hybrid) ─────────
if [ -d "$REPO_ROOT/web" ] && [ -f "$REPO_ROOT/web/package.json" ]; then
    if [ -f "$REPO_ROOT/web/vitest.config.ts" ] || [ -f "$REPO_ROOT/web/vite.config.ts" ]; then
        echo "[test-gate] Vitest detected (web/) → npx vitest run in web/"
        if ! (cd "$REPO_ROOT/web" && npx vitest run) 2>&1; then
            echo "[test-gate] ERROR: Web TypeScript tests failed"
            ERRORS=1
        fi
    fi
fi

# ── Go ──────────────────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/go.mod" ]; then
    echo "[test-gate] Go detected → go test ./..."
    if ! go test ./... 2>&1; then
        echo "[test-gate] ERROR: Go tests failed"
        ERRORS=1
    fi
fi

# ── Python (pytest, mise-aware resolver) ────────────────────────────────────
FIND_DIRS=()
[ -d "$REPO_ROOT/src" ] && FIND_DIRS+=("$REPO_ROOT/src")
[ -d "$REPO_ROOT/tests" ] && FIND_DIRS+=("$REPO_ROOT/tests")
HAS_PYTHON=""
if [ ${#FIND_DIRS[@]} -gt 0 ]; then
    HAS_PYTHON=$(find "${FIND_DIRS[@]}" -name '*.py' -maxdepth 3 2>/dev/null | head -1)
fi

if [ -n "$HAS_PYTHON" ]; then
    # macOS: git hooks are exec'd via /bin/sh, and SIP strips DYLD_* on that exec, so an
    # ambient / .envrc DYLD_FALLBACK_LIBRARY_PATH does NOT reach the hook's pytest. Re-provide
    # the Homebrew lib path here so tests importing native-lib-backed deps (e.g. weasyprint →
    # libgobject/pango/cairo) resolve. No-op off macOS or without Homebrew.
    if [ "$(uname)" = "Darwin" ] && [ -d /opt/homebrew/lib ]; then
        export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
    fi
    # Resolve pytest: mise (.python-version) → main-tree .venv → PATH, but ONLY if
    # PATH's pytest resolves under the main tree. A bare `command -v pytest` would
    # silently accept whatever venv the invoking shell happens to have active —
    # in a linked worktree that can be a DIFFERENT repo's interpreter+conftest
    # (AIH-409); worse, if that foreign suite happens to pass, the gate records a
    # pass having tested nothing here. If nothing repo-owned resolves, fail loud.
    PYTEST=""
    if [ -f "$REPO_ROOT/.python-version" ]; then
        PY_VER=$(cat "$REPO_ROOT/.python-version")
        MISE_PYTEST="$HOME/.local/share/mise/installs/python/$PY_VER/bin/pytest"
        [ -f "$MISE_PYTEST" ] && PYTEST="$MISE_PYTEST"
    fi
    [ -z "$PYTEST" ] && [ -f "$MAIN_ROOT/.venv/bin/pytest" ] && PYTEST="$MAIN_ROOT/.venv/bin/pytest"
    PATH_PYTEST=""
    if [ -z "$PYTEST" ]; then
        PATH_PYTEST="$(command -v pytest 2>/dev/null || true)"
        case "$PATH_PYTEST" in
            "$MAIN_ROOT"/*) PYTEST="$PATH_PYTEST" ;;
        esac
    fi

    if [ -n "$PYTEST" ]; then
        echo "[test-gate] Python detected → $PYTEST"
        PYTEST_EXIT=""

        # Optional remote offload (CORE-40): run the Python suite on a box where the
        # test DB is LOCAL — e.g. ai-core's Postgres on Hetzner, ~14x faster than
        # crossing Tailscale per query (37s vs ~9min). Opt-in per-repo:
        #   git config testgate.remote "user@host:/abs/path/to/runner-checkout"
        # Unset = run locally (fleet default — byte-identical to before). The runner
        # path is a dedicated checkout on the box with its own venv + .envrc (DSN via
        # direnv/Doppler). We rsync the LOCAL working tree (so uncommitted changes are
        # gated too) and run pytest there. ANY remote failure (unreachable / rsync /
        # ssh) falls back to a local run — a flaky tunnel must never block a push.
        REMOTE="${TEST_GATE_REMOTE:-$(git -C "$REPO_ROOT" config testgate.remote 2>/dev/null || true)}"
        if [ -n "$REMOTE" ] && command -v rsync >/dev/null 2>&1; then
            RHOST="${REMOTE%%:*}"; RPATH="${REMOTE#*:}"
            if ssh -o ConnectTimeout=8 -o BatchMode=yes "$RHOST" true 2>/dev/null; then
                echo "[test-gate] Offloading Python suite → $RHOST:$RPATH (local DB there; rsync + remote pytest)"
                if rsync -az --delete \
                        --exclude '.git' --exclude '.venv/' --exclude '__pycache__/' \
                        --exclude '.pytest_cache/' --exclude 'node_modules/' --exclude '*.db' \
                        "$REPO_ROOT/" "$RHOST:$RPATH/"; then
                    # Remote run is serial (the box shares one test DB — no per-worker
                    # isolation, so xdist would deadlock on DROP SCHEMA). uv sync is a
                    # fast no-op when deps are unchanged.
                    ssh "$RHOST" "cd '$RPATH' && direnv allow . >/dev/null 2>&1; direnv exec . uv sync --all-extras --dev -q && direnv exec . .venv/bin/pytest . --tb=short -q" \
                        && PYTEST_EXIT=0 || PYTEST_EXIT=$?
                    echo "[test-gate] remote suite exit=$PYTEST_EXIT"
                else
                    echo "[test-gate] WARN: rsync to $RHOST failed — falling back to a local run"
                fi
            else
                echo "[test-gate] WARN: remote $RHOST unreachable — falling back to a local run"
            fi
        fi

        # Local run — the default, or the fallback when the remote path bailed.
        if [ -z "$PYTEST_EXIT" ]; then
            # Parallelize across cores when pytest-xdist is installed. Guarded so a
            # repo/env without xdist still runs serially instead of erroring on -n.
            XDIST_ARG=""
            if "$PYTEST" --help 2>/dev/null | grep -q -- "--numprocesses"; then
                XDIST_ARG="-n auto"
                echo "[test-gate] pytest-xdist detected → running parallel ($XDIST_ARG)"
            fi
            # Capture exit code without tripping set -e
            "$PYTEST" "$REPO_ROOT" --tb=short -q $XDIST_ARG && PYTEST_EXIT=0 || PYTEST_EXIT=$?
        fi

        if [ "$PYTEST_EXIT" -eq 5 ]; then
            echo "[test-gate] No tests collected — skipping Python gate"
        elif [ "$PYTEST_EXIT" -ne 0 ]; then
            echo "[test-gate] ERROR: Python tests failed (exit $PYTEST_EXIT)"
            ERRORS=1
        fi
    else
        echo "[test-gate] ERROR: no repo-owned pytest found for $REPO_ROOT. Looked for:"
        echo "[test-gate]   - mise: \$HOME/.local/share/mise/installs/python/<version>/bin/pytest (needs $REPO_ROOT/.python-version)"
        echo "[test-gate]   - venv: $MAIN_ROOT/.venv/bin/pytest"
        echo "[test-gate]   - PATH pytest under $MAIN_ROOT (found: ${PATH_PYTEST:-<none>})"
        ERRORS=1
    fi
fi

if [ "$ERRORS" -ne 0 ]; then
    echo "[test-gate] Push blocked. Fix failing tests or override with SKIP_TESTS=1 git push."
    exit 1
fi

# Tests passed — record this HEAD so subsequent pushes on the same SHA skip the
# re-run. Future commits bump HEAD and invalidate the cache automatically.
CURRENT_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "")
if [ -n "$CURRENT_HEAD" ] && [ -d "$GIT_DIR_PATH" ]; then
    echo "$CURRENT_HEAD" > "$SENTINEL"
fi

exit 0
