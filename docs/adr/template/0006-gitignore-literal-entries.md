# 0006. Gitignore entries that must stay as written

Governs: `.gitignore`.

## Decision and reasons

- `.venv` has no trailing slash. A trailing-slash pattern matches only a real directory,
  never a symlink to one, and some worktrees carry `.venv` as a symlink to the main tree's
  venv, which then showed as untracked for good. `git check-ignore -v .venv` exits 1 under
  `.venv/` and 0 under `.venv`. Rejected: `.venv/`.
- `node_modules/` and `package-lock.json` are ignored for every stack, because
  `package.json` and `setup.sh`'s `npm install` ship for every stack.
- `.beads/.dolt-server-state.json` is ignored. It is machine-local Dolt server state (host, <!-- gitignored-path-ok: names the ignored entry, not evidence -->
  pid, port and start time), rewritten on every server start; a pid and a port mean nothing
  on another machine, and as an untracked `.beads/` path it trips the Beads check in
  `/sync-git`. `bd init` writes the other Beads ignores but not this one, so the template
  carries it. project-template's own root `.gitignore` carries the same entry.
- `*.gate.lock*` is the literal line `bd doctor` looks for in the root `.gitignore`, covering
  `.beads.gate.lock` and the store's `embeddeddolt.gate.lock`. A broader pattern that
  happens to match the file does not satisfy the check, so the exact string is kept.
