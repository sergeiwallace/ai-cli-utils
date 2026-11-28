"""No launcher wait can hold a launch past its bound, and each wait's retry stays on the one policy.

The wake sidecar is optional, so a hung one may cost a launch only its policy's total budget,
however many attempts the policy allows. The Dolt supervisor ensure ran with no timeout at all.
The policy-sync fetch of ``sync-names`` fetched once with only a first attempt's budget. And the
launch-time reinstall reinstalls just this package, while an ``ai update --force`` typed by hand
keeps uv's full reinstall, which also repairs the dependencies.

Every hang here is a real process that does not answer: a stub script, or a git remote whose
``upload-pack`` sleeps on cue.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli
from name_fleet import build_fleet, git, policy_of

from ai_cli import main, retry
from ai_cli import name_registry as reg
from ai_cli.main import _auto_update_if_stale, _do_update_or_deploy, _ensure_dolt_server


def _write_config(tmp_path: Path, monkeypatch, text: str) -> None:
    base = tmp_path / "user-config"
    (base / "ai-cli-utils").mkdir(parents=True, exist_ok=True)
    (base / "ai-cli-utils" / "config.toml").write_text(textwrap.dedent(text), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    monkeypatch.setenv("APPDATA", str(base))


_HANGS = """\
import pathlib, time
counter = pathlib.Path(__file__).with_name("calls")
counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
time.sleep(30)
"""


def _hanging_script(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text(_HANGS, encoding="utf-8")
    return script


def _calls(script: Path) -> int:
    counter = script.with_name("calls")
    return int(counter.read_text()) if counter.exists() else 0


def test_given_a_hung_sidecar_and_a_total_budget_when_launching_then_the_ensure_costs_no_more_than_the_budget(
    tmp_path, monkeypatch
):
    script = _hanging_script(tmp_path / "myhub" / "scripts", "wake_sidecar_cli.py")
    _write_config(
        tmp_path,
        monkeypatch,
        """\
        [retry.wake_sidecar]
        attempts = 10
        timeout_seconds = 1
        max_timeout_seconds = 1
        delay_seconds = 0
        max_total_seconds = 2.5
        """,
    )
    with ExitStack() as stack:
        stack.enter_context(patch("ai_cli.main._ensure_dolt_server"))
        stack.enter_context(patch("ai_cli.main._tunnel._ensure_nats_tunnel"))
        launched = stack.enter_context(patch("ai_cli.main._do_session_launch"))
        stack.enter_context(patch("ai_cli.process_manager._start_stale_session_reaper_for_launch"))
        stack.enter_context(patch("ai_cli.main._wake_sidecar_script", return_value=script))
        stack.enter_context(patch("ai_cli.main._sidecar_unit_manager_available", return_value=True))
        started = time.monotonic()
        code, _out, err = run_cli(["ai", "c", "session-1"])
        elapsed = time.monotonic() - started

    assert code == 0, err
    assert elapsed < 2.5 + 2.0, f"the ensure held the launch {elapsed:.1f} s"
    assert _calls(script) <= 3
    [line] = [line for line in err.splitlines() if "wake sidecar failed" in line]
    assert "2.5 s total" in line
    assert line.rstrip().endswith("launch continues")
    launched.assert_called_once()


def test_given_a_hung_dolt_supervisor_when_launching_then_the_ensure_is_bounded_and_the_launch_continues(
    tmp_path, monkeypatch
):
    script = _hanging_script(tmp_path / "hub" / "scripts", "dolt_server.py")
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, monkeypatch, "[retry.dolt_server]\ntimeout_seconds = 1\nmax_timeout_seconds = 1\n")
    errors = []

    with (
        patch("ai_cli.main.load_config", return_value={"dolt_server": {"script_path": str(script)}}),
        patch("ai_cli.main._out.error", side_effect=lambda *args, **_kwargs: errors.append(args)),
    ):
        started = time.monotonic()
        _ensure_dolt_server()
        elapsed = time.monotonic() - started

    assert elapsed < 1 + 2.0, f"the ensure held the launch {elapsed:.1f} s"
    assert _calls(script) == 1, "a supervisor ensure is asked once"
    [(_tag, _op, message)] = errors
    assert "did not finish within 1 s" in message
    assert message.endswith("launch continues")


_UPLOAD_PACK_HANGS_ONCE = """\
import pathlib, time
counter = pathlib.Path(__file__).with_name("upload-pack-calls")
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
if n == 1:
    time.sleep(8)
"""


def test_given_a_policy_fetch_that_times_out_once_when_syncing_names_then_it_is_retried_with_a_longer_timeout(
    tmp_path, monkeypatch
):
    fleet = build_fleet(tmp_path, monkeypatch, backend="git")
    stub = tmp_path / "upload-pack" / "upload_pack.py"
    stub.parent.mkdir()
    stub.write_text(_UPLOAD_PACK_HANGS_ONCE, encoding="utf-8")
    python = Path(sys.executable).as_posix()
    git(fleet.repo, "config", "remote.origin.uploadpack", f'"{python}" "{stub.as_posix()}" && git upload-pack')
    _write_config(
        tmp_path,
        monkeypatch,
        "[retry.registry_fetch]\nattempts = 2\ntimeout_seconds = 3\ngrowth = 20\nmax_timeout_seconds = 60\n"
        "delay_seconds = 0\n",
    )
    policy_fetches: list[float | None] = []
    real_run = subprocess.run

    def recording_run(command, *args, **kwargs):
        if command[:1] == ["git"] and command[-4:] == ["fetch", "--no-tags", "--quiet", "origin"]:
            policy_fetches.append(kwargs.get("timeout"))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(reg.subprocess, "run", recording_run)

    reg.sync_names(fleet.registry, local_policy=policy_of(fleet.repo), dry_run=True)

    assert len(policy_fetches) == 2
    assert policy_fetches[1] > policy_fetches[0] == 3


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "myproject"
    (root / "src" / "myproject").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "myproject"\nversion = "0.1.0"\n')
    (root / "src" / "myproject" / "__init__.py").write_text("")
    return root


def _uv_installs(checkout: Path, state: Path) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(cmd, **_kwargs):
        commands.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("ai_cli.config.get_xdg_state_home", return_value=state),
        patch("subprocess.run", side_effect=run),
        pytest.raises(SystemExit),
    ):
        _do_update_or_deploy(force_reinstall=True, config={"deploy": {"project_path": str(checkout)}}, quiet=True)
    return [c for c in commands if Path(c[0]).stem == "uv" and "install" in c]


def test_given_update_force_typed_by_hand_when_it_installs_then_uv_reinstalls_every_package(checkout, tmp_path):
    [install] = _uv_installs(checkout, tmp_path / "state")

    assert "--reinstall" in install
    assert "--reinstall-package" not in install


def test_given_a_stale_install_when_a_launch_updates_it_then_the_child_update_reinstalls_only_the_package(
    checkout, tmp_path
):
    state = tmp_path / "state"
    state.mkdir()
    children: list[list[str]] = []

    def run(cmd, **_kwargs):
        children.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("ai_cli.main._find_aicli_project_path", return_value=checkout),
        patch("ai_cli.config.get_xdg_state_home", return_value=state),
        patch("shutil.which", return_value="/usr/bin/ai"),
        patch("subprocess.run", side_effect=run),
    ):
        _auto_update_if_stale({"deploy": {"project_path": str(checkout)}})

    [update] = [c for c in children if "update" in c]
    assert "--force" in update
    assert "--package-only" in update


def test_given_a_stale_install_when_a_launch_updates_it_then_the_child_is_this_running_package(checkout, tmp_path):
    """An `ai` found on PATH may be an older install that rejects --package-only; this interpreter is not."""
    state = tmp_path / "state"
    state.mkdir()
    children: list[list[str]] = []

    def run(cmd, **_kwargs):
        children.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("ai_cli.main._find_aicli_project_path", return_value=checkout),
        patch("ai_cli.config.get_xdg_state_home", return_value=state),
        patch("shutil.which", return_value="/usr/bin/ai"),
        patch("subprocess.run", side_effect=run),
    ):
        _auto_update_if_stale({"deploy": {"project_path": str(checkout)}})

    [update] = [c for c in children if "update" in c]
    assert update[:4] == [sys.executable, "-P", "-m", "ai_cli.main"]
    assert update[4:7] == ["update", "--force", "--package-only"]


def _sites() -> list[str]:
    return [name for name, value in retry.shipped_table().items() if isinstance(value, dict)]


def _worst_case(policy: retry.RetryPolicy) -> float:
    timeouts = sum(policy.timeout_for(n) for n in range(1, policy.attempts + 1))
    delays = sum(policy.delay_after(n, rand=lambda: 0.0) for n in range(1, policy.attempts))
    return timeouts + delays


def test_given_the_shipped_policies_when_read_then_timeouts_never_shrink_and_every_attempt_fits_the_total(
    tmp_path, monkeypatch, capsys
):
    _write_config(tmp_path, monkeypatch, "")

    for site in _sites():
        policy = retry.policy(site)
        timeouts = [policy.timeout_for(n) for n in range(1, policy.attempts + 1)]
        assert timeouts == sorted(timeouts), site
        assert _worst_case(policy) <= policy.max_total_seconds, site
    assert capsys.readouterr().err == "", "no shipped policy contradicts itself"


@pytest.mark.parametrize(
    ("table", "keys"),
    [
        ("attempts = 3\ntimeout_seconds = 5\ngrowth = 2\nmax_timeout_seconds = 20\nmax_total_seconds = 12\n",
         ["attempts", "max_total_seconds"]),
        ("timeout_seconds = 12\nmax_timeout_seconds = 12\nmax_total_seconds = 12\n",
         ["timeout_seconds", "max_total_seconds"]),
    ],
)  # fmt: skip
def test_given_a_total_that_cannot_admit_the_attempts_when_a_site_reads_its_policy_then_it_warns_naming_site_and_keys(
    tmp_path, monkeypatch, capsys, table, keys
):
    _write_config(tmp_path, monkeypatch, f"[retry.wake_sidecar]\n{table}")

    retry.policy("wake_sidecar")

    err = capsys.readouterr().err
    assert "retry.wake_sidecar" in err
    assert all(key in err for key in keys), err


def test_given_a_delay_that_overshoots_the_total_when_retrying_then_it_stops_before_calling_with_no_time_left(
    tmp_path, monkeypatch
):
    _write_config(
        tmp_path,
        monkeypatch,
        "[retry.wake_sidecar]\nattempts = 3\ntimeout_seconds = 1\nmax_timeout_seconds = 1\n"
        "delay_seconds = 0.1\njitter = 0\nmax_total_seconds = 2\n",
    )
    timeouts: list[float] = []

    def call(attempt: retry.Attempt) -> None:
        timeouts.append(attempt.timeout)
        raise TimeoutError("no answer")

    with pytest.raises(retry.RetryExhaustedError) as exhausted:
        retry.run(
            "wake_sidecar", call, transient=lambda exc: isinstance(exc, TimeoutError), sleep=lambda _s: time.sleep(2.2)
        )

    assert timeouts == [1]
    assert exhausted.value.attempts == 1
    assert str(exhausted.value.last) == "no answer"


@pytest.mark.skipif(sys.platform == "win32", reason="the session wake watcher is POSIX-only")
def test_given_a_hung_sidecar_when_the_ensure_and_the_show_lookup_both_run_then_together_they_stay_within_twice_the_total(
    tmp_path, monkeypatch
):
    script = _hanging_script(tmp_path / "myhub" / "scripts", "wake_sidecar_cli.py")
    _write_config(
        tmp_path,
        monkeypatch,
        "[retry.wake_sidecar]\nattempts = 2\ntimeout_seconds = 1\ngrowth = 1.5\nmax_timeout_seconds = 1.5\n"
        "delay_seconds = 0.2\nmax_total_seconds = 3\n",
    )
    bound = 2 * retry.policy("wake_sidecar").max_total_seconds
    errors, warnings = [], []

    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=script),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=True),
        patch("ai_cli.main._out.error", side_effect=lambda *args, **_kwargs: errors.append(args)),
        patch("ai_cli.main._out.warning", side_effect=lambda *args, **_kwargs: warnings.append(args)),
    ):
        started = time.monotonic()
        main._ensure_wake_sidecar({})
        loop = main._session_wake_watch_loop({}, "myproject-tst-1")
        elapsed = time.monotonic() - started

    assert elapsed < bound + 2.0, f"ensure and show held the launch {elapsed:.1f} s"
    assert _calls(script) == 4, "each of the two lookups made both of its attempts"
    assert loop is None
    assert any("wake sidecar failed" in message for _tag, _op, message in errors)
    assert any("wake watcher unavailable" in message for _tag, _op, message in warnings)
