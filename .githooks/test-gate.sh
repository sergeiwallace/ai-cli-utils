#!/usr/bin/env bash
# Test gate — runs at pre-push stage via pre-commit framework.
# Auto-detects tech stack from repo contents (Cargo.toml, vitest config, go.mod, Python sources).
# Ported from the consolidated pre-push.jinja (SW-796 T-01).
#
# Environment overrides:
#   SKIP_TESTS=1  — skip this gate entirely
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
ERRORS=0

if [ "${SKIP_TESTS:-0}" = "1" ]; then
    echo "[test-gate] SKIP_TESTS=1 — skipping test gate"
    exit 0
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
    # Resolve pytest: mise (.python-version) → .venv → system PATH
    PYTEST=""
    if [ -f "$REPO_ROOT/.python-version" ]; then
        PY_VER=$(cat "$REPO_ROOT/.python-version")
        MISE_PYTEST="$HOME/.local/share/mise/installs/python/$PY_VER/bin/pytest"
        [ -f "$MISE_PYTEST" ] && PYTEST="$MISE_PYTEST"
    fi
    [ -z "$PYTEST" ] && [ -f "$REPO_ROOT/.venv/bin/pytest" ] && PYTEST="$REPO_ROOT/.venv/bin/pytest"
    [ -z "$PYTEST" ] && command -v pytest &>/dev/null && PYTEST="pytest"

    if [ -n "$PYTEST" ]; then
        echo "[test-gate] Python detected → $PYTEST"
        # Capture exit code without tripping set -e
        "$PYTEST" "$REPO_ROOT" --tb=short -q && PYTEST_EXIT=0 || PYTEST_EXIT=$?
        if [ "$PYTEST_EXIT" -eq 5 ]; then
            echo "[test-gate] No tests collected — skipping Python gate"
        elif [ "$PYTEST_EXIT" -ne 0 ]; then
            echo "[test-gate] ERROR: Python tests failed (exit $PYTEST_EXIT)"
            ERRORS=1
        fi
    else
        echo "[test-gate] Python sources found but no pytest available — skipping"
    fi
fi

if [ "$ERRORS" -ne 0 ]; then
    echo "[test-gate] Push blocked. Fix failing tests or override with SKIP_TESTS=1 git push."
    exit 1
fi

exit 0
