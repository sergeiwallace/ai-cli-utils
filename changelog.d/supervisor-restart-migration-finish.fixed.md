- A session's tmux supervisor now finishes a pending `ai migrate-session` migration to that
  session when it restarts the agent, between the old agent's exit and the new one's start.
  Before, only `ai c NEW` and `ai sessions relaunch` ran that finish, so a migration whose
  launch-time finish was refused stayed unfinished for as long as the supervisor kept
  restarting the agent. The session's own tmux session no longer counts as live for that
  finish. A live agent in either slot, or another tmux session hosting either name, still
  refuses it, and a refused finish only warns: the agent restarts anyway.
