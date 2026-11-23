"""Tests for Antigravity (agy) quota ingestion, freshness/unknown preservation, NATS sync, and statuslines."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_cli import agy_quota, quota
from ai_cli import quota_db as qdb
from ai_cli.main import _cli_group

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_FIXED_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _sample_agy_payload(
    *,
    remaining_fraction: float | None = 0.9378,
    reset_time: str = "2026-07-06T07:50:32Z",
    reset_in_seconds: int = 417032,
    plan_tier: str = "Ultra",
    extra_buckets: dict | None = None,
) -> dict:
    weekly: dict = {"reset_time": reset_time, "reset_in_seconds": reset_in_seconds}
    if remaining_fraction is not None:
        weekly["remaining_fraction"] = remaining_fraction
    buckets = {"gemini-weekly": weekly}
    if extra_buckets:
        buckets.update(extra_buckets)
    return {
        "cwd": "/home/user/myproject/.worktrees/myproject-box-ada",
        "session_id": "12345678-abcd-4ef0-8123-456789abcdef",
        "conversation_id": "12345678-abcd-4ef0-8123-456789abcdef",
        "model": {
            "id": "Gemini 4 Argon (Medium)",
            "display_name": "Gemini 4 Argon (Medium)",
            "effort": "medium",
        },
        "workspace": {
            "current_dir": "/home/user/myproject/.worktrees/myproject-box-ada",
            "project_dir": "/home/user/myproject/.worktrees/myproject-box-ada",
        },
        "version": "1.5.14",
        "context_window": {
            "total_input_tokens": 88244,
            "total_output_tokens": 61074,
            "context_window_size": 1048576,
            "used_percentage": 14.24,
            "remaining_percentage": 85.76,
        },
        "product": "antigravity",
        "quota": buckets,
        "agent_state": "idle",
        "plan_tier": plan_tier,
        "email": "user@example.com",
    }


@pytest.fixture
def agy_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_file = tmp_path / "state" / "agy-quota.json"
    monkeypatch.setattr(
        agy_quota,
        "agy_quota_settings",
        lambda: (state_file, 1800.0),
    )
    return state_file


@pytest.fixture
def isolated_quota_db(tmp_path: Path):
    qdb.set_db_path(tmp_path / "quota.db")
    try:
        yield tmp_path / "quota.db"
    finally:
        qdb.set_db_path(None)  # type: ignore[arg-type]


def test_given_valid_agy_payload_when_parsed_then_used_pct_and_no_email_are_recorded():
    parsed = agy_quota.parse_statusline_payload(_sample_agy_payload(), now=_FIXED_NOW)
    assert parsed is not None
    assert parsed["provider"] == "agy"
    assert parsed["plan_tier"] == "Ultra"
    assert "email" not in json.dumps(parsed)
    weekly = parsed["buckets"]["gemini-weekly"]
    assert weekly["comparable"] is True
    assert weekly["window_kind"] == "seven_day"
    assert weekly["remaining_fraction"] == pytest.approx(0.9378)
    assert weekly["used_percent"] == pytest.approx(6.22)
    assert weekly["reset_at"] == "2026-07-06T07:50:32Z"


def test_given_incomparable_remaining_amount_only_when_evaluated_then_unknown_is_preserved(agy_state_file: Path):
    payload = _sample_agy_payload(remaining_fraction=None)
    payload["quota"]["gemini-weekly"]["remaining_amount"] = 420
    agy_quota.record_agy_statusline_payload(payload, now=_FIXED_NOW, state_path=agy_state_file)

    status = agy_quota.read_agy_quota_status(now=_FIXED_NOW, state_path=agy_state_file, allow_kv_fallback=False)
    assert status["status"] == "unknown"
    assert status["used_percent"] is None
    assert status["remaining_fraction"] is None
    bucket = status["buckets"]["gemini-weekly"]
    assert bucket["status"] == "unknown"
    assert bucket["comparable"] is False
    assert bucket["remaining_amount"] == 420.0


def test_given_disabled_bucket_when_evaluated_then_unknown_is_preserved(agy_state_file: Path):
    payload = _sample_agy_payload()
    payload["quota"]["gemini-weekly"]["disabled"] = True
    agy_quota.record_agy_statusline_payload(payload, now=_FIXED_NOW, state_path=agy_state_file)

    status = agy_quota.read_agy_quota_status(now=_FIXED_NOW, state_path=agy_state_file, allow_kv_fallback=False)
    assert status["status"] == "unknown"
    assert status["used_percent"] is None
    assert status["buckets"]["gemini-weekly"]["disabled"] is True


def test_given_missing_state_file_when_evaluated_then_status_is_unknown(agy_state_file: Path):
    status = agy_quota.read_agy_quota_status(now=_FIXED_NOW, state_path=agy_state_file, allow_kv_fallback=False)
    assert status["status"] == "unknown"
    assert status["buckets"] == {}
    assert status["used_percent"] is None


def test_given_stale_or_expired_reading_when_evaluated_then_status_is_stale_and_segment_empty(agy_state_file: Path):
    agy_quota.record_agy_statusline_payload(_sample_agy_payload(), now=_FIXED_NOW, state_path=agy_state_file)

    stale_now = _FIXED_NOW + timedelta(seconds=1801)
    status = agy_quota.read_agy_quota_status(now=stale_now, state_path=agy_state_file, allow_kv_fallback=False)
    assert status["status"] == "stale"
    assert (
        agy_quota.render_agy_statusline_segment(now=stale_now, state_path=agy_state_file, allow_kv_fallback=False) == ""
    )

    past_reset = datetime(2026, 7, 6, 8, 0, 0, tzinfo=UTC)
    expired = agy_quota.read_agy_quota_status(now=past_reset, state_path=agy_state_file, allow_kv_fallback=False)
    assert expired["status"] == "stale"
    assert expired["buckets"]["gemini-weekly"]["reason"] == "reset time has passed"


def test_given_partial_update_when_recorded_then_prior_bucket_timestamp_is_not_relabeled_fresh(agy_state_file: Path):
    first = _sample_agy_payload(
        extra_buckets={"gemini-5h": {"remaining_fraction": 0.80, "reset_in_seconds": 12000}},
    )
    agy_quota.record_agy_statusline_payload(first, now=_FIXED_NOW, state_path=agy_state_file)

    later = _FIXED_NOW + timedelta(minutes=10)
    second = _sample_agy_payload(remaining_fraction=0.90)
    agy_quota.record_agy_statusline_payload(second, now=later, state_path=agy_state_file)

    saved = json.loads(agy_state_file.read_text(encoding="utf-8"))
    assert saved["buckets"]["gemini-weekly"]["updated_at"] == "2026-07-01T12:10:00Z"
    assert saved["buckets"]["gemini-5h"]["updated_at"] == "2026-07-01T12:00:00Z"


def test_given_local_missing_and_kv_fresh_when_read_then_kv_reading_is_used(agy_state_file: Path, monkeypatch):
    kv_doc = agy_quota.parse_statusline_payload(_sample_agy_payload(), now=_FIXED_NOW)
    monkeypatch.setattr(agy_quota, "read_agy_quota_kv", lambda: kv_doc)

    status = agy_quota.read_agy_quota_status(now=_FIXED_NOW, state_path=agy_state_file, allow_kv_fallback=True)
    assert status["status"] == "ok"
    assert status["used_percent"] == pytest.approx(6.22)
    assert status["remaining_fraction"] == pytest.approx(0.9378)


def test_given_fresh_weekly_and_five_hour_buckets_when_rendered_then_both_appear_in_google_blue(agy_state_file: Path):
    payload = _sample_agy_payload(
        remaining_fraction=0.75,
        extra_buckets={"gemini-5h": {"remaining_fraction": 0.60, "reset_in_seconds": 10800}},
    )
    agy_quota.record_agy_statusline_payload(payload, now=_FIXED_NOW, state_path=agy_state_file)

    rendered = agy_quota.render_agy_statusline_segment(
        now=_FIXED_NOW,
        state_path=agy_state_file,
        allow_kv_fallback=False,
    )
    clean = _strip_ansi(rendered)
    assert "agyWk 25%" in clean
    assert "agy5h 40%" in clean
    assert "\033[1;38;2;66;133;244m" in rendered
    assert "\n" not in rendered


def test_given_agy_statusline_cli_when_invoked_with_stdin_then_persists_and_outputs_single_line(
    agy_state_file: Path, isolated_quota_db: Path, monkeypatch: pytest.MonkeyPatch
):
    now = datetime.now(UTC)
    qdb.record_quota_snapshot(usage_percent=42.0, reset_at=(now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr(agy_quota, "publish_agy_quota", lambda **kwargs: False)
    payload = _sample_agy_payload(
        remaining_fraction=0.9378,
        reset_time=(now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reset_in_seconds=5 * 86400,
    )

    result = CliRunner().invoke(
        _cli_group,
        ["quota", "render-statusline", "-p", "agy"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1
    clean = _strip_ansi(lines[0])
    assert "Gemini 4 Argon" in clean
    assert "myproject" in clean
    assert "myproject-box-ada" in clean
    assert "14%" in clean
    assert "agyWk 6%" in clean
    assert "ccWk 42%" in clean
    assert "Ultra" in clean
    assert agy_state_file.is_file()


def test_given_malformed_stdin_when_render_statusline_runs_then_it_degrades_to_one_line(
    agy_state_file: Path, isolated_quota_db: Path
):
    result = CliRunner().invoke(_cli_group, ["quota", "render-statusline"], input="not json")
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1
    assert "--%" in _strip_ansi(lines[0])
    assert not agy_state_file.exists()


def test_given_fresh_agy_state_when_quota_status_json_runs_then_both_providers_are_emitted(
    agy_state_file: Path, isolated_quota_db: Path
):
    qdb.record_quota_snapshot(usage_percent=33.0, reset_at="2026-07-07T06:00:00Z")
    now = datetime.now(UTC)
    payload = _sample_agy_payload(
        remaining_fraction=0.88,
        reset_time=(now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reset_in_seconds=3 * 86400,
    )
    agy_quota.record_agy_statusline_payload(payload, now=now, state_path=agy_state_file)

    res_json = CliRunner().invoke(_cli_group, ["quota", "status", "-j"])
    assert res_json.exit_code == 0, res_json.output
    doc = json.loads(res_json.output)
    assert doc["claude"]["latest_snapshot"]["usage_percent"] == pytest.approx(33.0)
    assert doc["agy"]["status"] == "ok"
    assert doc["agy"]["used_percent"] == pytest.approx(12.0)
    assert doc["agy"]["remaining_fraction"] == pytest.approx(0.88)

    res_text = CliRunner().invoke(_cli_group, ["quota", "status"])
    assert res_text.exit_code == 0, res_text.output
    assert "Antigravity (agy): 12.0% used (88.0% remaining; status: ok, plan: Ultra" in res_text.output


def test_given_agy_provider_flag_when_statusline_part_runs_then_it_emits_agy_segment(
    agy_state_file: Path,
):
    now = datetime.now(UTC)
    payload = _sample_agy_payload(
        remaining_fraction=0.70,
        reset_time=(now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reset_in_seconds=4 * 86400,
    )
    agy_quota.record_agy_statusline_payload(payload, now=now, state_path=agy_state_file)

    res = CliRunner().invoke(_cli_group, ["quota", "statusline-part", "-p", "agy"])
    assert res.exit_code == 0, res.output
    assert "agyWk 30%" in _strip_ansi(res.output)


def test_given_env_segment_agy_weekly_when_rendered_then_google_adapter_is_used(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_CLI_QUOTA_STATUSLINE_SEGMENT", "agy-weekly")
    monkeypatch.setenv("AI_CLI_QUOTA_STATUSLINE_PCT", "20")
    monkeypatch.delenv("AI_CLI_QUOTA_STATUSLINE_RESET", raising=False)
    rendered = quota._render_env_statusline_segment("agy-weekly")
    assert "agyWk 20%" in _strip_ansi(rendered)
    assert "\033[1;38;2;66;133;244m" in rendered


def test_given_fresh_agy_state_when_quota_publish_runs_then_agy_publisher_is_called(
    tmp_path: Path, agy_state_file: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    missing_cc = tmp_path / "missing-cc-quota.json"
    monkeypatch.setattr(quota, "_quota_state_settings", lambda: (missing_cc, 1800.0))
    called = []
    monkeypatch.setattr(agy_quota, "publish_agy_quota", lambda: called.append(True) or True)

    code = quota.quota_publish()
    assert code == 0
    assert called == [True]
    assert "agy snapshot sent" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not available")
@pytest.mark.skipif(sys.platform == "win32", reason="statusline-command.sh requires POSIX bash")
def test_given_agy_quota_file_when_cc_statusline_runs_then_agy_segment_is_included(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".local" / "state" / "ai-cli").mkdir(parents=True)
    (home / ".local" / "state" / "ai-cli" / "agy-quota.json").write_text('{"buckets":{}}', encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ai = bin_dir / "ai"
    fake_ai.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"-p agy"* ]]; then echo "agyWk 12%"; '
        'elif [[ "$*" == *"statusline-part"* ]]; then echo "ccWk 40%"; fi\n',
        encoding="utf-8",
    )
    fake_ai.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "src" / "ai_cli" / "data" / "statusline-command.sh"
    sample_cc = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "context_window": {"used_percentage": 22, "total_input_tokens": 1000, "total_output_tokens": 200},
            "workspace": {"project_dir": "/tmp"},
        }
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TMUX": "",
        "COLUMNS": "200",
        "GIT_BRANCH_CACHE": "main",
        "CC_BILLING_MODE": "subscription",
    }
    res = subprocess.run(
        ["bash", str(script)], input=sample_cc, capture_output=True, text=True, env=env, timeout=30, check=False
    )
    assert res.returncode == 0, res.stderr
    lines = [line for line in res.stdout.splitlines() if line]
    assert len(lines) == 1
    assert "ccWk 40%" in res.stdout
    assert "agyWk 12%" in res.stdout


def test_given_fresh_agy_state_when_publish_agy_quota_runs_then_nats_subjects_and_kv_are_written(
    agy_state_file: Path, monkeypatch: pytest.MonkeyPatch
):
    now = datetime.now(UTC)
    payload = _sample_agy_payload(
        remaining_fraction=0.80,
        reset_time=(now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reset_in_seconds=5 * 86400,
    )
    agy_quota.record_agy_statusline_payload(payload, now=now, state_path=agy_state_file)
    monkeypatch.setenv("AI_HOST", "box-1")
    published: list[tuple[str, dict]] = []
    kv_puts: list[tuple[str, dict]] = []

    class FakeKV:
        async def put(self, key: str, value: bytes) -> None:
            kv_puts.append((key, json.loads(value.decode())))

    class FakeJS:
        async def key_value(self, bucket: str) -> FakeKV:
            assert bucket == "hw_state"
            return FakeKV()

    class FakeNATSClient:
        def __init__(self, servers):
            self.nc = object()
            self.js = FakeJS()

        async def connect(self):
            return None

        async def publish(self, subject: str, data: dict):
            published.append((subject, data))

        async def close(self):
            return None

    with (
        patch("ai_cli.messaging.NATSClient", FakeNATSClient),
        patch("ai_cli.config.load_config", return_value={"messaging": {"nats_servers": ["nats://127.0.0.1:4222"]}}),
    ):
        assert agy_quota.publish_agy_quota(now=now, state_path=agy_state_file) is True

    subjects = [s for s, _ in published]
    assert "quota.agy.snapshot" in subjects
    assert "hw.events.usage.agy.snapshot" in subjects
    kv_keys = [k for k, _ in kv_puts]
    assert "quota.agy.current.box-1" in kv_keys
    assert "quota.agy.current" in kv_keys
