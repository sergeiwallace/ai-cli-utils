- `ai quota publish` replaces `ai quota scrape` for sending Claude quota to NATS. It reads the
  state file the statusline writes from Claude Code's native `rate_limits` data
  (`[quota] state_file`, default `~/.claude/state/quota.json`). When the seven-day reading is
  current it publishes `quota.snapshot`, `hw.events.usage.claude.snapshot` and the
  `quota.claude.current.<host>` key. Otherwise it exits 0 and publishes nothing, so a scheduled
  job no longer fails every time `/usage` cannot be scraped. The statusline no longer starts any
  process, and prints nothing when it has no seven-day value.
- Every `/usage` scrape is removed: `ai quota scrape`, the statusline's background scrape, the
  Fable backoff scrape, and the `ai quota watch` daemon (`start`, `stop`, `status`, `run`, the
  `[quota_watch]` config section, the per-session auto-start, and its
  `quota.scrape.request.<host>` and heartbeat keys), whose loop ran the scrape. The Fable scrape had
  no caller and had stored only empty values since `/usage` stopped showing a per-model line.
  Fable weekly data has no source until Claude Code reports a Fable window; a reader of
  `quota.db` keeps seeing its last stored value or none. A host that registered quota-watch
  keeps a Circus watcher whose command no longer exists; remove it once with
  `circusctl --endpoint ipc://<state dir>/circus.endpoint rm quota-watch`, where the state dir is
  ai-cli's state directory (for example `~/.local/state/ai-cli-utils`).
