"""Tests for cc_usage module — CC CLI per-call token tracking."""

import json
import urllib.error
from datetime import UTC
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.cc_usage import (
    CCTokenEvent,
    _decode_project_path,
    _extract_event,
    _load_cursor,
    _parse_iso,
    _push_to_api,
    _read_jsonl,
    _save_cursor,
    get_cursor_summary,
    scan_and_push,
    scan_new_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assistant_entry(
    uuid="entry-1",
    session_id="sess-1",
    timestamp="2026-04-17T10:00:00Z",
    model="claude-opus-4-6",
    input_tokens=100,
    cache_creation=50,
    cache_read=25,
    output_tokens=200,
    version="2.1.0",
    git_branch="main",
    cwd="/home/user/myproject",
) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": session_id,
        "timestamp": timestamp,
        "version": version,
        "gitBranch": git_branch,
        "cwd": cwd,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
            },
        },
    }


def _make_human_entry(uuid="h-1", timestamp="2026-04-17T10:00:00Z") -> dict:
    return {"type": "human", "uuid": uuid, "timestamp": timestamp, "message": "hello"}


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_parse_iso_when_utc_z_suffix_then_returns_aware_datetime(self):
        dt = _parse_iso("2026-04-17T10:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_parse_iso_when_offset_then_returns_aware_datetime(self):
        dt = _parse_iso("2026-04-17T10:00:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_iso_when_naive_then_attaches_utc(self):
        dt = _parse_iso("2026-04-17T10:00:00")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_parse_iso_when_invalid_then_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_parse_iso_when_none_then_returns_none(self):
        assert _parse_iso(None) is None


# ---------------------------------------------------------------------------
# _decode_project_path
# ---------------------------------------------------------------------------


class TestDecodeProjectPath:
    def test_decode_when_leading_dash_then_converts_to_absolute_path(self):
        result = _decode_project_path("-Users-user-projects-myapp")
        assert result == "/Users/user/projects/myapp"

    def test_decode_when_no_leading_dash_then_returns_as_is(self):
        result = _decode_project_path("myproject")
        assert result == "myproject"


# ---------------------------------------------------------------------------
# _extract_event
# ---------------------------------------------------------------------------


class TestExtractEvent:
    def test_extract_when_valid_assistant_entry_then_returns_event(self):
        entry = _make_assistant_entry()
        event = _extract_event(entry, "sess-1", "/myproject", "mymachine")
        assert event is not None
        assert event.id == "entry-1"
        assert event.session_id == "sess-1"
        assert event.machine == "mymachine"
        assert event.model == "claude-opus-4-6"
        assert event.input_tokens == 100
        assert event.cache_creation_tokens == 50
        assert event.cache_read_tokens == 25
        assert event.output_tokens == 200
        assert event.cc_version == "2.1.0"
        assert event.git_branch == "main"
        assert event.project_path == "/myproject"

    def test_extract_when_no_project_path_then_falls_back_to_cwd(self):
        entry = _make_assistant_entry(cwd="/fallback/cwd")
        event = _extract_event(entry, "sess-1", None, "machine")
        assert event is not None
        assert event.project_path == "/fallback/cwd"

    def test_extract_when_human_entry_then_returns_none(self):
        entry = _make_human_entry()
        assert _extract_event(entry, "sess-1", "/p", "m") is None

    def test_extract_when_no_usage_then_returns_none(self):
        entry = _make_assistant_entry()
        entry["message"].pop("usage")
        assert _extract_event(entry, "sess-1", "/p", "m") is None

    def test_extract_when_no_uuid_then_returns_none(self):
        entry = _make_assistant_entry()
        entry.pop("uuid")
        assert _extract_event(entry, "sess-1", "/p", "m") is None

    def test_extract_when_bad_timestamp_then_returns_none(self):
        entry = _make_assistant_entry(timestamp="not-a-date")
        assert _extract_event(entry, "sess-1", "/p", "m") is None


# ---------------------------------------------------------------------------
# _read_jsonl
# ---------------------------------------------------------------------------


class TestReadJsonl:
    def test_read_when_valid_file_then_returns_all_entries(self, tmp_path):
        path = tmp_path / "test.jsonl"
        _write_jsonl(path, [{"a": 1}, {"b": 2}])
        result = _read_jsonl(path)
        assert len(result) == 2

    def test_read_when_file_missing_then_returns_empty_list(self, tmp_path):
        result = _read_jsonl(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_read_when_malformed_line_then_skips_it(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text('{"good": 1}\nnot-json\n{"also": 2}\n')
        result = _read_jsonl(path)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Cursor management
# ---------------------------------------------------------------------------


class TestCursorManagement:
    def test_load_cursor_when_missing_then_returns_empty_dict(self, tmp_path):
        with patch("ai_cli.cc_usage._CURSOR_FILE", tmp_path / "cursor.json"):
            result = _load_cursor()
        assert result == {}

    def test_save_and_load_cursor_roundtrip(self, tmp_path):
        cursor_file = tmp_path / "state" / "cursor.json"
        with patch("ai_cli.cc_usage._CURSOR_FILE", cursor_file):
            with patch("ai_cli.cc_usage._STATE_DIR", tmp_path / "state"):
                _save_cursor({"sess-1": "2026-04-17T10:00:00+00:00"})
                result = _load_cursor()
        assert result == {"sess-1": "2026-04-17T10:00:00+00:00"}

    def test_load_cursor_when_corrupt_then_returns_empty_dict(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        cursor_file.write_text("not json")
        with patch("ai_cli.cc_usage._CURSOR_FILE", cursor_file):
            result = _load_cursor()
        assert result == {}


# ---------------------------------------------------------------------------
# scan_new_events
# ---------------------------------------------------------------------------


class TestScanNewEvents:
    def _setup_claude_dir(self, tmp_path: Path) -> Path:
        """Create a mock ~/.claude/projects/ structure."""
        return tmp_path / "projects"

    def test_scan_when_no_projects_dir_then_returns_empty(self, tmp_path):
        events, cursor = scan_new_events(claude_dir=tmp_path / "nonexistent", machine="testmachine")
        assert events == []
        assert cursor == {}

    def test_scan_when_valid_session_then_returns_events(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        session_file = proj_dir / "session-uuid-1.jsonl"
        _write_jsonl(session_file, [_make_assistant_entry(uuid="e1", timestamp="2026-04-17T10:00:00Z")])

        events, new_cursor = scan_new_events(claude_dir=projects_dir, machine="testmachine", cursor={})
        assert len(events) == 1
        assert events[0].id == "e1"
        assert events[0].machine == "testmachine"
        assert "session-uuid-1" in new_cursor

    def test_scan_when_cursor_present_then_skips_old_events(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        session_file = proj_dir / "session-uuid-1.jsonl"
        _write_jsonl(
            session_file,
            [
                _make_assistant_entry(uuid="old", timestamp="2026-04-17T09:00:00Z"),
                _make_assistant_entry(uuid="new", timestamp="2026-04-17T11:00:00Z"),
            ],
        )

        cursor = {"session-uuid-1": "2026-04-17T10:00:00+00:00"}
        events, new_cursor = scan_new_events(claude_dir=projects_dir, machine="m", cursor=cursor)
        assert len(events) == 1
        assert events[0].id == "new"

    def test_scan_when_memory_file_then_skips_it(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        memory_file = proj_dir / "memory.jsonl"
        _write_jsonl(memory_file, [_make_assistant_entry(uuid="mem-entry")])

        events, _ = scan_new_events(claude_dir=projects_dir, machine="m", cursor={})
        assert events == []

    def test_scan_when_human_entries_then_skips_them(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        session_file = proj_dir / "sess.jsonl"
        _write_jsonl(session_file, [_make_human_entry(), _make_assistant_entry(uuid="a1")])

        events, _ = scan_new_events(claude_dir=projects_dir, machine="m", cursor={})
        assert len(events) == 1
        assert events[0].id == "a1"

    def test_scan_when_multiple_sessions_then_updates_cursor_per_session(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        _write_jsonl(
            proj_dir / "sess-a.jsonl",
            [_make_assistant_entry(uuid="a1", timestamp="2026-04-17T10:00:00Z")],
        )
        _write_jsonl(
            proj_dir / "sess-b.jsonl",
            [_make_assistant_entry(uuid="b1", timestamp="2026-04-17T11:00:00Z")],
        )

        events, new_cursor = scan_new_events(claude_dir=projects_dir, machine="m", cursor={})
        assert len(events) == 2
        assert "sess-a" in new_cursor
        assert "sess-b" in new_cursor

    def test_scan_when_no_new_events_then_cursor_unchanged(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        _write_jsonl(
            proj_dir / "sess.jsonl",
            [_make_assistant_entry(uuid="e1", timestamp="2026-04-17T10:00:00Z")],
        )

        cursor = {"sess": "2026-04-17T12:00:00+00:00"}
        events, new_cursor = scan_new_events(claude_dir=projects_dir, machine="m", cursor=cursor)
        assert events == []
        assert new_cursor == cursor

    def test_scan_uses_ai_cli_host_env_when_machine_not_passed(self, tmp_path):
        projects_dir = self._setup_claude_dir(tmp_path)
        proj_dir = projects_dir / "-Users-user-myapp"
        _write_jsonl(proj_dir / "sess.jsonl", [_make_assistant_entry(uuid="e1")])

        with patch.dict("os.environ", {"AI_HOST": "mymachine"}):
            events, _ = scan_new_events(claude_dir=projects_dir, cursor={})
        assert events[0].machine == "mymachine"


# ---------------------------------------------------------------------------
# _push_to_api
# ---------------------------------------------------------------------------


class TestPushToApi:
    def _make_event(self, uid="e1") -> CCTokenEvent:
        return CCTokenEvent(
            id=uid,
            session_id="sess-1",
            project_path="/myapp",
            machine="testmachine",
            model="claude-opus-4-6",
            input_tokens=100,
            cache_creation_tokens=50,
            cache_read_tokens=25,
            output_tokens=200,
            cc_version="2.1.0",
            git_branch="main",
            occurred_at="2026-04-17T10:00:00+00:00",
        )

    def test_push_when_success_then_returns_inserted_skipped(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"inserted": 3, "skipped": 1}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            inserted, skipped = _push_to_api([self._make_event()], "https://example.com", "hw-api-key")
        assert inserted == 3
        assert skipped == 1

    def test_push_when_network_error_then_raises(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with pytest.raises(urllib.error.URLError):
                _push_to_api([self._make_event()], "https://example.com", "hw-api-key")

    def test_push_sends_bearer_auth_header(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.get_header("Authorization")
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"inserted": 1, "skipped": 0}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", fake_urlopen):
            _push_to_api([self._make_event()], "https://example.com", "mykey")

        assert captured["auth"] == "Bearer mykey"

    def test_push_url_strips_trailing_slash(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"inserted": 0, "skipped": 0}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", fake_urlopen):
            _push_to_api([self._make_event()], "https://example.com///", "k")

        assert captured["url"] == "https://example.com/api/v1/usage/cc/ingest"


# ---------------------------------------------------------------------------
# scan_and_push
# ---------------------------------------------------------------------------


class TestScanAndPush:
    _CONFIG_MISSING: ClassVar[dict] = {"usage_api": {}}
    _CONFIG_OK: ClassVar[dict] = {"usage_api": {"api_url": "https://example.com", "api_key": "ua-api-key"}}

    def test_scan_and_push_when_config_missing_then_error_names_the_section_read(self):
        """The error must name the section the code actually reads.

        A message pointing at a section the template no longer declares is
        unactionable, so the asserted name is derived from the key
        ``scan_and_push`` looks up rather than written out twice.
        """
        section = next(iter(self._CONFIG_MISSING))
        result = scan_and_push(config=self._CONFIG_MISSING)
        assert result.error is not None
        assert "api_url" in result.error or "api_key" in result.error
        assert f"[{section}]" in result.error

    def test_scan_and_push_when_no_new_events_then_returns_zero_counts(self, tmp_path):
        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            with patch("ai_cli.cc_usage.scan_new_events", return_value=([], {})):
                result = scan_and_push(config=self._CONFIG_OK)
        assert result.new_events == 0
        assert result.inserted == 0
        assert result.error is None

    def test_scan_and_push_when_dry_run_then_does_not_push(self, tmp_path):
        events = [
            CCTokenEvent(
                id="e1",
                session_id="s1",
                project_path="/myapp",
                machine="m",
                model="claude-opus-4-6",
                input_tokens=10,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                output_tokens=20,
                cc_version="2.1.0",
                git_branch=None,
                occurred_at="2026-04-17T10:00:00+00:00",
            )
        ]
        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            with patch("ai_cli.cc_usage.scan_new_events", return_value=(events, {"s1": "2026-04-17T10:00:00+00:00"})):
                with patch("ai_cli.cc_usage._push_to_api") as mock_push:
                    result = scan_and_push(config=self._CONFIG_OK, dry_run=True)

        mock_push.assert_not_called()
        assert result.new_events == 1
        assert result.inserted == 0

    def test_scan_and_push_when_push_succeeds_then_saves_cursor(self, tmp_path):
        events = [
            CCTokenEvent(
                id="e1",
                session_id="s1",
                project_path="/myapp",
                machine="m",
                model="claude-opus-4-6",
                input_tokens=10,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                output_tokens=20,
                cc_version="2.1.0",
                git_branch=None,
                occurred_at="2026-04-17T10:00:00+00:00",
            )
        ]
        new_cursor = {"s1": "2026-04-17T10:00:00+00:00"}

        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            with patch("ai_cli.cc_usage.scan_new_events", return_value=(events, new_cursor)):
                with patch("ai_cli.cc_usage._push_to_api", return_value=(1, 0)):
                    with patch("ai_cli.cc_usage._save_cursor") as mock_save:
                        result = scan_and_push(config=self._CONFIG_OK)

        mock_save.assert_called_once_with(new_cursor)
        assert result.inserted == 1
        assert result.error is None

    def test_scan_and_push_when_push_fails_then_returns_error(self):
        events = [
            CCTokenEvent(
                id="e1",
                session_id="s1",
                project_path=None,
                machine="m",
                model="claude-opus-4-6",
                input_tokens=10,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                output_tokens=20,
                cc_version=None,
                git_branch=None,
                occurred_at="2026-04-17T10:00:00+00:00",
            )
        ]
        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            with patch("ai_cli.cc_usage.scan_new_events", return_value=(events, {})):
                with patch("ai_cli.cc_usage._push_to_api", side_effect=Exception("network error")):
                    with patch("ai_cli.cc_usage._save_cursor") as mock_save:
                        result = scan_and_push(config=self._CONFIG_OK)

        mock_save.assert_not_called()
        assert result.error == "network error"

    def test_scan_and_push_batches_large_event_sets(self):
        events = [
            CCTokenEvent(
                id=f"e{i}",
                session_id="s1",
                project_path=None,
                machine="m",
                model="model",
                input_tokens=1,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                output_tokens=1,
                cc_version=None,
                git_branch=None,
                occurred_at="2026-04-17T10:00:00+00:00",
            )
            for i in range(1200)
        ]
        call_sizes = []

        def fake_push(batch, api_url, api_key):
            call_sizes.append(len(batch))
            return len(batch), 0

        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            with patch("ai_cli.cc_usage.scan_new_events", return_value=(events, {})):
                with patch("ai_cli.cc_usage._push_to_api", side_effect=fake_push):
                    with patch("ai_cli.cc_usage._save_cursor"):
                        scan_and_push(config=self._CONFIG_OK)

        assert len(call_sizes) == 3  # 500 + 500 + 200
        assert call_sizes[0] == 500
        assert call_sizes[1] == 500
        assert call_sizes[2] == 200


# ---------------------------------------------------------------------------
# get_cursor_summary
# ---------------------------------------------------------------------------


class TestGetCursorSummary:
    def test_summary_when_empty_cursor_then_returns_zero(self):
        with patch("ai_cli.cc_usage._load_cursor", return_value={}):
            summary = get_cursor_summary()
        assert summary["sessions_tracked"] == 0
        assert summary["last_push"] is None

    def test_summary_when_cursor_has_entries_then_returns_max_ts(self):
        cursor = {
            "sess-a": "2026-04-17T09:00:00+00:00",
            "sess-b": "2026-04-17T11:00:00+00:00",
            "sess-c": "2026-04-17T10:00:00+00:00",
        }
        with patch("ai_cli.cc_usage._load_cursor", return_value=cursor):
            summary = get_cursor_summary()
        assert summary["sessions_tracked"] == 3
        assert summary["last_push"] == "2026-04-17T11:00:00+00:00"
