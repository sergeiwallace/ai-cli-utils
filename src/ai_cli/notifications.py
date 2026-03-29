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

    def emit_osc9(self, msg: str):
        r"""Writes OSC 9 escape sequence to stdout.

        Supported by iTerm2 and VS Code terminal to fire system notifications.
        Format: \e]9;{msg}\a
        """
        if self._is_suppressed():
            return

        # Use direct write to stderr to avoid being swallowed by some redirections
        sys.stderr.write(f"\033]9;{msg}\007")
        sys.stderr.flush()

    def emit_iterm2(self, msg: str):
        """Writes iTerm2-specific notification string.

        Requires a corresponding regex trigger in iTerm2 profile: ^NOTIFY: (.*)
        """
        if self._is_suppressed():
            return

        sys.stderr.write(f"NOTIFY: {msg}\n")
        sys.stderr.flush()

    def notify(self, msg: str):
        """Fires all enabled notification methods."""
        self.emit_osc9(msg)
        self.emit_iterm2(msg)
