"""``ai c`` requires tmux OR a harness-owned pty, and refuses at launch without both.

A session's auto-compact prompt is submitted through the terminal that owns the
engine process, and a running process's controlling terminal cannot be relocated
afterwards -- so the transport is settled at launch or never. Before this, a launch
on a machine with neither started anyway and went silent hours later when the session
filled its context.

Three outcomes, and the third is what keeps this portable. A transport resolves and
the launch proceeds; a transport was achievable here and withheld, so the launch is
refused and nothing is created; or no transport is implementable on this platform at
all, which proceeds with one notice. ``os.openpty`` is POSIX-only, so refusing the
third case would strand ``ai c`` on every Windows host and reintroduce the resolved
P1 ``AI-CLI-vs8``.
"""

import ast
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli import compact_transport
from ai_cli.main import _do_session_launch

_NO_PTY_PLATFORM = compact_transport.PtyProbe(
    False,
    "this platform has no os.openpty(), so a pty cannot be allocated (it is POSIX-only)",
    platform_supported=False,
)

pytestmark = pytest.mark.skipif(
    not hasattr(os, "openpty"),
    reason="the pty half of the requirement is POSIX-only; the refusal path is covered by unit tests",
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def real_repo(tmp_path):
    """A clone with a real upstream, which is what the launcher accepts."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    return repo


def _launch_kwargs(**over):
    kwargs = {
        "engine": "c",
        "name": "1",
        "resume": False,
        "once": False,
        "bare": True,
        "notify": False,
        "sandbox": False,
        "no_worktree": False,
        "remote": False,
        "project": "",
        "is_remote": False,
        "project_prefix_override": "kg",
        "extra_args": [],
        "config": {"worktree": {"enabled": True}, "session": {"use_tmux": False}},
    }
    kwargs.update(over)
    return kwargs


def _run_launch(repo, tmp_path, monkeypatch, **over):
    """Drive the real launch entry point, recording any exec instead of performing it."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    execs: list[tuple] = []

    def fake_execvp(file, args):
        execs.append((file, list(args), str(Path.cwd())))
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _do_session_launch(**_launch_kwargs(**over))
    return excinfo.value.code, execs


# --- the two-way control, at the real entry point -------------------------------


def test_given_neither_tmux_nor_a_pty_when_ai_c_launches_then_it_refuses_naming_both(
    real_repo, tmp_path, monkeypatch, capsys
):
    code, execs = _run_launch(
        real_repo,
        tmp_path,
        monkeypatch,
        config={"worktree": {"enabled": True}, "session": {"use_tmux": False, "use_pty": False}},
    )
    output = capsys.readouterr().err

    assert code == 1, "a launch with no submission transport must refuse"
    assert execs == [], "the refusal must make the exec unreachable, not merely warn before it"
    assert "tmux: unavailable" in output
    assert "harness-owned pty: unavailable" in output
    assert "[session] use_pty = false in config.toml" in output, "the refusal must name WHY each side failed"


def test_given_neither_transport_when_the_launch_refuses_then_no_worktree_was_created(real_repo, tmp_path, monkeypatch):
    """The refusal sits above every write, so a refused launch leaves no cleanup."""
    _run_launch(
        real_repo,
        tmp_path,
        monkeypatch,
        config={"worktree": {"enabled": True}, "session": {"use_tmux": False, "use_pty": False}},
    )

    assert not (real_repo / ".worktrees" / "kg-1").exists(), "a refused launch must create no worktree"
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=real_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "kg-1" not in listed, "a refused launch must register no worktree"


def test_given_a_pty_is_available_when_ai_c_launches_bare_then_it_proceeds(real_repo, tmp_path, monkeypatch):
    """The other half of the control: tmux is off, so the pty is what carries it."""
    code, execs = _run_launch(real_repo, tmp_path, monkeypatch)

    assert code == 0
    assert len(execs) == 1, "with a usable transport the launch must reach the engine"
    assert execs[0][0] == "claude"


def test_given_a_pty_is_available_when_the_transport_resolves_then_it_is_the_pty(real_repo, tmp_path, monkeypatch):
    resolved = compact_transport.resolve(tmux_usable=False, tmux_detail="--bare requested", config={})

    assert resolved.kind == "pty"
    assert resolved.usable


def test_given_tmux_hosts_the_session_when_the_transport_resolves_then_the_pty_is_not_probed():
    """A tmux launch must allocate nothing: tmux already carries the injection."""
    probes: list[int] = []

    def spy_probe():
        probes.append(1)
        return compact_transport.PtyProbe(True, "should not be reached")

    resolved = compact_transport.resolve(
        tmux_usable=True,
        tmux_detail="tmux is the default session mode",
        config={},
        probe=spy_probe,
    )

    assert resolved.kind == "tmux"
    assert probes == [], "resolving to tmux must not allocate a pty"


# --- a platform with no pty API proceeds; it is not refused ---------------------


def test_given_a_platform_with_no_pty_api_when_ai_c_launches_bare_then_it_proceeds(real_repo, tmp_path, monkeypatch):
    """The portability control: Windows has no os.openpty() and no tmux.

    Refusing here would strand the launch route on every Windows host and
    reintroduce the resolved P1 AI-CLI-vs8. Nothing the operator can do in-process
    satisfies the condition, so it is not a guard -- it degrades and says so.
    """
    with patch("ai_cli.compact_transport.probe_pty", return_value=_NO_PTY_PLATFORM):
        code, execs = _run_launch(real_repo, tmp_path, monkeypatch)

    assert code == 0, "a platform that cannot host any transport must not be refused"
    assert len(execs) == 1, "the launch must still reach the engine"


def test_given_a_platform_with_no_pty_api_when_ai_c_launches_then_it_says_so_once(
    real_repo, tmp_path, monkeypatch, capsys
):
    """Degraded, not silent: silent degradation is the shape being eliminated."""
    with patch("ai_cli.compact_transport.probe_pty", return_value=_NO_PTY_PLATFORM):
        _run_launch(real_repo, tmp_path, monkeypatch)
    output = capsys.readouterr().err

    assert "harness cannot drive compaction" in output
    assert "auto-compact still applies" in output, "must not claim the session is uncompactable"
    assert "Install tmux" in output, "must name the upgrade path"


def test_given_no_pty_api_when_the_transport_resolves_then_it_is_platform_limited_not_a_refusal():
    resolved = compact_transport.resolve(
        tmux_usable=False,
        tmux_detail="no native tmux on Windows (it runs under WSL/MSYS2/Cygwin)",
        config={},
        probe=lambda: _NO_PTY_PLATFORM,
    )

    assert not resolved.usable
    assert resolved.platform_limited
    assert not resolved.must_refuse, "a condition no in-process action can satisfy must not refuse"


def test_given_an_explicit_opt_out_on_a_pty_less_platform_when_resolved_then_it_still_refuses():
    """`use_pty = false` is the operator asking for tmux-or-nothing, not a platform gap."""
    probes: list[int] = []

    def spy_probe():
        probes.append(1)
        return _NO_PTY_PLATFORM

    resolved = compact_transport.resolve(
        tmux_usable=False,
        tmux_detail="--bare requested",
        config={"session": {"use_pty": False}},
        probe=spy_probe,
    )

    assert resolved.must_refuse
    assert not resolved.platform_limited
    assert probes == [], "an explicit opt-out must not allocate a pty to find out"


def test_given_no_openpty_when_the_probe_runs_then_it_reports_the_platform_unsupported():
    """The platform split is derived from the missing API, not hardcoded per-OS.

    A stand-in for ``os`` that simply lacks ``openpty`` is the honest simulation of
    Windows here: that absence, not ``sys.platform``, is what the probe reads.
    """

    class _OsWithoutOpenpty:
        """Everything the probe touches except the one API Windows does not have."""

        setsid = staticmethod(lambda: None)

    assert not hasattr(_OsWithoutOpenpty, "openpty"), "the stand-in must actually lack openpty"

    with patch("ai_cli.compact_transport.os", _OsWithoutOpenpty):
        result = compact_transport.probe_pty()

    assert not result.usable
    assert not result.platform_supported
    assert "os.openpty()" in result.detail


# --- the probe reads, it does not trust its own write ---------------------------


def test_given_the_pty_probe_when_nothing_reaches_the_slave_then_it_reports_unusable():
    """A successful write is not a successful delivery -- the 2026-09-27 discipline.

    The negative control for the probe itself: the write is swallowed, so the slave
    reads nothing. A probe that trusted its own ``os.write`` return value would pass
    here, and would then let a launch proceed onto a transport that carries nothing.
    """
    with patch("ai_cli.compact_transport.os.write", return_value=len(compact_transport._PROBE_MARKER)):
        result = compact_transport.probe_pty(timeout=0.2)

    assert not result.usable
    assert "nothing arrived at its slave" in result.detail


def test_given_the_pty_probe_when_the_slave_reads_other_bytes_then_it_reports_unusable():
    """What the slave READ is the verdict, so corrupted input is a refusal too.

    The replacement keeps the trailing newline. A pty is in canonical mode, so the
    slave has nothing to read until a line terminator arrives -- corrupting that away
    would be caught by the no-arrival branch instead and would prove nothing about
    the comparison.
    """
    real_write = os.write

    def corrupting_write(fd, data):
        return real_write(fd, b"x" * (len(data) - 1) + b"\n")

    with patch("ai_cli.compact_transport.os.write", side_effect=corrupting_write):
        result = compact_transport.probe_pty(timeout=1.0)

    assert not result.usable
    assert "instead of the marker" in result.detail


def test_given_a_posix_host_when_the_pty_probe_runs_then_the_marker_arrives_at_the_slave():
    result = compact_transport.probe_pty()

    assert result.usable, result.detail


def test_given_the_pty_probe_when_allocation_fails_then_the_errno_is_reported():
    with patch("ai_cli.compact_transport.os.openpty", side_effect=OSError(24, "Too many open files")):
        result = compact_transport.probe_pty()

    assert not result.usable
    assert "Too many open files" in result.detail


# --- the privileged path stays unused ------------------------------------------


def test_given_the_submission_transport_when_implemented_then_tiocsti_is_never_used():
    """TIOCSTI is denied on this platform and requires privilege.

    Read from the AST rather than by grepping the text: the module's own docstring
    explains why TIOCSTI is excluded, and a text search would match that explanation
    and pass for the wrong reason.
    """
    tree = ast.parse(Path(compact_transport.__file__).read_text())
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    # A termios constant can also be reached by name, as `hasattr(termios, "...")`,
    # which is an ordinary string constant in the tree. Collecting every string
    # constant instead would match the module docstring's own explanation of why
    # TIOCSTI is excluded, so only the lookup argument counts.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"hasattr", "getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            referenced.add(node.args[1].value)

    assert "TIOCSTI" not in referenced, "the submission path must not use TIOCSTI"
    assert "TIOCSCTTY" in referenced, "the pty path still needs the child to claim a controlling terminal"


def test_given_use_pty_false_when_the_opt_out_is_read_then_it_is_honoured():
    assert compact_transport.config_opts_out({"session": {"use_pty": False}})
    assert not compact_transport.config_opts_out({"session": {}})
    assert not compact_transport.config_opts_out({})
    assert not compact_transport.config_opts_out(None)
