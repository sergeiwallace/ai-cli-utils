# 0008. The workflow hook does not enforce branch names

Governs: `.claude/hooks/enforce-workflow.sh`.

## Decision

The hook checks commit messages for a Jira key (when Jira is on) and never checks the
branch name.

## Why

Sessions work in git worktrees on `wt-<tag>-N` style branches by default, and enforcing a
`feature/` pattern blocks that flow. Rejected: enforcing a branch-name pattern in this
hook. A repository that wants feature-branch discipline enforces it through review or a
hook of its own.
