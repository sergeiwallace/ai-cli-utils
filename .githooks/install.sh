#!/usr/bin/env bash
set -uo pipefail

echo "=== Git hooks setup (pre-commit framework) ==="

if ! command -v pre-commit &>/dev/null; then
    echo "ERROR: 'pre-commit' not installed — hooks are NOT active." >&2
    echo "  Install with:  pipx install pre-commit   (or: uv tool install pre-commit)" >&2
    echo "  Then re-run:   bash .githooks/install.sh" >&2
    exit 1
fi

if [ ! -f ".pre-commit-config.yaml" ]; then
    echo "WARNING: No .pre-commit-config.yaml in this repo yet. Skipping."
    exit 0
fi

git config --local --unset-all core.hooksPath 2>/dev/null || true

if ! pre-commit install --overwrite --install-hooks --hook-type pre-commit --hook-type commit-msg --hook-type pre-push; then
    echo "ERROR: 'pre-commit install' failed -- hooks are NOT active." >&2
    echo "  Fix the error above and re-run: bash .githooks/install.sh" >&2
    exit 1
fi

echo ""
echo "Hooks active for pre-commit + commit-msg + pre-push stages."
echo "  - Skip specific hook:   SKIP=hook-id git commit"
echo "  - Update hook pins:     pre-commit autoupdate"
echo "  - Full-repo audit:      pre-commit run --all-files"
