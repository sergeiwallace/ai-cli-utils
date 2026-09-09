"""zsh provisioning: install where the host cannot take it away, adopt at launch.

WHY THIS EXISTS (AI-CLI-s2q2). ``session_script.SESSION_SHELL_PREFERENCE`` is
``("zsh", "bash")``, so zsh is the interpreter a session prefers, and it is the
macOS default login shell. On a host without zsh the 16 real-shell signal
regressions in ``test_stale_session_reaper.py`` skip — which means the shell
production prefers is the one nothing verifies, while bash, the fallback, is fully
covered.

Two contracts carry the weight, and both were learned from AI-CLI-i2ih:

* **The install target must survive a host rebuild.** A conda base prefix is a fine
  place to put a tool until that prefix is on an ephemeral filesystem — measured on
  a managed notebook host, where ``conda info`` reports ``/opt/conda`` and ``df``
  puts it on an overlay mounted at ``/`` while only ``$HOME`` persists. Installing
  there yields a tool that silently vanishes on every restart.
* **A launch adopts; it never installs.** Adoption is a stat and a PATH edit.
  Installation is a package-manager solve measured in minutes, and putting one on
  the launch path would stall every session on exactly the hosts where it cannot
  succeed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import native_deps, zsh_setup
from ai_cli.native_deps import InstallResult, LoaderRepair


def _version(returncode: int = 0, stdout: str = "zsh 5.9 (x86_64-pc-linux-gnu)\n"):
    return subprocess.CompletedProcess(args=["zsh", "--version"], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Where it installs, which is the whole point
# ---------------------------------------------------------------------------


class TestProvisioningTarget:
    def test_given_a_prefix_is_needed_when_resolved_then_it_is_under_the_user_data_dir(self):
        """Not the package manager's default prefix. That is the bug, not the fix."""
        prefix = native_deps.native_prefix()

        assert prefix.name == "native"
        assert "ai-cli-utils" in prefix.parts
        # The one thing that must never be true, however conda is configured here.
        assert not str(prefix).startswith("/opt/conda"), str(prefix)

    def test_given_the_user_data_dir_when_resolved_then_the_prefix_follows_it(self, monkeypatch, tmp_path):
        """It tracks XDG rather than hardcoding a path, so an operator who moves
        their data directory does not get a tool installed somewhere they did not
        choose."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "linux")

        assert native_deps.native_prefix() == tmp_path / "ai-cli-utils" / "native"

    def test_given_windows_when_the_bin_dir_is_resolved_then_it_is_scripts_not_bin(self, tmp_path):
        """conda puts executables in Scripts\\ on Windows. Getting this wrong would
        make provisioning silently unfindable there rather than loudly broken."""
        with patch.object(sys, "platform", "win32"):
            assert native_deps.prefix_bin(tmp_path).name == "Scripts"
        with patch.object(sys, "platform", "linux"):
            assert native_deps.prefix_bin(tmp_path).name == "bin"

    def test_given_an_empty_prefix_when_installers_are_built_then_they_create_it(self, tmp_path):
        """``install`` needs an existing environment and ``create`` refuses a
        populated one, so the verb is chosen by probing rather than by guessing at
        whichever error message the installed version happens to emit."""
        candidates = native_deps.prefix_installers("zsh", tmp_path)

        assert candidates, "there must be at least one rootless candidate"
        for probe, argv in candidates:
            assert argv[0] == probe
            assert argv[1] == "create"
            assert "--prefix" in argv and str(tmp_path) in argv
            assert "conda-forge" in argv
            assert "zsh" in argv

    def test_given_an_existing_environment_when_installers_are_built_then_they_install_into_it(self, tmp_path):
        (tmp_path / "conda-meta").mkdir()

        for _probe, argv in native_deps.prefix_installers("zsh", tmp_path):
            assert argv[1] == "install"

    def test_given_prefix_installers_when_built_then_none_of_them_need_root(self, tmp_path):
        """These are the only candidates a launch could ever tolerate, so a system
        manager sneaking in here would reintroduce the password-prompt hazard."""
        for _probe, argv in native_deps.prefix_installers("zsh", tmp_path):
            assert argv[0] not in native_deps._ROOT_MANAGERS

    def test_every_system_candidate_argv_is_non_interactive(self):
        """An installer that can stop and ask a question would hang whatever ran it."""
        non_interactive = ("-y", "--noconfirm", "--non-interactive")
        for platform, candidates in zsh_setup._SYSTEM_INSTALLERS.items():
            for probe, argv in candidates:
                if probe == "brew":  # non-interactive by default
                    continue
                assert any(flag in argv for flag in non_interactive), (platform, argv)

    def test_windows_has_no_system_candidate_because_native_zsh_does_not_exist(self):
        assert "win32" not in zsh_setup._SYSTEM_INSTALLERS


# ---------------------------------------------------------------------------
# Adoption: the launch-time half
# ---------------------------------------------------------------------------


class TestAdoption:
    def test_given_a_provisioned_prefix_when_adopted_then_its_bin_goes_first_on_path(self, tmp_path):
        binary_dir = native_deps.prefix_bin(tmp_path)
        binary_dir.mkdir(parents=True)
        env = {"PATH": "/usr/bin"}

        assert native_deps.adopt_prefix_bin(env, tmp_path) is True

        assert env["PATH"].split(os.pathsep)[0] == str(binary_dir)
        assert "/usr/bin" in env["PATH"], "the operator's own PATH must survive"

    def test_given_no_provisioned_prefix_when_adopted_then_path_is_untouched(self, tmp_path):
        """Never put a nonexistent directory on PATH. It is silently ignored, which
        would make a failed provision look like a successful one."""
        env = {"PATH": "/usr/bin"}

        assert native_deps.adopt_prefix_bin(env, tmp_path / "absent") is False

        assert env["PATH"] == "/usr/bin"

    def test_given_an_already_adopted_prefix_when_adopted_again_then_path_does_not_grow(self, tmp_path):
        """Called on every launch, so a non-idempotent version would grow PATH
        without bound across re-execs."""
        binary_dir = native_deps.prefix_bin(tmp_path)
        binary_dir.mkdir(parents=True)
        env = {"PATH": "/usr/bin"}

        native_deps.adopt_prefix_bin(env, tmp_path)
        first = env["PATH"]
        native_deps.adopt_prefix_bin(env, tmp_path)

        assert env["PATH"] == first

    def test_given_an_empty_path_when_adopted_then_no_stray_separator_is_written(self, tmp_path):
        binary_dir = native_deps.prefix_bin(tmp_path)
        binary_dir.mkdir(parents=True)
        env: dict[str, str] = {}

        native_deps.adopt_prefix_bin(env, tmp_path)

        assert env["PATH"] == str(binary_dir)

    def test_given_a_provisioned_zsh_when_the_session_shell_is_resolved_then_it_is_preferred(self, tmp_path):
        """The launch-path contract, at the one seam that decides the interpreter.

        Uses a real executable on a real PATH rather than a patched ``which``, so
        the test exercises the same lookup the launcher does.
        """
        from ai_cli import session_script

        binary_dir = native_deps.prefix_bin(tmp_path)
        binary_dir.mkdir(parents=True)
        fake_zsh = binary_dir / ("zsh.exe" if sys.platform == "win32" else "zsh")
        fake_zsh.write_text("#!/bin/sh\nexit 0\n")
        fake_zsh.chmod(0o755)

        with (
            patch.object(native_deps, "native_prefix", return_value=tmp_path),
            patch.dict(os.environ, {"PATH": "/nonexistent-for-this-test"}, clear=False),
        ):
            resolved = session_script.resolve_session_shell()

        assert resolved is not None
        assert Path(resolved) == fake_zsh, resolved

    def test_given_the_prefix_cannot_even_be_resolved_when_adopted_then_it_reports_false(self):
        """Adoption runs from ``resolve_session_shell`` on the launch path, so an
        exception here takes down the session it exists to improve.

        A real instance, not a hypothetical: resolving the prefix consults
        ``sys.platform``, and a Windows-simulating test on a POSIX host makes
        ``get_xdg_data_home`` build a ``WindowsPath``, which raises
        ``NotImplementedError``. "Cannot work out where our prefix would be" must
        mean "there is nothing to adopt".
        """
        env = {"PATH": "/usr/bin"}
        with patch.object(sys, "platform", "win32"):
            assert native_deps.adopt_prefix_bin(env) is False
        assert env["PATH"] == "/usr/bin"

        with patch.object(native_deps, "prefix_bin", side_effect=RuntimeError("boom")):
            assert native_deps.adopt_prefix_bin(env) is False

    def test_given_adoption_when_it_runs_then_it_never_invokes_an_installer(self, tmp_path):
        """The whole reason adoption and installation are separate functions. A
        package-manager solve on the launch path is minutes of stall per session."""
        with (
            patch.object(native_deps, "native_prefix", return_value=tmp_path),
            patch("subprocess.run", side_effect=AssertionError("adoption spawned a process")),
        ):
            assert native_deps.adopt_prefix_bin() is False


# ---------------------------------------------------------------------------
# ensure_zsh
# ---------------------------------------------------------------------------


class TestEnsureZsh:
    def test_given_zsh_already_runs_when_ensured_then_nothing_is_installed(self):
        with (
            patch("ai_cli.zsh_setup.zsh_runs", return_value=True),
            patch("ai_cli.zsh_setup.install_zsh") as install,
            patch("ai_cli.native_deps.adopt_prefix_bin", return_value=True),
        ):
            result = zsh_setup.ensure_zsh(quiet=True)

        install.assert_not_called()
        assert result.installed is True
        assert result.tool == "already-present"

    def test_given_zsh_is_absent_when_ensured_then_it_is_provisioned(self):
        with (
            patch("ai_cli.zsh_setup.zsh_runs", return_value=False),
            patch("ai_cli.zsh_setup.zsh_present", return_value=False),
            patch("ai_cli.zsh_setup.install_zsh", return_value=InstallResult(True, tool="micromamba")) as install,
            patch("ai_cli.native_deps.adopt_prefix_bin", return_value=False),
        ):
            result = zsh_setup.ensure_zsh(quiet=True)

        install.assert_called_once()
        assert result.installed is True

    def test_given_zsh_is_present_but_broken_when_ensured_then_the_loader_is_repaired_first(self):
        """A conda-provisioned shell keeps its libraries inside the prefix, so a
        moved or partly removed prefix is exactly the loader failure tmux hit --
        and repairing it costs nothing next to a reinstall."""
        repair = LoaderRepair(
            repaired=True,
            missing=("libncursesw.so.6",),
            added_dirs=("/home/user/.local/share/x/lib",),
            variable="LD_LIBRARY_PATH",
        )
        with (
            patch("ai_cli.zsh_setup.zsh_runs", return_value=False),
            patch("ai_cli.zsh_setup.zsh_present", return_value=True),
            patch("ai_cli.native_deps.repair_loader_path", return_value=repair),
            patch("ai_cli.zsh_setup.install_zsh") as install,
            patch("ai_cli.native_deps.adopt_prefix_bin", return_value=True),
        ):
            result = zsh_setup.ensure_zsh(quiet=True)

        install.assert_not_called()
        assert result.installed is True
        assert result.tool == "loader-path"
        assert "libncursesw.so.6" in result.detail

    def test_given_provisioning_fails_when_ensured_then_it_says_bash_will_be_used(self, capsys):
        """A degradation notice, not an error. The session still works."""
        with (
            patch("ai_cli.zsh_setup.zsh_runs", return_value=False),
            patch("ai_cli.zsh_setup.zsh_present", return_value=False),
            patch("ai_cli.zsh_setup.install_zsh", return_value=InstallResult(False, detail="no manager on PATH")),
            patch("ai_cli.native_deps.adopt_prefix_bin", return_value=False),
        ):
            result = zsh_setup.ensure_zsh()

        assert result.installed is False
        assert result.unusable is True
        err = capsys.readouterr().err
        assert "bash" in err
        assert "no manager on PATH" in err

    def test_given_auto_install_is_off_when_ensured_then_nothing_is_installed(self):
        with (
            patch("ai_cli.zsh_setup.zsh_runs", return_value=False),
            patch("ai_cli.zsh_setup.zsh_present", return_value=False),
            patch("ai_cli.zsh_setup.install_zsh") as install,
            patch("ai_cli.native_deps.adopt_prefix_bin", return_value=False),
        ):
            result = zsh_setup.ensure_zsh(auto_install=False, quiet=True)

        install.assert_not_called()
        assert result.installed is False

    def test_given_ensure_zsh_when_it_runs_then_it_adopts_before_probing(self):
        """Order matters: probing first would report a provisioned zsh missing,
        then install a second copy of it."""
        calls: list[str] = []
        with (
            patch("ai_cli.native_deps.adopt_prefix_bin", side_effect=lambda *a, **k: calls.append("adopt")),
            patch("ai_cli.zsh_setup.zsh_runs", side_effect=lambda *a, **k: calls.append("probe") or True),
        ):
            zsh_setup.ensure_zsh(quiet=True)

        assert calls[:2] == ["adopt", "probe"], calls

    def test_given_the_probe_raises_when_ensured_then_it_reports_unusable_not_an_error(self):
        """Nothing in provisioning may raise; a caller may be mid-install."""
        for boom in (OSError("bad exec"), subprocess.TimeoutExpired(cmd="zsh", timeout=10)):
            with (
                patch("ai_cli.zsh_setup.zsh_present", return_value=True),
                patch("subprocess.run", side_effect=boom),
            ):
                assert zsh_setup.zsh_runs() is False

    def test_given_zsh_resolves_but_exits_nonzero_then_it_does_not_run(self):
        """Presence is not runnability -- the same conflation as AI-CLI-d89q, and a
        pane whose interpreter fails to exec is torn down with only `[exited]`."""
        with (
            patch("ai_cli.zsh_setup.zsh_present", return_value=True),
            patch("subprocess.run", return_value=_version(returncode=127)),
        ):
            assert zsh_setup.zsh_runs() is False

    def test_given_zsh_reports_a_version_then_it_runs(self):
        with (
            patch("ai_cli.zsh_setup.zsh_present", return_value=True),
            patch("subprocess.run", return_value=_version()) as run,
        ):
            assert zsh_setup.zsh_runs() is True
        assert run.call_args[0][0] == ["zsh", "--version"]

    def test_the_version_probe_is_skipped_entirely_when_zsh_is_absent(self):
        with patch("shutil.which", return_value=None), patch("subprocess.run") as run:
            assert zsh_setup.zsh_runs() is False
        run.assert_not_called()


# ---------------------------------------------------------------------------
# Root escalation, and the ways it must refuse
# ---------------------------------------------------------------------------


class TestRootEscalation:
    def test_given_no_terminal_when_asked_whether_to_prompt_then_it_refuses(self):
        """The whole hazard in one assertion: a password prompt with nobody
        watching blocks forever, which is worse than the degradation it replaces."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", return_value="/usr/bin/sudo"),
            patch("sys.stdin") as stdin,
        ):
            stdin.isatty.return_value = False
            assert native_deps.can_prompt_for_root() is False

            stdin.isatty.return_value = True
            assert native_deps.can_prompt_for_root() is True

    def test_given_a_detached_stdin_when_asked_then_it_refuses_rather_than_raising(self):
        """A closed or replaced stdin raises on isatty() instead of answering."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", return_value="/usr/bin/sudo"),
            patch("sys.stdin") as stdin,
        ):
            stdin.isatty.side_effect = ValueError("I/O operation on closed file")
            assert native_deps.can_prompt_for_root() is False

    def test_given_no_sudo_when_asked_then_it_refuses(self):
        with patch.object(sys, "platform", "linux"), patch("shutil.which", return_value=None):
            assert native_deps.can_prompt_for_root() is False

    def test_given_windows_when_asked_then_it_refuses(self):
        """Elevation there is a UAC prompt, which must never be triggered from a
        library call."""
        with patch.object(sys, "platform", "win32"):
            assert native_deps.can_prompt_for_root() is False

    def test_given_allow_root_is_off_when_a_root_manager_is_reached_then_it_is_skipped(self):
        """The default, and it must stay the default for every launch-path caller."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda t: "/usr/bin/apt-get" if t == "apt-get" else None),
            patch("os.geteuid", return_value=1000),
            patch("subprocess.run", side_effect=AssertionError("ran a root manager without permission")),
        ):
            result = native_deps.attempt_installs([("apt-get", ["apt-get", "install", "-y", "zsh"])], lambda: False)

        assert result.installed is False
        assert "needs root" in result.detail

    def test_given_allow_root_but_no_terminal_when_escalating_then_it_refuses_to_run(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}"),
            patch("os.geteuid", return_value=1000),
            patch("ai_cli.native_deps.can_prompt_for_root", return_value=False),
            patch("subprocess.run", side_effect=AssertionError("escalated with no terminal to ask on")),
        ):
            result = native_deps.attempt_installs(
                [("apt-get", ["apt-get", "install", "-y", "zsh"])], lambda: False, allow_root=True
            )

        assert result.installed is False
        assert "no terminal" in result.detail

    def test_given_the_password_is_declined_when_escalating_then_the_installer_never_runs(self):
        ran: list[list[str]] = []

        def _record(argv, *a, **k):
            ran.append(list(argv))
            return MagicMock(returncode=1, stdout="", stderr="")

        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}"),
            patch("os.geteuid", return_value=1000),
            patch("ai_cli.native_deps.can_prompt_for_root", return_value=True),
            patch("ai_cli.native_deps._authenticate_root", return_value=False),
            patch("subprocess.run", side_effect=_record),
        ):
            result = native_deps.attempt_installs(
                [("apt-get", ["apt-get", "install", "-y", "zsh"])], lambda: False, allow_root=True
            )

        assert result.installed is False
        assert "declined" in result.detail
        assert ran == [], f"the installer ran anyway: {ran}"

    def test_given_authentication_succeeds_when_escalating_then_the_installer_runs_non_interactively(self):
        """``sudo -n`` on the real call, because it is captured. Without -n a
        re-prompt would be invisible and read as a hang -- which is why the
        password is collected by a separate visible step."""
        ran: list[list[str]] = []

        def _record(argv, *a, **k):
            ran.append(list(argv))
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}"),
            patch("os.geteuid", return_value=1000),
            patch("ai_cli.native_deps.can_prompt_for_root", return_value=True),
            patch("ai_cli.native_deps._authenticate_root", return_value=True),
            patch("subprocess.run", side_effect=_record),
        ):
            result = native_deps.attempt_installs(
                [("apt-get", ["apt-get", "install", "-y", "zsh"])], lambda: True, allow_root=True
            )

        assert result.installed is True
        assert ran and ran[0][:2] == ["sudo", "-n"], ran

    def test_given_the_authenticate_step_when_it_runs_then_it_does_not_capture_output(self):
        """The prompt has to be visible. Capturing it is exactly how "asks for a
        password" becomes "hangs silently"."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as run:
            assert native_deps._authenticate_root() is True

        assert run.call_args[0][0] == ["sudo", "-v"]
        assert "capture_output" not in run.call_args.kwargs
        assert "stdout" not in run.call_args.kwargs

    def test_given_root_is_already_held_when_installing_then_no_escalation_happens(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}"),
            patch("os.geteuid", return_value=0),
            patch("ai_cli.native_deps._authenticate_root", side_effect=AssertionError("asked root for a password")),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            result = native_deps.attempt_installs(
                [("apt-get", ["apt-get", "install", "-y", "zsh"])], lambda: True, allow_root=True
            )

        assert result.installed is True


# ---------------------------------------------------------------------------
# install_zsh wiring
# ---------------------------------------------------------------------------


class TestInstallZsh:
    def test_given_a_rootless_install_when_attempted_then_the_prefix_candidates_come_first(self, monkeypatch):
        seen: list[list[str]] = []

        def _capture(candidates, verify, timeout=300, before_verify=None, allow_root=False):
            seen.extend(argv for _probe, argv in candidates)
            return InstallResult(False, detail="captured")

        monkeypatch.setattr(zsh_setup, "attempt_installs", _capture)
        monkeypatch.setattr(sys, "platform", "linux")

        zsh_setup.install_zsh(allow_root=True)

        assert seen, "no candidates were offered"
        assert "--prefix" in seen[0], f"a system manager was tried first: {seen[0]}"

    def test_given_allow_root_is_off_when_installing_then_only_prefix_candidates_are_offered(self, monkeypatch):
        seen: list[str] = []

        def _capture(candidates, verify, timeout=300, before_verify=None, allow_root=False):
            seen.extend(probe for probe, _argv in candidates)
            return InstallResult(False)

        monkeypatch.setattr(zsh_setup, "attempt_installs", _capture)
        monkeypatch.setattr(sys, "platform", "linux")

        zsh_setup.install_zsh(allow_root=False)

        assert seen == ["micromamba", "conda"], seen

    def test_given_an_install_when_verified_then_the_prefix_is_adopted_before_probing(self, monkeypatch):
        """A conda install lands outside PATH, so verifying without adopting first
        would report every successful install as a failure."""
        order: list[str] = []
        captured: dict[str, object] = {}

        def _capture(candidates, verify, timeout=300, before_verify=None, allow_root=False):
            captured["verify"] = verify
            return InstallResult(False)

        monkeypatch.setattr(zsh_setup, "attempt_installs", _capture)
        monkeypatch.setattr(native_deps, "adopt_prefix_bin", lambda *a, **k: order.append("adopt"))
        monkeypatch.setattr(zsh_setup, "zsh_runs", lambda *a, **k: order.append("probe") or True)

        zsh_setup.install_zsh()
        assert captured["verify"]() is True
        assert order == ["adopt", "probe"], order


# ---------------------------------------------------------------------------
# Install-time and doctor wiring
# ---------------------------------------------------------------------------


class TestInstallTimeWiring:
    def test_given_setup_runs_when_it_completes_then_zsh_was_provisioned(self, tmp_path):
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", return_value=InstallResult(True)),
            patch("ai_cli.zsh_setup.ensure_zsh", return_value=InstallResult(True, tool="micromamba")) as ensure,
        ):
            assert ai_setup.run_setup(tmp_path) == 0

        ensure.assert_called_once()

    def test_given_no_terminal_when_setup_runs_then_it_does_not_offer_escalation(self, tmp_path):
        """A piped or scripted install must not be able to reach a password prompt."""
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", return_value=InstallResult(True)),
            patch("ai_cli.native_deps.can_prompt_for_root", return_value=False),
            patch("ai_cli.zsh_setup.ensure_zsh", return_value=InstallResult(False)) as ensure,
        ):
            assert ai_setup.run_setup(tmp_path) == 0

        assert ensure.call_args.kwargs["allow_root"] is False

    def test_given_zsh_provisioning_explodes_when_setup_runs_then_the_install_survives(self, tmp_path):
        """An optional enhancement may never fail an install."""
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", return_value=InstallResult(True)),
            patch("ai_cli.zsh_setup.ensure_zsh", side_effect=OSError("boom")),
        ):
            assert ai_setup.run_setup(tmp_path) == 0


class TestDoctorWiring:
    def test_given_doctor_when_it_runs_then_it_reports_zsh(self):
        from conftest import run_cli

        with (
            patch("ai_cli.zsh_setup.ensure_zsh", return_value=InstallResult(True, tool="micromamba")),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            _code, stdout, _err = run_cli(["ai", "doctor"])

        assert "zsh" in stdout
        assert "micromamba" in stdout

    def test_given_dry_run_when_doctor_runs_then_it_provisions_nothing(self):
        """`-n/--dry-run` is the reporting-only mode; it must not install or escalate."""
        from conftest import run_cli

        with (
            patch("ai_cli.zsh_setup.ensure_zsh", return_value=InstallResult(False)) as ensure,
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            run_cli(["ai", "doctor", "--dry-run"])

        assert ensure.call_args.kwargs["auto_install"] is False
        assert ensure.call_args.kwargs["allow_root"] is False

    def test_given_zsh_is_unusable_when_doctor_runs_then_it_says_so(self):
        from conftest import run_cli

        with (
            patch(
                "ai_cli.zsh_setup.ensure_zsh",
                return_value=InstallResult(False, detail="no manager on PATH", unusable=True),
            ),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            _code, stdout, _err = run_cli(["ai", "doctor"])

        assert "MISS" in stdout
        assert "no manager on PATH" in stdout


def test_the_session_shell_preference_still_puts_zsh_first():
    """Anti-vacuity control for this whole file. If bash ever became the preferred
    interpreter, provisioning zsh would stop being the point and these tests would
    be defending a decision that no longer exists."""
    from ai_cli.session_script import SESSION_SHELL_PREFERENCE

    assert SESSION_SHELL_PREFERENCE[0] == "zsh", SESSION_SHELL_PREFERENCE


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_given_any_platform_when_remediation_is_built_then_it_names_a_real_route(platform):
    with patch.object(sys, "platform", platform):
        text = zsh_setup.remediation(InstallResult(False, detail="nothing worked"))

    assert "bash" in text, "the notice must say what runs instead"
    assert "nothing worked" in text
    assert "zsh" in text.lower()
