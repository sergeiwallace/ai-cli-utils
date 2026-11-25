- Start the agent under a project's direnv environment on Windows. `direnv exec` cannot
  run a native Windows program, so bare and one-shot launches there load the environment
  with `direnv export json` and find the agent with `shutil.which`, which honours PATHEXT.
  Managed sessions under Git Bash convert the PATH direnv exports back to the shell's own
  spelling. `ai doctor` reports where each agent CLI resolves on the PATH a launch uses.
