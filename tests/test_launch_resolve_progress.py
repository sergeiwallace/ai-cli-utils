"""The session-resolution step of `ai c` prints each slow sub-step as it starts and ends.

Between "mode: launching inside tmux" and "session: resolved", a launch refreshes this
host's id through a login shell, reads the name registry from git, fetches it from its
remote and, for a new session, reserves a name there. On a host where every process
spawn is slow, five dry runs measured that gap at 19 to 67 seconds with nothing printed,
and once a dry run sat there for over ten minutes with no way to tell which step it was
in. Each of those sub-steps now prints a start line, a heartbeat while it runs, and an
end line with its elapsed time.

The launch runs through the CLI against a real name registry (a bare git remote). The
reporter's clock is stepped three seconds per reading so every end line is timed.
"""

from __future__ import annotations

import itertools
import re
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli
from name_fleet import HOST, HOST_ALIAS, REPO, build_fleet

from ai_cli import session_naming

_TIMED = re.compile(r"\(\d+\.\ds\)$")

_STEPS = (
    ("refreshing this host's id", f"host id {HOST}"),
    ("reading the name registry", "name registry read"),
    ("fetching the name registry", "name registry fetched"),
)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.chdir(fleet.repo)
    alias_file = tmp_path / session_naming.ALIAS_FILENAME
    alias_file.write_text(f'[aliases]\n{HOST} = "{HOST_ALIAS}"\n', encoding="utf-8")
    monkeypatch.setattr(session_naming, "alias_sources", lambda: [alias_file])
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    clock = itertools.count(start=0.0, step=3.0)
    refreshed: list[dict] = []

    with (
        patch("ai_cli.launch_reporter.monotonic", side_effect=lambda: next(clock)),
        patch("ai_cli.host_env.refresh_host_env", side_effect=lambda config: refreshed.append(config)),
        patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
        patch("ai_cli.tmux_setup.formats_expand", return_value=True),
        patch("ai_cli.tmux_setup.probe", return_value=MagicMock(versions_disagree=False)),
        patch("ai_cli.tmux_setup.report_lines", return_value=[]),
        patch(
            "ai_cli.compact_transport.resolve",
            return_value=MagicMock(must_refuse=False, platform_limited=False, detail="tmux"),
        ),
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.config.get_current_project_name", return_value=REPO),
        patch("ai_cli.direnv_setup.ensure_direnv"),
        patch("ai_cli.session.create_worktree", side_effect=AssertionError("dry run created a worktree")),
        patch("os.execvp", side_effect=AssertionError("dry run executed")),
    ):
        yield lambda *argv: (*run_cli(["ai", "c", *argv, "--project-prefix", REPO]), refreshed)


#: The exit code of the stop placed at the first call after naming, before anything is created.
_NAMED = 97


def _assert_timed_steps(err: str, steps) -> list[str]:
    lines = err.splitlines()
    for start, end in steps:
        started = [i for i, line in enumerate(lines) if line.rstrip().endswith(f"session: {start}")]
        ended = [i for i, line in enumerate(lines) if f"session: {end}" in line and _TIMED.search(line.rstrip())]
        assert len(started) == 1, f"no start line for {start!r}:\n{err}"
        assert len(ended) == 1, f"no timed end line for {start!r}:\n{err}"
        assert started[0] < ended[0]
    return lines


def test_given_a_new_session_when_dry_run_resolves_it_then_each_slow_sub_step_prints_start_and_timed_end(launch):
    code, out, err, refreshed = launch("-d")

    assert code == 0, out + err
    assert len(refreshed) == 1, "the host id is refreshed once per launch"
    lines = _assert_timed_steps(err, _STEPS)
    planned = next(i for i, line in enumerate(lines) if "session: planned" in line)
    assert all(i < planned for i, line in enumerate(lines) if "name registry" in line)
    assert any(line.rstrip().endswith("worktree: looking up the session worktree") for line in lines), err
    assert any("worktree: no registered worktree" in line and _TIMED.search(line.rstrip()) for line in lines), err


def test_given_a_new_session_when_a_real_launch_reserves_its_name_then_the_reservation_prints_start_and_timed_end(
    launch,
):
    with (
        patch("ai_cli.main._ensure_dolt_server"),
        patch("ai_cli.main._ensure_wake_sidecar"),
        patch("ai_cli.main._sync_launch_client", return_value=None),
        patch("ai_cli.main._tunnel._ensure_nats_tunnel"),
        patch("ai_cli.main._process_manager._start_stale_session_reaper_for_launch"),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.main._finish_recorded_migrations", side_effect=SystemExit(_NAMED)),
    ):
        code, out, err, _refreshed = launch("-b")

    assert code == _NAMED, out + err
    lines = _assert_timed_steps(err, (*_STEPS, ("reserving a name in the name registry", "name reserved")))
    reserved = next(i for i, line in enumerate(lines) if "session: name reserved" in line)
    resolved = next(i for i, line in enumerate(lines) if "session: resolved" in line)
    assert reserved < resolved
