- `ai c NAME -R` over mosh now shows a launch the remote host refused, and exits with its status.
  Mosh clears the screen when the remote command ends and carries no exit status back, and the
  remote command's stderr was redirected into a file that was only read when mosh failed within
  a minute. A refusal that took longer, such as a migration guard re-planning for minutes,
  returned to the prompt with exit 0 and nothing printed. The remote `ai` now copies its launch
  log lines into that file and records a nonzero status beside it; once mosh ends, the launching
  terminal prints them with the finish command and exits with that status. stderr stays on the
  terminal, so the remote side's progress lines show while it works.
- `ai migrate-session --status` and the launch guard of `ai c NEW` now judge a recorded pair with
  one shared function, so they cannot disagree about whether a migration is partial.
- An `ai c -R` launch no longer prints two identical-looking launcher headers. Every line the
  remote side prints now starts with `[remote <host>]`, so its `start`, `install` and other steps
  read apart from the launching machine's own.
