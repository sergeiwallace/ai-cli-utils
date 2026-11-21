"""Unified notification delivery — Notifier class and per-channel drivers.

Provides a single entry point for firing alerts across Discord (or Slack)
webhooks and OS-native desktop alerts.
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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import output as _out
from .output import Untagged


@dataclass
class NotificationResult:
    """Delivery result for a single channel."""

    channel: str  # "discord" | "os"
    success: bool
    status_code: int | None = None
    error: str | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _validate_https_endpoint(url: str) -> None:
    """Reject malformed or non-TLS endpoints before sending notification data."""
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname.isascii()
    ):
        raise ValueError("Notification endpoint must be HTTPS with an ASCII hostname and no userinfo")


def _open_no_redirect(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


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
            primary = self._fire_parallel(title, body, priority, active)
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

        if requested is not None:
            return [c for c in requested if c in available]
        return available

    def _fire_parallel(self, title: str, body: str, priority: str, channels: list[str]) -> list[NotificationResult]:
        discord_url = os.environ.get("DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL", "") or self._cfg.get(
            "discord", {}
        ).get("webhook_url", "")

        results: list[NotificationResult] = []
        with ThreadPoolExecutor(max_workers=max(len(channels), 1)) as pool:
            futures: dict = {}
            for ch in channels:
                if ch == "discord":
                    futures[pool.submit(_send_discord, discord_url, title, body, priority)] = ch

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

        return [
            {
                "name": "discord",
                "enabled": bool(discord_url) and self._cfg.get("discord", {}).get("enabled", True),
                "credentials": {"webhook_url": "set" if discord_url else "missing"},
            },
            {
                "name": "os",
                "enabled": self._cfg.get("os_fallback", True),
                "credentials": {},
            },
        ]


def _send_discord(webhook_url: str, title: str, body: str, priority: str) -> NotificationResult:
    """POST to a Discord or Slack incoming webhook."""
    try:
        _validate_https_endpoint(webhook_url)
    except ValueError as exc:
        return NotificationResult(channel="discord", success=False, error=str(exc))
    hostname = urlsplit(webhook_url).hostname
    assert hostname is not None  # _validate_https_endpoint() rejects URLs without a hostname.
    hostname = hostname.lower()
    is_discord = hostname in {"discord.com", "discordapp.com"} or hostname.endswith((".discord.com", ".discordapp.com"))
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
        resp = _open_no_redirect(req, timeout=5)
        return NotificationResult(channel="discord", success=True, status_code=resp.status)
    except urllib.error.HTTPError as exc:
        return NotificationResult(channel="discord", success=False, status_code=exc.code, error=str(exc))
    except Exception as exc:
        return NotificationResult(channel="discord", success=False, error=str(exc))


def _send_os_notification(title: str, body: str) -> NotificationResult:
    """Fire an OS-native desktop notification.

    - macOS: osascript
    - Windows: plyer (optional extra ``[notify-win]``); silently degrades if not installed
    - Linux/other: notify-send
    """
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run",
                    title,
                    body,
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        elif sys.platform == "win32":
            try:
                from plyer import notification as _plyer_notify

                _plyer_notify.notify(title=title, message=body, app_name="ai-cli-utils")
            except ImportError:
                pass  # [notify-win] extra not installed — silently degrade
        else:
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=5, check=False)
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
        _out.raw(Untagged.TERMINAL_CONTROL, f"\033]1337;SetBadgeFormat={encoded}\007", err=True, nl=False)

    def notify(self, msg: str):
        """Update iTerm2 badge to reflect completion state."""
        self.emit_badge(msg)
