import os
import sys
from pathlib import Path


class NotificationManager:
    """Handles OS-level alerting via terminal escape sequences."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.lock_file = Path(f"/tmp/ai-batch-{session_id}.lock")

    def _is_suppressed(self) -> bool:
        """Checks if notifications are suppressed by an active batch lock."""
        return self.lock_file.exists()

    def emit_badge(self, msg: str):
        """Update iTerm2 badge to show completion status.

        ntfy is the sole macOS push notification channel. OSC 9 is not used
        because it fires macOS system notifications indistinguishable from ntfy,
        creating duplicate noise.
        """
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
