import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import hold_file_lock, make_iterm2_config, run_cli

from ai_cli.main import (
    _UPDATE_RETRY_BASE_SECONDS,
    _UPDATE_RETRY_MAX_SECONDS,
    _assign_iterm2_color_slot,
    _auto_update_if_stale,
    _bare_engine_command,
    _cmd_tunnel_start,
    _cmd_tunnel_status,
    _cmd_tunnel_stop,
    _find_aicli_project_path,
    _handle_internal,
    _installed_source_fingerprint,
    _load_iterm2_config,
    _migrate_xdg_dir,
    _release_iterm2_color_slot,
    _resolve_is_remote,
    _sweep_stale_iterm2_profiles,
    _update_retry_delay,
    cli,
    get_engine_script,
    get_xdg_cache_home,
    get_xdg_state_home,
    load_config,
)
from ai_cli.native_deps import InstallResult
from ai_cli.session_script import resolve_session_shell

# A thread rendezvous in a test needs a bound only so a broken implementation fails
# instead of hanging the suite forever. The bound must not double as a performance
# assertion: with the suite running under `-n auto` on a loaded machine, a 1-second
# bound made an otherwise-correct handoff fail intermittently. Generous here costs
# nothing when the code is right and still fails fast enough when it is not.
_RENDEZVOUS_TIMEOUT_SECONDS = 60

# --- XDG helpers ---


class TestXdgHelpers:
    """The POSIX branch, pinned via ``sys.platform``, so it is exercised on every OS.

    The base-directory value has to come from ``tmp_path`` rather than a literal
    such as ``/custom/state``: ``Path("/custom/state").is_absolute()`` is False on
    Windows, because a drive-relative path has no drive. ``resolve_base_dir`` then
    correctly discards it per the XDG spec's relative-path rule and returns the
    home fallback, so the test failed on an assertion about the literal while the
    code under test was behaving exactly as specified.
    """

    def test_get_xdg_state_home_when_env_var_set_then_uses_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = get_xdg_state_home()
        assert result == tmp_path / "ai-cli-utils"

    def test_get_xdg_state_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        result = get_xdg_state_home()
        assert result.name == "ai-cli-utils"
        assert ".local/state" in result.as_posix()

    def test_get_xdg_cache_home_when_env_var_set_then_uses_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        result = get_xdg_cache_home()
        assert result == tmp_path / "ai-cli-utils"

    def test_get_xdg_cache_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = get_xdg_cache_home()
        assert result.name == "ai-cli-utils"
        assert ".cache" in result.as_posix()


# --- load_config tests ---


class TestLoadConfig:
    def test_load_config_when_no_config_file_then_creates_default_with_known_keys(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert (config_dir / "config.toml").exists()
        # Default config has [behavior], [worktree], [session], [messaging] sections
        assert "behavior" in result
        assert result["behavior"]["notify_on_exit"] is True
        assert "worktree" in result
        assert result["worktree"]["enabled"] is True
        assert "messaging" in result

    def test_load_config_when_bad_toml_then_returns_empty(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("not valid toml [[[")
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert result == {}


# --- Group 1: _migrate_xdg_dir ---


class TestMigrateXdgDir:
    def test_when_old_exists_and_new_does_not_then_renames(self, tmp_path):
        old_dir = tmp_path / "ai-cli"
        old_dir.mkdir()
        (old_dir / "somefile.txt").write_text("data")
        new_dir = tmp_path / "ai-cli-utils"
        result = _migrate_xdg_dir(old_dir, new_dir)
        assert result == new_dir
        assert new_dir.exists()
        assert not old_dir.exists()
        assert (new_dir / "somefile.txt").read_text() == "data"


# --- Group 2: sweep_stale_iterm2_profiles ---


class TestSweepStaleIterm2Profiles:
    def test_when_profile_dir_does_not_exist_then_returns(self, tmp_path):
        nonexistent = tmp_path / "no-such-dir"
        mock_ig = MagicMock()
        mock_ig._dynamic_profile_dir.return_value = nonexistent
        mock_ig._DYNAMIC_PROFILE_PREFIX = "ai-cli-"
        with patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}):
            _sweep_stale_iterm2_profiles()

    def test_when_exception_occurs_then_suppressed(self):
        mock_ig = MagicMock()
        mock_ig._dynamic_profile_dir.side_effect = RuntimeError("broken")
        with patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}):
            _sweep_stale_iterm2_profiles()


# --- Group 3: _load_iterm2_config ---


class TestLoadIterm2Config:
    def test_when_both_toml_parses_fail_then_returns_empty_dict(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        config_dir.mkdir(parents=True)
        (config_dir / "iterm2.toml").write_text("valid = true")
        with (
            patch("ai_cli.config.get_xdg_config_home", return_value=config_dir),
            patch("tomllib.load", side_effect=Exception("parse fail")),
            patch("tomllib.loads", side_effect=Exception("parse fail")),
        ):
            result = _load_iterm2_config()
        assert result == {}


# --- Group 4: _assign_iterm2_color_slot ---


class TestAssignIterm2ColorSlot:
    def test_when_iterm2_disabled_via_config_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={"iterm2": {"enabled": False}},
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    def test_when_color_disabled_via_config_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={"iterm2": {"enabled": True, "color": {"enabled": False}}},
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    def test_when_palette_empty_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={
                    "iterm2": {
                        "enabled": True,
                        "color": {"enabled": True},
                        "palette": {},
                    }
                },
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    # Group 5: corrupt lease file
    def test_when_lease_file_is_corrupt_json_then_uses_empty_leases(self, tmp_path):
        state_dir = tmp_path / "iterm2"
        state_dir.mkdir(parents=True)
        (state_dir / "color-leases.json").write_text("CORRUPT")
        cfg = make_iterm2_config(palette={"red": "#e74c3c", "blue": "#1e88e5"})
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch("ai_cli.iterm2._iterm2_state_dir", return_value=state_dir),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is not None
        assert len(result) == 6  # hex color without #


# --- Group 6: _release_iterm2_color_slot ---


class TestReleaseIterm2ColorSlot:
    def test_when_lease_file_is_corrupt_then_handles_gracefully(self, tmp_path):
        state_dir = tmp_path / "iterm2"
        state_dir.mkdir(parents=True)
        (state_dir / "color-leases.json").write_text("CORRUPT")
        (state_dir / "color-leases.lock").touch()
        with patch("ai_cli.iterm2._iterm2_state_dir", return_value=state_dir):
            _release_iterm2_color_slot("session-1")
        # Should have written a valid JSON file after handling corrupt input
        data = json.loads((state_dir / "color-leases.json").read_text())
        assert "leases" in data


# --- Group 7: get_engine_script pkg_version exception ---


# The generated session script runs under tmux, which Windows does not provide, and `bash` there
# is the WSL stub; these tests execute the script's blocks in a real shell.
_POSIX_SHELL_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="tmux and bash are POSIX-only")


class TestGetEngineScript:
    def test_when_pkg_version_raises_then_uses_unknown(self):
        with patch(
            "importlib.metadata.version",
            side_effect=Exception("not found"),
        ):
            result = get_engine_script(
                engine="c",
                ai_name="session-1",
                session="c-session-1",
                prefix="c-session-",
                project_prefix="session",
                session_id_uuid="",
                sandbox=False,
                notify=False,
                project_name="myproject",
            )
        assert "unknown" in result

    def test_given_no_worktree_when_generating_script_then_all_agent_starts_reuse_direnv_environment(self):
        script = get_engine_script(
            engine="c",
            ai_name="session-1",
            session="c-session-1",
            prefix="c-session-",
            project_prefix="session",
        )

        assert 'direnv_root="$PWD"' in script
        assert "agent_direnv_initialized=false" in script
        assert '"$(direnv export bash)"' in script
        assert 'eval "$_direnv_exports"' in script
        assert 'direnv exec "$direnv_root" "$@"' not in script
        # Prompt and regular launches each have exact-match and fresh-session paths, and each
        # can also attach to a background session; all go through run_agent.
        assert script.count("run_agent claude") == 6
        assert "--resume" in script
        # Hot-reload must exec an interpreter that exists here — a hardcoded one
        # that does not kills the pane on the session's first self-update.
        _shell = resolve_session_shell()
        assert _shell is not None and os.access(_shell, os.X_OK)
        assert '"$_supervisor_script" --ai-cli-child-body <&0 &' in script
        assert '"$_supervisor_script" --ai-cli-child-body &' not in script
        assert "acquire-generation-lease" in script
        assert "exit 78" in script
        assert f'exec "{_shell}" "$_script_stable_path"' not in script
        assert "starting without the project environment" in script

    def test_given_direnv_cannot_load_when_agent_starts_then_script_continues_without_project_environment(self):
        script = get_engine_script(
            engine="c",
            ai_name="session-1",
            session="c-session-1",
            prefix="c-session-",
            project_prefix="session",
            worktree_dir="/tmp/project-worktree",
        )

        assert "agent_direnv_initialized=false" in script
        assert 'if _direnv_exports="$(direnv export bash)"; then' in script
        assert 'eval "$_direnv_exports"' in script
        # A backgrounded TUI must explicitly retain its controlling terminal.
        # The fallback preserves stdin for non-terminal subprocess launches.
        assert '"$@" </dev/tty &' in script
        assert '"$@" <&0 &' in script
        assert 'wait "$active_agent_pid"' in script
        assert '_ai_emit warning direnv "direnv could not load $direnv_root/.envrc' in script
        assert 'direnv exec "$direnv_root" "$@"' not in script

    def test_given_worktree_when_generating_script_then_direnv_uses_worktree_cwd(self):
        script = get_engine_script(
            engine="g",
            ai_name="session-1",
            session="g-session-1",
            prefix="g-session-",
            project_prefix="session",
            worktree_dir="/tmp/project-worktree",
            is_remote=True,
        )

        assert "cd -- /tmp/project-worktree" in script
        assert script.index('direnv_root="$PWD"') > script.index("cd -- /tmp/project-worktree")
        assert script.count("run_agent gemini") == 4
        assert '"$SHELL"' in script
        assert "exit 79" in script

    def test_given_pi_engine_when_generating_script_then_launches_named_pi_session(self):
        script = get_engine_script(
            engine="p",
            ai_name="myproject-1",
            session="p-myproject-1",
            prefix="p-myproject-",
            project_prefix="myproject",
        )

        assert 'export PI_TMUX_SESSION="$tmux_session"' in script
        assert 'run_agent pi --provider openai-codex --name "$ai_name"' in script
        assert 'run_agent pi --continue --provider openai-codex --name "$ai_name"' in script
        assert '[[ "$engine" == "p" ]] && export CLAUDE_CODE_TASK_LIST_ID' not in script

    def test_given_pi_provider_configured_when_generating_script_then_uses_configured_provider(self):
        # AI-CLI-1rk1: pi's own default provider (google) is silently unready on a
        # typical setup, so every pi launch must carry an explicit --provider.
        script = get_engine_script(
            engine="p",
            ai_name="myproject-1",
            session="p-myproject-1",
            prefix="p-myproject-",
            project_prefix="myproject",
            pi_provider="anthropic",
        )

        assert 'run_agent pi --provider anthropic --name "$ai_name"' in script
        assert 'run_agent pi --continue --provider anthropic --name "$ai_name"' in script

    def test_given_codex_engine_when_generating_script_then_resumes_last_worktree_session(self):
        script = get_engine_script(
            engine="cx",
            ai_name="myproject-1",
            session="cx-myproject-1",
            prefix="cx-myproject-",
            project_prefix="myproject",
        )

        assert 'export CX_TMUX_SESSION="$tmux_session"' in script
        assert "if $first_run; then run_agent codex" in script
        assert "else run_agent codex resume --last" in script

    def _make_script(self, **kwargs):
        defaults = {
            "engine": "c",
            "ai_name": "session-1",
            "session": "c-session-1",
            "prefix": "c-session-",
            "project_prefix": "session",
            "session_id_uuid": "",
            "sandbox": False,
            "notify": False,
            "project_name": "myproject",
        }
        defaults.update(kwargs)
        return get_engine_script(**defaults)

    def test_ps_cron_runs_at_session_start(self):
        """Auto-hygiene must run at session start via 'ai ps cron'."""
        script = self._make_script()
        assert "ai ps cron" in script

    def test_given_exit_signal_when_generating_script_then_never_auto_injects_exit(self):
        """Exit signals and idle-composer detection must not send commands to a live pane."""
        script = self._make_script()
        assert "signal_file" not in script
        assert "cc-exit-" not in script
        assert "gg-exit-" not in script
        assert "send-keys -t \"$tmux_session\" '/exit' C-m" not in script

    def test_given_agent_exit_when_generating_script_then_session_teardown_is_preserved(self):
        """A user-initiated client exit still reaches the normal supervisor teardown path."""
        script = self._make_script()
        assert '(ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &' in script
        assert "exit 77" in script

    def test_given_claude_script_when_generated_then_uses_shared_continue_target_resolver(self):
        """Every Claude Code continuation must first resolve an exact title."""
        script = self._make_script()
        assert 'ai internal resolve-continue-target "$PWD" "$ai_name"' in script
        assert "resolve_status=$?" in script
        assert script.index('ai internal resolve-continue-target "$PWD" "$ai_name"') < script.index(
            'if [[ -f "$prompt_file" ]];'
        )
        assert 'run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name" "$resume_msg"' in script
        assert 'run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name"' in script
        assert 'run_agent claude $claude_perms_flag --name "$ai_name" "$resume_msg"' in script

    def test_given_claude_script_when_generated_then_attaches_to_a_background_session_before_resuming(self):
        """After "Move to background and exit" the conversation runs on in the background: attach, never resume a copy."""
        script = self._make_script()
        lookup = 'resolve-background-target "$PWD" "$ai_name" "$tmux_session"'
        assert lookup in script
        attach = 'run_agent claude attach "$bg_target"'
        assert attach in script
        assert script.index(lookup) < script.index(attach) < script.rindex('--resume "$session_id" --name "$ai_name"')
        # Reset each pass, so a pinned restart never reuses the last target.
        assert script.index('bg_target=""') < script.index(lookup)
        assert 'find "$HOME/.claude/projects' not in script

    def test_given_a_resume_prompt_when_a_background_copy_is_live_then_the_prompt_is_delivered_to_it_not_resumed_beside_it(
        self,
    ):
        """An update restart always carries a resume prompt: it attaches, and delivers the prompt after, never --resume."""
        script = self._make_script()
        lookup = script.index('resolve-background-target "$PWD" "$ai_name" "$tmux_session"')
        # The lookup is not conditional on the absence of a prompt, and learns of one.
        assert '! -f "$prompt_file" ]]; then\n        bg_target' not in script
        assert '_bg_prompted="--prompted"' in script
        delivery = 'ai internal deliver-resume-prompt "$tmux_session" "$bg_target" "$prompt_file"'
        assert delivery in script
        head = script.index('if [[ "$engine" == "c" && -n "$bg_target" && -f "$prompt_file" ]]; then')
        main_branch = script.index('if [[ -f "$prompt_file" ]]; then\n        resume_msg=$(cat "$prompt_file")')
        # The attach branch heads the chain, and the prompt is queued before the attach blocks.
        attach = 'run_agent claude attach "$bg_target"'
        assert lookup < head < script.index(delivery, head) < script.index(attach, head) < main_branch

    @_POSIX_SHELL_ONLY
    def test_given_the_lookup_holds_then_the_script_waits_and_never_reaches_a_launch(self, tmp_path):
        """While a resume would start a second writer the lookup exits 75: the script holds and re-checks, never launching."""
        script = self._make_script()
        start = script.index("    # BEGIN BACKGROUND ATTACH resolution")
        end = script.index("    # END BACKGROUND ATTACH", start)
        block = script[start:end]
        harness = tmp_path / "hold.sh"
        harness.write_text(
            "\n".join(
                [
                    "engine=c; ai_name=x; tmux_session=c-x; resolve_status=0; matched_file=/t/a.jsonl",
                    f'prompt_file={tmp_path}/prompt; echo hello > "$prompt_file"',
                    "warnings=0",
                    # Holds twice, then answers with the live copy. The lookup runs in a
                    # command substitution, so its call count lives in the log file.
                    'ai() { echo "$*" >> ' + str(tmp_path / "ai.log") + "; "
                    "n=$(wc -l < " + str(tmp_path / "ai.log") + "); "
                    "[[ $n -le 2 ]] && return 75; echo aaaa1111; return 0; }",
                    "_ai_emit() { warnings=$((warnings+1)); }",
                    "sleep() { :; }",
                    block,
                    'echo "target=$bg_target warnings=$warnings"',
                ]
            ),
            encoding="utf-8",
        )
        done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
        assert done.stdout.strip() == "target=aaaa1111 warnings=1"
        asked = (tmp_path / "ai.log").read_text(encoding="utf-8").splitlines()
        assert len(asked) == 3
        assert all("--resume-follows" in line and "--prompted" in line for line in asked)

    @_POSIX_SHELL_ONLY
    def test_given_the_lookup_never_clears_then_no_launch_is_reached(self, tmp_path):
        script = self._make_script()
        start = script.index("    # BEGIN BACKGROUND ATTACH resolution")
        end = script.index("    # END BACKGROUND ATTACH", start)
        harness = tmp_path / "forever.sh"
        harness.write_text(
            "\n".join(
                [
                    "engine=c; ai_name=x; tmux_session=c-x; resolve_status=0; matched_file=/t/a.jsonl",
                    f"prompt_file={tmp_path}/none; n=0",
                    "ai() { return 75; }",
                    "_ai_emit() { :; }",
                    "sleep() { n=$((n+1)); [[ $n -lt 4 ]] || exit 99; }",
                    script[start:end],
                    "echo LAUNCH_REACHED",
                ]
            ),
            encoding="utf-8",
        )
        done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 99
        assert "LAUNCH_REACHED" not in done.stdout

    def _run_attach_blocks(self, tmp_path, *, ai_body: str, resume: bool, prompt: bool = True):
        """Run the resolution and parked blocks of the generated script in bash, inside a loop.

        `ai` is a stub with the given body; `date` advances 120 seconds per call and `sleep`
        does nothing, so a hold reaches its limits without waiting. A `break` that parks
        leaves the loop before LAUNCH_REACHED is printed.
        """
        script = self._make_script()
        start = script.index("    # BEGIN BACKGROUND ATTACH resolution")
        parked = script.index("    # BEGIN BACKGROUND ATTACH parked", start)
        end = script.index("    # END BACKGROUND ATTACH", parked) + len("    # END BACKGROUND ATTACH\n")
        harness = tmp_path / "blocks.sh"
        matched = "/t/a.jsonl" if resume else ""
        harness.write_text(
            "\n".join(
                [
                    f"engine=c; ai_name=x; tmux_session=c-x; resolve_status=0; matched_file={matched}",
                    f"prompt_file={tmp_path}/prompt",
                    f'{"echo hello" if prompt else ":"} > "$prompt_file"',
                    f"clock={tmp_path}/clock; echo 1000 > $clock",
                    # `date` and `ai` run in command substitutions, so their state lives in files.
                    "date() { n=$(cat $clock); echo $((n+120)) > $clock; echo $n; }",
                    f"ai() {{ {ai_body}; }}",
                    '_ai_emit() { echo "EMIT: $*"; }',
                    '_ai_persist_exit() { echo "PERSIST: $*"; }',
                    "sleep() { :; }",
                    "for _once in 1; do",
                    script[start:end],
                    "echo LAUNCH_REACHED",
                    "done",
                    'echo "gave_up=$_bg_gave_up target=$bg_target"',
                ]
            ),
            encoding="utf-8",
        )
        done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, done.stderr
        return done.stdout

    @_POSIX_SHELL_ONLY
    def test_given_the_lookup_times_out_then_the_script_parks_and_never_reaches_a_launch(self, tmp_path):
        """Exit 76 means the hold lasted the configured time: say why in the pane, record the exit, stop."""
        out = self._run_attach_blocks(tmp_path, ai_body="return 76", resume=True)

        assert "LAUNCH_REACHED" not in out
        assert "Relaunch parked" in out and "ai c x" in out and "exit 78" in out
        assert "PERSIST: unknown" in out
        assert "gave_up=true" in out

    @_POSIX_SHELL_ONLY
    def test_given_the_lookup_crashes_and_a_resume_follows_then_the_script_holds_then_parks(self, tmp_path):
        """A crashed lookup cannot say there is no live copy: it holds, then parks, never resuming."""
        out = self._run_attach_blocks(tmp_path, ai_body="return 1", resume=True)

        assert "LAUNCH_REACHED" not in out
        assert out.count("Relaunch held") == 1
        assert "Relaunch parked" in out

    @_POSIX_SHELL_ONLY
    def test_given_the_lookup_crashes_and_no_resume_follows_then_a_fresh_launch_goes_ahead(self, tmp_path):
        out = self._run_attach_blocks(tmp_path, ai_body="return 1", resume=False, prompt=False)

        assert "LAUNCH_REACHED" in out
        assert "Relaunch parked" not in out and "gave_up=false target=" in out

    @_POSIX_SHELL_ONLY
    def test_given_working_and_recent_idle_copies_when_relaunching_then_loop_stops_at_configured_attempts(
        self, tmp_path
    ):
        """Drive the generated loop and real resolver with a fake ``claude`` executable."""
        script = self._make_script()
        start = script.index("    # BEGIN BACKGROUND ATTACH resolution")
        parked = script.index("    # BEGIN BACKGROUND ATTACH parked", start)
        end = script.index("    # END BACKGROUND ATTACH", parked) + len("    # END BACKGROUND ATTACH\n")
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        ai = fake_bin / "ai"
        ai.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -c \'from ai_cli.main import cli; cli()\' "$@"\n',
            encoding="utf-8",
        )
        claude = fake_bin / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = agents ] && [ "$2" = --json ]; then\n'
            '  echo agents >> "$CALL_LOG"\n'
            '  printf "%s\\n" "$AGENTS_JSON"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = stop ]; then echo "$2" >> "$STOP_LOG"; exit 0; fi\n'
            "exit 1\n",
            encoding="utf-8",
        )
        ai.chmod(0o755)
        claude.chmod(0o755)
        config_home = tmp_path / "config" / "ai-cli-utils"
        config_home.mkdir(parents=True)
        (config_home / "config.toml").write_text(
            "[machine]\n"
            'host_id = "test-host"\n'
            'os_type = "linux"\n'
            "[relaunch]\n"
            "background_hold_max_attempts = 2\n"
            "background_hold_timeout_seconds = 20\n"
            "background_exit_copy_window_seconds = 30\n",
            encoding="utf-8",
        )
        exit_at = int(time.time())
        live_id = "aaaa1111"
        other_id = "bbbb2222"
        session_name = "myproject-1"
        rows = [
            {
                "id": other_id,
                "name": session_name,
                "cwd": str(tmp_path),
                "kind": "background",
                "state": "working",
            },
            {
                "id": live_id,
                "name": f"{session_name} (2)",
                "cwd": str(tmp_path),
                "kind": "background",
                "status": "idle",
                "state": "blocked",
                "pid": 12345,
                "startedAt": (exit_at - 1) * 1000,
            },
        ]
        harness = tmp_path / "relaunch.sh"
        harness.write_text(
            "\n".join(
                [
                    f'export PATH="{fake_bin}:$PATH"',
                    f'export XDG_CONFIG_HOME="{tmp_path / "config"}"',
                    f'export CALL_LOG="{tmp_path / "agents.log"}"',
                    f'export STOP_LOG="{tmp_path / "stop.log"}"',
                    f"export AGENTS_JSON='{json.dumps(rows)}'",
                    f"engine=c; ai_name={session_name}; tmux_session=c-{session_name}; resolve_status=0",
                    "matched_file=/t/session.jsonl",
                    f"prompt_file={tmp_path}/none",
                    f"_bg_relaunch_started_at={exit_at}",
                    f"clock={tmp_path}/clock; echo 1000 > $clock",
                    "date() { n=$(cat $clock); echo $((n+5)) > $clock; echo $n; }",
                    '_ai_emit() { echo "EMIT: $*"; }',
                    '_ai_persist_exit() { echo "PERSIST: $*"; }',
                    "sleep() { :; }",
                    "for _once in 1; do",
                    script[start:end],
                    "echo LAUNCH_REACHED",
                    "done",
                ]
            ),
            encoding="utf-8",
        )

        done = subprocess.run(
            ["bash", str(harness)], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
        )

        assert done.returncode == 0, done.stderr
        agents_calls = (tmp_path / "agents.log").read_text(encoding="utf-8").splitlines()
        assert len(agents_calls) == 2
        assert not (tmp_path / "stop.log").exists()
        assert "LAUNCH_REACHED" not in done.stdout
        for background_id in (live_id, other_id):
            assert done.stderr.count(f"claude attach {background_id}") == 1
            assert done.stderr.count(f"claude stop {background_id}") == 1

    @_POSIX_SHELL_ONLY
    def test_given_an_idle_live_copy_then_the_script_attaches_with_the_prompt_waiting(self, tmp_path):
        out = self._run_attach_blocks(tmp_path, ai_body="echo aaaa1111", resume=True)

        assert "LAUNCH_REACHED" in out
        assert "gave_up=false target=aaaa1111" in out

    @_POSIX_SHELL_ONLY
    def test_given_a_generated_script_then_it_parses_in_bash(self, tmp_path):
        path = tmp_path / "session.sh"
        path.write_text(self._make_script(), encoding="utf-8")
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, done.stderr


def test_given_pi_bare_launch_when_not_resuming_then_starts_named_session():
    command = _bare_engine_command("p", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["pi", "--provider", "openai-codex", "--name", "myproject-1"]


def test_given_pi_bare_launch_when_resuming_then_continues_named_session():
    command = _bare_engine_command("p", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [], resume=True)

    assert command == ["pi", "--continue", "--provider", "openai-codex", "--name", "myproject-1"]


def test_given_pi_bare_launch_when_provider_configured_then_uses_configured_provider():
    # AI-CLI-1rk1: pi's own default provider (google) is silently unready almost
    # everywhere -- ai-cli-utils must never launch pi without an explicit --provider.
    command = _bare_engine_command(
        "p", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [], pi_provider="anthropic"
    )

    assert command == ["pi", "--provider", "anthropic", "--name", "myproject-1"]


def test_given_codex_bare_launch_when_not_resuming_then_starts_interactive_codex():
    command = _bare_engine_command("cx", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["codex"]


def test_given_codex_bare_launch_when_resuming_then_resumes_last_session_in_worktree():
    command = _bare_engine_command("cx", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [], resume=True)

    assert command == ["codex", "resume", "--last"]


def test_given_matching_claude_transcript_when_bare_launching_then_resumes_its_session_id():
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-000000000001.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[transcript]),
        patch("ai_cli.main._cc_session_is_live", return_value=(False, None)),
    ):
        command = _bare_engine_command("c", "session-1", Path("/tmp"), None, "gemini", "--no-sandbox", [])

    assert command[-4:] == ["--resume", transcript.stem, "--name", "session-1"]


def test_given_invalid_claude_transcript_id_when_bare_launching_then_fails_loudly():
    with patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[Path("/tmp/not-a-session-id.jsonl")]):
        with pytest.raises(RuntimeError, match="invalid session UUID"):
            _bare_engine_command("c", "session-1", Path("/tmp"), None, "gemini", "--no-sandbox", [])


def test_given_resolve_continue_target_with_lone_mismatch_confirmed_then_prints_it(capsys):
    """AI-CLI-8xvd: `ai internal resolve-continue-target` must honor the same
    lone-mismatched-title confirm path `_bare_engine_command` does, since the
    tmux session script resolves through this CLI action, not that function."""
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-00000000000f.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[]),
        patch("ai_cli.main._find_lone_mismatched_cc_session", return_value=transcript),
        patch("ai_cli.main._confirm_mismatched_title_resume", return_value=True),
        patch("ai_cli.main._cc_session_is_live", return_value=(False, None)),
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["resolve-continue-target", "/tmp", "session-1"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == str(transcript)


def test_given_resolve_continue_target_with_lone_mismatch_declined_then_prints_nothing(capsys):
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-000000000010.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[]),
        patch("ai_cli.main._find_lone_mismatched_cc_session", return_value=transcript),
        patch("ai_cli.main._confirm_mismatched_title_resume", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["resolve-continue-target", "/tmp", "session-1"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == ""


def test_given_pi_missing_from_path_when_launching_then_reports_pi(capsys):
    from ai_cli.main import _do_session_launch

    with (
        patch("ai_cli.main.shutil.which", return_value=None),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _do_session_launch(
            engine="p",
            name="1",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="myproject",
            extra_args=[],
            config={},
        )

    assert exc_info.value.code == 1
    assert "pi executable not found" in capsys.readouterr().err


def test_given_codex_missing_from_path_when_launching_then_reports_codex(capsys):
    from ai_cli.main import _do_session_launch

    with (
        patch("ai_cli.main.shutil.which", return_value=None),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _do_session_launch(
            engine="cx",
            name="1",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="myproject",
            extra_args=[],
            config={},
        )

    assert exc_info.value.code == 1
    assert "codex executable not found" in capsys.readouterr().err


# --- Group 9: _find_aicli_project_path ---


class TestFindAicliProjectPath:
    def test_when_importlib_raises_then_falls_through_to_cwd_check(self, tmp_path):
        with (
            patch("importlib.util.find_spec", side_effect=Exception("broken")),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            result = _find_aicli_project_path({})
        assert result is None


# --- Group 10: _auto_update_if_stale ---


class TestAutoUpdateIfStaleFailure:
    def test_given_failed_update_when_auto_update_runs_then_warns_and_leaves_stamp_unset(self, tmp_path, capsys):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        head_result = MagicMock()
        head_result.returncode = 0
        head_result.stdout = "abc123\n"
        update_result = MagicMock()
        update_result.returncode = 1

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head_result
            return update_result

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
        assert updated is False
        assert "Warning" in capsys.readouterr().err
        assert not (tmp_path / "last_update_commit.txt").exists()


class TestAutoUpdateIfStaleLockContention:
    """The lock LOSER must not keep running while a peer reinstalls it.

    AI-CLI-ai-c-loser-auto-update-lock-bvzd. `ai update --force` runs `uv tool
    install`, which tears down and rewrites this process's own installed tree. The
    winner re-execs (AI-CLI-an5r / c5b33d04 added that), but the loser returned
    False -- "no restart needed" -- and carried on executing deferred imports
    against a directory being deleted. Measured 2026-08-17: five `ai c` launches
    within 34 seconds, and the two that had a prior transcript to resume died on
    `from .session_adopt import _pid_is_live` while three siblings survived.

    `False` conflated "no update was needed" with "a peer is replacing my files
    right now". Only the first is safe to continue on.
    """

    def _project(self, tmp_path):
        """Build the project and return the stamp value the launcher will look for.

        The staleness signal is a content fingerprint of the packaged source
        (`AI-CLI-ww8o`), not the repository's HEAD, so the stamp value is computed
        from the tree rather than mocked out of `git rev-parse`. Deriving it from
        the real function is deliberate: hard-coding a digest here would let the
        test pass against a fingerprint that no longer matches what the launcher
        computes, which is the whole condition these tests exist to pin.
        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        head = MagicMock()
        head.returncode = 0
        head.stdout = "abc123\n"
        return head, _installed_source_fingerprint(tmp_path)

    def test_given_lock_left_by_a_launcher_that_died_when_auto_update_runs_then_it_updates_without_waiting(
        self, tmp_path
    ):
        """AI-CLI-2kko: a launcher killed mid-update leaves its lock file behind.

        Its owner is gone, so the update is nobody's. Treating the file's existence as
        "a peer holds it" made every later launch wait the full bound and skip the
        update, so the stale fingerprint, and the wait, repeated on every launch.
        """
        self._project(tmp_path)
        lock = tmp_path / "last_install_fingerprint.lock"
        # A real process creates the lock and exits without releasing it, as a killed
        # launcher does.
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"import os; os.open({str(lock)!r}, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os._exit(0)",
            ],
            check=True,
        )
        assert lock.exists()
        update_calls = []

        def fake_run(cmd, **kwargs):
            if len(cmd) >= 2 and cmd[1] == "update":
                update_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("ai_cli.main._PEER_UPDATE_WAIT_SECONDS", 5.0),
        ):
            start = time.monotonic()
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
            elapsed = time.monotonic() - start

        assert update_calls, "an abandoned lock must not stop the update"
        assert updated is True
        assert elapsed < 2.0, f"waited {elapsed:.1f}s on a lock nobody holds"

    def test_given_a_live_process_holds_the_lock_when_auto_update_runs_then_it_waits_and_does_not_update(
        self, tmp_path, capsys
    ):
        """The negative constraint: a lock a running launcher holds is still respected."""
        self._project(tmp_path)
        lock = tmp_path / "last_install_fingerprint.lock"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, time, portalocker\n"
                f"f = open({str(lock)!r}, 'a')\n"
                "portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)\n"
                "print('held', flush=True)\n"
                "time.sleep(60)\n",
            ],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"

            def fake_run(cmd, **kwargs):
                if len(cmd) >= 2 and cmd[1] == "update":
                    raise AssertionError("a live peer owns the update")
                return MagicMock(returncode=0, stdout="", stderr="")

            with (
                patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
                patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
                patch("subprocess.run", side_effect=fake_run),
                patch("shutil.which", return_value="/usr/bin/ai"),
                patch("ai_cli.main._PEER_UPDATE_WAIT_SECONDS", 0.3),
                patch("ai_cli.main._PEER_UPDATE_POLL_SECONDS", 0.05),
            ):
                updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
        finally:
            holder.kill()
            holder.wait(timeout=10)

        assert updated is False
        assert "Warning" in capsys.readouterr().err

    def test_given_peer_holds_lock_and_completes_update_when_auto_update_runs_then_requests_reexec(self, tmp_path):
        """The peer finished and the stamp advanced, so our imports are stale -> re-exec."""
        head, fingerprint = self._project(tmp_path)
        lock = tmp_path / "last_install_fingerprint.lock"
        finished = []

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("the loser must not run `ai update` itself")

        def peer_finishes(*_args, **_kwargs):
            # Model the winner completing mid-wait: stamp written, then its process
            # ends and the OS drops its lock.
            if not finished:
                finished.append(True)
                (tmp_path / "last_install_fingerprint.txt").write_text(fingerprint)
                release()

        with (
            hold_file_lock(lock) as release,  # a peer already claimed the update
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("time.sleep", side_effect=peer_finishes),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is True, "loser saw its installation replaced and must re-exec, not continue"

    def test_given_peer_holds_lock_indefinitely_when_auto_update_runs_then_terminates_without_reexec(
        self, tmp_path, capsys
    ):
        """Bounded wait: a stuck peer must not hang the launch or loop re-execing (AC-3)."""
        head, _fingerprint = self._project(tmp_path)

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("the loser must not run `ai update` itself")

        with (
            hold_file_lock(tmp_path / "last_install_fingerprint.lock"),  # never released
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            # Shrink the real bound rather than stubbing sleep: with sleep stubbed and
            # the deadline on real monotonic time, the loop busy-spins the full 90s and
            # the test takes 101s. Patching the constant proves the bound is finite
            # AND keeps the suite fast.
            patch("ai_cli.main._PEER_UPDATE_WAIT_SECONDS", 0.05),
            patch("ai_cli.main._PEER_UPDATE_POLL_SECONDS", 0.01),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is False, "an unfinished peer must not trigger a re-exec (no exec loop)"
        assert "Warning" in capsys.readouterr().err, "silently continuing on a torn install is the bug"

    def test_given_stamp_already_current_when_auto_update_runs_then_no_wait_and_no_reexec(self, tmp_path):
        """Positive control + AC-4: the common case must not pay for the fix.

        Without this, both tests above would pass against a function that always
        returned True or always waited, which would assert nothing about contention.
        """
        head, fingerprint = self._project(tmp_path)
        (tmp_path / "last_install_fingerprint.txt").write_text(fingerprint)

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("no update should be attempted when the stamp is current")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("time.sleep", side_effect=AssertionError("must not wait when nothing is stale")),
        ):
            assert _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}}) is False

    def test_given_peer_pulls_new_source_when_loser_waits_then_loser_requests_reexec(self, tmp_path):
        """A peer may pull after taking the lock, changing the fingerprint a loser sees."""
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
        source = tmp_path / "src" / "demo" / "value.py"
        source.write_text('VALUE = "before"\n')
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        peer_pulled = threading.Event()
        finish_peer = threading.Event()
        updates: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            if len(cmd) >= 2 and cmd[1] == "update":
                updates.append(list(cmd))
                source.write_text('VALUE = "after"\n')
                peer_pulled.set()
                assert finish_peer.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
            return MagicMock(returncode=0, stdout="", stderr="")

        def finish_update(*_args, **_kwargs):
            finish_peer.set()

        config = {"deploy": {"project_path": str(tmp_path)}}
        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            winner = threading.Thread(target=_auto_update_if_stale, args=(config,))
            winner.start()
            try:
                assert peer_pulled.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
                with patch("time.sleep", side_effect=finish_update):
                    loser_reexec = _auto_update_if_stale(config)
            finally:
                # Release and join the winner INSIDE the patch context, on every exit
                # path. `with patch(...)` restores the real functions as soon as the
                # block is left, including by an assertion -- but it does not stop a
                # thread started inside it. An orphaned winner therefore resumed in
                # `_auto_update_if_stale` against the REAL `subprocess.run` and the
                # REAL `_find_aicli_project_path`, and the call it was about to make
                # is `[ai, "update", "--force", "--quiet"]`, which reaches
                # `git_repair.pull_rebase_autostash` -- `git pull --rebase
                # --autostash` against a live checkout. A test that fails must not
                # rewrite the branch it is being run on.
                finish_peer.set()
                winner.join(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
                assert not winner.is_alive(), "the winner thread outlived the patches that contain it"

        assert updates and len(updates) == 1
        assert loser_reexec is True, "the loser must restart after its peer replaced the installed files"


class TestAutoUpdateRetryBackoff:
    """A failed update is neither remembered as done nor retried on every single launch.

    AI-CLI-6bg. The stamp used to be written before the install ran, so a host whose
    update could not succeed was latched behind forever: the stamp read "current",
    the install had never worked, and nothing ever retried -- measured on one build
    host as five days without an auto-update. Writing the stamp only on success
    removes that latch and buys the opposite cost, which is what these tests pin:
    an unchanged, still-failing source is retried on a capped backoff instead of on
    every launch, and anything that changes the odds of succeeding -- a source edit,
    a successful update, a record that cannot be read -- retries at once.
    """

    def _tree(self, tmp_path: Path) -> tuple[Path, Path]:
        """A packaged source tree plus an empty state dir, as separate roots.

        Separate so the failure record and the fingerprint stamp cannot themselves
        land inside the tree being fingerprinted, which would change the fingerprint
        every time one was written and make every launch look stale for the wrong
        reason.
        """
        project = tmp_path / "myproject"
        (project / "src" / "demo").mkdir(parents=True)
        (project / "pyproject.toml").write_text('[project]\nname = "myproject"\nversion = "0.1.0"\n')
        (project / "src" / "demo" / "value.py").write_text('VALUE = "before"\n')
        state = tmp_path / "state"
        state.mkdir()
        return project, state

    def _record(self, state: Path) -> Path:
        return state / "last_install_failure.json"

    def _launch(self, project: Path, state: Path, *, returncode: int) -> list[list[str]]:
        """Run one session-launch auto-update; return the ``ai update`` calls it made."""
        updates: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            cmd = list(cmd)
            if len(cmd) >= 2 and cmd[1] == "update":
                updates.append(cmd)
                return MagicMock(returncode=returncode, stdout="", stderr="uv: no space left on device\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=project),
            patch("ai_cli.config.get_xdg_state_home", return_value=state),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(project)}})
        return updates

    def _age_record(self, state: Path, seconds: float) -> None:
        """Move the recorded attempt ``seconds`` into the past (negative = the future)."""
        record = json.loads(self._record(state).read_text())
        record["last_attempt"] = time.time() - seconds
        self._record(state).write_text(json.dumps(record))

    def test_given_a_failed_update_when_the_next_launch_starts_then_it_does_not_retry_immediately(
        self, tmp_path, capsys
    ):
        project, state = self._tree(tmp_path)
        assert self._launch(project, state, returncode=1), "the first launch must attempt the update"
        capsys.readouterr()

        second = self._launch(project, state, returncode=1)

        assert second == [], "a launch inside the backoff window must not re-run a known-failing update"
        assert "skipping auto-update" in capsys.readouterr().err, "a skipped update must say so, not go silent"

    def test_given_the_backoff_elapsed_when_a_launch_starts_then_the_update_is_retried(self, tmp_path):
        project, state = self._tree(tmp_path)
        self._launch(project, state, returncode=1)
        self._age_record(state, _UPDATE_RETRY_BASE_SECONDS + 1)

        assert self._launch(project, state, returncode=1), "the wait is a backoff, not a second latch"
        assert json.loads(self._record(state).read_text())["attempts"] == 2

    def test_given_the_source_changed_after_a_failure_when_a_launch_starts_then_it_retries_at_once(self, tmp_path):
        project, state = self._tree(tmp_path)
        self._launch(project, state, returncode=1)
        (project / "src" / "demo" / "value.py").write_text('VALUE = "after"\n')

        assert self._launch(project, state, returncode=1), "a changed source is a new attempt with new odds"
        assert json.loads(self._record(state).read_text())["attempts"] == 1, "a new source restarts the count"

    def test_given_a_failure_then_a_successful_update_when_it_finishes_then_the_record_is_cleared(self, tmp_path):
        project, state = self._tree(tmp_path)
        self._launch(project, state, returncode=1)
        assert self._record(state).exists()
        (project / "src" / "demo" / "value.py").write_text('VALUE = "after"\n')

        assert self._launch(project, state, returncode=0)

        assert not self._record(state).exists(), "a recovered host must not carry the old attempt count"

    def test_given_an_unreadable_failure_record_when_a_launch_starts_then_it_retries(self, tmp_path):
        project, state = self._tree(tmp_path)
        self._record(state).write_text("{ not json")

        assert self._launch(project, state, returncode=1), "an unusable record must fail toward retrying"

    def test_given_the_clock_moved_backwards_since_the_failure_when_a_launch_starts_then_it_retries(self, tmp_path):
        project, state = self._tree(tmp_path)
        self._launch(project, state, returncode=1)
        self._age_record(state, -3600)  # the attempt now claims to be an hour in the future

        assert self._launch(project, state, returncode=1), "a backwards clock must not read as a near-endless wait"

    def test_given_consecutive_failures_when_the_delay_is_computed_then_it_doubles_to_the_ceiling(self):
        assert _update_retry_delay(0) == 0.0
        assert _update_retry_delay(1) == _UPDATE_RETRY_BASE_SECONDS
        assert _update_retry_delay(2) == 2 * _UPDATE_RETRY_BASE_SECONDS
        assert _update_retry_delay(99) == _UPDATE_RETRY_MAX_SECONDS


# --- Group 11: _cmd_tunnel_start no remote host ---


class TestCmdTunnelStartNoHost:
    def test_when_remote_host_not_set_then_exits_1(self, tmp_path, capsys):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
        ):
            with pytest.raises(SystemExit) as exc:
                _cmd_tunnel_start(4222, 4222, forward=True, config={})
            assert exc.value.code == 1
        assert "host not set" in capsys.readouterr().err


# --- Group 12: _cmd_tunnel_stop ProcessLookupError ---


class TestCmdTunnelStopProcessDead:
    def test_when_process_already_dead_then_cleans_up_pid_file(self, tmp_path, capsys):
        import psutil as _psutil

        pid_file = tmp_path / "tunnel-9222.pid"
        pid_file.write_text("99999")
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = _psutil.NoSuchProcess(99999)
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            with patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc):
                _cmd_tunnel_stop(9222)
        assert not pid_file.exists()
        assert "stopped" in capsys.readouterr().out.lower()


# --- Group 13: _cmd_tunnel_status ---


class TestCmdTunnelStatus:
    def test_when_no_tunnel_pid_files_then_prints_no_tunnels(self, tmp_path, capsys):
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            _cmd_tunnel_status()
        assert "No tunnels registered" in capsys.readouterr().out

    def test_when_tunnel_pid_is_dead_then_prints_dead_and_removes_file(self, tmp_path, capsys):
        pid_file = tmp_path / "tunnel-4222.pid"
        pid_file.write_text("99999")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=False),
        ):
            _cmd_tunnel_status()
        assert "dead" in capsys.readouterr().out
        assert not pid_file.exists()


# --- Group 15: CLI dispatch tests ---


class TestCliDispatchExtended:
    def test_when_quota_status_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_status", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_history_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "history"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_history", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_scrape_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "scrape"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_scrape", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_record_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "record", "sid1", "mac1", "claude-sonnet", "1000"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_record", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_record_missing_args_then_exits_1(self):
        with (
            patch("sys.argv", ["ai", "quota", "record"]),
            patch("ai_cli.config.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_when_copier_update_dispatches_on_any_host(self):
        with (
            patch("sys.argv", ["ai", "copier-update"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_HOST": "hetzner"}),
            patch("ai_cli.copier_update.run_copier_update", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_subcommand_no_arg_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "color"])
        assert exit_code == 1

    def test_when_color_subcommand_no_session_env_then_exits_1(self, monkeypatch):
        monkeypatch.delenv("AI_TMUX_SESSION", raising=False)
        exit_code, _, stderr = run_cli(["ai", "color", "#e74c3c"])
        assert exit_code == 1
        assert "AI_TMUX_SESSION" in stderr

    def test_when_color_subcommand_with_hex_and_session_then_succeeds(self):
        cfg = make_iterm2_config()
        mock_ig = MagicMock()
        mock_ig.cleanup_session_files = MagicMock()
        mock_ig.generate_session_icon = MagicMock(return_value="/tmp/icon.png")
        mock_ig.generate_dynamic_profile = MagicMock()
        with (
            patch("sys.argv", ["ai", "color", "#e74c3c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_tunnel_no_args_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel"])
        assert exit_code == 1

    def test_when_tunnel_stop_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "tunnel", "stop", "9222"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_tunnel_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
            mock_stop.assert_called_once_with(9222)

    def test_when_tunnel_stop_no_port_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel", "stop"])
        assert exit_code == 1

    def test_when_tunnel_status_dispatches(self):
        with (
            patch("sys.argv", ["ai", "tunnel", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_tunnel_status") as mock_status,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
            mock_status.assert_called_once()

    def test_when_tunnel_unknown_action_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel", "unknown"])
        assert exit_code == 1

    def test_when_copier_update_with_project_flag_then_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "copier-update", "--project", "myproject"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.copier_update.run_copier_update", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_known_palette_name_then_resolves_hex(self):
        """lines 2304-2305: palette name resolves to hex."""
        cfg = make_iterm2_config()
        mock_ig = MagicMock()
        mock_ig.generate_session_icon = MagicMock(return_value=None)
        # Get first palette name from the config
        palette = cfg.get("iterm2", {}).get("palette", {})
        first_name = next(iter(palette))
        with (
            patch("sys.argv", ["ai", "color", first_name]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_unknown_name_then_exits_1(self):
        """lines 2306-2308: color arg not in palette and not hex → exits 1."""
        cfg = make_iterm2_config()
        with (
            patch("sys.argv", ["ai", "color", "notacolor"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_when_color_icon_generation_raises_then_continues(self, capsys):
        """lines 2323-2324: icon generation exception is caught and printed."""
        cfg = make_iterm2_config()
        with (
            patch("sys.argv", ["ai", "color", "#e74c3c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch("ai_cli.icon_generator.cleanup_session_files", side_effect=RuntimeError("icon err")),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0  # exception is caught, execution continues
            assert "icon generation failed" in capsys.readouterr().err

    def test_when_project_prefix_flag_then_uses_it(self):
        """line 2665: --project-prefix arg is used directly as project_prefix."""
        with (
            patch("sys.argv", ["ai", "c", "--project-prefix", "myprefix", "-R"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1  # exits because -R requires remote host

    def test_when_unknown_arg_then_used_as_session_name(self):
        """line 2685: unrecognized arg in unknown → used as name when args.name is empty."""
        # args.name is empty (no positional); --unrecognized-flag lands in unknown
        with (
            patch("sys.argv", ["ai", "c", "--project-prefix", "myprefix", "-R", "--unrecognized"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1  # exits because -R requires remote host

    def test_when_keyboard_interrupt_raised_then_exits_cleanly_without_traceback(self):
        """A raw Ctrl-C (e.g. from the registry-sync input() prompt) must not
        propagate past cli() as an unhandled KeyboardInterrupt — standalone_mode=False
        means Click won't convert it to click.exceptions.Abort on its own."""
        with patch("ai_cli.main._cli_group", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


class TestResolveIsRemote:
    def test_when_flag_true_then_returns_true_regardless_of_host(self):
        with patch.dict(os.environ, {"AI_HOST": "mac"}):
            assert _resolve_is_remote(True) is True

    def test_when_flag_false_and_mac_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": "mac"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_named_linux_host_then_returns_false(self):
        """A named host is NOT evidence of a remote session.

        This previously returned True for any AI_HOST != "mac", which made every
        ordinary local launch on a Linux workstation take the remote branch and
        create its worktree inside the configured main project instead of the
        repo the user was in.
        """
        with patch.dict(os.environ, {"AI_HOST": "my-linux-box"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_arbitrary_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": "devserver"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_no_host_env_then_returns_false(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_HOST"}
        with patch.dict(os.environ, env, clear=True):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_empty_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": ""}):
            assert _resolve_is_remote(False) is False


class TestEnsureNatsTunnel:
    def test_when_stale_pid_file_and_tunnel_start_raises_then_returns(self, tmp_path):
        """lines 1639-1640, 1643-1644: stale PID → ProcessLookupError, SystemExit caught."""
        from ai_cli.main import _ensure_nats_tunnel

        pid_file = tmp_path / "tunnel-4222.pid"
        pid_file.write_text("99999")  # nonexistent PID
        config = {"messaging": {"tunnel_port": 4222}}
        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._cmd_tunnel_start", side_effect=SystemExit(1)),
        ):
            _ensure_nats_tunnel(config)  # must not raise or propagate SystemExit


class TestEnsureCircusd:
    def test_when_all_connection_attempts_fail_then_raises(self, tmp_path):
        """lines 1727-1730: all 10 retry attempts fail → raises RuntimeError."""
        from ai_cli.main import _ensure_circusd

        # Mock the circus.client module so CircusClient always fails
        mock_client_instance = MagicMock()
        mock_client_instance.send_message.side_effect = Exception("not ready")
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_circus_client_module = MagicMock()
        mock_circus_client_module.CircusClient = mock_client_class

        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.Popen"),
            patch.dict("sys.modules", {"circus.client": mock_circus_client_module}),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="circusd did not start in time"):
                _ensure_circusd()

    def test_given_circusd_only_in_the_tool_venv_when_started_then_that_binary_is_launched(self, tmp_path):
        """`uv tool install` puts circusd beside the tool's python, not on PATH (fw-lnx, -R)."""
        from ai_cli.main import _ensure_circusd

        venv_bin = tmp_path / "tool" / "bin"
        venv_bin.mkdir(parents=True)
        circusd = venv_bin / ("circusd.exe" if sys.platform == "win32" else "circusd")
        circusd.write_text("")
        client = MagicMock()
        client.CircusClient.return_value.send_message.side_effect = [Exception("not up"), {"status": "ok"}]
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path / "state"),
            patch("ai_cli.process_manager.sys.executable", str(venv_bin / "python")),
            patch("ai_cli.process_manager.shutil.which", return_value=None),
            patch("subprocess.Popen") as popen,
            patch.dict("sys.modules", {"circus.client": client}),
            patch("time.sleep"),
        ):
            _ensure_circusd()
        assert popen.call_args[0][0][0] == str(circusd)


class TestDoSessionLaunchTmuxGuard:
    """The tmux preflight in _do_session_launch, on every platform.

    Originally Windows-only (T-09). Broadened in 55ace53 because every non-Windows
    machine without tmux was crashing with a raw FileNotFoundError from deep inside
    cleanup_stale_sessions(). It still fires on all platforms with a
    platform-appropriate hint, but since AI-CLI-yrpa the outcome is one
    unattended install attempt and then bare mode — never an exit. tmux is an
    enhancement, so its absence degrades a launch instead of blocking it.
    """

    def _base_kwargs(self):
        return {
            "engine": "c",
            "name": "1",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": True,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "test",
            "extra_args": [],
            "config": {},
        }

    def test_when_win32_and_tmux_not_found_then_falls_back_to_bare_mode(self, capsys):
        """On Windows, missing tmux must NOT abort — fall back to bare mode silently.

        Regression test for the bug where the tmux guard exited with code 1 on Windows
        instead of continuing in bare mode.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        # Must NOT be the tmux-not-found hard exit (code 1 with the error message).
        err = capsys.readouterr().err
        assert "tmux not found" not in err
        assert exc_info.value.code != 1, "Windows + no tmux should fall back to bare mode, not exit 1"

    def test_when_win32_and_tmux_found_then_does_not_exit_early(self, capsys):
        from ai_cli.main import _do_session_launch

        # With tmux present on "Windows", the guard passes and execution continues
        # into the normal launch path (which we stop at session resolution)
        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("ai_cli.main._emit_iterm2_profile_setup"),
            patch("ai_cli.main._assign_iterm2_color_slot", return_value=None),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        # Exit must not be the tmux-not-found exit (1) — it's 0 (subprocess mock)
        # confirming the guard was passed
        assert exc_info.value.code != 1 or "tmux not found" not in (capsys.readouterr().err)

    def test_when_non_windows_and_tmux_unavailable_then_falls_back_to_bare_not_exit_1(self, capsys):
        """A missing tmux must degrade the launch to bare mode, never block it.

        This asserted the opposite until AI-CLI-yrpa: the guard printed
        remediation and exited 1 on every non-Windows host without tmux, so an
        *enhancement* (detach/reattach, surviving a dropped SSH connection) was a
        launch precondition. The remediation text is still required — the operator
        should know what bare mode costs them — but the launch continues.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="no manager")),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        assert exc_info.value.code != 1, "a missing tmux must fall back to bare mode, not exit 1"
        err = capsys.readouterr().err
        assert "launching in bare mode instead" in err
        # Platform-appropriate install hint, not the Windows/WSL one.
        assert "apt install tmux" in err
        # The permanent opt-out must be named so the notice can be silenced.
        assert "use_tmux = false" in err

    def test_when_tmux_missing_then_one_unattended_install_is_attempted_first(self):
        """Falling back to bare is the last resort, not the first response.

        Without this, "never fatal" could be satisfied by never trying to install
        tmux at all, which would quietly strip detach/reattach from every host
        that merely needed one `brew install`.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="x")) as install,
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                _do_session_launch(**self._base_kwargs())
        assert install.call_count == 1

    def test_when_win32_and_tmux_missing_then_no_install_is_attempted(self):
        """Windows has no native tmux, so an install attempt there is pure noise."""
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.tmux_setup.install_tmux") as install,
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                _do_session_launch(**self._base_kwargs())
        install.assert_not_called()

    def test_when_auto_install_succeeds_then_the_launch_stays_in_tmux_mode(self, capsys):
        """The install must actually be believed — otherwise it buys nothing.

        `bare` has to come back False after a successful install, which is only
        observable through the notice: an install that reported success and still
        launched bare would look identical to no install at all.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "linux"),
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(True, tool="brew")),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                _do_session_launch(**self._base_kwargs())
        err = capsys.readouterr().err
        assert "[launch] tmux: installed via brew" in err
        assert "launching in bare mode instead" not in err

    def test_when_darwin_and_tmux_unavailable_then_hint_is_homebrew(self, capsys):
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "darwin"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="no manager")),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        assert exc_info.value.code != 1
        assert "brew install tmux" in capsys.readouterr().err

    def test_when_use_tmux_false_and_tmux_absent_then_guard_is_skipped(self):
        """The opt-out is the escape hatch the error message advertises.

        Nothing covered it before, so `use_tmux = false` could have silently stopped
        working and the only symptom would be a machine that cannot launch a session
        at all.
        """
        from ai_cli.main import _do_session_launch

        kwargs = self._base_kwargs()
        kwargs["config"] = {"session": {"use_tmux": False}}
        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)
        # Reached subprocess.run (0), not the tmux guard (1).
        assert exc_info.value.code != 1


class TestDoSessionLaunchRegistryDiscovery:
    def test_given_legacy_remote_session_when_local_index_launch_then_reuses_legacy_worktree(self):
        from ai_cli.main import _do_session_launch

        kwargs = {
            "engine": "c",
            "name": "7",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": False,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "APP",
            "extra_args": [],
            "config": {"worktree": {"enabled": True}},
        }
        with (
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.detect_repo_root", return_value=None),
            patch("ai_cli.trust.ensure_workspace_trusted"),
            patch("ai_cli.config.get_current_project_name", return_value="myproject"),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout="c-r-app-7\n"),
            ),
            patch("ai_cli.session.create_worktree", return_value=None) as create_worktree,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)

        assert exc_info.value.code == 1
        create_worktree.assert_called_once_with("app-7", with_status=True)

    def test_given_named_launch_and_unregistered_other_project_when_started_then_skips_registry_prompt(self):
        from ai_cli.main import _do_session_launch

        kwargs = {
            "engine": "c",
            "name": "1",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": False,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "",
            "extra_args": [],
            "config": {"worktree": {"enabled": True}},
        }
        with (
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=False) as validate,
            patch("ai_cli.session.is_current_project_resolved", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="app"),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.build_session_name", return_value=("c-app-1", "app-1")),
            patch("ai_cli.session.create_worktree", return_value=Path("/tmp/project-worktree")),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)

        assert exc_info.value.code == 0
        validate.assert_not_called()

    def test_given_targeted_cli_launch_and_unregistered_directory_when_started_then_skips_registry_discovery(
        self, tmp_path, monkeypatch
    ):
        """The Click command path must preserve targeted-launch discovery bypass."""
        import ai_cli.config as config_module

        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / "myproject"
        project_dir.mkdir(parents=True)
        (projects_dir / "unregistered-worktrees").mkdir()
        registry_path = tmp_path / "projects.toml"
        registry_path.write_text(
            '[[projects]]\nname = "myproject"\ntask_prefix = "MYPROJECT"\ntype = "tool"\nactive = true\n'
        )
        monkeypatch.chdir(project_dir)
        run = subprocess.run

        def fake_run(argv, **kwargs):
            if argv[:2] == ["tmux", "new-session"]:
                return subprocess.CompletedProcess(argv, 0)
            return run(argv, **kwargs)

        with (
            patch("sys.argv", ["ai", "c", "--once", "2"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("ai_cli.config.load_config", return_value={"worktree": {"enabled": False}}),
            patch("ai_cli.config._get_project_registry_path", return_value=registry_path),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("ai_cli.session._get_projects_dir", return_value=projects_dir),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._auto_update_if_stale"),
            patch("ai_cli.tunnel._ensure_nats_tunnel"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.session.get_project_prefix", return_value="MYPROJECT"),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.build_session_name", return_value=("c-myproject-2", "myproject-2")),
            patch("ai_cli.trust.ensure_workspace_trusted"),
            patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
            patch("ai_cli.main.subprocess.run", side_effect=fake_run),
            patch.object(
                config_module,
                "validate_registry_completeness",
                wraps=config_module.validate_registry_completeness,
            ) as validate,
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli()

        assert exc_info.value.code == 0
        validate.assert_not_called()


# --- uv link-mode detection tests ---


class TestUvLinkModeDetection:
    """Test _should_use_uv_link_mode_copy behavior across filesystem boundaries."""

    def test_given_same_filesystem_when_checked_then_returns_false(self, tmp_path):
        """When cache and target are on the same filesystem, no --link-mode flag needed."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        # Both directories on the same filesystem (tmp_path).
        cache_dir = tmp_path / "cache"
        tool_dir = tmp_path / "tools"
        cache_dir.mkdir()
        tool_dir.mkdir()

        mock_uv = "uv"
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    # First call: uv cache dir
                    MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                    # Second call: uv tool dir
                    MagicMock(returncode=0, stdout=str(tool_dir) + "\n"),
                ],
            ),
        ):
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is False

    def test_given_different_filesystems_when_checked_then_returns_true(self, tmp_path, monkeypatch):
        """When cache and target are on different filesystems, return True for --link-mode=copy."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        tool_dir = tmp_path / "tools"
        cache_dir.mkdir()
        tool_dir.mkdir()

        # Create mock stat_result objects with different st_dev values.
        cache_stat = os.stat_result((0o40755, 123, 100, 2, 1000, 1000, 4096, 0, 0, 0))
        tool_stat = os.stat_result((0o40755, 456, 200, 2, 1000, 1000, 4096, 0, 0, 0))

        original_stat = Path.stat

        def mock_stat(self, **kwargs):
            path_str = str(self)
            if path_str == str(cache_dir):
                return cache_stat
            if path_str == str(tool_dir):
                return tool_stat
            # Use original for other paths (walking up to parents)
            return original_stat(self, **kwargs)

        mock_uv = "uv"
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                    MagicMock(returncode=0, stdout=str(tool_dir) + "\n"),
                ],
            ),
        ):
            monkeypatch.setattr(Path, "stat", mock_stat)
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is True

    def test_given_cache_dir_unresolvable_when_checked_then_returns_false(self):
        """When uv cache dir command fails, preserve default behavior (return False)."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        mock_uv = "uv"
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is False

    def test_given_target_dir_does_not_exist_when_checked_then_walks_up_to_ancestor(self, tmp_path):
        """When target dir does not exist yet, walk up to nearest existing ancestor."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        # Tool dir does not exist yet, but its parent does.
        tool_parent = tmp_path / "tools-parent"
        tool_parent.mkdir()
        nonexistent_tool_dir = tool_parent / "subdir" / "tools"

        mock_uv = "uv"
        with patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                MagicMock(returncode=0, stdout=str(nonexistent_tool_dir) + "\n"),
            ],
        ):
            result = _should_use_uv_link_mode_copy(mock_uv)

        # Both are under tmp_path, so same filesystem → False.
        assert result is False

    def test_given_explicit_target_dir_when_checked_then_uses_it(self, tmp_path):
        """When target_dir is explicitly provided, use it instead of uv tool dir."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        venv_dir = tmp_path / "venv"
        cache_dir.mkdir()
        venv_dir.mkdir()

        mock_uv = "uv"
        with patch(
            "subprocess.run",
            side_effect=[
                # Only one call: uv cache dir (uv tool dir not called when target_dir provided).
                MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
            ],
        ):
            result = _should_use_uv_link_mode_copy(mock_uv, venv_dir)

        # Same filesystem → False.
        assert result is False
