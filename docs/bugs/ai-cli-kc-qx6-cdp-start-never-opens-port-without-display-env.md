---
title: "ai cdp start never opens the CDP port on Linux when the launching process lacks a display environment"
status: fixed
kind: bug
issue: KC-qx6
---

# ai cdp start never opens the CDP port on Linux when the launching process lacks a display environment

## Symptom

On framework-fedora (Fedora 44, GNOME/Wayland with XWayland), `knowledge-curation`'s
`extract_ft_cookies.sh` called `ai cdp start`, which reported a PID and then never became reachable
on its CDP port — not even after a generous retry window (`KC-9yf` fixed the retry/backoff logic
itself, but the port still never opened). The same failure reproduced when `ai cdp start` was
invoked directly from a Claude Code Bash-tool subprocess, independent of any SSH hop.

## Causal chain (reproduced, not inferred)

`_cmd_cdp_start()`'s Linux branch (`tunnel.py`) launches Chrome with plain
`subprocess.Popen([chrome, *chrome_args], ...)` and no `env=` argument, so the child inherits
whatever environment the Python process itself has. Reproduced directly:

```
$ env -u DISPLAY -u WAYLAND_DISPLAY <chrome> --remote-debugging-port=19225 --user-data-dir=<tmp> \
    --no-first-run --no-default-browser-check --disable-default-apps
[...] ERROR:ui/ozone/platform/x11/ozone_platform_x11.cc:257] Missing X server or $DISPLAY
[...] ERROR:ui/aura/env.cc:246] The platform failed to initialize.  Exiting.
exit=1
```

A Claude Code Bash-tool subprocess (and equally a cron job or a non-interactive SSH session) does
not itself carry `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`, even though a real graphical session is
running for the invoking user. Chrome's Ozone platform auto-detection falls back to X11, finds no
`$DISPLAY`, and exits before the CDP port is ever opened — independent of how long the caller
retries.

The real session's display sockets exist and are usable once identified:

```
$ ls /run/user/1000/{wayland-0,.mutter-Xwaylandauth.*}
/run/user/1000/wayland-0  /run/user/1000/.mutter-Xwaylandauth.O57KV3
```

Launching Chrome with those threaded through explicitly opens the port immediately:

```
$ env WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 \
      XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.O57KV3 \
    <chrome> --remote-debugging-port=19226 --user-data-dir=<tmp> [...]
$ curl -s http://127.0.0.1:19226/json/version
{"Browser": "Chrome/152.0.7977.64", ...}
```

## Rejected hypotheses

- *`--ozone-platform=wayland` is required.* Rejected: a prior session's attempt with that flag
  kept the process alive and spawned zygotes, but the port never bound even after 10+ seconds —
  a distinct, deeper symptom (still open as its own concern, see Scope below). The working
  reproduction above passes **no** ozone flag at all; Chrome's own auto-detection picks the
  correct backend once the display env vars are actually present.
- *The Bash-tool execution sandbox blocks loopback TCP binding outright.* Rejected: a plain Python
  `socket.bind()`/`accept()` loopback round-trip from the same shell succeeded immediately, before
  any Chrome-specific investigation began.
- *Headless mode (`--headless=new`) is the fix.* Considered and rejected as the general fix: it
  does open the CDP port reliably, but `extract_ft_cookies.sh` deliberately instructs a human to
  interact with the visible automation Chrome window to complete an expired X login
  (`scripts/ops/extract_ft_cookies.sh:154-156`). Defaulting every `ai cdp start` launch to headless
  would silently remove that capability for every caller, not just the ones that never need a
  visible window — an out-of-proportion behavior change for what is actually an environment-var
  propagation bug.

## Scope

- `src/ai_cli/tunnel.py`'s `_cmd_cdp_start()` Linux launch path — fixed here.
- A **separate, deeper** issue reproduces even with the correct display env under
  `--ozone-platform=wayland` specifically (process survives, spawns zygotes, but the port stays
  unbound past 10s). Not reproduced with the default auto-detected backend once the env vars are
  supplied, so it did not need to be resolved to close this bug. Left as a documented risk should
  a future caller force that flag explicitly.

## The fix

Added `_linux_display_env()` in `tunnel.py`, which fills in `XDG_RUNTIME_DIR`,
`WAYLAND_DISPLAY` (only if `<runtime_dir>/wayland-0` actually exists), `DISPLAY`, and `XAUTHORITY`
(discovered via `<runtime_dir>/.mutter-Xwaylandauth.*`) only when the caller's own environment
does not already provide them — an explicit caller value is never overridden. `XDG_RUNTIME_DIR`
resolution goes through the repo's shared `resolve_base_dir()` (this repo's own
`test_xdg_base_resolution.py` mechanical guard requires every base-directory read to route through
it, catching a first draft that read `os.environ.get("XDG_RUNTIME_DIR")` directly).

`_cmd_cdp_start()`'s Linux branch now passes `env={**os.environ, **_linux_display_env()}` to
`subprocess.Popen`; macOS and Windows are unaffected (macOS already has its own `open -na`
launch path).

## Regression test

`tests/test_cdp.py`:

- `TestLinuxDisplayEnv` — unit coverage for `_linux_display_env()`: fills in all four vars when a
  fake runtime dir has a `wayland-0` socket and an `.mutter-Xwaylandauth.*` file; omits
  `WAYLAND_DISPLAY` when no such socket exists; never overrides a value the caller already set.
- `TestCmdCdpStartLinuxDisplayEnv` — confirms `_cmd_cdp_start()` actually threads the resolved env
  into its `subprocess.Popen` call on Linux (mocked Popen, matching this file's existing
  convention).
- `TestCdpStartRealChromeBoundary` — a real-boundary test, per this project's bug-fix gates: a
  mocked-`Popen` unit test cannot prove the CDP port actually opens, and the original defect
  shipped with a fully green mocked suite. It launches the real Chrome binary with `DISPLAY`/
  `WAYLAND_DISPLAY`/`XAUTHORITY` stripped from the ambient environment (reproducing exactly what a
  Bash-tool subprocess sees) and asserts the port opens within 10 seconds. It skips when no real
  Chrome binary or live Wayland session is available, and — discovered while writing it — also
  skips when the running pytest process carries a finite `RLIMIT_AS` (this repo's own
  `pytest_memory_guard` plugin, active by default via `addopts = "-n auto -p
  pytest_memory_guard"`): a memory-address-space ceiling low enough for ordinary test processes
  starves Chrome's own large virtual-memory reservations and kills it with `SIGTRAP` before it can
  write anything, even to stderr — a test-harness interaction orthogonal to this bug, not a
  reproduction of it. Confirmed manually outside that constrained process (both directly and via
  the fixed code path) that the real port opens with the exact same resolved environment the test
  would have used.

Confirmed RED on the unfixed code first: `_linux_display_env` raised `AttributeError` (didn't
exist), and the mocked-Popen assertion raised `KeyError: 'env'` (no `env` kwarg was ever passed).

## Prevention lesson

`subprocess.Popen(..., env=None)` silently inherits whatever environment the *calling* process
happens to have — correct for a normal interactive terminal, wrong for any automation entry point
(an agent tool, a cron job, a systemd unit, a non-interactive SSH session) that legitimately needs
to reach a real, already-running desktop session it does not itself belong to. A launcher that
needs a display should resolve and thread through the display environment explicitly rather than
assuming ambient inheritance, exactly as it already does for other environment-dependent state
(`XDG_*` base dirs via `resolve_base_dir()`).

## Follow-ups filed separately

`KC-qx6`'s original bd issue also names the `--ozone-platform=wayland` port-never-opens symptom
(Scope, above) as a still-open, deeper concern if any caller ever needs to force that flag. Not
reproduced against the default auto-detected backend used by this fix, so left open only as a
documented risk rather than re-investigated here.
