#!/bin/bash
# PreToolUse hook: Enforce development workflow conventions
# Covers: branch naming, feature-on-main protection
# Exit 0 = allow, exit 2 = block

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')


BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

# ── Branch naming validation ──────────────────────────────────────────────
# When creating a new branch, enforce feature/* pattern
if echo "$COMMAND" | grep -qE 'git (checkout -b|switch -c)'; then
  BRANCH_NAME=$(echo "$COMMAND" | grep -oE '(checkout -b|switch -c)\s+\S+' | awk '{print $NF}')
  if [ -n "$BRANCH_NAME" ] && [ "$BRANCH_NAME" != "main" ]; then

    if ! echo "$BRANCH_NAME" | grep -qE '^feature/'; then
      echo "Branch name must follow: feature/short-description" >&2
      exit 2
    fi

  fi
fi

# ── Commit message validation ────────────────────────────────────────────
if echo "$COMMAND" | grep -q 'git commit'; then

  # No Jira — allow all commits
  exit 0

fi

exit 0
