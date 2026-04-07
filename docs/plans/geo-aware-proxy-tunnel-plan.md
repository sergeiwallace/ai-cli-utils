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

> **Status: PENDING — open question about fallback when Mac is unreachable (see OQ-4). D-3 depends on this decision.**

**Option A — SSH local port-forward + pproxy on relay host**
- Relay host (Mac) runs `pproxy` as a SOCKS5 server on a local port
- `ai gemini` on Hetzner opens local port-forward: `ssh -L 127.0.0.1:PORT:127.0.0.1:PORT relay`
  *(Note: plan originally said `-R`; corrected to `-L` — local forward routes Hetzner→Mac, `-R` is the opposite direction)*
- Sets `ALL_PROXY=socks5h://127.0.0.1:PORT` for the API call
- Tears down SSH tunnel + pproxy after call completes

Pros: no persistent daemon needed on Mac; relay host only needs SSH and pproxy; works with existing Tailscale/SSH setup; clean per-invocation lifecycle.
Cons: Mac must be awake and reachable; pproxy must be installed; adds ~1–2s tunnel setup time. **No fallback if Mac is asleep.**

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

**Option D — Multiple relay hosts with ordered fallback**
- Config lists multiple relay hosts (e.g. Mac primary, always-on VPS secondary)
- `ai gemini` tries each in order; moves to next if connection fails
- Each relay can be A-style (pproxy on demand) or C-style (always-on proxy)

Pros: resilient to Mac being asleep; VPS is always reachable.
Cons: requires provisioning a secondary relay; more config complexity.

**Recommendation: Option A with Option D fallback support.** Use Mac as primary (per-invocation pproxy). Add secondary relay config slot for an always-on fallback. D-3 schema should support a relay list.

---

### D-2: Geo-restriction detection

> **Status: APPROVED — Option C.**

**Option A — Static model allowlist in config**
- Config file lists which model IDs or aliases are geo-restricted
- `ai gemini` checks config before call; if model is in list → activate proxy

Pros: zero runtime overhead for non-restricted models; explicit and auditable; easy to update.
Cons: requires manual config update when new geo-restricted models appear.

**Option B — Runtime detection (try call, retry via proxy on HTTP 400 geo error)**
- Attempt API call normally; if response is `HTTP 400` with geo-restriction message → retry via proxy

Pros: zero config; automatically handles new geo-restricted models.
Cons: wastes one API call per new restricted model; adds latency on first detection; fragile error message parsing.

**Option C — Hybrid (config primary, runtime fallback) ✓ APPROVED**
- Use config allowlist first; also catch HTTP 400 geo errors at runtime and auto-add to config
- Example config:
  ```toml
  [gemini.geo_restricted_models]
  models = ["deep-research", "deep-research-pro-preview-12-2025"]
  ```

Pros: config-fast for known models; automatically self-updating for new ones.
Cons: slightly more complex; requires writing config on runtime detection.

---

### D-3: Relay host configuration

> **Status: PENDING — depends on D-1 decision re: fallback relay support.**

**Option A — Single relay in config file**
```toml
[gemini.geo_proxy]
enabled = true
relay_host = "100.106.24.69"
relay_user = "user"
socks_port = 19050
relay_pproxy_cmd = "pproxy -l socks5://:19050"
```
Pros: simple; minimal config.
Cons: no fallback if primary relay is unreachable.

**Option B — Ordered relay list in config file (recommended if D-1=A+D)**
```toml
[gemini.geo_proxy]
enabled = true
socks_port = 19050

[[gemini.geo_proxy.relays]]
host = "100.106.24.69"   # Mac (primary)
user = "user"
pproxy = true             # start pproxy on demand

[[gemini.geo_proxy.relays]]
host = "my-us-vps.example.com"  # always-on VPS (fallback)
user = "user"
pproxy = false            # persistent proxy already running
```
Pros: supports fallback; each relay can be pproxy-on-demand or always-on; extensible.
Cons: more config to set up; requires provisioning a fallback relay.

**Option C — Inferred from Tailscale peer list**
Auto-detect relay with a US IP.
Pros: zero config.
Cons: complex; may pick wrong relay; requires Tailscale API.

**Recommendation: Option B** if D-1 proceeds with fallback support; Option A if single relay is sufficient.

---

### D-4: Proxy lifecycle management

> **Status: APPROVED — Option B (combined with atexit).**

**Option A — subprocess with atexit cleanup**
- Spawns SSH as subprocess, registers `atexit` to kill it

Pros: simple; works in background jobs.
Cons: atexit doesn't fire on SIGKILL; teardown not guaranteed on exception mid-call.

**Option B — Context manager wrapping the API call ✓ APPROVED**
```python
with GeoProxyContext(config):
    result = call_api(...)
```
Combined with atexit as belt-and-suspenders.

Pros: Pythonic; exception-safe teardown; atexit provides backup cleanup.
Cons: Slightly more integration work.

---

## Open Questions

OQ-1: **pproxy auto-install** — PENDING. User asked: why not auto-install pproxy if missing rather than showing an error? Proposed approach: SSH to relay, check `which pproxy`; if missing, run `pip3 install --user pproxy` on the relay automatically before proceeding. Risks: pip3 may not be available; may install into wrong environment; surprising to users on shared relays. Alternative: use `pipx install pproxy` for isolated install. Awaiting decision: auto-install vs fail-with-error.

OQ-2: **Port conflicts** — PENDING. Port configurable via `socks_port` in config. Add `ExitOnForwardFailure=yes` SSH flag so tunnel fails immediately if port already bound. Consider also checking local port availability before opening tunnel (e.g. `ss -ltn | grep :19050`). Awaiting approval.

OQ-3: **Persistent tunnel manager** — PENDING. User asked for pros/cons. See analysis below.

**Pros of persistent tunnel manager:**
- Reuse tunnel across multiple calls in same session (~1–2s savings per call)
- Tunnel pre-warmed when call happens
- Better for research iteration loops (multiple gemini calls back-to-back)

**Cons of persistent tunnel manager:**
- Significantly more complex: need tunnel lifecycle tracking, health checks, restart on failure
- Orphaned tunnels/pproxy processes if persistent manager dies unexpectedly
- Port must remain reserved for lifetime of manager
- For occasional one-off calls, overhead is negligible anyway

**Recommendation:** Per-invocation for now. Add persistent tunnel as follow-up enhancement if repeated calls in a session become a pain point. Decision pending.

OQ-4: **Mac sleep / unreachable relay** — PENDING. User asked for fallback options. Options:
- **Wake-on-LAN via Tailscale** (`tailscale wake <peer>`) — possible but adds 30s+ wake latency; fragile
- **Secondary always-on relay** (e.g. a US VPS) — most reliable; ties into D-1/D-3 fallback discussion
- **Graceful fail-fast** — clear error message explaining relay is unreachable, how to fix
- **Direct call attempt** — attempt without proxy (will geo-fail, but at least user sees the real error)

Recommendation: ordered relay list (D-3 Option B) covers this — Mac primary, VPS secondary. Always-fail-fast with clear error if all relays unreachable. Awaiting decision on whether secondary relay is in scope.

---

## Implementation Tasks

> **Blocked on D-1, D-3, OQ-1, OQ-3, OQ-4 decisions. Will be updated once those are resolved.**

**Batch 1 — Core implementation**

1. Add `[gemini.geo_proxy]` and `[gemini.geo_restricted_models]` config sections to `DEFAULT_CONFIG` schema + docs
2. Implement `GeoProxyContext` class: `ssh -L` tunnel setup, `ALL_PROXY` injection, atexit + context manager teardown
3. Add preflight: check relay reachability + pproxy installed (and optionally auto-install) on relay before starting tunnel
4. Wire into `ai gemini` call path (both `run_gemini` and `_run_deep_research`): if model in `geo_restricted_models` → wrap with `GeoProxyContext`; also catch runtime HTTP 400 geo-errors (D-2=C)
5. Add `-P`/`--no-proxy` flag to bypass geo proxy for debugging

**Batch 2 — Polish**

6. Add `ai gemini proxy-status` subcommand: check relay reachability, test SOCKS connectivity
7. Tests: mock SSH subprocess; verify `ALL_PROXY` set/unset correctly; verify atexit cleanup; verify preflight failure path

---

## Approval Log

- 2026-04-07 Round 1: Plan drafted — D-1=A, D-2=A, D-3=A, D-4=B recommended. OQ-1 through OQ-4 open.
- 2026-04-07 Round 2: D-2=C approved, D-4=B approved. D-1 pending (fallback relay question). D-3 pending (depends on D-1). OQ-1 through OQ-4 under discussion. Bug fix: `-R` → `-L` SSH flag.
