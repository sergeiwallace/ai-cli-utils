import asyncio
import json
import os
import socket
import subprocess
import time
import nats
from nats.errors import TimeoutError, NoServersError

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
        # Accept either AI_CLI_HOST=mac or HUMANWARE_HOST=mac (HUMANWARE_HOST is set
        # in ~/.zshenv for zsh sessions; AI_CLI_HOST is the legacy name in ~/.bashrc).
        _host_id = os.environ.get("AI_CLI_HOST") or os.environ.get("HUMANWARE_HOST", "")
        if _host_id != "mac":
            return
        try:
            with socket.create_connection(("localhost", 4222), timeout=1):
                return  # already reachable
        except OSError:
            pass
        # Use configured remote host so Tailscale/direct-IP preference is respected.
        try:
            from .main import load_config as _load_config

            _cfg = _load_config()
            _remote = _cfg.get("remote", {})
            _user = _remote.get("user", "")
            _host = _remote.get("host", "")
            _port = str(_remote.get("port", 22))
            _identity = _remote.get("identity_file", "")
        except Exception:
            _user, _host, _port, _identity = "", "", "22", ""
        if not _user or not _host:
            return  # no remote configured — skip tunnel
        # Use vpn_host (direct IP) only when VPN is active — Tailscale is
        # unreachable under VPN so the tunnel must go via the direct IP instead.
        try:
            from .main import _is_vpn_active as _vpn_check

            _tunnel_host = (_remote.get("vpn_host", "") or _host) if _vpn_check() else _host
        except Exception:
            _tunnel_host = _host
        ssh_cmd = ["ssh", "-fNL", "4222:localhost:4222", "-o", "ConnectTimeout=5"]
        if _identity:
            ssh_cmd += ["-i", _identity]
        ssh_cmd += ["-p", _port, f"{_user}@{_tunnel_host}"]
        self._tunnel_proc = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                with socket.create_connection(("localhost", 4222), timeout=1):
                    return
            except OSError:
                pass

    async def connect(self):
        """Connects to NATS with bounded exponential backoff. Gives up after 3 attempts.

        Passes max_reconnect_attempts=0 to the nats library to disable its own internal
        retry loop (default 60 attempts × 2s = 2-minute hang when NATS is unavailable).
        Our own retry loop handles backoff instead.

        error_cb silences the nats library's verbose per-attempt tracebacks — connection
        failures are expected when NATS is not running locally (e.g. on Mac).
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

                self.nc = await nats.connect(
                    servers=self.servers,
                    max_reconnect_attempts=0,
                    error_cb=_noop_error_cb,
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
        except Exception:
            try:
                subjects = STREAM_CONFIG.get(stream_name, [subject])
                await self.js.add_stream(name=stream_name, subjects=subjects)
                self._streams_ensured.add(stream_name)
            except Exception:
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
            except Exception:
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
            except Exception:
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
            return

        if not self.js:
            return await self.subscribe(subject, callback)

        try:
            await self._ensure_stream(subject)
        except Exception:
            return await self.subscribe(subject, callback)

        async def _handler(msg):
            try:
                data = json.loads(msg.data.decode())
            except Exception:
                data = {}
            await callback(data)
            await msg.ack()

        try:
            await self.js.subscribe(subject, durable=consumer_name, cb=_handler)
        except Exception:
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
            self._tunnel_proc.terminate()
            self._tunnel_proc = None
