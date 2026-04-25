"""Unified notification delivery — Notifier class and per-channel drivers.

Provides a single entry point for firing alerts across Discord webhooks,
ntfy push notifications, and OS-native desktop alerts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass


@dataclass
class NotificationResult:
    """Delivery result for a single channel."""

    channel: str  # "discord" | "ntfy" | "os"
    success: bool
    status_code: int | None = None
    error: str | None = None


class Notifier:
    """Unified notification delivery.

    Fires all configured channels in parallel. OS fallback fires when all
    primaries fail or when no primary channels are configured.

    Config sources (layered, env vars take precedence):
      - config.toml [notifications] section — non-secret defaults, enabled flags
      - Environment variables — credentials injected by Doppler/direnv
    """

    def __init__(self, channels_config: dict | None = None) -> None:
        """Load channel config from config.toml + env vars, or accept an override dict."""
        self._cfg: dict = channels_config if channels_config is not None else self._load_config()

    @staticmethod
    def _load_config() -> dict:
        try:
            from .config import load_config

            return load_config().get("notifications", {})
        except Exception:
            return {}

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: list[str] | None = None,
        channels: list[str] | None = None,
        source: str = "",
    ) -> list[NotificationResult]:
        """Fire all enabled channels in parallel. Returns per-channel results. Never raises."""
        _tags = tags or []
        active = self._resolve_channels(channels)

        results: list[NotificationResult] = []

        if active:
            primary = self._fire_parallel(title, body, priority, _tags, active)
            results.extend(primary)

        primary_results = [r for r in results if r.channel != "os"]
        all_primaries_failed = not active or all(not r.success for r in primary_results)
        if all_primaries_failed and self._cfg.get("os_fallback", True):
            results.append(_send_os_notification(title, body))

        self._log(title, body, priority, _tags, results, source)
        return results

    def _resolve_channels(self, requested: list[str] | None) -> list[str]:
        """Return list of enabled, configured channel names."""
        available: list[str] = []

        discord_url = os.environ.get("DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL", "") or self._cfg.get(
            "discord", {}
        ).get("webhook_url", "")
        if discord_url and self._cfg.get("discord", {}).get("enabled", True):
            available.append("discord")

        ntfy_base = os.environ.get("NTFY_BASE_URL", "") or self._cfg.get("ntfy", {}).get("base_url", "")
        ntfy_topic = os.environ.get("NTFY_TOPIC", "") or self._cfg.get("ntfy", {}).get("topic", "")
        if ntfy_base and ntfy_topic and self._cfg.get("ntfy", {}).get("enabled", True):
            available.append("ntfy")

        if requested is not None:
            return [c for c in requested if c in available]
        return available

    def _fire_parallel(
        self, title: str, body: str, priority: str, tags: list[str], channels: list[str]
    ) -> list[NotificationResult]:
        discord_url = os.environ.get("DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL", "") or self._cfg.get(
            "discord", {}
        ).get("webhook_url", "")
        ntfy_base = os.environ.get("NTFY_BASE_URL", "") or self._cfg.get("ntfy", {}).get("base_url", "")
        ntfy_topic = os.environ.get("NTFY_TOPIC", "") or self._cfg.get("ntfy", {}).get("topic", "")
        ntfy_token = os.environ.get("NTFY_TOKEN", "") or self._cfg.get("ntfy", {}).get("token", "")
        ntfy_url = f"{ntfy_base.rstrip('/')}/{ntfy_topic}" if ntfy_base and ntfy_topic else ""

        results: list[NotificationResult] = []
        with ThreadPoolExecutor(max_workers=max(len(channels), 1)) as pool:
            futures: dict = {}
            for ch in channels:
                if ch == "discord":
                    futures[pool.submit(_send_discord, discord_url, title, body, priority)] = ch
                elif ch == "ntfy":
                    futures[pool.submit(_send_ntfy, ntfy_url, ntfy_token, title, body, priority, tags)] = ch

            for fut, ch_name in futures.items():
                try:
                    results.append(fut.result(timeout=10))
                except Exception as exc:
                    results.append(NotificationResult(channel=ch_name, success=False, error=str(exc)))

        return results

    def _log(
        self,
        title: str,
        body: str,
        priority: str,
        tags: list[str],
        results: list[NotificationResult],
        source: str,
    ) -> None:
        try:
            from .quota_db import log_notification

            log_notification(
                source=source,
                title=title,
                body=body,
                priority=priority,
                tags=tags,
                channels_attempted=[r.channel for r in results],
                channels_succeeded=[r.channel for r in results if r.success],
                channels_failed=[r.channel for r in results if not r.success],
            )
        except Exception:
            pass

    def list_channels(self) -> list[dict]:
        """Return channel audit records — name, enabled state, credential presence (not values)."""
        discord_url = os.environ.get("DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL", "") or self._cfg.get(
            "discord", {}
        ).get("webhook_url", "")
        ntfy_base = os.environ.get("NTFY_BASE_URL", "") or self._cfg.get("ntfy", {}).get("base_url", "")
        ntfy_topic = os.environ.get("NTFY_TOPIC", "") or self._cfg.get("ntfy", {}).get("topic", "")
        ntfy_token = os.environ.get("NTFY_TOKEN", "") or self._cfg.get("ntfy", {}).get("token", "")

        return [
            {
                "name": "discord",
                "enabled": bool(discord_url) and self._cfg.get("discord", {}).get("enabled", True),
                "credentials": {"webhook_url": "set" if discord_url else "missing"},
            },
            {
                "name": "ntfy",
                "enabled": bool(ntfy_base and ntfy_topic) and self._cfg.get("ntfy", {}).get("enabled", True),
                "credentials": {
                    "base_url": "set" if ntfy_base else "missing",
                    "topic": "set" if ntfy_topic else "missing",
                    "token": "set" if ntfy_token else "not configured",
                },
            },
            {
                "name": "os",
                "enabled": self._cfg.get("os_fallback", True),
                "credentials": {},
            },
        ]


def _send_discord(webhook_url: str, title: str, body: str, priority: str) -> NotificationResult:
    """POST to a Discord or Slack incoming webhook."""
    is_discord = "discord.com" in webhook_url or "discordapp.com" in webhook_url
    emoji = "\U0001f6a8" if priority == "urgent" else "⚠️" if priority == "high" else "ℹ️"
    text = f"{emoji} **{title}**\n{body}"
    payload = json.dumps({"content": text} if is_discord else {"text": text}).encode()
    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "ai-cli-utils/1.0"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        return NotificationResult(channel="discord", success=True, status_code=resp.status)
    except urllib.error.HTTPError as exc:
        return NotificationResult(channel="discord", success=False, status_code=exc.code, error=str(exc))
    except Exception as exc:
        return NotificationResult(channel="discord", success=False, error=str(exc))


def _send_ntfy(ntfy_url: str, token: str, title: str, body: str, priority: str, tags: list[str]) -> NotificationResult:
    """POST to a self-hosted or public ntfy server."""
    tags_str = ",".join(tags) if tags else ("rotating_light" if priority == "urgent" else "warning")
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
        "Tags": tags_str,
        "Content-Type": "text/plain",
        "User-Agent": "ai-cli-utils/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(ntfy_url, data=body.encode(), headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=5)
        return NotificationResult(channel="ntfy", success=True, status_code=resp.status)
    except urllib.error.HTTPError as exc:
        return NotificationResult(channel="ntfy", success=False, status_code=exc.code, error=str(exc))
    except Exception as exc:
        return NotificationResult(channel="ntfy", success=False, error=str(exc))


def _send_os_notification(title: str, body: str) -> NotificationResult:
    """Fire an OS-native desktop notification.

    - macOS: osascript
    - Windows: plyer (optional extra ``[notify-win]``); silently degrades if not installed
    - Linux/other: notify-send
    """
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
                capture_output=True,
                timeout=5,
            )
        elif sys.platform == "win32":
            try:
                from plyer import notification as _plyer_notify

                _plyer_notify.notify(title=title, message=body, app_name="ai-cli-utils")
            except ImportError:
                pass  # [notify-win] extra not installed — silently degrade
        else:
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=5)
        return NotificationResult(channel="os", success=True)
    except Exception as exc:
        return NotificationResult(channel="os", success=False, error=str(exc))


class NotificationManager:
    """Handles OS-level alerting via terminal escape sequences."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.lock_file = Path(tempfile.gettempdir()) / f"ai-batch-{session_id}.lock"

    def _is_suppressed(self) -> bool:
        return self.lock_file.exists()

    def emit_badge(self, msg: str):
        """Update iTerm2 badge to show completion status."""
        if self._is_suppressed():
            return
        session_num = os.environ.get("ITERM2_SESSION_NUM", "")
        if not session_num:
            return
        badge = f"✓ {msg}"
        encoded = __import__("base64").b64encode(badge.encode()).decode()
        sys.stderr.write(f"\033]1337;SetBadgeFormat={encoded}\007")
        sys.stderr.flush()

    def notify(self, msg: str):
        """Update iTerm2 badge to reflect completion state."""
        self.emit_badge(msg)
