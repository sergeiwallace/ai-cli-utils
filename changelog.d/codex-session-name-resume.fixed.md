- `ai cx <name>` now resumes the Codex thread that carries the session's name instead of
  starting a new one on every relaunch, and a new Codex session is named after the launcher
  session once, at its first launch, by typing `/rename <name>` into it. The thread is found
  by id from Codex's own `session_index.jsonl`, so a session that was named but never sent a
  prompt still resumes, although Codex's own `/resume` picker does not list it. This works for
  local and `-R` tmux launches, `--once`, and bare launches on a launcher-owned pty. A native
  Windows bare launch without tmux cannot be typed into, so it asks you to run the
  `/rename` once yourself.
- `ai p <name>` now continues the session's Pi conversation on every launch. A relaunch used
  to start a new Pi session unless it was an in-place restart or a bare launch with `-r`.
