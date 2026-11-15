- On a loaded host a session could end at launch with `tmux did not answer in time` before its
  agent started. The supervisor's tmux ownership bind gave each of its four attempts 2 s, and
  starting the tmux client alone could take longer than that on every attempt. Each attempt
  now has 10 s.
