#!/usr/bin/env bash
# Install git hooks and dev tools.
# Run once after cloning: bash .githooks/install.sh
set -euo pipefail

echo "=== Git Hooks Setup ==="

# Point git at the shared hooks directory
git config core.hooksPath .githooks
echo "[1/4] Git hooks path set to .githooks/"

# Make hooks executable (git stores 100644; this ensures they run)
chmod +x .githooks/install.sh .githooks/pre-commit .githooks/pre-push 2>/dev/null || true
git update-index --chmod=+x .githooks/install.sh .githooks/pre-commit .githooks/pre-push 2>/dev/null || true
echo "[2/4] Hook files marked executable"

# Install Node.js dev dependencies (markdownlint-cli2)
if command -v npm &> /dev/null; then
    npm install --silent
    echo "[3/4] Node.js tools installed (markdownlint-cli2)"
else
    echo "[3/4] SKIP: npm not found — install Node.js for markdown linting"
fi

echo "[4/4] Done!"
echo "  - Pre-commit:  lints markdown"
echo "  - Commit-msg:  blocks feat: commits missing a CHANGELOG.md [Unreleased] entry"
echo "  - Pre-push:    lints markdown, runs hook tests, runs Python tests"
echo "  - Override: SKIP_TESTS=1 git push       (skip test gate)"
echo "  - Override: SKIP_LINT=1 git push        (skip lint checks)"
echo "  - Override: SKIP_CHANGELOG=1 git commit (skip CHANGELOG enforcement)"
