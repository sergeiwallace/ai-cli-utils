from __future__ import annotations

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
