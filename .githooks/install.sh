#!/usr/bin/env bash
# Install git hooks via the pre-commit framework (SW-796 T-01).
# Idempotent — safe to run multiple times.
#
# Normally auto-invoked by copier `_tasks` on copy + update, so manual
# invocation should only be needed in clones that haven't run copier or
# machines where `pre-commit` wasn't installed at copier-time.
#
# Graceful-degradation: exits 0 with a warning when pre-commit or the
# config file isn't available yet, so `setup.sh` doesn't abort on fresh
# scaffolds where the user hasn't installed pre-commit yet.
set -uo pipefail

echo "=== Git hooks setup (pre-commit framework) ==="

if ! command -v pre-commit &>/dev/null; then
    echo "WARNING: 'pre-commit' not installed."
    echo "  Install with:  pipx install pre-commit   (or: uv tool install pre-commit)"
    echo "  Then re-run:   bash .githooks/install.sh"
    echo "  Skipping hook installation for now."
    exit 0
fi

if [ ! -f ".pre-commit-config.yaml" ]; then
    echo "WARNING: No .pre-commit-config.yaml in this repo yet. Skipping."
    exit 0
fi

# Unset any legacy core.hooksPath=.githooks from prior install.sh versions —
# pre-commit install refuses to run if core.hooksPath is set.
git config --local --unset-all core.hooksPath 2>/dev/null || true

pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push

echo ""
echo "Hooks active for pre-commit + pre-push stages."
echo "  - Skip specific hook:   SKIP=hook-id git commit"
echo "  - Skip test gate:       SKIP_TESTS=1 git push"
echo "  - Update hook pins:     pre-commit autoupdate"
echo "  - Full-repo audit:      pre-commit run --all-files"
