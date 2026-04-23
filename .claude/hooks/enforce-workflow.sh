#!/bin/bash
# PreToolUse hook: Enforce development workflow conventions
# Covers: (no enforcement — permissive)
# Exit 0 = allow, exit 2 = block
#
# Branch-name convention is intentionally NOT enforced here — humanware workflows
# use git worktrees (wt-<tag>-N branches) by default, and enforcing a feature/
# pattern blocks that flow. If you want feature-branch discipline, enforce it
# via PR review or a named hook on specific repos, not via the ecosystem default.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')


# ── Commit message validation ────────────────────────────────────────────
if echo "$COMMAND" | grep -q 'git commit'; then

  # No Jira — allow all commits
  exit 0

fi

exit 0
