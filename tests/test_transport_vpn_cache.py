"""Exercise shared VPN detection with real subprocesses and transport polling."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_cli import transport


@pytest.fixture
def vpn_probe(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("The stand-in executable uses a POSIX shebang")
    counter = tmp_path / "invocations"
    response = tmp_path / "response"
    response.write_text("Connected\n")
    binary = tmp_path / "bin" / "mullvad"
    binary.parent.mkdir()
    binary.write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as stream:\n"
        "    stream.write('probe\\n')\n"
        "time.sleep(0.2)\n"
        f"print(Path({str(response)!r}).read_text())\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    state_dir = transport.get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    return counter, response, state_dir / "vpn-state.json"


@pytest.mark.parametrize(("ttl", "expected"), [(2, 1), (0, 5)], ids=["shared", "bypassed"])
def test_given_concurrent_sessions_when_vpn_is_polled_then_one_probe_is_shared_until_expiry(
    vpn_probe, tmp_path, ttl, expected
):
    counter, _, cache = vpn_probe
    worker = """\
import sys, time
from pathlib import Path
from ai_cli.transport import _is_vpn_active
Path(sys.argv[1]).touch()
deadline = time.monotonic() + 10
while not Path(sys.argv[2]).exists():
    assert time.monotonic() < deadline, 'start barrier timed out'
    time.sleep(0.01)
ttl = float(sys.argv[3])
print(_is_vpn_active(ttl) if ttl else _is_vpn_active())
"""
    start = tmp_path / "start"
    ready = [tmp_path / f"ready-{i}" for i in range(5)]
    workers = []
    try:
        for path in ready:
            workers.append(
                subprocess.Popen(
                    [sys.executable, "-c", worker, str(path), str(start), str(ttl)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 15
        while not all(path.exists() for path in ready):
            assert time.monotonic() < deadline, "workers never reached the barrier"
            time.sleep(0.01)
        start.touch()
        for proc in workers:
            stdout, stderr = proc.communicate(timeout=15)
            assert proc.returncode == 0, stderr
            assert stdout.strip() == "True"
    finally:
        for proc in workers:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
    assert len(counter.read_text().splitlines()) == expected
    if ttl:
        # Wait out the configured interval, then use the same real entry point.
        expires = json.loads(cache.read_text())["checked_at"] + ttl
        time.sleep(max(0, expires - time.time()) + 0.02)
        assert transport._is_vpn_active(ttl) is True
        assert len(counter.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        "[]",
        "null",
        "{}",
        '{"active": "false", "checked_at": 0}',
        '{"active": false, "checked_at": "invalid"}',
        '{"active": false, "checked_at": 0}',
        '{"active": false, "checked_at": 1e300}',
        '{"active": false, "checked_at": ' + "9" * 400 + "}",
    ],
)
def test_given_invalid_or_stale_cache_when_vpn_is_polled_then_current_state_replaces_it(vpn_probe, contents):
    counter, _, cache = vpn_probe
    cache.write_text(contents)
    assert transport._is_vpn_active(3) is True
    assert counter.read_text().splitlines() == ["probe"]
    state = json.loads(cache.read_text())
    assert state["active"] is True
    assert 0 <= time.time() - state["checked_at"] < 3


def test_given_unreadable_cache_when_vpn_is_polled_then_detection_refreshes_it(vpn_probe):
    counter, _, cache = vpn_probe
    cache.write_text('{"active": false, "checked_at": 0}')
    real_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == cache:
            raise PermissionError("cache cannot be read")
        return real_read(path, *args, **kwargs)

    with patch.object(Path, "read_text", read):
        assert transport._is_vpn_active(3) is True
    assert counter.read_text().splitlines() == ["probe"]
    assert json.loads(cache.read_text())["active"] is True


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("failure", ["lock", "temporary-file", "replace"])
def test_given_unwritable_cache_when_vpn_is_polled_then_real_detection_result_survives(vpn_probe, active, failure):
    counter, response, cache = vpn_probe
    response.write_text("Connected" if active else "Disconnected")
    target = {
        "lock": "ai_cli.transport.portalocker.lock",
        "temporary-file": "ai_cli.transport.tempfile.NamedTemporaryFile",
        "replace": "ai_cli.transport.Path.replace",
    }[failure]
    with patch(target, side_effect=PermissionError("cache cannot be written")):
        assert transport._is_vpn_active(3) is active
    assert counter.read_text().splitlines() == ["probe"]
    assert not cache.exists()
    assert not list(cache.parent.glob(".vpn-state-*.tmp"))


def test_given_expired_cache_when_refreshed_then_replacement_is_atomic_in_the_same_directory(vpn_probe):
    _, _, cache = vpn_probe
    original = '{"active": false, "checked_at": 0}'
    cache.write_text(original)
    real_replace = Path.replace
    replacements = []

    def replace(source, destination):
        assert source.parent == cache.parent
        assert destination == cache
        assert cache.read_text() == original
        assert json.loads(source.read_text())["active"] is True
        replacements.append(source)
        return real_replace(source, destination)

    with patch.object(Path, "replace", replace):
        assert transport._is_vpn_active(3) is True
    assert len(replacements) == 1
    assert not replacements[0].exists()


@pytest.mark.parametrize("error", [None, subprocess.TimeoutExpired("mullvad", 2), OSError("probe failed")])
def test_given_inactive_or_failed_detection_when_polled_again_then_false_answer_is_shared(tmp_path, error):
    with (
        patch("ai_cli.transport.get_xdg_state_home", return_value=tmp_path),
        patch("ai_cli.transport.shutil.which", return_value="mullvad"),
        patch(
            "ai_cli.transport.subprocess.run", return_value=MagicMock(stdout="Disconnected"), side_effect=error
        ) as probe,
    ):
        assert transport._is_vpn_active(3) is False
        assert transport._is_vpn_active(3) is False
    assert probe.call_count == 1


@pytest.mark.parametrize(
    ("output", "active"),
    [
        ("utun3: flags=8051\n  inet 192.0.2.1\n", True),
        ("tun0: flags=8051\n  inet 192.0.2.1\n", True),
        ("utun3: flags=8051\n  inet6 fe80::1\n", False),
        ("en0: flags=8863\n  inet 192.0.2.1\n", False),
    ],
)
def test_given_no_mullvad_when_cached_detection_runs_then_ifconfig_rule_is_preserved(vpn_probe, output, active):
    counter, response, cache = vpn_probe
    executable = response.parent / "bin" / "ifconfig"
    executable.write_text((executable.parent / "mullvad").read_text())
    executable.chmod(0o755)
    response.write_text(output)
    with patch("ai_cli.transport.shutil.which", return_value=None):
        assert transport._is_vpn_active(3) is active
        assert transport._is_vpn_active(3) is active
    assert counter.read_text().splitlines() == ["probe"]
    assert json.loads(cache.read_text())["active"] is active


def test_given_slow_vpn_probe_when_transport_starts_and_polls_then_event_loop_keeps_ticking(vpn_probe):
    counter, response, cache = vpn_probe
    response.write_text("Disconnected")
    # An expired startup answer must not override the real current state.
    cache.write_text(json.dumps({"active": True, "checked_at": time.time() - 10}))
    mosh = MagicMock(pid=123, returncode=None)
    mosh.poll.return_value = None
    ssh = MagicMock(pid=456, returncode=0)
    ssh.poll.return_value = 0
    real_popen = subprocess.Popen
    launches = []

    def launch(args, *pargs, **kwargs):
        if args == ["test-mosh"]:
            launches.append("mosh")
            response.write_text("Connected")
            return mosh
        if args == ["test-ssh"]:
            launches.append("ssh")
            return ssh
        return real_popen(args, *pargs, **kwargs)

    nc = MagicMock(nc=None, connect=AsyncMock(), close=AsyncMock())
    ticks = {1: 0, 2: 0}

    async def run():
        task = asyncio.create_task(
            transport._run_transport_loop(
                ["test-ssh"],
                ["test-mosh"],
                [sys.executable, "-c", "pass"],
                "test-session",
                {"remote": {"vpn_poll_interval": 0.1}},
            )
        )
        while not task.done():
            count = len(counter.read_text().splitlines()) if counter.exists() else 0
            if count in ticks:
                ticks[count] += 1
            await asyncio.sleep(0.01)
        await task

    with (
        patch("ai_cli.messaging.NATSClient", return_value=nc),
        patch("ai_cli.transport.subprocess.Popen", side_effect=launch),
        patch("ai_cli.transport._monotonic", side_effect=[0, 10, 0, 10]),
    ):
        asyncio.run(asyncio.wait_for(run(), timeout=15))
    assert launches == ["mosh", "ssh"]
    mosh.terminate.assert_called_once()
    assert counter.read_text().splitlines() == ["probe", "probe"]
    assert ticks[1] >= 5
    assert ticks[2] >= 5
