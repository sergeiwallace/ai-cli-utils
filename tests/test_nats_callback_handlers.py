"""Unit tests for module-level NATS callback handlers extracted from main.py.

These functions were extracted from closures inside ``_internal_signal_watch``,
``_internal_handoff_drain``, and ``_internal_quota_subscriber`` so they can be
tested independently of a live NATS server.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

from ai_cli.main import (
    _on_handoff_signal_watch,
    _on_quota_snapshot_handler,
    _write_pending_if_claimed_drain,
)

# ---------------------------------------------------------------------------
# _write_pending_if_claimed_drain
# ---------------------------------------------------------------------------


def _drain_kwargs(tmp_path: Path, *, machine_id: str = "mac") -> dict:
    handoff_dir = tmp_path / "handoff"
    handoff_dir.mkdir()
    (handoff_dir / "pending").mkdir()
    (handoff_dir / "claimed").mkdir()
    return {
        "handoff_dir": handoff_dir,
        "prompt_file": tmp_path / "resume.txt",
        "session": "ses-1",
        "machine_id": machine_id,
    }


def _base_data(**overrides) -> dict:
    d = {"id": 42, "title": "Do thing", "priority": "P1", "message": "go!", "for_machine": "mac"}
    d.update(overrides)
    return d


def test_given_wrong_machine_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path, machine_id="hetzner")
    result = _write_pending_if_claimed_drain(_base_data(), **kw)
    assert result is False


def test_given_empty_for_machine_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path)
    result = _write_pending_if_claimed_drain(_base_data(for_machine=""), **kw)
    assert result is False


def test_given_none_handoff_dir_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path)
    kw["handoff_dir"] = None
    result = _write_pending_if_claimed_drain(_base_data(), **kw)
    assert result is False


def test_given_missing_id_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path)
    result = _write_pending_if_claimed_drain(_base_data(id=None), **kw)
    assert result is False


def test_given_claim_returns_none_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path)
    with patch("ai_cli.handoff._claim_handoff_for_signal", return_value=None):
        result = _write_pending_if_claimed_drain(_base_data(), **kw)
    assert result is False


def test_given_successful_claim_when_drain_then_writes_prompt_and_returns_true(tmp_path):
    kw = _drain_kwargs(tmp_path)
    fake_claimed = tmp_path / "handoff" / "claimed" / "42-do-thing.md"
    with (
        patch("ai_cli.handoff._claim_handoff_for_signal", return_value=fake_claimed),
        patch("ai_cli.handoff._log_handoff_event"),
    ):
        result = _write_pending_if_claimed_drain(_base_data(), **kw)

    assert result is True
    prompt = kw["prompt_file"].read_text()
    assert "Auto-pickup" in prompt
    assert "P1" in prompt
    assert "42" in prompt
    assert "go!" in prompt


def test_given_already_claimed_file_when_drain_then_returns_false(tmp_path):
    kw = _drain_kwargs(tmp_path)
    handoff_dir = kw["handoff_dir"]
    filename = "42-do.md"
    (handoff_dir / "claimed" / filename).write_text("done")
    data = _base_data(content="body", filename=filename)
    result = _write_pending_if_claimed_drain(data, **kw)
    assert result is False


def test_given_content_payload_when_drain_then_writes_pending_file(tmp_path):
    kw = _drain_kwargs(tmp_path)
    fake_claimed = tmp_path / "handoff" / "claimed" / "42.md"
    data = _base_data(content="task body", filename="42.md")
    with (
        patch("ai_cli.handoff._claim_handoff_for_signal", return_value=fake_claimed),
        patch("ai_cli.handoff._log_handoff_event"),
    ):
        _write_pending_if_claimed_drain(data, **kw)

    local_file = kw["handoff_dir"] / "pending" / "42.md"
    assert local_file.read_text() == "task body"


# ---------------------------------------------------------------------------
# _on_handoff_signal_watch
# ---------------------------------------------------------------------------


def _sw_kwargs(tmp_path: Path, *, machine_id: str = "mac") -> dict:
    handoff_dir = tmp_path / "handoff"
    handoff_dir.mkdir()
    (handoff_dir / "pending").mkdir()
    (handoff_dir / "claimed").mkdir()
    return {
        "handoff_dir": handoff_dir,
        "pending_file": tmp_path / "pending.txt",
        "session_id": "sw-1",
        "machine_id": machine_id,
    }


def test_given_wrong_machine_when_signal_watch_then_no_side_effects(tmp_path, capsys):
    kw = _sw_kwargs(tmp_path, machine_id="hetzner")
    asyncio.run(_on_handoff_signal_watch(_base_data(), **kw))
    assert capsys.readouterr().out == ""
    assert not kw["pending_file"].exists()


def test_given_none_handoff_dir_when_signal_watch_then_prints_but_no_file(tmp_path, capsys):
    kw = _sw_kwargs(tmp_path)
    kw["handoff_dir"] = None
    asyncio.run(_on_handoff_signal_watch(_base_data(), **kw))
    out = capsys.readouterr().out
    assert "[HANDOFF]" in out
    assert not kw["pending_file"].exists()


def test_given_claim_none_when_signal_watch_then_no_pending_file(tmp_path):
    kw = _sw_kwargs(tmp_path)
    with patch("ai_cli.handoff._claim_handoff_for_signal", return_value=None):
        asyncio.run(_on_handoff_signal_watch(_base_data(), **kw))
    assert not kw["pending_file"].exists()


def test_given_successful_claim_when_signal_watch_then_writes_pending_and_signal(tmp_path):
    kw = _sw_kwargs(tmp_path)
    fake_claimed = tmp_path / "handoff" / "claimed" / "42.md"
    with (
        patch("ai_cli.handoff._claim_handoff_for_signal", return_value=fake_claimed),
        patch("ai_cli.handoff._log_handoff_event"),
        patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
    ):
        asyncio.run(_on_handoff_signal_watch(_base_data(), **kw))

    assert kw["pending_file"].exists()
    text = kw["pending_file"].read_text()
    assert "Auto-pickup" in text
    signal_file = tmp_path / "cc-exit-sw-1"
    assert signal_file.exists()


def test_given_startup_scan_source_when_signal_watch_then_logs_startup_scan_layer(tmp_path):
    kw = _sw_kwargs(tmp_path)
    fake_claimed = tmp_path / "handoff" / "claimed" / "42.md"
    data = _base_data(_source="startup_scan")
    log_calls = []
    with (
        patch("ai_cli.handoff._claim_handoff_for_signal", return_value=fake_claimed),
        patch("ai_cli.handoff._log_handoff_event", side_effect=lambda *a, **kw2: log_calls.append(kw2)),
        patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
    ):
        asyncio.run(_on_handoff_signal_watch(data, **kw))

    assert any(c.get("layer") == "startup_scan" for c in log_calls)


def test_given_realtime_source_when_signal_watch_then_logs_nats_realtime_layer(tmp_path):
    kw = _sw_kwargs(tmp_path)
    fake_claimed = tmp_path / "handoff" / "claimed" / "42.md"
    log_calls = []
    with (
        patch("ai_cli.handoff._claim_handoff_for_signal", return_value=fake_claimed),
        patch("ai_cli.handoff._log_handoff_event", side_effect=lambda *a, **kw2: log_calls.append(kw2)),
        patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
    ):
        asyncio.run(_on_handoff_signal_watch(_base_data(), **kw))

    assert any(c.get("layer") == "nats_realtime" for c in log_calls)


def test_given_already_claimed_file_when_signal_watch_then_skips(tmp_path):
    kw = _sw_kwargs(tmp_path)
    handoff_dir = kw["handoff_dir"]
    filename = "42.md"
    (handoff_dir / "claimed" / filename).write_text("done")
    data = _base_data(content="body", filename=filename)
    with patch("ai_cli.handoff._claim_handoff_for_signal") as mock_claim:
        asyncio.run(_on_handoff_signal_watch(data, **kw))
    mock_claim.assert_not_called()


# ---------------------------------------------------------------------------
# _on_quota_snapshot_handler
# ---------------------------------------------------------------------------


def test_given_valid_snapshot_when_handler_then_calls_record(tmp_path):
    data = {
        "usage_percent": 55.0,
        "session_pct": 10.0,
        "weekly_sonnet_pct": 20.0,
        "extra_pct": 5.0,
        "reset_at": "2026-04-20T00:00:00Z",
    }
    with patch("ai_cli.quota_db.record_quota_snapshot") as mock_rec:
        asyncio.run(_on_quota_snapshot_handler(data))

    mock_rec.assert_called_once_with(
        usage_percent=55.0,
        session_pct=10.0,
        weekly_sonnet_pct=20.0,
        weekly_model_name=None,
        extra_pct=5.0,
        reset_at="2026-04-20T00:00:00Z",
    )


def test_given_missing_usage_percent_when_handler_then_swallows_exception():
    with patch("ai_cli.quota_db.record_quota_snapshot", side_effect=KeyError("usage_percent")):
        asyncio.run(_on_quota_snapshot_handler({}))


def test_given_record_raises_when_handler_then_does_not_propagate():
    with patch("ai_cli.quota_db.record_quota_snapshot", side_effect=RuntimeError("db down")):
        asyncio.run(_on_quota_snapshot_handler({"usage_percent": 50.0}))
