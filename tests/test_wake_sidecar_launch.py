"""Launcher coverage for the optional machine wake sidecar."""

import json
import subprocess
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import run_cli


@pytest.fixture
def local_launch():
    with ExitStack() as stack:
        stack.enter_context(patch("ai_cli.main._ensure_dolt_server"))
        stack.enter_context(patch("ai_cli.main._tunnel._ensure_nats_tunnel"))
        launch = stack.enter_context(patch("ai_cli.main._do_session_launch"))
        stack.enter_context(patch("ai_cli.process_manager._start_stale_session_reaper_for_launch"))
        yield launch


@pytest.fixture
def wake_script(tmp_path):
    script = tmp_path / "myhub" / "scripts" / "wake_sidecar_cli.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    return script


def _successful_result(action: str, reason: str = "already active"):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"action": action, "reason": reason, "unit": "example-sidecar.service"}),
        stderr="",
    )


def test_given_stopped_sidecar_when_two_local_launches_then_starts_exactly_once(local_launch, wake_script):
    actions = iter([_successful_result("started", "unit started"), _successful_result("none")])

    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=wake_script),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=True),
        patch("ai_cli.main.subprocess.run", side_effect=lambda *_args, **_kwargs: next(actions)) as run,
    ):
        first = run_cli(["ai", "c", "session-1"])
        second = run_cli(["ai", "c", "session-2"])

    assert first[0] == second[0] == 0
    assert run.call_count == 2
    assert run.call_args.args[0] == [
        sys.executable,
        str(wake_script),
        "ensure",
        "--json",
    ]
    assert run.call_args.kwargs["timeout"] == 3
    assert (first[2] + second[2]).count("wake sidecar started") == 1
    assert local_launch.call_count == 2


def test_given_sidecar_failure_when_local_launch_then_launch_succeeds(local_launch, wake_script):
    failed = SimpleNamespace(
        returncode=1,
        stdout=json.dumps({"action": "failed", "reason": "unit unavailable", "unit": "example-sidecar.service"}),
        stderr="",
    )
    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=wake_script),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=True),
        patch("ai_cli.main.subprocess.run", return_value=failed),
    ):
        code, _stdout, stderr = run_cli(["ai", "g", "session-1"])

    assert code == 0
    assert "wake sidecar failed: unit unavailable" in stderr
    local_launch.assert_called_once()


def test_given_sidecar_timeout_when_local_launch_then_launch_succeeds(local_launch, wake_script):
    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=wake_script),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=True),
        patch(
            "ai_cli.main.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["wake-sidecar", "ensure"], timeout=3),
        ),
    ):
        code, _stdout, stderr = run_cli(["ai", "p", "session-1"])

    assert code == 0
    assert "wake sidecar failed: ensure timed out" in stderr
    local_launch.assert_called_once()


def test_given_remote_launch_when_dispatched_then_does_not_call_sidecar(local_launch):
    with (
        patch("ai_cli.main._sync_launch_client", return_value=None),
        patch("ai_cli.main._ensure_wake_sidecar") as ensure,
    ):
        code, _stdout, _stderr = run_cli(["ai", "cx", "session-1", "-R"])

    assert code == 0
    ensure.assert_not_called()
    local_launch.assert_called_once()


def test_given_unsupported_platform_when_local_launch_then_does_not_call_sidecar(local_launch, wake_script):
    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=wake_script),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=False),
        patch("ai_cli.main.subprocess.run") as run,
    ):
        code, _stdout, _stderr = run_cli(["ai", "c", "session-1"])

    assert code == 0
    run.assert_not_called()


def test_given_enabled_local_sidecar_without_implementation_when_launching_then_warns_once(local_launch):
    with (
        patch("ai_cli.main._wake_sidecar_script", return_value=None),
        patch("ai_cli.main._sidecar_unit_manager_available", return_value=True),
    ):
        code, _stdout, stderr = run_cli(
            ["ai", "c", "session-1"],
            config={"sidecar": {"enabled": True, "mode": "machine", "boot": False}},
        )

    assert code == 0
    assert stderr.count("no wake sidecar implementation was found") == 1
