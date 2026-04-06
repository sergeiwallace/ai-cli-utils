#!/usr/bin/env bash
# Install git hooks and dev tools.
# Run once after cloning: bash .githooks/install.sh
set -euo pipefail

echo "=== Git Hooks Setup ==="

# Point git at the shared hooks directory
git config core.hooksPath .githooks
echo "[1/3] Git hooks path set to .githooks/"

# Install Node.js dev dependencies (markdownlint-cli2)
if command -v npm &> /dev/null; then
    npm install --silent
    echo "[2/3] Node.js tools installed (markdownlint-cli2)"
else
    echo "[2/3] SKIP: npm not found — install Node.js for markdown linting"
fi

echo "[3/3] Done!"
echo "  - Pre-commit: lints markdown"
echo "  - Pre-push: lints markdown, runs tests on changed files"
echo "  - Override: SKIP_TESTS=1 git push  (skip test gate)"
echo "  - Override: SKIP_LINT=1 git push   (skip lint checks)"
