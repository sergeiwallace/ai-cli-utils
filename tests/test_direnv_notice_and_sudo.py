"""The direnv remediation block prints once per host, and the auto-install tries ``sudo -n``.

Both are driven through the real entry points: ``_do_session_launch`` (what ``ai c`` runs)
and ``ai doctor``. The process boundary is faked with executables on a scratch ``PATH``: a
``sudo`` that either runs its command or refuses for want of a password, and an
``apt-get`` that records its argv and drops a ``direnv`` beside itself. No real package
manager or sudo is ever reachable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.launch_reporter import LaunchReporter
from ai_cli.main import _do_session_launch
from ai_cli.zsh_setup import InstallResult

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX shell stubs, and needs a non-root user for the sudo path",
)

FULL_BLOCK = "Install it with one of"
ONE_LINE = "direnv not installed; .envrc not loaded; run `ai doctor` for the fix"

SUDO = """#!/bin/sh
echo "sudo $*" >> "$STUB_LOG"
if [ "$1" = "-n" ] && [ "${STUB_SUDO_OK:-0}" = "1" ]; then
    shift
    exec "$@"
fi
echo "sudo: a password is required" >&2
exit 1
"""

#: ``chmod`` by absolute path: the scratch PATH holds only the stubs and a shell.
APT_GET = """#!/bin/sh
echo "apt-get $*" >> "$STUB_LOG"
printf '#!/bin/sh\\nexit 0\\n' > "${0%/*}/direnv"
CHMOD +x "${0%/*}/direnv"
"""


CHMOD = str(Path(shutil.which("chmod") or "/bin/chmod").resolve())


def _stub(directory: Path, name: str, text: str) -> None:
    path = directory / name
    path.write_text(text.replace("CHMOD ", f"{CHMOD} "), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A project with an .envrc, run from a PATH holding only bash and the given stubs."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / ".envrc").write_text("export X=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(Path("/bin/bash").resolve())
    (bin_dir / "sh").symlink_to(Path("/bin/sh").resolve())
    (bin_dir / "true").symlink_to(Path(shutil.which("true")).resolve())
    log = tmp_path / "stub.log"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.delenv("AI_CLI_NO_DIRENV", raising=False)
    monkeypatch.chdir(project)
    return {"bin": bin_dir, "log": log, "project": project}


def _calls(host) -> list[str]:
    return host["log"].read_text(encoding="utf-8").splitlines() if host["log"].exists() else []


STUBBED = {"sudo", "apt-get", "direnv", "bash", "sh", "true"}
_REAL_RUN = subprocess.run


def _run(argv, *args, **kwargs):
    """The launch's own git and tmux calls are stubbed; the stub executables really run."""
    if argv and Path(str(argv[0])).name in STUBBED:
        return _REAL_RUN(argv, *args, **kwargs)
    return MagicMock(returncode=0, stdout="", stderr="")


def _launch(tmp_path: Path) -> None:
    # A launch moves into its worktree; each one starts from the project, as `ai c` does.
    os.chdir(tmp_path / "myproject")
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    reporter = LaunchReporter()
    reporter.start(engine="Claude Code", mode="local, bare")
    with (
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.session.detect_repo_root", return_value=None),
        patch("ai_cli.session.create_worktree", return_value=(worktree, False)),
        patch("ai_cli.main.pull_rebase_autostash", return_value=(MagicMock(returncode=0), None)),
        patch("ai_cli.main.detect_missing_tracked_symlinks", return_value=[]),
        patch("ai_cli.main.detect_phantom_deleted_files", return_value=[]),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main.subprocess.run", side_effect=_run),
        patch("ai_cli.main._exec_with_direnv", side_effect=SystemExit(0)),
        pytest.raises(SystemExit),
    ):
        _do_session_launch(
            engine="c",
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
            config={"worktree": {"enabled": True}},
            reporter=reporter,
        )


def _doctor(tmp_path: Path) -> tuple[int, str, str]:
    from conftest import run_cli

    os.chdir(tmp_path / "myproject")

    with (
        patch("ai_cli.zsh_setup.ensure_zsh", return_value=InstallResult(True, tool="system")),
        patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
    ):
        return run_cli(["ai", "doctor"])


def test_given_direnv_missing_when_a_second_launch_runs_on_the_host_then_it_prints_one_line_not_the_block(
    host, tmp_path, capsys
):
    _launch(tmp_path)
    first = capsys.readouterr().err
    _launch(tmp_path)
    second = capsys.readouterr().err

    assert FULL_BLOCK in first, first
    assert FULL_BLOCK not in second, second
    assert ONE_LINE in second, second


def test_given_the_block_was_shown_on_a_launch_when_doctor_runs_then_it_still_prints_the_full_block(
    host, tmp_path, capsys
):
    _launch(tmp_path)
    capsys.readouterr()

    code, stdout, stderr = _doctor(tmp_path)

    assert code != 0
    assert FULL_BLOCK in stdout + stderr, stdout + stderr


def test_given_direnv_becomes_usable_and_breaks_again_when_launching_then_the_block_shows_once_more(
    host, tmp_path, capsys
):
    _launch(tmp_path)
    _stub(host["bin"], "direnv", "#!/bin/sh\nexit 0\n")
    _launch(tmp_path)
    (host["bin"] / "direnv").unlink()
    capsys.readouterr()

    _launch(tmp_path)

    assert FULL_BLOCK in capsys.readouterr().err


def test_given_passwordless_sudo_when_a_launch_auto_installs_direnv_then_it_runs_the_manager_through_sudo_n(
    host, tmp_path, monkeypatch, capsys
):
    _stub(host["bin"], "sudo", SUDO)
    _stub(host["bin"], "apt-get", APT_GET)
    monkeypatch.setenv("STUB_SUDO_OK", "1")

    _launch(tmp_path)

    assert _calls(host) == [
        "sudo -n true",
        "sudo -n apt-get install -y direnv",
        "apt-get install -y direnv",
    ]
    err = capsys.readouterr().err
    assert "direnv: installed via apt-get" in err, err
    assert FULL_BLOCK not in err


def test_given_sudo_that_needs_a_password_when_a_launch_auto_installs_then_it_never_prompts_and_logs_why(
    host, tmp_path, capsys
):
    _stub(host["bin"], "sudo", SUDO)
    _stub(host["bin"], "apt-get", APT_GET)

    _launch(tmp_path)

    assert _calls(host) == ["sudo -n true"]
    err = capsys.readouterr().err
    assert "passwordless sudo is not available (sudo: a password is required)" in err, err
    assert "apt-get (needs root" in err, err


def test_given_no_sudo_on_path_when_a_launch_auto_installs_then_it_says_so_and_runs_nothing(host, tmp_path, capsys):
    _stub(host["bin"], "apt-get", APT_GET)

    _launch(tmp_path)

    assert _calls(host) == []
    assert "passwordless sudo is not available (sudo is not on PATH)" in capsys.readouterr().err
