# Canonical worktree registry

`ai c`, `ai g`, `ai p`, and `ai cx` register the session worktree before
starting the tool. The launcher writes the absolute registry path and whether
the entry was `created`, `updated`, or `validated-already-tracked`.

The default registry is machine-local state, not project data:

- Linux and macOS: `$XDG_DATA_HOME/ai-cli-utils/canonical-session-worktrees.json`,
  or `~/.local/share/ai-cli-utils/canonical-session-worktrees.json`.
- Windows: `%LOCALAPPDATA%/ai-cli-utils/canonical-session-worktrees.json`.
- SageMaker Code Editor: `~/user-default-efs/.local/share/ai-cli-utils/canonical-session-worktrees.json`.

The SageMaker location is on EFS because its ordinary `$HOME` is ephemeral.
Administrators may set the absolute `AI_CLI_CANONICAL_WORKTREE_REGISTRY` path
when their machine has a different persistent-volume convention.

The file is ignored if it is ever placed in a checkout. It has this schema:

```json
{
  "version": 1,
  "worktrees": [
    {
      "path": "/projects/myproject/.worktrees/session-1",
      "engine": "c",
      "session_name": "c-myproject-1",
      "first_registered_at": "2026-09-15T00:00:00+00:00",
      "last_validated_at": "2026-09-15T00:00:00+00:00"
    }
  ]
}
```

Consumers must reject destructive requests when the registry is missing,
unreadable, invalid, or locked. Paths, rather than naming patterns, determine
which worktrees are canonical.
