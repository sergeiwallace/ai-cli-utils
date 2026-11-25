- On Windows, `ai c NAME` no longer fails to create the session worktree with "Filename too long"
  when a tracked file's path fits the main checkout but not `.worktrees/NAME/`. The git calls that
  create, check out and update worktrees run with `core.longpaths=true` (git outside Windows ignores the key), and on Windows a
  launch sets `core.longpaths=true` in the repository's own config when no config file sets it, so
  the session's own `git status` can read those paths too. An explicit `false` is left alone.
