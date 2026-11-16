- Each session row in the registry now records `canonical_name`, and its `agent_identity`
  also carries `proc_start` and `proc_start_backend` (the process start marker in the
  platform probe's own unit), the host `boot_id` (null on Windows) and `interactive`. A
  reader can then prove that a process descends from the agent the launcher started. Bare
  launches record the agent as well, except on Windows.
- A process can be attested by only one session: a second live claim on the same agent
  process is refused. `ai cx` now starts each session's Codex with `--no-daemon` when the
  installed Codex offers it, so sessions do not share Codex's per-host app-server.
- `agent_identity` records `engine_binding`: `exclusive`, or `shared-daemon` for a Codex that
  runs its tools in the host's shared app-server. A shared-daemon session never proves its
  identity from process ancestry, and `ai exit-session` with no name inside one now refuses
  and asks for the session name.
