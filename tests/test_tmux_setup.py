"""tmux bootstrap: detect, install unattended, else hand back a bare-mode verdict.

The contract these tests defend is that *nothing here may take a launch down*.
tmux buys detach/reattach and a session that survives a dropped SSH connection;
losing it is a degradation, and before AI-CLI-yrpa that degradation was
an exit 1 on every non-Windows host without tmux.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

from ai_cli import tmux_setup
from ai_cli.native_deps import InstallResult


def _completed(returncode=0, stdout="tmux 3.4\n", stderr=""):
    return subprocess.CompletedProcess(args=["tmux", "-V"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestPresenceVersusUsable:
    """On PATH and actually running are different questions.

    A hand-placed or half-installed tmux resolves and then dies on a missing
    shared library — measured on a SageMaker space, where `tmux` was on PATH and
    `tmux -V` exited 127 for want of libutempter.so.0, and `ai doctor` reported
    `OK tmux` throughout while no session could start.
    """

    def test_when_tmux_is_absent_from_path_then_not_present_and_not_runnable(self):
        with patch("shutil.which", return_value=None):
            assert tmux_setup.tmux_present() is False
            assert tmux_setup.tmux_runs() is False

    def test_when_tmux_is_on_path_and_runs_then_usable(self):
        with (
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("subprocess.run", return_value=_completed()) as run,
        ):
            assert tmux_setup.tmux_present() is True
            assert tmux_setup.tmux_runs() is True
        assert run.call_args[0][0] == ["tmux", "-V"]

    def test_when_tmux_is_on_path_but_cannot_execute_then_present_but_not_runnable(self):
        """The exact sem-kg failure: present, exit 127, no usable tmux."""
        with (
            patch("shutil.which", return_value="/home/u/.local/bin/tmux"),
            patch("subprocess.run", return_value=_completed(returncode=127, stdout="", stderr="libutempter.so.0")),
        ):
            assert tmux_setup.tmux_present() is True
            assert tmux_setup.tmux_runs() is False

    def test_when_the_version_probe_raises_then_it_reports_unusable_not_an_error(self):
        for boom in (OSError("bad exec"), subprocess.TimeoutExpired(cmd="tmux", timeout=10)):
            with (
                patch("shutil.which", return_value="/usr/bin/tmux"),
                patch("subprocess.run", side_effect=boom),
            ):
                assert tmux_setup.tmux_runs() is False

    def test_the_version_probe_is_skipped_entirely_when_tmux_is_absent(self):
        """No point spawning a process to ask a binary that is not there."""
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as run,
        ):
            assert tmux_setup.tmux_runs() is False
        run.assert_not_called()


class TestInstallCandidates:
    def test_windows_has_no_unattended_candidate_because_native_tmux_does_not_exist(self):
        """tmux runs under WSL/MSYS2/Cygwin; no manager ships a native win32 build.

        An empty candidate list is the honest answer, and it is what makes the
        launcher fall through to bare mode on Windows without noise.
        """
        assert "win32" not in tmux_setup._INSTALLERS
        with patch.object(sys, "platform", "win32"):
            result = tmux_setup.install_tmux()
        assert result.installed is False
        assert "no unattended installer" in result.detail

    def test_rootless_managers_are_tried_before_the_ones_needing_root(self):
        """An unprivileged host is the common case for the machines that need this.

        conda/micromamba/brew can install for a normal user; apt-get and friends
        cannot, and native_deps.needs_root skips them rather than hanging on a
        password prompt. Ordering them the other way round would make the whole
        attempt a no-op on exactly those hosts.
        """
        linux = [probe for probe, _ in tmux_setup._INSTALLERS["linux"]]
        root_managers = ("apt-get", "dnf", "pacman", "zypper")
        rootless = [i for i, probe in enumerate(linux) if probe not in root_managers]
        needs_root = [i for i, probe in enumerate(linux) if probe in root_managers]
        assert rootless and needs_root, linux
        assert max(rootless) < min(needs_root), linux

    def test_every_candidate_argv_is_non_interactive(self):
        """An installer that can stop and ask a question would hang a launch."""
        non_interactive = ("-y", "--noconfirm", "--non-interactive")
        for platform, candidates in tmux_setup._INSTALLERS.items():
            for probe, argv in candidates:
                if probe == "brew":  # brew install is non-interactive by default
                    continue
                assert any(flag in argv for flag in non_interactive), (platform, argv)

    def test_when_the_manager_cannot_even_be_spawned_then_it_reports_failure_not_an_error(self):
        """Mid-launch, a raising bootstrap helper IS the outage it exists to avoid."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda tool: "/usr/bin/conda" if tool == "conda" else None),
            patch("subprocess.run", side_effect=OSError("exec format error")),
        ):
            result = tmux_setup.install_tmux()
        assert result.installed is False
        assert "OSError" in result.detail

    def test_when_a_manager_reports_success_but_no_binary_landed_then_it_is_a_failure(self):
        """Some managers exit 0 having only staged a pending install."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda tool: "/usr/bin/conda" if tool == "conda" else None),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        ):
            result = tmux_setup.install_tmux()
        assert result.installed is False


class TestEnsureTmux:
    def test_when_tmux_is_already_present_then_nothing_is_installed(self):
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux()
        assert result.installed is True
        install.assert_not_called()

    def test_when_the_install_succeeds_then_it_says_so_on_stderr(self, capsys):
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(True, tool="micromamba")),
        ):
            result = tmux_setup.ensure_tmux()
        assert result.installed is True
        assert "installed tmux via micromamba" in capsys.readouterr().err

    def test_when_the_install_fails_then_it_returns_false_and_prints_remediation(self, capsys):
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="conda exit 1")),
        ):
            result = tmux_setup.ensure_tmux()
        assert result.installed is False
        err = capsys.readouterr().err
        assert "launching in bare mode instead" in err
        assert "conda exit 1" in err

    def test_quiet_suppresses_the_notice_without_changing_the_verdict(self, capsys):
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="nope")),
        ):
            result = tmux_setup.ensure_tmux(quiet=True)
        assert result.installed is False
        assert capsys.readouterr().err == ""

    def test_auto_install_false_probes_but_installs_nothing(self):
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(auto_install=False, quiet=True)
        assert result.installed is False
        install.assert_not_called()


class TestConfigOptOut:
    """`[session] use_tmux` is the per-machine setting, and it wins outright."""

    def test_absent_setting_means_tmux_is_wanted(self):
        assert tmux_setup.config_opts_out({}) is False
        assert tmux_setup.config_opts_out(None) is False
        assert tmux_setup.config_opts_out({"session": {}}) is False

    def test_explicit_false_opts_out(self):
        assert tmux_setup.config_opts_out({"session": {"use_tmux": False}}) is True

    def test_explicit_true_opts_in(self):
        assert tmux_setup.config_opts_out({"session": {"use_tmux": True}}) is False


class TestRemediation:
    def test_it_names_the_platform_command_and_the_permanent_opt_out(self):
        with patch.object(sys, "platform", "linux"):
            text = tmux_setup.remediation(InstallResult(False, detail="apt-get needs root"))
        assert "apt install tmux" in text
        assert "conda install -c conda-forge tmux" in text  # the rootless route
        assert "use_tmux = false" in text
        assert "apt-get needs root" in text

    def test_it_says_what_bare_mode_costs_rather_than_only_that_it_happened(self):
        """An operator who does not know what tmux was doing cannot judge the loss."""
        text = tmux_setup.remediation()
        assert "ai attach" in text
        assert "dropped SSH connection" in text

    def test_windows_is_told_about_wsl_not_a_nonexistent_native_install(self):
        with patch.object(sys, "platform", "win32"):
            text = tmux_setup.remediation()
        assert "wsl --install" in text
