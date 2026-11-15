- Each session row in the registry now records `canonical_name`, and its `agent_identity`
  also carries `proc_start` and `proc_start_backend` (the process start marker in the
  platform probe's own unit), the host `boot_id` (null on Windows) and `interactive`. A
  reader can then prove that a process descends from the agent the launcher started. Bare
  launches record the agent as well, except on Windows.
