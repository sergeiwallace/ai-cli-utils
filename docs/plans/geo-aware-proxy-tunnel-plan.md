---
title: "Implementation Plan — Geo-Aware SSH Reverse Proxy for `ai gemini`"
category: plan
tags: [ai-cli, ai-gemini, geo-restriction, ssh, socks-proxy, deep-research]
status: active
source: claude-sonnet-4-6
related_tasks: [AI-CLI-40]
---

# Implementation Plan — Geo-Aware SSH Reverse SOCKS Proxy for `ai gemini`

**Task:** AI-CLI-40

## Problem

The Gemini Deep Research Interactions API (`deep-research-pro-preview-12-2025`) returns
`HTTP 400: "User location is not supported for the API use."` when invoked from
non-US IP addresses (e.g., Hetzner server in Germany). This has blocked research runs
at least twice, requiring manual workarounds (delegating to a local Mac CC session).

Other Gemini APIs (flash, pro, deep-think) work fine from Hetzner — the restriction
is specific to the Deep Research model. As more geo-restricted APIs are adopted, this
will recur.

## Goal

When `ai gemini` detects that the target model is geo-restricted from the current
machine, automatically:
1. Establish an SSH reverse SOCKS proxy through a configured relay host (Mac)
2. Route the API call through that proxy
3. Tear down the proxy cleanly after completion

Zero manual intervention. Should be transparent — existing callers don't need to
change anything.

---

## Options

### D-1: Proxy establishment method

**Option A — SSH reverse tunnel + pproxy on relay host (recommended)**
- Relay host (Mac) runs `pproxy` as a SOCKS5 server on a local port
- `ai gemini` on Hetzner opens reverse tunnel: `ssh -R 127.0.0.1:PORT:127.0.0.1:PORT relay`
- Sets `ALL_PROXY=socks5h://127.0.0.1:PORT` for the API call
- Tears down SSH tunnel process after call completes

Pros: no persistent daemon needed on Mac; relay host only needs SSH and pproxy installed; works with existing Tailscale/SSH setup; clean per-invocation lifecycle.
Cons: Mac must be awake and reachable via SSH; pproxy must be installed; adds ~1–2s tunnel setup time.

**Option B — Tailscale exit node**
- Configure Mac as a Tailscale exit node
- `ai gemini` runs `tailscale set --exit-node=<mac-ip>` before call, clears after

Pros: no SSH needed; Tailscale already in use.
Cons: routes ALL Hetzner traffic through Mac (not just the API call); requires Tailscale admin; side-effects on other Hetzner services; `tailscale set` requires root/sudo.

**Option C — Pre-existing SOCKS proxy (always-on)**
- Mac always runs a SOCKS proxy; `ai gemini` just sets `ALL_PROXY`
- No tunnel setup/teardown per call

Pros: zero per-call overhead.
Cons: requires Mac to always be running a daemon; less robust to Mac reboots; no automatic fallback if proxy is down.

**Recommendation: Option A.** Per-invocation lifecycle is cleanest — no persistent daemon required on Mac, no side-effects on non-geo-restricted calls, and the ~1–2s overhead is negligible for research runs that take minutes.

---

### D-2: Geo-restriction detection

**Option A — Static model allowlist in config (recommended)**
- Config file lists which model IDs or aliases are geo-restricted
- `ai gemini` checks config before call; if model is in list → activate proxy
- Example config:
  ```toml
  [gemini.geo_restricted_models]
  models = ["deep-research", "deep-research-pro-preview-12-2025"]
  ```

Pros: zero runtime overhead for non-restricted models; explicit and auditable; easy to update as more models are restricted.
Cons: requires manual config update when new geo-restricted models appear.

**Option B — Runtime detection (try call, retry via proxy on HTTP 400 geo error)**
- Attempt API call normally; if response is `HTTP 400` with geo-restriction message → retry via proxy

Pros: zero config; automatically handles new geo-restricted models.
Cons: wastes one API call per new restricted model (though Deep Research calls are idempotent); adds latency on first detection; parsing error message is fragile.

**Option C — Hybrid (config primary, runtime fallback)**
- Use config allowlist first; also catch HTTP 400 geo errors at runtime and auto-add to config

Recommendation: **Option A for initial implementation**, Option C as a follow-up if new geo-restricted models keep appearing.

---

### D-3: Relay host configuration

**Option A — Configured in ai-cli config file (recommended)**
```toml
[gemini.geo_proxy]
enabled = true
relay_host = "100.106.24.69"  # Tailscale IP of Mac
relay_user = "sergeiwallace"
socks_port = 19050             # local port on relay + tunnel endpoint
relay_pproxy_cmd = "pproxy -l socks5://:19050"
```

Pros: clean separation of concerns; user-configurable; not hardcoded.

**Option B — Inferred from existing SSH config / Tailscale peer list**
Auto-detect a relay host with a US IP address.

Pros: zero config for users with Tailscale.
Cons: complex; may pick wrong relay; requires Tailscale API access.

**Recommendation: Option A.** Explicit is better than magic for infrastructure routing.

---

### D-4: Proxy lifecycle management

**Option A — subprocess with atexit cleanup (recommended)**
- `ai gemini` spawns `ssh -R ... -N -f` as a subprocess
- Registers `atexit` handler to kill the SSH process + any pproxy subprocess
- Also tears down on SIGTERM/SIGINT

Pros: clean; works with `at now` background jobs; no orphaned processes.
Cons: atexit doesn't fire on SIGKILL; acceptable for this use case.

**Option B — Context manager wrapping the API call**
```python
with geo_proxy_context(config):
    result = call_api(...)
```

Pros: Pythonic; ensures teardown even on exceptions.
Cons: Slightly more complex to integrate with existing call flow.

**Recommendation: Option B** — context manager is cleaner and exception-safe. Combine with atexit as belt-and-suspenders.

---

## Open Questions

OQ-1: **pproxy availability on Mac** — Does the user's Mac already have `pproxy` installed, or should the plan include a setup step (`pip3 install pproxy`)? Consider adding a preflight check in `ai gemini` that prints a helpful error if pproxy is missing.

OQ-2: **Port conflicts** — Should the SOCKS port (default 19050) be configurable, and should `ai gemini` verify the port is free before opening the tunnel?

OQ-3: **Multiple geo-restricted calls in a session** — If a user runs `ai gemini -m deep-research` twice in quick succession, should the tunnel be reused or re-established? Reuse is more efficient but requires a persistent tunnel manager.

OQ-4: **Mac sleep / unreachable relay** — If the relay host is unreachable (Mac asleep), should `ai gemini` fail fast with a clear error, or fall back to a direct call (which will fail with geo-error anyway)?

---

## Implementation Tasks

**Batch 1 — Core implementation**

1. Add `[gemini.geo_proxy]` config section to `~/.config/ai-cli/config.toml` schema + docs
2. Implement `GeoProxyContext` class: `ssh -R` tunnel setup, `ALL_PROXY` injection, teardown
3. Add preflight: check relay reachability + pproxy installed on relay before starting tunnel
4. Wire into `ai gemini` call path: if model in `geo_restricted_models` → wrap with `GeoProxyContext`
5. Add `--no-proxy` flag to bypass geo proxy for debugging

**Batch 2 — Polish**

6. Add `ai gemini proxy-status` subcommand: check relay reachability, test SOCKS connectivity
7. Runtime geo-error detection: catch HTTP 400 geo message, log warning, suggest config update
8. Tests: mock SSH subprocess + pproxy; verify proxy env var set/unset correctly; verify atexit cleanup

---

## Approval Log

- 2026-04-07 Round 1: Plan drafted — D-1=A, D-2=A, D-3=A, D-4=B recommended. OQ-1 through OQ-4 open.
