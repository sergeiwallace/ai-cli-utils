#!/usr/bin/env bash
# Install git hooks via the pre-commit framework (SW-796 T-01).
# Idempotent — safe to run multiple times.
#
# Normally auto-invoked by copier `_tasks` on copy + update, so manual
# invocation should only be needed in clones that haven't run copier or
# machines where `pre-commit` wasn't installed at copier-time.
#
# Fails non-zero (AIH-393) when pre-commit or the config file isn't
# available: a bootstrap that cannot install hooks must not be allowed to
# silently claim success, matching Tier-B's loud-failure contract in
# ai-harness's docs/designs/aih-393-shared-hook-distribution-model.md.
# setup.sh.jinja's hook-install step handles this exit non-zero itself
# (warns and continues the rest of the scaffold) so a fresh scaffold
# without pre-commit yet installed is still usable — this script's own
# exit code stays truthful either way.
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

# Unset any legacy core.hooksPath=.githooks from prior install.sh versions —
# pre-commit install refuses to run if core.hooksPath is set.
git config --local --unset-all core.hooksPath 2>/dev/null || true

pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push

echo ""
echo "Hooks active for pre-commit + pre-push stages."
echo "  - Skip specific hook:   SKIP=hook-id git commit"
echo "  - Update hook pins:     pre-commit autoupdate"
echo "  - Full-repo audit:      pre-commit run --all-files"
# Deliberately does NOT name the test-gate bypass var here (AIH-469, matching
# AIH-452 AC-5's precedent in hooks/test-gate.sh): SKIP_TESTS=1 is FORBIDDEN by
# fleet rules outside repo bootstrap, and this banner prints unconditionally on
# every successful install — advertising it here teaches the escape hatch to
# every user, not just the ones who legitimately need it. SKIP=hook-id above is
# pre-commit's own standard single-hook mechanism, not fleet-forbidden.
