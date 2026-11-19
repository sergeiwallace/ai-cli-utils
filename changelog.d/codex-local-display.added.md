- On Linux, a supervised `ai cx` session started from a shell with no `DISPLAY` and no `XAUTHORITY`
  (over SSH, for example) now starts Codex with the display of your own local desktop session, so
  Codex's native clipboard works while you use that desktop over remote desktop. The launcher uses
  the pair only when it can verify it: exactly one active, non-remote X11 or Wayland logind session
  of your user, the pair taken from your systemd user manager's environment, and an authority file
  you own that is not older than the session. Otherwise it adds nothing and writes the reason to the
  launch log. A `DISPLAY` or `XAUTHORITY` you set yourself is kept, and only the Codex process gets
  the pair, not your shell or tmux. macOS and Windows are unchanged.
