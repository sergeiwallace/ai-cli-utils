"""`ai quota publish`: publish the statusline's native quota reading, never scrape.

Driven through the real CLI entry point with the state file in tmp_path and a fake NATS
client that records every publish and KV write.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state(now: float, *, seven_day_age: float = 60.0, five_hour_age: float = 60.0, **overrides) -> dict:
    state = {
        "seven_day_pct": 42.0,
        "seven_day_reset": int(now + 3 * 86400),
        "seven_day_updated_at": _iso(now - seven_day_age),
        "seven_day_session_id": "session-1",
        "five_hour_pct": 12.0,
        "five_hour_reset": int(now + 3600),
        "five_hour_updated_at": _iso(now - five_hour_age),
        "five_hour_session_id": "session-1",
        "updated_at": _iso(now - min(seven_day_age, five_hour_age)),
        "source": "statusline_rate_limits",
    }
    state.update(overrides)
    return state


class _FakeNats:
    """Records what a publish would send: subjects with payloads, and KV puts."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.kv: dict[str, dict] = {}
        self.client = MagicMock()
        self.client.nc = MagicMock()
        self.client.js = MagicMock()
        kv = MagicMock()

        async def _connect():
            pass

        async def _close():
            pass

        async def _publish(subject, payload):
            self.published.append((subject, payload))

        async def _put(key, value):
            self.kv[key] = json.loads(value)

        async def _key_value(bucket):
            assert bucket == "hw_state"
            return kv

        kv.put = _put
        self.client.connect = _connect
        self.client.close = _close
        self.client.publish = _publish
        self.client.js.key_value = _key_value

    @property
    def subjects(self) -> list[str]:
        return [subject for subject, _ in self.published]


@pytest.fixture
def nats():
    fake = _FakeNats()
    with patch("ai_cli.messaging.NATSClient", return_value=fake.client) as factory:
        fake.factory = factory
        yield fake


@pytest.fixture
def no_process():
    with patch("subprocess.Popen") as popen, patch("subprocess.run") as run:
        yield popen, run
    popen.assert_not_called()
    run.assert_not_called()


def _publish(state_file, env=None):
    config = {"quota": {"state_file": str(state_file)}}
    return run_cli(["ai", "quota", "publish"], config=config, env={"AI_HOST": "myhost", **(env or {})})


def test_given_fresh_state_file_when_publish_then_sends_snapshot_usage_event_and_kv(tmp_path, nats, no_process):
    now = time.time()
    state = _state(now)
    state_file = tmp_path / "quota.json"
    state_file.write_text(json.dumps(state))

    code, out, _ = _publish(state_file)

    assert code == 0
    assert nats.subjects == ["quota.snapshot", "hw.events.usage.claude.snapshot"]
    snapshot = nats.published[0][1]
    assert snapshot["usage_percent"] == 42.0
    assert snapshot["session_pct"] == 12.0
    assert snapshot["reset_at"] == _iso(state["seven_day_reset"])
    assert snapshot["weekly_sonnet_pct"] is None
    usage_event = nats.published[1][1]
    assert usage_event["machine"] == "myhost"
    assert usage_event["used_pct"] == 42.0
    assert nats.kv["quota.claude.current.myhost"]["usage_percent"] == 42.0
    assert "42.0%" in out


def test_given_stale_five_hour_window_when_publish_then_sends_seven_day_without_session_pct(tmp_path, nats, no_process):
    now = time.time()
    state_file = tmp_path / "quota.json"
    state_file.write_text(json.dumps(_state(now, five_hour_age=7200)))

    code, _, _ = _publish(state_file)

    assert code == 0
    assert nats.published[0][1]["usage_percent"] == 42.0
    assert nats.published[0][1]["session_pct"] is None


@pytest.mark.parametrize(
    "case",
    [
        "stale",
        "reset_passed",
        "reset_beyond_window",
        "pct_null",
        "pct_out_of_range",
        "no_observed_at",
        "not_an_object",
        "malformed",
        "missing",
    ],
)
def test_given_no_current_seven_day_reading_when_publish_then_exits_0_and_sends_nothing(
    tmp_path, nats, no_process, case
):
    now = time.time()
    state_file = tmp_path / "quota.json"
    contents = {
        "stale": json.dumps(_state(now, seven_day_age=1801)),
        "reset_passed": json.dumps(_state(now, seven_day_reset=int(now - 60))),
        "reset_beyond_window": json.dumps(_state(now, seven_day_reset=10**20)),
        "pct_null": json.dumps(_state(now, seven_day_pct=None)),
        "pct_out_of_range": json.dumps(_state(now, seven_day_pct=140.0)),
        "no_observed_at": json.dumps(_state(now, seven_day_updated_at=None)),
        "not_an_object": json.dumps([42.0]),
        "malformed": "{not json",
        "missing": None,
    }[case]
    if contents is not None:
        state_file.write_text(contents)

    code, out, _ = _publish(state_file)

    assert code == 0
    nats.factory.assert_not_called()
    assert nats.published == []
    assert nats.kv == {}
    assert "quota publish:" in out


def test_given_configured_max_age_when_reading_older_than_default_then_publishes(tmp_path, nats, no_process):
    now = time.time()
    state_file = tmp_path / "quota.json"
    state_file.write_text(json.dumps(_state(now, seven_day_age=3000)))
    config = {"quota": {"state_file": str(state_file), "max_age_seconds": 3600}}

    code, _, _ = run_cli(["ai", "quota", "publish"], config=config, env={"AI_HOST": "myhost"})

    assert code == 0
    assert nats.subjects == ["quota.snapshot", "hw.events.usage.claude.snapshot"]


def test_given_no_config_when_publish_then_reads_default_state_file_under_home(tmp_path, monkeypatch, nats, no_process):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    state_file = tmp_path / ".claude" / "state" / "quota.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps(_state(time.time())))

    code, _, _ = run_cli(["ai", "quota", "publish"], config={}, env={"AI_HOST": "myhost"})

    assert code == 0
    assert "quota.snapshot" in nats.subjects


def test_given_retired_scrape_command_when_invoked_then_cli_rejects_it(no_process):
    code, _, err = run_cli(["ai", "quota", "scrape"])

    assert code != 0
    assert "No such command 'scrape'" in err


# -- the real producer -------------------------------------------------------------------------

_STATUSLINE = Path(__file__).parent.parent / "src/ai_cli/data/statusline-command.sh"


def _statusline_writes(tmp_path: Path, rate_limits: dict) -> Path:
    """Run the shipped statusline on one Claude Code payload; return the state file it wrote."""
    home, tmp, bin_dir = tmp_path / "home", tmp_path / "tmp", tmp_path / "bin"
    for directory in (home, tmp, bin_dir):
        directory.mkdir(exist_ok=True)
    fake_ai = bin_dir / "ai"
    fake_ai.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_ai.chmod(0o755)
    payload = {
        "model": "claude-sonnet-4-6",
        "context_window": {"used_percentage": 10, "total_input_tokens": 100, "total_output_tokens": 10},
        "workspace": {"project_dir": str(tmp_path)},
        "rate_limits": rate_limits,
    }
    env = {
        **os.environ,
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TMUX": "",
        "COLUMNS": "200",
        "GIT_BRANCH_CACHE": "main",
        "CC_BILLING_MODE": "subscription",
    }
    done = subprocess.run(
        ["bash", str(_STATUSLINE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return home / ".claude" / "state" / "quota.json"


_needs_posix_statusline = pytest.mark.skipif(
    shutil.which("jq") is None or sys.platform == "win32",
    reason="statusline-command.sh needs bash and jq",
)


@_needs_posix_statusline
def test_given_the_real_statusline_wrote_a_fresh_reading_when_publish_then_it_reaches_all_three_targets(tmp_path, nats):
    now = time.time()
    state_file = _statusline_writes(
        tmp_path,
        {
            "seven_day": {"used_percentage": 42, "resets_at": int(now + 3 * 86400)},
            "five_hour": {"used_percentage": 12, "resets_at": int(now + 3600)},
        },
    )

    code, out, _ = _publish(state_file)

    assert code == 0, out
    assert nats.subjects == ["quota.snapshot", "hw.events.usage.claude.snapshot"]
    assert nats.published[0][1]["usage_percent"] == 42.0
    assert nats.published[0][1]["session_pct"] == 12.0
    assert nats.kv["quota.claude.current.myhost"]["usage_percent"] == 42.0


@_needs_posix_statusline
def test_given_a_payload_without_the_five_hour_window_when_the_statusline_writes_then_that_window_keeps_its_own_time(
    tmp_path,
):
    now = time.time()
    first = {
        "seven_day": {"used_percentage": 40, "resets_at": int(now + 3 * 86400)},
        "five_hour": {"used_percentage": 10, "resets_at": int(now + 3600)},
    }
    state_file = _statusline_writes(tmp_path, first)
    old = json.loads(state_file.read_text())
    old["five_hour_updated_at"] = "2000-01-01T00:00:00Z"
    state_file.write_text(json.dumps(old))

    _statusline_writes(tmp_path, {"seven_day": {"used_percentage": 41, "resets_at": int(now + 3 * 86400)}})
    state = json.loads(state_file.read_text())

    assert state["seven_day_pct"] == 41
    assert state["seven_day_updated_at"] != "2000-01-01T00:00:00Z"
    assert state["five_hour_pct"] == 10, "an absent window keeps its last reading"
    assert state["five_hour_updated_at"] == "2000-01-01T00:00:00Z", "and is not relabelled fresh"
