"""The spawn guard must not be walkable past by running a protected binary in a shell.

AI-CLI-013v. The guard refuses by program NAME, and a shell is a legitimate program that
runs whatever string it is handed -- so ``os.execvp("zsh", ["zsh", "-c", "ssh ..."])`` named
nothing protected and was allowed. Measured 2026-09-27: that exec replaced the pytest
process, the run ended mid-collection with exit 0 and no summary, and a real outbound SSH
connection was attempted.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from unittest.mock import patch

import pytest
from conftest import (
    _PROTECTED_TEST_BINARIES,
    _command_program,
    payload_word_reaches_a_real_binary,
    shell_command_payloads,
    shell_payload_protected_binary,
)

#: The exact argv shape that walked past the guard, with a host that cannot resolve.
LAUNDERED_ARGV = ["zsh", "-c", "ssh user@example.com 'ai c'; exec zsh -l"]


@pytest.fixture
def protected_binaries_are_installed():
    """Resolve every bare name to a real path outside the temp tree, on any host.

    The rule is deliberately sensitive to what a payload would actually reach, so a test of
    the REFUSING half must pin that input rather than inherit it: whether ``mosh`` or
    ``direnv`` happens to be installed is not what any of these tests is about, and on
    Windows not even ``ssh`` can be assumed.
    """
    with patch("shutil.which", side_effect=lambda name, *args, **kwargs: f"/usr/bin/{name}"):
        yield


def test_given_shell_argv_naming_ssh_when_the_name_guard_inspects_it_then_it_sees_nothing_protected():
    """Negative control: the program-name rule alone passes the argv that broke the run.

    This is what makes the payload rule necessary rather than belt-and-braces. If this test
    ever fails because ``zsh`` became a protected name, the fix was replaced by a blanket
    shell ban, which the issue explicitly rejected.
    """
    assert _command_program(LAUNDERED_ARGV) == "zsh"
    assert _command_program(LAUNDERED_ARGV) not in _PROTECTED_TEST_BINARIES


def test_given_shell_execvp_when_its_payload_runs_ssh_then_the_spawn_is_refused_and_recorded(
    expect_protected_spawn, protected_binaries_are_installed
):
    """The reproducing shape: a shell exec'd with a payload that runs a real ``ssh``.

    ``os.execvp`` is the call that made this destroy the evidence rather than fail -- the
    process is REPLACED, so there is no traceback and no red test. The refusal has to be
    raised (so the call never happens) and recorded (so a caller that catches broadly cannot
    swallow it silently), which is why this test opts into the record instead of ignoring it.
    """
    with pytest.raises(RuntimeError, match="laundering route"):
        os.execvp("zsh", LAUNDERED_ARGV)

    assert "ssh" in expect_protected_spawn


def test_given_a_swallowed_refusal_when_the_test_ends_then_the_attempt_is_still_reported(
    expect_protected_spawn, protected_binaries_are_installed
):
    """Prevention without a report is how this class of defect survives, so record both."""
    # A caller that catches broadly, as production and several tests here do.
    with contextlib.suppress(RuntimeError):
        os.execvp("bash", ["bash", "-c", "mosh user@example.com -- ai c"])

    assert expect_protected_spawn == ["mosh"], (
        "the exception was caught, so only the record can tell the test it reached a protected boundary"
    )


def test_given_a_shell_payload_touching_nothing_protected_when_run_then_it_still_works():
    """The rule must stay precise: a shell is allowed, and most payloads are innocent.

    Run for real rather than asserted against the predicate, because the regression this
    guards against is the guard refusing a legitimate spawn -- which only a real call proves.
    """
    completed = subprocess.run(
        ["bash", "-c", "echo guarded-but-allowed"], capture_output=True, text=True, timeout=30, check=False
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "guarded-but-allowed"


def test_given_a_shell_running_a_script_path_when_inspected_then_the_path_is_not_a_payload():
    """A script PATH is not a command string, and scanning one would charge its directory.

    Several tests here run a generated script as ``<shell> [options] <path>``; the supervisor
    tests pass ``zsh -o NO_BG_NICE <path>``. Treating that path as a command string would let
    the directory a temp file happens to sit in decide whether the test is refused.
    """
    assert shell_command_payloads(["zsh", "-o", "NO_BG_NICE", "/tmp/ssh/supervisor.sh"]) == []
    assert shell_payload_protected_binary("zsh", ["zsh", "-o", "NO_BG_NICE", "/tmp/ssh/supervisor.sh"]) is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["zsh", "-c", "ssh host"], "ssh"),
        (["bash", "-lc", "exec ssh host"], "ssh"),
        (["sh", "-c", "(/usr/bin/ssh host)"], "ssh"),
        (["bash", "-c", "true && osascript -e beep"], "osascript"),
        (["cmd.exe", "/c", "claude --version"], "claude"),
        (["pwsh", "-Command", "direnv exec . ai c"], "direnv"),
        (["bash", "-c", "echo fine; sleep 1"], None),
        (["python3", "-c", "import ssh_nothing"], None),
    ],
)
def test_given_a_shell_command_string_when_inspected_then_a_protected_binary_is_named(
    argv, expected, protected_binaries_are_installed
):
    """Every route a shell takes a command string, and the non-shell case that must not match.

    ``-lc`` is in here because production builds one: the mosh remote command is
    ``<shell> -l -c <cmd>``. The ``python3`` row is the boundary of the rule -- an interpreter
    that also takes ``-c`` is not a shell and its payload is code, not a command line.
    """
    assert shell_payload_protected_binary(_command_program(argv), argv) == expected


def test_given_a_stub_on_a_clean_path_when_a_shell_payload_names_it_then_it_is_not_refused(tmp_path, monkeypatch):
    """The carve-out that keeps the rule precise, and the tests that depend on it green.

    ``test_session_launch_shell_resolution`` writes a ``claude`` and a ``direnv`` stub into a
    temp bin directory, points PATH at it alone, and runs the generated launch command through
    the interpreter the launcher chose -- the only way that path is observed end to end. The
    payload names a protected binary and reaches nothing of the operator's, so the guard must
    let it through while still refusing the same word when PATH resolves it for real.
    """
    bin_dir = tmp_path / "cleanbin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    argv = ["bash", "-c", "claude --version"]

    assert shell_payload_protected_binary("bash", argv) is None
    assert not payload_word_reaches_a_real_binary("claude")
    assert shell_payload_protected_binary("bash", ["bash", "-c", f"{stub} --version"]) is None, (
        "naming the stub by absolute path must be allowed too, or a test is punished for being explicit"
    )
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        assert shell_payload_protected_binary("bash", argv) == "claude"


def test_given_a_binary_that_is_not_installed_when_a_payload_names_it_then_it_is_not_refused(monkeypatch, tmp_path):
    """An unresolvable name is no hazard: the shell could not have run it either."""
    monkeypatch.setenv("PATH", str(tmp_path))

    assert not payload_word_reaches_a_real_binary("mosh")
    assert shell_payload_protected_binary("sh", ["sh", "-c", "mosh user@example.com"]) is None


def test_given_a_test_allowed_to_use_tmux_when_it_runs_tmux_in_a_shell_then_it_is_not_refused(
    protected_binaries_are_installed,
):
    """The allowance a ``real_tmux`` test gets for a direct spawn must survive a shell.

    Otherwise the payload rule would forbid through a shell exactly what the same test may do
    directly, which is the shape of an inconsistency that gets a guard worked around.
    """
    argv = ["bash", "-c", "tmux -S /tmp/socket kill-server"]

    assert shell_payload_protected_binary("bash", argv) == "tmux"
    assert shell_payload_protected_binary("bash", argv, frozenset({"tmux"})) is None


def test_given_an_unbalanced_quote_in_a_payload_when_inspected_then_the_binary_is_still_found():
    """A payload whose quoting defeats ``shlex`` must not defeat the guard with it."""
    assert shell_payload_protected_binary("sh", ["sh", "-c", "ssh host 'unterminated"]) == "ssh"
