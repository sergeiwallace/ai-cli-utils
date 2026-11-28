- `ai migrate-session` now repoints every symlink inside the slot and the moved agent
  worktrees that names the old slot or an `OLD-agents` container, a `.venv` link included, at
  the same path under the new name; a relative link stays relative. Before, each such link
  dangled after the rename, so a venv kept in a sibling worktree stopped resolving. A link
  outside the session's worktrees is never changed: when a link inside resolves through one
  that names an old-name path, the run and `--finish` list it as `NOT fixed` and exit 1.
