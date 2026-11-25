- Allow owner-approved recovery of Codex slots that predate identity records with
  `python -m ai_cli.codex_session prepare|launch RECORD CWD SESSION false ROUTE
  --start-fresh SESSION` (or `-F SESSION`). Approval must name the exact session and
  can create only a missing record; it is never saved as a launch option. Old native
  records remain untouched, and normal confirmation and exact-thread resume proofs
  still apply. Legacy launcher metadata cannot prove an exact native thread ID, so
  automatic migration remains refused. Windows launch retains its existing refusal
  because it cannot provide the required POSIX exec identity continuity; macOS and
  real-client recovery remain unverified.
