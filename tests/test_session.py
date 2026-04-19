import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


from ai_cli.main import (
    build_session_name,
    cleanup_stale_sessions,
    cleanup_worktree,
    _checkpoint_to_chat_uuid,
    _convert_checkpoint_to_chat,
    create_worktree,
    detect_repo_root,
    _find_latest_gemini_uuid,
    _get_chat_last_message_timestamp,
    find_next_index,
    find_recent_session,
    get_latest_gemini_session_id,
    get_session_map,
    get_session_map_path,
    resolve_session,
    save_session_map,
)

from conftest import _make_list_panes_output


# --- build_session_name tests ---
# Session name format: {c|g}[-r]-{project}-{index}
# ai_name format: {project}-{index}


def test_build_session_name_no_name_when_no_sessions_then_uses_index_1():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "")

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_gemini_when_no_name_then_uses_g_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("g", "sw", "")

        assert session_id == "g-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_with_short_prefix_when_called_then_strips_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "sw-planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_old_full_prefix_when_called_then_strips_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "claude-sw-planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_new_full_name_and_index_when_called_then_strips_all():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "c-sw-1")

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_with_name_when_no_sessions_then_uses_name_index_1():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_double_hyphens_when_called_then_cleans_up():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "research--test")

        assert session_id == "c-sw-research-test-1"
        assert ai_name == "sw-research-test-1"
        assert "--" not in session_id


def test_build_session_name_with_index_when_called_then_respects_index():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        session_id, ai_name = build_session_name("c", "sw", "3")
    assert session_id == "c-sw-3"
    assert ai_name == "sw-3"


def test_build_session_name_never_produces_double_hyphen():
    """Final assembled session name must never contain --."""
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        for name in ["", "1", "planning", "-R", "-R-1", "research--test", "sw-1"]:
            session_id, _ = build_session_name("c", "sw", name)
            assert "--" not in session_id, f"Double hyphen in session_id={session_id!r} for name={name!r}"


def test_build_session_name_is_remote_when_true_then_inserts_r_segment():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "1", is_remote=True)

        assert session_id == "c-r-sw-1"
        assert ai_name == "sw-1"  # ai_name does not include remote tag


def test_build_session_name_is_remote_when_false_then_no_r_segment():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "1", is_remote=False)

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_is_remote_no_name_then_finds_next_index():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "", is_remote=True)

        assert session_id == "c-r-sw-1"
        assert ai_name == "sw-1"


# --- cleanup_stale_sessions tests ---


def _cleanup(config, panes_output, now=None):
    """Run cleanup_stale_sessions with mocked tmux and time."""
    now = now or int(time.time())
    kill_calls = []

    def fake_run(cmd, **kwargs):
        if "list-panes" in cmd:
            return panes_output
        if "kill-session" in cmd:
            kill_calls.append(cmd[cmd.index("-t") + 1])
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run), patch("ai_cli.session.time") as mock_time:
        mock_time.time.return_value = now
        cleanup_stale_sessions(config)
    return kill_calls


def test_cleanup_when_pane_is_shell_then_kills_session():
    now = int(time.time())
    panes = _make_list_panes_output(("c-sw-1", now - 61, "bash"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" in killed


def test_cleanup_when_pane_is_claude_and_recent_then_preserves_session():
    now = int(time.time())
    panes = _make_list_panes_output(("c-sw-1", now - 60, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" not in killed


def test_cleanup_when_claude_abandoned_beyond_timeout_then_kills_session():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds - 1, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" in killed


def test_cleanup_when_claude_within_timeout_then_preserves_session():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds + 60, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" not in killed


def test_cleanup_when_non_ai_session_then_ignores_it():
    now = int(time.time())
    panes = _make_list_panes_output(("my-server", now - 9999, "bash"))
    killed = _cleanup({}, panes, now)
    assert killed == []


def test_cleanup_when_gemini_session_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("g-sw-1", now - timeout_seconds - 1, "gemini"))
    killed = _cleanup({}, panes, now)
    assert "g-sw-1" in killed


def test_cleanup_when_remote_session_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-r-sw-1", now - timeout_seconds - 1, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-r-sw-1" in killed


def test_cleanup_when_custom_timeout_configured_then_uses_it():
    now = int(time.time())
    config = {"session": {"stale_session_timeout": 5}}  # 5 minutes
    timeout_seconds = 5 * 60
    panes = _make_list_panes_output(("c-sw-1", now - timeout_seconds - 1, "claude"))
    killed = _cleanup(config, panes, now)
    assert "c-sw-1" in killed


def test_cleanup_when_no_tmux_then_does_nothing():
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    killed = _cleanup({}, mock)
    assert killed == []


def test_cleanup_when_session_currently_attached_then_never_kills_it():
    """Session with clients attached must never be killed — this was the root bug."""
    now = int(time.time())
    panes = _make_list_panes_output(("c-sw-1", now - 7200, "claude", 1))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" not in killed


def test_cleanup_when_session_detached_and_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds - 1, "claude", 0))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" in killed


def test_cleanup_when_old_format_session_then_ignores_it():
    """Old claude-sw-1 format sessions are not matched by new regex — not killed."""
    now = int(time.time())
    panes = _make_list_panes_output(("claude-sw-1", now - 9999, "bash"))
    killed = _cleanup({}, panes, now)
    assert killed == []


# --- Session map tests ---


class TestSessionMap:
    def test_get_session_map_path_when_gemini_then_returns_gemini_path(self, tmp_path):
        with patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path):
            result = get_session_map_path(engine="g")
        assert "gemini_sessions.json" in str(result)

    def test_get_session_map_when_invalid_json_then_returns_empty(self, tmp_path):
        with patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path):
            path = tmp_path / "gemini_sessions.json"
            path.write_text("not json {{{")
            with patch("ai_cli.config.get_session_map_path", return_value=path):
                result = get_session_map(engine="g")
        assert result == {}

    def test_save_session_map_when_called_then_writes_json(self, tmp_path):
        import json

        path = tmp_path / "test_sessions.json"
        with patch("ai_cli.config.get_session_map_path", return_value=path):
            save_session_map({"sw-1": "uuid123"}, engine="c")
        assert json.loads(path.read_text()) == {"sw-1": "uuid123"}


class TestSessionMapEdgeCases:
    def test_get_session_map_path_when_engine_c_then_returns_claude_path(self, tmp_path):
        with patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path):
            result = get_session_map_path(engine="c")
        assert "cc-sessions.json" in str(result)

    def test_get_session_map_when_file_not_exists_then_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with patch("ai_cli.config.get_session_map_path", return_value=path):
            result = get_session_map(engine="c")
        assert result == {}


# --- _find_latest_gemini_uuid ---


class TestFindLatestGeminiUuid:
    def test_when_chats_dir_has_session_then_returns_session_id(self, tmp_path):
        chats = tmp_path / ".gemini" / "tmp" / "art-1" / "chats"
        chats.mkdir(parents=True)
        (chats / "session-2026-01-01T00-00-abc.json").write_text('{"sessionId": "abc-full-uuid-here"}')
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result == "abc-full-uuid-here"

    def test_when_multiple_files_then_returns_most_recent(self, tmp_path):
        chats = tmp_path / ".gemini" / "tmp" / "art-1" / "chats"
        chats.mkdir(parents=True)
        old_file = chats / "session-old.json"
        new_file = chats / "session-new.json"
        old_file.write_text('{"sessionId": "old-uuid"}')
        new_file.write_text('{"sessionId": "new-uuid"}')
        # Set explicit mtimes to avoid sleep: old=1000, new=2000
        import os as _os

        _os.utime(old_file, (1000.0, 1000.0))
        _os.utime(new_file, (2000.0, 2000.0))
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result == "new-uuid"

    def test_when_no_chats_dir_then_returns_none(self, tmp_path):
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result is None

    def test_when_file_has_no_session_id_then_skips_it(self, tmp_path):
        chats = tmp_path / ".gemini" / "tmp" / "art-1" / "chats"
        chats.mkdir(parents=True)
        (chats / "session-bad.json").write_text('{"otherKey": "value"}')
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result is None

    def test_when_file_is_invalid_json_then_skips_it(self, tmp_path):
        chats = tmp_path / ".gemini" / "tmp" / "art-1" / "chats"
        chats.mkdir(parents=True)
        (chats / "session-corrupt.json").write_text("not json {{{")
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result is None

    def test_when_checkpoint_newer_than_chat_then_converts_and_returns_uuid(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        chats = gemini_tmp / "chats"
        chats.mkdir(parents=True)
        # Chat file with last message at t=1000
        old_chat = chats / "session-old.json"
        old_chat.write_text(
            json.dumps(
                {
                    "sessionId": "old-uuid",
                    "messages": [{"timestamp": "1970-01-01T00:16:40Z", "type": "user", "content": [{"text": "hi"}]}],
                }
            )
        )
        os.utime(old_chat, (1000.0, 1000.0))
        # Checkpoint saved at t=2000 (newer than last message)
        checkpoint = gemini_tmp / "checkpoint-art-1.json"
        history = [{"role": "user", "parts": [{"text": "hello"}]}]
        checkpoint.write_text(json.dumps({"history": history, "authType": "oauth"}))
        os.utime(checkpoint, (2000.0, 2000.0))
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result is not None
        assert result != "old-uuid"  # converted checkpoint UUID, not the old chat

    def test_when_chat_last_message_newer_than_checkpoint_then_uses_existing_chat(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        chats = gemini_tmp / "chats"
        chats.mkdir(parents=True)
        # Chat file with last message at t=2000, but frozen mtime at t=500
        # (simulates gemini-cli not updating mtime on auto-save writes)
        chat = chats / "session-new.json"
        chat.write_text(
            json.dumps(
                {
                    "sessionId": "new-uuid",
                    "messages": [
                        {"timestamp": "1970-01-01T00:33:20Z", "type": "user", "content": [{"text": "later message"}]}
                    ],
                }
            )
        )
        os.utime(chat, (500.0, 500.0))
        # Checkpoint saved at t=1000 (older than last message in chat)
        checkpoint = gemini_tmp / "checkpoint-art-1.json"
        checkpoint.write_text(json.dumps({"history": [{"role": "user", "parts": [{"text": "hi"}]}]}))
        os.utime(checkpoint, (1000.0, 1000.0))
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result == "new-uuid"

    def test_when_resume_save_mid_session_then_uses_chat_with_later_messages(self, tmp_path):
        # Regression: /resume save at t=1500 mid-session; auto-save continued to t=2000.
        # Chat file mtime is frozen at t=500 (gemini-cli does not update mtime on writes).
        # Must use chat file (last message t=2000), not reconvert checkpoint (t=1500).
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        chats = gemini_tmp / "chats"
        chats.mkdir(parents=True)
        chat = chats / "session-active.json"
        chat.write_text(
            json.dumps(
                {
                    "sessionId": "active-uuid",
                    "messages": [
                        {"timestamp": "1970-01-01T00:16:40Z", "type": "user", "content": [{"text": "early msg"}]},
                        {
                            "timestamp": "1970-01-01T00:33:20Z",
                            "type": "gemini",
                            "content": [{"text": "later msg auto-saved after /resume save"}],
                        },
                    ],
                }
            )
        )
        os.utime(chat, (500.0, 500.0))  # mtime frozen — does not reflect actual writes
        checkpoint = gemini_tmp / "checkpoint-art-1.json"
        checkpoint.write_text(json.dumps({"history": [{"role": "user", "parts": [{"text": "hi"}]}]}))
        os.utime(checkpoint, (1500.0, 1500.0))  # /resume save at t=1500
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        # chat last message ts=2000 > checkpoint mtime=1500 → use chat, not checkpoint
        assert result == "active-uuid"

    def test_when_no_checkpoint_and_no_chats_then_returns_none(self, tmp_path):
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _find_latest_gemini_uuid("art-1")
        assert result is None


# --- _get_chat_last_message_timestamp ---


class TestGetChatLastMessageTimestamp:
    def test_when_messages_exist_then_returns_last_timestamp(self, tmp_path):
        chat = tmp_path / "session.json"
        chat.write_text(
            json.dumps(
                {
                    "messages": [
                        {"timestamp": "1970-01-01T00:16:40Z", "type": "user", "content": []},
                        {"timestamp": "1970-01-01T00:33:20Z", "type": "gemini", "content": []},
                    ]
                }
            )
        )
        result = _get_chat_last_message_timestamp(chat)
        assert result == 2000.0

    def test_when_empty_messages_then_returns_zero(self, tmp_path):
        chat = tmp_path / "session.json"
        chat.write_text(json.dumps({"messages": []}))
        result = _get_chat_last_message_timestamp(chat)
        assert result == 0.0

    def test_when_invalid_json_then_returns_zero(self, tmp_path):
        chat = tmp_path / "session.json"
        chat.write_text("not json {{{")
        result = _get_chat_last_message_timestamp(chat)
        assert result == 0.0

    def test_when_file_missing_then_returns_zero(self, tmp_path):
        result = _get_chat_last_message_timestamp(tmp_path / "nonexistent.json")
        assert result == 0.0

    def test_when_timestamp_missing_from_last_message_then_returns_zero(self, tmp_path):
        chat = tmp_path / "session.json"
        chat.write_text(json.dumps({"messages": [{"type": "user", "content": []}]}))
        result = _get_chat_last_message_timestamp(chat)
        assert result == 0.0


# --- _convert_checkpoint_to_chat ---


class TestConvertCheckpointToChat:
    def _make_checkpoint(self, gemini_tmp: Path, ai_name: str, history: list, mtime: float = 1000.0) -> Path:
        gemini_tmp.mkdir(parents=True, exist_ok=True)
        path = gemini_tmp / f"checkpoint-{ai_name}.json"
        path.write_text(json.dumps({"history": history, "authType": "oauth"}))
        os.utime(path, (mtime, mtime))
        return path

    def test_when_checkpoint_exists_then_creates_chat_file(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        history = [
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi there"}]},
        ]
        self._make_checkpoint(gemini_tmp, "art-1", history)
        result = _convert_checkpoint_to_chat("art-1", gemini_tmp)
        assert result is not None
        chats_dir = gemini_tmp / "chats"
        chat_files = list(chats_dir.glob("session-*.json"))
        assert len(chat_files) == 1

    def test_when_called_twice_then_idempotent(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        history = [{"role": "user", "parts": [{"text": "hello"}]}]
        self._make_checkpoint(gemini_tmp, "art-1", history)
        uuid1 = _convert_checkpoint_to_chat("art-1", gemini_tmp)
        uuid2 = _convert_checkpoint_to_chat("art-1", gemini_tmp)
        assert uuid1 == uuid2
        assert len(list((gemini_tmp / "chats").glob("session-*.json"))) == 1

    def test_maps_model_role_to_gemini_type(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        history = [
            {"role": "user", "parts": [{"text": "question"}]},
            {"role": "model", "parts": [{"text": "answer"}]},
        ]
        self._make_checkpoint(gemini_tmp, "art-1", history)
        _convert_checkpoint_to_chat("art-1", gemini_tmp)
        chat_file = next((gemini_tmp / "chats").glob("session-*.json"))
        data = json.loads(chat_file.read_text())
        types = [m["type"] for m in data["messages"]]
        assert types == ["user", "gemini"]

    def test_project_hash_matches_sha256_of_project_root(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        project_root = "/home/user/projects/myproject/.worktrees/art-1"
        (gemini_tmp).mkdir(parents=True, exist_ok=True)
        (gemini_tmp / ".project_root").write_text(project_root)
        history = [{"role": "user", "parts": [{"text": "hi"}]}]
        self._make_checkpoint(gemini_tmp, "art-1", history)
        _convert_checkpoint_to_chat("art-1", gemini_tmp)
        chat_file = next((gemini_tmp / "chats").glob("session-*.json"))
        data = json.loads(chat_file.read_text())
        expected_hash = hashlib.sha256(project_root.encode()).hexdigest()
        assert data["projectHash"] == expected_hash

    def test_chat_file_mtime_matches_checkpoint_mtime(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        history = [{"role": "user", "parts": [{"text": "hi"}]}]
        self._make_checkpoint(gemini_tmp, "art-1", history, mtime=1234567890.0)
        _convert_checkpoint_to_chat("art-1", gemini_tmp)
        chat_file = next((gemini_tmp / "chats").glob("session-*.json"))
        assert abs(chat_file.stat().st_mtime - 1234567890.0) < 1.0

    def test_when_no_checkpoint_then_returns_none(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        gemini_tmp.mkdir(parents=True)
        result = _convert_checkpoint_to_chat("art-1", gemini_tmp)
        assert result is None

    def test_when_checkpoint_empty_history_then_returns_none(self, tmp_path):
        gemini_tmp = tmp_path / ".gemini" / "tmp" / "art-1"
        gemini_tmp.mkdir(parents=True)
        (gemini_tmp / "checkpoint-art-1.json").write_text(json.dumps({"history": [], "authType": "oauth"}))
        result = _convert_checkpoint_to_chat("art-1", gemini_tmp)
        assert result is None

    def test_uuid_is_stable_across_calls(self, tmp_path):
        raw = b'{"history": [{"role": "user", "parts": [{"text": "test"}]}]}'
        uuid1 = _checkpoint_to_chat_uuid(raw)
        uuid2 = _checkpoint_to_chat_uuid(raw)
        assert uuid1 == uuid2

    def test_uuid_has_valid_format(self, tmp_path):
        raw = b'{"history": [{"role": "user", "parts": [{"text": "test"}]}]}'
        uuid = _checkpoint_to_chat_uuid(raw)
        parts = uuid.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert parts[2].startswith("4")  # version 4


# --- get_latest_gemini_session_id ---


class TestGetLatestGeminiSessionId:
    def test_latest_gemini_id_when_ai_name_provided_then_scans_chats_first(self, tmp_path):
        chats = tmp_path / ".gemini" / "tmp" / "art-1" / "chats"
        chats.mkdir(parents=True)
        (chats / "session-abc.json").write_text('{"sessionId": "chats-uuid"}')
        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("ai_cli.config._get_main_project_name", return_value=None):
                result = get_latest_gemini_session_id("art-1")
        assert result == "chats-uuid"

    def test_latest_gemini_id_when_ai_name_provided_but_no_chats_then_returns_none(self, tmp_path):
        # Sessions started via /resume load (checkpoint restore) don't write a chat
        # file, so their UUID in logs.json cannot be used for -r resume.  We must
        # return None so the caller falls back to /resume load again.
        logs_dir = tmp_path / ".gemini" / "tmp" / "artelier"
        logs_dir.mkdir(parents=True)
        (logs_dir / "logs.json").write_text('{"sessionId": "logs-uuid"}')
        with patch("pathlib.Path.cwd", return_value=tmp_path / "artelier"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.config._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id("art-1")
        assert result is None

    def test_latest_gemini_id_when_logs_exist_then_returns_last(self, tmp_path):
        logs_dir = tmp_path / ".gemini" / "tmp" / "testproject"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        logs_file.write_text('{"sessionId": "abc123"}\n{"sessionId": "def456"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "testproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.config._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result == "def456"

    def test_latest_gemini_id_when_no_logs_then_returns_none(self, tmp_path):
        with patch("pathlib.Path.cwd", return_value=tmp_path / "noproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.config._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result is None


class TestGetLatestGeminiEdgeCases:
    def test_latest_gemini_id_when_main_project_set_then_checks_both_paths(self, tmp_path):
        main_logs = tmp_path / ".gemini" / "tmp" / "myproject" / "logs.json"
        main_logs.parent.mkdir(parents=True)
        main_logs.write_text('{"sessionId": "main-id-123"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "otherproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.session._get_main_project_name", return_value="myproject"):
                    result = get_latest_gemini_session_id()
        assert result == "main-id-123"

    def test_latest_gemini_id_when_large_file_then_seeks_tail(self, tmp_path):
        logs_dir = tmp_path / ".gemini" / "tmp" / "bigproject"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        padding = "x" * 5000 + "\n"
        logs_file.write_text(padding + '{"sessionId": "tail-id-456"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "bigproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.session._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result == "tail-id-456"


class TestGetLatestGeminiSessionIdException:
    def test_get_latest_gemini_session_id_when_open_raises_then_returns_none(self, tmp_path):
        """Covers lines 241-242: exception in open() inside get_latest_gemini_session_id."""
        import builtins as _builtins

        log_dir = tmp_path / ".gemini" / "tmp" / "testproj"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "logs.json"
        log_file.write_bytes(b'{"sessionId": "abc"}')

        real_open = _builtins.open

        def fail_on_log(path, *args, **kwargs):
            if str(path).endswith("logs.json"):
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("pathlib.Path.cwd", return_value=tmp_path / "projects" / "testproj"):
                with patch("ai_cli.config._get_main_project_name", return_value=None):
                    with patch("builtins.open", side_effect=fail_on_log):
                        result = get_latest_gemini_session_id()
        assert result is None


# --- resolve_session ---


class TestResolveSession:
    def test_resolve_session_when_name_and_session_exists_then_returns_it(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = resolve_session("c-sw-", "3")
        assert result == "c-sw-3"

    def test_resolve_session_when_name_not_found_then_finds_recent(self):
        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "has-session" in cmd:
                m.returncode = 1
            elif "list-sessions" in cmd:
                m.returncode = 1
                m.stdout = ""
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "99")
        assert result == ""


class TestResolveSessionEdgeCases:
    def test_resolve_session_when_no_name_and_current_session_matches_then_returns_it(self):
        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "display-message" in cmd:
                m.returncode = 0
                m.stdout = "c-sw-5"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "")
        assert result == "c-sw-5"


class TestResolveSessionFallback:
    def test_resolve_session_when_no_name_and_no_current_session_then_finds_recent(self):
        """Covers line 362: falls through to find_recent_session."""

        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "display-message" in cmd:
                m.returncode = 0
                m.stdout = "other-session"  # doesn't match prefix
            elif "list-sessions" in cmd:
                m.returncode = 0
                m.stdout = "c-sw-1 100\n"
            else:
                m.returncode = 1
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "")
        assert result == "c-sw-1"


# --- detect_repo_root ---


class TestDetectRepoRoot:
    def test_detect_repo_root_when_in_repo_then_returns_path(self):
        # --git-common-dir returns the .git directory; parent is the repo root.
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/projects/myapp/.git\n"
        with patch("subprocess.run", return_value=mock_result):
            result = detect_repo_root()
        assert result == Path("/home/user/projects/myapp")

    def test_detect_repo_root_when_in_worktree_then_returns_main_root(self):
        # --git-common-dir from a worktree may return a relative path; resolve it.
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "../../.git\n"
        with patch("subprocess.run", return_value=mock_result):
            with patch("ai_cli.session.Path.cwd", return_value=Path("/home/user/projects/myapp/.worktrees/sw-1")):
                result = detect_repo_root()
        assert result == Path("/home/user/projects/myapp")

    def test_detect_repo_root_when_not_in_repo_then_returns_none(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = detect_repo_root()
        assert result is None


# --- create_worktree ---


class TestCreateWorktree:
    def test_create_worktree_when_no_repo_then_returns_none(self):
        with patch("ai_cli.session.detect_repo_root", return_value=None):
            result = create_worktree("sw-1")
        assert result is None

    def test_create_worktree_when_existing_valid_wt_then_returns_it(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-1"
        wt_dir.mkdir(parents=True)

        mock_prune = MagicMock(returncode=0)
        mock_list = MagicMock(returncode=0, stdout=str(wt_dir))

        def mock_run(cmd, **kwargs):
            if "prune" in cmd:
                return mock_prune
            if "list" in cmd:
                return mock_list
            return MagicMock(returncode=0)

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-1")
        assert result == wt_dir

    def test_create_worktree_when_new_then_creates_and_returns(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-2"

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            m = MagicMock(returncode=0)
            m.stdout = ""
            if "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-2")
        assert result == wt_dir


class TestCreateWorktreeEdgeCases:
    def test_create_worktree_when_stale_dir_then_recreates(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-3"
        wt_dir.mkdir(parents=True)

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            m = MagicMock(returncode=0)
            if "list" in cmd and "--porcelain" in cmd:
                m.stdout = ""
            elif "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            else:
                m.stdout = ""
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-3")
        assert result == wt_dir


class TestCreateWorktreeEdgeCases2:
    def test_create_worktree_when_first_add_fails_then_retries_existing_branch(self, tmp_path):
        """Covers line 444: fallback to existing branch."""
        wt_dir = tmp_path / ".worktrees" / "sw-4"
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock(returncode=0, stdout="")
            if "worktree" in cmd and "add" in cmd and "-b" in cmd:
                m.returncode = 1  # branch already exists
            elif "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-4")
        assert result == wt_dir
        add_calls = [c for c in calls if "worktree" in c and "add" in c]
        assert len(add_calls) == 2

    def test_create_worktree_when_src_not_exists_then_skips_symlink(self, tmp_path):
        """Covers lines 454-455: src doesn't exist, so no symlink."""
        wt_dir = tmp_path / ".worktrees" / "sw-5"

        def mock_run(cmd, **kwargs):
            m = MagicMock(returncode=0, stdout="")
            if "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-5")
        assert result == wt_dir
        assert not (wt_dir / ".venv").exists()

    def test_create_worktree_when_wt_dir_not_created_then_returns_none(self, tmp_path):
        """Covers line 457: wt_dir.exists() is False after git commands."""

        def mock_run(cmd, **kwargs):
            m = MagicMock(returncode=1, stdout="")
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-6")
        assert result is None


class TestCreateWorktreeSymlink:
    def test_create_worktree_when_venv_exists_then_symlinks(self, tmp_path):
        """Covers line 455: os.symlink when src exists and dst does not."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".venv").mkdir()
        wt_dir = repo_root / ".worktrees" / "sw-1"

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0, stdout="")

        with patch("ai_cli.session.detect_repo_root", return_value=repo_root):
            with patch("ai_cli.session.get_project_prefix", return_value="sw"):
                with patch("subprocess.run", side_effect=fake_run):
                    result = create_worktree("sw-1")

        assert (wt_dir / ".venv").is_symlink()
        assert result == wt_dir


# --- cleanup_worktree ---


class TestCleanupWorktree:
    def test_cleanup_worktree_when_no_repo_then_noop(self):
        """Covers lines 461-463: no repo root."""
        with patch("ai_cli.session.detect_repo_root", return_value=None):
            cleanup_worktree("sw-1")

    def test_cleanup_worktree_when_dir_not_exists_then_noop(self, tmp_path):
        """Covers lines 465-466: worktree dir doesn't exist."""
        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            cleanup_worktree("nonexistent")

    def test_cleanup_worktree_when_dirty_then_skips_remove(self, tmp_path):
        """Covers lines 469-472: diff returns nonzero, so no removal."""
        wt_dir = tmp_path / ".worktrees" / "sw-7"
        wt_dir.mkdir(parents=True)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock()
            if "diff" in cmd and "--cached" not in cmd:
                m.returncode = 1  # dirty
            else:
                m.returncode = 0
            return m

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                cleanup_worktree("sw-7")
        remove_calls = [c for c in calls if "remove" in c]
        assert len(remove_calls) == 0

    def test_cleanup_worktree_when_clean_then_removes(self, tmp_path):
        """Covers lines 471-472: both diffs clean, calls worktree remove."""
        wt_dir = tmp_path / ".worktrees" / "sw-8"
        wt_dir.mkdir(parents=True)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                cleanup_worktree("sw-8")
        remove_calls = [c for c in calls if "remove" in c]
        assert len(remove_calls) == 1


# --- find_next_index ---


class TestFindNextIndex:
    def test_find_next_index_when_first_slot_taken_then_returns_second(self):
        """Covers line 267: i += 1 (second iteration of the while loop)."""
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                m.returncode = 0  # prefix1 exists
            else:
                m.returncode = 1  # prefix2 does not exist
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = find_next_index("c-sw-")
        assert result == 2


# --- find_recent_session ---


class TestFindRecentSession:
    def test_find_recent_session_when_tmux_fails_then_returns_empty(self):
        """Covers line 276 (returncode != 0 early return path)."""
        m = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == ""

    def test_find_recent_session_when_empty_lines_then_skips(self):
        """Covers lines 278-279: empty line in output."""
        m = MagicMock(returncode=0, stdout="\n\nc-sw-1 100\n\nc-sw-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == "c-sw-2"

    def test_find_recent_session_when_bad_timestamp_then_skips(self):
        """Covers lines 284-285: ValueError parsing timestamp."""
        m = MagicMock(returncode=0, stdout="c-sw-1 notanumber\nc-sw-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == "c-sw-2"

    def test_find_recent_session_when_no_matching_sessions_then_returns_empty(self):
        """Covers lines 286-287: no sessions match prefix."""
        m = MagicMock(returncode=0, stdout="other-1 100\nother-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == ""


# --- cleanup_stale_sessions edge cases ---


class TestCleanupStaleSessions:
    def test_cleanup_stale_when_empty_line_then_skips(self):
        """Covers line 325: empty line in tmux output."""
        m = MagicMock(returncode=0, stdout="\n\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})

    def test_cleanup_stale_when_bad_format_then_skips(self):
        """Covers line 328: line with wrong number of parts."""
        m = MagicMock(returncode=0, stdout="badline\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})

    def test_cleanup_stale_when_bad_timestamp_then_skips(self):
        """Covers lines 334-335: ValueError parsing last_attached."""
        m = MagicMock(returncode=0, stdout="c-sw-1|notanumber|0|bash\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})


# --- get_latest_gemini_session_id — exception branch ---


class TestProjectRegistryExceptionBranches:
    def test_get_latest_gemini_session_id_when_read_fails_then_returns_none(self, tmp_path):
        """Covers lines 241-242: exception reading logs.json."""
        logs_dir = tmp_path / ".gemini" / "tmp" / "testproj"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        logs_file.write_text("")  # empty file

        with patch("pathlib.Path.cwd", return_value=tmp_path / "testproj"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.config._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result is None
