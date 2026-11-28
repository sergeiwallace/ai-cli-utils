"""A supervised Codex launch borrows the display of the user's own verified local desktop.

Each launch test runs the session script's real ``run_agent`` for Codex, fresh and resumed,
with ``ai internal resolve-codex-display`` as a real subprocess. ``loginctl`` and
``systemctl --user`` are fake executables that print scripted output, and the authority
file is a tmp_path file, so nothing here reads the host's sessions or touches a clipboard.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_cli import config as _config
from ai_cli.launch_logging import LAUNCH_LOG_ENV
from ai_cli.main import _handle_internal
from ai_cli.session_script import get_engine_script

# logind and the systemd user manager exist only on Linux, and the detector is Linux-only.
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="logind display detection is Linux-only")

UID = os.getuid() if sys.platform.startswith("linux") else 0
SESSION = "session-1"
_MICROSECONDS = 1_000_000


def _now_monotonic_us() -> int:
    return int(time.clock_gettime(time.CLOCK_MONOTONIC) * _MICROSECONDS)


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


class Desktop:
    """The scripted logind sessions, user manager environment and authority file."""

    def __init__(self, root: Path):
        self.root = root
        self.authority = root / "Xauthority"
        self.sessions: list[dict[str, str]] = []
        self.manager: dict[str, str] | None = {}

    def add_session(self, session_id="2", **overrides) -> dict[str, str]:
        session = {
            "Id": session_id,
            "User": str(UID),
            "State": "active",
            "Remote": "no",
            "Type": "wayland",
            "Display": "",
            # Started a minute ago, so an authority file written now is fresh.
            "TimestampMonotonic": str(_now_monotonic_us() - 60 * _MICROSECONDS),
        }
        session.update(overrides)
        self.sessions.append(session)
        return session

    def write(self) -> None:
        listing = "".join(f"{s['Id']} {s['User']} user seat0 tty2\n" for s in self.sessions)
        (self.root / "sessions.txt").write_text(listing, encoding="utf-8")
        for session in self.sessions:
            text = "".join(f"{key}={value}\n" for key, value in session.items())
            (self.root / f"session-{session['Id']}.txt").write_text(text, encoding="utf-8")
        manager = self.root / "manager.txt"
        if self.manager is None:
            manager.unlink(missing_ok=True)
        else:
            manager.write_text("".join(f"{key}={value}\n" for key, value in self.manager.items()), encoding="utf-8")


@pytest.fixture
def desktop(tmp_path: Path) -> Desktop:
    root = tmp_path / "desktop"
    root.mkdir()
    state = Desktop(root)
    state.authority.write_text("cookie", encoding="utf-8")
    state.manager = {"DISPLAY": ":0", "XAUTHORITY": str(state.authority), "LANG": "C.UTF-8"}
    return state


@pytest.fixture
def tools(tmp_path: Path, desktop: Desktop) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    probes = shlex.quote(str(tmp_path / "probes.log"))
    root = shlex.quote(str(desktop.root))
    _executable(
        bin_dir / "loginctl",
        "#!/bin/sh\n"
        f'printf "%s\\n" "loginctl $*" >> {probes}\n'
        f'[ "$1" = list-sessions ] && exec cat {root}/sessions.txt\n'
        f'[ "$1" = show-session ] && exec cat {root}/session-"$2".txt\n'
        "exit 1\n",
    )
    _executable(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        f'printf "%s\\n" "systemctl $*" >> {probes}\n'
        '[ "$1 $2" = "--user show-environment" ] || exit 1\n'
        f"[ -f {root}/manager.txt ] || exit 1\n"
        f"exec cat {root}/manager.txt\n",
    )
    _executable(
        bin_dir / "tmux",
        f'#!/bin/sh\nprintf "%s\\n" "tmux $*" >> {shlex.quote(str(tmp_path / "tmux.log"))}\n',
    )
    _executable(
        bin_dir / "ai",
        f"#!{sys.executable}\nimport sys\nfrom ai_cli.main import _handle_internal\n_handle_internal(sys.argv[2:])\n",
    )
    _executable(
        bin_dir / "codex",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(tmp_path / 'codex.jsonl')!r}, 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps([sys.argv[1:], os.environ.get('DISPLAY'), os.environ.get('XAUTHORITY')]) + '\\n')\n",
    )
    return bin_dir


def _launch(tmp_path: Path, tools: Path, desktop: Desktop, caller_env: dict[str, str] | None = None):
    """Start Codex fresh, then resumed, through the supervisor's ``run_agent``."""
    desktop.write()
    launch_log = tmp_path / "launch.log"
    launch_log.write_text("", encoding="utf-8")
    script = get_engine_script("cx", SESSION, f"cx-{SESSION}", "cx-myproject-", "myproject")
    start = script.index("    run_agent() {")
    end = script.index("    first_run=true", start)
    harness = tmp_path / "launch.sh"
    harness.write_text(
        script[start:end]
        + "\nagent_direnv_initialized=true\n"
        + "_reset_agent_terminal() { :; }\n"
        + f"_ai_launch_log={shlex.quote(str(launch_log))}\n"
        + "run_agent codex\n"
        + "run_agent codex resume --last\n"
        + 'printf "%s %s\\n" "${DISPLAY-unset}" "${XAUTHORITY-unset}"\n',
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key not in ("DISPLAY", "XAUTHORITY")}
    env["PATH"] = str(tools) + os.pathsep + os.defpath
    env.update(caller_env or {})
    completed = subprocess.run(
        ["bash", str(harness)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    calls = [json.loads(line) for line in (tmp_path / "codex.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [argv for argv, _display, _authority in calls] == [[], ["resume", "--last"]]
    seen = [(display, authority) for _argv, display, authority in calls]
    return seen, launch_log.read_text(encoding="utf-8"), completed.stdout


def _probes(tmp_path: Path) -> str:
    path = tmp_path / "probes.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_given_one_verified_local_desktop_when_codex_launches_and_resumes_then_both_get_its_display(
    tmp_path, tools, desktop
):
    desktop.add_session()
    seen, log, shell_after = _launch(tmp_path, tools, desktop)
    assert seen == [(":0", str(desktop.authority))] * 2
    assert "using DISPLAY :0" in log
    # Only the Codex process got the pair: the supervisor's shell and tmux never saw it.
    assert shell_after.strip() == "unset unset"
    assert not (tmp_path / "tmux.log").exists()


def test_given_an_x11_session_naming_the_same_display_when_codex_launches_then_it_gets_the_pair(
    tmp_path, tools, desktop
):
    desktop.add_session(Type="x11", Display=":0")
    seen, _log, _ = _launch(tmp_path, tools, desktop)
    assert seen == [(":0", str(desktop.authority))] * 2


def _refusal_cases():
    def zero(desktop):
        pass

    def two(desktop):
        desktop.add_session("2")
        desktop.add_session("3")

    def remote(desktop):
        desktop.add_session(Remote="yes")

    def foreign_user(desktop):
        desktop.add_session(User=str(UID + 1))

    def inactive(desktop):
        desktop.add_session(State="online")

    def text_console(desktop):
        desktop.add_session(Type="tty")

    def missing_authority(desktop):
        desktop.add_session()
        desktop.authority.unlink()

    def foreign_authority(desktop):
        desktop.add_session()
        # The root directory is root-owned on every Linux host; the test is skipped as root.
        assert desktop.manager is not None
        desktop.manager["XAUTHORITY"] = "/"

    def stale_authority(desktop):
        desktop.add_session(TimestampMonotonic=str(_now_monotonic_us()))
        an_hour_ago = time.time() - 3600
        os.utime(desktop.authority, (an_hour_ago, an_hour_ago))

    def no_manager_pair(desktop):
        desktop.add_session()
        desktop.manager = {"LANG": "C.UTF-8"}

    def manager_unreachable(desktop):
        desktop.add_session()
        desktop.manager = None

    def mismatched_x11_display(desktop):
        desktop.add_session(Type="x11", Display=":1")

    return [
        pytest.param(zero, "0 active local graphical sessions", id="zero-sessions"),
        pytest.param(two, "2 active local graphical sessions", id="two-sessions"),
        pytest.param(remote, "0 active local graphical sessions", id="remote-session"),
        pytest.param(foreign_user, "0 active local graphical sessions", id="other-user-session"),
        pytest.param(inactive, "0 active local graphical sessions", id="inactive-session"),
        pytest.param(text_console, "0 active local graphical sessions", id="non-graphical-session"),
        pytest.param(missing_authority, "is missing", id="missing-authority"),
        pytest.param(foreign_authority, "is not owned by this user", id="foreign-authority"),
        pytest.param(stale_authority, "older than session", id="stale-authority"),
        pytest.param(no_manager_pair, "no DISPLAY and XAUTHORITY pair", id="manager-without-pair"),
        pytest.param(manager_unreachable, "show-environment failed", id="manager-unreachable"),
        pytest.param(mismatched_x11_display, "does not match", id="x11-display-mismatch"),
    ]


@pytest.mark.parametrize(("arrange", "reason"), _refusal_cases())
def test_given_an_unverifiable_desktop_when_codex_launches_then_no_display_is_added_and_the_log_says_why(
    tmp_path, tools, desktop, arrange, reason
):
    if os.getuid() == 0:
        pytest.skip("ownership checks cannot be exercised as root")
    arrange(desktop)
    seen, log, _ = _launch(tmp_path, tools, desktop)
    assert seen == [(None, None)] * 2
    assert reason in log
    assert "[launch] display:" in log


def test_given_caller_display_and_authority_when_codex_launches_then_they_are_kept_and_nothing_is_probed(
    tmp_path, tools, desktop
):
    desktop.add_session()
    caller = {"DISPLAY": ":7", "XAUTHORITY": str(tmp_path / "caller-auth")}
    seen, log, _ = _launch(tmp_path, tools, desktop, caller)
    assert seen == [(":7", caller["XAUTHORITY"])] * 2
    assert _probes(tmp_path) == ""
    assert "[launch] display:" not in log


@pytest.mark.parametrize("variable", ["DISPLAY", "XAUTHORITY"])
def test_given_only_one_caller_variable_when_codex_launches_then_it_is_kept_alone_and_nothing_is_probed(
    tmp_path, tools, desktop, variable
):
    desktop.add_session()
    value = ":7" if variable == "DISPLAY" else str(tmp_path / "caller-auth")
    seen, log, _ = _launch(tmp_path, tools, desktop, {variable: value})
    expected = (value, None) if variable == "DISPLAY" else (None, value)
    assert seen == [expected] * 2
    assert _probes(tmp_path) == ""
    assert f"{variable} is set by the caller" in log


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_given_a_non_linux_host_when_resolving_the_codex_display_then_nothing_is_probed_or_logged(
    tmp_path, tools, desktop, monkeypatch, capsys, platform
):
    desktop.add_session()
    desktop.write()
    launch_log = tmp_path / "launch.log"
    launch_log.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.defpath)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setenv(LAUNCH_LOG_ENV, str(launch_log))
    monkeypatch.setattr(sys, "platform", platform)
    # Every record an `ai internal` call emits reaches the launch log, and the patched
    # platform is a machine profile this config has never seen, which the config load
    # registers and announces. Register it first so the log can only show display output.
    _config.load_config()
    with pytest.raises(SystemExit) as exited:
        _handle_internal(["resolve-codex-display"])
    assert exited.value.code == 0
    assert capsys.readouterr().out == ""
    assert _probes(tmp_path) == ""
    assert launch_log.read_text(encoding="utf-8") == ""
