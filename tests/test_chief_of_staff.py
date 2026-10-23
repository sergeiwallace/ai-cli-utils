"""`ai cos` launches one chief-of-staff session per machine and refuses to guess.

The chief-of-staff home is a per-machine state directory the environment's installer
seeds. The launcher must resolve it from the machine key, refuse a missing or unseeded
home, refuse a second chief while the first is running, write the registration other
sessions read, and only then hand off to the ordinary Claude launch with FM_HOME and the
role exported and worktree isolation off. A dry run must write nothing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import types
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import CHIEF_PROJECT, with_environment_config
from conftest import run_cli as _run_cli

from ai_cli import chief_of_staff as cos
from ai_cli.transport import REMOTE_LAUNCH_REFUSED_EXIT

VALID_POLICY = '{"schema_version":1,"primary":"native","fallbacks":["agent-mail","fm-send"]}\n'


def run_cli(argv, config=None, env=None):
    """``conftest.run_cli`` with the ``[chief_of_staff]`` settings configured."""
    return _run_cli(argv, config=with_environment_config(config), env=env)


@pytest.fixture(autouse=True)
def chief_project(tmp_path, monkeypatch):
    """The configured chief project (prefix ``hub``), registered and checked out in a temp tree.

    Every chief launches into it whatever the cwd, so the launch resolves that project by
    name and enters its directory. ``monkeypatch.chdir`` first, so the launch's own chdir is
    undone at teardown. Calls outside ``run_cli`` read the same settings.
    """
    monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: with_environment_config())
    monkeypatch.chdir(tmp_path)
    projects = tmp_path / "projects"
    (projects / CHIEF_PROJECT).mkdir(parents=True)
    monkeypatch.setattr("ai_cli.config._find_project_dir", lambda name, _home=None: projects / name)
    monkeypatch.setattr("ai_cli.config.get_project_aliases", lambda: {"hub": CHIEF_PROJECT})

    def prefix_for(name):
        from ai_cli.config import ProjectPrefixError

        if name in ("hub", CHIEF_PROJECT):
            return "hub"
        raise ProjectPrefixError(f"Unknown project {name!r}")

    monkeypatch.setattr("ai_cli.config.resolve_project_prefix_by_name", prefix_for)
    return projects / CHIEF_PROJECT


@pytest.fixture
def click_expands_like_windows(monkeypatch):
    """Make Click take its Windows-only argv expansion branch (``~`` and globs) on every OS.

    Click decides with ``os.name == "nt"`` inside ``click.core``; only that module's view of
    ``os`` is swapped, so nothing else in the process believes it is on Windows.
    """
    import click.core

    real_os = vars(click.core)["os"]
    windows_view = {k: getattr(real_os, k) for k in dir(real_os) if not k.startswith("__")}
    windows_view["name"] = "nt"
    monkeypatch.setattr(click.core, "os", types.SimpleNamespace(**windows_view))


@pytest.fixture
def seeded_home(tmp_path, monkeypatch):
    """A chief home as the installer leaves it, under an isolated XDG state root."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv(cos.MACHINE_KEY_ENV, "machine-key-1")
    home = state / "myhub" / "chief-of-staff" / "machine-key-1"
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
            instance_key_prefix="example-chief",
            now=when,
        )
        on_disk = json.loads(cos.registration_path(seeded_home).read_text())
        assert on_disk == registration
        assert on_disk["native_agent_name"] == "myproject-cos-1"
        assert on_disk["tmux_target"] == "c-myproject-cos-1"
        assert on_disk["session"] == "myproject-cos-1@workstation"
        assert on_disk["registered_at"] == "2026-09-26T12:00:00Z"
        assert on_disk["chief_generation"] == 1
        registry = json.loads(cos.registry_path(seeded_home).read_text())
        assert registry["schema_version"] == 2
        assert registry["chief_of_staff_session"] == "myproject-cos-1@workstation"
        assert registry["chief_routes"]["native_agent_name"] == "myproject-cos-1"
        assert registry["chief_routes"]["route_revision"] == 1
        assert registry["chief_instance_key"] == "example-chief/machine-key-1"
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
            instance_key_prefix="example-chief",
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
    # The attach execs tmux and never returns; SystemExit(0) stands in for that.
    launch.attach = stack.enter_context(patch("ai_cli.main._do_attach", side_effect=SystemExit(0)))
    targets: list[tuple[str, dict]] = [
        ("ai_cli.chief_of_staff.tmux_panes_all_dead", {"return_value": False}),
        ("ai_cli.session._tmux_session_names", {"return_value": []}),
        ("ai_cli.main._ensure_dolt_server", {}),
        ("ai_cli.main._launch_install_origin", {}),
        ("ai_cli.main._tunnel._ensure_nats_tunnel", {}),
        ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
        ("ai_cli.chief_of_staff.tmux_has_session", {"return_value": live}),
        ("ai_cli.config.detect_machine_profile", {"return_value": {"host_id": "workstation", "os_type": "linux"}}),
        ("ai_cli.tmux_setup.config_opts_out", {"return_value": False}),
    ]
    if not real_names:
        targets.append(
            (
                "ai_cli.session.build_session_name",
                {"return_value": ("c-hub-chief-1", "hub-chief-1")},
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
    @pytest.mark.parametrize("dry_run", [False, True])
    def test_given_missing_host_alias_when_chief_launched_then_refuses_without_registration(self, seeded_home, dry_run):
        from ai_cli.session_naming import HostAliasError

        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(
                patch(
                    "ai_cli.session_naming.resolve_host_alias", side_effect=HostAliasError("no host alias configured")
                )
            )
            code, _out, err = run_cli(["ai", "cos", *(["--dry-run"] if dry_run else [])])
            launch.assert_not_called()
        assert code == 1
        assert "no host alias configured" in err
        assert not cos.registration_path(seeded_home).exists()
        assert not cos.registry_path(seeded_home).exists()

    @pytest.mark.parametrize("command", [["cos"], ["c", "-F"], ["g", "-F"], ["p", "-F"], ["cx", "-F"]])
    @pytest.mark.parametrize("suffix", [[], ["review"]])
    def test_given_host_alias_when_chief_registered_then_names_equal_the_launch_plan(
        self, seeded_home, monkeypatch, command, suffix
    ):
        from ai_cli import main, session_naming

        monkeypatch.setattr(session_naming, "resolve_host_alias", lambda: "box")
        real_launch = main._do_session_launch
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(patch("ai_cli.session._resolve_is_remote", return_value=False))
            stack.enter_context(patch("ai_cli.session.is_current_project_resolved", return_value=True))
            stack.enter_context(patch("ai_cli.tmux_setup.tmux_present", return_value=True))
            plan = stack.enter_context(patch("ai_cli.main._print_launch_plan"))
            # The pi and codex engines refuse to start without their binary on PATH; a CI runner has
            # neither, and this test is about names, not about those binaries.
            real_which = shutil.which
            stack.enter_context(
                patch(
                    "shutil.which",
                    side_effect=lambda cmd, *a, **k: (
                        f"/usr/bin/{cmd}" if cmd in {"pi", "codex"} else real_which(cmd, *a, **k)
                    ),
                )
            )

            # Exercise the real launch's name resolution, stopping at its read-only plan.
            def dry_launch(**kwargs):
                kwargs["dry_run"] = True
                return real_launch(**kwargs)

            launch.side_effect = dry_launch
            code, _out, err = run_cli(["ai", *command, *suffix])
            assert code == 0, err
            plan.assert_called_once()
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        expected_name = "hub-box-chief-" + ("review-" if suffix else "") + "1"
        assert plan.call_args.kwargs["ai_name"] == expected_name
        assert registration["native_agent_name"] == plan.call_args.kwargs["ai_name"]
        assert registration["tmux_target"] == plan.call_args.kwargs["session_id"]
        assert cos.registration_is_live(
            registration, has_session=lambda target: target == plan.call_args.kwargs["session_id"]
        )

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
        assert kwargs["name"] == "chief"
        assert kwargs["no_worktree"] is False
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-chief-1"
        assert registration["tmux_target"] == "c-hub-chief-1"
        assert registration["machine_key"] == "machine-key-1"
        assert registration["machine_name"] == "workstation"
        assert "chief-of-staff registered: agent hub-chief-1" in err

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

    def test_given_live_chief_when_launched_again_then_attaches_to_it_and_writes_nothing(self, seeded_home):
        registration = {"schema_version": 1, "native_agent_name": "hub-chief-1", "tmux_target": "c-hub-chief-1"}
        cos.registration_path(seeded_home).write_text(json.dumps(registration))
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True)
            code, _out, err = run_cli(["ai", "cos"])
            launch.assert_not_called()
            launch.attach.assert_called_once_with("c-hub-chief-1")
        assert code == 0, err
        assert "attaching to the running chief-of-staff hub-chief-1" in err
        assert json.loads(cos.registration_path(seeded_home).read_text()) == registration
        assert not cos.registry_path(seeded_home).exists()

    def test_given_dead_registration_when_launched_then_re_registers_with_next_generation(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_agent_name": "hub-chief-1",
                    "tmux_target": "c-hub-chief-1",
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
            assert launch.call_args.kwargs["name"] == "chief-review"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-tst-chief-review-1"
        assert registration["tmux_target"] == "c-hub-tst-chief-review-1"

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


class TestChiefFlag:
    """`--chief` turns any engine's session into this machine's chief session."""

    def test_given_claude_chief_when_launched_then_named_chief_with_a_worktree(self, seeded_home, monkeypatch):
        monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief"])
            assert code == 0, err
            kwargs = launch.call_args.kwargs
        assert kwargs["engine"] == "c"
        assert kwargs["name"] == "chief"
        assert kwargs["no_worktree"] is False
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-tst-chief-1"
        assert registration["tmux_target"] == "c-hub-tst-chief-1"

    def test_given_human_names_when_the_chief_launches_then_a_picked_name_follows_and_a_second_chief_is_refused(
        self, seeded_home, tmp_path, monkeypatch
    ):
        from name_fleet import build_fleet

        from ai_cli import session_naming

        own_host_id = session_naming.host_id
        (tmp_path / "fleet").mkdir()
        fleet = build_fleet(tmp_path / "fleet", monkeypatch)
        # Keep this module's host id, the one its alias registry names (alias "tst").
        monkeypatch.setattr(session_naming, "host_id", own_host_id)
        monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief"])
            assert code == 0, err
            title = launch.call_args.kwargs["name"]
        token = title.rsplit("-", 1)[-1]
        assert title == f"hub-tst-chief-{token}"
        assert fleet.rows()[token]["label"] == "chief"
        assert json.loads(cos.registration_path(seeded_home).read_text())["native_agent_name"] == title

        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            stack.enter_context(patch("ai_cli.session._tmux_session_names", return_value=[f"c-{title}"]))
            code, _out, err = run_cli(["ai", "c", "--chief", "review"])
        assert code == 1
        assert "one chief per machine" in err
        assert not launch.called

    def test_given_a_positional_suffix_when_launched_then_it_sits_between_chief_and_the_index(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief", "review"])
            assert code == 0, err
            assert launch.call_args.kwargs["name"] == "chief-review"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-tst-chief-review-1"
        assert registration["tmux_target"] == "c-hub-tst-chief-review-1"

    def test_given_an_unsafe_suffix_when_launched_then_it_is_sanitized_like_any_session_name(self, seeded_home):
        with ExitStack() as stack:
            _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief", "Review/Queue"])
            assert code == 0, err
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-tst-chief-review-queue-1"

    @pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
    def test_given_any_engine_when_chief_then_that_engine_launches_as_the_chief(self, engine, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", engine, "-F"])
            assert code == 0, err
            kwargs = launch.call_args.kwargs
        assert kwargs["engine"] == engine
        assert kwargs["name"] == "chief"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["tmux_target"] == f"{engine}-hub-tst-chief-1"

    def test_given_no_worktree_requested_when_chief_then_isolation_is_off(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief", "-W"])
            assert code == 0, err
            assert launch.call_args.kwargs["no_worktree"] is True

    def test_given_explicit_home_and_key_when_chief_then_that_home_is_used(self, tmp_path, monkeypatch):
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

    def test_given_a_live_chief_when_chief_launched_again_then_attaches_to_it(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_agent_name": "hub-chief-1",
                    "tmux_target": "c-hub-chief-1",
                }
            )
        )
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", "g", "--chief"])
            launch.assert_not_called()
            launch.attach.assert_called_once_with("c-hub-chief-1")
        assert code == 0, err

    def test_given_chief_options_without_chief_when_launched_then_usage_error(self, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", "-k", "machine-key-1"])
        assert code == 1
        assert "--chief" in err
        launch.assert_not_called()

    def test_given_an_ordinary_session_when_launched_then_nothing_is_registered(self, seeded_home, monkeypatch):
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c"])
            assert code == 0, err
            assert launch.seen_env[cos.ROLE_ENV] is None
        assert not cos.registration_path(seeded_home).exists()

    def test_given_dry_run_when_chief_then_nothing_is_registered(self, seeded_home):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "--chief", "--dry-run"])
            assert code == 0, err
            assert launch.call_args.kwargs["dry_run"] is True
        assert not cos.registration_path(seeded_home).exists()
        assert not cos.registry_path(seeded_home).exists()

    @pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
    def test_given_help_when_asked_then_every_engine_documents_the_chief_options(self, engine):
        code, out, _err = run_cli(["ai", engine, "--help"])
        assert code == 0
        assert "--chief" in out
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


REMOTE_CONFIG = with_environment_config(
    {
        "remote": {
            "default": "hz",
            "machines": {
                "fw": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"},
                "hz": {"host": "hz.example.com", "user": "root", "port": 22, "identity_file": "", "transport": "ssh"},
            },
        }
    }
)


def _explode(*_args, **_kwargs):
    raise AssertionError("a remote chief dispatch resolved or wrote chief state on the dispatching machine")


def _dispatcher_patches(stack: ExitStack) -> None:
    """The dispatching machine as the Mac sees it: no chief home, no host alias for this repo.

    Every chief-home call and every network call is a tripwire, so the dispatching side is
    proved to resolve, check and write nothing about the chief: that is all the remote's.
    """
    for target in (
        "ai_cli.chief_of_staff.resolve_chief_home",
        "ai_cli.chief_of_staff.read_registration",
        "ai_cli.chief_of_staff.write_registration",
        "ai_cli.main.subprocess.run",
        "os.execvp",
    ):
        stack.enter_context(patch(target, side_effect=_explode))
    from ai_cli.session_naming import HostAliasError

    stack.enter_context(
        patch("ai_cli.session_naming.resolve_host_alias", side_effect=HostAliasError("no host alias here"))
    )
    stack.enter_context(patch("ai_cli.session.get_project_prefix", return_value="hub"))
    stack.enter_context(patch("ai_cli.config.get_current_project_name", return_value="myhub"))
    stack.enter_context(patch("ai_cli.direnv_setup.ensure_direnv"))
    real_which = shutil.which
    stack.enter_context(
        patch(
            "shutil.which",
            side_effect=lambda cmd, *a, **k: f"/usr/bin/{cmd}" if cmd in {"pi", "codex"} else real_which(cmd, *a, **k),
        )
    )


class TestRemoteChief:
    """A chief runs on whichever machine it is launched on; `-R` puts it on the remote one.

    Cross-machine messaging reaches it wherever it runs, so the dispatching machine only
    plans the ssh hand-off and forwards ``--chief``; the remote ``ai`` owns the chief
    home, the one-chief check, the registration and the host-aliased session name.
    """

    @pytest.mark.parametrize("command", [["cos"], ["c", "-F"], ["g", "-F"], ["p", "-F"], ["cx", "--chief"]])
    def test_given_remote_machine_when_chief_dry_run_then_plan_hands_the_chief_to_that_host(
        self, command, monkeypatch, tmp_path
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        engine = "c" if command == ["cos"] else command[0]
        with ExitStack() as stack:
            _dispatcher_patches(stack)
            code, out, err = run_cli(["ai", *command, "-R", "-m", "fw", "--dry-run"], config=REMOTE_CONFIG)
        assert code == 0, err
        assert "target host  dev@fw.example.com:22 (ssh)" in out
        assert f"remote cmd   ai {engine} --is-remote --project-prefix hub --project myhub --chief chief" in out
        assert "role         chief-of-staff" in out
        assert not (tmp_path / "state" / "chief").exists()

    def test_given_home_options_when_remote_chief_dry_run_then_they_reach_the_remote_ai_unexpanded(
        self, monkeypatch, tmp_path, click_expands_like_windows
    ):
        """The remote machine resolves ``~`` against its own home, so the dispatcher must not.

        Run with Click's Windows argument expansion switched on, on every OS: that expansion
        is what turned ``~/chief home`` into the dispatching machine's own path before it was
        forwarded (caught by test-windows on the first push).
        """
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "dispatcher-home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "dispatcher-home"))
        with ExitStack() as stack:
            _dispatcher_patches(stack)
            code, out, err = run_cli(
                ["ai", "cos", "-R", "-m", "fw", "-k", "key-x", "-H", "~/chief home", "review", "--dry-run"],
                config=REMOTE_CONFIG,
            )
        assert code == 0, err
        assert "--chief --machine-key key-x --fm-home '~/chief home' chief-review" in out
        assert "dispatcher-home" not in out

    @pytest.mark.parametrize(
        ("argv", "expands"),
        [
            (["cos", "-R"], False),
            (["c", "-F", "-H", "~/x"], False),
            (["-v", "cx"], False),
            (["migrate-transcript", "~/dest"], True),
            ([], True),
        ],
    )
    def test_given_a_command_when_windows_argv_expansion_decided_then_only_session_launches_opt_out(
        self, argv, expands
    ):
        from ai_cli.main import _windows_expand_args

        assert _windows_expand_args(argv) is expands

    def test_given_tilde_home_when_local_chief_launched_then_it_still_expands_here(
        self, tmp_path, monkeypatch, click_expands_like_windows
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        home = tmp_path / "dispatcher-home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        chief_home = home / "chief home"
        (chief_home / "config").mkdir(parents=True)
        (chief_home / "config" / "message-transports.json").write_text(VALID_POLICY)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "-F", "-k", "key-x", "-H", "~/chief home"])
            assert code == 0, err
        assert launch.seen_env[cos.FM_HOME_ENV] == str(chief_home)

    def test_given_unconfigured_remote_machine_when_chief_launched_then_refuses_before_anything(self, monkeypatch):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with ExitStack() as stack:
            _dispatcher_patches(stack)
            code, _out, err = run_cli(["ai", "cos", "-R", "-m", "nope", "--dry-run"], config=REMOTE_CONFIG)
        assert code == 1
        assert "nope" in err
        assert "fw, hz" in err

    def test_given_real_dispatch_when_unnumbered_remote_chief_launched_then_the_remote_host_picks_the_chief(
        self, monkeypatch, tmp_path
    ):
        """No slot is allocated here: the next free index would be a second chief the remote refuses."""
        from conftest import _run_cli_with_args

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        with (
            patch("ai_cli.main.sys.platform", "linux"),
            patch("ai_cli.chief_of_staff.write_registration", side_effect=_explode),
        ):
            runner = _run_cli_with_args(["ai", "cos", "-R", "-m", "fw"], REMOTE_CONFIG, capture_ssh_runner=True)
        runner.assert_called_once()
        ssh_args = runner.call_args[0][0]
        assert "dev@fw.example.com" in ssh_args
        assert ssh_args[-1].endswith("--chief chief'")

    def test_given_allocated_remote_slot_when_remote_side_runs_then_it_registers_that_slot_with_its_alias(
        self, seeded_home, monkeypatch
    ):
        from ai_cli import session_naming

        monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
        monkeypatch.setattr(session_naming, "resolve_host_alias", lambda: "fw")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(
                ["ai", "c", "--is-remote", "--project-prefix", "hub", "--chief", "c-r-hub-fw-chief-1"]
            )
            assert code == 0, err
            assert launch.call_args.kwargs["name"] == "c-r-hub-fw-chief-1"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-fw-chief-1"
        assert registration["tmux_target"] == "c-r-hub-fw-chief-1"
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"

    def test_given_live_chief_on_remote_when_remote_side_runs_then_refuses(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps({"schema_version": 1, "native_agent_name": "hub-fw-chief-1", "tmux_target": "c-hub-fw-chief-1"})
        )
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(
                ["ai", "c", "--is-remote", "--project-prefix", "hub", "--chief", "c-r-hub-fw-chief-2"]
            )
            launch.assert_not_called()
        assert code == REMOTE_LAUNCH_REFUSED_EXIT
        assert "already running" in err

    def test_given_no_host_alias_on_remote_when_remote_side_runs_then_refuses_without_registration(self, seeded_home):
        from ai_cli.session_naming import HostAliasError

        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(
                patch(
                    "ai_cli.session_naming.resolve_host_alias", side_effect=HostAliasError("no host alias configured")
                )
            )
            code, _out, err = run_cli(["ai", "c", "--is-remote", "--project-prefix", "hub", "--chief"])
            launch.assert_not_called()
        assert code == REMOTE_LAUNCH_REFUSED_EXIT
        assert "no host alias configured" in err
        assert not cos.registration_path(seeded_home).exists()


class TestChiefSessionName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("", "chief"),
            ("review", "chief-review"),
            ("2", "chief-2"),
            ("chief-review", "chief-review"),
            ("hub-fw-chief-1", "hub-fw-chief-1"),
            ("c-r-hub-fw-chief-1", "c-r-hub-fw-chief-1"),
        ],
    )
    def test_given_a_name_when_chief_named_then_chief_is_never_doubled(self, name, expected):
        assert cos.chief_session_name(name) == expected


def _real_plan(stack: ExitStack, monkeypatch):
    """Run the real launch up to its read-only local plan, recording the plan and the cwd."""
    from ai_cli import main, session_naming

    monkeypatch.setattr(session_naming, "resolve_host_alias", lambda: "fw")
    real_launch = main._do_session_launch
    launch = _launch_patches(stack, live=False, real_names=True)
    stack.enter_context(patch("ai_cli.session._resolve_is_remote", return_value=False))
    stack.enter_context(patch("ai_cli.tmux_setup.tmux_present", return_value=True))
    stack.enter_context(patch("ai_cli.direnv_setup.ensure_direnv"))
    plan = stack.enter_context(patch("ai_cli.main._print_launch_plan"))
    plan.side_effect = lambda **_kwargs: plan.__dict__.setdefault("cwd", Path.cwd())

    def dry_launch(**kwargs):
        kwargs["dry_run"] = True
        return real_launch(**kwargs)

    launch.side_effect = dry_launch
    return launch, plan


class TestChiefLaunchForms:
    """`ai c chief[-N]`, `ai c [N] --chief`, with or without -R and -p hub, are all the
    chief launch, named `hub-<host-alias>-chief-<N>` in myhub whatever the cwd."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["chief"], "hub-fw-chief-1"),
            (["chief-3"], "hub-fw-chief-3"),
            (["chief-3", "-p", "hub"], "hub-fw-chief-3"),
            (["chief", "-p", "myhub"], "hub-fw-chief-1"),
            (["--chief"], "hub-fw-chief-1"),
            (["2", "--chief"], "hub-fw-chief-2"),
            (["2", "-F", "-p", "hub"], "hub-fw-chief-2"),
        ],
    )
    def test_given_a_chief_form_from_another_repo_when_dry_run_then_environment_repo_chief_slot_is_planned(
        self, argv, expected, seeded_home, chief_project, monkeypatch, tmp_path
    ):
        other_repo = tmp_path / "projects" / "other-repo"
        other_repo.mkdir(parents=True)
        monkeypatch.chdir(other_repo)
        with ExitStack() as stack:
            _launch, plan = _real_plan(stack, monkeypatch)
            code, _out, err = run_cli(["ai", "c", *argv])
            assert code == 0, err
            plan.assert_called_once()
        assert plan.call_args.kwargs["ai_name"] == expected
        assert plan.call_args.kwargs["session_id"] == f"c-{expected}"
        assert plan.cwd == chief_project

    @pytest.mark.parametrize("name", ["chief", "chief-3"])
    def test_given_positional_chief_name_when_launched_then_it_registers_and_exports_like_the_flag(
        self, name, seeded_home, monkeypatch
    ):
        from ai_cli import session_naming

        monkeypatch.setattr(session_naming, "resolve_host_alias", lambda: "fw")
        monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
        monkeypatch.delenv(cos.ROLE_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", name])
            assert code == 0, err
            kwargs = launch.call_args.kwargs
        assert kwargs["project"] == CHIEF_PROJECT
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        assert launch.seen_env[cos.ROLE_ENV] == "chief-of-staff"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == ("hub-fw-chief-3" if name.endswith("3") else "hub-fw-chief-1")

    @pytest.mark.parametrize(
        ("argv", "remote_tail"),
        [
            (["chief-2", "-R"], "--chief chief-2"),
            (["chief", "-R", "-p", "hub"], "--chief chief"),
            (["2", "-R", "--chief"], "--chief chief-2"),
        ],
    )
    def test_given_a_chief_form_with_remote_when_dry_run_then_remote_environment_repo_chief_is_planned(
        self, argv, remote_tail, monkeypatch, tmp_path
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        with ExitStack() as stack:
            _dispatcher_patches(stack)
            stack.enter_context(patch("ai_cli.config.get_current_project_name", return_value="other-repo"))
            code, out, err = run_cli(["ai", "c", *argv, "-m", "fw", "--dry-run"], config=REMOTE_CONFIG)
        assert code == 0, err
        assert f"ai c --is-remote --project-prefix hub --project myhub {remote_tail}\n" in out

    @pytest.mark.parametrize("name", ["chief-x", "chief-review", "hub-fw-chief-x"])
    def test_given_a_malformed_chief_name_without_the_flag_when_launched_then_refuses(self, name, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", name])
        assert code == 1
        assert "chief-N" in err
        launch.assert_not_called()
        assert not cos.registration_path(seeded_home).exists()

    @pytest.mark.parametrize("argv", [["chief", "-p", "other-repo"], ["--chief", "-p", "other-repo"]])
    def test_given_another_project_when_chief_launched_then_refuses(self, argv, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", *argv])
        assert code == 1
        assert "always launches into myhub" in err
        launch.assert_not_called()

    def test_given_a_live_chief_when_positional_chief_launched_then_refuses(self, seeded_home):
        cos.registration_path(seeded_home).write_text(
            json.dumps({"schema_version": 1, "native_agent_name": "hub-fw-chief-1", "tmux_target": "c-hub-fw-chief-1"})
        )
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", "c", "chief-2"])
            launch.assert_not_called()
        assert code == 1
        assert "already running" in err

    def test_given_an_ordinary_name_when_launched_then_it_is_not_a_chief(self, seeded_home, monkeypatch):
        # Launched from inside a chief session, which exports both to its own shell.
        monkeypatch.setenv(cos.ROLE_ENV, cos.ROLE)
        monkeypatch.setenv(cos.FM_HOME_ENV, str(seeded_home))
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "review-2"])
            assert code == 0, err
            assert launch.seen_env[cos.ROLE_ENV] is None
            assert launch.seen_env[cos.FM_HOME_ENV] is None
        assert not cos.registration_path(seeded_home).exists()


# --- Attach / resume / create and the one naming convention (AI-CLI-au4q) ---
#
# Every chief form, with or without -r, -R and -p hub: running -> attach to the tmux
# session the registration (or tmux) names, writing nothing; tmux alive but CC exited,
# registered but not running, or a saved transcript -> relaunch under the same name;
# nothing -> create (with -r: say there is nothing to resume); another N -> refused.
# Names are c-hub-fw-chief-N locally, c-r-hub-fw-chief-N when launched from
# another machine, never prefixed twice.

REMOTE_SIDE = ["c", "--is-remote", "--project-prefix", "hub", "--project", "myhub", "--chief"]


def _register(home: Path, agent: str = "hub-fw-chief-1", target: str = "c-hub-fw-chief-1") -> dict:
    registration = {"schema_version": 1, "native_agent_name": agent, "tmux_target": target}
    cos.registration_path(home).write_text(json.dumps(registration))
    return registration


@pytest.fixture
def fw_alias(monkeypatch):
    from ai_cli import session_naming

    monkeypatch.setattr(session_naming, "resolve_host_alias", lambda: "fw")
    monkeypatch.delenv(cos.FM_HOME_ENV, raising=False)
    monkeypatch.delenv(cos.ROLE_ENV, raising=False)


class TestAttach:
    @pytest.mark.parametrize(
        "argv",
        [
            ["c", "chief-1"],
            ["c", "chief", "1"],
            ["c", "chief-1", "-p", "hub"],
            ["c", "--chief", "1"],
            ["c", "--chief"],
            ["cos"],
            ["cos", "1", "-r"],
            ["mychief", "1", "-r"],
            ["c", "--mychief", "1"],
        ],
    )
    def test_given_a_running_chief_when_any_form_names_it_then_attaches_and_writes_nothing(
        self, argv, seeded_home, fw_alias
    ):
        registration = _register(seeded_home)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", *argv])
            launch.assert_not_called()
            launch.attach.assert_called_once_with("c-hub-fw-chief-1")
        assert code == 0, err
        assert json.loads(cos.registration_path(seeded_home).read_text()) == registration
        assert not cos.registry_path(seeded_home).exists()

    def test_given_a_remote_launched_chief_when_attached_locally_then_its_registered_c_r_session_is_used(
        self, seeded_home, fw_alias
    ):
        """Measured 2026-10-13: the chief launched from the Mac runs as c-r-hub-fw-chief-1 on fw."""
        _register(seeded_home, target="c-r-hub-fw-chief-1")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", "c", "chief-1"])
            launch.attach.assert_called_once_with("c-r-hub-fw-chief-1")
        assert code == 0, err

    @pytest.mark.parametrize("name", ["c-r-hub-fw-chief-1", "c-hub-fw-chief-1", "chief-1", "chief"])
    def test_given_a_running_chief_when_the_remote_side_resumes_any_allocated_name_then_it_attaches(
        self, name, seeded_home, fw_alias
    ):
        """The remote `ai` receives the allocator's full name; it must never be prefixed again."""
        _register(seeded_home, target="c-r-hub-fw-chief-1")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", *REMOTE_SIDE, "--resume", name])
            launch.attach.assert_called_once_with("c-r-hub-fw-chief-1")
        assert code == 0, err
        assert "c-r-hub-fw-c-r" not in err

    def test_given_a_running_chief_when_dry_run_then_the_plan_is_an_attach_and_exit_zero(self, seeded_home, fw_alias):
        _register(seeded_home)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, out, err = run_cli(["ai", "c", "chief-1", "--dry-run"])
            launch.attach.assert_not_called()
            launch.assert_not_called()
        assert code == 0, err
        assert "action     attach to tmux session c-hub-fw-chief-1" in out
        assert "created    nothing" in out
        assert not cos.registry_path(seeded_home).exists()

    def test_given_a_running_chief_when_another_n_is_asked_for_then_refuses_naming_the_attach_command(
        self, seeded_home, fw_alias
    ):
        _register(seeded_home)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            code, _out, err = run_cli(["ai", "c", "chief-2"])
            launch.assert_not_called()
            launch.attach.assert_not_called()
        assert code == 1
        assert "already running" in err
        assert "`ai c chief-1`" in err
        assert "`ai c chief-1 -R -p hub`" in err

    def test_given_an_unregistered_chief_in_tmux_when_asked_for_then_attaches_and_registers_it(
        self, seeded_home, fw_alias
    ):
        """A later launch overwrote the registration and died; the real chief still runs."""
        _register(seeded_home, agent="hub-fw-chief-2", target="c-r-hub-fw-chief-2")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(
                patch("ai_cli.session._tmux_session_names", return_value=["c-r-hub-fw-chief-1", "c-r-hub-1"])
            )
            code, _out, err = run_cli(["ai", "c", "chief-1"])
            launch.attach.assert_called_once_with("c-r-hub-fw-chief-1")
        assert code == 0, err
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert (registration["native_agent_name"], registration["tmux_target"]) == (
            "hub-fw-chief-1",
            "c-r-hub-fw-chief-1",
        )


class TestResumeAndCreate:
    def test_given_tmux_alive_but_cc_exited_when_launched_then_resumes_under_the_same_name(self, seeded_home, fw_alias):
        _register(seeded_home)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=True, real_names=True)
            stack.enter_context(patch("ai_cli.chief_of_staff.tmux_panes_all_dead", return_value=True))
            code, _out, err = run_cli(["ai", "cos", "-r"])
            launch.attach.assert_not_called()
            kwargs = launch.call_args.kwargs
        assert code == 0, err
        assert kwargs["name"] == "chief-1"
        assert kwargs["resume"] is False
        assert "its process has exited; resuming it as chief-1" in err

    def test_given_a_registered_chief_not_running_when_resumed_then_relaunches_it_by_name(self, seeded_home, fw_alias):
        _register(seeded_home, target="c-r-hub-fw-chief-1")
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "mychief", "1", "-r"])
            kwargs = launch.call_args.kwargs
        assert code == 0, err
        assert kwargs["name"] == "chief-1"
        assert kwargs["resume"] is False
        assert json.loads(cos.registration_path(seeded_home).read_text())["tmux_target"] == "c-hub-fw-chief-1"

    def test_given_nothing_running_but_a_saved_transcript_when_resumed_then_relaunches_it(self, seeded_home, fw_alias):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(
                patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[Path("t.jsonl")])
            )
            code, _out, err = run_cli(["ai", "c", "chief-1", "-r"])
            kwargs = launch.call_args.kwargs
        assert code == 0, err
        assert kwargs["name"] == "chief-1"
        assert kwargs["resume"] is False

    @pytest.mark.parametrize("argv", [["c", "chief-1", "-r"], [*REMOTE_SIDE, "--resume", "c-r-hub-fw-chief-1"]])
    def test_given_nothing_at_all_when_resumed_then_says_so_last_and_writes_nothing(self, argv, seeded_home, fw_alias):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            stack.enter_context(patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[]))
            code, _out, err = run_cli(["ai", *argv])
            launch.assert_not_called()
        assert code == (REMOTE_LAUNCH_REFUSED_EXIT if "--is-remote" in argv else 1)
        last = err.strip().splitlines()[-1]
        assert "no resumable chief session for hub-fw-chief-1 on workstation" in last
        assert "start one with `ai c chief-1`" in last
        assert "c-r-hub-fw-c-r" not in err
        assert not cos.registration_path(seeded_home).exists()

    def test_given_nothing_at_all_when_launched_then_creates_and_registers(self, seeded_home, fw_alias):
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "chief", "2"])
            kwargs = launch.call_args.kwargs
        assert code == 0, err
        assert kwargs["name"] == "chief-2"
        assert kwargs["extra_args"] == []
        assert json.loads(cos.registration_path(seeded_home).read_text())["native_agent_name"] == "hub-fw-chief-2"


class TestNaming:
    @pytest.mark.parametrize(
        ("argv", "session_id"),
        [
            (["c", "chief-1"], "c-hub-fw-chief-1"),
            (["c", "chief", "2"], "c-hub-fw-chief-2"),
            (["c", "chief-2", "-p", "hub"], "c-hub-fw-chief-2"),
            (["c", "--chief", "2"], "c-hub-fw-chief-2"),
            (["cos", "2"], "c-hub-fw-chief-2"),
            (["mychief", "1"], "c-hub-fw-chief-1"),
            (["c", "--mychief", "1"], "c-hub-fw-chief-1"),
            ([*REMOTE_SIDE, "c-r-hub-fw-chief-2"], "c-r-hub-fw-chief-2"),
            ([*REMOTE_SIDE, "chief-2"], "c-r-hub-fw-chief-2"),
            ([*REMOTE_SIDE, "chief"], "c-r-hub-fw-chief-1"),
            ([*REMOTE_SIDE, "c-hub-fw-chief-1"], "c-r-hub-fw-chief-1"),
        ],
    )
    def test_given_every_create_path_when_planned_then_the_session_name_is_prefixed_once(
        self, argv, session_id, seeded_home, monkeypatch
    ):
        with ExitStack() as stack:
            _launch, plan = _real_plan(stack, monkeypatch)
            if "--is-remote" in argv:
                stack.enter_context(patch("ai_cli.session._resolve_is_remote", return_value=True))
            code, _out, err = run_cli(["ai", *argv])
            assert code == 0, err
        assert plan.call_args.kwargs["session_id"] == session_id

    @pytest.mark.parametrize("prefix", ["c-hub-fw-", "c-r-hub-fw-", "cx-r-hub-fw-", "g-hub-"])
    @pytest.mark.parametrize("name", ["1", "chief-1", "chief", "c-r-hub-fw-chief-1", "c-hub-fw-chief-2"])
    def test_given_any_name_when_compiled_twice_then_the_second_pass_changes_nothing(self, prefix, name):
        from ai_cli.session import session_name_for

        once = session_name_for(prefix, name)
        assert session_name_for(prefix, once) == once
        assert once.count("hub-fw-") <= 1

    def test_given_a_full_remote_name_not_running_when_resolved_then_nothing_is_double_prefixed(self):
        from ai_cli.session import resolve_session

        probes: list[list[str]] = []

        def tmux(cmd, **_kwargs):
            probes.append(cmd)
            return types.SimpleNamespace(returncode=0 if "list-sessions" in cmd else 1, stdout="c-r-hub-1\n")

        with patch("subprocess.run", side_effect=tmux):
            assert resolve_session("c-r-hub-fw-", "c-r-hub-fw-chief-1") == ""
        assert not any("c-r-hub-fw-c-r" in " ".join(cmd) for cmd in probes)


class TestRemoteForms:
    @pytest.mark.parametrize(
        ("argv", "tail"),
        [
            (["cos", "1", "-r"], "--chief --resume chief-1"),
            (["c", "chief", "1", "-r"], "--chief --resume chief-1"),
            (["c", "chief-1", "-r"], "--chief --resume chief-1"),
            (["mychief", "1", "-r"], "--chief --resume chief-1"),
            (["c", "--mychief", "1", "-r"], "--chief --resume chief-1"),
            (["c", "--chief", "1", "-r"], "--chief --resume chief-1"),
            (["c", "chief-1", "-p", "hub"], "--chief chief-1"),
        ],
    )
    def test_given_every_chief_form_with_remote_when_dry_run_then_the_same_remote_command_is_planned(
        self, argv, tail, monkeypatch
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with ExitStack() as stack:
            _dispatcher_patches(stack)
            code, out, err = run_cli(["ai", *argv, "-R", "-m", "fw", "--dry-run"], config=REMOTE_CONFIG)
        assert code == 0, err
        assert f"remote cmd   ai c --is-remote --project-prefix hub --project myhub {tail}\n" in out
        assert "attach to its running chief, resume a registered chief" in out


class TestProjectOptionWins:
    def test_given_another_repos_cwd_when_p_names_a_repo_then_that_repo_root_is_the_launch_root(
        self, seeded_home, chief_project, monkeypatch, tmp_path
    ):
        other_repo = tmp_path / "projects" / "other-repo"
        other_repo.mkdir(parents=True)
        monkeypatch.chdir(other_repo)
        with ExitStack() as stack:
            _launch, plan = _real_plan(stack, monkeypatch)
            from ai_cli import direnv_setup

            code, _out, err = run_cli(["ai", "c", "7", "-p", "hub"])
            assert code == 0, err
            direnv_root = direnv_setup.ensure_direnv.call_args[0][0]
        assert plan.cwd == chief_project
        assert plan.call_args.kwargs["session_id"] == "c-hub-fw-7"
        assert direnv_root == chief_project

    @pytest.mark.parametrize("argv", [["c", "chief-1", "-p", "-hub"], ["c", "7", "-p", "-hub"]])
    def test_given_a_dash_led_project_when_launched_then_the_typo_is_named(self, argv, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", *argv])
        assert code == 1
        assert "Did you mean `-p hub`?" in err
        assert "drop -p" not in err
        launch.assert_not_called()
        assert not cos.registration_path(seeded_home).exists()

    def test_given_another_repo_with_a_chief_when_launched_then_refuses_naming_the_chief_alias(self, seeded_home):
        with patch("ai_cli.main._do_session_launch") as launch:
            code, _out, err = run_cli(["ai", "c", "chief-1", "-p", "other-repo"])
        assert code == 1
        assert "-p/--project other-repo names another project" in err
        assert "pass `-p hub`" in err
        launch.assert_not_called()


class TestRemoteRelaunchOfAChief:
    """`ai c chief-1 -R` from another machine, as the remote `ai` receives it (AI-CLI-a8h5).

    The dispatcher runs the remote side as ``<shell> -l -c 'ai c --is-remote ...'``. That
    shell is login but not interactive, so a machine key exported below an rc file's
    "not interactive: return" guard never reaches it. Measured on the reporting host: the
    key was unset under ``bash -l -c`` and set under ``bash -i``, so every reattach refused
    with "no machine key" and the local reconnect ladder retried that refusal ten times.
    """

    def test_given_no_machine_key_in_the_remote_shell_when_the_remote_side_launches_then_the_one_seeded_home_is_used(
        self, seeded_home, fw_alias, monkeypatch
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", *REMOTE_SIDE, "chief-1"])
            launch.assert_called_once()
        assert code == 0, err
        assert launch.seen_env[cos.FM_HOME_ENV] == str(seeded_home)
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["machine_key"] == "machine-key-1"

    def test_given_two_seeded_homes_and_no_machine_key_when_the_remote_side_launches_then_it_refuses_naming_both(
        self, seeded_home, fw_alias, monkeypatch
    ):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        other = seeded_home.parent / "machine-key-2"
        shutil.copytree(seeded_home, other)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", *REMOTE_SIDE, "chief-1"])
            launch.assert_not_called()
        assert code == REMOTE_LAUNCH_REFUSED_EXIT
        assert "machine-key-1, machine-key-2" in err
        assert "-k/--machine-key" in err

    def test_given_a_chief_registered_under_an_older_prefix_when_relaunched_then_refuses_naming_session_migrate(
        self, seeded_home, fw_alias, chief_project
    ):
        """The host now names chiefs `hub-fw-...`; the registered one is `hub-chief-1`.

        Relaunching under the new name would open a fresh worktree and a new conversation
        and strand the old ones, while saying it resumed them.
        """
        registration = _register(seeded_home, agent="hub-chief-1", target="c-hub-chief-1")
        (chief_project / ".worktrees" / "hub-chief-1").mkdir(parents=True)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", *REMOTE_SIDE, "chief-1"])
            launch.assert_not_called()
        assert code == REMOTE_LAUNCH_REFUSED_EXIT
        assert f"ai migrate-session hub-chief-1 hub-fw-chief-1 -r {chief_project}" in err
        assert json.loads(cos.registration_path(seeded_home).read_text()) == registration
        assert not (chief_project / ".worktrees" / "hub-fw-chief-1").exists()

    def test_given_the_old_chief_already_migrated_when_relaunched_by_its_old_name_then_resumes_under_the_new_one(
        self, seeded_home, fw_alias, chief_project
    ):
        _register(seeded_home, agent="hub-chief-1", target="c-hub-chief-1")
        (chief_project / ".worktrees" / "hub-fw-chief-1").mkdir(parents=True)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", *REMOTE_SIDE, "chief-1"])
            kwargs = launch.call_args.kwargs
        assert code == 0, err
        assert kwargs["name"] == "chief-1"
        registration = json.loads(cos.registration_path(seeded_home).read_text())
        assert registration["native_agent_name"] == "hub-fw-chief-1"

    def test_given_a_local_launch_refusal_when_not_remote_then_it_still_exits_one(self, monkeypatch, fw_alias):
        monkeypatch.delenv(cos.MACHINE_KEY_ENV, raising=False)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False, real_names=True)
            code, _out, err = run_cli(["ai", "c", "chief-1"])
            launch.assert_not_called()
        assert code == 1
        assert "no machine key" in err


@pytest.mark.skipif(importlib.util.find_spec("fcntl") is None, reason="POSIX-only: fcntl advisory locks")
class TestRegistryLock:
    """``ai cos`` holds the registry lock across its whole load-change-save.

    The other writer of ``registry.json`` takes ``fcntl.flock(LOCK_EX)`` on
    ``<home>/registry.json.lock`` around each load-change-save and replaces the file whole.
    """

    def test_given_a_locked_writer_mid_change_when_chief_launched_then_it_waits_and_the_writer_change_survives(
        self, seeded_home
    ):
        import fcntl
        import threading

        registry = cos.registry_path(seeded_home)
        registry.write_text(json.dumps({"schema_version": 2, "vp_sessions": [{"vp_id": "vp/myproject/old"}]}))
        writer_has_lock = threading.Event()
        cos_loaded = threading.Event()
        writer_saved = threading.Event()
        order: list[str] = []

        def locked_writer():
            fd = os.open(seeded_home / "registry.json.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                data = json.loads(registry.read_text())
                data["vp_sessions"] = [{"vp_id": "vp/myproject/rekeyed"}]
                writer_has_lock.set()
                # An unlocked launcher loads mid-change; a locked one cannot load until release.
                cos_loaded.wait(timeout=1.0)
                tmp = registry.with_name("registry.json.writer-tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(registry)
                writer_saved.set()
                order.append("writer released")
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

        real_read = cos._read_json_object

        def observed_read(path, what):
            data = real_read(path, what)
            if path == registry:
                order.append("cos loaded")
                cos_loaded.set()
                writer_saved.wait(timeout=5.0)
            return data

        writer = threading.Thread(target=locked_writer)
        writer.start()
        assert writer_has_lock.wait(timeout=5.0)
        try:
            with ExitStack() as stack:
                _launch_patches(stack, live=False)
                stack.enter_context(patch("ai_cli.chief_of_staff._read_json_object", side_effect=observed_read))
                code, _out, err = run_cli(["ai", "cos"])
        finally:
            writer.join(timeout=10.0)
        assert code == 0, err
        assert order[:2] == ["writer released", "cos loaded"]
        final = json.loads(registry.read_text())
        assert final["vp_sessions"] == [{"vp_id": "vp/myproject/rekeyed"}]
        assert final["chief_routes"]["native_agent_name"] == "hub-chief-1"
        assert final["chief_generation"] == 1


def _config_without(key: str) -> dict:
    config = with_environment_config()
    del config["chief_of_staff"][key]
    return config


class TestChiefSettings:
    """The names that tie a chief to one environment come from ``[chief_of_staff]``, never code."""

    @pytest.mark.parametrize("key", ["project", "session_name", "instance_key_prefix", "homes_dir"])
    def test_given_a_required_setting_unset_when_cos_launched_then_refuses_naming_it_and_writes_nothing(
        self, key, seeded_home, monkeypatch
    ):
        config = _config_without(key)
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, err = _run_cli(["ai", "cos"], config=config)
            launch.assert_not_called()
        assert code == 1
        assert f"chief_of_staff.{key} is not set" in err
        assert not cos.registration_path(seeded_home).exists()
        assert not cos.registry_path(seeded_home).exists()

    def test_given_homes_dir_unset_when_cos_names_its_home_then_launches_without_it(self, seeded_home, monkeypatch):
        config = _config_without("homes_dir")
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        with ExitStack() as stack:
            launch = _launch_patches(stack, live=False)
            code, _out, err = _run_cli(["ai", "cos", "-H", str(seeded_home)], config=config)
            launch.assert_called_once()
        assert code == 0, err
        assert cos.registration_path(seeded_home).exists()

    def test_given_a_configured_instance_key_prefix_when_registered_then_the_registry_key_carries_it(
        self, seeded_home, monkeypatch
    ):
        config = with_environment_config({"chief_of_staff": {"instance_key_prefix": "other-env-chief"}})
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        with ExitStack() as stack:
            _launch_patches(stack, live=False)
            code, _out, err = _run_cli(["ai", "cos"], config=config)
        assert code == 0, err
        registry = json.loads(cos.registry_path(seeded_home).read_text())
        assert registry["chief_instance_key"] == "other-env-chief/machine-key-1"

    def test_given_an_absolute_homes_dir_when_resolved_then_it_is_used_as_given(self, tmp_path, monkeypatch):
        homes = tmp_path / "chief-homes"
        config = with_environment_config({"chief_of_staff": {"homes_dir": str(homes)}})
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        assert cos.resolve_chief_home(None, "key-1") == homes / "key-1"

    def test_given_a_configured_session_name_when_named_positionally_then_it_is_the_chief_launch(self, monkeypatch):
        config = with_environment_config({"chief_of_staff": {"session_name": "Lead"}})
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        assert cos.is_chief_positional("lead-2")
        assert cos.chief_session_name("review") == "lead-review"
        assert not cos.is_chief_positional("chief-2")

    def test_given_no_session_name_configured_when_a_name_is_checked_then_nothing_is_a_chief(self, monkeypatch):
        config = _config_without("session_name")
        monkeypatch.setattr("ai_cli.config.load_config", lambda *_args, **_kwargs: config)
        assert not cos.is_chief_positional("chief-1")
        assert not cos.names_a_chief("myproject-box-chief-1")


def _dispatched(argv, config):
    """The ``(engine, options)`` an ``ai`` command line hands the session dispatcher."""
    with patch("ai_cli.main._dispatch_session") as dispatch:
        code, _out, err = _run_cli(["ai", *argv], config=config)
    assert code == 0, err
    engine, _ctx, options = dispatch.call_args.args
    return engine, options


class TestChiefAliases:
    """``command_aliases`` and ``flag_aliases`` are synonyms for ``ai cos`` and ``--chief``, read from config."""

    def test_given_a_configured_command_alias_when_invoked_then_it_dispatches_exactly_like_cos(self):
        config = with_environment_config()

        assert _dispatched(["mychief", "2", "-r"], config) == _dispatched(["cos", "2", "-r"], config)

    @pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
    def test_given_a_configured_flag_alias_when_passed_then_it_dispatches_exactly_like_chief(self, engine):
        config = with_environment_config()

        aliased = _dispatched([engine, "--mychief", "2"], config)

        assert aliased == _dispatched([engine, "--chief", "2"], config)
        assert aliased[1]["chief"] is True

    def test_given_no_command_aliases_configured_when_the_alias_is_invoked_then_it_is_no_command(self):
        config = _config_without("command_aliases")
        with patch("ai_cli.main._dispatch_session") as dispatch:
            code, _out, err = _run_cli(["ai", "mychief"], config=config)
        assert code == 1
        assert "No such command 'mychief'" in err
        dispatch.assert_not_called()

    def test_given_no_flag_aliases_configured_when_the_alias_is_passed_then_it_is_not_the_chief(self):
        engine, options = _dispatched(["c", "--mychief", "2"], _config_without("flag_aliases"))

        assert engine == "c"
        assert options["chief"] is False

    @pytest.mark.parametrize("argv", [["cos"], ["ls"]])
    def test_given_a_command_alias_naming_a_built_in_when_any_command_runs_then_refuses_naming_it(self, argv):
        config = with_environment_config({"chief_of_staff": {"command_aliases": ["ls"]}})
        with patch("ai_cli.main._dispatch_session") as dispatch, patch("ai_cli.main._do_ls") as do_ls:
            code, _out, err = _run_cli(["ai", *argv], config=config)
        assert code == 1
        assert "command_aliases names 'ls', which is already the `ai ls` command" in err
        dispatch.assert_not_called()
        do_ls.assert_not_called()

    @pytest.mark.parametrize("alias", ["resume", "chief", "help"])
    def test_given_a_flag_alias_naming_an_existing_option_when_launched_then_refuses_naming_it(self, alias):
        config = with_environment_config({"chief_of_staff": {"flag_aliases": [alias]}})
        with patch("ai_cli.main._dispatch_session") as dispatch:
            code, _out, err = _run_cli(["ai", "c", "--chief"], config=config)
        assert code == 1
        assert f"flag_aliases names {alias!r}, but --{alias} is already an option of `ai c`" in err
        dispatch.assert_not_called()

    @pytest.mark.parametrize(
        ("key", "value"),
        [("command_aliases", "mychief"), ("flag_aliases", ["--mychief"]), ("command_aliases", [""])],
    )
    def test_given_an_alias_setting_that_is_not_a_list_of_bare_names_when_launched_then_refuses_naming_it(
        self, key, value
    ):
        config = with_environment_config({"chief_of_staff": {key: value}})
        with patch("ai_cli.main._dispatch_session") as dispatch:
            code, _out, err = _run_cli(["ai", "c", "--chief"], config=config)
        assert code == 1
        assert f"chief_of_staff.{key} must be a list of bare names" in err
        dispatch.assert_not_called()
