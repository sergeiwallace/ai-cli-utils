"""A remote launch brings this machine's own editable client up to origin/main first.

``ai c <name> -R`` synced the REMOTE host's ai-cli-utils, but the client running the
launch was the local editable checkout, and the install check only fingerprints the
files on disk. A checkout nobody had pulled therefore printed
``Install: editable checkout ...; current`` while running code from before the fix
it needed, so a client-side fix could never reach that machine through ``ai c -R``.

Every test drives ``cli()`` against a real git checkout cloned from a real bare
origin; only the session launch itself, the install fingerprint and the re-exec are
stubbed.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.launch_reporter import InstallOrigin
from ai_cli.main import cli

REMOTE_CONFIG = {"remote": {"host": "192.0.2.10", "user": "user", "transport": "ssh"}}


def _git(*args, cwd) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", name, cwd=repo)


@pytest.fixture
def origin_and_checkout(tmp_path):
    """A bare origin, a publisher clone that advances it, and the client checkout."""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    publisher = tmp_path / "publisher"
    _git("clone", "-q", str(origin), str(publisher), cwd=tmp_path)
    _git("checkout", "-q", "-B", "main", cwd=publisher)
    _commit(publisher, "pyproject.toml", '[project]\nname = "myproject"\nversion = "0.1.0"\n')
    _git("push", "-q", "origin", "main", cwd=publisher)
    checkout = tmp_path / "myproject"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    return origin, publisher, checkout


def _advance_origin(publisher: Path, commits: int = 1) -> str:
    for index in range(commits):
        _commit(publisher, f"fix-{index}.py", f"value = {index}\n")
    _git("push", "-q", "origin", "main", cwd=publisher)
    return _git("rev-parse", "HEAD", cwd=publisher)


def _launch(checkout: Path, argv=("ai", "c", "session-1", "-R")):
    """Run the launch; return (stderr, execvp mock, session-launch mock)."""
    execvp = MagicMock(side_effect=SystemExit(0))
    session_launch = MagicMock()
    stderr = io.StringIO()
    with (
        patch("sys.argv", list(argv)),
        patch("ai_cli.config.load_config", return_value=REMOTE_CONFIG),
        patch("ai_cli.main._find_aicli_project_path", return_value=checkout),
        patch("ai_cli.main._launch_install_origin", return_value=InstallOrigin.EDITABLE_CHECKOUT),
        patch("ai_cli.main._auto_update_if_stale", return_value=False),
        patch("ai_cli.main._ensure_dolt_server"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.tunnel._ensure_nats_tunnel"),
        patch("ai_cli.main._do_session_launch", session_launch),
        patch("ai_cli.main.os.execvp", execvp),
        patch("sys.stderr", stderr),
        pytest.raises(SystemExit),
    ):
        cli()
    return stderr.getvalue(), execvp, session_launch


def _install_line(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.startswith("[launch] Install:") and ";" in line]
    assert len(lines) == 1, stderr
    return lines[0]


def test_given_a_clean_checkout_behind_origin_when_launching_remote_then_it_is_fast_forwarded_and_restarts(
    origin_and_checkout,
):
    _, publisher, checkout = origin_and_checkout
    target = _advance_origin(publisher, commits=2)

    stderr, execvp, session_launch = _launch(checkout)

    assert _git("rev-parse", "HEAD", cwd=checkout) == target
    assert "fast-forwarded 2 commit(s) from origin/main; restarting" in _install_line(stderr)
    assert "current" not in _install_line(stderr)
    execvp.assert_called_once()
    session_launch.assert_not_called()


def test_given_a_dirty_checkout_behind_origin_when_launching_remote_then_it_is_refused_with_the_fix_command(
    origin_and_checkout,
):
    _, publisher, checkout = origin_and_checkout
    _advance_origin(publisher)
    before = _git("rev-parse", "HEAD", cwd=checkout)
    (checkout / "pyproject.toml").write_text("local edit\n")

    stderr, execvp, session_launch = _launch(checkout)

    assert _git("rev-parse", "HEAD", cwd=checkout) == before
    assert (checkout / "pyproject.toml").read_text() == "local edit\n"
    assert "1 commit(s) behind origin/main, not updated" in _install_line(stderr)
    assert "current" not in _install_line(stderr)
    assert f"[launch] Warning: this machine's ai-cli-utils checkout {checkout} is 1 commit(s) behind" in stderr
    assert "uncommitted changes" in stderr
    assert f"git -C {checkout} merge --ff-only origin/main" in stderr
    execvp.assert_not_called()
    session_launch.assert_called_once()


def test_given_a_checkout_diverged_from_origin_when_launching_remote_then_it_is_refused_and_left_alone(
    origin_and_checkout,
):
    _, publisher, checkout = origin_and_checkout
    _advance_origin(publisher)
    _commit(checkout, "local.py", "local = True\n")
    before = _git("rev-parse", "HEAD", cwd=checkout)

    stderr, execvp, session_launch = _launch(checkout)

    assert _git("rev-parse", "HEAD", cwd=checkout) == before
    assert "1 commit(s) behind origin/main, not updated" in _install_line(stderr)
    assert "cannot fast-forward" in stderr
    assert f"git -C {checkout} rebase origin/main" in stderr
    execvp.assert_not_called()
    session_launch.assert_called_once()


def test_given_a_checkout_on_another_branch_when_launching_remote_then_it_is_refused_with_the_switch_command(
    origin_and_checkout,
):
    _, publisher, checkout = origin_and_checkout
    _advance_origin(publisher)
    _git("checkout", "-q", "-b", "feature", cwd=checkout)

    stderr, execvp, _ = _launch(checkout)

    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=checkout) == "feature"
    assert "not updated" in _install_line(stderr)
    assert f"git -C {checkout} switch main && git -C {checkout} merge --ff-only origin/main" in stderr
    execvp.assert_not_called()


def test_given_a_checkout_at_origin_when_launching_remote_then_it_reads_current_and_launches(origin_and_checkout):
    _, _, checkout = origin_and_checkout

    stderr, execvp, session_launch = _launch(checkout)

    assert _install_line(stderr).endswith("; current")
    assert "Warning" not in stderr
    execvp.assert_not_called()
    session_launch.assert_called_once()


def test_given_an_unreachable_origin_when_launching_remote_then_it_reads_could_not_check_and_launches(
    origin_and_checkout, tmp_path
):
    _, _, checkout = origin_and_checkout
    _git("remote", "set-url", "origin", str(tmp_path / "missing.git"), cwd=checkout)

    stderr, execvp, session_launch = _launch(checkout)

    line = _install_line(stderr)
    assert "could not check origin/main (git fetch failed" in line
    assert "; current;" not in line
    execvp.assert_not_called()
    session_launch.assert_called_once()


def test_given_a_fetch_that_hangs_when_launching_remote_then_it_is_bounded_and_launches(origin_and_checkout):
    _, publisher, checkout = origin_and_checkout
    _advance_origin(publisher)
    real_run = subprocess.run

    def hanging_fetch(command, **kwargs):
        if "fetch" in command:
            assert kwargs.get("timeout"), "the fetch must carry a timeout"
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    with patch("ai_cli.client_checkout.subprocess.run", side_effect=hanging_fetch):
        stderr, execvp, session_launch = _launch(checkout)

    assert "could not check origin/main (git fetch timed out" in _install_line(stderr)
    execvp.assert_not_called()
    session_launch.assert_called_once()


def test_given_a_checkout_behind_origin_when_launching_locally_then_it_is_not_touched(origin_and_checkout):
    _, publisher, checkout = origin_and_checkout
    _advance_origin(publisher)
    before = _git("rev-parse", "HEAD", cwd=checkout)

    stderr, _, session_launch = _launch(checkout, argv=("ai", "c", "session-1"))

    assert _git("rev-parse", "HEAD", cwd=checkout) == before
    assert _install_line(stderr).endswith("; current")
    session_launch.assert_called_once()
