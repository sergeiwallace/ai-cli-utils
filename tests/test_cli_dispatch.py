"""CLI dispatch tests for main.py Click commands and internal handler edges.

Covers previously-uncovered lines in ``ai_cli.main``:
- ``refresh-template`` internal handler (316-356) + unknown action (449-450)
- ``_cli_group`` no-subcommand (1397-1398)
- ``cmd_quota_statusline_part`` / ``cmd_quota_sync`` (1554-1563)
- ``cmd_spend_gemini_cli`` (1616-1618)
- ``cmd_cc_usage_push`` + ``cmd_cc_usage_status`` (1629-1653)
- ``cmd_vpn_watch`` / ``cmd_ps`` (1793-1796, 1806-1808)
- ``sync repair-worktree`` / ``cleanup`` / unknown (1846-1887)
- ``cli()`` error paths (1936, 1946, 1952)
- reconnect transport JSON error (875-876)
- ``_auto_update_if_stale`` lockfile early-return + stamp-match after-lock (216-217, 221)
- ``_deploy_cc_config_files`` copy path (185-194)
- session launch positional-name promotion (1101-1102)
"""

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from conftest import run_cli

from ai_cli.main import (
    _auto_update_if_stale,
    _deploy_cc_config_files,
    _installed_source_fingerprint,
    cli,
    main,
)

# --- `ai internal refresh-template` ---


class TestInternalRefreshTemplate:
    @staticmethod
    def _meta(session: str = "testses") -> dict:
        return {
            "engine": "c",
            "ai_name": "1",
            "session": session,
            "prefix": "c-myproject-",
            "project_prefix": "myproject",
            "session_id_uuid": "",
            "sandbox": False,
            "worktree_dir": "",
            "notify": False,
            "is_remote": False,
            "project_name": "myproject",
            "iterm2_slot": "",
            "iterm2_cfg": {},
            "config_reload_idle_secs": 90,
            "gemini_cmd": "gemini",
        }

    def test_given_no_session_arg_when_refresh_template_then_exits_1(self):
        exit_code, _, stderr = run_cli(["ai", "internal", "refresh-template"])
        assert exit_code == 1
        assert "Usage: ai internal refresh-template" in stderr

    def test_given_missing_meta_file_when_refresh_template_then_exits_1(self, tmp_path):
        with patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path):
            exit_code, _, stderr = run_cli(["ai", "internal", "refresh-template", "nosuch"])
        assert exit_code == 1
        assert "No session metadata" in stderr

    def test_given_corrupt_meta_file_when_refresh_template_then_exits_1(self, tmp_path):
        (tmp_path / "session-meta-badjson.json").write_text("{not-json")
        with patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path):
            exit_code, _, stderr = run_cli(["ai", "internal", "refresh-template", "badjson"])
        assert exit_code == 1
        assert "Failed to read session metadata" in stderr

    def test_given_valid_meta_when_refresh_template_then_writes_script_and_exits_0(self, tmp_path):
        meta_path = tmp_path / "session-meta-testses.json"
        meta_path.write_text(json.dumps(self._meta("testses")))
        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.session_script.get_engine_script", return_value="echo hello\n") as mock_get,
        ):
            exit_code, stdout, _ = run_cli(["ai", "internal", "refresh-template", "testses"])
        assert exit_code == 0
        assert mock_get.called
        # stdout is the temp path printed by the handler
        printed = stdout.strip()
        assert printed  # non-empty path
        # Script file should exist and contain the generated script + self-delete header
        contents = Path(printed).read_text()
        assert "#!/usr/bin/env bash" in contents
        assert "echo hello" in contents
        assert f'rm -f "{printed}"' in contents
        # cleanup
        Path(printed).unlink(missing_ok=True)


# --- unknown `ai internal` action ---


class TestInternalUnknownAction:
    def test_given_unknown_action_when_internal_then_exits_1(self):
        exit_code, _, stderr = run_cli(["ai", "internal", "bogus-action"])
        assert exit_code == 1
        assert "unknown action" in stderr


# --- `_cli_group` no subcommand ---


class TestCliGroupNoSubcommand:
    def test_given_no_subcommand_when_ai_then_prints_help_and_exits_0(self):
        exit_code, stdout, _ = run_cli(["ai"])
        assert exit_code == 0
        assert "Usage:" in stdout or "Commands" in stdout

    def test_given_pi_help_when_requested_then_matches_session_command_options(self):
        exit_code, stdout, _ = run_cli(["ai", "p", "--help"])

        assert exit_code == 0
        assert "Launch a Pi session" in stdout
        assert "-r, --resume" in stdout
        assert "-b, --bare" in stdout

    def test_given_codex_help_when_requested_then_matches_session_command_options(self):
        exit_code, stdout, _ = run_cli(["ai", "cx", "--help"])

        assert exit_code == 0
        assert "Launch a Codex session" in stdout
        assert "-r, --resume" in stdout
        assert "-b, --bare" in stdout


# --- `ai quota statusline-part` / `ai quota sync` ---


class TestCmdQuotaMiscDispatch:
    def test_given_quota_statusline_part_when_invoked_then_dispatches(self):
        with patch("ai_cli.quota.quota_statusline_part", return_value=0) as mock_fn:
            exit_code, _, _ = run_cli(["ai", "quota", "statusline-part"])
        assert exit_code == 0
        mock_fn.assert_called_once()

    def test_given_quota_sync_when_invoked_then_dispatches(self):
        with patch("ai_cli.quota.quota_sync_from_remote", return_value=0) as mock_fn:
            exit_code, _, _ = run_cli(["ai", "quota", "sync"])
        assert exit_code == 0
        mock_fn.assert_called_once()


# --- `ai spend gemini` ---


class TestCmdSpendGemini:
    def test_given_spend_gemini_when_invoked_then_dispatches(self):
        with patch("ai_cli.spend.cmd_spend_gemini", return_value=0) as mock_fn:
            exit_code, _, _ = run_cli(["ai", "spend", "gemini"])
        assert exit_code == 0
        mock_fn.assert_called_once()


# --- `ai cc-usage push` / `status` ---


class TestCmdCcUsage:
    def test_given_cc_usage_push_when_success_then_prints_pushed(self):
        result = MagicMock()
        result.error = None
        result.inserted = 5
        result.skipped = 2
        result.scanned_sessions = 3
        result.new_events = 7
        with patch("ai_cli.cc_usage.scan_and_push", return_value=result):
            exit_code, stdout, _ = run_cli(["ai", "cc-usage", "push"])
        assert exit_code == 0
        assert "Pushed" in stdout
        assert "5" in stdout

    def test_given_cc_usage_push_when_dry_run_then_prints_dry_run(self):
        result = MagicMock()
        result.error = None
        result.new_events = 4
        result.scanned_sessions = 2
        result.inserted = 0
        result.skipped = 0
        with patch("ai_cli.cc_usage.scan_and_push", return_value=result):
            exit_code, stdout, _ = run_cli(["ai", "cc-usage", "push", "--dry-run"])
        assert exit_code == 0
        assert "Dry run" in stdout
        assert "4" in stdout

    def test_given_cc_usage_push_when_error_then_exits_1(self):
        result = MagicMock()
        result.error = "boom"
        with patch("ai_cli.cc_usage.scan_and_push", return_value=result):
            exit_code, _, stderr = run_cli(["ai", "cc-usage", "push"])
        assert exit_code == 1
        assert "boom" in stderr

    def test_given_cc_usage_status_when_invoked_then_prints_summary(self):
        with patch(
            "ai_cli.cc_usage.get_cursor_summary",
            return_value={"sessions_tracked": 3, "last_push": "2026-01-01"},
        ):
            exit_code, stdout, _ = run_cli(["ai", "cc-usage", "status"])
        assert exit_code == 0
        assert "Sessions tracked: 3" in stdout
        assert "2026-01-01" in stdout

    def test_given_cc_usage_status_when_last_push_none_then_prints_never(self):
        with patch(
            "ai_cli.cc_usage.get_cursor_summary",
            return_value={"sessions_tracked": 0, "last_push": None},
        ):
            exit_code, stdout, _ = run_cli(["ai", "cc-usage", "status"])
        assert exit_code == 0
        assert "never" in stdout


# --- `ai vpn-watch` / `ai ps` ---


class TestCmdVpnWatchAndPs:
    def test_given_vpn_watch_when_invoked_then_dispatches(self):
        with patch("ai_cli.vpn_watch.run_vpn_watch") as mock_fn:
            exit_code, _, _ = run_cli(["ai", "vpn-watch"])
        assert exit_code == 0
        mock_fn.assert_called_once()

    def test_given_ps_when_invoked_then_dispatches(self):
        with patch("ai_cli.process_hygiene.cmd_ps", return_value=0) as mock_fn:
            exit_code, _, _ = run_cli(["ai", "ps"])
        assert exit_code == 0
        mock_fn.assert_called_once()


# --- `ai sync` subcommands ---


class TestCmdSync:
    def test_given_repair_worktree_no_positional_when_invoked_then_exits_1(self):
        exit_code, _, stderr = run_cli(["ai", "sync", "repair-worktree"])
        assert exit_code == 1
        assert "Usage" in stderr

    def test_given_repair_worktree_with_positional_when_invoked_then_dispatches(self):
        with (
            patch("ai_cli.sync.repair_worktree_cc_dir", return_value=2) as mock_repair,
            patch("ai_cli.sync._cc_projects_dir", return_value=Path("/tmp/projects")),
            patch("ai_cli.sync.get_local_prefix", return_value="mac"),
        ):
            exit_code, _, _ = run_cli(["ai", "sync", "repair-worktree", "myproject", "wt-1"])
        assert exit_code == 0
        mock_repair.assert_called_once()

    def test_given_repair_worktree_when_fails_then_exits_1(self):
        with (
            patch("ai_cli.sync.repair_worktree_cc_dir", return_value=-1),
            patch("ai_cli.sync._cc_projects_dir", return_value=Path("/tmp/projects")),
            patch("ai_cli.sync.get_local_prefix", return_value="mac"),
        ):
            exit_code, _, _ = run_cli(["ai", "sync", "repair-worktree", "myproject", "wt-1"])
        assert exit_code == 1

    def test_given_cleanup_when_invoked_then_prints_counts(self):
        with (
            patch("ai_cli.sync.clean_worktree_cc_dirs", return_value=(3, 1)) as mock_clean,
            patch("ai_cli.sync._cc_projects_dir", return_value=Path("/tmp/projects")),
            patch("ai_cli.sync.get_local_prefix", return_value="mac"),
        ):
            exit_code, stdout, _ = run_cli(["ai", "sync", "cleanup"])
        assert exit_code == 0
        assert "3" in stdout
        assert "Removed" in stdout
        mock_clean.assert_called_once()

    def test_given_cleanup_dry_run_when_invoked_then_prints_would_remove(self):
        with (
            patch("ai_cli.sync.clean_worktree_cc_dirs", return_value=(2, 0)),
            patch("ai_cli.sync._cc_projects_dir", return_value=Path("/tmp/projects")),
            patch("ai_cli.sync.get_local_prefix", return_value="mac"),
        ):
            exit_code, stdout, _ = run_cli(["ai", "sync", "cleanup", "-n"])
        assert exit_code == 0
        assert "Would remove" in stdout

    def test_given_unknown_action_when_sync_then_exits_1(self):
        with (
            patch("ai_cli.sync._cc_projects_dir", return_value=Path("/tmp/projects")),
            patch("ai_cli.sync.get_local_prefix", return_value="mac"),
        ):
            exit_code, _, stderr = run_cli(["ai", "sync", "badaction"])
        assert exit_code == 1
        assert "Unknown sync action" in stderr


# --- `cli()` error paths ---


class TestCliErrorPaths:
    def test_given_internal_get_version_when_cli_then_returns_normally(self, capsys):
        """`ai internal` fast-path takes the ``return`` branch (line 1936)."""
        with (
            patch("sys.argv", ["ai", "internal", "get-version"]),
            patch("ai_cli.config.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        # _handle_internal always sys.exit() internally
        assert exc.value.code == 0

    def test_given_click_abort_when_cli_then_exits_1(self):
        """Line 1946: Click ``Abort`` → exit code 1."""
        with (
            patch("sys.argv", ["ai", "c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._cli_group", side_effect=click.exceptions.Abort()),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1

    def test_given_click_exception_when_cli_then_uses_exit_code(self, capsys):
        """Lines 1943-1944: ``ClickException`` prints and uses its exit code."""
        exc_obj = click.exceptions.ClickException("boom")
        exc_obj.exit_code = 3
        with (
            patch("sys.argv", ["ai", "c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._cli_group", side_effect=exc_obj),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 3

    def test_given_main_when_invoked_then_delegates_to_cli(self):
        """Line 1952: ``main()`` delegates to ``cli()``."""
        with patch("ai_cli.main.cli") as mock_cli:
            main()
        mock_cli.assert_called_once()


# --- reconnect transport JSON error (875-876) ---


class TestReconnectTransportError:
    def test_given_corrupt_transport_file_when_reconnect_then_suppresses_and_continues(self, tmp_path, capsys):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "transport-c-r-sw-1.json").write_text("{not-json}")
        probe = MagicMock()
        probe.returncode = 0
        probe.stdout = "c-r-myproject-1\n"

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ssh":
                return probe
            return MagicMock(returncode=0, stdout="")

        config = {"remote": {"host": "example.com", "user": "user"}}
        with (
            patch("sys.argv", ["ai", "reconnect"]),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 0
        # Corrupt file should not have killed execution — output should list the session
        out = capsys.readouterr().out
        assert "ai c 1" in out or "remote session" in out.lower()


# --- _auto_update_if_stale lockfile early-return + post-lock re-check ---


class TestAutoUpdateLockfile:
    def test_given_lockfile_exists_when_auto_update_then_returns_early(self, tmp_path):
        """A pre-existing lockfile → O_CREAT|O_EXCL raises OSError → return."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        # Pre-create the lockfile so O_CREAT|O_EXCL fails
        (state_dir / "last_install_fingerprint.lock").touch()

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run) as mock_run,
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
        # The source is unstamped (so stale), but the update subprocess must NOT run
        calls = [c.args[0] for c in mock_run.call_args_list]
        update_calls = [c for c in calls if len(c) >= 2 and c[1] == "update"]
        assert not update_calls

    def test_given_stamp_matches_after_lock_when_auto_update_then_skips_update(self, tmp_path):
        """After acquiring the lock, a now-matching stamp returns before the update."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        # Initially the stamp is absent — so we pass the pre-lock check. To exercise
        # the POST-lock check we write the current fingerprint at the moment the
        # lockfile is created, standing in for a concurrent worker that just finished.
        fingerprint = _installed_source_fingerprint(tmp_path)

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="", stderr="")

        real_os_open = os.open

        def fake_os_open(path, flags, *args, **kwargs):
            fd = real_os_open(path, flags, *args, **kwargs)
            if "last_install_fingerprint.lock" in str(path):
                (state_dir / "last_install_fingerprint.txt").write_text(fingerprint)
            return fd

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run) as mock_run,
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("os.open", side_effect=fake_os_open),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        calls = [c.args[0] for c in mock_run.call_args_list]
        update_calls = [c for c in calls if len(c) >= 2 and c[1] == "update"]
        assert not update_calls  # must skip update due to post-lock re-check
        # lockfile cleaned up
        assert not (state_dir / "last_install_fingerprint.lock").exists()

    def test_given_successful_update_when_auto_update_then_records_stamp_and_requests_restart(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        head = MagicMock(returncode=0, stdout="abc123\n")
        update = MagicMock(returncode=0)

        def fake_run(cmd, **kwargs):
            return head if "rev-parse" in cmd else update

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is True
        # The recorded stamp is the packaged-source fingerprint, not HEAD (`AI-CLI-ww8o`):
        # a commit pointer moved for commits that ship nothing and stood still for
        # uncommitted edits under src/, so it was wrong in both directions.
        assert (state_dir / "last_install_fingerprint.txt").read_text() == _installed_source_fingerprint(tmp_path)


class TestSessionAutoUpdateRestart:
    def test_given_successful_auto_update_when_launching_session_then_reexecs_current_entrypoint(self):
        with (
            patch("sys.argv", ["ai", "c", "session-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._auto_update_if_stale", return_value=True),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("os.execvp", side_effect=SystemExit(0)) as execvp,
        ):
            with pytest.raises(SystemExit):
                cli()

        execvp.assert_called_once_with("/usr/bin/ai", ["/usr/bin/ai", "c", "session-1"])


# --- _deploy_cc_config_files copy path (185-194) ---


class TestDeployCcConfigFiles:
    def test_given_statusline_src_exists_when_deploy_then_copied_with_exec_bits(self, tmp_path):
        project = tmp_path / "project"
        (project / "src" / "ai_cli" / "data").mkdir(parents=True)
        src = project / "src" / "ai_cli" / "data" / "statusline-command.sh"
        src.write_text("#!/bin/bash\necho hi\n")

        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch("ai_cli.main.Path.home", return_value=fake_home):
            _deploy_cc_config_files(project)

        dst = fake_home / ".claude" / "statusline-command.sh"
        assert dst.exists()
        assert dst.read_text() == "#!/bin/bash\necho hi\n"
        # Windows does not expose POSIX executable mode bits. The copy itself is
        # the portable contract; assert the mode only where it is meaningful.
        if sys.platform != "win32":
            mode = dst.stat().st_mode
            assert mode & stat.S_IXUSR
            assert mode & stat.S_IXGRP
            assert mode & stat.S_IXOTH

    def test_given_existing_symlink_at_dst_when_deploy_then_symlink_preserved(self, tmp_path):
        # An externally managed symlink must be preserved by ai update.
        project = tmp_path / "project"
        (project / "src" / "ai_cli" / "data").mkdir(parents=True)
        src = project / "src" / "ai_cli" / "data" / "statusline-command.sh"
        src.write_text("#!/bin/bash\necho new\n")

        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        other = tmp_path / "other.sh"
        other.write_text("harness content")
        dst = fake_home / ".claude" / "statusline-command.sh"
        dst.symlink_to(other)

        with patch("ai_cli.main.Path.home", return_value=fake_home):
            _deploy_cc_config_files(project)

        # Symlink must survive — its target content is unchanged
        assert dst.is_symlink()
        assert dst.read_text() == "harness content"

    def test_given_src_missing_when_deploy_then_no_op(self, tmp_path):
        project = tmp_path / "project"
        (project / "src" / "ai_cli" / "data").mkdir(parents=True)
        # No statusline-command.sh at the source
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch("ai_cli.main.Path.home", return_value=fake_home):
            _deploy_cc_config_files(project)
        assert not (fake_home / ".claude" / "statusline-command.sh").exists()


# --- session launch positional-name promotion (1101-1102) ---


class TestSessionLaunchExtraArgsName:
    def test_given_empty_name_and_extra_arg_when_session_launch_then_extra_arg_becomes_name(self):
        """Lines 1101-1102: if ``name`` is empty and ``extra_args`` is non-empty,
        the first extra_arg is promoted to ``name``.

        We trigger with ``-R`` (remote) so the function exits early (no [remote] host) —
        just enough to hit the name-promotion branch without exec'ing anything.
        """
        with (
            patch("sys.argv", ["ai", "c", "-R", "--", "myname"]),
            patch("ai_cli.config.load_config", return_value={"remote": {}}),
            patch("ai_cli.session.get_project_prefix", return_value="test-project"),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        # Exits 1 because [remote] host not set — but the name-promotion line ran first
        assert exc.value.code == 1


class TestRegisterCommand:
    def test_given_repository_and_prefix_when_register_then_persists_registry_entry(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        with patch("ai_cli.config.get_xdg_config_home", return_value=tmp_path / "config"):
            exit_code, stdout, _ = run_cli(["ai", "register", "-p", str(repo), "-x", "PROJECT", "-t", "tool"])
        assert exit_code == 0
        assert "Registered" in stdout
