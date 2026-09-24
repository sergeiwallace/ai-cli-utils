from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_cli.launch_logging import _MAX_LOGS_PER_SESSION, create_launch_log
from ai_cli.launch_reporter import LaunchReporter


def test_given_launch_reporter_when_a_launch_log_is_created_then_it_persists_timestamped_phase_output(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: tmp_path)

    launch_log = create_launch_log("session-1")
    reporter = LaunchReporter(logger=launch_log.logger, stream=launch_log.live_stderr)
    reporter.start(engine="Claude Code", mode="local, tmux")
    reporter.detail("Request", "configuration loaded")

    for handler in launch_log.logger.handlers:
        handler.flush()
    content = launch_log.path.read_text(encoding="utf-8")

    assert launch_log.path.parent == tmp_path / "launch-logs"
    assert launch_log.path.name.startswith("session-1-")
    assert "INFO [launch] Starting Claude Code session: local, tmux" in content
    assert "DEBUG [launch] Request: configuration loaded" in content


def test_given_more_than_retention_limit_when_launch_logs_are_created_then_only_recent_logs_remain(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: tmp_path)

    for _ in range(_MAX_LOGS_PER_SESSION + 2):
        launch_log = create_launch_log("session-1")
        for handler in launch_log.logger.handlers:
            handler.close()
        launch_log.logger.handlers.clear()

    assert len(list((tmp_path / "launch-logs").glob("session-1-*.log"))) == _MAX_LOGS_PER_SESSION


def test_given_a_coarse_clock_when_logs_are_created_then_each_gets_its_own_path(tmp_path, monkeypatch):
    """Two launches inside one clock tick must not resolve to the same file.

    The path embeds a microsecond timestamp, but a timestamp is only as unique as the
    platform clock is fine-grained: `time()` granularity is about 15.6 ms on Windows,
    so a loop like the retention test's collides and several iterations reopen one
    file. That is what made the retention count report fewer files than the limit,
    varying from run to run.

    The frozen clock reproduces that without needing the slow platform: it is the
    coarsest clock there is, so distinctness here cannot come from the timestamp.
    """
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: tmp_path)
    frozen = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)

    class _StoppedClock:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr("ai_cli.launch_logging.datetime", _StoppedClock)

    paths = []
    for _ in range(4):
        launch_log = create_launch_log("session-1")
        paths.append(launch_log.path)
        for handler in launch_log.logger.handlers:
            handler.close()
        launch_log.logger.handlers.clear()

    assert len(set(paths)) == 4, f"the clock alone decided these paths: {paths}"
    assert len(list((tmp_path / "launch-logs").glob("session-1-*.log"))) == 4


def test_given_a_log_that_cannot_be_deleted_when_pruning_then_the_launch_still_proceeds(tmp_path, monkeypatch):
    """A log held open by a live session must be kept, not fatal.

    POSIX unlinks an open file happily, so this was invisible there. Windows raises
    `PermissionError: [WinError 32]`, which `missing_ok=True` does not suppress, so
    `ai c` died inside log setup the moment a prune candidate belonged to a still-
    running session. Simulated by refusing the unlink outright, which is the same
    condition the OS presents, and asserted on both halves: the launch survives, and
    the undeletable log is still there afterwards.
    """
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: tmp_path)

    for _ in range(_MAX_LOGS_PER_SESSION + 1):
        launch_log = create_launch_log("session-1")
        for handler in launch_log.logger.handlers:
            handler.close()
        launch_log.logger.handlers.clear()

    log_dir = tmp_path / "launch-logs"
    locked = sorted(log_dir.glob("session-1-*.log"), key=lambda item: item.stat().st_mtime)[0]
    real_unlink = Path.unlink

    def _refuse_locked(self, *args, **kwargs):
        if self == locked:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _refuse_locked)

    survived = create_launch_log("session-1")

    assert survived.path.exists()
    assert locked.exists(), "a log still in use must be kept rather than reported as pruned"
    for handler in survived.logger.handlers:
        handler.close()
    survived.logger.handlers.clear()
