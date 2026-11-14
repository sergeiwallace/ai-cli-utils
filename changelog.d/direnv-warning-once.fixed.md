- When direnv is not usable, `ai c` prints its full remediation block (install commands, shell
  hooks and bypasses) only the first time on a host. Later launches print one line saying
  direnv is not installed and `.envrc` is not loaded, and pointing at `ai doctor` for the fix.
  `ai doctor` always prints the full block. The record lives in the state directory and is
  cleared once direnv is usable again, so a later breakage shows the block once more.
- The automatic install of a native tool (direnv, tmux, zsh) now tries passwordless `sudo -n`
  before reporting that a system package manager needs root, and prints the exact command
  (`installing direnv with: sudo -n apt-get install -y direnv`) before it runs. It never prompts
  for a password, and it is skipped on Windows and when `sudo` is not on PATH. When passwordless
  sudo is not available, a line on stderr, which the launch log records, says why.
