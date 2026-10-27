"""One-shot launch bypasses the wrapper but still refreshes before handing off."""

import json
import shutil

from test_session_wake_watcher import launch  # noqa: F401

from ai_cli import config


def test_given_configured_plugin_when_one_shot_launcher_starts_then_update_precedes_tmux_start(
    launch,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    plugin = "example-plugin@example-marketplace"
    path = tmp_path / "config.toml"
    path.write_text("[launch]\nrefresh_plugins = " + json.dumps([plugin]) + "\n")
    monkeypatch.setattr(config, "config_file_text", lambda: str(path))
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: "claude" if name == "claude" else which(name))
    launch.invoke(once=True)
    calls = [argv for argv, _ in launch.calls]
    assert calls.index(["claude", "plugin", "marketplace", "update", "example-marketplace"]) < calls.index(
        ["claude", "plugin", "update", plugin]
    )
    update = calls.index(["claude", "plugin", "update", plugin])
    start = next(index for index, argv in enumerate(calls) if argv[:2] == ["tmux", "new-session"])
    assert update < start
