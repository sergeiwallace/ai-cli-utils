- A `[session] auto_restart` key in `config.toml` turns off the restart of Claude Code when it
  exits inside a tmux session. Missing or `true` keeps today's behaviour; `false` lets the
  session end at the agent's next exit and says why. The supervisor reads the key from the file
  at every exit, so an edit applies at the next exit with no relaunch, and an unreadable or
  malformed file never stops restarts.
- `docs/reference/config.toml` is the complete, commented `config.toml` that `ai` writes for a
  new user, with every key and its default. A test keeps it identical to that text, fails when
  the code reads a key in a documented table that the template does not list, and fails when a
  table validated by a `*_DEFAULTS` dict lists a key nothing reads.
