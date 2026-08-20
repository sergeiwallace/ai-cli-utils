"""Cross-platform direnv bootstrap.

The contract under test is narrow but load-bearing: direnv handling must be
*loud* when it fails and must never abort the caller. A bootstrap helper that
raises, exits, or blocks would reintroduce the launch-killing regression that
``_exec_with_direnv`` was already fixed for once.

Nothing here may touch a real package manager, so ``shutil.which`` and
``subprocess.run`` are stubbed at the module boundary in every install test.
"""

import subprocess
import sys
from unittest.mock import patch

import pytest

from ai_cli.direnv_setup import (
    BYPASS_ENV,
    InstallResult,
    bash_available,
    direnv_available,
    ensure_direnv,
    find_envrc,
    install_direnv,
    is_bypassed,
    remediation,
)

# --- bypass -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "anything"])
def test_given_truthy_bypass_env_when_checked_then_bypassed(monkeypatch, value):
    monkeypatch.setenv(BYPASS_ENV, value)
    assert is_bypassed() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "FALSE"])
def test_given_falsy_bypass_env_when_checked_then_not_bypassed(monkeypatch, value):
    monkeypatch.setenv(BYPASS_ENV, value)
    assert is_bypassed() is False


def test_given_no_bypass_env_when_checked_then_not_bypassed(monkeypatch):
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    assert is_bypassed() is False


def test_given_config_disabling_direnv_when_checked_then_bypassed(monkeypatch):
    """config.toml is the permanent opt-out, matching [worktree] enabled = false."""
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    assert is_bypassed({"direnv": {"enabled": False}}) is True


def test_given_config_enabling_direnv_when_checked_then_not_bypassed(monkeypatch):
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    assert is_bypassed({"direnv": {"enabled": True}}) is False


# --- probes -------------------------------------------------------------------


def test_given_direnv_on_path_when_probed_then_available():
    with patch("ai_cli.direnv_setup.shutil.which", return_value="/usr/bin/direnv"):
        assert direnv_available() is True


def test_given_direnv_absent_when_probed_then_unavailable():
    with patch("ai_cli.direnv_setup.shutil.which", return_value=None):
        assert direnv_available() is False


def test_given_bash_absent_when_probed_then_unavailable():
    """Tracked separately from direnv: on Windows the two fail independently."""
    with patch("ai_cli.direnv_setup.shutil.which", return_value=None):
        assert bash_available() is False


# --- .envrc discovery ---------------------------------------------------------


def test_given_envrc_in_start_dir_when_searched_then_found(tmp_path):
    envrc = tmp_path / ".envrc"
    envrc.write_text("export X=1\n")
    assert find_envrc(tmp_path) == envrc.resolve()


def test_given_envrc_in_parent_when_searched_then_found(tmp_path):
    """direnv searches upward, so an inherited .envrc counts as present."""
    envrc = tmp_path / ".envrc"
    envrc.write_text("export X=1\n")
    child = tmp_path / "nested" / "deeper"
    child.mkdir(parents=True)
    assert find_envrc(child) == envrc.resolve()


def test_given_no_envrc_anywhere_when_searched_then_none(tmp_path):
    assert find_envrc(tmp_path) is None


# --- install ------------------------------------------------------------------


def _which(*present: str):
    """Stub shutil.which that reports only ``present`` executables on PATH."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_given_successful_manager_when_installing_then_reports_that_tool(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    completed = subprocess.CompletedProcess(["brew"], 0, "", "")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("brew", "direnv")),
        patch("ai_cli.direnv_setup.subprocess.run", return_value=completed) as run,
    ):
        result = install_direnv()

    assert result.installed is True
    assert result.tool == "brew"
    assert run.call_args[0][0] == ["brew", "install", "direnv"]


def test_given_manager_exiting_zero_without_installing_when_installing_then_not_installed(monkeypatch):
    """A manager can exit 0 having only staged a pending install — verify the binary."""
    monkeypatch.setattr(sys, "platform", "darwin")
    completed = subprocess.CompletedProcess(["brew"], 0, "", "")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("brew")),
        patch("ai_cli.direnv_setup.subprocess.run", return_value=completed),
    ):
        result = install_direnv()

    assert result.installed is False


def test_given_failing_manager_when_installing_then_detail_carries_its_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    completed = subprocess.CompletedProcess(["brew"], 1, "", "No available formula")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("brew")),
        patch("ai_cli.direnv_setup.subprocess.run", return_value=completed),
    ):
        result = install_direnv()

    assert result.installed is False
    assert "No available formula" in result.detail


def test_given_no_manager_on_path_when_installing_then_says_so(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("ai_cli.direnv_setup.shutil.which", side_effect=_which()):
        result = install_direnv()

    assert result.installed is False
    assert "on PATH" in result.detail
    assert "winget" in result.detail


def test_given_unknown_platform_when_installing_then_reports_no_installer(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    result = install_direnv()

    assert result.installed is False
    assert "sunos5" in result.detail


def test_given_unprivileged_linux_when_installing_then_root_manager_is_skipped_not_run(monkeypatch):
    """An unprivileged apt-get would prompt for a password and hang the launch."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("apt-get")),
        patch("ai_cli.direnv_setup.subprocess.run") as run,
    ):
        result = install_direnv()

    assert result.installed is False
    assert "needs root" in result.detail
    run.assert_not_called()


def test_given_root_linux_when_installing_then_package_manager_runs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
    completed = subprocess.CompletedProcess(["apt-get"], 0, "", "")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("apt-get", "direnv")),
        patch("ai_cli.direnv_setup.subprocess.run", return_value=completed) as run,
    ):
        result = install_direnv()

    assert result.installed is True
    assert run.call_args[0][0][0] == "apt-get"


@pytest.mark.parametrize("boom", [OSError("denied"), subprocess.TimeoutExpired("winget", 300)])
def test_given_installer_raising_when_installing_then_returns_failure_without_raising(monkeypatch, boom):
    """install_direnv must never propagate: callers are mid-launch."""
    monkeypatch.setattr(sys, "platform", "win32")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("winget")),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=boom),
    ):
        result = install_direnv()

    assert result.installed is False
    assert type(boom).__name__ in result.detail


def test_given_first_manager_failing_when_installing_then_next_is_tried(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    outcomes = [
        subprocess.CompletedProcess(["scoop"], 1, "", "not found"),
        subprocess.CompletedProcess(["winget"], 0, "", ""),
    ]
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("scoop", "winget", "direnv")),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=outcomes) as run,
    ):
        result = install_direnv()

    assert result.installed is True
    assert result.tool == "winget"
    assert run.call_count == 2


# --- remediation text ---------------------------------------------------------


def test_given_windows_when_remediating_then_names_windows_managers(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("ai_cli.direnv_setup.shutil.which", return_value="/bash"):
        text = remediation()

    assert "winget install" in text
    assert "brew install" not in text


def test_given_macos_when_remediating_then_names_brew(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    text = remediation()

    assert "brew install direnv" in text
    assert "winget" not in text


def test_given_windows_without_bash_when_remediating_then_explains_bash_requirement(monkeypatch):
    """The Windows-specific trap: direnv present but no bash to evaluate .envrc."""
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("ai_cli.direnv_setup.shutil.which", return_value=None):
        text = remediation()

    assert "bash" in text.lower()
    assert "git-scm.com" in text


def test_given_windows_with_bash_when_remediating_then_omits_bash_requirement(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("ai_cli.direnv_setup.shutil.which", return_value="/usr/bin/bash"):
        text = remediation()

    assert "git-scm.com" not in text


def test_given_any_platform_when_remediating_then_documents_every_bypass():
    """The bypass is what stops a broken dependency bricking the tool — always shown."""
    text = remediation()

    assert BYPASS_ENV in text
    assert "--no-direnv" in text or "-D" in text
    assert "enabled = false" in text


def test_given_any_platform_when_remediating_then_documents_shell_hooks():
    text = remediation()

    for shell in ("bash", "zsh", "fish", "pwsh"):
        assert shell in text


def test_given_an_envrc_when_remediating_then_names_the_file(tmp_path):
    envrc = tmp_path / ".envrc"
    text = remediation(envrc)

    assert str(envrc) in text


def test_given_a_failed_result_when_remediating_then_includes_its_detail():
    text = remediation(None, InstallResult(False, detail="winget exit 1"))

    assert "winget exit 1" in text


# --- ensure_direnv orchestration ----------------------------------------------


def test_given_bypassed_when_ensuring_then_nothing_is_probed_or_installed(tmp_path, monkeypatch):
    monkeypatch.setenv(BYPASS_ENV, "1")
    with patch("ai_cli.direnv_setup.install_direnv") as install:
        result = ensure_direnv(tmp_path)

    assert result.installed is True
    install.assert_not_called()


def test_given_no_envrc_when_ensuring_then_no_install_is_attempted(tmp_path, monkeypatch):
    """Do not install a tool that would have no work to do."""
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    with patch("ai_cli.direnv_setup.install_direnv") as install:
        result = ensure_direnv(tmp_path)

    assert result.installed is True
    install.assert_not_called()


def test_given_direnv_already_usable_when_ensuring_then_no_install_is_attempted(tmp_path, monkeypatch):
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    (tmp_path / ".envrc").write_text("export X=1\n")
    with (
        patch("ai_cli.direnv_setup.shutil.which", return_value="/usr/bin/x"),
        patch("ai_cli.direnv_setup.install_direnv") as install,
    ):
        result = ensure_direnv(tmp_path)

    assert result.installed is True
    install.assert_not_called()


def test_given_direnv_missing_when_ensuring_then_it_installs_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    (tmp_path / ".envrc").write_text("export X=1\n")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("bash")),
        patch("ai_cli.direnv_setup.install_direnv", return_value=InstallResult(True, tool="scoop")),
    ):
        result = ensure_direnv(tmp_path)

    assert result.installed is True
    assert "installed direnv via scoop" in capsys.readouterr().err


def test_given_install_failing_when_ensuring_then_it_warns_loudly_without_raising(tmp_path, monkeypatch, capsys):
    """The core contract: loud, actionable, and the caller still lives."""
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    envrc = tmp_path / ".envrc"
    envrc.write_text("export X=1\n")
    with (
        patch("ai_cli.direnv_setup.shutil.which", return_value=None),
        patch("ai_cli.direnv_setup.install_direnv", return_value=InstallResult(False, detail="no manager")),
    ):
        result = ensure_direnv(tmp_path)

    err = capsys.readouterr().err
    assert result.installed is False
    assert str(envrc) in err
    assert "no manager" in err
    assert BYPASS_ENV in err


def test_given_auto_install_disabled_when_ensuring_then_it_only_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    (tmp_path / ".envrc").write_text("export X=1\n")
    with (
        patch("ai_cli.direnv_setup.shutil.which", return_value=None),
        patch("ai_cli.direnv_setup.install_direnv") as install,
    ):
        result = ensure_direnv(tmp_path, auto_install=False)

    assert result.installed is False
    install.assert_not_called()
    assert BYPASS_ENV in capsys.readouterr().err


def test_given_direnv_installed_but_bash_missing_when_ensuring_then_it_still_warns(tmp_path, monkeypatch, capsys):
    """Windows trap: direnv on PATH is not enough without a bash to run .envrc."""
    monkeypatch.delenv(BYPASS_ENV, raising=False)
    (tmp_path / ".envrc").write_text("export X=1\n")
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which("direnv")),
        patch("ai_cli.direnv_setup.install_direnv") as install,
    ):
        result = ensure_direnv(tmp_path)

    assert result.installed is False
    install.assert_not_called()
    assert "bash" in capsys.readouterr().err.lower()


def test_given_a_real_project_root_when_ensuring_bypassed_then_it_never_touches_subprocess(tmp_path, monkeypatch):
    """End-to-end guard: the bypass short-circuits before any process spawn."""
    monkeypatch.setenv(BYPASS_ENV, "1")
    (tmp_path / ".envrc").write_text("export X=1\n")
    with patch("ai_cli.direnv_setup.subprocess.run") as run:
        ensure_direnv(tmp_path)

    run.assert_not_called()
