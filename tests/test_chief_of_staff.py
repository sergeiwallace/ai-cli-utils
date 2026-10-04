"""`ai cos` launches one chief-of-staff session per machine and refuses to guess.

The chief-of-staff home is a per-machine state directory the environment's installer
seeds. The launcher must resolve it from the machine key, refuse a missing or unseeded
home, refuse a second chief while the first is running, write the registration other
sessions read, and only then hand off to the ordinary Claude launch with FM_HOME and the
role exported and worktree isolation off. A dry run must write nothing.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli

from ai_cli import chief_of_staff as cos

VALID_POLICY = '{"schema_version":1,"primary":"native","fallbacks":["agent-mail","fm-send"]}\n'


@pytest.fixture
def seeded_home(tmp_path, monkeypatch):
    """A chief home as the installer leaves it, under an isolated XDG state root."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv(cos.MACHINE_KEY_ENV, "machine-key-1")
    home = state / "firstmate" / "chief-of-staff" / "machine-key-1"
    (home / "config").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "config" / "message-transports.json").write_text(VALID_POLICY)
    return home


class TestResolveChiefHome:
    def test_given_machine_key_env_when_resolved_then_path_is_under_xdg_state(self, seeded_home):
        assert cos.resolve_chief_home(None, None) == seeded_home

    def test_given_explicit_key_when_resolved_then_it_wins_over_env(self, seeded_home):
        assert cos.resolve_chief_home(None, "other-key").name == "other-key"

    def test_given_explicit_home_when_resolved_then_it_wins_over_everything(self, seeded_home, tmp_path):
        assert cos.resolve_chief_home(str(tmp_path / "elsewhere"), "other-key") == tmp_path / "elsewhere"

    def test_given_no_key_anywhere_when_resolved_then_refuses_naming_the_remedy(self, monkeypatch):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with pytest.raises(cos.ChiefOfStaffError, match="machine-key"):
            cos.resolve_chief_home(None, None)

    def test_given_path_like_key_when_resolved_then_refuses(self):
        with pytest.raises(cos.ChiefOfStaffError, match="plain token"):
            cos.resolve_chief_home(None, "../escape")


class TestValidateChiefHome:
    def test_given_missing_home_when_validated_then_refuses_with_provisioning_remedy(self, tmp_path):
        with pytest.raises(cos.ChiefOfStaffError, match="does not exist"):
            cos.validate_chief_home(tmp_path / "absent")

    def test_given_home_without_policy_when_validated_then_refuses_naming_the_file(self, tmp_path):
        (tmp_path / "config").mkdir()
        with pytest.raises(cos.ChiefOfStaffError, match=r"message-transports\.json"):
            cos.validate_chief_home(tmp_path)

    def test_given_seeded_home_when_validated_then_passes(self, seeded_home):
        cos.validate_chief_home(seeded_home)


class TestRegistration:
    def test_given_no_registration_when_read_then_none(self, seeded_home):
        assert cos.read_registration(seeded_home) is None

    def test_given_malformed_registration_when_read_then_refuses(self, seeded_home):
        cos.registration_path(seeded_home).write_text("{not json")
        with pytest.raises(cos.ChiefOfStaffError, match="not JSON"):
            cos.read_registration(seeded_home)

    def test_given_first_launch_when_written_then_registry_and_registration_are_private_and_complete(self, seeded_home):
        when = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        registration = cos.write_registration(
            seeded_home,
            native_agent_name="myproject-cos-1",
            tmux_target="c-myproject-cos-1",
            machine_key="machine-key-1",
            machine_name="workstation",
            launch_cwd="/work/myproject",
            now=when,
        )
        on_disk = json.loads(cos.registration_path(seeded_home).read_text())
        assert on_disk == registration
        assert on_disk["native_agent_name"] == "myproject-cos-1"
        assert on_disk["tmux_target"] == "c-myproject-cos-1"
        assert on_disk["session"] == "workstation/myproject-cos-1"
        assert on_disk["registered_at"] == "2026-09-26T12:00:00Z"
        assert on_disk["chief_generation"] == 1
        registry = json.loads(cos.registry_path(seeded_home).read_text())
        assert registry["schema_version"] == 2
        assert registry["chief_of_staff_session"] == "workstation/myproject-cos-1"
        assert registry["chief_routes"]["native_agent_name"] == "myproject-cos-1"
        assert registry["chief_routes"]["route_revision"] == 1
        assert registry["chief_instance_key"] == "firstmate-chief/machine-key-1"
        assert registry["vp_sessions"] == []
        if os.name == "posix":
            for path in (cos.registry_path(seeded_home), cos.registration_path(seeded_home)):
                assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_given_existing_registry_when_rewritten_then_generation_increments_and_vps_survive(self, seeded_home):
        cos.registry_path(seeded_home).write_text(
            json.dumps({"schema_version": 2, "chief_generation": 6, "vp_sessions": [{"vp_id": "vp/myproject/primary"}]})
        )
        cos.write_registration(
            seeded_home,
            native_agent_name="myproject-cos-2",
            tmux_target="c-myproject-cos-2",
            machine_key="machine-key-1",
            machine_name="workstation",
            launch_cwd="/work/myproject",
        )
        registry = json.loads(cos.registry_path(seeded_home).read_text())
        assert registry["chief_generation"] == 7
        assert registry["vp_sessions"] == [{"vp_id": "vp/myproject/primary"}]
        assert registry["chief_routes"]["native_agent_name"] == "myproject-cos-2"

    def test_given_registration_when_liveness_checked_then_it_is_the_tmux_probe_of_its_target(self):
        registration = {"tmux_target": "c-myproject-cos-1"}
        assert cos.registration_is_live(registration, has_session=lambda t: t == "c-myproject-cos-1")
        assert not cos.registration_is_live(registration, has_session=lambda t: False)

    def test_given_bare_registration_without_target_when_liveness_checked_then_never_live(self):
        assert not cos.registration_is_live({"tmux_target": ""}, has_session=lambda t: True)
        assert not cos.registration_is_live(None, has_session=lambda t: True)


def _launch_patches(stack: ExitStack, live: bool, real_names: bool = False):
    """Patch the launch machinery so a chief launch resolves a plan without touching tmux or git.

    Returns the `_do_session_launch` mock, the one call the command must (or must not) reach.

    ``real_names=True`` leaves ``build_session_name`` in place and silences only the tmux
    session listing it probes, so a test can assert the naming convention the launcher
    actually produces rather than a stubbed string.
    """
    launch = stack.enter_context(patch("ai_cli.main._do_session_launch"))
    targets: list[tuple[str, dict]] = [
        ("ai_cli.main._ensure_dolt_server", {}),
        ("ai_cli.main._launch_install_origin", {}),
        ("ai_cli.main._tunnel._ensure_nats_tunnel", {}),
        ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
        ("ai_cli.chief_of_staff.tmux_has_session", {"return_value": live}),
        ("ai_cli.config.detect_machine_profile", {"return_value": {"host_id": "workstation", "os_type": "linux"}}),
        ("ai_cli.tmux_setup.config_opts_out", {"return_value": False}),
    ]
    if real_names:
        targets.append(("ai_cli.session._tmux_session_names", {"return_value": []}))
    else:
        targets.append(
            (
                "ai_cli.session.build_session_name",
                {"return_value": ("c-myproject-firstmate-1", "myproject-firstmate-1")},
            )
        )
    for target, kwargs in targets:
        stack.enter_context(patch(target, **kwargs))
    # run_cli restores os.environ on exit, so the environment the launch would inherit is
    # observable only at hand-off time: record it from inside the mock.
    seen: dict[str, str | None] = {}
    launch.side_effect = lambda **_kwargs: seen.update({k: os.environ.get(k) for k in (cos.FM_HOME_ENV, cos.ROLE_ENV)})
    launch.seen_env = seen
    return launch


class TestCosCommand:
    def test_given_seeded_home_when_launched_then_registers_exports_env_and_hands_off_with_a_worktree(
        self, seeded_home, monkeypatch
    ):
        monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, err = run_cli(["ai", "cos"])
            assert code == 0, err
            launch.assert_called_once()
            kwargs = launch.call_args.kwargs
        assert kwargs["engine"] == "c"
        assert kwargs["name"] == "firstmate"
        assert kwargs["no_worktree"] is False
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "myproject-firstmate-1"
        assert registration["tmux_target"] == "c-myproject-firstmate-1"
        assert registration["machine_key"] == "machine-key-1"
        assert registration["machine_name"] == "workstation"
        assert "chief-of-staff registered: agent myproject-firstmate-1" in err

    def test_given_no_worktree_requested_when_launched_then_isolation_is_off(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, err = run_cli(["ai", "cos", "-W"])
            assert code == 0, err
            assert launch.call_args.kwargs["no_worktree"] is True

    def test_given_dry_run_when_launched_then_nothing_is_registered(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, _err = run_cli(["ai", "cos", "--dry-run"])
            assert code == 0
            assert launch.call_args.kwargs["dry_run"] is True
        assert not cos.registration_path(seeded_home).exists()
        assert not cos.registry_path(seeded_home).exists()

    def test_given_missing_home_when_launched_then_refuses_before_any_launch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv(cos.MACHINE_KEY_ENV, "machine-key-1")
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "cos"])
        assert code == 1
        assert "does not exist" in err
        assert "machine-key-1" in err
        launch.assert_not_called()

    def test_given_no_machine_key_when_launched_then_refuses_naming_the_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "cos"])
        assert code == 1
        assert cos.MACHINE_KEY_ENV in err
        launch.assert_not_called()

    def test_given_live_chief_when_launched_again_then_refuses_and_points_at_attach(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_agent_name": "myproject-firstmate-1",
                    "tmux_target": "c-myproject-firstmate-1",
                }
            )
        )
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True)
            code, _out, err = run_cli(["ai", "cos"])
            launch.assert_not_called()
        assert code == 1
        assert "already running" in err
        assert "ai attach c-myproject-firstmate-1" in err

    def test_given_dead_registration_when_launched_then_re_registers_with_next_generation(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_agent_name": "myproject-firstmate-1",
                    "tmux_target": "c-myproject-firstmate-1",
                }
            )
        )
        cos.registry_path(seeded_home).write_text(json.dumps({"schema_version": 2, "chief_generation": 3}))
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, _err = run_cli(["ai", "cos"])
            launch.assert_called_once()
        assert code == 0
        assert json.loads(cos.registry_path(seeded_home).read_text())["chief_generation"] == 4

    def test_given_positional_suffix_when_launched_then_it_names_the_session(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "cos", "review"])
            assert code == 0, err
            assert launch.call_args.kwargs["name"] == "firstmate-review"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "myproject-firstmate-review-1"
        assert registration["tmux_target"] == "c-myproject-firstmate-review-1"

    def test_given_remote_requested_when_launched_then_usage_error(self, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "cos", "-R"])
        # cli() maps click usage errors to exit 1 on purpose (its docstring: the exit
        # contract matches the argparse dispatcher it replaced).
        assert code == 1
        assert "--remote" in err
        launch.assert_not_called()

    def test_given_explicit_home_option_when_launched_then_that_home_is_used(self, tmp_path, monkeypatch):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        home = tmp_path / "custom-home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "message-transports.json").write_text(VALID_POLICY)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, _err = run_cli(["ai", "cos", "-H", str(home), "-k", "key-x"])
            launch.assert_called_once()
        assert code == 0
        assert launch.seen_env[cos.FM_HOME_ENV] == str(home)
        assert json.loads(cos.registration_path(home).read_text())["machine_key"] == "key-x"

    def test_given_help_when_asked_then_cos_is_documented(self):
        code, out, _err = run_cli(["ai", "cos", "--help"])
        assert code == 0
        assert "--machine-key" in out
        assert "--fm-home" in out
        assert "--dry-run" in out


class TestFirstmateFlag:
    """`--firstmate` turns any engine's session into this machine's chief session."""

    def test_given_claude_firstmate_when_launched_then_named_firstmate_with_a_worktree(self, seeded_home, monkeypatch):
        monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--firstmate"])
            assert code == 0, err
            kwargs = launch.call_args.kwargs
        assert kwargs["engine"] == "c"
        assert kwargs["name"] == "firstmate"
        assert kwargs["no_worktree"] is False
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "myproject-firstmate-1"
        assert registration["tmux_target"] == "c-myproject-firstmate-1"

    def test_given_a_positional_suffix_when_launched_then_it_sits_between_firstmate_and_the_index(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--firstmate", "review"])
            assert code == 0, err
            assert launch.call_args.kwargs["name"] == "firstmate-review"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "myproject-firstmate-review-1"
        assert registration["tmux_target"] == "c-myproject-firstmate-review-1"

    def test_given_an_unsafe_suffix_when_launched_then_it_is_sanitized_like_any_session_name(self, seeded_home):
        with ExitStack() as stack:
            _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--firstmate", "Review/Queue"])
            assert code == 0, err
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "myproject-firstmate-review-queue-1"

    @pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
    def test_given_any_engine_when_firstmate_then_that_engine_launches_as_the_chief(self, engine, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", engine, "-F"])
            assert code == 0, err
            kwargs = launch.call_args.kwargs
        assert kwargs["engine"] == engine
        assert kwargs["name"] == "firstmate"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["tmux_target"] == f"{engine}-myproject-firstmate-1"

    def test_given_no_worktree_requested_when_firstmate_then_isolation_is_off(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--firstmate", "-W"])
            assert code == 0, err
            assert launch.call_args.kwargs["no_worktree"] is True

    def test_given_firstmate_with_remote_when_launched_then_usage_error(self, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", "--firstmate", "-R"])
        assert code == 1
        assert "--remote" in err
        launch.assert_not_called()

    def test_given_explicit_home_and_key_when_firstmate_then_that_home_is_used(self, tmp_path, monkeypatch):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        home = tmp_path / "custom-home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "message-transports.json").write_text(VALID_POLICY)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "p", "-F", "-H", str(home), "-k", "key-x"])
            assert code == 0, err
        assert launch.seen_env[cos.FM_HOME_ENV] == str(home)
        assert json.loads(cos.registration_path(home).read_text())["machine_key"] == "key-x"

    def test_given_a_live_chief_when_firstmate_launched_again_then_refuses(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_agent_name": "myproject-firstmate-1",
                    "tmux_target": "c-myproject-firstmate-1",
                }
            )
        )
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", "g", "--firstmate"])
            launch.assert_not_called()
        assert code == 1
        assert "already running" in err

    def test_given_chief_options_without_firstmate_when_launched_then_usage_error(self, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", "-k", "machine-key-1"])
        assert code == 1
        assert "--firstmate" in err
        launch.assert_not_called()

    def test_given_an_ordinary_session_when_launched_then_nothing_is_registered(self, seeded_home, monkeypatch):
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c"])
            assert code == 0, err
            assert launch.seen_env[cos.ROLE_ENV] is None
        assert not cos.registration_path(seeded_home).exists()

    def test_given_dry_run_when_firstmate_then_nothing_is_registered(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--firstmate", "--dry-run"])
            assert code == 0, err
            assert launch.call_args.kwargs["dry_run"] is True
        assert not cos.registration_path(seeded_home).exists()
        assert not cos.registry_path(seeded_home).exists()

    @pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
    def test_given_help_when_asked_then_every_engine_documents_the_chief_options(self, engine):
        code, out, _err = run_cli(["ai", engine, "--help"])
        assert code == 0
        assert "--firstmate" in out
        assert "--machine-key" in out
        assert "--fm-home" in out


def test_tmux_env_forwarding_carries_the_chief_home_and_role_only_when_set():
    from ai_cli.main import build_tmux_env_flags

    flags = build_tmux_env_flags({"PATH": "/bin", "FM_HOME": "/state/chief", "AI_SESSION_ROLE": "chief-of-staff"})
    assert "FM_HOME=/state/chief" in flags
    assert "AI_SESSION_ROLE=chief-of-staff" in flags
    plain = build_tmux_env_flags({"PATH": "/bin"})
    assert not any(flag.startswith(("FM_HOME=", "AI_SESSION_ROLE=")) for flag in plain)
    xdg = next(flag for flag in plain if flag.startswith("XDG_STATE_HOME="))
    assert Path(xdg.split("=", 1)[1]).is_absolute()
