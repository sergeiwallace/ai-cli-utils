"""``[tmux] binary`` names the one multiplexer executable every launcher tmux call runs.

On a Windows machine whose tmux is psmux, more than one build can answer to ``tmux``,
and which one a shell finds depends on its PATH. The setting pins one build per
machine: globally in ``[tmux]``, or for one machine in ``[tmux.hosts.<host id>]``.
With no setting the launcher keeps running ``tmux`` through the PATH lookup, and a
configured binary that does not exist is refused by name, never swapped for PATH's
tmux.

Every case drives a real entry point (the probe, the launch-time report, the tmux
preflight, ``ai attach``, a dry-run ``ai c``) with fakes at the process boundary, so
no case needs psmux or tmux installed.
"""

from __future__ import annotations

import ast
import contextlib
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import config as _config
from ai_cli import tmux_binary, tmux_setup
from ai_cli.main import cli

PSMUX_V = "tmux 3.4.0\npsmux 3.4.0 (a1b2c3d 2026-10-13)"
SRC = Path(__file__).resolve().parent.parent / "src" / "ai_cli"


def _write_config(text: str) -> Path:
    path = Path(_config.config_file_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _executable(directory: Path, name: str) -> Path:
    """An executable file that is never run: every probe below is faked.

    Named ``.exe`` so the same file resolves through ``shutil.which`` on Windows too.
    """
    path = directory / f"{name}.exe"
    path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _same(a: str | os.PathLike | None, b: str | os.PathLike) -> bool:
    """One file, however it is spelled (a venv interpreter is often a symlink)."""
    return a is not None and Path(a).resolve() == Path(b).resolve()


def _toml_path(path: Path) -> str:
    """A TOML literal string for ``path``: no escaping, so Windows backslashes survive."""
    return f"'{path}'"


@pytest.fixture
def probes(monkeypatch):
    """Record every probe argv; answer as psmux would. PATH's ``tmux`` is a different file."""
    calls: list[list[str]] = []

    def _probe(argv, timeout):
        calls.append(list(argv))
        if argv[1:2] == ["-V"]:
            return PSMUX_V
        if argv[1:2] == ["display-message"]:
            return "3.4.0"
        raise AssertionError(f"unexpected probe: {argv}")

    real_which = tmux_binary.shutil.which

    def _which(name, *args, **kwargs):
        if name == "tmux":
            return "/usr/bin/tmux"
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(tmux_setup, "_probe_output", _probe)
    monkeypatch.setattr(tmux_binary.shutil, "which", _which)
    return calls


# --------------------------------------------------------------------------
# The probe and the launch-time report
# --------------------------------------------------------------------------


def test_given_a_global_tmux_binary_when_probed_then_every_probe_runs_that_binary(tmp_path, probes) -> None:
    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    report = tmux_setup.probe()

    assert probes, "the probe ran nothing, so it proved nothing"
    assert all(_same(argv[0], psmux) for argv in probes)
    assert _same(report.path, psmux)
    assert report.kind == "psmux"
    assert report.client_version == "3.4.0"


def test_given_a_global_tmux_binary_when_reported_then_the_launch_output_names_it(tmp_path, probes) -> None:
    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    lines = tmux_setup.report_lines(report=tmux_setup.probe(), bare=False, reason="default")

    first = next(text for phase, text in lines if phase == "tmux")
    assert first.startswith("psmux 3.4.0 at ")
    assert psmux.name in first


def test_given_a_per_machine_binary_when_probed_then_it_overrides_the_global_one(tmp_path, probes) -> None:
    global_binary = _executable(tmp_path, "tmux-global")
    machine_binary = _executable(tmp_path, "psmux")
    _write_config(
        '[machine]\nhost_id = "build-box"\n\n'
        f"[tmux]\nbinary = {_toml_path(global_binary)}\n\n"
        f"[tmux.hosts.build-box]\nbinary = {_toml_path(machine_binary)}\n"
    )

    report = tmux_setup.probe()

    assert probes
    assert all(_same(argv[0], machine_binary) for argv in probes)
    assert _same(report.path, machine_binary)


def test_given_a_binary_for_another_machine_when_probed_then_the_global_one_is_used(tmp_path, probes) -> None:
    global_binary = _executable(tmp_path, "psmux")
    other_binary = _executable(tmp_path, "tmux-other")
    _write_config(
        '[machine]\nhost_id = "build-box"\n\n'
        f"[tmux]\nbinary = {_toml_path(global_binary)}\n\n"
        f"[tmux.hosts.laptop]\nbinary = {_toml_path(other_binary)}\n"
    )

    tmux_setup.probe()

    assert probes
    assert all(_same(argv[0], global_binary) for argv in probes)


def test_given_no_tmux_binary_setting_when_probed_then_the_path_lookup_of_tmux_is_kept(probes) -> None:
    _write_config("[session]\nuse_tmux = true\n")

    report = tmux_setup.probe()

    assert probes
    assert all(argv[0] == "tmux" for argv in probes)
    assert report.path == "/usr/bin/tmux"


def test_given_an_empty_tmux_binary_when_probed_then_it_reads_as_unset(probes) -> None:
    _write_config('[tmux]\nbinary = ""\n')

    tmux_setup.probe()

    assert probes
    assert all(argv[0] == "tmux" for argv in probes)


def test_given_a_missing_configured_binary_when_probed_then_it_is_refused_by_name_not_replaced_by_path(
    tmp_path, probes
) -> None:
    missing = tmp_path / "no-such-psmux.exe"
    config_path = _write_config(f"[tmux]\nbinary = {_toml_path(missing)}\n")

    report = tmux_setup.probe()
    lines = tmux_setup.report_lines(report=report, bare=True, reason="default")

    assert probes == [], "a missing configured binary must not be probed, nor PATH's tmux in its place"
    assert report.present is False
    assert report.path is None
    warning = next(text for phase, text in lines if phase == "Warning")
    assert str(missing) in warning
    assert str(config_path) in warning
    assert "[tmux] binary" in warning
    assert "/usr/bin/tmux" not in "\n".join(text for _, text in lines)


def test_given_a_missing_configured_binary_when_tmux_is_ensured_then_nothing_is_installed(
    tmp_path, probes, monkeypatch
) -> None:
    missing = tmp_path / "no-such-psmux.exe"
    _write_config(f"[tmux]\nbinary = {_toml_path(missing)}\n")
    monkeypatch.setattr(tmux_setup, "install_tmux", MagicMock(side_effect=AssertionError("installed a tmux")))

    result = tmux_setup.ensure_tmux(auto_install=True, quiet=True)

    assert result.installed is False
    assert result.unusable is True
    assert "[tmux] binary" in result.detail
    assert probes == []


def test_given_the_setting_changes_between_calls_when_probed_then_each_call_reads_it_fresh(tmp_path, probes) -> None:
    first = _executable(tmp_path, "psmux")
    second = _executable(tmp_path, "pmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(first)}\n")
    tmux_setup.probe()
    seen_first = list(probes)
    probes.clear()

    _write_config(f"[tmux]\nbinary = {_toml_path(second)}\n")
    tmux_setup.probe()

    assert seen_first and probes
    assert all(_same(argv[0], first) for argv in seen_first)
    assert all(_same(argv[0], second) for argv in probes)


# --------------------------------------------------------------------------
# The real CLI entry points
# --------------------------------------------------------------------------


def test_given_a_global_tmux_binary_when_attaching_then_the_check_and_the_exec_use_it(tmp_path) -> None:
    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    with (
        patch("sys.argv", ["ai", "attach", "myproject-1"]),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as run,
        patch("os.execvp", side_effect=SystemExit(0)) as execvp,
    ):
        with pytest.raises(SystemExit):
            cli()

    ran = [call.args[0] for call in run.call_args_list if call.args and isinstance(call.args[0], list)]
    assert any(_same(argv[0], psmux) and argv[1] == "has-session" for argv in ran)
    assert not any(argv[0] == "tmux" for argv in ran)
    file, args = execvp.call_args.args
    assert _same(file, psmux)
    assert _same(args[0], psmux)
    assert args[1:] == ["attach-session", "-t", "myproject-1"]


@pytest.fixture(params=sorted({sys.platform, "win32"}))
def launch_platform(request, monkeypatch) -> str:
    """This host's own platform, and Windows (the psmux machine) faked on any other host.

    Faked for the whole case, not around the launch alone: ``config.toml``'s location
    depends on ``sys.platform``, so a platform faked only around the launch reads a
    different file from the one the case wrote.
    """
    monkeypatch.setattr(sys, "platform", request.param)
    return request.param


@contextlib.contextmanager
def _dry_run_launch(tmp_path: Path, monkeypatch):
    """``ai c 7 --dry-run`` through the real entry point, with the repo and name resolution faked."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    with contextlib.ExitStack() as stack:
        for patcher in (
            patch("sys.argv", ["ai", "c", "7", "--dry-run", "--no-direnv"]),
            patch("ai_cli.tmux_setup.install_tmux", side_effect=AssertionError("a dry run installed tmux")),
            patch("ai_cli.session.detect_repo_root", return_value=str(tmp_path)),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="myproject"),
            patch("ai_cli.session.is_current_project_resolved", return_value=True),
            patch("ai_cli.session.build_session_name", return_value=("c-myproject-7", "myproject-7")),
            patch("ai_cli.main.os.execvp", side_effect=AssertionError("a dry run must not exec")),
        ):
            stack.enter_context(patcher)
        yield


def test_given_a_global_tmux_binary_when_launching_then_the_tmux_preflight_runs_that_binary(
    tmp_path, monkeypatch, probes, capsys
) -> None:
    # A real executable that exists on every platform, so a tmux call outside the
    # faked probe seam fails harmlessly instead of reaching a real multiplexer.
    # Runs on the host's own platform only: under a faked win32, ``shutil.which``
    # wants a PATHEXT suffix the interpreter of a POSIX host does not have.
    _write_config(f"[tmux]\nbinary = {_toml_path(Path(sys.executable))}\n")

    with _dry_run_launch(tmp_path, monkeypatch), pytest.raises(SystemExit) as exc_info:
        cli()

    captured = capsys.readouterr()
    assert exc_info.value.code == 0, captured.err
    assert probes, "the launch reached no tmux probe"
    assert all(_same(argv[0], sys.executable) for argv in probes)
    assert "psmux 3.4.0 at " in captured.err + captured.out


def test_given_a_missing_configured_binary_when_launching_then_the_launch_is_refused_by_name(
    tmp_path, monkeypatch, launch_platform, probes, capsys
) -> None:
    missing = tmp_path / "no-such-psmux.exe"
    config_path = _write_config(f"[tmux]\nbinary = {_toml_path(missing)}\n")

    with _dry_run_launch(tmp_path, monkeypatch), pytest.raises(SystemExit) as exc_info:
        cli()

    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert exc_info.value.code == 1
    assert probes == []
    assert str(missing) in output
    assert str(config_path) in output
    assert "[tmux] binary" in output


# --------------------------------------------------------------------------
# The generated session script
# --------------------------------------------------------------------------


def test_given_a_global_tmux_binary_when_the_shell_prelude_is_built_then_bare_tmux_runs_it(tmp_path) -> None:
    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    prelude = tmux_binary.shell_prelude()

    assert prelude.startswith("tmux() {")
    assert psmux.as_posix() in prelude


def test_given_no_tmux_binary_setting_when_the_shell_prelude_is_built_then_it_only_clears_the_export() -> None:
    _write_config("")

    commands = [line.strip() for line in tmux_binary.shell_prelude().splitlines() if not line.lstrip().startswith("#")]

    assert commands == [f"unset {tmux_binary.ENV_VAR}"]


# --------------------------------------------------------------------------
# The statusline script, which the agent runs as a child of the session script
# --------------------------------------------------------------------------

STATUSLINE = SRC / "data" / "statusline-command.sh"
STATUSLINE_INPUT = '{"model":"claude-sonnet-4-6","workspace":{"project_dir":"/tmp"}}'

posix_bash_with_jq = pytest.mark.skipif(
    sys.platform == "win32" or __import__("shutil").which("jq") is None,
    reason="statusline-command.sh needs a POSIX bash and jq",
)


def _recording_tmux(directory: Path, name: str, log: Path) -> Path:
    """A fake multiplexer that logs each call and answers display-message for ``session-1``."""
    path = directory / name
    path.write_text(
        f'#!/bin/sh\necho "$0 $*" >> "{log}"\nif [ "$1" = "display-message" ]; then echo "session-1|\\$1"; fi\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_statusline_in_session(tmp_path: Path, inherited: dict[str, str] | None = None):
    """Run the statusline as a session would: the session script's prelude, then the agent's child."""
    import subprocess

    home, scratch, bin_dir = tmp_path / "home", tmp_path / "tmp", tmp_path / "bin"
    for directory in (home, scratch, bin_dir):
        directory.mkdir(exist_ok=True)
    (bin_dir / "ai").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "ai").chmod(0o755)
    path_log = tmp_path / "path-tmux.log"
    _recording_tmux(bin_dir, "tmux", path_log)
    session_script = tmp_path / "session.sh"
    session_script.write_text(f"{tmux_binary.shell_prelude()}exec bash {STATUSLINE.as_posix()}\n", encoding="utf-8")
    env = {
        **os.environ,
        **(inherited or {}),
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TMUX": "/tmp/tmux-1000/default,1,0",
        "COLUMNS": "200",
        "GIT_BRANCH_CACHE": "main",
        "CC_BILLING_MODE": "subscription",
    }
    result = subprocess.run(
        ["bash", str(session_script)],
        input=STATUSLINE_INPUT,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    return result, path_log


@posix_bash_with_jq
def test_given_a_global_tmux_binary_when_the_statusline_runs_in_a_session_then_it_queries_that_binary(
    tmp_path,
) -> None:
    configured_log = tmp_path / "configured.log"
    psmux = _recording_tmux(tmp_path, "psmux", configured_log)
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    result, path_log = _run_statusline_in_session(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "display-message" in configured_log.read_text(encoding="utf-8")
    assert "session-1" in result.stdout
    assert not path_log.exists(), "the statusline ran the tmux on PATH instead of [tmux] binary"


@posix_bash_with_jq
def test_given_no_tmux_binary_setting_when_the_statusline_runs_then_the_tmux_on_path_answers(tmp_path) -> None:
    """An outer session's exported binary must not outlive a config that no longer sets one."""
    stale_log = tmp_path / "stale.log"
    stale = _recording_tmux(tmp_path, "old-psmux", stale_log)
    _write_config("")

    result, path_log = _run_statusline_in_session(tmp_path, inherited={tmux_binary.ENV_VAR: str(stale)})

    assert result.returncode == 0, result.stderr
    assert "display-message" in path_log.read_text(encoding="utf-8")
    assert "session-1" in result.stdout
    assert not stale_log.exists()


# --------------------------------------------------------------------------
# Remote commands: the tmux there is the other host's, never this host's setting
# --------------------------------------------------------------------------


def test_given_a_local_tmux_binary_when_reconnect_lists_remote_sessions_then_ssh_runs_bare_tmux_there(
    tmp_path,
) -> None:
    from ai_cli import main as _main

    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")

    with (
        patch.object(_main.subprocess, "run", return_value=MagicMock(returncode=0, stdout="")) as run,
        pytest.raises(SystemExit),
    ):
        _main._do_reconnect(None, {"remote": {"host": "192.0.2.10", "user": "user"}})

    argv = run.call_args.args[0]
    assert argv[:3] == ["ssh", "user@192.0.2.10", "tmux"]
    assert not any(str(psmux) in part for part in argv)


def test_given_a_local_tmux_binary_when_a_remote_session_is_probed_then_ssh_runs_bare_tmux_there(
    tmp_path,
) -> None:
    from ai_cli import session_registry

    psmux = _executable(tmp_path, "psmux")
    _write_config(f"[tmux]\nbinary = {_toml_path(psmux)}\n")
    record = {"remote": {"alias": "", "session": "c-myproject-1"}}

    with patch.object(session_registry.subprocess, "run", return_value=MagicMock(returncode=1)) as run:
        alive = session_registry._remote_session_alive(record, {"remote": {"host": "192.0.2.10", "user": "user"}})

    assert alive is False
    remote_command = run.call_args.args[0][-1]
    assert remote_command.startswith("tmux has-session ")
    assert str(psmux) not in remote_command


# --------------------------------------------------------------------------
# The guard: no module chooses tmux's argv[0] itself
# --------------------------------------------------------------------------


# Calls whose first argument names the program to find or exec.
_NAMED_PROGRAM_CALLS = ("which", "execvp", "execlp", "_exec_handoff")


def _literal_tmux_argv_sites(root: Path = SRC) -> list[str]:
    """Every list whose first element is the literal ``"tmux"``, every ``("tmux",)``
    command tuple, and every ``which("tmux")`` or exec of ``"tmux"``, outside the resolver."""
    sites: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "tmux_binary.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            first = None
            if (isinstance(node, ast.List) and node.elts) or (isinstance(node, ast.Tuple) and len(node.elts) == 1):
                first = node.elts[0]
            elif isinstance(node, ast.Call) and ast.unparse(node.func).endswith(_NAMED_PROGRAM_CALLS) and node.args:
                first = node.args[0]
            if isinstance(first, ast.Constant) and first.value == "tmux":
                sites.append(f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {ast.unparse(node)[:80]}")
    return sites


def test_given_the_package_source_when_scanned_then_no_module_builds_a_literal_tmux_argv() -> None:
    sites = _literal_tmux_argv_sites()

    assert sites == [], "route these through ai_cli.tmux_binary:\n" + "\n".join(sites)


def test_given_a_literal_tmux_argv_when_scanned_then_the_guard_finds_it(tmp_path) -> None:
    """Anti-vacuity control: the scanner must flag the shape it exists to forbid."""
    planted = tmp_path / "ai_cli"
    planted.mkdir()
    (planted / "planted.py").write_text(
        'import os, shutil, subprocess\nsubprocess.run(["tmux", "-V"])\nshutil.which("tmux")\nX = ("tmux",)\n'
        'os.execvp("tmux", argv)\n',
        encoding="utf-8",
    )

    assert len(_literal_tmux_argv_sites(planted)) == 4
