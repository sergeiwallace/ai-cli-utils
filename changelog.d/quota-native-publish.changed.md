- `ai quota publish` replaces `ai quota scrape` for sending Claude quota to NATS. It reads the
  state file the statusline writes from Claude Code's native `rate_limits` data
  (`[quota] state_file`, default `~/.claude/state/quota.json`). When the seven-day reading is
  current it publishes `quota.snapshot`, `hw.events.usage.claude.snapshot` and the
  `quota.claude.current.<host>` key. Otherwise it exits 0 and publishes nothing, so a scheduled
  job no longer fails every time `/usage` cannot be scraped. The statusline no longer starts a
  scrape, and `quota watch` no longer answers `quota.scrape.request.<host>`. The hidden-pane
  scrape remains as `ai quota scrape-fable`, because the native data has no per-model weekly
  (Fable) window and readers of the local quota DB still use that value.
