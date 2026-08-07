import asyncio
import contextlib
import json
import os
import socket
import subprocess
import time

import nats
from nats.errors import NoServersError, TimeoutError

# JetStream stream configs: stream_name -> list of subject patterns
STREAM_CONFIG = {
    "fleet": ["fleet.worker.>"],
    "sync": ["sync.>"],
    "session": ["session.>"],
    "memory": ["memory.>"],
    "quota": ["quota.>"],
    "telemetry": ["telemetry.>"],
    "handoff-archive": ["handoff-archive.>"],
    "task": ["task.>"],
    "health": ["health.>"],
    "handoff": ["handoff.>"],
}


class NATSClient:
    """Asynchronous NATS client wrapper with JetStream support."""

    def __init__(self, servers=None):
        self.servers = servers if servers is not None else ["nats://localhost:4222"]
        self.nc = None
        self.js = None
        self._streams_ensured = set()
        self._tunnel_proc: subprocess.Popen | None = None

    async def _open_ssh_tunnel(self) -> None:
        """Open SSH tunnel to Hetzner NATS when running on Mac and port 4222 is unreachable."""
        _host_id = os.environ.get("AI_HOST", "")
        if _host_id != "mac":
            return
        try:
            with socket.create_connection(("localhost", 4222), timeout=1):
                return  # already reachable
        except OSError:
            pass
        # Use configured remote host so Tailscale/direct-IP preference is respected.
        try:
            from .config import load_config as _load_config

            _cfg = _load_config()
            _remote = _cfg.get("remote", {})
            _user = _remote.get("user", "")
            _host = _remote.get("host", "")
            _port = str(_remote.get("port", 22))
            _identity = _remote.get("identity_file", "")
        except Exception:  # config load must never block the tunnel
            _user, _host, _port, _identity = "", "", "22", ""
        if not _user or not _host:
            return  # no remote configured — skip tunnel
        # Use vpn_host (direct IP) only when VPN is active — Tailscale is
        # unreachable under VPN so the tunnel must go via the direct IP instead.
        try:
            from .transport import _is_vpn_active as _vpn_check

            _tunnel_host = (_remote.get("vpn_host", "") or _host) if _vpn_check() else _host
        except Exception:  # VPN-detection failure must not block the tunnel
            _tunnel_host = _host
        ssh_cmd = ["ssh", "-fNL", "4222:localhost:4222", "-o", "ConnectTimeout=5"]
        if _identity:
            ssh_cmd += ["-i", _identity]
        ssh_cmd += ["-p", _port, f"{_user}@{_tunnel_host}"]
        # Deliberately synchronous subprocess.Popen: `ssh -f` forks itself to
        # the background after auth and the foreground process we launch here
        # exits on its own; nothing here needs to `await` it. Switching to
        # asyncio.create_subprocess_exec would change the -f semantics this
        # method (and TestSshTunnel) depends on.
        self._tunnel_proc = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(6):
                await asyncio.sleep(0.5)
                try:
                    with socket.create_connection(("localhost", 4222), timeout=1):
                        return
                except OSError:
                    pass
        finally:
            await self._reap_tunnel_parent()

    async def _reap_tunnel_parent(self) -> None:
        """Reap the ``ssh -f`` foreground process so it doesn't linger as a zombie.

        ``ssh -fNL`` forks the tunnel to the background after authentication; the
        foreground process we Popen'd then exits. Nothing else in ai-cli reaps
        children — there is no SIGCHLD handler, and we deliberately do NOT set
        ``SIGCHLD=SIG_IGN`` because these watcher processes (signal/sync/memory
        watch) also run ``subprocess.run`` for git, which would then fail with
        ECHILD. Without this reap, every long-running watcher accumulates one
        ``<defunct>`` ssh for its whole lifetime (AI-CLI-86). ``poll()`` reaps if
        the parent already exited; otherwise wait briefly off the event loop so
        asyncio is never blocked.
        """
        proc = self._tunnel_proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                await asyncio.to_thread(proc.wait, 6)
        except Exception:  # reap best-effort, TimeoutExpired included
            pass

    # Bound on the whole connect() call, including the library's own retries. Nothing
    # here is on a user-visible critical path -- every caller treats a null client as
    # "messaging unavailable" -- so failing fast always beats blocking.
    _CONNECT_DEADLINE_SECS = 10

    async def connect(self):
        """Connects to NATS with bounded exponential backoff. Gives up after 3 attempts.

        Passes max_reconnect_attempts=1 to the nats library to bound its own internal
        initial-connect retry loop (AIDO-294). max_reconnect_attempts=0 looks like it
        should mean "no retries" but does NOT: nats-py's _select_next_server() only
        discards a server from its pool when `max_reconnect_attempts > 0` is true, so
        0 is treated as unlimited (equivalent to a negative value) — the server is
        never discarded, the pool never empties, NoServersError is never raised, and
        connect() never returns. With a single configured server this produced a
        genuine infinite loop (~2s busy-sleep cadence) rather than the documented
        "60 attempts x 2s = 2-minute hang", and was the root cause of 3+ day old
        orphaned `ai internal publish` processes (49 found in one census). A value of
        1 lets the pool-discard condition (`reconnects > max_reconnect_attempts`) fire
        after two failed attempts, so nats.connect() reliably raises NoServersError
        and our own retry loop below handles backoff instead, as originally intended.

        The call is additionally wrapped in a hard deadline, because relying on the
        library to honour its own options is not itself a bound: a partially-reachable
        server (accepts the TCP connection, never completes the NATS handshake) would
        otherwise still hang a caller indefinitely.

        error_cb silences the nats library's verbose per-attempt tracebacks — connection
        failures are expected when NATS is not running locally.
        """
        await self._open_ssh_tunnel()
        retry_delay = 1
        max_retries = 3
        for attempt in range(max_retries):
            try:

                async def _noop_error_cb(e):
                    # Not covered: invoked by the NATS client library on connection
                    # error — never called from application code directly. Requires a
                    # live NATS server to trigger. See docs/test/unit-tests.md §Intentionally
                    # Uncovered Lines.
                    pass

                self.nc = await asyncio.wait_for(
                    nats.connect(
                        servers=self.servers,
                        max_reconnect_attempts=1,
                        error_cb=_noop_error_cb,
                    ),
                    timeout=self._CONNECT_DEADLINE_SECS,
                )
                self.js = self.nc.jetstream()
                return
            except (NoServersError, TimeoutError, OSError):
                if attempt == max_retries - 1:
                    return  # self.nc remains None -- callers must check
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)

    async def _ensure_stream(self, subject: str) -> bool:
        """Ensure the JetStream stream for a subject exists. Returns True if JS is available."""
        if not self.js:
            return False
        stream_name = self._stream_for_subject(subject)
        if not stream_name or stream_name in self._streams_ensured:
            return bool(stream_name)
        try:
            await self.js.find_stream_name_by_subject(subject)
            self._streams_ensured.add(stream_name)
        except Exception:  # lookup failure falls through to create-stream
            try:
                subjects = STREAM_CONFIG.get(stream_name, [subject])
                await self.js.add_stream(name=stream_name, subjects=subjects)
                self._streams_ensured.add(stream_name)
            except Exception:  # stream unavailable, caller falls back
                return False
        return True

    @staticmethod
    def _stream_for_subject(subject: str) -> str | None:
        """Map a subject to its stream name."""
        for stream_name, patterns in STREAM_CONFIG.items():
            for pattern in patterns:
                prefix = pattern.replace(".>", ".")
                if subject.startswith(prefix) or subject == pattern.replace(".>", ""):
                    return stream_name
        return None

    async def _publish(self, subject: str, payload: dict, use_jetstream: bool = True) -> bool:
        """Connect if needed and publish. Returns True on success, False if NATS unavailable."""
        if not self.nc:
            await self.connect()
        if not self.nc:
            return False
        data = json.dumps(payload).encode()
        if use_jetstream and self.js:
            try:
                await self._ensure_stream(subject)
                await self.js.publish(subject, data)
                return True
            except Exception:  # JetStream publish failed, fall back to core NATS
                pass
        # Fallback to core NATS
        await self.nc.publish(subject, data)
        return True

    async def publish_heartbeat(self, session_id: str, data: dict) -> bool:
        """Publishes a worker heartbeat. Fire-and-forget (no ACK required)."""
        subject = f"fleet.worker.{session_id}.heartbeat"
        return await self._publish(subject, data)

    async def publish_event(self, session_id: str, event_type: str, data: dict | None = None) -> bool:
        """Publishes a fleet event."""
        subject = f"fleet.worker.{session_id}.event"
        payload = {"type": event_type, "data": data or {}, "ts": time.time()}
        return await self._publish(subject, payload)

    async def publish(self, subject: str, data: dict | None = None) -> bool:
        """Publishes to an arbitrary subject via JetStream if available."""
        return await self._publish(subject, data or {})

    async def subscribe(self, subject: str, callback) -> None:
        """Subscribe to subject and invoke callback(data: dict) for each message.

        Blocks until cancelled (KeyboardInterrupt or asyncio.CancelledError).
        If NATS is unavailable the method returns immediately without error.
        """
        if not self.nc:
            await self.connect()
        if not self.nc:
            return

        async def _handler(msg):
            try:
                data = json.loads(msg.data.decode())
            except Exception:  # malformed payload becomes an empty dict
                data = {}
            await callback(data)

        await self.nc.subscribe(subject, cb=_handler)
        try:
            while True:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

    async def subscribe_durable(self, subject: str, consumer_name: str, callback) -> None:
        """Subscribe via JetStream durable consumer. Messages require ACK.

        Blocks until cancelled. Falls back to core subscribe if JetStream unavailable.
        """
        if not self.nc:
            await self.connect()
        if not self.nc:
            return None

        if not self.js:
            return await self.subscribe(subject, callback)

        try:
            await self._ensure_stream(subject)
        except Exception:  # stream setup failed, fall back to core subscribe
            return await self.subscribe(subject, callback)

        async def _handler(msg):
            try:
                data = json.loads(msg.data.decode())
            except Exception:  # malformed payload becomes an empty dict
                data = {}
            await callback(data)
            await msg.ack()

        try:
            await self.js.subscribe(subject, durable=consumer_name, cb=_handler)
        except Exception:  # durable subscribe failed, fall back to core subscribe
            return await self.subscribe(subject, callback)

        try:
            while True:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

    async def close(self):
        """Closes the connection and any SSH tunnel opened for Mac."""
        if self.nc:
            await self.nc.close()
        if self._tunnel_proc:
            # The ssh -f foreground parent may already be reaped (see
            # _reap_tunnel_parent); terminating a dead process must not raise.
            with contextlib.suppress(ProcessLookupError, OSError):
                self._tunnel_proc.terminate()
            self._tunnel_proc = None
