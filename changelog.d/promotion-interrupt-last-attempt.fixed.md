- A Ctrl+C pressed while a session's terminal handoff made its last attempt is now treated as a
  deliberate stop. It used to be reported as `could not promote the session shell's process group`
  and the supervisor exited 1 instead of tearing its session down, which happened on loaded hosts,
  where each attempt can take seconds.
